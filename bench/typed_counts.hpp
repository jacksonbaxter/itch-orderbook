// Hardware counts by message type, for T-PERF-1 and T-PERF-2: an untimed pass that reads
// every event once at each frame boundary, and charges what passed since the last read to
// the frame just walked. Nothing platform-specific is in here, so a test drives it with a
// fake group.
//
// The spans tile the pass. One message's span runs from the read after the previous frame
// to the read after its own, so it holds the frame walk, apply(), the read itself and the
// tally's addition, and nothing falls between two spans. Summed over every type, the spans
// are the last reading less the first, exactly, and the pass checks that it is so. A span
// with nothing in it still holds one read and one addition: empty_spans() measures that,
// so that a figure can be shown with the cost of counting it beside it, never quietly less
// that cost.
//
// The counts are exact for each message, with no sampling, and say nothing about time: the
// reads are fenced and serialise the core, so no timing comes from this pass.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <utility>
#include <vector>

#include "itch/reader.hpp"
#include "itch/wire.hpp"
#include "replay.hpp"

namespace itch::bench {

// A span is charged to its message's type byte. No ITCH type is zero, so slot zero takes
// the frames the reader walks past without decoding, and the empty spans of a calibration.
inline constexpr std::size_t kTypeSlots = 256;
inline constexpr std::uint8_t kNoMessageSlot = 0;

// Each event's count summed over the spans of each slot. Sized once, so adding never
// allocates. The rows are the slots, so one span touches one row of a few words.
class TypeTally {
 public:
  explicit TypeTally(std::size_t events)
      : events_{events}, sums_(kTypeSlots * events, 0) {}

  // One span: each event's reading now less its reading at the span's start.
  void add(std::uint8_t slot, std::span<const std::uint64_t> now,
           std::span<const std::uint64_t> start) noexcept {
    std::uint64_t* row = sums_.data() + (std::size_t{slot} * events_);
    for (std::size_t event = 0; event < events_; ++event) {
      row[event] += now[event] - start[event];
    }
    ++spans_[slot];
  }

  [[nodiscard]] std::size_t events() const noexcept { return events_; }
  [[nodiscard]] std::uint64_t spans(std::uint8_t slot) const noexcept { return spans_[slot]; }
  [[nodiscard]] std::uint64_t sum(std::uint8_t slot, std::size_t event) const noexcept {
    return sums_[(std::size_t{slot} * events_) + event];
  }
  // One event over every slot.
  [[nodiscard]] std::uint64_t total(std::size_t event) const noexcept {
    std::uint64_t out = 0;
    for (std::size_t slot = 0; slot < kTypeSlots; ++slot) {
      out += sums_[(slot * events_) + event];
    }
    return out;
  }

 private:
  std::size_t events_;
  std::vector<std::uint64_t> sums_;
  std::array<std::uint64_t, kTypeSlots> spans_{};
};

struct TypedPass {
  std::vector<std::uint64_t> first;  // Each event's reading before the first frame.
  std::vector<std::uint64_t> last;   // And after the last.
  std::uint64_t frames = 0;
  std::uint64_t missed = 0;    // Reads that gave no value. One makes the attribution unusable.
  std::uint64_t rejected = 0;  // Messages apply() refused. A clean day has none.

  // The whole pass by rdpmc, which the spans tile.
  [[nodiscard]] std::uint64_t whole(std::size_t event) const noexcept {
    return last[event] - first[event];
  }
};

// True if, for every event, the spans add up to the whole pass. They must by construction:
// a span lost, counted twice or started from the wrong reading breaks it.
[[nodiscard]] inline bool spans_tile(const TypedPass& pass, const TypeTally& tally) noexcept {
  for (std::size_t event = 0; event < tally.events(); ++event) {
    if (tally.total(event) != pass.whole(event)) {
      return false;
    }
  }
  return true;
}

// The pass. Group gives event_count() and read_fast(span). A design whose heap ages takes
// its steps inside the spans, so it is not offered here.
template <class Group, class Book>
  requires(!AgesHeap<Book>)
[[nodiscard]] TypedPass typed_counts_pass(std::span<const std::byte> input, Book& book,
                                          Group& group, TypeTally& tally) {
  const std::size_t events = group.event_count();
  TypedPass pass{.first = std::vector<std::uint64_t>(events),
                 .last = std::vector<std::uint64_t>(events),
                 .frames = 0,
                 .missed = 0,
                 .rejected = 0};
  std::vector<std::uint64_t> now(events);
  std::vector<std::uint64_t> start(events);
  pass.missed += group.read_fast(start) ? 0U : 1U;
  pass.first = start;
  FrameReader reader{input};
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    std::uint8_t slot = kNoMessageSlot;
    if (frame.status == FrameStatus::message) {
      slot = static_cast<std::uint8_t>(read_char(frame.payload.data()));
      pass.rejected += is_rejection(book.apply(frame.payload)) ? 1U : 0U;
    }
    pass.missed += group.read_fast(now) ? 0U : 1U;
    tally.add(slot, now, start);
    std::swap(now, start);
    ++pass.frames;
  }
  pass.last = std::move(start);
  return pass;
}

// What counting costs: `spans` spans with nothing in them, each one read and one addition,
// as a message's span has, charged to slot zero of their own tally.
struct EmptySpans {
  std::uint64_t spans = 0;
  std::uint64_t missed = 0;
  std::vector<std::uint64_t> sums;  // Each event over every empty span.
};

template <class Group>
[[nodiscard]] EmptySpans empty_spans(Group& group, std::uint64_t spans) {
  const std::size_t events = group.event_count();
  TypeTally tally{events};
  std::vector<std::uint64_t> now(events);
  std::vector<std::uint64_t> start(events);
  EmptySpans out{.spans = spans, .missed = 0, .sums = std::vector<std::uint64_t>(events)};
  out.missed += group.read_fast(start) ? 0U : 1U;
  for (std::uint64_t i = 0; i < spans; ++i) {
    out.missed += group.read_fast(now) ? 0U : 1U;
    tally.add(kNoMessageSlot, now, start);
    std::swap(now, start);
  }
  for (std::size_t event = 0; event < events; ++event) {
    out.sums[event] = tally.sum(kNoMessageSlot, event);
  }
  return out;
}

}  // namespace itch::bench
