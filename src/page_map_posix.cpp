// PageMapping where the OS has no hugepage call this project knows: base pages, through
// mmap, and every other request refused. Emscripten builds this one.
#include <sys/mman.h>
#include <unistd.h>

#include <cerrno>
#include <cstddef>
#include <format>
#include <system_error>

#include "itch/page_map.hpp"

namespace itch {

PageMapping::PageMapping(std::size_t bytes, PageRequest request) {
  report_.requested = request;
  report_.base_page = static_cast<std::size_t>(::getpagesize());
  if (request != PageRequest::base) {
    report_.how = "this OS has no hugepage call here, so nothing was mapped";
    return;
  }
  const std::size_t page = report_.base_page;
  const std::size_t wanted = ((bytes == 0 ? 1 : bytes) + page - 1) / page * page;
  void* at = ::mmap(nullptr, wanted, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (at == MAP_FAILED) {
    const int error = errno;
    report_.how =
        std::format("mmap refused: errno {} ({})", error, std::system_category().message(error));
    return;
  }
  base_ = at;
  reserved_ = wanted;
  data_ = static_cast<std::byte*>(at);
  for (std::size_t offset = 0; offset < wanted; offset += page) {
    data_[offset] = std::byte{0};
  }
  report_.mapped = true;
  report_.bytes = wanted;
  report_.prefaulted = true;
  report_.how = "mmap, base pages, not verified";
}

}  // namespace itch
