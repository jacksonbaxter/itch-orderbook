// A read-only mapping of a whole file, for the tools that walk a trading day.
#pragma once

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cstddef>
#include <optional>
#include <span>

namespace itch::tools {

// A read-only mapping of a whole file. An empty file maps to an empty span.
class MappedFile {
 public:
  [[nodiscard]] static std::optional<MappedFile> open(const char* path) {
    const int fd = ::open(path, O_RDONLY);  // NOLINT(cppcoreguidelines-pro-type-vararg)
    if (fd < 0) {
      return std::nullopt;
    }
    struct stat info{};
    if (::fstat(fd, &info) != 0) {
      ::close(fd);
      return std::nullopt;
    }
    const auto size = static_cast<std::size_t>(info.st_size);
    void* base = nullptr;
    if (size != 0) {
      base = ::mmap(nullptr, size, PROT_READ, MAP_PRIVATE, fd, 0);
      if (base == MAP_FAILED) {
        ::close(fd);
        return std::nullopt;
      }
      ::posix_madvise(base, size, POSIX_MADV_SEQUENTIAL);
    }
    ::close(fd);
    return MappedFile{base, size};
  }

  MappedFile(const MappedFile&) = delete;
  MappedFile& operator=(const MappedFile&) = delete;
  MappedFile(MappedFile&& other) noexcept : base_{other.base_}, size_{other.size_} {
    other.base_ = nullptr;
    other.size_ = 0;
  }
  MappedFile& operator=(MappedFile&&) = delete;
  ~MappedFile() {
    if (base_ != nullptr) {
      ::munmap(base_, size_);
    }
  }

  [[nodiscard]] std::span<const std::byte> bytes() const noexcept {
    return {static_cast<const std::byte*>(base_), size_};
  }

 private:
  MappedFile(void* base, std::size_t size) : base_{base}, size_{size} {}
  void* base_;
  std::size_t size_;
};

}  // namespace itch::tools
