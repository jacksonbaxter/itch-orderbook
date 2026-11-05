// A frame reader that runs ahead of the book, so that the loads a message will make are
// started before the book applies it (E8 in simd.md's experiments).
//
//     LookaheadReader<Book> reader{bytes, book, distance};
//     for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) ...
//
// It hands over the same frames as FrameReader, in the same order, and the same terminal
// frame at the end. Before it hands over a frame it calls book.prefetch_stage() on the
// frames ahead of it: stage 0 for the frame `distance` ahead, and each later stage for a
// frame nearer, at max(stages - s, distance >> s) ahead, so that each stage finds what the
// stage before asked for already on its way, and each frame gets every stage once. The
// book's loads form a chain, each address read from the line before, so a stage that ran
// on the same frame as the one before it would wait for that line here instead of in
// apply().
//
// A distance of 0 is the plain reader. Any other must be between Book::kPrefetchStages
// and kMostAhead, which valid_distance() says. The frames ahead sit in a ring inside the
// reader, so it allocates nothing, and prefetch_stage() only reads the book, so the book
// ends each message as it would have without it.
//
// counters() are the inner reader's, which runs up to `distance` frames ahead until the
// input ends; read them once the walk is over.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

#include "itch/reader.hpp"

namespace itch {

template <class Book>
class LookaheadReader {
 public:
  static constexpr unsigned kStages = Book::kPrefetchStages;
  static constexpr unsigned kMostAhead = 64;

  [[nodiscard]] static constexpr bool valid_distance(unsigned distance) noexcept {
    return distance == 0 || (distance >= kStages && distance <= kMostAhead);
  }

  // How far ahead of the frame handed over each stage runs.
  [[nodiscard]] static constexpr unsigned ahead(unsigned distance, unsigned stage) noexcept {
    const unsigned halved = distance >> stage;
    const unsigned spaced = kStages - stage;
    return halved > spaced ? halved : spaced;
  }

  // distance must pass valid_distance(). One that does not reads with none.
  LookaheadReader(std::span<const std::byte> bytes, const Book& book, unsigned distance) noexcept
      : reader_{bytes}, book_{&book}, distance_{valid_distance(distance) ? distance : 0U} {
    for (unsigned stage = 0; stage < kStages; ++stage) {
      ahead_[stage] = ahead(distance_, stage);
    }
  }

  [[nodiscard]] Frame next() noexcept {
    if (distance_ == 0) {
      return reader_.next();
    }
    while (!ended_ && read_ - handed_ <= distance_) {
      const Frame frame = reader_.next();
      ring_[read_ & kRingMask] = frame;
      ++read_;
      ended_ = is_terminal(frame.status);
    }
    for (unsigned stage = 0; stage < kStages; ++stage) {
      const std::uint64_t at = handed_ + ahead_[stage];
      if (at < read_ && ring_[at & kRingMask].status == FrameStatus::message) {
        book_->prefetch_stage(ring_[at & kRingMask].payload, stage);
      }
    }
    const Frame frame = ring_[handed_ & kRingMask];
    if (!is_terminal(frame.status)) {
      ++handed_;
    }
    return frame;
  }

  [[nodiscard]] unsigned distance() const noexcept { return distance_; }
  [[nodiscard]] const ReaderCounters& counters() const noexcept { return reader_.counters(); }

 private:
  static constexpr std::size_t kRing = 128;  // A power of two above kMostAhead.
  static constexpr std::uint64_t kRingMask = kRing - 1;
  static_assert(kRing > kMostAhead);

  FrameReader reader_;
  const Book* book_;
  unsigned distance_;
  std::array<unsigned, kStages> ahead_{};
  std::array<Frame, kRing> ring_{};
  std::uint64_t read_ = 0;    // Frames taken from the inner reader.
  std::uint64_t handed_ = 0;  // Frames handed over.
  bool ended_ = false;
};

}  // namespace itch
