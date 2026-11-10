// The allocator arms of T-ARENA-1 a tool or a benchmark can run, by name, for --allocator.
//
// Each is the node book (itch/node_book.hpp) on one arm of itch/node_arms.hpp, and
// malloc-aged is the malloc arm on a heap HeapAger ages as the day goes. with_alloc_arm()
// calls a generic callable with the arm's book type, so that one binary holds every arm.
// mimalloc is there only in a build that fetched it (ITCH_MIMALLOC): elsewhere its name
// parses and with_alloc_arm() says it is missing, so that a run that asks for it is refused
// rather than run on some other arm.
#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <span>
#include <string_view>

#include "itch/heap_ager.hpp"
#include "itch/node_arms.hpp"
#include "itch/node_book.hpp"
#ifdef ITCH_HAVE_MIMALLOC
#include "itch/mimalloc_arm.hpp"
#endif

namespace itch::tools {

enum class AllocArm : std::uint8_t {
  malloc_fresh,
  malloc_aged,
  mimalloc,
  pmr_pool,
  pmr_monotonic,
  pool_index,
  pool_pointer,
  pool_fifo,
};

inline constexpr std::array kAllocArms{
    AllocArm::malloc_fresh,  AllocArm::malloc_aged, AllocArm::mimalloc,     AllocArm::pmr_pool,
    AllocArm::pmr_monotonic, AllocArm::pool_index,  AllocArm::pool_pointer, AllocArm::pool_fifo,
};

[[nodiscard]] constexpr std::string_view to_string(AllocArm arm) noexcept {
  switch (arm) {
    case AllocArm::malloc_fresh: return "malloc-fresh";
    case AllocArm::malloc_aged: return "malloc-aged";
    case AllocArm::mimalloc: return "mimalloc";
    case AllocArm::pmr_pool: return "pmr-pool";
    case AllocArm::pmr_monotonic: return "pmr-monotonic";
    case AllocArm::pool_index: return "pool-index";
    case AllocArm::pool_pointer: return "pool-pointer";
    case AllocArm::pool_fifo: return "pool-fifo";
  }
  return "unnamed";
}

[[nodiscard]] constexpr std::optional<AllocArm> parse_alloc_arm(std::string_view name) noexcept {
  for (const AllocArm arm : kAllocArms) {
    if (name == to_string(arm)) {
      return arm;
    }
  }
  return std::nullopt;
}

// The arms whose nodes come from a pool built and pre-faulted up front, which must
// allocate nothing and fault nothing once the book is built.
[[nodiscard]] constexpr bool is_pool_arm(AllocArm arm) noexcept {
  return arm == AllocArm::pool_index || arm == AllocArm::pool_pointer || arm == AllocArm::pool_fifo;
}

// The arms whose calls tests/oracle/alloc_counter.cpp sees: every one that goes through
// the global operator new. mimalloc's mi_new does not.
[[nodiscard]] constexpr bool counter_sees(AllocArm arm) noexcept {
  return arm != AllocArm::mimalloc && !is_pool_arm(arm);
}

[[nodiscard]] constexpr bool mimalloc_built() noexcept {
#ifdef ITCH_HAVE_MIMALLOC
  return true;
#else
  return false;
#endif
}

// The malloc arm's book on a heap aged as heap_ager.hpp says. The ager is built first, so
// the heap is aged before the book takes anything from it. age_heap() is the step a
// caller takes before each message that adds an order (bench/replay.hpp's AgesHeap), and
// before_message() the same step for a caller that hands it every payload.
template <class Arm>
class AgedNodeBook {
 public:
  using Book = NodeBook<Arm>;

  explicit AgedNodeBook(const NodeCapacity& capacity = {}, const HeapAgeing& ageing = {})
      : ager_{ageing}, book_{capacity} {}

  [[nodiscard]] ApplyStatus apply(std::span<const std::byte> payload) {
    return book_.apply(payload);
  }
  [[nodiscard]] TopOfBook best(std::uint16_t locate, Side side) const noexcept {
    return book_.best(locate, side);
  }
  void age_heap() { ager_.step(); }
  void before_message(std::span<const std::byte> payload) { ager_.before_message(payload); }

  [[nodiscard]] const Book& book() const noexcept { return book_; }
  [[nodiscard]] const HeapAger& ager() const noexcept { return ager_; }
  // The ballast the ager holds, which the untimed pass's memory figure includes.
  [[nodiscard]] std::uint64_t ager_held_bytes() const noexcept { return ager_.held_bytes(); }

 private:
  HeapAger ager_;
  Book book_;
};

// Calls run.template operator()<Book>() for the arm, Book its node book type, and returns
// what it does; missing() when the arm is mimalloc and this build has none.
template <class Run, class Missing>
decltype(auto) with_alloc_arm(AllocArm arm, const Run& run,
                              [[maybe_unused]] const Missing& missing) {
  switch (arm) {
    case AllocArm::malloc_fresh: return run.template operator()<NodeBook<MallocArm>>();
    case AllocArm::malloc_aged: return run.template operator()<AgedNodeBook<MallocArm>>();
    case AllocArm::mimalloc:
#ifdef ITCH_HAVE_MIMALLOC
      return run.template operator()<NodeBook<MimallocArm>>();
#else
      return missing();
#endif
    case AllocArm::pmr_pool: return run.template operator()<NodeBook<PmrPoolArm>>();
    case AllocArm::pmr_monotonic: return run.template operator()<NodeBook<PmrMonotonicArm>>();
    case AllocArm::pool_index: return run.template operator()<NodeBook<PoolIndexArm>>();
    case AllocArm::pool_pointer: return run.template operator()<NodeBook<PoolPointerArm>>();
    case AllocArm::pool_fifo: break;
  }
  return run.template operator()<NodeBook<PoolFifoArm>>();
}

// The node book under a book type with_alloc_arm() hands over.
template <class Book>
[[nodiscard]] const auto& node_book_of(const Book& book) noexcept {
  if constexpr (requires { book.book(); }) {
    return book.book();
  } else {
    return book;
  }
}

}  // namespace itch::tools
