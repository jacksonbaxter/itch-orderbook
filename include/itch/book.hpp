// A minimal L3 book, keyed on stock locate. Phase 3 replaces its internals; what stays
// is the shape the oracle relies on.
//
// All storage is sized in the constructor, so apply() never allocates. Orders live in
// a pool and sit on an intrusive FIFO per level. A hash table, open addressing with
// linear probing, maps a reference to its order. Levels live in a second pool, linked
// best-first per (locate, side), and an add walks that list from the best price.
//
// Every write to what a level holds goes through write_level(), which reports the
// level's identity to WriteObserver before changing anything. That policy is part of
// the design, not a test hook: the default is an empty inline call that costs nothing,
// and the oracle build records the writes to prove what each message left alone.
//
// A message the book cannot apply leaves it unchanged and returns why. No wire content
// can make apply() fail any other way.
#pragma once

#include <bit>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string_view>
#include <vector>

#include "itch/messages.hpp"
#include "itch/wire.hpp"

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
struct NoWriteObserver {
  static constexpr void on_level_write(std::uint16_t /*locate*/, Side /*side*/,
                                       Price4 /*price*/) noexcept {}
};

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
};

struct BookLevel {
  std::uint64_t aggregate_shares;
  Price4 price;
  std::uint32_t order_count;
  std::uint32_t front;
  std::uint32_t back;
  std::uint32_t better;  // Towards the best price.
  std::uint32_t worse;   // Away from the best price.
  std::uint16_t locate;
  Side side;
};

static_assert(sizeof(BookOrder) == 24);
static_assert(sizeof(BookLevel) == 40);

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

template <class WriteObserver = NoWriteObserver>
class FastBook {
 public:
  explicit FastBook(BookCapacity capacity = {})
      : orders_(capacity.orders),
        levels_(capacity.levels),
        index_(std::bit_ceil(std::size_t{2} * capacity.orders), kNoIndex),
        index_mask_{index_.size() - 1},
        index_shift_{64U - static_cast<unsigned>(std::countr_zero(index_.size()))},
        tops_(std::size_t{2} * 65536U,
              Top{.level = kNoIndex, .price = 0, .shares = 0, .orders = 0}) {}

  // One whole message payload, as the frame reader hands it over.
  [[nodiscard]] ApplyStatus apply(std::span<const std::byte> payload) noexcept {
    if (payload.empty() || payload.size() != message_length(read_char(payload.data()))) {
      return ApplyStatus::no_book_effect;
    }
    const std::byte* m = payload.data();
    switch (read_char(m)) {
      case AddOrder::kType: {
        const AddOrder add = AddOrder::from_wire(m);
        return add_order(add.header.stock_locate, add.order_reference, add.side, add.shares,
                         add.price);
      }
      case AddOrderAttributed::kType: {
        const AddOrderAttributed add = AddOrderAttributed::from_wire(m);
        return add_order(add.header.stock_locate, add.order_reference, add.side, add.shares,
                         add.price);
      }
      case OrderExecuted::kType: {
        const OrderExecuted e = OrderExecuted::from_wire(m);
        return deduct(e.header.stock_locate, e.order_reference, e.executed_shares);
      }
      case OrderExecutedWithPrice::kType: {
        const OrderExecutedWithPrice c = OrderExecutedWithPrice::from_wire(m);
        return deduct(c.header.stock_locate, c.order_reference, c.executed_shares);
      }
      case OrderCancel::kType: {
        const OrderCancel x = OrderCancel::from_wire(m);
        return deduct(x.header.stock_locate, x.order_reference, x.cancelled_shares);
      }
      case OrderDelete::kType: {
        const OrderDelete d = OrderDelete::from_wire(m);
        return delete_order(d.header.stock_locate, d.order_reference);
      }
      case OrderReplace::kType: return replace_order(OrderReplace::from_wire(m));
      default: return ApplyStatus::no_book_effect;
    }
  }

  // The level at this price, or nullptr. Walks from the best price.
  [[nodiscard]] const BookLevel* find_level(std::uint16_t locate, Side side,
                                            Price4 price) const noexcept {
    std::uint32_t at = tops_[top_key(locate, side)].level;
    while (at != kNoIndex && is_better(side, levels_[at].price, price)) {
      at = levels_[at].worse;
    }
    return at != kNoIndex && levels_[at].price == price ? &levels_[at] : nullptr;
  }

  [[nodiscard]] TopOfBook best(std::uint16_t locate, Side side) const noexcept {
    const Top& top = tops_[top_key(locate, side)];
    return {.present = top.level != kNoIndex,
            .price = top.price,
            .shares = top.shares,
            .orders = top.orders};
  }

