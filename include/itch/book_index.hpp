// The order lookup arms: reference to the order's slot in the pool.
//
// Each is a policy of FastBook, sized in the constructor and never grown. A lookup takes
// the order pool, because the hashes keep only slots and confirm a key by reading the
// order. The book calls insert only for a reference that find did not return, and erase
// only for one that is live.
//
// HashIndex and PagedIndex are the two arms the book was designed with. The other two
// repeat carteret's layout experiments on this book, each changing one thing against
// HashIndex: IdentityHashIndex its hash, the reference's own low bits for the multiply-shift
// (carteret's record 016), and ChainedIndex its collisions, a chain from a head per bucket
// for linear probing. docs/NOTES.md, "carteret's layout experiments on our book".
//
// Each arm also names the dependent loads a lookup makes before it has the order's slot,
// kPrefetchStages of them, so that a reader running ahead of the book can ask for each in
// turn (itch/lookahead.hpp): prefetch_stage(reference, s) starts load s, reading only what
// stages before it should already have brought in, and peek() then gives the slot those
// loads lead to without reading the order pool. Both only read, and neither may be
// trusted: by the time the message is applied the slot may hold another order.
#pragma once

#include <algorithm>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

#include "itch/book_types.hpp"

namespace itch {

// The hash policies: a reference to its home among 2^k slots, given 64 - k and 2^k - 1.
//
// MultiplyShift keeps the top k bits of the reference times 2^64 over the golden ratio,
// which spreads any run of references over the whole table. Identity keeps the reference's
// own low k bits, so references given out one after another land in slots one after
// another, and an order added just after another shares its line.
struct MultiplyShift {
  [[nodiscard]] static constexpr std::size_t home(std::uint64_t reference, unsigned shift,
                                                  std::size_t /*mask*/) noexcept {
    return static_cast<std::size_t>((reference * 0x9E3779B97F4A7C15ULL) >> shift);
  }
};

struct Identity {
  [[nodiscard]] static constexpr std::size_t home(std::uint64_t reference, unsigned /*shift*/,
                                                  std::size_t mask) noexcept {
    return static_cast<std::size_t>(reference) & mask;
  }
};

// Open addressing with linear probing, at most half full, keyed by Hash of the reference.
// Any 64-bit reference is a key. orders must be at least 1: for 0 the table has one slot, the
// shift is 64, which is undefined, and find() can read past the table. itch_alloc,
// itch_trades and itch_map_compare refuse 0 (tools/capacity.hpp).
template <class Hash>
class OpenHashIndex {
 public:
  explicit OpenHashIndex(const BookCapacity& capacity) : OpenHashIndex(capacity.orders) {}

  explicit OpenHashIndex(std::uint32_t orders)
      : slots_(std::bit_ceil(std::size_t{2} * orders), kNoIndex),
        mask_{slots_.size() - 1},
        shift_{64U - static_cast<unsigned>(std::countr_zero(slots_.size()))} {}

  // One load before the slot: the home slot's line.
  static constexpr unsigned kPrefetchStages = 1;

  void prefetch_stage(std::uint64_t reference, unsigned /*stage*/) const noexcept {
    // measured: E8, see FastBook::prefetch_stage.
    __builtin_prefetch(&slots_[home_slot(reference)]);
  }

  // The order in the home slot, which is the reference's own unless it was displaced.
  [[nodiscard]] std::uint32_t peek(std::uint64_t reference) const noexcept {
    return slots_[home_slot(reference)];
  }

  [[nodiscard]] std::uint32_t find(std::uint64_t reference,
                                   std::span<const BookOrder> orders) const noexcept {
    for (std::size_t slot = home_slot(reference);; slot = (slot + 1) & mask_) {
      const std::uint32_t order = slots_[slot];
      if (order == kNoIndex || orders[order].reference == reference) {
        return order;
      }
    }
  }

  void insert(std::uint64_t reference, std::uint32_t order,
              std::span<const BookOrder> /*orders*/) noexcept {
    std::size_t slot = home_slot(reference);
    while (slots_[slot] != kNoIndex) {
      slot = (slot + 1) & mask_;
    }
    slots_[slot] = order;
  }

  // Backward-shift deletion, so that a long day leaves no tombstones behind.
  void erase(std::uint64_t reference, std::span<const BookOrder> orders) noexcept {
    std::size_t hole = home_slot(reference);
    while (orders[slots_[hole]].reference != reference) {
      hole = (hole + 1) & mask_;
    }
    for (std::size_t next = (hole + 1) & mask_; slots_[next] != kNoIndex;
         next = (next + 1) & mask_) {
      const std::size_t home = home_slot(orders[slots_[next]].reference);
      if (((next - home) & mask_) >= ((next - hole) & mask_)) {
        slots_[hole] = slots_[next];
        hole = next;
      }
    }
    slots_[hole] = kNoIndex;
  }

  friend bool operator==(const OpenHashIndex&, const OpenHashIndex&) = default;

