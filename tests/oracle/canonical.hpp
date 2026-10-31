// The whole of a book as one canonical sequence of 64-bit words.
//
// Each book has its own walk here, reading its own structures. Neither walk sees the
// choke point's records, so the hash and the exhaustive compare cover what a write that
// bypassed the choke point would change. The fast book's words carry its stored totals
// and its cached top of book, and the reference's are summed from its FIFOs, so a total
// that has drifted from the orders under it changes the sequence.
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
#include <span>
#include <vector>

#include "itch/book.hpp"
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
    state_ = std::rotl(state_ ^ word, 27) * 5ULL + 0x52DCE729ULL;
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

template <class FastBookType, class Sink>
void emit_fast_side(const FastBookType& book, std::uint16_t locate, Side side, Sink& sink) {
  const std::uint32_t first = book.best_level(locate, side);
  if (first == kNoIndex) {
    return;
  }
  const TopOfBook top = book.best(locate, side);
  std::uint64_t level_count = 0;
  for (std::uint32_t at = first; at != kNoIndex && level_count < kWalkLimit;
       at = book.level_at(at).worse) {
    ++level_count;
  }
  sink(locate);
  sink(static_cast<std::uint64_t>(side));
  sink(top.price);
  sink(top.shares);
  sink(top.orders);
  sink(level_count);
  std::uint64_t remaining = level_count;
  for (std::uint32_t at = first; remaining != 0; at = book.level_at(at).worse, --remaining) {
    const BookLevel& level = book.level_at(at);
    sink(level.price);
    sink(level.order_count);
    sink(level.aggregate_shares);
    std::uint64_t steps = 0;
    for (std::uint32_t order = level.front; order != kNoIndex && steps < kWalkLimit;
         order = book.order_at(order).newer, ++steps) {
      sink(book.order_at(order).reference);
      sink(book.order_at(order).shares);
    }
  }
}

// Every locate of a fast book, or just the named ones.
template <class FastBookType, class Sink>
void emit_fast(const FastBookType& book, std::span<const std::uint16_t> locates, Sink& sink) {
  for (const std::uint16_t locate : locates) {
    emit_fast_side(book, locate, Side::bid, sink);
    emit_fast_side(book, locate, Side::ask, sink);
  }
}

template <class FastBookType, class Sink>
void emit_fast(const FastBookType& book, Sink& sink) {
  for (std::uint32_t locate = 0; locate < 65536U; ++locate) {
    emit_fast_side(book, static_cast<std::uint16_t>(locate), Side::bid, sink);
    emit_fast_side(book, static_cast<std::uint16_t>(locate), Side::ask, sink);
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
    for (auto level = levels.rbegin(); level != levels.rend(); ++level) {
      emit_reference_level(level->first, level->second, sink);
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

}  // namespace itch::oracle
