// Where each output byte of a kernel comes from, worked out once at compile time and shared
// by every vector kernel, so that the kernels differ only in the instructions that move the
// bytes and never in which bytes they move.
//
// A kernel turns a message into a 32-byte record: a BookOp for the book's seven types, or
// AddFields for an Add Order (E2 in simd.md). Each output byte is one wire byte or a zero,
// and every multi-byte field lands little-endian, so the byte swap is part of the move.
//
// Vector loads are 16 bytes. A Gather also says which 16-byte windows of the message to load
// so that every field lies whole inside one window and no window passes the message's end:
// a window is anchored at a field's first byte, or at length - 16 when that would pass the
// end. Every book type is at least 19 bytes long, so the windows never start before the
// message either. Nothing here reads a byte; it only plans.
#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

#include "itch/book_op.hpp"
#include "itch/messages.hpp"

namespace itch::kernels {

inline constexpr std::size_t kWindow = 16;
inline constexpr std::size_t kRecord = 32;
inline constexpr std::uint8_t kZero = 0xFF;  // An output byte that is zero.
inline constexpr std::size_t kMostWindows = 3;

// An Add Order's fields for E2: every multi-byte field the message has, the stock left out,
// in 32 bytes so that one record is two 16-byte vectors or one 32-byte one.
struct AddFields {
  std::uint64_t reference = 0;
  std::uint64_t timestamp_ns = 0;  // 6 bytes on the wire.
  std::uint32_t shares = 0;
  Price4 price = 0;
  std::uint16_t locate = 0;
  std::uint16_t tracking = 0;
  char side = 0;
  std::array<char, 3> padding{};

  friend bool operator==(const AddFields&, const AddFields&) = default;
};
static_assert(sizeof(AddFields) == kRecord);
static_assert(sizeof(BookOp) == kRecord);

// Four Add Orders' book fields, one array a field, for E5's batch: the layout a batched
// consumer would read, and the one a vector byte swap can fill a register at a time.
struct AddBatch {
  std::array<std::uint64_t, 4> reference{};
  std::array<std::uint32_t, 4> shares{};
  std::array<Price4, 4> price{};
  std::array<std::uint16_t, 4> locate{};

  friend bool operator==(const AddBatch&, const AddBatch&) = default;
};

struct Field {
  std::uint8_t at = 0;     // First wire byte.
  std::uint8_t width = 0;  // 1 is copied; 2, 4, 6 and 8 are swapped.
  std::uint8_t to = 0;     // First output byte.
};

struct Gather {
  std::uint8_t length = 0;                   // The message's length.
  std::array<std::uint8_t, kRecord> from{};  // Wire byte of each output byte, or kZero.
  std::uint8_t windows = 0;
  std::array<std::uint8_t, kMostWindows> anchor{};  // First wire byte of each window.
  // The window each output byte comes from, and its place in that window, or kZero.
  std::array<std::uint8_t, kRecord> window{};
  std::array<std::uint8_t, kRecord> lane{};
  std::uint8_t kind = 0;         // A BookOpKind, or 0 for AddFields.
  std::uint8_t kind_at = kZero;  // The output byte the kind goes in, or kZero for none.

