// The market maker's PnL in three parts that sum exactly to its total: spread capture, adverse
// selection and inventory (BENCHMARKS.md, T-MM-1; the proof and a worked example are in the
// research evidence, market-maker.md, "Attribution identity").
//
//     S = Σᵢ qᵢ(mᵢ⁻ − Pᵢ) − Σᵢ fᵢ               spread capture, fees inside it as a sub-line
//     A = Σᵢ qᵢ(mᵢʰ − mᵢ⁻)                      adverse selection
//     I = Q₀(m_T − m₀) + Σᵢ qᵢ(m_T − mᵢʰ)       inventory
//
// with mᵢ⁻ = m(tᵢ−), mᵢʰ = m(min(tᵢ + h, T)), m₀ = m(t₀) and m_T = m(T), for fills i of signed
// quantity qᵢ at price Pᵢ and fee fᵢ, over the window [t₀, T] with opening position Q₀.
//
// This module reads fills and marks only. It never reads the ledger (ledger.hpp), which
// computes the total from cash and position, so that a test can hold the two to each other
// with `==`. Inventory is never the residual of the other two: it is computed term by term,
// and a second, independent computation of A and I walks the mark path instead of the fills:
//
//     A = Σₖ Δmₖ · Y(tₖ),   Y(t) = Σᵢ qᵢ · 1{tᵢ ≤ t ≤ tᵢ + h}         the young position
//     I = Σₖ Δmₖ · O(tₖ),   O(t) = Q₀ · 1{t > t₀} + Σᵢ qᵢ · 1{tᵢ + h < t}   the old position
//
// over the jumps Δmₖ = m(tₖ) − m(tₖ−) at tₖ ≤ T. Both forms must agree exactly.
//
// Every amount is an integer (fills.hpp, "Units"), worked in 128 bits. A component beyond a
// quarter of the 64-bit range, about $2.3 million, is refused rather than wrapped, so that
// their sum cannot overflow either.
#pragma once

#include <cstddef>
#include <cstdint>
#include <expected>
#include <limits>
#include <optional>
#include <span>
#include <string_view>
#include <vector>

#include "itch/mm/fills.hpp"

namespace itch::mm {

// h past the close: every window is cut at T, and A takes all of positioning.
inline constexpr Nanos kPastClose = std::numeric_limits<Nanos>::max();

enum class AttributionError : std::uint8_t {
  marks_out_of_order,   // The mark path's times do not strictly increase.
  fills_out_of_order,   // A fill before the one listed ahead of it.
  fill_outside_window,  // A fill before t₀ or after T, or t₀ after T.
  quantity_too_large,   // A fill or Q₀ beyond kMaxQuantity.
  mark_undefined,       // No mark at t₀, or none strictly before a fill.
  overflow,             // A component beyond a quarter of the 64-bit range.
};

[[nodiscard]] constexpr std::string_view to_string(AttributionError error) noexcept {
  switch (error) {
    case AttributionError::marks_out_of_order: return "marks_out_of_order";
    case AttributionError::fills_out_of_order: return "fills_out_of_order";
    case AttributionError::fill_outside_window: return "fill_outside_window";
    case AttributionError::quantity_too_large: return "quantity_too_large";
    case AttributionError::mark_undefined: return "mark_undefined";
    case AttributionError::overflow: return "overflow";
  }
  return "unnamed";
}

struct Components {
  Picodollars gross_spread = 0;  // Σ qᵢ(mᵢ⁻ − Pᵢ)
  Picodollars fees = 0;          // Σ fᵢ, paid positive
  Picodollars adverse_selection = 0;
  Picodollars inventory = 0;

  [[nodiscard]] constexpr Picodollars spread_capture() const noexcept {
    return gross_spread - fees;
  }
  [[nodiscard]] constexpr Picodollars total() const noexcept {
    return spread_capture() + adverse_selection + inventory;
  }
  friend bool operator==(const Components&, const Components&) = default;
};

// One fill's share of each component. The opening position's inventory term is in no row.
struct FillTerms {
  Picodollars gross_spread = 0;
  Picodollars fee = 0;
  Picodollars adverse_selection = 0;
  Picodollars inventory = 0;

