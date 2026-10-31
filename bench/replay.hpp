// The passes of a replay benchmark, with nothing platform-specific in them, so that a
// test can drive them with a fake clock, input and sink.
//
// A run is one warm-up pass, then separate throughput and latency passes, then an untimed
// counter pass. Each pass gets a fresh book, built outside its timed span. The warm-up's
// numbers go nowhere near the results: it exists to fault the input in, warm the code and
// count the messages. Nothing is timed unless the warm-up ran and every page of the input
// is resident, and a refusal is on the record rather than a quiet skip.
#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <limits>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include "itch/book.hpp"
#include "itch/reader.hpp"
#include "platform/memory.hpp"
#include "platform/timer.hpp"

namespace itch::bench {

struct PassTotals {
  std::uint64_t messages = 0;
  std::uint64_t ticks = 0;
  std::uint64_t rejected = 0;  // apply() refused the message. A clean day has none.
};

[[nodiscard]] constexpr bool is_rejection(ApplyStatus status) noexcept {
  return status != ApplyStatus::applied && status != ApplyStatus::no_book_effect;
}

struct PlatformClock {
  [[nodiscard]] static std::uint64_t start() noexcept { return ticks_start(); }
  [[nodiscard]] static std::uint64_t stop() noexcept { return ticks_stop(); }
};

template <class Clock, class Book>
[[nodiscard]] PassTotals throughput_pass(std::span<const std::byte> input, Book& book,
                                         Clock& clock) {
  FrameReader reader{input};
  PassTotals totals;
  const std::uint64_t started = clock.start();
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == FrameStatus::message) {
      ++totals.messages;
      totals.rejected += is_rejection(book.apply(frame.payload)) ? 1U : 0U;
    }
  }
  totals.ticks = clock.stop() - started;
  return totals;
}

// One message's span is apply() alone, and the frame walk stays outside it. The sink is
// called only after the span has closed, so that whatever it does is not measured. The
// pass's own ticks include that cost, which is what the instrumentation ratio reports.
template <class Clock, class Book, class Sink>
[[nodiscard]] PassTotals latency_pass(std::span<const std::byte> input, Book& book, Clock& clock,
                                      Sink& sink) {
  FrameReader reader{input};
  PassTotals totals;
  const std::uint64_t started = clock.start();
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != FrameStatus::message) {
      continue;
    }
    ++totals.messages;
    const std::uint64_t before = clock.start();
    const ApplyStatus status = book.apply(frame.payload);
    const std::uint64_t after = clock.stop();
    sink.push(after - before);
    totals.rejected += is_rejection(status) ? 1U : 0U;
  }
  totals.ticks = clock.stop() - started;
  return totals;
}

// Latency samples, written straight into a pre-faulted arena. HdrHistogram sees them
// only after the pass, so its recording cost is in no span and no cache line of the run.
class SampleBuffer {
 public:
  explicit SampleBuffer(
      std::span<std::byte> memory) noexcept  // NOLINT(google-explicit-constructor)
      // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
      : samples_{reinterpret_cast<std::uint32_t*>(memory.data())},
        capacity_{memory.size() / sizeof(std::uint32_t)} {}

  void push(std::uint64_t ticks) noexcept {
    constexpr std::uint64_t kMax = std::numeric_limits<std::uint32_t>::max();
    if (count_ < capacity_) {
      samples_[count_++] = static_cast<std::uint32_t>(ticks < kMax ? ticks : kMax);
    }
  }

  [[nodiscard]] std::span<const std::uint32_t> samples() const noexcept {
    return {samples_, count_};
  }

 private:
  std::uint32_t* samples_;
  std::size_t capacity_;
  std::size_t count_ = 0;
};

// A percentile the timer can resolve carries a value. One it cannot carries none, so
// there is nothing to copy into a table by accident. Raw spans include the empty pair,
// so the test is on what is left once that is taken off.
struct Percentile {
  double percentile = 0.0;
  std::optional<double> value_ns;
};

[[nodiscard]] inline Percentile classify_percentile(double percentile, double ticks,
                                                    const TimerInfo& timer) {
  const double net = ticks - timer.pair_ticks;
  if (timer.quantum_ticks == 0 || net < timer.floor_ticks()) {
    return {.percentile = percentile, .value_ns = std::nullopt};
  }
  return {.percentile = percentile, .value_ns = timer.ticks_to_ns(ticks)};
}

