// The age of the order each message names, so that counts can be charged to message type and
// order age together (NOTES.md, "Attributing the misses").
//
// age_slots() walks the day once, before anything is counted, through a book of its own, and
// gives each frame the slot of typed_counts.hpp's tally its span is to be charged to:
//
//   - a frame that is not a message, slot 0, as in typed_counts.hpp;
//   - a message that names a live order, E, C, X, D and U, 128 + 8 * its row + its age
//     class, the age being the message's timestamp less that of the add, the A, F or U, that
//     made the order;
//   - any other message, its type byte, as in typed_counts.hpp. A and F add an order and
//     name none.
//
// So the type bytes, all below 128, keep their slots, and a named order's slots sit above
// them, one row a type and one column an age. The counted pass then reads one byte a frame
// from this array in order, which the hardware prefetcher streams, and does no lookup of its
// own: the book under count makes exactly the loads it makes without it.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string_view>
#include <vector>

#include "itch/book_types.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"
#include "typed_counts.hpp"

namespace itch::bench {

inline constexpr std::array<char, 5> kNamingTypes{OrderExecuted::kType,
                                                  OrderExecutedWithPrice::kType, OrderCancel::kType,
                                                  OrderDelete::kType, OrderReplace::kType};

// Ages from the add, in nanoseconds of the feed's own timestamps: under 1 ms, then each
// decade to 1 s, then 1 s and over. unknown is a reference that is not live, which a clean
// day never sends.
enum class AgeClass : std::uint8_t {
  under_1ms = 0,
  under_10ms,
  under_100ms,
  under_1s,
  from_1s,
  unknown
};
inline constexpr std::size_t kAgeClasses = 6;
inline constexpr std::array<std::string_view, kAgeClasses> kAgeNames{
    "under 1 ms", "1 to 10 ms", "10 to 100 ms", "100 ms to 1 s", "1 s and over", "not live"};
inline constexpr std::uint8_t kFirstAgeSlot = 128;
inline constexpr std::uint8_t kAgeRow = 8;

[[nodiscard]] constexpr AgeClass age_class(std::uint64_t nanoseconds) noexcept {
  constexpr std::uint64_t kMillisecond = 1'000'000;
  if (nanoseconds < kMillisecond) {
    return AgeClass::under_1ms;
  }
  if (nanoseconds < 10 * kMillisecond) {
    return AgeClass::under_10ms;
  }
  if (nanoseconds < 100 * kMillisecond) {
    return AgeClass::under_100ms;
  }
  return nanoseconds < 1000 * kMillisecond ? AgeClass::under_1s : AgeClass::from_1s;
}

[[nodiscard]] constexpr std::optional<std::size_t> naming_row(char type) noexcept {
  std::size_t row = 0;
  for (const char naming : kNamingTypes) {
    if (naming == type) {
      return row;
    }
    ++row;
  }
  return std::nullopt;
}

[[nodiscard]] constexpr std::uint8_t age_slot(std::size_t row, AgeClass age) noexcept {
  return static_cast<std::uint8_t>(kFirstAgeSlot + (row * kAgeRow) + static_cast<std::size_t>(age));
}

// What a slot above the type bytes stands for, or nothing for a type byte's own slot.
struct NamedSlot {
  char type;
  AgeClass age;
};

[[nodiscard]] constexpr std::optional<NamedSlot> named_slot(std::uint8_t slot) noexcept {
  if (slot < kFirstAgeSlot) {
    return std::nullopt;
  }
  const std::size_t row = (slot - kFirstAgeSlot) / kAgeRow;
  const std::size_t age = (slot - kFirstAgeSlot) % kAgeRow;
  if (row >= kNamingTypes.size() || age >= kAgeClasses) {
    return std::nullopt;
  }
  return NamedSlot{.type = kNamingTypes[row], .age = static_cast<AgeClass>(age)};
}

// Each frame's slot, in the order a FrameReader walks them. `book` must be fresh and is left
// as the day leaves it; `added_at` holds one timestamp for each of its order slots.
template <class Book>
[[nodiscard]] std::vector<std::uint8_t> age_slots(std::span<const std::byte> input, Book& book) {
  std::vector<std::uint64_t> added_at(book.order_capacity(), 0);
  std::vector<std::uint8_t> slots;
  const auto stamp = [&](std::uint64_t reference, std::uint64_t at) {
    const std::uint32_t order = book.order_slot(reference);
    if (order != kNoIndex) {
      added_at[order] = at;
    }
  };
  FrameReader reader{input};
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != FrameStatus::message) {
      slots.push_back(kNoMessageSlot);
      continue;
    }
    const std::byte* const m = frame.payload.data();
    const char type = read_char(m);
    const bool whole = frame.payload.size() == message_length(type);
    auto slot = static_cast<std::uint8_t>(type);
    const std::optional<std::size_t> row = naming_row(type);
    const std::uint64_t now = whole ? read_be48(m + 5) : 0;
    if (whole && row.has_value()) {
      // Every naming type, and both adds, have the order's reference at offset 11.
      const std::uint32_t order = book.order_slot(read_be<std::uint64_t>(m + 11));
      const AgeClass age = order == kNoIndex || now < added_at[order]
                               ? AgeClass::unknown
                               : age_class(now - added_at[order]);
      slot = age_slot(*row, age);
    }
    const bool applied = book.apply(frame.payload) == ApplyStatus::applied;
    if (applied && (type == AddOrder::kType || type == AddOrderAttributed::kType)) {
      stamp(read_be<std::uint64_t>(m + 11), now);
    } else if (applied && type == OrderReplace::kType) {
      stamp(OrderReplace::from_wire(m).new_order_reference, now);
    }
    slots.push_back(slot);
  }
  return slots;
}

}  // namespace itch::bench
