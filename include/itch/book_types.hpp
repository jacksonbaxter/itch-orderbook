// What every arm of the book shares: the statuses, the write policy, the capacities, and
// the order record that each FIFO is made of.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string_view>
#include <type_traits>

#include "itch/messages.hpp"

namespace itch {

// What apply() did with a message. Every status after no_book_effect left the book as
// it was.
enum class ApplyStatus : std::uint8_t {
  applied,
  no_book_effect,       // A valid message of a type that does not change the book.
  unknown_order,        // The reference is not live.
  locate_mismatch,      // The order is live on another locate.
  over_deduction,       // More shares than the order has left.
  duplicate_reference,  // A new reference that is already live.
  bad_side,             // A side other than 'B' or 'S'.
  capacity_exhausted,   // The preallocated pools are full.
};

[[nodiscard]] constexpr std::string_view to_string(ApplyStatus status) noexcept {
  switch (status) {
    case ApplyStatus::applied: return "applied";
    case ApplyStatus::no_book_effect: return "no_book_effect";
    case ApplyStatus::unknown_order: return "unknown_order";
    case ApplyStatus::locate_mismatch: return "locate_mismatch";
    case ApplyStatus::over_deduction: return "over_deduction";
    case ApplyStatus::duplicate_reference: return "duplicate_reference";
    case ApplyStatus::bad_side: return "bad_side";
    case ApplyStatus::capacity_exhausted: return "capacity_exhausted";
  }
  return "unnamed";
}

enum class Side : std::uint8_t { bid = 0, ask = 1 };

// The default write policy: nothing, at no cost.
//
// on_level_write names a level whose contents a write changes: its orders, its shares or
// its count, or the level itself coming or going. on_level_move names a level whose
// contents stay as they are while the structure that finds it by price is rewritten,
// such as a neighbour's link.
struct NoWriteObserver {
  static constexpr void on_level_write(std::uint16_t /*locate*/, Side /*side*/,
                                       Price4 /*price*/) noexcept {}
  static constexpr void on_level_move(std::uint16_t /*locate*/, Side /*side*/,
                                      Price4 /*price*/) noexcept {}

  friend bool operator==(const NoWriteObserver&, const NoWriteObserver&) = default;
};

// Everything the book holds is sized from this in the constructor. An arm reads only
// the fields it uses.
struct BookCapacity {
  std::uint32_t orders = 1U << 22U;
  std::uint32_t levels = 1U << 21U;
};

inline constexpr std::uint32_t kNoIndex = 0xFFFFFFFFU;

struct BookOrder {
  std::uint64_t reference;
  std::uint32_t shares;
  std::uint32_t level;
  std::uint32_t older;  // Towards the front of the FIFO.
  std::uint32_t newer;  // Towards the back of the FIFO.

  friend bool operator==(const BookOrder&, const BookOrder&) = default;
};

static_assert(sizeof(BookOrder) == 24);
static_assert(alignof(BookOrder) == 8);
static_assert(std::is_trivially_copyable_v<BookOrder>);

// A level by identity.
struct LevelId {
  std::uint16_t locate;
  Side side;
  Price4 price;
};

struct TopOfBook {
  bool present;
  Price4 price;
  std::uint64_t shares;
  std::uint32_t orders;

  friend bool operator==(const TopOfBook&, const TopOfBook&) = default;
};

// One live order, as the book holds it.
struct RestingOrder {
  std::uint64_t reference;
  std::uint16_t locate;
  Side side;
  Price4 price;
  std::uint32_t shares;
  bool at_front;  // First in its level's FIFO.
};

// Each side's record lives at 2 * locate + side.
[[nodiscard]] constexpr std::size_t side_key(std::uint16_t locate, Side side) noexcept {
  return (std::size_t{locate} << 1U) | static_cast<std::size_t>(side);
}

inline constexpr std::size_t kSideCount = std::size_t{2} * 65536U;

}  // namespace itch