  friend bool operator==(const FillTerms&, const FillTerms&) = default;
};

namespace attribution_detail {

using Wide = __int128;

inline constexpr Wide kLimit = Wide{std::numeric_limits<Picodollars>::max()} / 4;

[[nodiscard]] constexpr std::optional<Picodollars> narrow(Wide value) noexcept {
  if (value > kLimit || value < -kLimit) {
    return std::nullopt;
  }
  return static_cast<Picodollars>(value);
}

[[nodiscard]] constexpr bool too_large(std::int64_t quantity) noexcept {
  return quantity > kMaxQuantity || quantity < -kMaxQuantity;
}

// The end of fill i's window: min(tᵢ + h, T), with no overflow for h past the close.
[[nodiscard]] constexpr Nanos horizon_end(Nanos fill, Nanos horizon, Nanos close) noexcept {
  return horizon >= close - fill ? close : fill + horizon;
}

// What both forms require of their input, so that they refuse the same things.
[[nodiscard]] inline std::optional<AttributionError> validate(std::span<const Fill> fills,
                                                              const MarkPath& marks,
                                                              const Window& window) noexcept {
  if (!marks.ordered()) {
    return AttributionError::marks_out_of_order;
  }
  if (window.open > window.close) {
    return AttributionError::fill_outside_window;
  }
  if (too_large(window.opening_position)) {
    return AttributionError::quantity_too_large;
  }
  if (!marks.at(window.open)) {
    return AttributionError::mark_undefined;
  }
  Nanos last = window.open;
  for (const Fill& fill : fills) {
    if (fill.time < window.open || fill.time > window.close) {
      return AttributionError::fill_outside_window;
    }
    if (fill.time < last) {
      return AttributionError::fills_out_of_order;
    }
    if (too_large(fill.quantity)) {
      return AttributionError::quantity_too_large;
    }
    if (!marks.before(fill.time)) {
      return AttributionError::mark_undefined;
    }
    last = fill.time;
  }
  return std::nullopt;
}

}  // namespace attribution_detail

// Each fill's terms, in the order given. Sums of these, with Q₀(m_T − m₀) added to inventory,
// are the per-fill form.
[[nodiscard]] inline std::expected<std::vector<FillTerms>, AttributionError> fill_terms(
    std::span<const Fill> fills, const MarkPath& marks, const Window& window, Nanos horizon) {
  using attribution_detail::narrow;
  using attribution_detail::Wide;
  if (const auto error = attribution_detail::validate(fills, marks, window)) {
    return std::unexpected(*error);
  }
  const Wide unit = kPicodollarsPerMid2;
  const std::optional<Mid2> close = marks.at(window.close);
  std::vector<FillTerms> rows;
  rows.reserve(fills.size());
  for (const Fill& fill : fills) {
    const std::optional<Mid2> before = marks.before(fill.time);
    const std::optional<Mid2> later =
        marks.at(attribution_detail::horizon_end(fill.time, horizon, window.close));
    if (!close || !before || !later) {
      return std::unexpected(AttributionError::mark_undefined);
    }
    const Wide quantity = fill.quantity;
    const Wide price2 = Wide{2} * fill.price;
    const auto gross = narrow(quantity * (Wide{*before} - price2) * unit);
    const auto adverse = narrow(quantity * (Wide{*later} - *before) * unit);
    const auto inventory = narrow(quantity * (Wide{*close} - *later) * unit);
    if (!gross || !adverse || !inventory) {
      return std::unexpected(AttributionError::overflow);
    }
    rows.push_back({.gross_spread = *gross,
                    .fee = fill.fee,
                    .adverse_selection = *adverse,
                    .inventory = *inventory});
  }
  return rows;
}

// The per-fill form: S, A and I fill by fill, then the opening position's inventory.
[[nodiscard]] inline std::expected<Components, AttributionError> attribute(
    std::span<const Fill> fills, const MarkPath& marks, const Window& window, Nanos horizon) {
  using attribution_detail::narrow;
  using attribution_detail::Wide;
  const auto rows = fill_terms(fills, marks, window, horizon);
  if (!rows) {
    return std::unexpected(rows.error());
  }
  const std::optional<Mid2> open = marks.at(window.open);
  const std::optional<Mid2> close = marks.at(window.close);
  if (!open || !close) {
    return std::unexpected(AttributionError::mark_undefined);
  }
  Wide gross = 0;
  Wide fees = 0;
  Wide adverse = 0;
  Wide inventory =
      Wide{window.opening_position} * (Wide{*close} - *open) * Wide{kPicodollarsPerMid2};
  for (const FillTerms& row : *rows) {
    gross += row.gross_spread;
    fees += row.fee;
    adverse += row.adverse_selection;
    inventory += row.inventory;
  }
  const auto s = narrow(gross);
  const auto f = narrow(fees);
  const auto a = narrow(adverse);
  const auto i = narrow(inventory);
  if (!s || !f || !a || !i) {
    return std::unexpected(AttributionError::overflow);
  }
  return Components{.gross_spread = *s, .fees = *f, .adverse_selection = *a, .inventory = *i};
}

// The path form of A and I: one walk over the mark's jumps up to T, carrying the young and
// the old position. Shares nothing with the per-fill form but the input checks.
struct PathSplit {
  Picodollars adverse_selection = 0;
  Picodollars inventory = 0;

  friend bool operator==(const PathSplit&, const PathSplit&) = default;
};

[[nodiscard]] inline std::expected<PathSplit, AttributionError> attribute_by_path(
    std::span<const Fill> fills, const MarkPath& marks, const Window& window, Nanos horizon) {
  using attribution_detail::narrow;
  using attribution_detail::Wide;
  if (const auto error = attribution_detail::validate(fills, marks, window)) {
    return std::unexpected(*error);
  }
  const std::span<const MarkPoint> points = marks.points();
  Wide adverse = 0;
  Wide inventory = 0;
  Wide entered_position = 0;  // Fills at or before tₖ.
  Wide aged_position = 0;     // Fills whose window closed before tₖ.
  std::size_t entered = 0;
  std::size_t aged = 0;
  for (std::size_t k = 1; k < points.size() && points[k].time <= window.close; ++k) {
    const Nanos time = points[k].time;
    while (entered < fills.size() && fills[entered].time <= time) {
      entered_position += fills[entered].quantity;
      ++entered;
    }
    while (aged < entered && time - fills[aged].time > horizon) {
      aged_position += fills[aged].quantity;
      ++aged;
    }
    const Wide young = entered_position - aged_position;
    const Wide old = (time > window.open ? Wide{window.opening_position} : Wide{0}) + aged_position;
    const Wide jump = (Wide{points[k].mid2} - Wide{points[k - 1].mid2}) * kPicodollarsPerMid2;
    adverse += jump * young;
    inventory += jump * old;
  }
  const auto a = narrow(adverse);
  const auto i = narrow(inventory);
  if (!a || !i) {
    return std::unexpected(AttributionError::overflow);
  }
  return PathSplit{.adverse_selection = *a, .inventory = *i};
}

}  // namespace itch::mm