  // The best level on a side, for walking `worse` links through level_at().
  [[nodiscard]] std::uint32_t best_level(std::uint16_t locate, Side side) const noexcept {
    return tops_[top_key(locate, side)].level;
  }
  // Every side's record, 2 * locate + side, for a walk that has to see the whole table.
  [[nodiscard]] auto sides() const noexcept { return std::span{tops_}; }
  [[nodiscard]] const BookLevel& level_at(std::uint32_t index) const noexcept {
    return levels_[index];
  }
  [[nodiscard]] const BookOrder& order_at(std::uint32_t index) const noexcept {
    return orders_[index];
  }

  // The live order with this reference, wherever it rests, or nothing.
  [[nodiscard]] std::optional<RestingOrder> resting_order(std::uint64_t reference) const noexcept {
    const std::uint32_t at = find_order(reference);
    if (at == kNoIndex) {
      return std::nullopt;
    }
    const BookOrder& order = orders_[at];
    const BookLevel& level = levels_[order.level];
    return RestingOrder{.reference = reference,
                        .locate = level.locate,
                        .side = level.side,
                        .price = level.price,
                        .shares = order.shares,
                        .at_front = order.older == kNoIndex};
  }

  [[nodiscard]] std::uint64_t live_orders() const noexcept { return live_orders_; }
  [[nodiscard]] std::uint64_t live_levels() const noexcept { return live_levels_; }
  [[nodiscard]] std::uint64_t peak_live_orders() const noexcept { return peak_live_orders_; }
  [[nodiscard]] std::uint64_t peak_live_levels() const noexcept { return peak_live_levels_; }

  [[nodiscard]] WriteObserver& observer() noexcept { return observer_; }

 private:
  struct Top {
    std::uint32_t level;  // Head of the level list. kNoIndex on an empty side.
    Price4 price;
    std::uint64_t shares;
    std::uint32_t orders;
  };

  struct LevelId {
    std::uint16_t locate;
    Side side;
    Price4 price;
  };

  enum class LevelOp : std::uint8_t { open, append, reduce, unlink, close };

  struct LevelWrite {
    LevelOp op;
    std::uint32_t level = kNoIndex;
    std::uint32_t order = kNoIndex;
    std::uint32_t shares = 0;
    std::uint32_t better = kNoIndex;
    std::uint32_t worse = kNoIndex;
    std::uint64_t reference = 0;
  };

  // The write choke point. Nothing else changes a level's orders, shares or count, or
  // adds or removes a level. open and close also rewrite the neighbours' `better` and
  // `worse` links, which order the levels and are no part of what a level holds.
  void write_level(const LevelId& id, const LevelWrite& write) noexcept {
    observer_.on_level_write(id.locate, id.side, id.price);
    switch (write.op) {
      case LevelOp::open: open_level(id, write); break;
      case LevelOp::append: append_order(write); break;
      case LevelOp::reduce: reduce_order(write); break;
      case LevelOp::unlink: unlink_order(write); break;
      case LevelOp::close: close_level(id, write); break;
    }
    refresh_top(id);
  }

  void open_level(const LevelId& id, const LevelWrite& write) noexcept {
    levels_[write.level] = BookLevel{.aggregate_shares = 0,
                                     .price = id.price,
                                     .order_count = 0,
                                     .front = kNoIndex,
                                     .back = kNoIndex,
                                     .better = write.better,
                                     .worse = write.worse,
                                     .locate = id.locate,
                                     .side = id.side};
    if (write.better == kNoIndex) {
      tops_[top_key(id.locate, id.side)].level = write.level;
    } else {
      levels_[write.better].worse = write.level;
    }
    if (write.worse != kNoIndex) {
      levels_[write.worse].better = write.level;
    }
  }

  void append_order(const LevelWrite& write) noexcept {
    BookLevel& level = levels_[write.level];
    orders_[write.order] = BookOrder{.reference = write.reference,
                                     .shares = write.shares,
                                     .level = write.level,
                                     .older = level.back,
                                     .newer = kNoIndex};
    if (level.back == kNoIndex) {
      level.front = write.order;
    } else {
      orders_[level.back].newer = write.order;
    }
    level.back = write.order;
    level.aggregate_shares += write.shares;
    ++level.order_count;
  }

  void reduce_order(const LevelWrite& write) noexcept {
    orders_[write.order].shares -= write.shares;
    levels_[write.level].aggregate_shares -= write.shares;
  }

  void unlink_order(const LevelWrite& write) noexcept {
    const BookOrder& order = orders_[write.order];
    BookLevel& level = levels_[write.level];
    if (order.older == kNoIndex) {
      level.front = order.newer;
    } else {
      orders_[order.older].newer = order.newer;
    }
    if (order.newer == kNoIndex) {
      level.back = order.older;
    } else {
      orders_[order.newer].older = order.older;
    }
    level.aggregate_shares -= order.shares;
    --level.order_count;
  }

