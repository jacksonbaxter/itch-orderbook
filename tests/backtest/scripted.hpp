// Test strategies: one that does nothing, and one that sends a fixed plan of orders at
// fixed times of its own clock and records everything it is given.
#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <vector>

#include "itch/backtest.hpp"
#include "itch/book.hpp"

namespace itch::backtest::synthetic {

struct Null {
  template <class Context>
  void on_market(Context& /*context*/, const MarketEvent& /*event*/) {}
  template <class Context>
  void on_report(Context& /*context*/, const Report& /*report*/) {}
  template <class Context>
  void on_wake(Context& /*context*/) {}
};

class Scripted {
 public:
  enum class Op : std::uint8_t { send, cancel, replace };

  struct Step {
    Nanos time;
    Op op;
    int name;
    OrderRequest request;
  };

  struct Seen {
    Nanos time;
    Nanos exchange_time;
    std::uint64_t sequence;
    char type;
    std::size_t reports_before;  // Reports received before this event.
  };

  // Shares resting at the order's price in the view book when it was sent.
  struct Sent {
    int name;
    Nanos time;
    std::uint64_t view_shares;
  };

  void send(int name, Nanos time, Side side, Price4 price, std::uint32_t shares,
            Tif tif = Tif::day) {
    plan_.push_back(
        {.time = time,
         .op = Op::send,
         .name = name,
         .request = {.locate = 1, .side = side, .price = price, .shares = shares, .tif = tif}});
  }
  void cancel(int name, Nanos time) {
    plan_.push_back({.time = time, .op = Op::cancel, .name = name, .request = {}});
  }
  void replace(int name, Nanos time, Price4 price, std::uint32_t shares) {
    plan_.push_back(
        {.time = time,
         .op = Op::replace,
         .name = name,
         .request = {.locate = 1, .side = Side::bid, .price = price, .shares = shares}});
  }

  template <class Context>
  void on_market(Context& context, const MarketEvent& event) {
    seen_.push_back({.time = event.time,
                     .exchange_time = event.exchange_time,
                     .sequence = event.sequence,
                     .type = static_cast<char>(event.payload[0]),
                     .reports_before = reports_.size()});
    schedule(context);
  }
  template <class Context>
  void on_report(Context& context, const Report& report) {
    reports_.push_back(report);
    schedule(context);
  }
  template <class Context>
  void on_wake(Context& context) {
    while (next_ < plan_.size() && plan_[next_].time <= context.now()) {
      const Step& step = plan_[next_++];
      switch (step.op) {
        case Op::send: {
          ids_[step.name] = context.send(step.request);
          const auto* level =
              context.book().find_level(step.request.locate, step.request.side, step.request.price);
          sent_.push_back({.name = step.name,
                           .time = context.now(),
                           .view_shares = level == nullptr ? 0 : level->aggregate_shares});
          break;
        }
        case Op::cancel: context.cancel(ids_.at(step.name)); break;
        case Op::replace:
          context.replace(ids_.at(step.name), step.request.price, step.request.shares);
          break;
      }
    }
    schedule(context);
  }

  [[nodiscard]] OrderId id(int name) const { return ids_.at(name); }
  [[nodiscard]] const std::vector<Report>& reports() const { return reports_; }
  [[nodiscard]] const std::vector<Seen>& seen() const { return seen_; }
  [[nodiscard]] const std::vector<Sent>& sent() const { return sent_; }

  // The reports of one kind for one order, in the order received.
  [[nodiscard]] std::vector<Report> reports(int name, ReportKind kind) const {
    std::vector<Report> out;
    for (const Report& report : reports_) {
      if (report.kind == kind && ids_.contains(name) && report.order == ids_.at(name)) {
        out.push_back(report);
      }
    }
    return out;
  }

 private:
  template <class Context>
  void schedule(Context& context) {
    if (next_ < plan_.size()) {
      context.wake_at(plan_[next_].time);
    }
  }

  std::vector<Step> plan_;
  std::size_t next_ = 0;
  std::map<int, OrderId> ids_;
  std::vector<Report> reports_;
  std::vector<Seen> seen_;
  std::vector<Sent> sent_;
};

}  // namespace itch::backtest::synthetic