struct SessionReport {
  bool warmed = false;
  std::uint64_t warmup_messages = 0;  // Proof the warm-up ran. Published nowhere else.
  std::uint64_t warmup_ticks = 0;
  std::optional<PassTotals> throughput;
  std::optional<PassTotals> latency;
  std::string residency = "unavailable: never checked";
  std::vector<std::string> refusals;

  [[nodiscard]] bool publishable() const noexcept { return refusals.empty(); }
  // What instrumenting every message costs: the latency pass over the throughput pass.
  [[nodiscard]] std::optional<double> instrumentation_ratio() const noexcept {
    if (!throughput || !latency || throughput->ticks == 0) {
      return std::nullopt;
    }
    return static_cast<double>(latency->ticks) / static_cast<double>(throughput->ticks);
  }
};

// Input gives bytes() and residency(). Book comes fresh from make_book for each pass.
template <class Input, class Clock, class Book>
class ReplaySession {
 public:
  using MakeBook = std::function<std::unique_ptr<Book>()>;

  ReplaySession(Input& input, Clock& clock, MakeBook make_book)
      : input_{&input}, clock_{&clock}, make_book_{std::move(make_book)} {}

  void refuse(std::string why) { report_.refusals.push_back(std::move(why)); }

  void warm_up() {
    const std::unique_ptr<Book> book = make_book_();
    const PassTotals pass = throughput_pass(input_->bytes(), *book, *clock_);
    report_.warmed = true;
    report_.warmup_messages = pass.messages;
    report_.warmup_ticks = pass.ticks;
  }

  [[nodiscard]] bool timed_throughput() {
    if (!ready_to_time()) {
      return false;
    }
    const std::unique_ptr<Book> book = make_book_();
    const PassTotals pass = throughput_pass(input_->bytes(), *book, *clock_);
    report_.throughput = pass;
    return note_rejections(pass);
  }

  template <class Sink>
  [[nodiscard]] bool timed_latency(Sink& sink) {
    if (!ready_to_time()) {
      return false;
    }
    const std::unique_ptr<Book> book = make_book_();
    const PassTotals pass = latency_pass(input_->bytes(), *book, *clock_, sink);
    report_.latency = pass;
    return note_rejections(pass);
  }

  [[nodiscard]] const SessionReport& report() const noexcept { return report_; }

 private:
  [[nodiscard]] bool ready_to_time() {
    if (!report_.warmed) {
      refuse("no warm-up pass ran before timing");
      return false;
    }
    const Residency residency = input_->residency();
    report_.residency = residency.describe();
    if (!residency.complete()) {
      refuse("input not resident: " + report_.residency);
      return false;
    }
    return report_.publishable();
  }

  // A rejected message means the book diverged or ran out of room, and a timing of that
  // is a timing of something else.
  [[nodiscard]] bool note_rejections(const PassTotals& pass) {
    if (pass.rejected != 0) {
      refuse("the book rejected " + std::to_string(pass.rejected) + " messages");
      return false;
    }
    return true;
  }

  Input* input_;
  Clock* clock_;
  MakeBook make_book_;
  SessionReport report_;
};

// The untimed pass of per-message hardware counts: an rdpmc of every event on each side
// of apply(), and the difference handed to the sink after the second read. Group gives
// read_fast(span) and event_count(), sink push(event, count).
template <class Group, class Book, class Sink>
[[nodiscard]] std::uint64_t counts_pass(std::span<const std::byte> input, Book& book, Group& group,
                                        Sink& sink) {
  const std::size_t events = group.event_count();
  std::vector<std::uint64_t> before(events);
  std::vector<std::uint64_t> after(events);
  std::uint64_t missed = 0;
  FrameReader reader{input};
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != FrameStatus::message) {
      continue;
    }
    if (events == 0) {
      static_cast<void>(book.apply(frame.payload));
      continue;
    }
    const bool opened = group.read_fast(before);
    static_cast<void>(book.apply(frame.payload));
    const bool closed = group.read_fast(after);
    if (!opened || !closed) {
      ++missed;
      continue;
    }
    for (std::size_t event = 0; event < events; ++event) {
      sink.push(event, after[event] - before[event]);
    }
  }
  return missed;
}

}  // namespace itch::bench
