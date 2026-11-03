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
// contents stay as they are while the structure that finds it by price is rewritten: a
// neighbour's link, or its entry in a flat array that shifts or moves to a bigger block.
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
  // PagedIndex: pages of direct slots, 1 KiB each. The most pages pinned at once is
  // 340,939 on 12302019 and 305,506 on 10302019, so this is the larger peak with a
  // quarter more, rounded up to 4,096 pages: 418 MiB of slots. A busier day that runs the
  // pool dry falls back to a hash table for each reference it cannot place, and counts it.
  std::uint32_t index_pages = 428'032;
  // PagedIndex: references below 2^index_bits index directly, through a directory of
  // one entry per page. The largest reference is 308,631,072 on 12302019 and 316,375,126
  // on 10302019, so 2^30 reaches more than three times past both, for a 16 MiB
  // directory. A reference past the reach falls back as well. At most 40.
  std::uint32_t index_bits = 30;
  // FlatLevels: array entries shared by every side's block. 12302019 reaches 1,642,464.
  std::uint32_t level_slots = 1U << 22U;
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
