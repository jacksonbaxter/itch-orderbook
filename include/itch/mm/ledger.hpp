// The market maker's total PnL from cash and position alone: what the attribution
// (attribution.hpp) must sum to, computed without it.
//
//     cash      C_T = C₀ − Σᵢ qᵢPᵢ − Σᵢ fᵢ
//     position  Q_T = Q₀ + Σᵢ qᵢ
//     total     Π = (C_T + Q_T·m_T) − (C₀ + Q₀·m₀)
//
// Inventory is marked at the mid with no exit cost, as Avellaneda and Stoikov value it at T.
// Cash is held in 128 bits, since a day's notional passes the 64-bit range of picodollars
// ($9.2 million) long before its PnL does.
#pragma once

#include <cstdint>
#include <limits>
#include <optional>
#include <span>

#include "itch/mm/fills.hpp"

namespace itch::mm {

class Ledger {
 public:
  explicit Ledger(std::int64_t opening_position = 0) noexcept
      : opening_position_{opening_position}, position_{opening_position} {}

  void record(const Fill& fill) noexcept {
    cash_ -= Wide{fill.quantity} * Wide{fill.price} * Wide{kPicodollarsPerPrice4};
    cash_ -= fill.fee;
    position_ += fill.quantity;
  }

  [[nodiscard]] std::int64_t position() const noexcept { return position_; }

  // Π between the opening mark and the closing one. None past a quarter of the 64-bit range,
  // as the attribution refuses.
  [[nodiscard]] std::optional<Picodollars> total(Mid2 opening, Mid2 closing) const noexcept {
    const Wide held = (Wide{position_} * closing) - (Wide{opening_position_} * opening);
    const Wide value = cash_ + (held * Wide{kPicodollarsPerMid2});
    constexpr Wide kLimit = Wide{std::numeric_limits<Picodollars>::max()} / 4;
    if (value > kLimit || value < -kLimit) {
      return std::nullopt;
    }
    return static_cast<Picodollars>(value);
  }

  // A whole fill log at once.
  [[nodiscard]] static std::optional<Picodollars> total_of(std::span<const Fill> fills,
                                                           std::int64_t opening_position,
                                                           Mid2 opening, Mid2 closing) noexcept {
    Ledger ledger{opening_position};
    for (const Fill& fill : fills) {
      ledger.record(fill);
    }
    return ledger.total(opening, closing);
  }

 private:
  using Wide = __int128;

  std::int64_t opening_position_;
  std::int64_t position_;
  Wide cash_ = 0;  // Relative to C₀.
};

}  // namespace itch::mm
