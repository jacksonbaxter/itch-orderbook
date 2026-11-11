// The two level arms: how each (locate, side) keeps its levels in price order.
//
// Each is a policy of FastBook. It owns the level records and the structure that orders
// them, and sizes both in the constructor. FastBook owns the orders and every FIFO, and
// it is the only caller of open() and close(), which it makes from its write choke
// point. Those two report through `moved` each other level whose place in the structure
// they rewrite, before rewriting it.
//
// Stub quotes are real and span almost the whole price field (DS-73), so neither arm
// indexes a level by its price. Both search by comparing prices, and a far price is just
// one more entry at the far end.
//
// For a reader running ahead of the book (itch/lookahead.hpp), each arm can ask for a
// level record's line, a side's record, and the first thing a search of that side reads
// after it. Each only reads, takes any index, and asks for nothing past its storage.
#pragma once

#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <span>
#include <type_traits>
#include <vector>

#include "itch/book_types.hpp"

namespace itch {

// A fixed pool of level records with a free list threaded through one field.
template <class Level, std::uint32_t Level::* kLink>
class LevelPool {
 public:
  explicit LevelPool(std::uint32_t capacity) : levels_(capacity) {}

  [[nodiscard]] Level& operator[](std::uint32_t at) noexcept { return levels_[at]; }
  [[nodiscard]] const Level& operator[](std::uint32_t at) const noexcept { return levels_[at]; }

  [[nodiscard]] bool has_free() const noexcept {
    return free_ != kNoIndex || fresh_ < levels_.size();
  }
  [[nodiscard]] std::size_t size() const noexcept { return levels_.size(); }

  [[nodiscard]] std::uint32_t take() noexcept {
    ++live_;
    peak_ = peak_ < live_ ? live_ : peak_;
    if (free_ == kNoIndex) {
      return fresh_++;
    }
    const std::uint32_t level = free_;
    free_ = levels_[level].*kLink;
    return level;
  }

  void give_back(std::uint32_t level) noexcept {
    --live_;
    levels_[level].*kLink = free_;
    free_ = level;
  }

  [[nodiscard]] std::uint64_t live() const noexcept { return live_; }
  [[nodiscard]] std::uint64_t peak() const noexcept { return peak_; }

  friend bool operator==(const LevelPool&, const LevelPool&) = default;

 private:
  std::vector<Level> levels_;
  std::uint32_t free_ = kNoIndex;
  std::uint32_t fresh_ = 0;
  std::uint64_t live_ = 0;
  std::uint64_t peak_ = 0;
};

// True when a level at `resting` is strictly better than `price` on this side.
[[nodiscard]] constexpr bool is_better(Side side, Price4 resting, Price4 price) noexcept {
  return side == Side::bid ? resting > price : resting < price;
}

struct LinkedLevel {
  std::uint64_t aggregate_shares;
  Price4 price;
  std::uint32_t order_count;
  std::uint32_t front;
  std::uint32_t back;
  std::uint32_t better;  // Towards the best price.
  std::uint32_t worse;   // Away from the best price. The free list, once given back.
  std::uint16_t locate;
  Side side;

  friend bool operator==(const LinkedLevel&, const LinkedLevel&) = default;
};

static_assert(sizeof(LinkedLevel) == 40);
static_assert(alignof(LinkedLevel) == 8);
static_assert(std::is_trivially_copyable_v<LinkedLevel>);

// Levels from a pool, linked best-first per (locate, side), with the best level's
// totals cached per side. A search walks the list from the best price.
class LinkedLevels {
 public:
  using Level = LinkedLevel;

  // Where a level at a price is, or goes: between `better` and `worse`.
  struct Place {
    std::uint32_t level;  // The level at this price, or kNoIndex.
    std::uint32_t better;
    std::uint32_t worse;
  };

  struct Top {
    std::uint32_t level;  // Head of the level list. kNoIndex on an empty side.
    Price4 price;
    std::uint64_t shares;
    std::uint32_t orders;

