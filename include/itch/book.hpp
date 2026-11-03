// The L3 book, keyed on stock locate, with its design choices as policies so that each
// arm can be measured against the others in one binary.
//
//     FastBook<WriteObserver, OrderIndex, Levels>
//
// OrderIndex finds an order's slot from its reference: HashIndex or PagedIndex, in
// book_index.hpp. Levels keeps each side's levels in price order: LinkedLevels or
// FlatLevels, in book_levels.hpp. The default, the paged index with flat levels, is the
// fastest arm on both hosts measured, on 12302019 with the day in memory and mapped with
// the top tracked. It holds about three times the memory of the hash arms, nearly all of it
// the page pool sized up front, and stays under T-BOOK-3. NOTES.md, "The default arm".
//
// All storage is sized in the constructor, so apply() never allocates. Orders live in
// a pool and sit on an intrusive FIFO per level, with the oldest order at the front, in
// every arm.
//
// Every write to what a level holds goes through write_level(), which reports the
// level's identity to WriteObserver before changing anything. So does every write to the
// structure that finds a level by price, which the level arm makes from inside
// write_level() and reports as a move. That policy is part of the design, not a test
// hook: the default is an empty inline call that costs nothing, and the oracle build
// records the writes to prove what each message left alone.
//
// A message the book cannot apply leaves it unchanged and returns why. No wire content
// can make apply() fail any other way.
#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <vector>

#include "itch/book_index.hpp"
#include "itch/book_levels.hpp"
#include "itch/book_types.hpp"
#include "itch/messages.hpp"
#include "itch/wire.hpp"

namespace itch {

template <class WriteObserver = NoWriteObserver, class OrderIndex = PagedIndex<>,
          class Levels = FlatLevels>
class FastBook {
 public:
  using Level = Levels::Level;
  using Index = OrderIndex;
  using LevelArm = Levels;

  explicit FastBook(BookCapacity capacity = {})
      : orders_(capacity.orders), index_(capacity), levels_(capacity) {}

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

  // The level at this price, or nullptr.
  [[nodiscard]] const Level* find_level(std::uint16_t locate, Side side,
                                        Price4 price) const noexcept {
    const auto place = levels_.search({.locate = locate, .side = side, .price = price});
    return place.level == kNoIndex ? nullptr : &levels_[place.level];
  }

  [[nodiscard]] TopOfBook best(std::uint16_t locate, Side side) const noexcept {
    return levels_.best(locate, side);
  }

  // Each level of a side, best price first, until `visit` returns false.
  template <class Visit>
  void walk_side(std::uint16_t locate, Side side, const Visit& visit) const {
    levels_.walk(locate, side, visit);
  }
  // Every side's record, 2 * locate + side, for a walk that has to see the whole table.
  [[nodiscard]] auto sides() const noexcept { return levels_.sides(); }
  [[nodiscard]] const Level& level_at(std::uint32_t index) const noexcept { return levels_[index]; }
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
    const Level& level = levels_[order.level];
    return RestingOrder{.reference = reference,
                        .locate = level.locate,
                        .side = level.side,
                        .price = level.price,
                        .shares = order.shares,
                        .at_front = order.older == kNoIndex};
  }

  [[nodiscard]] std::uint64_t live_orders() const noexcept { return live_orders_; }
  [[nodiscard]] std::uint64_t live_levels() const noexcept { return levels_.live(); }
  [[nodiscard]] std::uint64_t peak_live_orders() const noexcept { return peak_live_orders_; }
  [[nodiscard]] std::uint64_t peak_live_levels() const noexcept { return levels_.peak(); }

  [[nodiscard]] const OrderIndex& index() const noexcept { return index_; }
  [[nodiscard]] const Levels& levels() const noexcept { return levels_; }

  [[nodiscard]] WriteObserver& observer() noexcept { return observer_; }

