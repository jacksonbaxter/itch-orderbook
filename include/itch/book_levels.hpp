// The level arms: how each (locate, side) keeps its levels in price order.
//
// Each is a policy of FastBook. It owns the level records and the structure that orders
// them, and sizes both in the constructor. FastBook owns the orders and every FIFO, and
// it is the only caller of open() and close(), which it makes from its write choke
// point. Those two report through `moved` each other level whose place in the structure
// they rewrite, before rewriting it.
//
// Stub quotes are real and span almost the whole price field (DS-73), so no arm indexes
// a level by its price. Each searches by comparing prices, and a far price is just one
// more level at the far end.
#pragma once

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

}  // namespace itch
