// The map book: the naive design that T-DESIGN-1 measures the fast book against.
//
// Per (locate, side), a std::map from price to level. Each level keeps its orders in a
// std::list, oldest first, with its total shares beside them. A std::unordered_map finds
// an order's level and list node from its reference. Every add takes nodes from the
// heap and every removal gives them back, so this book allocates on the hot path by
// design: that is the cost the comparison is there to show. heap_ager.hpp ages the heap
// it allocates from, for the run the Optiver result says a fair comparison needs.
//
// It takes the same messages and gives the same statuses as FastBook, so the two can be
// replayed side by side and compared. It never runs out of room: an allocation that
// fails throws std::bad_alloc, and no status says capacity_exhausted.
#pragma once

#include <cstddef>
#include <cstdint>
#include <iterator>
#include <list>
#include <map>
#include <span>
#include <unordered_map>
#include <vector>

#include "itch/book_types.hpp"
#include "itch/messages.hpp"
#include "itch/wire.hpp"

namespace itch {

class MapBook {
 public:
  struct Resting {
    std::uint64_t reference;
    std::uint32_t shares;
  };
  using Fifo = std::list<Resting>;
  struct Level {
    Fifo fifo;
    std::uint64_t aggregate_shares = 0;
  };
  // Ascending price on both sides, so the best bid is the last entry and the best ask
  // the first.
  using SideLevels = std::map<Price4, Level>;

  MapBook() : sides_(kSideCount) {}

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
    const SideLevels& levels = sides_[side_key(locate, side)];
    if (levels.empty()) {
      return {.present = false, .price = 0, .shares = 0, .orders = 0};
    }
    const auto& [price, level] = side == Side::bid ? *levels.rbegin() : *levels.begin();
    return {.present = true,
            .price = price,
            .shares = level.aggregate_shares,
            .orders = static_cast<std::uint32_t>(level.fifo.size())};
  }

  // Each level of a side, best price first, until `visit(price, level)` returns false.
  template <class Visit>
  void walk_side(std::uint16_t locate, Side side, const Visit& visit) const {
    const SideLevels& levels = sides_[side_key(locate, side)];
    if (side == Side::bid) {
      for (auto at = levels.rbegin(); at != levels.rend() && visit(at->first, at->second); ++at) {
      }
    } else {
      for (auto at = levels.begin(); at != levels.end() && visit(at->first, at->second); ++at) {
      }
    }
  }

  // Every side, 2 * locate + side, for a walk that has to see the whole book.
  [[nodiscard]] std::span<const SideLevels> sides() const noexcept { return sides_; }

  [[nodiscard]] std::uint64_t live_orders() const noexcept { return orders_.size(); }
  [[nodiscard]] std::uint64_t live_levels() const noexcept { return live_levels_; }

 private:
  struct Order {
    std::uint16_t locate;
    Side side;
    SideLevels::iterator level;
    Fifo::iterator at;
  };

  [[nodiscard]] ApplyStatus add_order(std::uint16_t locate, std::uint64_t reference, char side,
                                      std::uint32_t shares, Price4 price) {
    if (side != 'B' && side != 'S') {
      return ApplyStatus::bad_side;
    }
    if (orders_.contains(reference)) {
      return ApplyStatus::duplicate_reference;
    }
    rest_order(locate, side == 'B' ? Side::bid : Side::ask, price, reference, shares);
    return ApplyStatus::applied;
  }

  void rest_order(std::uint16_t locate, Side side, Price4 price, std::uint64_t reference,
                  std::uint32_t shares) {
    SideLevels& levels = sides_[side_key(locate, side)];
    const auto [level, opened] = levels.try_emplace(price);
    live_levels_ += opened ? 1U : 0U;
    level->second.fifo.push_back({.reference = reference, .shares = shares});
    level->second.aggregate_shares += shares;
    orders_.emplace(reference, Order{.locate = locate,
                                     .side = side,
                                     .level = level,
                                     .at = std::prev(level->second.fifo.end())});
  }

  [[nodiscard]] ApplyStatus deduct(std::uint16_t locate, std::uint64_t reference,
                                   std::uint32_t shares) {
    const auto found = orders_.find(reference);
    if (found == orders_.end()) {
      return ApplyStatus::unknown_order;
    }
    const Order& order = found->second;
    if (order.locate != locate) {
      return ApplyStatus::locate_mismatch;
    }
    if (shares > order.at->shares) {
      return ApplyStatus::over_deduction;
    }
    if (shares == order.at->shares) {
      remove_order(found);
    } else {
      order.at->shares -= shares;
      order.level->second.aggregate_shares -= shares;
    }
    return ApplyStatus::applied;
  }

  [[nodiscard]] ApplyStatus delete_order(std::uint16_t locate, std::uint64_t reference) {
    const auto found = orders_.find(reference);
    if (found == orders_.end()) {
      return ApplyStatus::unknown_order;
    }
    if (found->second.locate != locate) {
      return ApplyStatus::locate_mismatch;
    }
    remove_order(found);
    return ApplyStatus::applied;
  }

  // The new order keeps the old one's locate and side, and goes to the back of the FIFO
  // at its price, even when that is the price it left.
  [[nodiscard]] ApplyStatus replace_order(const OrderReplace& u) {
    const auto found = orders_.find(u.original_order_reference);
    if (found == orders_.end()) {
      return ApplyStatus::unknown_order;
    }
    const Order order = found->second;
    if (order.locate != u.header.stock_locate) {
      return ApplyStatus::locate_mismatch;
    }
    if (orders_.contains(u.new_order_reference)) {
      return ApplyStatus::duplicate_reference;
    }
    remove_order(found);
    rest_order(order.locate, order.side, u.price, u.new_order_reference, u.shares);
    return ApplyStatus::applied;
  }

  void remove_order(std::unordered_map<std::uint64_t, Order>::iterator found) {
    const Order& order = found->second;
    Level& level = order.level->second;
    level.aggregate_shares -= order.at->shares;
    level.fifo.erase(order.at);
    if (level.fifo.empty()) {
      sides_[side_key(order.locate, order.side)].erase(order.level);
      --live_levels_;
    }
    orders_.erase(found);
  }

  std::vector<SideLevels> sides_;
  std::unordered_map<std::uint64_t, Order> orders_;
  std::uint64_t live_levels_ = 0;
};

}  // namespace itch
