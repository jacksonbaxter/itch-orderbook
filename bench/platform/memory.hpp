// The arena behind the latency samples, and the check that the input is in memory.
//
// Arena::make() asks for hugepages where the platform has a hint, touches every page so
// that no fault lands inside a timed span, and reports the page size it actually got.
// resident() asks the kernel, so a test can see it say no.
#pragma once

#include <sys/mman.h>
#include <sys/resource.h>
#include <unistd.h>

#include <cstddef>
#include <cstdint>
#include <format>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#if defined(__linux__) && !defined(__EMSCRIPTEN__)
#include <fstream>
#include <sstream>
#endif

namespace itch::bench {

struct Residency {
  std::size_t pages = 0;
  std::size_t resident_pages = 0;
  std::string error;  // Set when the kernel would not say.

  [[nodiscard]] double fraction() const noexcept {
    return pages == 0 ? 0.0 : static_cast<double>(resident_pages) / static_cast<double>(pages);
  }
  // Every page, not most of them: one miss mid-run is a disk read inside a timed span.
  [[nodiscard]] bool complete() const noexcept {
    return error.empty() && pages != 0 && resident_pages == pages;
  }
  [[nodiscard]] std::string describe() const {
    if (!error.empty()) {
      return std::format("unavailable: {}", error);
    }
    return std::format("{} of {} pages resident", resident_pages, pages);
  }
};

[[nodiscard]] inline std::size_t base_page_size() noexcept {
  return static_cast<std::size_t>(::getpagesize());
}

// mincore's vector is char on macOS and unsigned char on Linux. On both, bit 0 means in
// core, and a file page counts whether or not this process has touched it.
#ifdef __APPLE__
using MincoreFlag = char;
#else
using MincoreFlag = unsigned char;
#endif

[[nodiscard]] inline Residency resident(std::span<const std::byte> bytes) {
  Residency out;
  const std::size_t page = base_page_size();
  const auto address = reinterpret_cast<std::uintptr_t>(bytes.data());  // NOLINT
  if (bytes.empty()) {
    out.error = "empty input";
    return out;
  }
  if (address % page != 0) {
    out.error = "input is not page aligned";
    return out;
  }
  out.pages = (bytes.size() + page - 1) / page;
  std::vector<MincoreFlag> flags(out.pages);
  // mincore takes a void*, and reads only the mapping's state.
  void* start = const_cast<std::byte*>(bytes.data());  // NOLINT
  if (::mincore(start, bytes.size(), flags.data()) != 0) {
    out.error = "mincore failed";
    return out;
  }
  for (const MincoreFlag flag : flags) {
    out.resident_pages += (static_cast<unsigned>(flag) & 1U) != 0U ? 1U : 0U;
  }
  return out;
}

// The kernel's account of one mapping in /proc/self/smaps: the page size it got, from
// the KernelPageSize line, unless AnonHugePages says transparent hugepages backed it.
[[nodiscard]] inline std::optional<std::size_t> hugepage_kb_from_smaps(std::string_view smaps,
                                                                       std::uintptr_t start) {
  const std::string head = std::format("{:x}-", start);
  const std::size_t at = smaps.starts_with(head) ? 0 : smaps.find("\n" + head);
  if (at == std::string_view::npos) {
    return std::nullopt;
  }
  const std::string_view rest = smaps.substr(at);
  constexpr std::string_view kField = "AnonHugePages:";
  const std::size_t field = rest.find(kField);
  if (field == std::string_view::npos) {
    return std::nullopt;
  }
  std::size_t value = 0;
  for (std::size_t i = field + kField.size(); i < rest.size() && rest[i] != 'k'; ++i) {
    if (rest[i] >= '0' && rest[i] <= '9') {
      value = (value * 10) + static_cast<std::size_t>(rest[i] - '0');
    }
  }
  return value;
}

struct ArenaReport {
  std::size_t bytes = 0;
  std::size_t page_size_obtained = 0;
  std::string hint;  // What the hugepage hint did.
  bool prefaulted = false;
};

[[nodiscard]] inline std::string describe_arena(const ArenaReport& report) {
  return std::format("{} bytes obtained per page; hint: {}; prefaulted: {}",
                     report.page_size_obtained, report.hint, report.prefaulted ? "yes" : "no");
}

class Arena {
 public:
  [[nodiscard]] static std::optional<Arena> make(std::size_t bytes);

