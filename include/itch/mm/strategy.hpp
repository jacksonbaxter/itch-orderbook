// The market maker on the backtester's strategy interface (backtest.hpp): one symbol, one bid
// and one ask of a fixed size, priced by a quoting model (quoting.hpp).
//
//     Quoter<AsModel> maker{config, AsModel{parameters, close}};
//     engine.run(stream, maker);
//
// **Prices.** The model's targets are in ticks, from the view book's mid. The bid is the target
// rounded down to the tick grid and the ask rounded up, so rounding never narrows the model's
// spread. Each is then held off the other side of the view book: the bid at most one tick under
// its best ask, the ask at least one over its best bid, so the maker never sends an order that
// would take. From the rounding on, every price is an integer Price4.
//
// **Requotes.** An order is replaced only when its rounded price changes, and the replace
// restores the full size. A replace goes to the back of the queue, so a rounded target that
// has not moved must not cost the order its place (market-maker.md, "Requote rule"). A side
// with a request in flight waits for its report.
//
// **Inventory.** In lots of the order size. A side whose fills could take the position past
// max_lots lots, counting what an order already out could still fill, is not quoted, for
// either model.
//
// **Session.** Quoting starts at the first delivered message at or after `start` once the
// market has opened (System Event `Q`) and the symbol trades, and stops at `stop` or the close
// (System Event `M`), whichever is first, with both orders cancelled. The model's horizon T is
// its own: AsModel takes it in its constructor.
//
// **Fills.** Each fill report becomes a Fill of fills.hpp, at the exchange's time, with the
// fill's whole fee. The log is sized in the constructor; a fill past its capacity is counted
// and the run is no longer valid.
//
// Nothing allocates after the constructor.
#pragma once

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <utility>
#include <vector>

#include "itch/backtest.hpp"
#include "itch/book_types.hpp"
#include "itch/messages.hpp"
#include "itch/mm/fills.hpp"
#include "itch/mm/quoting.hpp"
#include "itch/wire.hpp"

namespace itch::mm {

using backtest::MarketEvent;
using backtest::OrderId;
using backtest::Reject;
using backtest::Report;
using backtest::ReportKind;

inline constexpr double kNanosPerSecond = 1e9;

// Avellaneda-Stoikov with its horizon T, in nanoseconds after midnight.
class AsModel {
 public:
  AsModel(const ModelParameters& parameters, Nanos horizon) noexcept
      : model_{parameters}, horizon_{horizon} {}

  [[nodiscard]] Targets operator()(double mid, std::int64_t position, std::uint32_t lot,
                                   Nanos now) const noexcept {
    const Nanos left = now < horizon_ ? horizon_ - now : 0;
    const double lots = static_cast<double>(position) / static_cast<double>(lot);
    return model_.quote(mid, lots, static_cast<double>(left) / kNanosPerSecond);
  }

  [[nodiscard]] const AvellanedaStoikov& model() const noexcept { return model_; }

 private:
  AvellanedaStoikov model_;
  Nanos horizon_;
};

// GLFT's stationary quotes. The position is counted in whole lots towards zero.
class GlftModel {
 public:
  GlftModel(const ModelParameters& parameters, std::int64_t max_lots)
      : model_{parameters, max_lots} {}

  [[nodiscard]] Targets operator()(double mid, std::int64_t position, std::uint32_t lot,
                                   Nanos /*now*/) const noexcept {
    return model_.quote(mid, position / std::int64_t{lot});
  }

  [[nodiscard]] const GlftStationary& model() const noexcept { return model_; }

 private:
  GlftStationary model_;
};

struct QuoterConfig {
  Stock symbol{};
  std::uint32_t lot = 100;  // Shares in each order.
  Price4 tick = 100;
  std::int64_t max_lots = 5;
  Nanos start = 0;  // Nanoseconds after midnight.
  Nanos stop = 0;
  std::uint32_t max_fills = 1U << 16U;
};

struct QuoterCounts {
  std::uint64_t sends = 0;
  std::uint64_t replaces = 0;
  std::uint64_t cancels = 0;
  std::uint64_t fills = 0;
  std::uint64_t filled_shares = 0;
  std::uint64_t rejects = 0;
  std::uint64_t unpriced = 0;  // A target the tick grid cannot hold: not finite, or off it.
  std::uint64_t fill_overflows = 0;

