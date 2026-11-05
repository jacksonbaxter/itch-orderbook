// What the market maker's PnL is computed from: its fills, and the mark path of the replayed
// book. The ledger (ledger.hpp) and the attribution (attribution.hpp) both read these and
// nothing of each other.
//
// **Units.** Every amount is an integer. A price is ITCH's Price4, 10^-4 of a dollar. A mark
// is a Mid2, best bid plus best ask in Price4, which is twice the mid: a mid half a tick off
// the grid is still whole. Money is in picodollars, 10^-12 of a dollar, the unit fees.hpp
// already uses, so a fee needs no rounding to join a spread. One Price4 is 10^8 picodollars
// and one Mid2 is 5 × 10^7.
#pragma once

#include <algorithm>
#include <cstdint>
#include <optional>
#include <span>

#include "itch/fees.hpp"
#include "itch/messages.hpp"

namespace itch::mm {

using Nanos = std::uint64_t;
using Mid2 = std::int64_t;

inline constexpr Picodollars kPicodollarsPerPrice4 = 100'000'000;
inline constexpr Picodollars kPicodollarsPerMid2 = kPicodollarsPerPrice4 / 2;

// One execution of the strategy's own order. A buy has a positive quantity and a sell a
// negative one. The fee is the fill's whole FeeBreakdown::total(): positive is paid, negative
// is a rebate received. The time is the exchange's, when the fill happened.
struct Fill {
  Nanos time = 0;
  std::int64_t quantity = 0;
  Price4 price = 0;
  Picodollars fee = 0;

  friend bool operator==(const Fill&, const Fill&) = default;
};

// The mark from `time` on, until the next point.
struct MarkPoint {
  Nanos time = 0;
  Mid2 mid2 = 0;

  friend bool operator==(const MarkPoint&, const MarkPoint&) = default;
};

// A right-continuous step function over strictly increasing times. m(t) is the last point at
// or before t, and m(t−) the last point strictly before t, so a fill at t is marked before
// every message of its nanosecond, the burst that filled it included.
class MarkPath {
 public:
  explicit MarkPath(std::span<const MarkPoint> points) noexcept : points_{points} {}

  [[nodiscard]] std::span<const MarkPoint> points() const noexcept { return points_; }

  // True if the times strictly increase.
  [[nodiscard]] bool ordered() const noexcept {
    return std::ranges::adjacent_find(points_, [](const MarkPoint& a, const MarkPoint& b) {
             return a.time >= b.time;
           }) == points_.end();
  }

  // m(t): none before the first point.
  [[nodiscard]] std::optional<Mid2> at(Nanos time) const noexcept {
    const auto after = std::ranges::upper_bound(points_, time, {}, &MarkPoint::time);
    if (after == points_.begin()) {
      return std::nullopt;
    }
    return std::prev(after)->mid2;
  }

  // m(t−): none at or before the first point.
  [[nodiscard]] std::optional<Mid2> before(Nanos time) const noexcept {
    const auto at_or_after = std::ranges::lower_bound(points_, time, {}, &MarkPoint::time);
    if (at_or_after == points_.begin()) {
      return std::nullopt;
    }
    return std::prev(at_or_after)->mid2;
  }

 private:
  std::span<const MarkPoint> points_;
};

// The session the PnL covers: from `open` (t₀) to `close` (T), with the position held at
// `open` (Q₀). Every fill must fall in [open, close].
struct Window {
  Nanos open = 0;
  Nanos close = 0;
  std::int64_t opening_position = 0;
};

// A fill quantity or position beyond this is refused, so that no product below can come near
// the 127 bits it is worked in: 2^32 shares × 2^34 Mid2 × 2^26 picodollars is 2^92, and a sum
// of 2^34 of them still fits.
inline constexpr std::int64_t kMaxQuantity = 4'294'967'296;  // 2^32

}  // namespace itch::mm
