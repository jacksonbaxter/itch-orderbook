// The node book: an L3 book whose every order and every level is a node taken from an
// allocator, so that T-ARENA-1 can change the allocator and nothing else.
//
//     NodeBook<Arm>
//
// Why a book of its own. FastBook<> keeps its orders and levels in arrays sized up front and
// links them by 32-bit index, so there is no allocator in it to change: a malloc arm would
// have to change its layout and its links as well. MapBook allocates on every add, but
// through std::map, std::list and std::unordered_map, whose node types, bucket arrays and
// links belong to the standard library, so a pool could not take their nodes by index and
// an arm would change how the index grows. itch-book's pooled-against-inline comparison
// changed the index along with the allocator (LC-45). Here the index, the side table, the
// search and every write are one code path for every arm, and the arm decides only where
// a node's storage comes from and what a link to it is.
//
// The design is the fast book's linked-levels arm, written over handles: a hash index
// from reference to order, a table of each (locate, side)'s best level, the levels of a
// side linked best-first and searched from the best, and each level's orders on an
// intrusive FIFO, oldest first. An add takes one order node, and one level node when its
// price opens a level. A full deduction, a delete or a replace gives the order back, and
// the level when it empties.
//
// The arm is a policy with a handle type and a heap:
//
//     struct Arm {
//       template <class T> using Handle = ...;   // T* or a 32-bit slot
//       template <class Order, class Level> class Heap;
//     };
//
// Heap(capacity) holds what the arm needs; make<T>() returns a node's storage, drop<T>()
// gives it back, at<T>() reads through a handle, and null<T>() is the handle of nothing.
// Order and Level hold only trivial members, and the book writes every field of a node it
// makes. Every arm has the same capacity: an add past capacity.orders live orders, or a
// level past capacity.levels, is refused as capacity_exhausted, so that every arm gives
// the same status for every message. A heap with ready() says whether it holds what it was
// sized for. One that does not, a pool whose pages were refused, leaves the book with no
// room at all, so that every add is refused rather than run on memory that is not there.
//
// The arms (node_arms.hpp, and mimalloc_arm.hpp for mimalloc) are system malloc, mimalloc,
// std::pmr's two resources, and node_pool.hpp's pool by index, by pointer and with a FIFO
// free list. A node's links are as wide as the arm's handle: the index arms' Order is 24
// bytes and the pointer arms' 40, and that is the comparison the pool's index and pointer
// arms are there to make (LC-56). Every pointer arm holds the same layout.
//
// The index keeps the reference beside the handle in each slot, 16 bytes whatever the
// handle, so a probe never reads an order and the index is the same size for every arm.
// It is sized from capacity.orders, at most half full, and never grows. The index and the
// side table come from std::vector in every arm, never from the arm.
//
// A message the book cannot apply leaves it unchanged and returns why. An arm that takes
// its nodes from a heap can throw std::bad_alloc from make(), which the pool arms never do.
#pragma once

#include <bit>
#include <cstddef>
#include <cstdint>
#include <span>
#include <type_traits>
#include <vector>

#include "itch/book_levels.hpp"
#include "itch/book_types.hpp"
#include "itch/messages.hpp"
#include "itch/page_map.hpp"
#include "itch/wire.hpp"

namespace itch {

// The pools are sized from the counted peak: 1,924,078 live orders and 784,060 live levels
// on 12302019, and 1,824,830 and 747,780 on 10302019 (results/oracle_*.md). Each is the
// larger day's with a quarter more, rounded up to 4,096, as PagedIndex's pages are.
struct NodeCapacity {
  std::uint32_t orders = 2'408'448;
  std::uint32_t levels = 983'040;
  PageRequest pages = PageRequest::huge_if_offered;  // Read by the pool arms only.
};

// Reference to order, linear probing, backward-shift deletion, keyed as HashIndex is.
template <class Handle>
class NodeIndex {
 public:
  struct Slot {
    std::uint64_t reference;
    Handle node;
  };
  static_assert(sizeof(Slot) == 16, "one slot size for every handle");

  NodeIndex(std::uint32_t orders, Handle none)
      : slots_(std::bit_ceil(std::size_t{2} * orders), Slot{.reference = 0, .node = none}),
        mask_{slots_.size() - 1},
        shift_{64U - static_cast<unsigned>(std::countr_zero(slots_.size()))},
        none_{none} {}

  [[nodiscard]] Handle find(std::uint64_t reference) const noexcept {
    for (std::size_t slot = home(reference);; slot = (slot + 1) & mask_) {
      if (slots_[slot].node == none_ || slots_[slot].reference == reference) {
        return slots_[slot].node;
      }
    }
  }