    [[nodiscard]] bool empty() const noexcept { return level == kNoIndex; }
    friend bool operator==(const Top&, const Top&) = default;
  };

  static_assert(sizeof(Top) == 24);
  static_assert(std::is_trivially_copyable_v<Top>);

  explicit LinkedLevels(const BookCapacity& capacity)
      : levels_(capacity.levels),
        tops_(kSideCount, Top{.level = kNoIndex, .price = 0, .shares = 0, .orders = 0}) {}

  [[nodiscard]] Level& operator[](std::uint32_t at) noexcept { return levels_[at]; }
  [[nodiscard]] const Level& operator[](std::uint32_t at) const noexcept { return levels_[at]; }

  [[nodiscard]] Place search(const LevelId& id) const noexcept {
    std::uint32_t better = kNoIndex;
    std::uint32_t worse = tops_[side_key(id.locate, id.side)].level;
    while (worse != kNoIndex && is_better(id.side, levels_[worse].price, id.price)) {
      better = worse;
      worse = levels_[worse].worse;
    }
    const bool exists = worse != kNoIndex && levels_[worse].price == id.price;
    return {.level = exists ? worse : kNoIndex, .better = better, .worse = worse};
  }

  void prefetch_level(std::uint32_t level) const noexcept {
    if (level < levels_.size()) {
      // measured: E8, see FastBook::prefetch_stage.
      __builtin_prefetch(&levels_[level]);
    }
  }
  void prefetch_side(std::uint16_t locate, Side side) const noexcept {
    // measured: E8, see FastBook::prefetch_stage.
    __builtin_prefetch(&tops_[side_key(locate, side)]);
  }
  // A search starts at the best level.
  void prefetch_search(std::uint16_t locate, Side side) const noexcept {
    prefetch_level(tops_[side_key(locate, side)].level);
  }

  [[nodiscard]] bool has_room(const LevelId& /*id*/) const noexcept { return levels_.has_free(); }
  [[nodiscard]] std::uint32_t take() noexcept { return levels_.take(); }
  void give_back(std::uint32_t level) noexcept { levels_.give_back(level); }

  template <class Moved>
  void open(const LevelId& id, std::uint32_t level, const Place& place,
            const Moved& moved) noexcept {
    levels_[level] = Level{.aggregate_shares = 0,
                           .price = id.price,
                           .order_count = 0,
                           .front = kNoIndex,
                           .back = kNoIndex,
                           .better = place.better,
                           .worse = place.worse,
                           .locate = id.locate,
                           .side = id.side};
    if (place.better == kNoIndex) {
      tops_[side_key(id.locate, id.side)].level = level;
    } else {
      moved(place.better);
      levels_[place.better].worse = level;
    }
    if (place.worse != kNoIndex) {
      moved(place.worse);
      levels_[place.worse].better = level;
    }
  }

  template <class Moved>
  void close(const LevelId& id, std::uint32_t at, const Moved& moved) noexcept {
    const Level& level = levels_[at];
    if (level.better == kNoIndex) {
      tops_[side_key(id.locate, id.side)].level = level.worse;
    } else {
      moved(level.better);
      levels_[level.better].worse = level.worse;
    }
    if (level.worse != kNoIndex) {
      moved(level.worse);
      levels_[level.worse].better = level.better;
    }
  }

  void refresh(const LevelId& id) noexcept {
    Top& top = tops_[side_key(id.locate, id.side)];
    if (top.level == kNoIndex) {
      return;
    }
    const Level& best = levels_[top.level];
    top.price = best.price;
    top.shares = best.aggregate_shares;
    top.orders = best.order_count;
  }

  [[nodiscard]] TopOfBook best(std::uint16_t locate, Side side) const noexcept {
    const Top& top = tops_[side_key(locate, side)];
    return {.present = top.level != kNoIndex,
            .price = top.price,
            .shares = top.shares,
            .orders = top.orders};
  }

