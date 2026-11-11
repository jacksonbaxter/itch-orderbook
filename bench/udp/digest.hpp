// What a replay did to the book, in a form two paths can be compared by: every frame's
// status and bytes folded in order, the book's answer to each, and the book's canonical
// hash at a checkpoint and at the end. The file path and the UDP path each feed one, and
// a functional check passes only if the two are equal in every field.
//
// A real day's book is empty again at the close, so the end hash alone would pass a
// replay that lost the middle; the checkpoint is the book part way through.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <span>

#include "itch/book_types.hpp"
#include "itch/reader.hpp"
#include "oracle/canonical.hpp"

namespace itch::bench::udp {

inline constexpr std::size_t kApplyStatuses = 8;  // ApplyStatus's values.
static_assert(static_cast<std::size_t>(ApplyStatus::capacity_exhausted) + 1 == kApplyStatuses);
inline constexpr std::uint64_t kNoCheckpoint = std::numeric_limits<std::uint64_t>::max();

struct ReplaySummary {
  std::uint64_t frames = 0;
  std::uint64_t frame_hash = 0;  // FNV-1a over each frame's status, length and bytes.
  std::array<std::uint64_t, kApplyStatuses> statuses{};
  bool checkpoint_reached = false;
  std::uint64_t checkpoint_hash = 0;
  std::uint64_t end_hash = 0;

  friend bool operator==(const ReplaySummary&, const ReplaySummary&) = default;
};

template <class Book>
[[nodiscard]] std::uint64_t book_hash(const Book& book) {
  oracle::WordHash hash;
  oracle::emit_fast(book, hash);
  return hash.value();
}

// A mold::FrameSink that applies each frame to the book and folds it into the summary.
template <class Book>
class BookDigest {
 public:
  explicit BookDigest(Book& book, std::uint64_t checkpoint_frame = kNoCheckpoint) noexcept
      : book_{&book}, checkpoint_{checkpoint_frame} {}

  void operator()(const Frame& frame) noexcept {
    fold(static_cast<std::byte>(frame.status));
    fold(static_cast<std::byte>(frame.payload.size() >> 8U));
    fold(static_cast<std::byte>(frame.payload.size() & 0xFFU));
    for (const std::byte b : frame.payload) {
      fold(b);
    }
    ++summary_.statuses[static_cast<std::size_t>(book_->apply(frame.payload))];
    ++summary_.frames;
    if (summary_.frames == checkpoint_) {
      summary_.checkpoint_reached = true;
      summary_.checkpoint_hash = book_hash(*book_);
    }
  }

  // The summary, with the book's hash as it stands now.
  [[nodiscard]] ReplaySummary finish() const {
    ReplaySummary out = summary_;
    out.end_hash = book_hash(*book_);
    return out;
  }

 private:
  void fold(std::byte b) noexcept {
    summary_.frame_hash ^= static_cast<std::uint64_t>(b);
    summary_.frame_hash *= 0x100000001B3ULL;
  }

  Book* book_;
  std::uint64_t checkpoint_;
  ReplaySummary summary_{.frame_hash = 0xCBF29CE484222325ULL};
};

// The file path: the same slice build_packets() takes, frames first to first + limit,
// straight from the FrameReader into the sink.
template <class Sink>
void replay_frames(std::span<const std::byte> input, std::uint64_t first, std::uint64_t limit,
                   Sink& sink) {
  FrameReader reader{input};
  std::uint64_t index = 0;
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (index >= first) {
      if (index - first >= limit) {
        return;
      }
      sink(frame);
    }
    ++index;
  }
}

}  // namespace itch::bench::udp
