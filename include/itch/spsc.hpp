// A bounded single-producer single-consumer ring, with each of its techniques a
// compile-time switch so that T-SPSC-2 can take them away one at a time.
//
//     SpscRing<T, SpscConfig{...}, Ordering>
//
// One thread calls the producer's functions and one other thread the consumer's. The two
// indices run free, as 64-bit counts that never wrap in practice, so a full ring holds
// all of its slots and no slot is kept empty: tail - head == capacity means full.
//
// The techniques, each on by default:
//   cached_indices  each side keeps its own copy of the other side's index and reads the
//                   shared one only when the copy says the ring is full, or empty. Off,
//                   every push loads the consumer's index and every pop the producer's.
//   wrap            mask: a slot is index & (capacity - 1), and the capacity is rounded
//                   up to a power of two. modulo: index % capacity, a division by a
//                   capacity read at run time, so the compiler cannot make it a mask.
//   batch_publish   the batch calls write or read every slot they can and then store
//                   their index once. Off, they store it after each slot, as single
//                   calls do.
//   padding         the alignment of each side's block: the producer's index with its
//                   copy of the consumer's, the consumer's with its copy of the
//                   producer's, and the fields both read. kCacheLine by default.
//
// Ordering names the four memory orders, so that a negative control can weaken one in a
// test build without touching this file. SpscOrdering is the only one shipped: the
// producer publishes its index with release after writing the slot, and the consumer
// loads it with acquire before reading the slot; the consumer returns slots with a
// release store of its index, and the producer loads it with acquire before writing over
// them. Nothing else is needed: no fence, no seq_cst and no read-modify-write.
//
// The slots are allocated once, in the constructor, zeroed, so that every page is touched
// before the first push. No call after construction allocates (tests/test_alloc.cpp).
#pragma once

#include <atomic>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <span>
#include <type_traits>
#include <vector>

namespace itch {

// The size of the line the ring pads to, from the hardware, one constant for every
// padded member, set for the build's target by cmake/ItchCacheLine.cmake, since parser
// code holds no conditional compilation (scripts/gates.py): 128 on Apple silicon, whose
// cores move 128-byte lines (hw.cachelinesize is 128 on M-series, and XNU sizes its L2
// line as 1 << 7, LC-21), where 64 would leave both indices on one line; 64 on x86-64,
// AMD's Zen 1 on debian among them (LC-24), and on other arm64 cores, the Linux CI
// runners' Neoverse among them. A target CMake has no measured size for leaves it
// undefined, and this does not compile. test_spsc asserts the value against the
// compiler's own target at compile time and against the line size the system reports at
// run time. std::hardware_destructive_interference_size is not used: it is the compiler's
// __GCC_DESTRUCTIVE_SIZE, which on this Mac is 256 under Apple clang 21 and 128 under
// LLVM 23, and which GCC's own documentation calls unfit for a header because it changes
// with the compiler (components.md, LC-17 to LC-20).
inline constexpr std::size_t kCacheLine = ITCH_CACHE_LINE;
static_assert(kCacheLine == 64 || kCacheLine == 128,
              "every line size measured for this project is 64 or 128 bytes");
static_assert(std::has_single_bit(kCacheLine), "a line size is a power of two");

enum class SpscWrap : std::uint8_t { mask, modulo };

struct SpscConfig {
  bool cached_indices = true;
  SpscWrap wrap = SpscWrap::mask;
  bool batch_publish = true;
  std::size_t padding = kCacheLine;
};

// The orders the ring uses, by role.
struct SpscOrdering {
  static constexpr std::memory_order kPublish = std::memory_order_release;  // Producer's index.
  static constexpr std::memory_order kObserve = std::memory_order_acquire;  // Consumer reads it.
  static constexpr std::memory_order kReturn = std::memory_order_release;   // Consumer's index.
  static constexpr std::memory_order kReclaim = std::memory_order_acquire;  // Producer reads it.
};

template <class T, SpscConfig Config = SpscConfig{}, class Ordering = SpscOrdering>
class SpscRing {
  static_assert(std::is_trivially_copyable_v<T>, "a slot is copied in and out whole");
  static_assert(std::has_single_bit(Config.padding) && Config.padding >= alignof(std::uint64_t),
                "padding is a power of two, at least the alignment of an index");

 public:
  using value_type = T;
  static constexpr SpscConfig kConfig = Config;

  // Under the mask, a capacity that is not a power of two is rounded up to one, as folly's
  // queue does. At least one slot.
  explicit SpscRing(std::size_t capacity)
      : shared_{.slots = nullptr, .capacity = slots_for(capacity), .mask = slots_for(capacity) - 1},
        storage_(shared_.capacity) {
    shared_.slots = storage_.data();
  }

  SpscRing(const SpscRing&) = delete;
  SpscRing& operator=(const SpscRing&) = delete;
  SpscRing(SpscRing&&) = delete;
  SpscRing& operator=(SpscRing&&) = delete;
  ~SpscRing() = default;

  [[nodiscard]] std::size_t capacity() const noexcept { return shared_.capacity; }

  // Producer. False, and nothing written, when the ring is full.
  [[nodiscard]] bool try_push(const T& value) noexcept {
    const std::uint64_t tail = producer_.tail.load(std::memory_order_relaxed);
    if (free_slots(tail, 1) == 0) {
      return false;
    }
    shared_.slots[slot(tail)] = value;
    producer_.tail.store(tail + 1, Ordering::kPublish);
    return true;
  }