  // Each level of a side, best price first, until `visit` returns false.
  template <class Visit>
  void walk(std::uint16_t locate, Side side, const Visit& visit) const {
    for (std::uint32_t at = tops_[side_key(locate, side)].level; at != kNoIndex;
         at = levels_[at].worse) {
      if (!visit(levels_[at])) {
        return;
      }
    }
  }

  [[nodiscard]] std::span<const Top> sides() const noexcept { return tops_; }
  [[nodiscard]] std::uint64_t live() const noexcept { return levels_.live(); }
  [[nodiscard]] std::uint64_t peak() const noexcept { return levels_.peak(); }

  friend bool operator==(const LinkedLevels&, const LinkedLevels&) = default;

 private:
  LevelPool<Level, &Level::worse> levels_;
  std::vector<Top> tops_;
};

struct FlatLevel {
  std::uint64_t aggregate_shares;
  Price4 price;
  std::uint32_t order_count;
  std::uint32_t front;  // The free list, once given back.
  std::uint32_t back;
  std::uint16_t locate;
  Side side;

  friend bool operator==(const FlatLevel&, const FlatLevel&) = default;
};

static_assert(sizeof(FlatLevel) == 32);
static_assert(alignof(FlatLevel) == 8);
static_assert(std::is_trivially_copyable_v<FlatLevel>);

// Levels from a pool, found through one sorted array per (locate, side), best price at
// the end. Each entry is a key and a level slot, in two parallel arrays, so that a
// search reads only keys. An ask's key is its price inverted, which makes the best price
// the largest key on either side. A search goes from the end, where most changes land.
//
// The entries live in level records' stead because an order points at its level: moving
// a record would mean finding every order on it. So a level record never moves, and only
// its entry does.
//
// Every side's array is a block of 2^k entries from one arena, sized up front. A full
// block moves to one twice its size, and the old one goes to a free list for its size.
// A side keeps its block when it empties, so a message that removes a level never
// leaves the next one short of room.
//
// Scan is how many entries a search reads one by one from the best end before it
// binary-searches the rest: all of them in FlatLevels, the arm as designed, and fewer in
// the arms that stand for carteret's window-width experiment (docs/NOTES.md, "carteret's layout
// experiments on our book"). This book has no window: a level of any price is an entry of
// the same array, so nothing overflows. What a far price costs here is a longer search, and
// Scan is how far the search walks before it stops walking. Every Scan finds the same place.
inline constexpr std::uint32_t kScanAll = 0xFFFFFFFFU;

template <std::uint32_t Scan>
class ScanFlatLevels {
 public:
  using Level = FlatLevel;
  static constexpr std::uint32_t kScan = Scan;

  // Where a level at a price is, or goes: its position in the side's array.
  struct Place {
    std::uint32_t level;  // The level at this price, or kNoIndex.
    std::uint32_t position;
  };

  struct Top {
    std::uint32_t base;  // First entry of the side's block.
    std::uint32_t count;
    std::uint32_t capacity;

    [[nodiscard]] bool empty() const noexcept { return count == 0; }
    friend bool operator==(const Top&, const Top&) = default;
  };

  static_assert(sizeof(Top) == 12);
  static_assert(std::is_trivially_copyable_v<Top>);

  static constexpr std::uint32_t kFirstBlock = 8;

  explicit ScanFlatLevels(const BookCapacity& capacity)
      : levels_(capacity.levels),
        sides_(kSideCount, Top{.base = 0, .count = 0, .capacity = 0}),
        keys_(capacity.level_slots, 0),
        slots_(capacity.level_slots, kNoIndex) {
    free_blocks_.fill(kNoIndex);
  }

  [[nodiscard]] Level& operator[](std::uint32_t at) noexcept { return levels_[at]; }
  [[nodiscard]] const Level& operator[](std::uint32_t at) const noexcept { return levels_[at]; }