  friend bool operator==(const QuoterCounts&, const QuoterCounts&) = default;
};

// A model's target in ticks, as a Price4 on the grid: the bid rounded down, the ask up. None if
// the target is not finite or falls outside one tick to the largest Price4.
[[nodiscard]] inline std::optional<Price4> on_grid(double ticks, Side side, Price4 tick) noexcept {
  if (!std::isfinite(ticks)) {
    return std::nullopt;
  }
  const double rounded = side == Side::bid ? std::floor(ticks) : std::ceil(ticks);
  const double most = 4'294'967'295.0 / static_cast<double>(tick);
  if (rounded < 1.0 || rounded > most) {
    return std::nullopt;
  }
  return static_cast<Price4>(static_cast<std::uint64_t>(rounded) * tick);
}

template <class Model>
class Quoter {
 public:
  Quoter(const QuoterConfig& config, Model model)
      : config_{config}, model_{std::move(model)}, fills_(config.max_fills) {}

  template <class Context>
  void on_market(Context& context, const MarketEvent& event) {
    const std::byte* m = event.payload.data();
    const char type = read_char(m);
    const auto locate = read_be<std::uint16_t>(m + 1);
    if (type == SystemEvent::kType) {
      session(context, SystemEvent::from_wire(m).event_code);
      return;
    }
    if (type == StockDirectory::kType && StockDirectory::from_wire(m).stock == config_.symbol) {
      locate_ = locate;
      known_ = true;
    }
    if (!known_ || locate != locate_) {
      return;
    }
    if (type == StockTradingAction::kType) {
      trading_ = StockTradingAction::from_wire(m).trading_state == 'T';
    }
    if (context.now() >= config_.stop) {
      stop(context);
      return;
    }
    if (open_ && trading_ && !stopped_ && context.now() >= config_.start) {
      requote(context);
    }
  }

  template <class Context>
  void on_report(Context& /*context*/, const Report& report) {
    if (report.kind == ReportKind::fill) {
      record_fill(report);
    }
    if (report.kind == ReportKind::rejected) {
      ++counts_.rejects;
    }
    // By id, not side: the engine reports a cancel or replace that found no order with no side.
    Mine* const found = mine_of(report.order);
    if (found == nullptr) {
      return;
    }
    Mine& mine = *found;
    switch (report.kind) {
      case ReportKind::accepted:
      case ReportKind::replaced:
        mine.busy = false;
        mine.remaining = report.remaining;
        break;
      case ReportKind::fill:
        mine.remaining = report.remaining;
        if (report.remaining == 0) {
          mine = {};
        }
        break;
      case ReportKind::replace_rejected:
      case ReportKind::cancel_rejected:
        mine.busy = false;
        if (report.reason == Reject::not_live) {
          mine = {};
        }
        break;
      case ReportKind::rejected:
      case ReportKind::cancelled:
      case ReportKind::expired: mine = {}; break;
    }
  }

  template <class Context>
  void on_wake(Context& context) {
    if (context.now() >= config_.stop) {
      stop(context);
    }
  }

  [[nodiscard]] std::span<const Fill> fills() const noexcept {
    return std::span<const Fill>{fills_}.first(filled_);
  }
  [[nodiscard]] std::int64_t position() const noexcept { return position_; }
  [[nodiscard]] const QuoterCounts& counts() const noexcept { return counts_; }
  [[nodiscard]] const Model& model() const noexcept { return model_; }
  // The price of the order on a side, sent or resting, or none.
  [[nodiscard]] std::optional<Price4> quoted(Side side) const noexcept {
    const Mine& mine = mine_[static_cast<std::size_t>(side)];
    return mine.id.valid() ? std::optional<Price4>{mine.price} : std::nullopt;
  }

 private:
  struct Mine {
    OrderId id{};
    Price4 price = 0;
    std::int64_t remaining = 0;  // As last reported, or as sent.
    bool busy = false;
  };

  [[nodiscard]] Mine* mine_of(OrderId order) noexcept {
    for (Mine& mine : mine_) {
      if (order.valid() && mine.id == order) {
        return &mine;
      }
    }
    return nullptr;
  }

