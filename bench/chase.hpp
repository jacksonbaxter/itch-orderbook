// A pointer chase whose cache behaviour follows from the buffer size alone.
//
// One pointer per cache line, linked into one random cycle through every line. Each load
// needs the one before it, so no two overlap, and the order gives a prefetcher nothing to
// follow. A buffer that fits a cache level stays in it after a lap, and one that does not
// misses it on nearly every load. That is a workload with a known answer, which is what a
// counter has to be held against before its count is believed.
#pragma once

#include <array>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <numeric>
#include <optional>
#include <random>
#include <span>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

namespace itch::bench {

inline constexpr std::size_t kChaseLineBytes = 64;

struct ChaseLine {
  const ChaseLine* next = nullptr;
  std::array<std::byte, kChaseLineBytes - sizeof(const ChaseLine*)> padding{};
};
static_assert(sizeof(ChaseLine) == kChaseLineBytes);

// Sattolo's shuffle. Never swapping an element with itself is what leaves one cycle
// through every line, where a Fisher-Yates shuffle leaves several shorter ones.
inline void link_single_cycle(std::span<ChaseLine> lines, std::uint64_t seed) {
  std::vector<std::uint32_t> successor(lines.size());
  std::iota(successor.begin(), successor.end(), 0U);
  // The same cycle every run, for a given size.
  // NOLINTNEXTLINE(cert-msc32-c,cert-msc51-cpp,bugprone-random-generator-seed)
  std::mt19937_64 random{seed};
  for (std::size_t i = lines.size() - 1; i > 0; --i) {
    const std::size_t other = static_cast<std::size_t>(random() % i);
    std::swap(successor[i], successor[other]);
  }
  for (std::size_t i = 0; i < lines.size(); ++i) {
    lines[i].next = &lines[successor[i]];
  }
}

[[nodiscard]] inline std::size_t cycle_length(std::span<const ChaseLine> lines) {
  std::size_t length = 1;
  for (const ChaseLine* line = lines[0].next; line != lines.data(); line = line->next) {
    ++length;
  }
  return length;
}

// Not inlined, so that the loop is the same code for every size.
[[gnu::noinline]] inline const ChaseLine* chase(const ChaseLine* line, std::uint64_t loads) {
  for (std::uint64_t i = 0; i < loads; ++i) {
    line = line->next;
  }
  return line;
}

// A number with K or M after it, and nothing else.
[[nodiscard]] inline std::optional<std::size_t> parse_chase_size(std::string_view text) {
  if (text.size() < 2) {
    return std::nullopt;
  }
  std::size_t unit = 0;
  switch (text.back()) {
    case 'K': unit = 1024; break;
    case 'M': unit = std::size_t{1024} * 1024; break;
    default: return std::nullopt;
  }
  std::size_t count = 0;
  const char* const end = text.data() + text.size() - 1;
  const auto [stopped, error] = std::from_chars(text.data(), end, count);
  if (error != std::errc{} || stopped != end) {
    return std::nullopt;
  }
  return count * unit;
}

// For debian: well under its 512 KB L2, then between L2 and its 4 MB L3, then far above.
[[nodiscard]] inline std::vector<std::size_t> default_chase_sizes() {
  constexpr std::size_t kKiB = 1024;
  constexpr std::size_t kMiB = kKiB * 1024;
  return {64 * kKiB, 128 * kKiB, 256 * kKiB, kMiB,      2 * kMiB,
          3 * kMiB,  8 * kMiB,   16 * kMiB,  64 * kMiB, 256 * kMiB};
}

}  // namespace itch::bench