  [[nodiscard]] Place search(const LevelId& id) const noexcept {
    const Top& side = sides_[side_key(id.locate, id.side)];
    const std::uint32_t key = key_of(id.side, id.price);
    std::uint32_t at = side.count;
    if constexpr (Scan == kScanAll) {
      while (at != 0 && keys_[side.base + at - 1] > key) {
        --at;
      }
    } else {
      at = scan_then_bisect(side, key);
    }
    if (at != 0 && keys_[side.base + at - 1] == key) {
      return {.level = slots_[side.base + at - 1], .position = at - 1};
    }
    return {.level = kNoIndex, .position = at};
  }

  void prefetch_level(std::uint32_t level) const noexcept {
    if (level < levels_.size()) {
      // measured: E8, see FastBook::prefetch_stage.
      __builtin_prefetch(&levels_[level]);
    }
  }
  void prefetch_side(std::uint16_t locate, Side side) const noexcept {
    // measured: E8, see FastBook::prefetch_stage.
    __builtin_prefetch(&sides_[side_key(locate, side)]);
  }
  // A search reads keys from the end of the side's block, where the best price is.
  void prefetch_search(std::uint16_t locate, Side side) const noexcept {
    const Top& top = sides_[side_key(locate, side)];
    if (top.count != 0) {
      // measured: E8, see FastBook::prefetch_stage.
      __builtin_prefetch(&keys_[top.base + top.count - 1]);
    }
  }

  [[nodiscard]] bool has_room(const LevelId& id) const noexcept {
    const Top& side = sides_[side_key(id.locate, id.side)];
    return levels_.has_free() &&
           (side.count < side.capacity || block_available(next_capacity(side.capacity)));
  }
  [[nodiscard]] std::uint32_t take() noexcept { return levels_.take(); }
  void give_back(std::uint32_t level) noexcept { levels_.give_back(level); }

  template <class Moved>
  void open(const LevelId& id, std::uint32_t level, const Place& place,
            const Moved& moved) noexcept {
    levels_[level] = Level{.aggregate_shares = 0,
                           .price = id.price,
                           .order_count = 0,
                           .front = kNoIndex,
                           .back = kNoIndex,
                           .locate = id.locate,
                           .side = id.side};
    Top& side = sides_[side_key(id.locate, id.side)];
    if (side.count == side.capacity) {
      grow(side, moved);
    }
    for (std::uint32_t at = side.base + side.count; at > side.base + place.position; --at) {
      moved(slots_[at - 1]);
      keys_[at] = keys_[at - 1];
      slots_[at] = slots_[at - 1];
    }
    keys_[side.base + place.position] = key_of(id.side, id.price);
    slots_[side.base + place.position] = level;
    ++side.count;
  }

  template <class Moved>
  void close(const LevelId& id, std::uint32_t /*level*/, const Moved& moved) noexcept {
    Top& side = sides_[side_key(id.locate, id.side)];
    const std::uint32_t end = side.base + side.count;
    for (std::uint32_t at = side.base + search(id).position + 1; at < end; ++at) {
      moved(slots_[at]);
      keys_[at - 1] = keys_[at];
      slots_[at - 1] = slots_[at];
    }
    --side.count;
  }

  static constexpr void refresh(const LevelId& /*id*/) noexcept {}

  [[nodiscard]] TopOfBook best(std::uint16_t locate, Side side) const noexcept {
    const Top& top = sides_[side_key(locate, side)];
    if (top.count == 0) {
      return {.present = false, .price = 0, .shares = 0, .orders = 0};
    }
    const Level& level = levels_[slots_[top.base + top.count - 1]];
    return {.present = true,
            .price = level.price,
            .shares = level.aggregate_shares,
            .orders = level.order_count};
  }

  template <class Visit>
  void walk(std::uint16_t locate, Side side, const Visit& visit) const {
    const Top& top = sides_[side_key(locate, side)];
    for (std::uint32_t at = top.base + top.count; at != top.base; --at) {
      if (!visit(levels_[slots_[at - 1]])) {
        return;
      }
    }
  }

