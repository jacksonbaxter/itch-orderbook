// What every OS's PageMapping shares: the record of what was obtained, and giving the
// mapping back. The constructor is in page_map_<os>.cpp.
#include "itch/page_map.hpp"

#include <sys/mman.h>

#include <cstddef>
#include <format>
#include <string>

namespace itch {

std::string PageReport::describe() const {
  std::string obtained;
  if (!mapped) {
    obtained = "nothing mapped";
  } else if (!verified) {
    obtained = std::format("{} bytes, page size not verified", bytes);
  } else if (page_bytes() == kHugePage) {
    obtained = std::format("{}-byte pages for all {} bytes", kHugePage, bytes);
  } else if (page_bytes() == base_page) {
    obtained = std::format("{}-byte base pages for all {} bytes", base_page, bytes);
  } else {
    obtained = std::format("{} of {} bytes in {}-byte pages, the rest in {}-byte base pages",
                           huge_bytes, bytes, kHugePage, base_page);
  }
  return std::format("{} requested: {}; {}; prefaulted: {}", to_string(requested), obtained, how,
                     prefaulted ? "yes" : "no");
}

PageMapping::PageMapping(PageMapping&& other) noexcept
    : base_{other.base_},
      reserved_{other.reserved_},
      data_{other.data_},
      report_{std::move(other.report_)} {
  other.base_ = nullptr;
  other.data_ = nullptr;
  other.report_.mapped = false;
}

PageMapping::~PageMapping() {
  if (base_ != nullptr) {
    ::munmap(base_, reserved_);
  }
}

}  // namespace itch
