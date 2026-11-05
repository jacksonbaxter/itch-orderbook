// σ, A and k for one symbol from the market tape, in ticks and seconds as GLFT use them
// (market-maker.md, "Parameters to calibrate"). tools/itch_calibrate runs it over a day.
//
// The calibrator is shown each message twice, before the book applies it and after, and reads
// only the replayed historical book. Everything is measured in regular hours, from System
// Event `Q` to `M`, while the mid is valid: the symbol in state `T`, both sides present and
// bid ≤ ask, as the mark path (marks.hpp). Changes inside one nanosecond collapse into one,
// the state after the last of them. With mid2 = best bid + best ask and τ = tick in Price4:
//
// σ, two estimators, both reported (MK-22, MK-26):
//   - clock: the mid in force at t = open + i·S, the state after every message at or before
//     t. Each change between two consecutive valid samples is Δ_i = (mid2_{i+1} − mid2_i)/(2τ)
//     ticks, and σ_clock = sqrt((1/n)·Σ(Δ_i − Δ̄)² / S), S in seconds.
//   - jumps: J_j is each change of the mid between two consecutive valid points, in ticks;
//     a change across a spell with no valid mid is not one. σ_jump = sqrt(ΣJ_j² / V), V the
//     seconds with a valid mid.
//
// A and k, λ(δ) = A e^(−kδ) (MK-21): intervals (g, g + Δ] for g = open + i·Δ, each with the
// mid in force at g, its start mid. An interval whose start mid is not valid is left out. In
// each interval and on each side, the deepest depth an aggressor reached from the start mid:
// (mid2 − 2p)/(2τ) ticks against a resting bid at p, (2p − mid2)/(2τ) against a resting ask,
// for every `E` and every printable `C` at its execution price. Non-printable `C` and `P` are left
// out: the quotes being calibrated are displayed, and λ is the rate at which aggressors reach
// displayed depth. A quote can rest only on the price grid, so a depth δ is open to an
// interval only when start mid ± δ is a multiple of τ: half ticks when the spread is odd in
// ticks, whole ticks when it is even, none when the start mid is off the grid. For each
// depth on the half-tick grid up to the cap, exposure is the intervals it was open to and
// hits the sides of those intervals whose deepest depth reached it, so
//     λ(δ) = hits / (2 · exposure · Δ),
// per side, both sides pooled. log λ = log A − kδ is fitted by ordinary least squares over
// the depths with a hit, and the number of them is reported beside the fit. λ is the
// market's rate of aggressive executions, not any strategy's fill rate (MK-21, MK-28).
#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <vector>

#include "itch/book_types.hpp"
#include "itch/messages.hpp"
#include "itch/mm/attribution.hpp"
#include "itch/mm/fills.hpp"
#include "itch/wire.hpp"

namespace itch::mm {

struct CalibrationSettings {
  Nanos interval = 100'000'000;  // Δ, as MK-21.
  Nanos sample = 1'000'000'000;  // S, coarser than the bounce (MK-22).
  Price4 tick = 100;
  std::int64_t max_depth_ticks = 20;
};

// One depth on the half-tick grid: half_ticks/2 ticks from the start mid.
struct DepthPoint {
  std::int64_t half_ticks = 0;
  std::uint64_t exposure = 0;
  std::uint64_t hits = 0;
};

struct IntensityFit {
  std::size_t points = 0;
  double k = 0;
  double a = 0;
  double r_squared = 0;
};

// λ for one point, per second.
[[nodiscard]] inline double intensity(const DepthPoint& point, Nanos interval) noexcept {
  const double seconds = static_cast<double>(interval) / 1e9;
  return static_cast<double>(point.hits) / (2.0 * static_cast<double>(point.exposure) * seconds);
}

// OLS of log λ on δ over the points with a hit. Nothing below two distinct depths.
[[nodiscard]] inline std::optional<IntensityFit> fit_intensity(std::span<const DepthPoint> points,
                                                               Nanos interval) {
  std::vector<std::array<double, 2>> xy;
  for (const DepthPoint& point : points) {
    if (point.hits > 0 && point.exposure > 0) {
      xy.push_back(
          {static_cast<double>(point.half_ticks) / 2.0, std::log(intensity(point, interval))});
    }
  }
  if (xy.size() < 2) {
    return std::nullopt;
  }
  const auto n = static_cast<double>(xy.size());
  double mean_x = 0;
  double mean_y = 0;
  for (const auto& [x, y] : xy) {
    mean_x += x / n;
    mean_y += y / n;
  }
  double sxx = 0;
  double sxy = 0;
  double syy = 0;
  for (const auto& [x, y] : xy) {
    sxx += (x - mean_x) * (x - mean_x);
    sxy += (x - mean_x) * (y - mean_y);
    syy += (y - mean_y) * (y - mean_y);
  }
  const double slope = sxy / sxx;
  return IntensityFit{.points = xy.size(),
                      .k = -slope,
                      .a = std::exp(mean_y - (slope * mean_x)),
                      .r_squared = syy == 0 ? 1.0 : (slope * sxy) / syy};
}

class Calibrator {
 public:
  Calibrator(const Stock& symbol, const CalibrationSettings& settings)
      : settings_{settings},
        hits_{std::vector<std::uint64_t>(cap() + 1, 0), std::vector<std::uint64_t>(cap() + 1, 0)},
        symbol_{symbol} {}

