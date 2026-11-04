// The two loads bench_backtest times, each a strategy for itch::backtest::Engine that
// allocates nothing once built.
//
// Quoter, for T-BT-1: a simple quoting strategy. On every message of a traded symbol it
// joins the view book's best bid and best ask with 100 shares, and moves each quote with a
// replace when that side's best price moves. It stops bidding while long 1,000 shares or
// more and stops offering while short as much, cancelling the quote on that side, and quotes
// neither side while the view book is locked or crossed. Nothing else: no fair value, no
// skew, and no claim about what it earns. It is not the Avellaneda-Stoikov maker.
//
// ShadowPlacer, for T-BT-3: no strategy, only a schedule, as queuetruth's shadow orders
// are. It places `count` orders of 100 shares at evenly spaced times across the regular
// session, cycling through the traded symbols and the two sides, each at the view book's
// best price on its side then, and cancels each 60 s after it was placed. A side that is
// empty at its time is skipped and counted. The span and the life can be shortened, for a
// fixture whose session lasts milliseconds.
#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

#include "itch/backtest.hpp"
#include "itch/book_types.hpp"
#include "itch/messages.hpp"
#include "itch/wire.hpp"

namespace itch::bench {

using backtest::MarketEvent;
using backtest::Nanos;
using backtest::OrderId;
using backtest::Report;
using backtest::ReportKind;

inline constexpr std::uint32_t kQuoteShares = 100;

class Quoter {
 public:
  static constexpr std::int64_t kInventoryLimit = 1'000;

  struct Counts {
    std::uint64_t events = 0;
    std::uint64_t sends = 0;
    std::uint64_t replaces = 0;
    std::uint64_t cancels = 0;
    std::uint64_t fills = 0;
    std::uint64_t filled_shares = 0;
    std::uint64_t reports = 0;
  };

  Quoter() : mine_(kSideCount), inventory_(std::size_t{1} << 16U, 0) {}

  template <class Context>
  void on_market(Context& context, const MarketEvent& event) {
    ++counts_.events;
    const auto locate = read_be<std::uint16_t>(event.payload.data() + 1);
    if (locate == 0) {
      return;
    }
    const TopOfBook bid = context.book().best(locate, Side::bid);
    const TopOfBook ask = context.book().best(locate, Side::ask);
    const bool open = bid.present && ask.present && bid.price < ask.price;
    const std::int64_t held = inventory_[locate];
    quote(context, locate, Side::bid, bid, open && held < kInventoryLimit);
    quote(context, locate, Side::ask, ask, open && held > -kInventoryLimit);
  }

  template <class Context>
  void on_report(Context& /*context*/, const Report& report) {
    ++counts_.reports;
    if (report.kind == ReportKind::fill) {
      ++counts_.fills;
      counts_.filled_shares += report.shares;
      const auto shares = static_cast<std::int64_t>(report.shares);
      inventory_[report.locate] += report.side == Side::bid ? shares : -shares;
    }
    Mine& mine = mine_[side_key(report.locate, report.side)];
    if (!mine.id.valid() || report.order != mine.id) {
      return;
    }
    switch (report.kind) {
      case ReportKind::accepted:
      case ReportKind::replaced: mine.busy = false; break;
      case ReportKind::fill:
        if (report.remaining == 0) {
          mine = {};
        }
        break;
      case ReportKind::replace_rejected:
        mine.busy = false;
        if (report.reason == backtest::Reject::not_live) {
          mine = {};
        }
        break;
      case ReportKind::rejected:
      case ReportKind::cancelled:
      case ReportKind::cancel_rejected:
      case ReportKind::expired: mine = {}; break;
    }
  }

  template <class Context>
  void on_wake(Context& /*context*/) {}

  [[nodiscard]] const Counts& counts() const noexcept { return counts_; }

 private:
  struct Mine {
    OrderId id{};
    Price4 price = 0;
    bool busy = false;  // A send, replace or cancel is in flight.
  };

  template <class Context>
  void quote(Context& context, std::uint16_t locate, Side side, const TopOfBook& top, bool wanted) {
    Mine& mine = mine_[side_key(locate, side)];
    if (mine.busy) {
      return;
    }
    if (!wanted) {
      if (mine.id.valid()) {
        context.cancel(mine.id);
        mine.busy = true;
        ++counts_.cancels;
      }
      return;
    }
    if (!mine.id.valid()) {
      mine.id = context.send(
          {.locate = locate, .side = side, .price = top.price, .shares = kQuoteShares});
      mine.price = top.price;
      mine.busy = mine.id.valid();
      ++counts_.sends;
    } else if (mine.price != top.price) {
      context.replace(mine.id, top.price, kQuoteShares);
      mine.price = top.price;
      mine.busy = true;
      ++counts_.replaces;
    }
  }

