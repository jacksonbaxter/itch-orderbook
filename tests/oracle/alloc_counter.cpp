// Replaces every form of global operator new with a counted one that calls malloc, and
// every operator delete to match.
#include "oracle/alloc_counter.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <new>

namespace itch::oracle {

// NOLINTNEXTLINE(cppcoreguidelines-avoid-non-const-global-variables)
static std::array<std::atomic<std::uint64_t>, 8> g_counts{};

static void count(AllocForm form) noexcept {
  g_counts[static_cast<std::size_t>(form)].fetch_add(1, std::memory_order_relaxed);
}

static void* allocate(std::size_t size) noexcept {
  return std::malloc(size != 0 ? size : 1);  // NOLINT(cppcoreguidelines-no-malloc)
}

static void* allocate_aligned(std::size_t size, std::align_val_t alignment) noexcept {
  void* memory = nullptr;
  const auto bytes = static_cast<std::size_t>(alignment);
  return ::posix_memalign(&memory, bytes < sizeof(void*) ? sizeof(void*) : bytes,
                          size != 0 ? size : 1) == 0
             ? memory
             : nullptr;
}

static void* allocate_or_throw(std::size_t size) {
  void* memory = allocate(size);
  if (memory == nullptr) {
    throw std::bad_alloc{};
  }
  return memory;
}

static void* allocate_aligned_or_throw(std::size_t size, std::align_val_t alignment) {
  void* memory = allocate_aligned(size, alignment);
  if (memory == nullptr) {
    throw std::bad_alloc{};
  }
  return memory;
}

void reset_alloc_counts() noexcept {
  for (std::atomic<std::uint64_t>& counter : g_counts) {
    counter.store(0, std::memory_order_relaxed);
  }
}

AllocCounts read_alloc_counts() noexcept {
  AllocCounts counts;
  for (std::size_t i = 0; i < g_counts.size(); ++i) {
    counts.by_form[i] = g_counts[i].load(std::memory_order_relaxed);
  }
  return counts;
}

}  // namespace itch::oracle

// NOLINTBEGIN(cppcoreguidelines-no-malloc,cppcoreguidelines-owning-memory,misc-new-delete-overloads)
namespace oracle = itch::oracle;
using oracle::AllocForm;

void* operator new(std::size_t size) {
  oracle::count(AllocForm::plain);
  return oracle::allocate_or_throw(size);
}
void* operator new[](std::size_t size) {
  oracle::count(AllocForm::array);
  return oracle::allocate_or_throw(size);
}
void* operator new(std::size_t size, const std::nothrow_t& /*tag*/) noexcept {
  oracle::count(AllocForm::nothrow);
  return oracle::allocate(size);
}
void* operator new[](std::size_t size, const std::nothrow_t& /*tag*/) noexcept {
  oracle::count(AllocForm::array_nothrow);
  return oracle::allocate(size);
}
void* operator new(std::size_t size, std::align_val_t alignment) {
  oracle::count(AllocForm::aligned);
  return oracle::allocate_aligned_or_throw(size, alignment);
}
void* operator new[](std::size_t size, std::align_val_t alignment) {
  oracle::count(AllocForm::array_aligned);
  return oracle::allocate_aligned_or_throw(size, alignment);
}
void* operator new(std::size_t size, std::align_val_t alignment,
                   const std::nothrow_t& /*tag*/) noexcept {
  oracle::count(AllocForm::aligned_nothrow);
  return oracle::allocate_aligned(size, alignment);
}
void* operator new[](std::size_t size, std::align_val_t alignment,
                     const std::nothrow_t& /*tag*/) noexcept {
  oracle::count(AllocForm::array_aligned_nothrow);
  return oracle::allocate_aligned(size, alignment);
}

void operator delete(void* memory) noexcept { std::free(memory); }
void operator delete[](void* memory) noexcept { std::free(memory); }
void operator delete(void* memory, std::size_t /*size*/) noexcept { std::free(memory); }
void operator delete[](void* memory, std::size_t /*size*/) noexcept { std::free(memory); }
void operator delete(void* memory, const std::nothrow_t& /*tag*/) noexcept { std::free(memory); }
void operator delete[](void* memory, const std::nothrow_t& /*tag*/) noexcept { std::free(memory); }
void operator delete(void* memory, std::align_val_t /*alignment*/) noexcept { std::free(memory); }
void operator delete[](void* memory, std::align_val_t /*alignment*/) noexcept { std::free(memory); }
void operator delete(void* memory, std::size_t /*size*/, std::align_val_t /*alignment*/) noexcept {
  std::free(memory);
}
void operator delete[](void* memory, std::size_t /*size*/,
                       std::align_val_t /*alignment*/) noexcept {
  std::free(memory);
}
void operator delete(void* memory, std::align_val_t /*alignment*/,
                     const std::nothrow_t& /*tag*/) noexcept {
  std::free(memory);
}
void operator delete[](void* memory, std::align_val_t /*alignment*/,
                       const std::nothrow_t& /*tag*/) noexcept {
  std::free(memory);
}
// NOLINTEND(cppcoreguidelines-no-malloc,cppcoreguidelines-owning-memory,misc-new-delete-overloads)
