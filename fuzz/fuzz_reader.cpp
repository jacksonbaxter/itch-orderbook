// Arbitrary bytes through the frame reader and the full decode.
//
// A second walk of the same bytes, written here without the reader, has to agree
// with it on every frame. Between them they must account for every input byte, and
// each frame the reader calls a message has to decode and re-encode to itself.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

#include "fuzz_check.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "support/encode.hpp"

namespace {

using itch::Frame;
using itch::FrameStatus;

void check_message(const Frame& frame) {
  const auto decoded = itch::decode(frame.payload);
  FUZZ_CHECK(decoded.has_value());
  const std::vector<std::byte> again = itch::testing::encode(*decoded);
  FUZZ_CHECK(std::ranges::equal(again, frame.payload));
}

void check_frame_status(const Frame& frame, std::size_t length) {
  if (length == 0) {
    FUZZ_CHECK(frame.status == FrameStatus::zero_length);
    return;
  }
  const auto type = static_cast<char>(frame.payload[0]);
  const std::size_t wanted = itch::message_length(type);
  if (wanted == 0) {
    FUZZ_CHECK(frame.status == FrameStatus::unknown_type);
    FUZZ_CHECK(itch::decode(frame.payload).error() == itch::DecodeError::unknown_type);
  } else if (wanted != length) {
    FUZZ_CHECK(frame.status == FrameStatus::bad_length);
    FUZZ_CHECK(itch::decode(frame.payload).error() == itch::DecodeError::bad_length);
  } else {
    FUZZ_CHECK(frame.status == FrameStatus::message);
    check_message(frame);
  }
}

}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  const auto bytes = std::as_bytes(std::span{data, size});
  itch::FrameReader reader{bytes};

  std::size_t at = 0;
  std::uint64_t walked = 0;
  std::uint64_t zero_length = 0;
  Frame frame = reader.next();
  for (; !itch::is_terminal(frame.status); frame = reader.next()) {
    FUZZ_CHECK(size - at >= 2);
    const std::size_t length = (std::size_t{data[at]} << 8U) | data[at + 1];
    FUZZ_CHECK(length <= size - at - 2);
    FUZZ_CHECK(frame.offset == at);
    FUZZ_CHECK(frame.payload.size() == length);
    FUZZ_CHECK(length == 0 || frame.payload.data() == bytes.data() + at + 2);
    check_frame_status(frame, length);
    at += 2 + length;
    FUZZ_CHECK(reader.consumed() == at);
    if (length == 0) {
      ++zero_length;
    } else {
      ++walked;
    }
  }

  // The end state must describe exactly the bytes the walk could not take.
  const std::size_t tail = size - at;
  FUZZ_CHECK(reader.consumed() == at);
  FUZZ_CHECK(frame.offset == at);
  FUZZ_CHECK(frame.payload.empty());
  if (frame.status == FrameStatus::end_of_input) {
    FUZZ_CHECK(tail == 0);
  } else if (frame.status == FrameStatus::truncated_prefix) {
    FUZZ_CHECK(tail == 1);
  } else {
    FUZZ_CHECK(frame.status == FrameStatus::truncated_payload);
    FUZZ_CHECK(tail >= 2);
    FUZZ_CHECK(((std::size_t{data[at]} << 8U) | data[at + 1]) > tail - 2);
  }

  const itch::ReaderCounters& counters = reader.counters();
  FUZZ_CHECK(counters.frames == walked);
  FUZZ_CHECK(counters.zero_length == zero_length);
  FUZZ_CHECK(counters.messages + counters.unknown_type + counters.bad_length == walked);
  std::uint64_t by_type = 0;
  for (const std::uint64_t count : counters.frames_by_type) {
    by_type += count;
  }
  FUZZ_CHECK(by_type == walked);

  // A terminal state stays, and asking again counts nothing twice.
  const Frame again = reader.next();
  FUZZ_CHECK(again.status == frame.status);
  FUZZ_CHECK(reader.consumed() == at);
  FUZZ_CHECK(reader.counters().truncated_prefix + reader.counters().truncated_payload <= 1);
  return 0;
}
