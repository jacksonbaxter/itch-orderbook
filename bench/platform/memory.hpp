// The arena behind the latency samples and an input read into memory, the check that the
// input is in memory, and the memory charged to the process.
//
// Arena::make() asks for hugepages where the platform has a hint, unless told not to,
// touches every page so that no fault lands inside a timed span, unless the caller will
// write every page itself, and reports the page size it actually got. resident() asks the
// kernel, so a test can see it say no.
#pragma once

#include <sys/mman.h>
#include <sys/resource.h>
#include <unistd.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <format>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#ifdef __APPLE__
#include <mach/mach.h>
#endif

#if defined(__linux__) && !defined(__EMSCRIPTEN__)
#include <fcntl.h>

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

// What the process is charged for now, in bytes, apart from file pages: the measure of a
// book's memory (T-BOOK-3). On macOS that is phys_footprint, the figure Activity Monitor
// and `footprint` report, which counts dirty anonymous pages whether resident or
// compressed. On Linux it is RssAnon, the anonymous pages resident now.
#ifdef __APPLE__
inline constexpr std::string_view kChargedSource = "phys_footprint from task_info(TASK_VM_INFO)";
#else
inline constexpr std::string_view kChargedSource = "RssAnon from /proc/self/status";
#endif

// RssAnon in kB from the text of /proc/self/status, or nothing if the line is not there.
[[nodiscard]] inline std::optional<std::uint64_t> rss_anon_kb(std::string_view status) noexcept {
  constexpr std::string_view kField = "\nRssAnon:";
  const std::size_t at = status.find(kField);
  if (at == std::string_view::npos) {
    return std::nullopt;
  }
  std::size_t i = at + kField.size();
  while (i < status.size() && (status[i] == ' ' || status[i] == '\t')) {
    ++i;
  }
  if (i == status.size() || status[i] < '0' || status[i] > '9') {
    return std::nullopt;
  }
  std::uint64_t kb = 0;
  for (; i < status.size() && status[i] >= '0' && status[i] <= '9'; ++i) {
    kb = (kb * 10) + static_cast<std::uint64_t>(status[i] - '0');
  }
  return kb;
}

// Reads without allocating, so that sampling inside a pass changes no heap a book uses.
[[nodiscard]] inline std::optional<std::uint64_t> charged_bytes() noexcept {
#ifdef __APPLE__
  task_vm_info_data_t info{};
  mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast): the Mach call's own type.
  if (::task_info(mach_task_self(), TASK_VM_INFO, reinterpret_cast<task_info_t>(&info), &count) !=
      KERN_SUCCESS) {
    return std::nullopt;
  }
  return info.phys_footprint;
#elif defined(__linux__) && !defined(__EMSCRIPTEN__)
  std::array<char, 8192> text{};
  const int fd =
      ::open("/proc/self/status", O_RDONLY);  // NOLINT(cppcoreguidelines-pro-type-vararg)
  if (fd < 0) {
    return std::nullopt;
  }
  std::size_t used = 0;
  for (ssize_t got = 1; got > 0 && used < text.size();) {
    got = ::read(fd, text.data() + used, text.size() - used);
    used += got > 0 ? static_cast<std::size_t>(got) : 0U;
  }
  ::close(fd);
  const std::optional<std::uint64_t> kb = rss_anon_kb({text.data(), used});
  return kb ? std::optional<std::uint64_t>{*kb * 1024U} : std::nullopt;
#else
  return std::nullopt;
#endif
}

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

// The kB of transparent hugepages backing the mapping that starts at `start`, or nothing
// where the kernel has no such account.
[[nodiscard]] inline std::optional<std::size_t> hugepage_kb_of(const void* start) {
#if defined(__linux__) && !defined(__EMSCRIPTEN__)
  std::ifstream smaps{"/proc/self/smaps"};
  std::stringstream text;
  text << smaps.rdbuf();
  return hugepage_kb_from_smaps(text.str(), reinterpret_cast<std::uintptr_t>(start));  // NOLINT
#else
  static_cast<void>(start);
  return std::nullopt;
#endif
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

// Whether an arena asks for hugepages. The input read into memory does not: the sources
// for T-BOOK-1 ran with transparent hugepages off.
enum class PageHint : std::uint8_t { huge, base };

// Whether make() touches every page, or leaves that to the caller's first write. The input
// read into memory is faulted in by the reads that fill it, a piece at a time, so that the
// whole copy is never charged before the file's pages have been let go.
enum class Prefault : std::uint8_t { now, by_caller };

class Arena {
 public:
  [[nodiscard]] static std::optional<Arena> make(std::size_t bytes,
                                                 PageHint hint_wanted = PageHint::huge,
                                                 Prefault prefault = Prefault::now);

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

inline std::optional<Arena> Arena::make(std::size_t bytes, PageHint hint_wanted,
                                        Prefault prefault) {
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
  std::string hint;
  if (hint_wanted == PageHint::huge) {
    const int advised = ::madvise(data, wanted, MADV_HUGEPAGE);
    hint = advised == 0 ? "madvise(MADV_HUGEPAGE) accepted" : "madvise(MADV_HUGEPAGE) refused";
  } else {
    const int advised = ::madvise(data, wanted, MADV_NOHUGEPAGE);
    hint = advised == 0 ? "madvise(MADV_NOHUGEPAGE) accepted" : "madvise(MADV_NOHUGEPAGE) refused";
  }
#else
  static_cast<void>(hint_wanted);
  const std::size_t mapped = wanted;
  void* base = ::mmap(nullptr, mapped, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
  if (base == MAP_FAILED) {
    return std::nullopt;
  }
  auto* data = static_cast<std::byte*>(base);
  std::string hint = "none on this platform, base pages used";
#endif
  if (prefault == Prefault::now) {
    for (std::size_t offset = 0; offset < wanted; offset += page) {
      data[offset] = std::byte{0};
    }
  }
  std::size_t obtained = page;  // NOLINT(misc-const-correctness): the Linux branch moves it.
#if defined(__linux__) && !defined(__EMSCRIPTEN__)
  const std::optional<std::size_t> huge_kb = hugepage_kb_of(data);
  if (huge_kb && *huge_kb != 0) {
    obtained = kHuge;
    hint += std::format(", {} kB in transparent hugepages", *huge_kb);
  }
#endif
  return Arena{base, mapped, data,
               ArenaReport{.bytes = wanted,
                           .page_size_obtained = obtained,
                           .hint = std::move(hint),
                           .prefaulted = prefault == Prefault::now}};
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
