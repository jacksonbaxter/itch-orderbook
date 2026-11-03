// The whole of a book as one canonical sequence of 64-bit words.
//
// Each book has its own walk here, reading its own structures. Neither walk sees the
// choke point's records, so the hash and the exhaustive compare cover what a write that
// bypassed the choke point would change. The fast book's words carry its stored totals
// and its cached top of book, and the reference's are summed from its FIFOs, so a total
// that has drifted from the orders under it changes the sequence. The map book's words,
// for the T-DESIGN-1 baseline, carry its stored totals and its own best().
//
//     for each non-empty side, by locate then bid before ask:
//       locate, side, best price, best shares, best orders, level count
//       for each level, best price first:
//         price, order count, aggregate shares
//         for each order, front of the FIFO first: reference, shares
#pragma once

#include <bit>
#include <cstddef>
#include <cstdint>
#include <ranges>
#include <span>
#include <vector>

#include "itch/book.hpp"
#include "itch/map_book.hpp"
#include "oracle/reference_book.hpp"

namespace itch::oracle {

// A fixed mix of each word, order-sensitive. Not a hash of per-level hashes, so one
// wrong word anywhere changes the result.
class WordHash {
 public:
  void operator()(std::uint64_t word) noexcept {
    word *= 0xFF51AFD7ED558CCDULL;
    word = std::rotl(word, 31);
    word *= 0xC4CEB9FE1A85EC53ULL;
    state_ = (std::rotl(state_ ^ word, 27) * 5ULL) + 0x52DCE729ULL;
    ++words_;
  }
  [[nodiscard]] std::uint64_t value() const noexcept { return state_ ^ words_; }
  [[nodiscard]] std::uint64_t words() const noexcept { return words_; }

 private:
  std::uint64_t state_ = 0x9E3779B97F4A7C15ULL;
  std::uint64_t words_ = 0;
};

// Collects the words, for the exhaustive compare.
class WordList {
 public:
  void operator()(std::uint64_t word) { words_.push_back(word); }
  [[nodiscard]] const std::vector<std::uint64_t>& words() const noexcept { return words_; }

