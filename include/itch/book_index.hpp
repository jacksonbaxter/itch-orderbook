// The two order lookup arms: reference to the order's slot in the pool.
//
// Each is a policy of FastBook, sized in the constructor and never grown. A lookup takes
// the order pool, because the hash keeps only slots and confirms a key by reading the
// order. The book calls insert only for a reference that find did not return, and erase
// only for one that is live.
#pragma once

#include <algorithm>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

#include "itch/book_types.hpp"

namespace itch {

// Open addressing with linear probing, at most half full, keyed by a multiply-shift of
// the reference. Any 64-bit reference is a key.
class HashIndex {
 public:
  explicit HashIndex(const BookCapacity& capacity) : HashIndex(capacity.orders) {}

  explicit HashIndex(std::uint32_t orders)
      : slots_(std::bit_ceil(std::size_t{2} * orders), kNoIndex),
        mask_{slots_.size() - 1},
        shift_{64U - static_cast<unsigned>(std::countr_zero(slots_.size()))} {}

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

  friend bool operator==(const HashIndex&, const HashIndex&) = default;

 private:
  [[nodiscard]] std::size_t home_slot(std::uint64_t reference) const noexcept {
    return static_cast<std::size_t>((reference * 0x9E3779B97F4A7C15ULL) >> shift_);
  }

  std::vector<std::uint32_t> slots_;
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