  [[nodiscard]] std::span<const Top> sides() const noexcept { return sides_; }
  [[nodiscard]] std::uint64_t live() const noexcept { return levels_.live(); }
  [[nodiscard]] std::uint64_t peak() const noexcept { return levels_.peak(); }
  // Entries the arena has handed out, in blocks in use or free.
  [[nodiscard]] std::uint64_t slots_used() const noexcept { return fresh_slot_; }
  [[nodiscard]] std::uint64_t relocations() const noexcept { return relocations_; }

  friend bool operator==(const ScanFlatLevels&, const ScanFlatLevels&) = default;

 private:
  static constexpr std::size_t kClasses = 32;

  // How many of the side's keys are at or below `key`: the first Scan from the best end one
  // by one, and, if every one of those is above it, a binary search of the rest.
  [[nodiscard]] std::uint32_t scan_then_bisect(const Top& side, std::uint32_t key) const noexcept {
    const std::uint32_t floor = side.count > Scan ? side.count - Scan : 0;
    std::uint32_t at = side.count;
    while (at != floor && keys_[side.base + at - 1] > key) {
      --at;
    }
    if (at != floor || floor == 0) {
      return at;
    }
    std::uint32_t low = 0;
    std::uint32_t high = floor;
    while (low != high) {
      const std::uint32_t middle = low + ((high - low) / 2);
      if (keys_[side.base + middle] > key) {
        high = middle;
      } else {
        low = middle + 1;
      }
    }
    return low;
  }

  [[nodiscard]] static std::uint32_t key_of(Side side, Price4 price) noexcept {
    return side == Side::bid ? price : ~price;
  }

  [[nodiscard]] static std::uint64_t next_capacity(std::uint32_t capacity) noexcept {
    return capacity == 0 ? kFirstBlock : std::uint64_t{capacity} * 2U;
  }

  [[nodiscard]] static std::size_t class_of(std::uint64_t capacity) noexcept {
    return static_cast<std::size_t>(std::countr_zero(capacity));
  }

  [[nodiscard]] bool block_available(std::uint64_t capacity) const noexcept {
    if (class_of(capacity) >= kClasses) {
      return false;
    }
    return free_blocks_[class_of(capacity)] != kNoIndex || capacity <= keys_.size() - fresh_slot_;
  }

  // A free block's first key holds the next free block of its size.
  [[nodiscard]] std::uint32_t take_block(std::uint32_t capacity) noexcept {
    std::uint32_t& head = free_blocks_[class_of(capacity)];
    if (head == kNoIndex) {
      const std::uint32_t base = fresh_slot_;
      fresh_slot_ += capacity;
      return base;
    }
    const std::uint32_t base = head;
    head = keys_[base];
    return base;
  }

  void give_back_block(std::uint32_t base, std::uint32_t capacity) noexcept {
    std::uint32_t& head = free_blocks_[class_of(capacity)];
    keys_[base] = head;
    head = base;
  }

  template <class Moved>
  void grow(Top& side, const Moved& moved) noexcept {
    const auto capacity = static_cast<std::uint32_t>(next_capacity(side.capacity));
    const std::uint32_t base = take_block(capacity);
    for (std::uint32_t at = 0; at < side.count; ++at) {
      moved(slots_[side.base + at]);
      keys_[base + at] = keys_[side.base + at];
      slots_[base + at] = slots_[side.base + at];
    }
    if (side.capacity != 0) {
      give_back_block(side.base, side.capacity);
      ++relocations_;
    }
    side.base = base;
    side.capacity = capacity;
  }

  LevelPool<Level, &Level::front> levels_;
  std::vector<Top> sides_;
  std::vector<std::uint32_t> keys_;
  std::vector<std::uint32_t> slots_;
  std::array<std::uint32_t, kClasses> free_blocks_{};
  std::uint32_t fresh_slot_ = 0;
  std::uint64_t relocations_ = 0;
};

using FlatLevels = ScanFlatLevels<kScanAll>;
// One line of keys, 16 of them, one by one, then a binary search.
using Scan16FlatLevels = ScanFlatLevels<16>;
// A binary search of the whole side.
using BisectFlatLevels = ScanFlatLevels<0>;

}  // namespace itch
