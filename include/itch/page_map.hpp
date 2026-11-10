// Anonymous memory for a pool, mapped once, with the page size asked for and the page size
// obtained both on the record, and every page touched before the mapping is handed over.
//
//     PageMapping pages{bytes, PageRequest::huge_if_offered};
//
// What each request asks of the kernel:
//   base             base pages only. On Linux, madvise(MADV_NOHUGEPAGE), so that
//                    transparent hugepages set to always cannot back it either.
//   huge_if_offered  2 MiB pages where this OS offers them, base pages where it does not,
//                    the refusal recorded. On Linux, transparent hugepages through
//                    madvise(MADV_HUGEPAGE). On macOS, mmap with
//                    VM_FLAGS_SUPERPAGE_SIZE_2MB, which the SDK calls unsupported on
//                    current hardware and which this M4 Max refuses with EINVAL (LC-57).
//   transparent      Linux only: transparent hugepages through madvise, and no fallback.
//   explicit_huge    2 MiB pages from the kernel's reserved pool, MAP_HUGETLB on Linux and
//                    the superpage flag on macOS, and no fallback: a refusal leaves the
//                    mapping empty.
//
// The page size obtained is what the kernel says backs the mapping after it was touched,
// never what was asked for: on Linux the AnonHugePages and KernelPageSize lines of the
// mapping in /proc/self/smaps. macOS has no call that reports it, so a superpage mapping
// it accepted would be recorded as unverified. A mapping that failed has no data and says
// why, and holds nothing.
//
// The code for each OS is in its own file under src/, chosen by CMake, since parser code
// holds no conditional compilation (scripts/gates.py).
#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace itch {

enum class PageRequest : std::uint8_t { base, huge_if_offered, transparent, explicit_huge };

inline constexpr std::size_t kHugePage = std::size_t{2} << 20U;

[[nodiscard]] constexpr std::string_view to_string(PageRequest request) noexcept {
  switch (request) {
    case PageRequest::base: return "base";
    case PageRequest::huge_if_offered: return "huge-if-offered";
    case PageRequest::transparent: return "transparent";
    case PageRequest::explicit_huge: return "explicit";
  }
  return "unnamed";
}

[[nodiscard]] constexpr std::optional<PageRequest> parse_page_request(
    std::string_view name) noexcept {
  for (const PageRequest request : {PageRequest::base, PageRequest::huge_if_offered,
                                    PageRequest::transparent, PageRequest::explicit_huge}) {
    if (name == to_string(request)) {
      return request;
    }
  }
  return std::nullopt;
}

struct PageReport {
  PageRequest requested = PageRequest::base;
  bool mapped = false;
  std::size_t bytes = 0;       // Handed to the caller, a whole number of pages.
  std::size_t base_page = 0;   // The OS's base page.
  std::size_t huge_bytes = 0;  // Of `bytes`, what the kernel says 2 MiB pages back.
  bool verified = false;       // The kernel said what backs the mapping.
  bool prefaulted = false;
  std::string how;  // Every call made and what it returned, refusals with their errno.

  // The one page size that backs all of it, or 0 when it is mixed or not known.
  [[nodiscard]] std::size_t page_bytes() const noexcept {
    if (!mapped || !verified) {
      return 0;
    }
    if (huge_bytes == bytes) {
      return kHugePage;
    }
    return huge_bytes == 0 ? base_page : 0;
  }

  [[nodiscard]] std::string describe() const;
};

class PageMapping {
 public:
  PageMapping(std::size_t bytes, PageRequest request);
  PageMapping(const PageMapping&) = delete;
  PageMapping& operator=(const PageMapping&) = delete;
  PageMapping(PageMapping&& other) noexcept;
  PageMapping& operator=(PageMapping&&) = delete;
  ~PageMapping();

  // Null when the mapping failed.
  [[nodiscard]] std::byte* data() const noexcept { return data_; }
  [[nodiscard]] std::size_t size() const noexcept { return report_.mapped ? report_.bytes : 0; }
  [[nodiscard]] const PageReport& report() const noexcept { return report_; }

 private:
  void* base_ = nullptr;  // What munmap takes back: the whole reservation.
  std::size_t reserved_ = 0;
  std::byte* data_ = nullptr;
  PageReport report_;
};

}  // namespace itch