  template <class Context>
  void session(Context& context, char code) {
    if (code == 'Q') {
      open_ = true;
      context.wake_at(config_.stop);
    } else if (code == 'M') {
      open_ = false;
      stop(context);
    }
  }

  template <class Context>
  void stop(Context& context) {
    stopped_ = true;
    for (const Side side : {Side::bid, Side::ask}) {
      place(context, side, std::nullopt, 0);
    }
  }

  template <class Context>
  void requote(Context& context) {
    const TopOfBook bid = context.book().best(locate_, Side::bid);
    const TopOfBook ask = context.book().best(locate_, Side::ask);
    if (!bid.present || !ask.present || bid.price > ask.price) {
      return;
    }
    const double mid = (static_cast<double>(bid.price) + static_cast<double>(ask.price)) /
                       (2.0 * static_cast<double>(config_.tick));
    const Targets targets = model_(mid, position_, config_.lot, context.now());
    std::optional<Price4> buy = targets.bid ? priced(*targets.bid, Side::bid) : std::nullopt;
    std::optional<Price4> sell = targets.ask ? priced(*targets.ask, Side::ask) : std::nullopt;
    if (buy && *buy >= ask.price) {
      buy =
          ask.price > config_.tick ? std::optional<Price4>{ask.price - config_.tick} : std::nullopt;
    }
    if (sell && *sell <= bid.price) {
      sell = bid.price + config_.tick;
    }
    const std::int64_t limit = config_.max_lots * std::int64_t{config_.lot};
    place(context, Side::bid, buy, limit - position_);
    place(context, Side::ask, sell, limit + position_);
  }

  [[nodiscard]] std::optional<Price4> priced(double ticks, Side side) noexcept {
    const std::optional<Price4> price = on_grid(ticks, side, config_.tick);
    counts_.unpriced += price ? 0U : 1U;
    return price;
  }

  // Moves one side towards `target`: none cancels it. `room` is how many more shares this side
  // may fill before the position passes the bound. An order still resting at its old price can
  // fill what it has left before a replace arrives, and the replace then restores the full lot,
  // so a replace needs room for both; where there is none the order is cancelled instead.
  template <class Context>
  void place(Context& context, Side side, std::optional<Price4> target, std::int64_t room) {
    Mine& mine = mine_[static_cast<std::size_t>(side)];
    if (mine.busy) {
      return;
    }
    const std::int64_t lot = config_.lot;
    if (target && !mine.id.valid() && lot > room) {
      return;
    }
    if (target && mine.id.valid() && mine.price != *target && mine.remaining + lot > room) {
      target.reset();
    }
    if (!target) {
      if (mine.id.valid()) {
        context.cancel(mine.id);
        mine.busy = true;
        ++counts_.cancels;
      }
      return;
    }
    if (!mine.id.valid()) {
      mine.id =
          context.send({.locate = locate_, .side = side, .price = *target, .shares = config_.lot});
      mine.price = *target;
      mine.remaining = config_.lot;
      mine.busy = mine.id.valid();
      ++counts_.sends;
    } else if (mine.price != *target) {
      context.replace(mine.id, *target, config_.lot);
      mine.price = *target;
      mine.busy = true;
      ++counts_.replaces;
    }
  }

  void record_fill(const Report& report) noexcept {
    const auto shares = std::int64_t{report.shares};
    const std::int64_t signed_shares = report.side == Side::bid ? shares : -shares;
    position_ += signed_shares;
    ++counts_.fills;
    counts_.filled_shares += report.shares;
    if (filled_ == fills_.size()) {
      ++counts_.fill_overflows;
      return;
    }
    fills_[filled_] = {.time = report.exchange_time,
                       .quantity = signed_shares,
                       .price = report.price,
                       .fee = report.fees.total()};
    ++filled_;
  }

  QuoterConfig config_;
  Model model_;
  std::array<Mine, 2> mine_{};
  std::vector<Fill> fills_;  // Sized once; the first filled_ are the log.
  std::size_t filled_ = 0;
  std::int64_t position_ = 0;
  std::uint16_t locate_ = 0;
  bool known_ = false;
  bool trading_ = false;
  bool open_ = false;
  bool stopped_ = false;
  QuoterCounts counts_;
};

}  // namespace itch::mm
