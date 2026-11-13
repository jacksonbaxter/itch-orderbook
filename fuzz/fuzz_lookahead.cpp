// LookaheadReader against FrameReader on arbitrary bytes, at a look-ahead the input picks, on
// a real book, as the tools run it.
//
// The input's first byte is the distance asked for, modulo 72, so that 0, the distances an arm
// can stage, and the ones on either side it must refuse all come up; the second picks the arm,
// paged-flat (the default, four stages) or hash-linked (three); the rest is the stream. Each
// message the reader hands over is applied to the book it looks ahead for, as itch_alloc and
// itch_oracle apply it, so that the book's stages read a book that changes under them.
//
//   - frame for frame, the same status, offset and payload as FrameReader, the same terminal
//     frame at the end, and again when asked twice more;
//   - the inner reader's counters equal FrameReader's once the walk is over;
//   - a distance it must refuse reads with none;
//   - each stage s of each message k is asked for once, while frame k - lead(s) is being
//     handed over, where lead(s) = max(distance >> s, stages - s); a frame nearer the start
//     than lead(s) never gets stage s, and no other frame is ever asked for.
//
// The book's prefetch_stage() runs on whatever bytes the input holds, under ASan. That the
// book ends each message as it would have without the stages is Lookahead's unit test
// EveryArmEndsEveryMessageAsWithoutIt: a second book here costs half the executions, since
// a fresh book sizes the tops of all 131,072 sides.
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

#include "fuzz_check.hpp"
#include "itch/book.hpp"
#include "itch/lookahead.hpp"
#include "itch/reader.hpp"

namespace {

using itch::Frame;
using itch::FrameStatus;

constexpr std::size_t kHeaderBytes = 2;
constexpr unsigned kDistances = 72;
constexpr itch::BookCapacity kCapacity{
    .orders = 8192, .levels = 8192, .index_pages = 2, .index_bits = 16, .level_slots = 1024};

// A book that notes each stage it is asked for, with how many frames had been handed over
// then, and passes the ask on to the real one. Its log is sized before the walk: a frame
// takes at least two bytes, and each is asked for at most once a stage.
template <class Book>
struct Staged {
  static constexpr unsigned kPrefetchStages = Book::kPrefetchStages;
  struct Call {
    const std::byte* payload;
    unsigned stage;
    std::uint64_t handed;
  };

  void prefetch_stage(std::span<const std::byte> payload, unsigned stage) const noexcept {
    if (count < log.size()) {
      log[count] = {.payload = payload.data(), .stage = stage, .handed = handed};
    }
    ++count;
    book->prefetch_stage(payload, stage);
  }

  const Book* book;
  std::uint64_t handed = 0;
  mutable std::vector<Call> log;
  mutable std::size_t count = 0;
};

// How far ahead of the frame handed over stage s runs, from lookahead.hpp's comment.
unsigned lead(unsigned distance, unsigned stages, unsigned stage) {
  return std::max(distance >> stage, stages - stage);
}

void check_counters(const itch::ReaderCounters& got, const itch::ReaderCounters& want) {
  FUZZ_CHECK(got.frames == want.frames);
  FUZZ_CHECK(got.messages == want.messages);
  FUZZ_CHECK(got.unknown_type == want.unknown_type);
  FUZZ_CHECK(got.bad_length == want.bad_length);
  FUZZ_CHECK(got.zero_length == want.zero_length);
  FUZZ_CHECK(got.truncated_prefix == want.truncated_prefix);
  FUZZ_CHECK(got.truncated_payload == want.truncated_payload);
  FUZZ_CHECK(got.frames_by_type == want.frames_by_type);
}

template <class Book>
void check(std::span<const std::byte> stream, unsigned asked) {
  constexpr unsigned kStages = Book::kPrefetchStages;
  using Reader = itch::LookaheadReader<Staged<Book>>;
  Book book{kCapacity};
  Staged<Book> staged{.book = &book,
                      .log = std::vector<typename Staged<Book>::Call>(
                          ((stream.size() / kHeaderBytes) + 1) * kStages)};
  Reader ahead{stream, staged, asked};
  itch::FrameReader plain{stream};
  const bool valid = asked == 0 || (asked >= kStages && asked <= Reader::kMostAhead);
  const unsigned distance = valid ? asked : 0;
  FUZZ_CHECK(ahead.distance() == distance);

  std::vector<Frame> frames;
  Frame want = plain.next();
  for (;; want = plain.next()) {
    staged.handed = frames.size();
    const Frame got = ahead.next();
    FUZZ_CHECK(got.status == want.status);
    FUZZ_CHECK(got.offset == want.offset);
    FUZZ_CHECK(got.payload.data() == want.payload.data());
    FUZZ_CHECK(got.payload.size() == want.payload.size());
    if (itch::is_terminal(want.status)) {
      break;
    }
    frames.push_back(want);
    if (got.status == FrameStatus::message) {
      static_cast<void>(book.apply(got.payload));
    }
  }
  for (int again = 0; again < 2; ++again) {
    const Frame got = ahead.next();
    FUZZ_CHECK(got.status == want.status);
    FUZZ_CHECK(got.offset == want.offset);
  }
  check_counters(ahead.counters(), plain.counters());

  // Where each ask landed, as a frame number, from the payload it named.
  FUZZ_CHECK(staged.count <= staged.log.size());
  std::vector<std::array<unsigned, kStages>> asks(frames.size());
  for (const auto& call : std::span{staged.log}.first(staged.count)) {
    FUZZ_CHECK(call.payload >= stream.data() + kHeaderBytes);
    const auto offset = static_cast<std::size_t>(call.payload - stream.data()) - kHeaderBytes;
    const auto at = std::ranges::lower_bound(frames, offset, {}, &Frame::offset);
    FUZZ_CHECK(at != frames.end() && at->offset == offset && !at->payload.empty());
    FUZZ_CHECK(call.stage < kStages);
    const auto k = static_cast<std::size_t>(at - frames.begin());
    FUZZ_CHECK(k == call.handed + lead(distance, kStages, call.stage));
    ++asks[k][call.stage];
  }
  for (std::size_t k = 0; k < frames.size(); ++k) {
    for (unsigned s = 0; s < kStages; ++s) {
      const bool due = distance != 0 && frames[k].status == FrameStatus::message &&
                       k >= lead(distance, kStages, s);
      FUZZ_CHECK(asks[k][s] == (due ? 1U : 0U));
    }
  }
}

}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  if (size < 2) {
    return 0;
  }
  const auto input = std::as_bytes(std::span{data, size});
  const unsigned asked = static_cast<unsigned>(data[0]) % kDistances;
  const std::span<const std::byte> stream = input.subspan(2);
  if ((static_cast<unsigned>(data[1]) & 1U) == 0) {
    check<itch::FastBook<>>(stream, asked);
  } else {
    check<itch::FastBook<itch::NoWriteObserver, itch::HashIndex, itch::LinkedLevels>>(stream,
                                                                                      asked);
  }
  return 0;
}