 private:
  [[nodiscard]] std::size_t home_slot(std::uint64_t reference) const noexcept {
    return Hash::home(reference, shift_, mask_);
  }

  std::vector<std::uint32_t> slots_;
  std::size_t mask_;
  unsigned shift_;
};

using HashIndex = OpenHashIndex<MultiplyShift>;
using IdentityHashIndex = OpenHashIndex<Identity>;

// Separate chaining with the multiply-shift hash, as many buckets as HashIndex has slots,
// so that the experiment changes how collisions are kept and nothing else. A bucket holds
// the slot of the first order on its chain, and each order's link to the next sits in an
// array beside the pool, indexed by the order's slot, so that BookOrder stays 24 bytes. A
// lookup reads its bucket, then each order on the chain to compare its reference, and that
// order's link to go on. A new order goes at the front of its chain. orders must be at least
// 1, as for OpenHashIndex.
class ChainedIndex {
 public:
  explicit ChainedIndex(const BookCapacity& capacity) : ChainedIndex(capacity.orders) {}

  explicit ChainedIndex(std::uint32_t orders)
      : heads_(std::bit_ceil(std::size_t{2} * orders), kNoIndex),
        next_(orders, kNoIndex),
        mask_{heads_.size() - 1},
        shift_{64U - static_cast<unsigned>(std::countr_zero(heads_.size()))} {}

  // One load before the slot: the bucket's line.
  static constexpr unsigned kPrefetchStages = 1;

  void prefetch_stage(std::uint64_t reference, unsigned /*stage*/) const noexcept {
    // measured: E8, see FastBook::prefetch_stage.
    __builtin_prefetch(&heads_[bucket(reference)]);
  }

  // The first order on the reference's chain, which is the reference's own unless another
  // was added there after it.
  [[nodiscard]] std::uint32_t peek(std::uint64_t reference) const noexcept {
    return heads_[bucket(reference)];
  }

  [[nodiscard]] std::uint32_t find(std::uint64_t reference,
                                   std::span<const BookOrder> orders) const noexcept {
    std::uint32_t order = heads_[bucket(reference)];
    while (order != kNoIndex && orders[order].reference != reference) {
      order = next_[order];
    }
    return order;
  }

  void insert(std::uint64_t reference, std::uint32_t order,
              std::span<const BookOrder> /*orders*/) noexcept {
    std::uint32_t& head = heads_[bucket(reference)];
    next_[order] = head;
    head = order;
  }

  void erase(std::uint64_t reference, std::span<const BookOrder> orders) noexcept {
    std::uint32_t* link = &heads_[bucket(reference)];
    while (orders[*link].reference != reference) {
      link = &next_[*link];
    }
    *link = next_[*link];
  }

  friend bool operator==(const ChainedIndex&, const ChainedIndex&) = default;

 private:
  [[nodiscard]] std::size_t bucket(std::uint64_t reference) const noexcept {
    return MultiplyShift::home(reference, shift_, mask_);
  }

  std::vector<std::uint32_t> heads_;  // First order on each bucket's chain.
  std::vector<std::uint32_t> next_;   // The next order on its chain, by order slot.
  std::size_t mask_;
  unsigned shift_;
};

// A paged direct index: the reference itself, split into page and offset, finds the
// slot. A directory covers every reference below 2^index_bits, 2^30 by default, and
// pages come from a pool sized up front, so a page is held only while one of its
// references is live.
//
// Nothing in the spec bounds a reference (DS-72). A reference past the directory's
// reach, or one whose page cannot be had because the pool is empty, goes to a hash table
// sized for every order instead. A lookup tries its page first and the hash only while the hash
// holds anything, so a day that never falls back never probes it. Each fallback is
// counted, so that none is silent.
template <unsigned PageBits = 8>
class PagedIndex {
 public:
  static constexpr std::uint32_t kPageSlots = 1U << PageBits;
  static constexpr std::uint64_t kOffsetMask = kPageSlots - 1U;
  static constexpr std::uint32_t kMostBits = 40;

  explicit PagedIndex(const BookCapacity& capacity)
      : reach_{std::uint64_t{1} << std::min(capacity.index_bits, kMostBits)},
        directory_(static_cast<std::size_t>(((reach_ - 1) >> PageBits) + 1), kNoIndex),
        slots_(std::size_t{capacity.index_pages} << PageBits, kNoIndex),
        page_live_(capacity.index_pages, 0),
        page_home_(capacity.index_pages, kNoIndex),
        fallback_(capacity.orders) {}

  // Two loads before the slot: the directory entry, then the page's slot. A reference
  // past the reach or on the fallback hash is not looked ahead for.
  static constexpr unsigned kPrefetchStages = 2;