  // Every byte the book holds, in use or not, for a test that a copy restored it.
  friend bool operator==(const FastBook&, const FastBook&) = default;

 private:
  using Place = Levels::Place;

  enum class LevelOp : std::uint8_t { open, append, reduce, unlink, close };

  struct LevelWrite {
    LevelOp op;
    std::uint32_t level = kNoIndex;
    std::uint32_t order = kNoIndex;
    std::uint32_t shares = 0;
    Place place{};
    std::uint64_t reference = 0;
  };

  // The write choke point. Nothing else changes a level's orders, shares or count, adds
  // or removes a level, or changes where a level sits in its side's price order. open and
  // close rewrite other levels' places, and the level arm reports each of those as a
  // move before it makes it.
  void write_level(const LevelId& id, const LevelWrite& write) noexcept {
    observer_.on_level_write(id.locate, id.side, id.price);
    const auto moved = [this](std::uint32_t level) {
      const Level& other = levels_[level];
      observer_.on_level_move(other.locate, other.side, other.price);
    };
    switch (write.op) {
      case LevelOp::open: levels_.open(id, write.level, write.place, moved); break;
      case LevelOp::append: append_order(write); break;
      case LevelOp::reduce: reduce_order(write); break;
      case LevelOp::unlink: unlink_order(write); break;
      case LevelOp::close: levels_.close(id, write.level, moved); break;
    }
    levels_.refresh(id);
  }

  void append_order(const LevelWrite& write) noexcept {
    Level& level = levels_[write.level];
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
    Level& level = levels_[write.level];
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
    const Place place = levels_.search(id);
    const bool exists = place.level != kNoIndex;
    if (!pool_has_order() || (!exists && !levels_.has_room(id))) {
      return ApplyStatus::capacity_exhausted;
    }
    std::uint32_t level = place.level;
    if (!exists) {
      level = levels_.take();
      write_level(id, {.op = LevelOp::open, .level = level, .place = place});
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
  // FIFO at its price, even when that is the price it left. Room for a new level is
  // checked before the old order goes, so a refused replace changes nothing.
  [[nodiscard]] ApplyStatus replace_order(const OrderReplace& u) noexcept {
    const std::uint32_t order = find_order(u.original_order_reference);
    if (order == kNoIndex) {
      return ApplyStatus::unknown_order;
    }
    const Level& level = levels_[orders_[order].level];
    if (level.locate != u.header.stock_locate) {
      return ApplyStatus::locate_mismatch;
    }
    if (find_order(u.new_order_reference) != kNoIndex) {
      return ApplyStatus::duplicate_reference;
    }
    const LevelId moved{.locate = level.locate, .side = level.side, .price = u.price};
    if (!levels_.has_room(moved)) {
      return ApplyStatus::capacity_exhausted;
    }
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
      levels_.give_back(level);
    }
  }

  [[nodiscard]] LevelId id_of(std::uint32_t level) const noexcept {
    return {.locate = levels_[level].locate,
            .side = levels_[level].side,
            .price = levels_[level].price};
  }

  [[nodiscard]] bool pool_has_order() const noexcept {
    return free_order_ != kNoIndex || fresh_order_ < orders_.size();
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

  [[nodiscard]] std::uint32_t find_order(std::uint64_t reference) const noexcept {
    return index_.find(reference, orders_);
  }
  void insert_index(std::uint64_t reference, std::uint32_t order) noexcept {
    index_.insert(reference, order, orders_);
  }
  void erase_index(std::uint64_t reference) noexcept { index_.erase(reference, orders_); }

  std::vector<BookOrder> orders_;
  OrderIndex index_;
  Levels levels_;
  std::uint32_t free_order_ = kNoIndex;
  std::uint32_t fresh_order_ = 0;
  std::uint64_t live_orders_ = 0;
  std::uint64_t peak_live_orders_ = 0;
  [[no_unique_address]] WriteObserver observer_;
};

}  // namespace itch