 private:
  std::vector<std::uint64_t> words_;
};

// Stops a corrupted list from looping forever. A walk this long is itself wrong, and
// its words then differ from the reference's.
inline constexpr std::uint64_t kWalkLimit = 1ULL << 32U;

// The levels come from the book's own walk of its price order, whichever arm it is: the
// linked list from its head, or the flat array from its end.
template <class FastBookType, class Sink>
void emit_fast_side(const FastBookType& book, std::uint16_t locate, Side side, Sink& sink) {
  using Level = FastBookType::Level;
  std::uint64_t level_count = 0;
  book.walk_side(locate, side, [&level_count](const Level& /*level*/) {
    ++level_count;
    return level_count < kWalkLimit;
  });
  if (level_count == 0) {
    return;
  }
  const TopOfBook top = book.best(locate, side);
  sink(locate);
  sink(static_cast<std::uint64_t>(side));
  sink(top.price);
  sink(top.shares);
  sink(top.orders);
  sink(level_count);
  std::uint64_t remaining = level_count;
  book.walk_side(locate, side, [&book, &sink, &remaining](const Level& level) {
    sink(level.price);
    sink(level.order_count);
    sink(level.aggregate_shares);
    std::uint64_t steps = 0;
    for (std::uint32_t order = level.front; order != kNoIndex && steps < kWalkLimit;
         order = book.order_at(order).newer, ++steps) {
      sink(book.order_at(order).reference);
      sink(book.order_at(order).shares);
    }
    return --remaining != 0;
  });
}

// Every locate of a fast book, or just the named ones.
template <class FastBookType, class Sink>
void emit_fast(const FastBookType& book, std::span<const std::uint16_t> locates, Sink& sink) {
  for (const std::uint16_t locate : locates) {
    emit_fast_side(book, locate, Side::bid, sink);
    emit_fast_side(book, locate, Side::ask, sink);
  }
}

// The first side from `key` on that holds a level, or the end of the table. A whole-book
// walk has 131,072 sides to look at and a fuzz input has a handful in use, so each input
// spent 38% of its time here, in checks on a loop that only reads inside this span. The
// walk of each side found stays instrumented.
template <class Top>
[[nodiscard, clang::no_sanitize("address", "undefined", "coverage")]] std::size_t next_side(
    std::span<const Top> sides, std::size_t key) noexcept {
  const Top* const tops = sides.data();
  const std::size_t count = sides.size();
  while (key < count && tops[key].empty()) {
    ++key;
  }
  return key;
}

template <class FastBookType, class Sink>
void emit_fast(const FastBookType& book, Sink& sink) {
  const auto sides = book.sides();
  for (std::size_t key = next_side(sides, 0); key < sides.size(); key = next_side(sides, key + 1)) {
    emit_fast_side(book, static_cast<std::uint16_t>(key >> 1U),
                   (key & 1U) == 0 ? Side::bid : Side::ask, sink);
  }
}

template <class Sink>
void emit_reference_level(std::uint32_t price, const ReferenceBook::Fifo& fifo, Sink& sink) {
  std::uint64_t shares = 0;
  std::uint64_t orders = 0;
  for (const ReferenceBook::Resting& order : fifo) {
    shares += order.shares;
    ++orders;
  }
  sink(price);
  sink(orders);
  sink(shares);
  for (const ReferenceBook::Resting& order : fifo) {
    sink(order.reference);
    sink(order.shares);
  }
}

template <class Sink>
void emit_reference_side(const ReferenceBook& book, const ReferenceBook::SideKey& key,
                         const ReferenceBook::PriceLevels& levels, Sink& sink) {
  const auto [locate, side] = key;
  const RefTop top = book.best(locate, side);
  sink(locate);
  sink(side == 'B' ? 0U : 1U);
  sink(top.price);
  sink(top.shares);
  sink(top.orders);
  sink(levels.size());
  if (side == 'B') {
    for (const auto& [price, fifo] : std::views::reverse(levels)) {
      emit_reference_level(price, fifo, sink);
    }
  } else {
    for (const auto& [price, fifo] : levels) {
      emit_reference_level(price, fifo, sink);
    }
  }
}

template <class Sink>
void emit_reference(const ReferenceBook& book, std::span<const std::uint16_t> locates, Sink& sink) {
  for (const std::uint16_t locate : locates) {
    for (const char side : {'B', 'S'}) {
      const auto found = book.sides().find({locate, side});
      if (found != book.sides().end()) {
        emit_reference_side(book, found->first, found->second, sink);
      }
    }
  }
}

template <class Sink>
void emit_reference(const ReferenceBook& book, Sink& sink) {
  for (const auto& [key, levels] : book.sides()) {
    emit_reference_side(book, key, levels, sink);
  }
}

template <class Sink>
void emit_map_side(const MapBook& book, std::uint16_t locate, Side side, Sink& sink) {
  const MapBook::SideLevels& levels = book.sides()[side_key(locate, side)];
  if (levels.empty()) {
    return;
  }
  const TopOfBook top = book.best(locate, side);
  sink(locate);
  sink(static_cast<std::uint64_t>(side));
  sink(top.price);
  sink(top.shares);
  sink(top.orders);
  sink(levels.size());
  book.walk_side(locate, side, [&sink](Price4 price, const MapBook::Level& level) {
    sink(price);
    sink(level.fifo.size());
    sink(level.aggregate_shares);
    for (const MapBook::Resting& order : level.fifo) {
      sink(order.reference);
      sink(order.shares);
    }
    return true;
  });
}

template <class Sink>
void emit_map(const MapBook& book, Sink& sink) {
  const std::size_t count = book.sides().size();
  for (std::size_t key = 0; key < count; ++key) {
    if (!book.sides()[key].empty()) {
      emit_map_side(book, static_cast<std::uint16_t>(key >> 1U),
                    (key & 1U) == 0 ? Side::bid : Side::ask, sink);
    }
  }
}

}  // namespace itch::oracle