  // Only for a reference find() did not return.
  void insert(std::uint64_t reference, Handle node) noexcept {
    std::size_t slot = home(reference);
    while (slots_[slot].node != none_) {
      slot = (slot + 1) & mask_;
    }
    slots_[slot] = {.reference = reference, .node = node};
  }

  // Only for a live reference.
  void erase(std::uint64_t reference) noexcept {
    std::size_t hole = home(reference);
    while (slots_[hole].reference != reference || slots_[hole].node == none_) {
      hole = (hole + 1) & mask_;
    }
    for (std::size_t next = (hole + 1) & mask_; slots_[next].node != none_;
         next = (next + 1) & mask_) {
      const std::size_t start = home(slots_[next].reference);
      if (((next - start) & mask_) >= ((next - hole) & mask_)) {
        slots_[hole] = slots_[next];
        hole = next;
      }
    }
    slots_[hole].node = none_;
  }

  [[nodiscard]] std::size_t slots() const noexcept { return slots_.size(); }

 private:
  [[nodiscard]] std::size_t home(std::uint64_t reference) const noexcept {
    return static_cast<std::size_t>((reference * 0x9E3779B97F4A7C15ULL) >> shift_);
  }

  std::vector<Slot> slots_;
  std::size_t mask_;
  unsigned shift_;
  Handle none_;
};

template <class Arm>
class NodeBook {
 public:
  template <class T>
  using Handle = Arm::template Handle<T>;

  struct Level;
  struct Order {
    std::uint64_t reference;
    std::uint32_t shares;
    Handle<Level> level;
    Handle<Order> older;  // Towards the front of the FIFO.
    Handle<Order> newer;  // Towards the back of the FIFO.
  };
  struct Level {
    std::uint64_t aggregate_shares;
    Price4 price;
    std::uint32_t order_count;
    Handle<Order> front;
    Handle<Order> back;
    Handle<Level> better;  // Towards the best price.
    Handle<Level> worse;   // Away from the best price.
    std::uint16_t locate;
    Side side;
  };
  static_assert(std::is_trivially_copyable_v<Order> && std::is_trivially_copyable_v<Level>);
  static_assert(sizeof(Handle<Order>) == 4 ? sizeof(Order) == 24 && sizeof(Level) == 40
                                           : sizeof(Order) == 40 && sizeof(Level) == 56);

  using Heap = Arm::template Heap<Order, Level>;
  static constexpr Handle<Order> kNoOrder = Heap::template null<Order>();
  static constexpr Handle<Level> kNoLevel = Heap::template null<Level>();

  explicit NodeBook(const NodeCapacity& capacity = {})
      : capacity_{capacity},
        heap_{capacity},
        index_{capacity.orders, kNoOrder},
        best_(kSideCount, kNoLevel) {
    if constexpr (requires { heap_.ready(); }) {
      if (!heap_.ready()) {
        capacity_.orders = 0;
        capacity_.levels = 0;
      }
    }
  }

  NodeBook(const NodeBook&) = delete;
  NodeBook& operator=(const NodeBook&) = delete;
  NodeBook(NodeBook&&) = delete;
  NodeBook& operator=(NodeBook&&) = delete;

  // Gives back every live node, so that a heap arm's memory goes back to its heap.
  ~NodeBook() {
    for (const Handle<Level> head : best_) {
      for (Handle<Level> at_level = head; at_level != kNoLevel;) {
        const Handle<Level> next = level_at(at_level).worse;
        for (Handle<Order> at_order = level_at(at_level).front; at_order != kNoOrder;) {
          const Handle<Order> newer = order_at(at_order).newer;
          heap_.template drop<Order>(at_order);
          at_order = newer;
        }
        heap_.template drop<Level>(at_level);
        at_level = next;
      }
    }
  }