  std::vector<Mine> mine_;
  std::vector<std::int64_t> inventory_;  // Shares held, per locate.
  Counts counts_;
};

class ShadowPlacer {
 public:
  static constexpr Nanos kOpen = 34'200'000'000'000;      // 09:30:00
  static constexpr Nanos kClose = 57'600'000'000'000;     // 16:00:00
  static constexpr Nanos kLife = 60'000'000'000;          // Each order rests 60 s.
  static constexpr Nanos kSpan = kClose - kLife - kOpen;  // Placements, from the open.

  struct Counts {
    std::uint64_t events = 0;
    std::uint64_t placed = 0;
    std::uint64_t skipped = 0;  // Its side was empty at its time.
    std::uint64_t cancels = 0;
    std::uint64_t fills = 0;
    std::uint64_t filled_shares = 0;
    std::uint64_t reports = 0;
    std::uint64_t wakes = 0;
  };

  // `locates` are learnt from the Stock Directory messages delivered, in this order of
  // `symbols`, so the placer needs nothing from outside the engine.
  ShadowPlacer(std::span<const Stock> symbols, std::uint32_t count, Nanos span = kSpan,
               Nanos life = kLife)
      : symbols_(symbols.begin(), symbols.end()),
        locates_(symbols.size(), 0),
        live_(count),
        count_{count},
        spacing_{count == 0 ? 0 : span / count},
        life_{life} {}

  template <class Context>
  void on_market(Context& context, const MarketEvent& event) {
    ++counts_.events;
    const std::byte* m = event.payload.data();
    if (read_char(m) == StockDirectory::kType) {
      const StockDirectory directory = StockDirectory::from_wire(m);
      for (std::size_t i = 0; i < symbols_.size(); ++i) {
        locates_[i] = symbols_[i] == directory.stock ? directory.header.stock_locate : locates_[i];
      }
    }
    if (!started_ && count_ != 0) {
      started_ = true;
      context.wake_at(kOpen);
    }
  }

  template <class Context>
  void on_report(Context& /*context*/, const Report& report) {
    ++counts_.reports;
    if (report.kind == ReportKind::fill) {
      ++counts_.fills;
      counts_.filled_shares += report.shares;
    }
  }

  template <class Context>
  void on_wake(Context& context) {
    ++counts_.wakes;
    const Nanos now = context.now();
    while (head_ != tail_ && live_[head_ % live_.size()].cancel_at <= now) {
      context.cancel(live_[head_ % live_.size()].id);
      ++counts_.cancels;
      ++head_;
    }
    while (next_ < count_ && placement_time(next_) <= now) {
      place(context, next_++, now);
    }
    Nanos wake = next_ < count_ ? placement_time(next_) : kNever;
    if (head_ != tail_) {
      wake = std::min(wake, live_[head_ % live_.size()].cancel_at);
    }
    if (wake != kNever) {
      context.wake_at(wake);
    }
  }

  [[nodiscard]] const Counts& counts() const noexcept { return counts_; }

 private:
  static constexpr Nanos kNever = ~Nanos{0};

  struct Live {
    OrderId id{};
    Nanos cancel_at = 0;
  };

  [[nodiscard]] Nanos placement_time(std::uint64_t index) const noexcept {
    return kOpen + (index * spacing_);
  }

  template <class Context>
  void place(Context& context, std::uint64_t index, Nanos now) {
    const std::size_t symbol = index % symbols_.size();
    const Side side = (index / symbols_.size()) % 2 == 0 ? Side::bid : Side::ask;
    const std::uint16_t locate = locates_[symbol];
    const TopOfBook top = locate == 0 ? TopOfBook{} : context.book().best(locate, side);
    if (!top.present) {
      ++counts_.skipped;
      return;
    }
    const OrderId id =
        context.send({.locate = locate, .side = side, .price = top.price, .shares = kQuoteShares});
    if (!id.valid()) {
      ++counts_.skipped;
      return;
    }
    ++counts_.placed;
    live_[tail_ % live_.size()] = {.id = id, .cancel_at = now + life_};
    ++tail_;
  }

  std::vector<Stock> symbols_;
  std::vector<std::uint16_t> locates_;
  std::vector<Live> live_;  // A FIFO: every order lives the same time.
  std::uint64_t head_ = 0;
  std::uint64_t tail_ = 0;
  std::uint64_t next_ = 0;
  std::uint32_t count_;
  Nanos spacing_;
  Nanos life_;
  bool started_ = false;
  Counts counts_;
};

}  // namespace itch::bench