  // Before the book applies `payload`: the clocks move to its time, and an execution is
  // measured against the order it hits while that order still rests.
  template <class Book>
  void before(const Book& book, std::span<const std::byte> payload) {
    if (payload.size() < 11) {
      return;
    }
    const std::byte* m = payload.data();
    const Nanos time = read_be48(m + 5);
    clock_ = std::max(clock_, time);
    if (!open_) {
      return;
    }
    advance(clock_);
    if (!known_ || read_be<std::uint16_t>(m + 1) != locate_ || !interval_valid_) {
      return;
    }
    const char type = read_char(m);
    if (payload.size() != message_length(type)) {
      return;
    }
    if (type == OrderExecuted::kType) {
      const auto order = book.resting_order(OrderExecuted::from_wire(m).order_reference);
      if (order && order->locate == locate_) {
        reach(order->side, order->price);
      }
    } else if (type == OrderExecutedWithPrice::kType) {
      const OrderExecutedWithPrice c = OrderExecutedWithPrice::from_wire(m);
      const auto order = book.resting_order(c.order_reference);
      if (c.printable == 'Y' && order && order->locate == locate_) {
        reach(order->side, c.execution_price);
      }
    }
  }

  // After the book has applied `payload`: the session, the symbol's state and its mid.
  template <class Book>
  void after(const Book& book, std::span<const std::byte> payload) {
    if (payload.size() < 11) {
      return;
    }
    const std::byte* m = payload.data();
    const char type = read_char(m);
    const auto locate = read_be<std::uint16_t>(m + 1);
    const bool whole = payload.size() == message_length(type);
    if (type == StockDirectory::kType && whole && StockDirectory::from_wire(m).stock == symbol_) {
      locate_ = locate;
      known_ = true;
    }
    if (type == StockTradingAction::kType && whole && known_ && locate == locate_) {
      trading_ = StockTradingAction::from_wire(m).trading_state;
    }
    const char event =
        type == SystemEvent::kType && whole ? SystemEvent::from_wire(m).event_code : ' ';
    if (event == 'M' && open_) {
      close();
    }
    refresh(book);
    if (event == 'Q' && !open_ && !closed_) {
      open_ = true;
      opened_at_ = clock_;
      next_sample_ = clock_;
      next_interval_ = clock_;
      group_time_ = clock_;
      point_valid_ = false;
      refresh(book);
    }
  }

  // At the end of the day: a session with no `M` closes at the last message.
  void finish() {
    if (open_) {
      close();
    }
  }

  [[nodiscard]] const Stock& symbol() const noexcept { return symbol_; }
  [[nodiscard]] bool known() const noexcept { return known_; }
  [[nodiscard]] Nanos opened_at() const noexcept { return opened_at_; }
  [[nodiscard]] Nanos closed_at() const noexcept { return closed_at_; }
  [[nodiscard]] Nanos valid_nanos() const noexcept { return valid_nanos_; }
  [[nodiscard]] std::uint64_t sample_changes() const noexcept { return changes_; }
  [[nodiscard]] std::uint64_t jumps() const noexcept { return jumps_; }
  [[nodiscard]] std::uint64_t intervals() const noexcept { return intervals_; }
  [[nodiscard]] std::uint64_t intervals_off_grid() const noexcept { return off_grid_; }
  [[nodiscard]] std::uint64_t executions() const noexcept { return executions_; }

  // Ticks per √second, or nothing with no change to measure.
  [[nodiscard]] std::optional<double> sigma_clock() const noexcept {
    if (changes_ == 0) {
      return std::nullopt;
    }
    const auto n = static_cast<attribution_detail::Wide>(changes_);
    const auto spread = (n * squares_) - (sum_ * sum_);  // n² times the variance, in mid2².
    const double variance = static_cast<double>(spread) / static_cast<double>(n * n) / ticks2();
    return std::sqrt(variance / (static_cast<double>(settings_.sample) / 1e9));
  }
  [[nodiscard]] std::optional<double> sigma_jump() const noexcept {
    if (valid_nanos_ == 0) {
      return std::nullopt;
    }
    return std::sqrt(static_cast<double>(jump_squares_) / ticks2() /
                     (static_cast<double>(valid_nanos_) / 1e9));
  }

  // Every depth open to some interval, up to the deepest any reached.
  [[nodiscard]] std::vector<DepthPoint> points() const {
    std::int64_t deepest = 0;
    for (const auto& side : hits_) {
      for (std::size_t j = 0; j < side.size(); ++j) {
        deepest = side[j] > 0 ? std::max(deepest, static_cast<std::int64_t>(j)) : deepest;
      }
    }
    std::vector<DepthPoint> out;
    for (std::int64_t j = 1; j <= deepest; ++j) {
      const auto parity = static_cast<std::size_t>(j % 2);
      if (exposure_.at(parity) == 0) {
        continue;
      }
      std::uint64_t hits = 0;
      for (auto at = static_cast<std::size_t>(j); at < hits_.at(parity).size(); ++at) {
        hits += hits_.at(parity)[at];
      }
      out.push_back({.half_ticks = j, .exposure = exposure_.at(parity), .hits = hits});
    }
    return out;
  }