  // Producer. Writes as many of `values`, from the front, as there is room for, and
  // returns how many.
  [[nodiscard]] std::size_t try_push_batch(std::span<const T> values) noexcept {
    const std::uint64_t tail = producer_.tail.load(std::memory_order_relaxed);
    const std::size_t count = free_slots(tail, values.size());
    for (std::size_t i = 0; i < count; ++i) {
      shared_.slots[slot(tail + i)] = values[i];
      if constexpr (!Config.batch_publish) {
        producer_.tail.store(tail + i + 1, Ordering::kPublish);
      }
    }
    if constexpr (Config.batch_publish) {
      if (count != 0) {
        producer_.tail.store(tail + count, Ordering::kPublish);
      }
    }
    return count;
  }

  // Consumer. False, and `out` untouched, when the ring is empty.
  [[nodiscard]] bool try_pop(T& out) noexcept {
    const std::uint64_t head = consumer_.head.load(std::memory_order_relaxed);
    if (ready_slots(head, 1) == 0) {
      return false;
    }
    out = shared_.slots[slot(head)];
    consumer_.head.store(head + 1, Ordering::kReturn);
    return true;
  }

  // Consumer. Reads as many slots into `out`, from the front, as are ready, and returns
  // how many.
  [[nodiscard]] std::size_t try_pop_batch(std::span<T> out) noexcept {
    const std::uint64_t head = consumer_.head.load(std::memory_order_relaxed);
    const std::size_t count = ready_slots(head, out.size());
    for (std::size_t i = 0; i < count; ++i) {
      out[i] = shared_.slots[slot(head + i)];
      if constexpr (!Config.batch_publish) {
        consumer_.head.store(head + i + 1, Ordering::kReturn);
      }
    }
    if constexpr (Config.batch_publish) {
      if (count != 0) {
        consumer_.head.store(head + count, Ordering::kReturn);
      }
    }
    return count;
  }

  // Where each side's block and the shared block start, for the layout test.
  [[nodiscard]] const void* producer_block() const noexcept { return &producer_; }
  [[nodiscard]] const void* consumer_block() const noexcept { return &consumer_; }
  [[nodiscard]] const void* shared_block() const noexcept { return &shared_; }

 private:
  // Written by the producer alone. cached_head is its copy of the consumer's index.
  struct alignas(Config.padding) Producer {
    std::atomic<std::uint64_t> tail{0};
    std::uint64_t cached_head = 0;
  };
  // Written by the consumer alone. cached_tail is its copy of the producer's index.
  struct alignas(Config.padding) Consumer {
    std::atomic<std::uint64_t> head{0};
    std::uint64_t cached_tail = 0;
  };
  // Read by both and written by neither after construction.
  struct alignas(Config.padding) Shared {
    T* slots;
    std::size_t capacity;
    std::size_t mask;
  };

  [[nodiscard]] static std::size_t slots_for(std::size_t capacity) noexcept {
    const std::size_t wanted = capacity == 0 ? 1 : capacity;
    return Config.wrap == SpscWrap::mask ? std::bit_ceil(wanted) : wanted;
  }

  [[nodiscard]] std::size_t slot(std::uint64_t index) const noexcept {
    if constexpr (Config.wrap == SpscWrap::mask) {
      return static_cast<std::size_t>(index & shared_.mask);
    } else {
      return static_cast<std::size_t>(index % shared_.capacity);
    }
  }

  // Up to `wanted` slots free from `tail` on. The shared index is read only when the
  // copy shows fewer than wanted, and a full ring can only free more, so a stale copy
  // is never unsafe.
  [[nodiscard]] std::size_t free_slots(std::uint64_t tail, std::size_t wanted) noexcept {
    const std::uint64_t capacity = shared_.capacity;
    if constexpr (Config.cached_indices) {
      std::uint64_t free = capacity - (tail - producer_.cached_head);
      if (free < wanted) {
        producer_.cached_head = consumer_.head.load(Ordering::kReclaim);
        free = capacity - (tail - producer_.cached_head);
      }
      return static_cast<std::size_t>(free < wanted ? free : wanted);
    } else {
      const std::uint64_t free = capacity - (tail - consumer_.head.load(Ordering::kReclaim));
      return static_cast<std::size_t>(free < wanted ? free : wanted);
    }
  }

  // Up to `wanted` slots ready from `head` on, by the same rule.
  [[nodiscard]] std::size_t ready_slots(std::uint64_t head, std::size_t wanted) noexcept {
    if constexpr (Config.cached_indices) {
      std::uint64_t ready = consumer_.cached_tail - head;
      if (ready < wanted) {
        consumer_.cached_tail = producer_.tail.load(Ordering::kObserve);
        ready = consumer_.cached_tail - head;
      }
      return static_cast<std::size_t>(ready < wanted ? ready : wanted);
    } else {
      const std::uint64_t ready = producer_.tail.load(Ordering::kObserve) - head;
      return static_cast<std::size_t>(ready < wanted ? ready : wanted);
    }
  }

  Producer producer_;
  Consumer consumer_;
  Shared shared_;
  std::vector<T> storage_;  // Owns what shared_.slots points at; never resized.
};

}  // namespace itch
