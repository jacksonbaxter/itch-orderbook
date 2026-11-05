// T-SPSC-3: a day through a parser thread, the SPSC ring and a book thread, against the
// same day through one thread and the same book.
//
//     replay_single()     one thread: the frame walk and apply() on each payload, the
//                         shipped single-threaded replay.
//     replay_decoded()    one thread: the frame walk, decode_book_op() and apply() on the
//                         op. The pipeline's work with no handover, so the two can be told
//                         apart.
//     replay_pipelined()  a parser thread walks the frames, decodes each message the book
//                         acts on and pushes the ops through the ring in batches; this
//                         thread pops them in batches and applies each.
//
// Each returns the same counts, so the caller can hold the three to one another, and
// book_hash() reads the book each left. A real day's book is empty again at the close, so
// each also takes the book's hash once along the way, a checkpoint: the book just after
// the checkpoint_at-th apply() that returned anything but no_book_effect, the same op in
// all three, since the decoded paths hand the book exactly the messages it acts on
// (tests/test_pipeline.cpp). The walk for that hash is left out of the span; in the
// pipeline the parser keeps filling the ring meanwhile, as it does all day. The parser
// thread is started and waiting before the span opens; the span closes when the book
// thread has applied the last op. Nothing in a span allocates (tests/test_alloc.cpp).
#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <thread>

#include "itch/book.hpp"
#include "itch/book_op.hpp"
#include "itch/reader.hpp"
#include "itch/spsc.hpp"
#include "oracle/canonical.hpp"
#include "platform/affinity.hpp"
#include "platform/timer.hpp"

namespace itch::bench {

// 2^16 ops of 32 bytes: 2 MiB, which holds about 15 ms of the day's busiest stretch. The
// parser fills a batch of 64 before it publishes, and the book thread takes up to 64 at a
// time.
inline constexpr std::size_t kPipelineCapacity = std::size_t{1} << 16U;
inline constexpr std::size_t kPipelineBatch = 64;
inline constexpr std::size_t kStatuses = 8;  // ApplyStatus's values.

using OpRing = SpscRing<BookOp>;
inline constexpr std::uint64_t kNoCheckpoint = std::numeric_limits<std::uint64_t>::max();

// The whole book as one word, the oracle's canonical walk through its order-sensitive mix.
template <class Book>
[[nodiscard]] std::uint64_t book_hash(const Book& book) {
  oracle::WordHash hash;
  oracle::emit_fast(book, hash);
  return hash.value();
}

struct ReplayCounts {
  std::uint64_t messages = 0;  // Whole frames walked.
  std::uint64_t ops = 0;       // Messages that reached the book: every message in replay_single().
  std::array<std::uint64_t, kStatuses> by_status{};  // What apply() returned, by ApplyStatus.
  std::uint64_t ticks = 0;                           // The span.
  // replay_pipelined() only: how often the parser found the ring full, and the book thread
  // found it empty, each counted once per failed try.
  std::uint64_t full_waits = 0;
  std::uint64_t empty_waits = 0;
  PinOutcome parser_pin;  // replay_pipelined() only.
  bool checkpoint_reached = false;
  std::uint64_t checkpoint_hash = 0;    // book_hash() at the checkpoint, once reached.
  std::uint64_t checkpoint_orders = 0;  // The book's live orders and levels there.
  std::uint64_t checkpoint_levels = 0;
  std::uint64_t checkpoint_ticks = 0;  // Its walk, left out of `ticks`.

  [[nodiscard]] std::uint64_t of(ApplyStatus status) const noexcept {
    return by_status[static_cast<std::size_t>(status)];
  }
};

// Counts each status and takes the checkpoint's hash when its op comes.
class Tally {
 public:
  explicit Tally(std::uint64_t checkpoint_at) noexcept : at_{checkpoint_at} {}

  template <class Book>
  void count(ApplyStatus status, const Book& book, ReplayCounts& counts) noexcept {
    ++counts.by_status[static_cast<std::size_t>(status)];
    if (status != ApplyStatus::no_book_effect && ++acted_ == at_) {
      const std::uint64_t start = ticks_start();
      counts.checkpoint_hash = book_hash(book);
      counts.checkpoint_reached = true;
      counts.checkpoint_orders = book.live_orders();
      counts.checkpoint_levels = book.live_levels();
      counts.checkpoint_ticks = ticks_stop() - start;
    }
  }

 private:
  std::uint64_t at_;
  std::uint64_t acted_ = 0;
};

template <class Book>
[[nodiscard]] ReplayCounts replay_single(std::span<const std::byte> day, Book& book,
                                         std::uint64_t checkpoint_at = kNoCheckpoint) noexcept {
  ReplayCounts counts;
  Tally tally{checkpoint_at};
  const std::uint64_t start = ticks_start();
  FrameReader reader{day};
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != FrameStatus::message) {
      continue;
    }
    ++counts.messages;
    tally.count(book.apply(frame.payload), book, counts);
  }
  counts.ticks = ticks_stop() - start - counts.checkpoint_ticks;
  counts.ops = counts.messages;
  return counts;
}