  // Whether output half h (bytes 16h to 16h + 15) takes any byte from window w.
  [[nodiscard]] constexpr bool uses(std::size_t h, std::size_t w) const noexcept {
    for (std::size_t i = h * kWindow; i < (h + 1) * kWindow; ++i) {
      if (window[i] == w) {
        return true;
      }
    }
    return false;
  }
};

// The shuffle index that moves window w's bytes into output half h, for an instruction that
// reads one 16-byte register: each lane's place in the window, or `none` for a lane that
// window does not fill (0x80 makes PSHUFB write a zero; 16 points WebAssembly's two-input
// shuffle at a zero vector).
[[nodiscard]] constexpr std::array<std::uint8_t, kWindow> window_index(const Gather& g,
                                                                       std::size_t h, std::size_t w,
                                                                       std::uint8_t none) noexcept {
  std::array<std::uint8_t, kWindow> index{};
  for (std::size_t i = 0; i < kWindow; ++i) {
    const std::size_t out = (h * kWindow) + i;
    index[i] = g.window[out] == w ? g.lane[out] : none;
  }
  return index;
}

template <std::size_t N>
[[nodiscard]] constexpr Gather plan(std::uint8_t length, const std::array<Field, N>& fields,
                                    std::uint8_t kind, std::uint8_t kind_at) noexcept {
  Gather g{};
  g.length = length;
  g.kind = kind;
  g.kind_at = kind_at;
  g.from.fill(kZero);
  g.window.fill(kZero);
  g.lane.fill(kZero);
  // Fields by first byte, so that the windows can be laid left to right.
  std::array<Field, N> sorted = fields;
  for (std::size_t i = 0; i < N; ++i) {
    for (std::size_t j = i + 1; j < N; ++j) {
      if (sorted[j].at < sorted[i].at) {
        const Field held = sorted[i];
        sorted[i] = sorted[j];
        sorted[j] = held;
      }
    }
  }
  for (const Field& f : sorted) {
    const bool fits = g.windows != 0 && f.at + f.width <= g.anchor[g.windows - 1] + kWindow;
    if (!fits) {
      const std::size_t last = length - kWindow;
      g.anchor[g.windows] = static_cast<std::uint8_t>(f.at < last ? f.at : last);
      ++g.windows;
    }
    const auto w = static_cast<std::uint8_t>(g.windows - 1);
    for (std::uint8_t k = 0; k < f.width; ++k) {
      // Big-endian on the wire, little-endian out: the last wire byte comes first.
      const auto wire = static_cast<std::uint8_t>(f.width == 1 ? f.at : f.at + f.width - 1 - k);
      const std::size_t out = f.to + k;
      g.from[out] = wire;
      g.window[out] = w;
      g.lane[out] = static_cast<std::uint8_t>(wire - g.anchor[w]);
    }
  }
  return g;
}

// The BookOp layout, from the struct itself.
inline constexpr std::uint8_t kOpReference = offsetof(BookOp, reference);
inline constexpr std::uint8_t kOpNewReference = offsetof(BookOp, new_reference);
inline constexpr std::uint8_t kOpShares = offsetof(BookOp, shares);
inline constexpr std::uint8_t kOpPrice = offsetof(BookOp, price);
inline constexpr std::uint8_t kOpLocate = offsetof(BookOp, locate);
inline constexpr std::uint8_t kOpSide = offsetof(BookOp, side);
inline constexpr std::uint8_t kOpKind = offsetof(BookOp, kind);

[[nodiscard]] constexpr std::uint8_t kind_byte(BookOpKind kind) noexcept {
  return static_cast<std::uint8_t>(kind);
}

// Offsets from the spec's field tables, as in messages.hpp.
inline constexpr Field kLocate{.at = 1, .width = 2, .to = kOpLocate};
inline constexpr Field kReference{.at = 11, .width = 8, .to = kOpReference};

inline constexpr Gather kAddOp =
    plan(36,
         std::array{kLocate, kReference, Field{.at = 19, .width = 1, .to = kOpSide},
                    Field{.at = 20, .width = 4, .to = kOpShares},
                    Field{.at = 32, .width = 4, .to = kOpPrice}},
         kind_byte(BookOpKind::add), kOpKind);
inline constexpr Gather kAddAttributedOp =
    plan(40,
         std::array{kLocate, kReference, Field{.at = 19, .width = 1, .to = kOpSide},
                    Field{.at = 20, .width = 4, .to = kOpShares},
                    Field{.at = 32, .width = 4, .to = kOpPrice}},
         kind_byte(BookOpKind::add), kOpKind);
inline constexpr Gather kExecutedOp =
    plan(31, std::array{kLocate, kReference, Field{.at = 19, .width = 4, .to = kOpShares}},
         kind_byte(BookOpKind::deduct), kOpKind);
inline constexpr Gather kExecutedWithPriceOp =
    plan(36, std::array{kLocate, kReference, Field{.at = 19, .width = 4, .to = kOpShares}},
         kind_byte(BookOpKind::deduct), kOpKind);
inline constexpr Gather kCancelOp =
    plan(23, std::array{kLocate, kReference, Field{.at = 19, .width = 4, .to = kOpShares}},
         kind_byte(BookOpKind::deduct), kOpKind);
inline constexpr Gather kDeleteOp =
    plan(19, std::array{kLocate, kReference}, kind_byte(BookOpKind::erase), kOpKind);
inline constexpr Gather kReplaceOp =
    plan(35,
         std::array{kLocate, kReference, Field{.at = 19, .width = 8, .to = kOpNewReference},
                    Field{.at = 27, .width = 4, .to = kOpShares},
                    Field{.at = 31, .width = 4, .to = kOpPrice}},
         kind_byte(BookOpKind::replace), kOpKind);

inline constexpr Gather kAddFields =
    plan(36,
         std::array{Field{.at = 1, .width = 2, .to = offsetof(AddFields, locate)},
                    Field{.at = 3, .width = 2, .to = offsetof(AddFields, tracking)},
                    Field{.at = 5, .width = 6, .to = offsetof(AddFields, timestamp_ns)},
                    Field{.at = 11, .width = 8, .to = offsetof(AddFields, reference)},
                    Field{.at = 19, .width = 1, .to = offsetof(AddFields, side)},
                    Field{.at = 20, .width = 4, .to = offsetof(AddFields, shares)},
                    Field{.at = 32, .width = 4, .to = offsetof(AddFields, price)}},
         0, kZero);

// The types a kernel decodes into a BookOp, the ones FastBook acts on.
inline constexpr std::array kOpTypes{AddOrder::kType,      AddOrderAttributed::kType,
                                     OrderExecuted::kType, OrderExecutedWithPrice::kType,
                                     OrderCancel::kType,   OrderDelete::kType,
                                     OrderReplace::kType};

[[nodiscard]] constexpr bool is_op_type(char type) noexcept {
  return std::ranges::any_of(kOpTypes, [type](char t) { return t == type; });
}

// The plan for each op type, so a kernel can switch on the type byte once and then run a
// gather fixed at compile time.
template <char Type>
inline constexpr const Gather& kOpPlan = kAddOp;
template <>
inline constexpr const Gather& kOpPlan<AddOrderAttributed::kType> = kAddAttributedOp;
template <>
inline constexpr const Gather& kOpPlan<OrderExecuted::kType> = kExecutedOp;
template <>
inline constexpr const Gather& kOpPlan<OrderExecutedWithPrice::kType> = kExecutedWithPriceOp;
template <>
inline constexpr const Gather& kOpPlan<OrderCancel::kType> = kCancelOp;
template <>
inline constexpr const Gather& kOpPlan<OrderDelete::kType> = kDeleteOp;
template <>
inline constexpr const Gather& kOpPlan<OrderReplace::kType> = kReplaceOp;

// Every plan is held to its message's length, and to the windows it promised.
[[nodiscard]] constexpr bool plan_in_bounds(const Gather& g) noexcept {
  if (g.windows == 0 || g.windows > kMostWindows) {
    return false;
  }
  for (std::size_t w = 0; w < g.windows; ++w) {
    if (g.anchor[w] + kWindow > g.length) {
      return false;
    }
  }
  for (std::size_t i = 0; i < kRecord; ++i) {
    if (g.from[i] != kZero &&
        (g.from[i] >= g.length || g.window[i] >= g.windows || g.lane[i] >= kWindow ||
         g.anchor[g.window[i]] + g.lane[i] != g.from[i])) {
      return false;
    }
  }
  return true;
}
static_assert(plan_in_bounds(kAddOp) && plan_in_bounds(kAddAttributedOp) &&
              plan_in_bounds(kExecutedOp) && plan_in_bounds(kExecutedWithPriceOp) &&
              plan_in_bounds(kCancelOp) && plan_in_bounds(kDeleteOp) &&
              plan_in_bounds(kReplaceOp) && plan_in_bounds(kAddFields));
static_assert(kAddOp.length == message_length(AddOrder::kType) &&
              kAddAttributedOp.length == message_length(AddOrderAttributed::kType) &&
              kExecutedOp.length == message_length(OrderExecuted::kType) &&
              kExecutedWithPriceOp.length == message_length(OrderExecutedWithPrice::kType) &&
              kCancelOp.length == message_length(OrderCancel::kType) &&
              kDeleteOp.length == message_length(OrderDelete::kType) &&
              kReplaceOp.length == message_length(OrderReplace::kType));

}  // namespace itch::kernels