  void close_level(const LevelId& id, const LevelWrite& write) noexcept {
    const BookLevel& level = levels_[write.level];
    if (level.better == kNoIndex) {
      tops_[top_key(id.locate, id.side)].level = level.worse;
    } else {
      levels_[level.better].worse = level.worse;
    }
    if (level.worse != kNoIndex) {
      levels_[level.worse].better = level.better;
    }
  }

  void refresh_top(const LevelId& id) noexcept {
    Top& top = tops_[top_key(id.locate, id.side)];
    if (top.level == kNoIndex) {
      return;
    }
    const BookLevel& best = levels_[top.level];
    top.price = best.price;
    top.shares = best.aggregate_shares;
    top.orders = best.order_count;
  }

  [[nodiscard]] ApplyStatus add_order(std::uint16_t locate, std::uint64_t reference, char side,
                                      std::uint32_t shares, Price4 price) noexcept {
    if (side != 'B' && side != 'S') {
      return ApplyStatus::bad_side;
    }
    if (find_order(reference) != kNoIndex) {
      return ApplyStatus::duplicate_reference;
    }
    return rest_order(
        LevelId{.locate = locate, .side = side == 'B' ? Side::bid : Side::ask, .price = price},
        reference, shares);
  }

  [[nodiscard]] ApplyStatus rest_order(const LevelId& id, std::uint64_t reference,
                                       std::uint32_t shares) noexcept {
    std::uint32_t better = kNoIndex;
    std::uint32_t worse = tops_[top_key(id.locate, id.side)].level;
    while (worse != kNoIndex && is_better(id.side, levels_[worse].price, id.price)) {
      better = worse;
      worse = levels_[worse].worse;
    }
    const bool exists = worse != kNoIndex && levels_[worse].price == id.price;
    if (!pool_has_order() || (!exists && !pool_has_level())) {
      return ApplyStatus::capacity_exhausted;
    }
    std::uint32_t level = worse;
    if (!exists) {
      level = take_level();
      write_level(id, {.op = LevelOp::open, .level = level, .better = better, .worse = worse});
    }
    const std::uint32_t order = take_order();
    write_level(id, {.op = LevelOp::append,
                     .level = level,
                     .order = order,
                     .shares = shares,
                     .reference = reference});
    insert_index(reference, order);
    return ApplyStatus::applied;
  }

  [[nodiscard]] ApplyStatus deduct(std::uint16_t locate, std::uint64_t reference,
                                   std::uint32_t shares) noexcept {
    const std::uint32_t order = find_order(reference);
    if (order == kNoIndex) {
      return ApplyStatus::unknown_order;
    }
    const std::uint32_t level = orders_[order].level;
    if (levels_[level].locate != locate) {
      return ApplyStatus::locate_mismatch;
    }
    if (shares > orders_[order].shares) {
      return ApplyStatus::over_deduction;
    }
    if (shares == orders_[order].shares) {
      remove_order(order);
    } else {
      write_level(id_of(level),
                  {.op = LevelOp::reduce, .level = level, .order = order, .shares = shares});
    }
    return ApplyStatus::applied;
  }

  [[nodiscard]] ApplyStatus delete_order(std::uint16_t locate, std::uint64_t reference) noexcept {
    const std::uint32_t order = find_order(reference);
    if (order == kNoIndex) {
      return ApplyStatus::unknown_order;
    }
    if (levels_[orders_[order].level].locate != locate) {
      return ApplyStatus::locate_mismatch;
    }
    remove_order(order);
    return ApplyStatus::applied;
  }

  // The new order keeps the old one's locate and side, and goes to the back of the
  // FIFO at its price, even when that is the price it left.
  [[nodiscard]] ApplyStatus replace_order(const OrderReplace& u) noexcept {
    const std::uint32_t order = find_order(u.original_order_reference);
    if (order == kNoIndex) {
      return ApplyStatus::unknown_order;
    }
    const BookLevel& level = levels_[orders_[order].level];
    if (level.locate != u.header.stock_locate) {
      return ApplyStatus::locate_mismatch;
    }
    if (find_order(u.new_order_reference) != kNoIndex) {
      return ApplyStatus::duplicate_reference;
    }
    if (!pool_has_level()) {
      return ApplyStatus::capacity_exhausted;
    }
    const LevelId moved{.locate = level.locate, .side = level.side, .price = u.price};
    remove_order(order);
    return rest_order(moved, u.new_order_reference, u.shares);
  }

