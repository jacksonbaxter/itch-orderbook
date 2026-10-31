// Counts calls to every form of global operator new. alloc_counter.cpp replaces them,
// so a binary links that file to be counted. Direct malloc calls are not seen.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace itch::oracle {

enum class AllocForm : std::uint8_t {
  plain,
  array,
  nothrow,
  array_nothrow,
  aligned,
  array_aligned,
  aligned_nothrow,
  array_aligned_nothrow,
};

inline constexpr std::array<std::string_view, 8> kAllocFormNames{
    "new",     "new[]",         "new_nothrow",         "new[]_nothrow",
    "aligned", "aligned_new[]", "aligned_new_nothrow", "aligned_new[]_nothrow",
};

// The calls made in each form since the last reset, in AllocForm order.
struct AllocCounts {
  std::array<std::uint64_t, 8> by_form{};

  [[nodiscard]] std::uint64_t total() const noexcept {
    std::uint64_t sum = 0;
    for (const std::uint64_t count : by_form) {
      sum += count;
    }
    return sum;
  }
  [[nodiscard]] std::uint64_t of(AllocForm form) const noexcept {
    return by_form[static_cast<std::size_t>(form)];
  }
};

void reset_alloc_counts() noexcept;
[[nodiscard]] AllocCounts read_alloc_counts() noexcept;

}  // namespace itch::oracle
