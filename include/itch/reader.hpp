// BinaryFILE frame reader over a byte span.
//
// A BinaryFILE stream is a sequence of frames: a 2-byte big-endian payload length,
// then the payload. The frame's own length is what moves the reader on, so a frame
// whose type is unknown, or whose length is wrong for its type, is skipped whole and
// the next frame starts where it should. No wire content can desynchronise the walk,
// and nothing reads a payload byte until its length has been checked against the
// bytes that remain.
//
// Real files end on System Event 'C' at physical end of file, with none of the
// zero-length frames BinaryFILE describes. A truncated file is a well-formed prefix
// of a good one, so a clean walk does not show a file is complete: its last message
// has to be checked as well.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>

#include "itch/messages.hpp"
#include "itch/wire.hpp"

namespace itch {

enum class FrameStatus : std::uint8_t {
  // Frames that were walked past. The reader can continue after each.
  message,       // A known type at its exact length: payload can be decoded.
  unknown_type,  // A type the spec does not define, skipped by its length.
  bad_length,    // A known type at the wrong length, skipped by its frame length.
  zero_length,   // BinaryFILE's end-of-session marker, 2 bytes with no payload.
  // Terminal states. next() keeps returning the same one.
  end_of_input,       // Every byte was walked, ending on a frame boundary.
  truncated_prefix,   // One stray byte remains, too few for a length prefix.
  truncated_payload,  // The length prefix asks for more bytes than remain.
};

[[nodiscard]] constexpr bool is_terminal(FrameStatus status) noexcept {
  return status >= FrameStatus::end_of_input;
}

// A stable name for logs and tools.
[[nodiscard]] std::string_view to_string(FrameStatus status) noexcept;

struct Frame {
  FrameStatus status;
  std::size_t offset;                  // Where the length prefix starts.
  std::span<const std::byte> payload;  // Empty unless a whole frame was walked.
};

struct ReaderCounters {
  std::uint64_t frames = 0;  // Frames with a payload. Excludes zero-length frames.
  std::uint64_t messages = 0;
  std::uint64_t unknown_type = 0;
  std::uint64_t bad_length = 0;
  std::uint64_t zero_length = 0;
  std::uint64_t truncated_prefix = 0;
  std::uint64_t truncated_payload = 0;
  std::array<std::uint64_t, 256> frames_by_type{};  // Indexed by the type byte.
};

class FrameReader {
 public:
  static constexpr std::size_t kLengthPrefix = 2;

  explicit FrameReader(std::span<const std::byte> bytes) noexcept : bytes_{bytes} {}

  [[nodiscard]] Frame next() noexcept {
    const std::size_t remaining = bytes_.size() - offset_;
    if (remaining == 0) {
      return {.status = FrameStatus::end_of_input, .offset = offset_, .payload = {}};
    }
    if (remaining < kLengthPrefix) {
      counters_.truncated_prefix = 1;
      return {.status = FrameStatus::truncated_prefix, .offset = offset_, .payload = {}};
    }
    const std::size_t length = read_be<std::uint16_t>(bytes_.data() + offset_);
    if (length > remaining - kLengthPrefix) {
      counters_.truncated_payload = 1;
      return {.status = FrameStatus::truncated_payload, .offset = offset_, .payload = {}};
    }
    const Frame frame{.status = classify(length),
                      .offset = offset_,
                      .payload = bytes_.subspan(offset_ + kLengthPrefix, length)};
    offset_ += kLengthPrefix + length;
    return frame;
  }

  // Bytes of whole frames walked so far. A terminal state leaves this at the start
  // of whatever could not be walked, so size minus consumed is the unwalked tail.
  [[nodiscard]] std::size_t consumed() const noexcept { return offset_; }
  [[nodiscard]] const ReaderCounters& counters() const noexcept { return counters_; }

 private:
  [[nodiscard]] FrameStatus classify(std::size_t length) noexcept {
    if (length == 0) {
      ++counters_.zero_length;
      return FrameStatus::zero_length;
    }
    ++counters_.frames;
    const std::byte first = bytes_[offset_ + kLengthPrefix];
    ++counters_.frames_by_type[static_cast<unsigned char>(first)];
    const std::size_t expected = message_length(static_cast<char>(first));
    if (expected == 0) {
      ++counters_.unknown_type;
      return FrameStatus::unknown_type;
    }
    if (expected != length) {
      ++counters_.bad_length;
      return FrameStatus::bad_length;
    }
    ++counters_.messages;
    return FrameStatus::message;
  }

  std::span<const std::byte> bytes_;
  std::size_t offset_ = 0;
  ReaderCounters counters_;
};

}  // namespace itch