  void remove_order(std::uint32_t order) noexcept {
    const std::uint32_t level = orders_[order].level;
    const LevelId id = id_of(level);
    erase_index(orders_[order].reference);
    write_level(id, {.op = LevelOp::unlink, .level = level, .order = order});
    give_back_order(order);
    if (levels_[level].front == kNoIndex) {
      write_level(id, {.op = LevelOp::close, .level = level});
      give_back_level(level);
    }
  }

  [[nodiscard]] LevelId id_of(std::uint32_t level) const noexcept {
    return {.locate = levels_[level].locate,
            .side = levels_[level].side,
            .price = levels_[level].price};
  }

  [[nodiscard]] static std::size_t top_key(std::uint16_t locate, Side side) noexcept {
    return (std::size_t{locate} << 1U) | static_cast<std::size_t>(side);
  }

  // True when a level at `resting` is strictly better than `price` on this side.
  [[nodiscard]] static bool is_better(Side side, Price4 resting, Price4 price) noexcept {
    return side == Side::bid ? resting > price : resting < price;
  }

  [[nodiscard]] bool pool_has_order() const noexcept {
    return free_order_ != kNoIndex || fresh_order_ < orders_.size();
  }
  [[nodiscard]] bool pool_has_level() const noexcept {
    return free_level_ != kNoIndex || fresh_level_ < levels_.size();
  }

  [[nodiscard]] std::uint32_t take_order() noexcept {
    ++live_orders_;
    peak_live_orders_ = peak_live_orders_ < live_orders_ ? live_orders_ : peak_live_orders_;
    if (free_order_ == kNoIndex) {
      return fresh_order_++;
    }
    const std::uint32_t order = free_order_;
    free_order_ = orders_[order].newer;
    return order;
  }

  void give_back_order(std::uint32_t order) noexcept {
    --live_orders_;
    orders_[order].newer = free_order_;
    free_order_ = order;
  }

  [[nodiscard]] std::uint32_t take_level() noexcept {
    ++live_levels_;
    peak_live_levels_ = peak_live_levels_ < live_levels_ ? live_levels_ : peak_live_levels_;
    if (free_level_ == kNoIndex) {
      return fresh_level_++;
    }
    const std::uint32_t level = free_level_;
    free_level_ = levels_[level].worse;
    return level;
  }

  void give_back_level(std::uint32_t level) noexcept {
    --live_levels_;
    levels_[level].worse = free_level_;
    free_level_ = level;
  }

  [[nodiscard]] std::size_t home_slot(std::uint64_t reference) const noexcept {
    return static_cast<std::size_t>((reference * 0x9E3779B97F4A7C15ULL) >> index_shift_);
  }

  [[nodiscard]] std::uint32_t find_order(std::uint64_t reference) const noexcept {
    for (std::size_t slot = home_slot(reference);; slot = (slot + 1) & index_mask_) {
      const std::uint32_t order = index_[slot];
      if (order == kNoIndex || orders_[order].reference == reference) {
        return order;
      }
    }
  }

  void insert_index(std::uint64_t reference, std::uint32_t order) noexcept {
    std::size_t slot = home_slot(reference);
    while (index_[slot] != kNoIndex) {
      slot = (slot + 1) & index_mask_;
    }
    index_[slot] = order;
  }

  // Backward-shift deletion, so that a long day leaves no tombstones behind.
  void erase_index(std::uint64_t reference) noexcept {
    std::size_t hole = home_slot(reference);
    while (orders_[index_[hole]].reference != reference) {
      hole = (hole + 1) & index_mask_;
    }
    for (std::size_t next = (hole + 1) & index_mask_; index_[next] != kNoIndex;
         next = (next + 1) & index_mask_) {
      const std::size_t home = home_slot(orders_[index_[next]].reference);
      if (((next - home) & index_mask_) >= ((next - hole) & index_mask_)) {
        index_[hole] = index_[next];
        hole = next;
      }
    }
    index_[hole] = kNoIndex;
  }

  std::vector<BookOrder> orders_;
  std::vector<BookLevel> levels_;
  std::vector<std::uint32_t> index_;
  std::size_t index_mask_;
  unsigned index_shift_;
  std::vector<Top> tops_;
  std::uint32_t free_order_ = kNoIndex;
  std::uint32_t free_level_ = kNoIndex;
  std::uint32_t fresh_order_ = 0;
  std::uint32_t fresh_level_ = 0;
  std::uint64_t live_orders_ = 0;
  std::uint64_t live_levels_ = 0;
  std::uint64_t peak_live_orders_ = 0;
  std::uint64_t peak_live_levels_ = 0;
  [[no_unique_address]] WriteObserver observer_;
};

}  // namespace itch