 private:
  [[nodiscard]] std::size_t cap() const noexcept {
    return static_cast<std::size_t>(2 * settings_.max_depth_ticks);
  }
  [[nodiscard]] double ticks2() const noexcept {
    const double two_ticks = 2.0 * static_cast<double>(settings_.tick);
    return two_ticks * two_ticks;
  }

  template <class Book>
  void refresh(const Book& book) noexcept {
    valid_ = false;
    if (!open_ || !known_ || trading_ != 'T') {
      return;
    }
    const TopOfBook bid = book.best(locate_, Side::bid);
    const TopOfBook ask = book.best(locate_, Side::ask);
    if (bid.present && ask.present && bid.price <= ask.price) {
      valid_ = true;
      mid2_ = Mid2{bid.price} + Mid2{ask.price};
    }
  }

  // Every sample and interval start before `time` takes the state in force, and the
  // nanosecond group before it ends.
  void advance(Nanos time) {
    while (next_sample_ < time) {
      if (valid_ && sample_valid_) {
        const Mid2 change = mid2_ - sample_mid2_;
        ++changes_;
        sum_ += change;
        squares_ += attribution_detail::Wide{change} * change;
      }
      sample_valid_ = valid_;
      sample_mid2_ = mid2_;
      next_sample_ += settings_.sample;
    }
    while (next_interval_ < time) {
      end_interval();
      interval_valid_ = valid_;
      start_mid2_ = mid2_;
      deepest_ = {-1, -1};
      next_interval_ += settings_.interval;
    }
    if (time > group_time_) {
      if (valid_) {
        valid_nanos_ += time - group_time_;
        if (point_valid_ && mid2_ != point_mid2_) {
          const Mid2 jump = mid2_ - point_mid2_;
          ++jumps_;
          jump_squares_ += attribution_detail::Wide{jump} * jump;
        }
      }
      point_valid_ = valid_;
      point_mid2_ = mid2_;
      group_time_ = time;
    }
  }

  void reach(Side side, Price4 price) noexcept {
    ++executions_;
    const Mid2 twice = 2 * Mid2{price};
    const Mid2 depth = side == Side::bid ? start_mid2_ - twice : twice - start_mid2_;
    Mid2& deepest = side == Side::bid ? deepest_[0] : deepest_[1];
    deepest = std::max(deepest, depth);
  }

  void end_interval() {
    if (!interval_valid_) {
      return;
    }
    interval_valid_ = false;
    ++intervals_;
    const Mid2 tick{settings_.tick};
    if (start_mid2_ % tick != 0) {
      ++off_grid_;
      return;
    }
    const auto parity = static_cast<std::size_t>((start_mid2_ / tick) % 2);
    ++exposure_.at(parity);
    for (const Mid2 depth : deepest_) {
      if (depth >= tick) {
        const auto j = std::min(static_cast<std::size_t>(depth / tick), cap());
        ++hits_.at(parity)[j];
      }
    }
  }

  void close() {
    if (clock_ > group_time_) {
      advance(clock_);
    }
    end_interval();
    open_ = false;
    closed_ = true;
    closed_at_ = clock_;
  }

  // Widest first, so the class carries no padding to speak of.
  attribution_detail::Wide sum_ = 0;           // Of the clock's changes, in mid2.
  attribution_detail::Wide squares_ = 0;       // Of their squares.
  attribution_detail::Wide jump_squares_ = 0;  // Of the jumps' squares.
  Nanos opened_at_ = 0;
  Nanos closed_at_ = 0;
  Nanos clock_ = 0;
  Mid2 mid2_ = 0;  // The mid in force: after the last message applied, if valid_.
  Nanos next_sample_ = 0;
  Mid2 sample_mid2_ = 0;
  std::uint64_t changes_ = 0;
  Nanos group_time_ = 0;
  Mid2 point_mid2_ = 0;
  Nanos valid_nanos_ = 0;
  std::uint64_t jumps_ = 0;
  Nanos next_interval_ = 0;
  Mid2 start_mid2_ = 0;
  std::uint64_t intervals_ = 0;
  std::uint64_t off_grid_ = 0;
  std::uint64_t executions_ = 0;
  std::array<Mid2, 2> deepest_{-1, -1};      // Bid side, ask side; −1 for none.
  std::array<std::uint64_t, 2> exposure_{};  // By the parity of the depths open.
  CalibrationSettings settings_;
  // By parity, then the deepest half-tick depth reached, capped.
  std::array<std::vector<std::uint64_t>, 2> hits_;
  std::uint16_t locate_ = 0;
  bool known_ = false;
  char trading_ = ' ';
  bool open_ = false;
  bool closed_ = false;
  bool valid_ = false;
  bool sample_valid_ = false;
  bool point_valid_ = false;
  bool interval_valid_ = false;
  Stock symbol_;
};

}  // namespace itch::mm
