// PageMapping on Linux. Transparent hugepages through madvise(MADV_HUGEPAGE) on a range
// aligned to 2 MiB, or explicit 2 MiB pages from the reserved pool through MAP_HUGETLB, or
// base pages with madvise(MADV_NOHUGEPAGE). What backs the mapping is read back from its
// entry in /proc/self/smaps once every page has been touched, and the host's
// transparent_hugepage settings go on the record beside it (MM-69).
#include <sys/mman.h>
#include <unistd.h>

#include <bit>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <format>
#include <fstream>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>

#include "itch/page_map.hpp"

namespace itch {

namespace {

[[nodiscard]] std::size_t round_up(std::size_t bytes, std::size_t page) noexcept {
  return (bytes + page - 1) / page * page;
}

[[nodiscard]] std::string refusal(std::string_view call, int error) {
  return std::format("{} refused: errno {} ({})", call, error,
                     std::system_category().message(error));
}

[[nodiscard]] std::string read_text(const char* path) {
  std::ifstream in{path};
  std::stringstream text;
  text << in.rdbuf();
  return text.str();
}

[[nodiscard]] std::string first_line(const char* path) {
  const std::string text = read_text(path);
  const std::size_t end = text.find('\n');
  return text.empty() ? std::string{"unreadable"} : text.substr(0, end);
}

// A field of the smaps entry that starts at `start`, in kB.
[[nodiscard]] std::optional<std::size_t> smaps_kb(std::string_view smaps, std::uintptr_t start,
                                                  std::string_view field) {
  const std::string head = std::format("{:x}-", start);
  const std::size_t at = smaps.starts_with(head) ? 0 : smaps.find("\n" + head);
  if (at == std::string_view::npos) {
    return std::nullopt;
  }
  const std::size_t found = smaps.find(field, at);
  if (found == std::string_view::npos) {
    return std::nullopt;
  }
  std::size_t value = 0;
  bool digits = false;
  for (std::size_t i = found + field.size(); i < smaps.size() && smaps[i] != 'k'; ++i) {
    if (smaps[i] >= '0' && smaps[i] <= '9') {
      value = (value * 10) + static_cast<std::size_t>(smaps[i] - '0');
      digits = true;
    }
  }
  return digits ? std::optional{value} : std::nullopt;
}

void touch(std::byte* data, std::size_t bytes, std::size_t page) noexcept {
  for (std::size_t offset = 0; offset < bytes; offset += page) {
    data[offset] = std::byte{0};
  }
}

}  // namespace

PageMapping::PageMapping(std::size_t bytes, PageRequest request) {
  report_.requested = request;
  report_.base_page = static_cast<std::size_t>(::getpagesize());
  const std::size_t wanted = round_up(bytes == 0 ? 1 : bytes, report_.base_page);
  const std::string settings =
      std::format("transparent_hugepage enabled {}, defrag {}",
                  first_line("/sys/kernel/mm/transparent_hugepage/enabled"),
                  first_line("/sys/kernel/mm/transparent_hugepage/defrag"));
  std::size_t page = report_.base_page;
  std::size_t length = wanted;
  if (request == PageRequest::explicit_huge) {
    length = round_up(wanted, kHugePage);
    void* at = ::mmap(nullptr, length, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB | (21 << MAP_HUGE_SHIFT), -1, 0);
    if (at == MAP_FAILED) {
      report_.how =
          std::format("{}, so nothing was mapped: explicit takes no fallback; nr_hugepages {}; {}",
                      refusal("mmap with MAP_HUGETLB and MAP_HUGE_2MB", errno),
                      first_line("/proc/sys/vm/nr_hugepages"), settings);
      return;
    }
    base_ = at;
    reserved_ = length;
    data_ = static_cast<std::byte*>(at);
    page = kHugePage;
    report_.how = "mmap with MAP_HUGETLB and MAP_HUGE_2MB accepted";
  } else {
    const bool huge = request != PageRequest::base;
    if (huge) {
      length = round_up(wanted, kHugePage);
    }
    // Aligned to 2 MiB, or transparent hugepages cannot back the first and last of it.
    reserved_ = huge ? length + kHugePage : length;
    void* at =
        ::mmap(nullptr, reserved_, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (at == MAP_FAILED) {
      report_.how = std::format("{}; {}", refusal("mmap", errno), settings);
      reserved_ = 0;
      return;
    }
    base_ = at;
    data_ = static_cast<std::byte*>(at);
    if (huge) {
      const auto address = std::bit_cast<std::uintptr_t>(at);
      data_ += (kHugePage - address % kHugePage) % kHugePage;
    }
    const int advice = huge ? MADV_HUGEPAGE : MADV_NOHUGEPAGE;
    const std::string_view name = huge ? "madvise(MADV_HUGEPAGE)" : "madvise(MADV_NOHUGEPAGE)";
    report_.how = ::madvise(data_, length, advice) == 0 ? std::format("{} accepted", name)
                                                        : refusal(name, errno);
    if (request == PageRequest::huge_if_offered) {
      report_.how = "on Linux, transparent hugepages: " + report_.how;
    }
  }
  touch(data_, length, page);
  report_.mapped = true;
  report_.bytes = length;
  report_.prefaulted = true;
  const std::string smaps = read_text("/proc/self/smaps");
  const auto start = std::bit_cast<std::uintptr_t>(data_);
  const std::optional<std::size_t> kernel_kb = smaps_kb(smaps, start, "KernelPageSize:");
  const std::optional<std::size_t> anon_huge_kb = smaps_kb(smaps, start, "AnonHugePages:");
  if (kernel_kb && *kernel_kb * 1024 == kHugePage) {
    report_.huge_bytes = length;
    report_.verified = true;
  } else if (kernel_kb && anon_huge_kb) {
    report_.huge_bytes = *anon_huge_kb * 1024;
    report_.verified = true;
  }
  report_.how += std::format("; smaps: KernelPageSize {} kB, AnonHugePages {} kB; {}",
                             kernel_kb ? std::to_string(*kernel_kb) : "unread",
                             anon_huge_kb ? std::to_string(*anon_huge_kb) : "unread", settings);
}

}  // namespace itch
