// How a whole file's BinaryFILE framing ends, walked apart from any replay, for the tools whose
// replay stops at the first terminal status without asking which: a file cut in the middle of
// a frame would otherwise end like a complete day. No benchmark includes this header.
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <print>
#include <span>
#include <string_view>

#include "itch/reader.hpp"

namespace itch::tools {

struct Framing {
  FrameStatus end = FrameStatus::end_of_input;
  std::size_t offset = 0;  // Where the walk stopped.
  std::uint64_t unknown_type = 0;
  std::uint64_t bad_length = 0;
};

[[nodiscard]] inline Framing walk_framing(std::span<const std::byte> bytes) noexcept {
  FrameReader reader{bytes};
  Frame frame = reader.next();
  while (!is_terminal(frame.status)) {
    frame = reader.next();
  }
  const ReaderCounters& counters = reader.counters();
  return {.end = frame.status,
          .offset = frame.offset,
          .unknown_type = counters.unknown_type,
          .bad_length = counters.bad_length};
}

// True if the file's framing ends on end_of_input. Otherwise says so on stderr, with the status,
// where the walk stopped and the frames a replay would have skipped by their length.
[[nodiscard]] inline bool framing_ends_cleanly(std::string_view tool,
                                               std::span<const std::byte> bytes) {
  const Framing framing = walk_framing(bytes);
  if (framing.end == FrameStatus::end_of_input) {
    return true;
  }
  std::println(stderr,
               "{}: FAIL framing: {} at offset {}; skipped frames {} (unknown_type {}, "
               "bad_length {})",
               tool, to_string(framing.end), framing.offset,
               framing.unknown_type + framing.bad_length, framing.unknown_type, framing.bad_length);
  return false;
}

}  // namespace itch::tools