  // One whole message payload, as the frame reader hands it over.
  [[nodiscard]] ApplyStatus apply(std::span<const std::byte> payload) {
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

  [[nodiscard]] TopOfBook best(std::uint16_t locate, Side side) const noexcept {
    const Handle<Level> top = best_[side_key(locate, side)];
    if (top == kNoLevel) {
      return {.present = false, .price = 0, .shares = 0, .orders = 0};
    }
    const Level& best_level = level(top);
    return {.present = true,
            .price = best_level.price,
            .shares = best_level.aggregate_shares,
            .orders = best_level.order_count};
  }

  // Each level of a side, best price first, until `visit` returns false.
  template <class Visit>
  void walk_side(std::uint16_t locate, Side side, const Visit& visit) const {
    for (Handle<Level> at_level = best_[side_key(locate, side)]; at_level != kNoLevel;
         at_level = level(at_level).worse) {
      if (!visit(level(at_level))) {
        return;
      }
    }
  }

  // Each side's best level, 2 * locate + side, for a walk that has to see the whole book.
  [[nodiscard]] std::span<const Handle<Level>> sides() const noexcept { return best_; }
  [[nodiscard]] const Order& order_at(Handle<Order> node) const noexcept { return order(node); }
  [[nodiscard]] const Level& level_at(Handle<Level> node) const noexcept { return level(node); }

  // The level at this price, or nullptr.
  [[nodiscard]] const Level* find_level(std::uint16_t locate, Side side,
                                        Price4 price) const noexcept {
    const Place place = search({.locate = locate, .side = side, .price = price});
    return place.level == kNoLevel ? nullptr : &level(place.level);
  }

  [[nodiscard]] std::uint64_t live_orders() const noexcept { return live_orders_; }
  [[nodiscard]] std::uint64_t live_levels() const noexcept { return live_levels_; }
  [[nodiscard]] std::uint64_t peak_live_orders() const noexcept { return peak_orders_; }
  [[nodiscard]] std::uint64_t peak_live_levels() const noexcept { return peak_levels_; }
  // Nodes asked of the arm since construction, each one make() call.
  [[nodiscard]] std::uint64_t nodes_made() const noexcept { return made_; }
  [[nodiscard]] const NodeCapacity& capacity() const noexcept { return capacity_; }
  [[nodiscard]] const Heap& heap() const noexcept { return heap_; }
  [[nodiscard]] std::size_t index_slots() const noexcept { return index_.slots(); }

 private:
  struct Place {
    Handle<Level> level;  // The level at this price, or kNoLevel.
    Handle<Level> better;
    Handle<Level> worse;
  };

  // Handles of both types may be one integer type, so each type is read by its own name.
  [[nodiscard]] Order& order(Handle<Order> node) noexcept { return heap_.template at<Order>(node); }
  [[nodiscard]] const Order& order(Handle<Order> node) const noexcept {
    return heap_.template at<Order>(node);
  }
  [[nodiscard]] Level& level(Handle<Level> node) noexcept { return heap_.template at<Level>(node); }
  [[nodiscard]] const Level& level(Handle<Level> node) const noexcept {
    return heap_.template at<Level>(node);
  }

  [[nodiscard]] Place search(const LevelId& id) const noexcept {
    Handle<Level> better = kNoLevel;
    Handle<Level> worse = best_[side_key(id.locate, id.side)];
    while (worse != kNoLevel && is_better(id.side, level(worse).price, id.price)) {
      better = worse;
      worse = level(worse).worse;
    }
    const bool exists = worse != kNoLevel && level(worse).price == id.price;
    return {.level = exists ? worse : kNoLevel, .better = better, .worse = worse};
  }

  [[nodiscard]] bool has_order_room() const noexcept { return live_orders_ < capacity_.orders; }
  [[nodiscard]] bool has_level_room() const noexcept { return live_levels_ < capacity_.levels; }

  [[nodiscard]] ApplyStatus add_order(std::uint16_t locate, std::uint64_t reference, char side,
                                      std::uint32_t shares, Price4 price) {
    if (side != 'B' && side != 'S') {
      return ApplyStatus::bad_side;
    }
    if (index_.find(reference) != kNoOrder) {
      return ApplyStatus::duplicate_reference;
    }
    return rest_order(
        LevelId{.locate = locate, .side = side == 'B' ? Side::bid : Side::ask, .price = price},
        reference, shares);
  }

  [[nodiscard]] ApplyStatus rest_order(const LevelId& id, std::uint64_t reference,
                                       std::uint32_t shares) {
    const Place place = search(id);
    const bool exists = place.level != kNoLevel;
    if (!has_order_room() || (!exists && !has_level_room())) {
      return ApplyStatus::capacity_exhausted;
    }
    Handle<Level> at_level = place.level;
    if (!exists) {
      at_level = open_level(id, place);
    }
    const Handle<Order> made = heap_.template make<Order>();
    ++made_;
    ++live_orders_;
    peak_orders_ = peak_orders_ < live_orders_ ? live_orders_ : peak_orders_;
    Level& host = level(at_level);
    order(made) = Order{.reference = reference,
                        .shares = shares,
                        .level = at_level,
                        .older = host.back,
                        .newer = kNoOrder};
    if (host.back == kNoOrder) {
      host.front = made;
    } else {
      order(host.back).newer = made;
    }
    host.back = made;
    host.aggregate_shares += shares;
    ++host.order_count;
    index_.insert(reference, made);
    return ApplyStatus::applied;
  }

  [[nodiscard]] Handle<Level> open_level(const LevelId& id, const Place& place) {
    const Handle<Level> made = heap_.template make<Level>();
    ++made_;
    ++live_levels_;
    peak_levels_ = peak_levels_ < live_levels_ ? live_levels_ : peak_levels_;
    level(made) = Level{.aggregate_shares = 0,
                        .price = id.price,
                        .order_count = 0,
                        .front = kNoOrder,
                        .back = kNoOrder,
                        .better = place.better,
                        .worse = place.worse,
                        .locate = id.locate,
                        .side = id.side};
    if (place.better == kNoLevel) {
      best_[side_key(id.locate, id.side)] = made;
    } else {
      level(place.better).worse = made;
    }
    if (place.worse != kNoLevel) {
      level(place.worse).better = made;
    }
    return made;
  }

  void close_level(Handle<Level> gone) noexcept {
    const Level& closing = level(gone);
    if (closing.better == kNoLevel) {
      best_[side_key(closing.locate, closing.side)] = closing.worse;
    } else {
      level(closing.better).worse = closing.worse;
    }
    if (closing.worse != kNoLevel) {
      level(closing.worse).better = closing.better;
    }
    --live_levels_;
    heap_.template drop<Level>(gone);
  }

  [[nodiscard]] ApplyStatus deduct(std::uint16_t locate, std::uint64_t reference,
                                   std::uint32_t shares) noexcept {
    const Handle<Order> found = index_.find(reference);
    if (found == kNoOrder) {
      return ApplyStatus::unknown_order;
    }
    Order& resting = order(found);
    Level& host = level(resting.level);
    if (host.locate != locate) {
      return ApplyStatus::locate_mismatch;
    }
    if (shares > resting.shares) {
      return ApplyStatus::over_deduction;
    }
    if (shares == resting.shares) {
      remove_order(found);
    } else {
      resting.shares -= shares;
      host.aggregate_shares -= shares;
    }
    return ApplyStatus::applied;
  }

  [[nodiscard]] ApplyStatus delete_order(std::uint16_t locate, std::uint64_t reference) noexcept {
    const Handle<Order> found = index_.find(reference);
    if (found == kNoOrder) {
      return ApplyStatus::unknown_order;
    }
    if (level(order(found).level).locate != locate) {
      return ApplyStatus::locate_mismatch;
    }
    remove_order(found);
    return ApplyStatus::applied;
  }

  // The new order keeps the old one's locate and side, and goes to the back of the FIFO at
  // its price, even when that is the price it left. Room for a new level is checked before
  // the old order goes, as the fast book's linked arm does, so a refused replace changes
  // nothing.
  [[nodiscard]] ApplyStatus replace_order(const OrderReplace& u) {
    const Handle<Order> found = index_.find(u.original_order_reference);
    if (found == kNoOrder) {
      return ApplyStatus::unknown_order;
    }
    const Level& host = level(order(found).level);
    if (host.locate != u.header.stock_locate) {
      return ApplyStatus::locate_mismatch;
    }
    if (index_.find(u.new_order_reference) != kNoOrder) {
      return ApplyStatus::duplicate_reference;
    }
    const LevelId moved{.locate = host.locate, .side = host.side, .price = u.price};
    if (!has_level_room()) {
      return ApplyStatus::capacity_exhausted;
    }
    remove_order(found);
    return rest_order(moved, u.new_order_reference, u.shares);
  }

  void remove_order(Handle<Order> gone) noexcept {
    const Order& leaving = order(gone);
    const Handle<Level> at_level = leaving.level;
    Level& host = level(at_level);
    index_.erase(leaving.reference);
    if (leaving.older == kNoOrder) {
      host.front = leaving.newer;
    } else {
      order(leaving.older).newer = leaving.newer;
    }
    if (leaving.newer == kNoOrder) {
      host.back = leaving.older;
    } else {
      order(leaving.newer).older = leaving.older;
    }
    host.aggregate_shares -= leaving.shares;
    --host.order_count;
    --live_orders_;
    heap_.template drop<Order>(gone);
    if (host.front == kNoOrder) {
      close_level(at_level);
    }
  }

  NodeCapacity capacity_;
  Heap heap_;
  NodeIndex<Handle<Order>> index_;
  std::vector<Handle<Level>> best_;
  std::uint64_t live_orders_ = 0;
  std::uint64_t live_levels_ = 0;
  std::uint64_t peak_orders_ = 0;
  std::uint64_t peak_levels_ = 0;
  std::uint64_t made_ = 0;
};

}  // namespace itch