  void prefetch_stage(std::uint64_t reference, unsigned stage) const noexcept {
    if (reference >= reach_) {
      return;
    }
    if (stage == 0) {
      // measured: E8, see FastBook::prefetch_stage.
      __builtin_prefetch(&directory_[static_cast<std::size_t>(reference >> PageBits)]);
      return;
    }
    const std::uint32_t page = directory_[static_cast<std::size_t>(reference >> PageBits)];
    if (page != kNoIndex) {
      // measured: E8, see FastBook::prefetch_stage.
      __builtin_prefetch(&slots_[slot_of(page, reference)]);
    }
  }

  [[nodiscard]] std::uint32_t peek(std::uint64_t reference) const noexcept {
    if (reference >= reach_) {
      return kNoIndex;
    }
    const std::uint32_t page = directory_[static_cast<std::size_t>(reference >> PageBits)];
    return page == kNoIndex ? kNoIndex : slots_[slot_of(page, reference)];
  }

  [[nodiscard]] std::uint32_t find(std::uint64_t reference,
                                   std::span<const BookOrder> orders) const noexcept {
    if (reference < reach_) {
      const std::uint32_t page = directory_[static_cast<std::size_t>(reference >> PageBits)];
      if (page != kNoIndex) {
        const std::uint32_t order = slots_[slot_of(page, reference)];
        if (order != kNoIndex) {
          return order;
        }
      }
    }
    return fallback_live_ == 0 ? kNoIndex : fallback_.find(reference, orders);
  }

  void insert(std::uint64_t reference, std::uint32_t order,
              std::span<const BookOrder> orders) noexcept {
    if (reference < reach_) {
      std::uint32_t& page = directory_[static_cast<std::size_t>(reference >> PageBits)];
      if (page == kNoIndex && has_free_page()) {
        page = take_page(static_cast<std::uint32_t>(reference >> PageBits));
      }
      if (page != kNoIndex) {
        slots_[slot_of(page, reference)] = order;
        ++page_live_[page];
        return;
      }
    }
    fallback_.insert(reference, order, orders);
    ++fallback_live_;
    ++fallback_inserts_;
  }

  void erase(std::uint64_t reference, std::span<const BookOrder> orders) noexcept {
    if (reference < reach_) {
      const std::uint32_t page = directory_[static_cast<std::size_t>(reference >> PageBits)];
      if (page != kNoIndex && slots_[slot_of(page, reference)] != kNoIndex) {
        slots_[slot_of(page, reference)] = kNoIndex;
        if (--page_live_[page] == 0) {
          give_back_page(page);
        }
        return;
      }
    }
    fallback_.erase(reference, orders);
    --fallback_live_;
  }

  // References held by the hash now, and ever.
  [[nodiscard]] std::uint64_t fallback_live() const noexcept { return fallback_live_; }
  [[nodiscard]] std::uint64_t fallback_inserts() const noexcept { return fallback_inserts_; }
  [[nodiscard]] std::uint64_t live_pages() const noexcept { return live_pages_; }
  [[nodiscard]] std::uint64_t peak_live_pages() const noexcept { return peak_live_pages_; }
  [[nodiscard]] std::uint64_t reach() const noexcept { return reach_; }

  friend bool operator==(const PagedIndex&, const PagedIndex&) = default;

 private:
  [[nodiscard]] static std::size_t slot_of(std::uint32_t page, std::uint64_t reference) noexcept {
    return (std::size_t{page} << PageBits) | static_cast<std::size_t>(reference & kOffsetMask);
  }

  [[nodiscard]] bool has_free_page() const noexcept {
    return free_page_ != kNoIndex || fresh_page_ < page_live_.size();
  }

  // A page comes back from the pool with every slot empty, since it went back only once
  // its last reference had gone.
  [[nodiscard]] std::uint32_t take_page(std::uint32_t home) noexcept {
    ++live_pages_;
    peak_live_pages_ = peak_live_pages_ < live_pages_ ? live_pages_ : peak_live_pages_;
    std::uint32_t page = free_page_;
    if (page == kNoIndex) {
      page = fresh_page_++;
    } else {
      free_page_ = page_home_[page];
    }
    page_home_[page] = home;
    return page;
  }

  // A free page's home holds the next free page.
  void give_back_page(std::uint32_t page) noexcept {
    --live_pages_;
    directory_[page_home_[page]] = kNoIndex;
    page_home_[page] = free_page_;
    free_page_ = page;
  }

  std::uint64_t reach_;                   // The first reference that cannot index directly.
  std::vector<std::uint32_t> directory_;  // Page of each run of 2^PageBits references.
  std::vector<std::uint32_t> slots_;      // Order slot of each reference, page by page.
  std::vector<std::uint32_t> page_live_;  // Live references on each page.
  std::vector<std::uint32_t> page_home_;  // The directory entry each page serves.
  HashIndex fallback_;
  std::uint32_t free_page_ = kNoIndex;
  std::uint32_t fresh_page_ = 0;
  std::uint64_t fallback_live_ = 0;
  std::uint64_t fallback_inserts_ = 0;
  std::uint64_t live_pages_ = 0;
  std::uint64_t peak_live_pages_ = 0;
};

}  // namespace itch
