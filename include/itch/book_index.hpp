// The order lookup arms: reference to the order's slot in the pool.
//
// Each is a policy of FastBook, sized in the constructor and never grown. A lookup takes
// the order pool, because the hash keeps only slots and confirms a key by reading the
// order. The book calls insert only for a reference that find did not return, and erase
// only for one that is live.
#pragma once

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

}  // namespace itch