template <class Book>
[[nodiscard]] ReplayCounts replay_decoded(std::span<const std::byte> day, Book& book,
                                          std::uint64_t checkpoint_at = kNoCheckpoint) noexcept {
  ReplayCounts counts;
  Tally tally{checkpoint_at};
  const std::uint64_t start = ticks_start();
  FrameReader reader{day};
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != FrameStatus::message) {
      continue;
    }
    ++counts.messages;
    const std::optional<BookOp> op = decode_book_op(frame.payload);
    if (op) {
      ++counts.ops;
      tally.count(book.apply(*op), book, counts);
    }
  }
  counts.ticks = ticks_stop() - start - counts.checkpoint_ticks;
  return counts;
}

// The parser's half of replay_pipelined(), on its own thread.
class Parser {
 public:
  Parser(std::span<const std::byte> day, OpRing& ring) noexcept : day_{day}, ring_{ring} {}

  void run(const std::atomic<bool>& go) noexcept {
    while (!go.load(std::memory_order_acquire)) {
      // Started before the span, and spinning so that the release costs no wake-up.
    }
    FrameReader reader{day_};
    for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
      if (frame.status != FrameStatus::message) {
        continue;
      }
      ++messages_;
      const std::optional<BookOp> op = decode_book_op(frame.payload);
      if (!op) {
        continue;
      }
      batch_[filled_++] = *op;
      if (filled_ == batch_.size()) {
        flush();
      }
    }
    flush();
    total_.store(ops_, std::memory_order_release);
  }

  // How many ops the parser pushed in all, once it has pushed the last; until then, more
  // than any day holds.
  [[nodiscard]] std::uint64_t total() const noexcept {
    return total_.load(std::memory_order_acquire);
  }
  [[nodiscard]] std::uint64_t messages() const noexcept { return messages_; }
  [[nodiscard]] std::uint64_t full_waits() const noexcept { return full_waits_; }

 private:
  void flush() noexcept {
    std::size_t at = 0;
    while (at < filled_) {
      const std::size_t pushed =
          ring_.try_push_batch(std::span<const BookOp>{batch_}.subspan(at, filled_ - at));
      full_waits_ += pushed == 0 ? 1U : 0U;
      at += pushed;
    }
    ops_ += filled_;
    filled_ = 0;
  }

  std::span<const std::byte> day_;
  OpRing& ring_;
  std::array<BookOp, kPipelineBatch> batch_{};
  std::size_t filled_ = 0;
  std::uint64_t messages_ = 0;
  std::uint64_t ops_ = 0;
  std::uint64_t full_waits_ = 0;
  std::atomic<std::uint64_t> total_{std::numeric_limits<std::uint64_t>::max()};
};

// The parser thread is pinned to parser_core where the platform can pin, as the caller's
// thread is to its own. `ready` runs on this thread once the parser thread is waiting and
// before the span opens: the allocation test opens its window there.
template <class Book, class Ready>
[[nodiscard]] ReplayCounts replay_pipelined(std::span<const std::byte> day, Book& book,
                                            OpRing& ring, int parser_core, Ready ready,
                                            std::uint64_t checkpoint_at = kNoCheckpoint) {
  ReplayCounts counts;
  Tally tally{checkpoint_at};
  Parser parser{day, ring};
  std::atomic<bool> go{false};
  std::atomic<bool> waiting{false};
  std::thread thread{[&parser, &go, &waiting, &counts, parser_core] {
    counts.parser_pin = pin_current_thread(parser_core);
    waiting.store(true, std::memory_order_release);
    parser.run(go);
  }};
  while (!waiting.load(std::memory_order_acquire)) {
    // The thread is pinned and about to spin on go.
  }
  ready();
  std::array<BookOp, kPipelineBatch> ops{};
  std::uint64_t applied = 0;
  const std::uint64_t start = ticks_start();
  go.store(true, std::memory_order_release);
  for (;;) {
    const std::size_t got = ring.try_pop_batch(ops);
    if (got == 0) {
      if (applied == parser.total()) {
        break;
      }
      ++counts.empty_waits;
      continue;
    }
    for (std::size_t i = 0; i < got; ++i) {
      tally.count(book.apply(ops[i]), book, counts);
    }
    applied += got;
  }
  counts.ticks = ticks_stop() - start - counts.checkpoint_ticks;
  thread.join();
  counts.messages = parser.messages();
  counts.ops = applied;
  counts.full_waits = parser.full_waits();
  return counts;
}

}  // namespace itch::bench
