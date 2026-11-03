// Ages the heap that the map book allocates from, for the aged run of T-DESIGN-1.
//
// Optiver's talk at CppCon 2024 (FT-1 and FT-2 in the research) timed a std::map book at a
// median 33 ns, then at 63 ns after allocating "some memory locations" between adds, "just
// to randomize the heap". Its reason: consecutive mallocs hand a fresh benchmark one
// contiguous run of memory, which a production heap never is. The talk gives no sizes,
// counts or order. This class fills them in, and fixes them by a seed, so that every run
// with that seed makes the same calls in the same order:
//
//   1. The constructor allocates warm_blocks blocks of 16 to 256 bytes, in steps of 16,
//      which spans the sizes of the map book's nodes. It shuffles them and frees the first
//      half in that order. The rest stays as ballast, with the holes between it on the
//      allocator's free lists in no address order.
//   2. Before each message that adds an order, an A, an F or a U, before_message() frees
//      the block in one drawn slot of a ring of ring_blocks, and allocates one of a drawn
//      size in its place. So the ballast keeps moving while the book grows.
//
// The calls are deterministic. The addresses they return are not: those depend on the
// allocator and on address-space randomisation. Every block is freed on destruction.
// The draws come from splitmix64 rather than <random>, whose distributions differ between
// standard libraries.
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <new>
#include <span>
#include <utility>
#include <vector>

#include "itch/messages.hpp"
#include "itch/wire.hpp"

namespace itch {

struct HeapAgeing {
  std::uint64_t seed = 0x243F6A8885A308D3ULL;
  std::size_t warm_blocks = std::size_t{1} << 20U;
  std::size_t ring_blocks = std::size_t{1} << 16U;
};

class HeapAger {
 public:
  static constexpr std::size_t kSizeStep = 16;
  static constexpr std::size_t kSizeSteps = 16;

  explicit HeapAger(const HeapAgeing& ageing) : state_{ageing.seed}, ring_(ageing.ring_blocks) {
    std::vector<Block> warm;
    warm.reserve(ageing.warm_blocks);
    for (std::size_t i = 0; i < ageing.warm_blocks; ++i) {
      warm.push_back(take());
    }
    // Fisher-Yates, from the end.
    for (std::size_t i = warm.size(); i > 1; --i) {
      std::swap(warm[i - 1], warm[next() % i]);
    }
    const std::size_t freed = warm.size() / 2;
    for (std::size_t i = 0; i < freed; ++i) {
      give_back(warm[i]);
    }
    held_.assign(warm.begin() + static_cast<std::ptrdiff_t>(freed), warm.end());
  }

  HeapAger(const HeapAger&) = delete;
  HeapAger& operator=(const HeapAger&) = delete;
  HeapAger(HeapAger&&) = delete;
  HeapAger& operator=(HeapAger&&) = delete;

  ~HeapAger() {
    for (Block& block : held_) {
      give_back(block);
    }
    for (Block& block : ring_) {
      give_back(block);
    }
  }

  // Moves one block of ballast if this payload adds an order.
  void before_message(std::span<const std::byte> payload) {
    if (payload.empty()) {
      return;
    }
    const char type = read_char(payload.data());
    if (type == AddOrder::kType || type == AddOrderAttributed::kType ||
        type == OrderReplace::kType) {
      step();
    }
  }

  void step() {
    if (ring_.empty()) {
      return;
    }
    Block& slot = ring_[next() % ring_.size()];
    give_back(slot);
    slot = take();
  }

  [[nodiscard]] std::uint64_t allocations() const noexcept { return allocations_; }
  [[nodiscard]] std::uint64_t releases() const noexcept { return releases_; }
  [[nodiscard]] std::uint64_t held_bytes() const noexcept { return held_bytes_; }
  // The next draw, for a test that two agers with one seed draw alike.
  [[nodiscard]] std::uint64_t peek() const noexcept { return mix(state_ + kGamma); }

 private:
  struct Block {
    void* at = nullptr;
    std::size_t size = 0;
  };

  static constexpr std::uint64_t kGamma = 0x9E3779B97F4A7C15ULL;

  [[nodiscard]] static constexpr std::uint64_t mix(std::uint64_t z) noexcept {
    z = (z ^ (z >> 30U)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27U)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31U);
  }

  [[nodiscard]] std::uint64_t next() noexcept {
    state_ += kGamma;
    return mix(state_);
  }

  [[nodiscard]] Block take() {
    const std::size_t size = kSizeStep * (1 + (next() % kSizeSteps));
    void* at = ::operator new(size);
    std::memset(at, 0, 1);  // Touched, as a block in use would be.
    ++allocations_;
    held_bytes_ += size;
    return {.at = at, .size = size};
  }

  void give_back(Block& block) noexcept {
    if (block.at == nullptr) {
      return;
    }
    ::operator delete(block.at);
    ++releases_;
    held_bytes_ -= block.size;
    block = {};
  }

  std::uint64_t state_;
  std::vector<Block> ring_;
  std::vector<Block> held_;
  std::uint64_t allocations_ = 0;
  std::uint64_t releases_ = 0;
  std::uint64_t held_bytes_ = 0;
};

}  // namespace itch
