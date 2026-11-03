// A trivial strategy that exercises every path of the backtester and allocates nothing
// once built: on each message of a traded symbol it joins the view book's best bid and
// best ask with 100 shares, follows them with replaces, cancels its bid every 64th
// message, and takes 100 with an IOC every 16th. It is a test load, not a strategy anyone
// should run: no price, inventory or PnL logic, and no claim about what it earns.
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "itch/backtest.hpp"
#include "itch/book_types.hpp"
#include "itch/wire.hpp"

namespace itch::backtest::synthetic {

class Joiner {
 public:
  struct Counts {
    std::uint64_t sends = 0;
    std::uint64_t replaces = 0;
    std::uint64_t cancels = 0;
    std::uint64_t takes = 0;
    std::uint64_t fills = 0;
    std::uint64_t rejects = 0;
  };

  Joiner() : mine_(kSideCount) {}

  template <class Context>
  void on_market(Context& context, const MarketEvent& event) {
    const auto locate = read_be<std::uint16_t>(event.payload.data() + 1);
    if (locate == 0 || stopped_) {
      return;
    }
    ++events_;
    for (const Side side : {Side::bid, Side::ask}) {
      quote(context, locate, side);
    }
    if (events_ % 64 == 0) {
      Mine& bid = mine_[side_key(locate, Side::bid)];
      if (bid.id.valid() && !bid.busy) {
        context.cancel(bid.id);
        bid.busy = true;
        ++counts_.cancels;
      }
    }
    if (events_ % 16 == 0) {
      take(context, locate, events_ % 32 == 0 ? Side::bid : Side::ask);
    }
  }

  template <class Context>
  void on_report(Context& /*context*/, const Report& report) {
    Mine& mine = mine_[side_key(report.locate, report.side)];
    if (report.kind == ReportKind::fill) {
      ++counts_.fills;
    }
    if (report.kind == ReportKind::rejected) {
      ++counts_.rejects;
      stopped_ = stopped_ || report.reason == Reject::closed;
    }
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
        if (report.reason == Reject::not_live) {
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
    bool busy = false;
  };

  template <class Context>
  void quote(Context& context, std::uint16_t locate, Side side) {
    const TopOfBook top = context.book().best(locate, side);
    Mine& mine = mine_[side_key(locate, side)];
    if (!top.present || mine.busy) {
      return;
    }
    if (!mine.id.valid()) {
      mine.id = context.send({.locate = locate, .side = side, .price = top.price, .shares = 100});
      mine.price = top.price;
      mine.busy = mine.id.valid();
      ++counts_.sends;
    } else if (mine.price != top.price) {
      context.replace(mine.id, top.price, 100);
      mine.price = top.price;
      mine.busy = true;
      ++counts_.replaces;
    }
  }

  template <class Context>
  void take(Context& context, std::uint16_t locate, Side side) {
    const TopOfBook far = context.book().best(locate, side == Side::bid ? Side::ask : Side::bid);
    if (!far.present) {
      return;
    }
    static_cast<void>(context.send(
        {.locate = locate, .side = side, .price = far.price, .shares = 100, .tif = Tif::ioc}));
    ++counts_.takes;
  }

  std::vector<Mine> mine_;
  std::uint64_t events_ = 0;
  Counts counts_;
  bool stopped_ = false;
};

}  // namespace itch::backtest::synthetic