  Arena(const Arena&) = delete;
  Arena& operator=(const Arena&) = delete;
  Arena(Arena&& other) noexcept
      : base_{other.base_},
        mapped_{other.mapped_},
        data_{other.data_},
        report_{std::move(other.report_)} {
    other.base_ = nullptr;
  }
  Arena& operator=(Arena&&) = delete;
  ~Arena() {
    if (base_ != nullptr) {
      ::munmap(base_, mapped_);
    }
  }

  [[nodiscard]] std::byte* data() const noexcept { return data_; }
  [[nodiscard]] std::size_t size() const noexcept { return report_.bytes; }
  [[nodiscard]] const ArenaReport& report() const noexcept { return report_; }

 private:
  Arena(void* base, std::size_t mapped, std::byte* data, ArenaReport report)
      : base_{base}, mapped_{mapped}, data_{data}, report_{std::move(report)} {}

  void* base_;
  std::size_t mapped_;
  std::byte* data_;
  ArenaReport report_;
};

inline std::optional<Arena> Arena::make(std::size_t bytes) {
  const std::size_t page = base_page_size();
  const std::size_t wanted = bytes == 0 ? page : (bytes + page - 1) / page * page;
#if defined(__linux__) && !defined(__EMSCRIPTEN__)
  // Aligned to 2 MB, or transparent hugepages cannot back the first and last of it.
  constexpr std::size_t kHuge = std::size_t{2} * 1024 * 1024;
  const std::size_t mapped = wanted + kHuge;
  void* base = ::mmap(nullptr, mapped, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (base == MAP_FAILED) {
    return std::nullopt;
  }
  const auto address = reinterpret_cast<std::uintptr_t>(base);  // NOLINT
  auto* data = static_cast<std::byte*>(base) + ((kHuge - address % kHuge) % kHuge);
  const int advised = ::madvise(data, wanted, MADV_HUGEPAGE);
  std::string hint =
      advised == 0 ? "madvise(MADV_HUGEPAGE) accepted" : "madvise(MADV_HUGEPAGE) refused";
#else
  const std::size_t mapped = wanted;
  void* base = ::mmap(nullptr, mapped, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
  if (base == MAP_FAILED) {
    return std::nullopt;
  }
  auto* data = static_cast<std::byte*>(base);
  std::string hint = "none on this platform, base pages used";
#endif
  for (std::size_t offset = 0; offset < wanted; offset += page) {
    data[offset] = std::byte{0};
  }
  std::size_t obtained = page;  // NOLINT(misc-const-correctness): the Linux branch moves it.
#if defined(__linux__) && !defined(__EMSCRIPTEN__)
  std::ifstream smaps{"/proc/self/smaps"};
  std::stringstream text;
  text << smaps.rdbuf();
  const auto start = reinterpret_cast<std::uintptr_t>(data);  // NOLINT
  const std::optional<std::size_t> huge_kb = hugepage_kb_from_smaps(text.str(), start);
  if (huge_kb && *huge_kb != 0) {
    obtained = kHuge;
    hint += std::format(", {} kB in transparent hugepages", *huge_kb);
  }
#endif
  return Arena{base, mapped, data,
               ArenaReport{.bytes = wanted,
                           .page_size_obtained = obtained,
                           .hint = std::move(hint),
                           .prefaulted = true}};
}

// Peak resident set of this process in bytes. Linux reports kB and macOS bytes.
[[nodiscard]] inline std::uint64_t peak_rss_bytes() noexcept {
  rusage usage{};
  if (::getrusage(RUSAGE_SELF, &usage) != 0) {
    return 0;
  }
#ifdef __APPLE__
  return static_cast<std::uint64_t>(usage.ru_maxrss);
#else
  return static_cast<std::uint64_t>(usage.ru_maxrss) * 1024U;
#endif
}

}  // namespace itch::bench
