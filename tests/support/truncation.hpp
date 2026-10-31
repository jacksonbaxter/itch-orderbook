// The check behind "truncate a valid stream at every byte offset", shared by the
// plain and the guard-page tests. It uses no test framework, so that it can run in a
// forked child.
#pragma once

#include <algorithm>
#include <cstddef>
#include <iostream>
#include <span>
#include <vector>

#include "itch/reader.hpp"
#include "support/encode.hpp"

namespace itch::testing {

// The offset of each frame in a well-formed stream, plus its end, walked here from
// the bytes without using the reader.
[[nodiscard]] inline std::vector<std::size_t> frame_boundaries(std::span<const std::byte> stream) {
  std::vector<std::size_t> boundaries{0};
  std::size_t at = 0;
  while (at < stream.size()) {
    const std::size_t length = (std::to_integer<std::size_t>(stream[at]) << 8U) |
                               std::to_integer<std::size_t>(stream[at + 1]);
    at += 2 + length;
    boundaries.push_back(at);
  }
  return boundaries;
}

// Walks one prefix of a stream. Returns how many expectations failed. Every whole
// frame in the prefix must come back intact, and the walk must end in the typed state
// that fits where the cut fell. Stops at the first surprise, because after one the
// reader's position cannot be trusted.
[[nodiscard]] inline int check_prefix(std::span<const std::byte> prefix,
                                      std::span<const std::byte> whole,
                                      std::span<const std::size_t> boundaries) {
  const auto report = [&](const char* what, FrameStatus status) {
    std::cerr << "cut at " << prefix.size() << ": " << what << ", status " << to_string(status)
              << "\n";
    return 1;
  };

  FrameReader reader{prefix};
  std::size_t expected = 0;  // Index into boundaries of the next frame's start.
  while (expected + 1 < boundaries.size() && boundaries[expected + 1] <= prefix.size()) {
    const Frame frame = reader.next();
    if (frame.status != FrameStatus::message) {
      return report("a whole frame did not come back as a message", frame.status);
    }
    const std::size_t start = boundaries[expected] + FrameReader::kLengthPrefix;
    const auto wanted = whole.subspan(start, boundaries[expected + 1] - start);
    if (frame.offset != boundaries[expected] || !std::ranges::equal(frame.payload, wanted)) {
      return report("a frame came back with the wrong bytes", frame.status);
    }
    if (!round_trips(frame.payload)) {
      return report("a whole frame did not round-trip", frame.status);
    }
    ++expected;
  }

  const std::size_t tail = prefix.size() - boundaries[expected];
  FrameStatus wanted_end = FrameStatus::truncated_payload;
  if (tail == 0) {
    wanted_end = FrameStatus::end_of_input;
  } else if (tail == 1) {
    wanted_end = FrameStatus::truncated_prefix;
  }
  const Frame end = reader.next();
  if (end.status != wanted_end) {
    return report("wrong end state", end.status);
  }
  if (!end.payload.empty() || end.offset != boundaries[expected] ||
      reader.consumed() != boundaries[expected]) {
    return report("the end state does not point at the cut frame", end.status);
  }
  const ReaderCounters& counters = reader.counters();
  const bool counted_right =
      counters.messages == expected && counters.frames == expected &&
      counters.truncated_prefix == (wanted_end == FrameStatus::truncated_prefix ? 1U : 0U) &&
      counters.truncated_payload == (wanted_end == FrameStatus::truncated_payload ? 1U : 0U);
  if (!counted_right) {
    return report("wrong counters", end.status);
  }
  if (reader.next().status != wanted_end || reader.consumed() != boundaries[expected]) {
    return report("the end state did not stay", end.status);
  }
  return 0;
}

}  // namespace itch::testing
