// PageMapping on macOS. The VM has one page size, vm_page_size, 16 KiB on Apple silicon, and
// a superpage flag that mmap takes in place of a file descriptor. The SDK's
// mach/vm_statistics.h calls superpages "not supported on any current hardware", and this
// M4 Max refuses VM_FLAGS_SUPERPAGE_SIZE_2MB with EINVAL from mmap and mach_vm_allocate alike
// (LC-57). macOS has no call that reports the page size behind a mapping, so a superpage
// mapping it did accept would be recorded as unverified.
#include <mach/vm_statistics.h>
#include <sys/mman.h>
#include <unistd.h>

#include <cerrno>
#include <cstddef>
#include <format>
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
  if (request == PageRequest::transparent) {
    report_.how = "transparent hugepages are Linux only, so nothing was mapped";
    return;
  }
  if (request == PageRequest::huge_if_offered || request == PageRequest::explicit_huge) {
    const std::size_t huge = round_up(wanted, kHugePage);
    void* at = ::mmap(nullptr, huge, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON,
                      VM_FLAGS_SUPERPAGE_SIZE_2MB, 0);
    if (at != MAP_FAILED) {
      base_ = at;
      reserved_ = huge;
      data_ = static_cast<std::byte*>(at);
      touch(data_, huge, kHugePage);
      report_.mapped = true;
      report_.bytes = huge;
      report_.prefaulted = true;
      report_.how =
          "mmap with VM_FLAGS_SUPERPAGE_SIZE_2MB accepted; macOS reports no page size for a "
          "mapping, so it is not verified";
      return;
    }
    report_.how = refusal("mmap with VM_FLAGS_SUPERPAGE_SIZE_2MB", errno);
    if (request == PageRequest::explicit_huge) {
      report_.how += "; explicit takes no fallback, so nothing was mapped";
      return;
    }
    report_.how += "; so base pages: ";
  }
  void* at = ::mmap(nullptr, wanted, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
  if (at == MAP_FAILED) {
    report_.how += refusal("mmap", errno);
    return;
  }
  base_ = at;
  reserved_ = wanted;
  data_ = static_cast<std::byte*>(at);
  touch(data_, wanted, report_.base_page);
  report_.mapped = true;
  report_.bytes = wanted;
  report_.verified = true;
  report_.prefaulted = true;
  report_.how +=
      "mmap with no superpage flag, which macOS backs with vm_page_size pages, its only "
      "other size";
}

}  // namespace itch
