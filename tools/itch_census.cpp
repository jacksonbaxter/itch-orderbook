// Walks a BinaryFILE ITCH 5.0 file and prints total and per-type message counts.
//
// Exit codes: 0 when the walk reached the end of the file cleanly and the last
// message is System Event 'C', 1 when it did not, 2 when the file could not be read.
// A truncated download is a well-formed prefix of a good file, so a clean walk alone
// proves nothing: the last message is what shows the file is complete.
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cctype>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <optional>
#include <print>
#include <span>
#include <variant>

#include "itch/messages.hpp"
#include "itch/reader.hpp"

namespace {

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

void print_type_line(unsigned type, std::uint64_t count) {
  if (std::isprint(static_cast<int>(type)) != 0 && type != ' ') {
    std::println("type {:c} {}", static_cast<char>(type), count);
  } else {
    std::println("type 0x{:02x} {}", type, count);
  }
}

int run(const char* path) {
  const auto file = MappedFile::open(path);
  if (!file) {
    std::println(stderr, "itch_census: cannot read {}", path);
    return 2;
  }

  itch::FrameReader reader{file->bytes()};
  std::optional<itch::SystemEvent> last_event;
  std::optional<std::size_t> last_offset;
  std::uint64_t decode_failures = 0;
  itch::Frame frame = reader.next();
  for (; !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == itch::FrameStatus::zero_length) {
      continue;
    }
    last_offset = frame.offset;
    last_event.reset();
    if (frame.status != itch::FrameStatus::message) {
      continue;
    }
    const auto decoded = itch::decode(frame.payload);
    if (!decoded) {
      ++decode_failures;
    } else if (const auto* event = std::get_if<itch::SystemEvent>(&*decoded)) {
      last_event = *event;
    }
  }

  const itch::ReaderCounters& counters = reader.counters();
  std::println("bytes {}", file->bytes().size());
  std::println("consumed {}", reader.consumed());
  std::println("messages {}", counters.frames);
  for (unsigned type = 0; type < counters.frames_by_type.size(); ++type) {
    if (counters.frames_by_type[type] != 0) {
      print_type_line(type, counters.frames_by_type[type]);
    }
  }
  std::println("unknown_type {}", counters.unknown_type);
  std::println("bad_length {}", counters.bad_length);
  std::println("zero_length {}", counters.zero_length);
  std::println("decode_failures {}", decode_failures);
  std::println("end {}", itch::to_string(frame.status));
  if (last_event) {
    std::println("last_message S {:c} locate={} tracking={} timestamp_ns={} offset={}",
                 last_event->event_code, last_event->header.stock_locate,
                 last_event->header.tracking_number, last_event->header.timestamp_ns,
                 last_offset.value_or(0));
  }

  if (frame.status != itch::FrameStatus::end_of_input) {
    std::println(stderr, "itch_census: FAIL framing: {} at offset {}",
                 itch::to_string(frame.status), frame.offset);
    return 1;
  }
  if (decode_failures != 0) {
    std::println(stderr, "itch_census: FAIL {} messages did not decode", decode_failures);
    return 1;
  }
  if (!last_event || last_event->event_code != 'C') {
    std::println(stderr, "itch_census: FAIL last message is not S/C");
    return 1;
  }
  return 0;
}

}  // namespace

int main(int argc, const char* const* argv) {
  if (argc != 2) {
    static_cast<void>(std::fputs("usage: itch_census <file>\n", stderr));
    return 2;
  }
  try {
    return run(argv[1]);
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
