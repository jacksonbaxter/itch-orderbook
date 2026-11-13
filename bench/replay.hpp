// The passes of a replay benchmark, with nothing platform-specific in them, so that a
// test can drive them with a fake clock, input and sink.
//
// A run is one warm-up pass, then separate throughput and latency passes, then an untimed
// pass of counters and memory. Each pass gets a fresh book, built outside its timed span.
// The warm-up's numbers go nowhere near the results: it exists to fault the input in, warm
// the code and count the messages. Nothing is timed unless the warm-up ran and every page
// of the input is resident, and a refusal is on the record rather than a quiet skip.
//
// Two things a design may add to a pass, each kept out of what it times:
//
//   - A book that ages its heap (AgesHeap) takes one ageing step before each message that
//     adds an order. The step is the experiment's setup, not the book's work, so the
//     throughput pass stops its clock for it, and the latency pass takes it outside the
//     span. The stops are counted, so that their own cost is on the record.
//   - With top-of-book tracking on, every message the book applied is followed by a read of
//     the best bid and ask on its locate, compared with the last seen there. This is what
//     itch-book's replay tool does for T-BOOK-2: price and shares on each side, after every
//     book event. The throughput pass times it with the book. The latency span stays
//     apply() alone.
//
// A clock that can count page faults (CountsFaults) is read for them just before each timed
// pass starts and just after it stops, so that each pass carries the faults it took. The
// span is the whole pass, an ageing book's stops included.
//
// A design may also warm the code of its own timed passes (warm_code): each pass, untimed,
// on a fresh book of the design, over the stream up to its first few thousand book
// operations. A pass that runs code no earlier pass ran faults the pages of its own
// instructions in, which is not what a fault count of the pass is after (T-ARENA-1).
//
// Residency is asked last, after everything the pass allocates, because allocating can
// push file pages out: on debian 155 pages of a day went between the warm-up and the first
// pass, with the sample arena allocated in between. It is asked again when the pass ends,
// so that a page lost during a timed span is a refusal too.
#pragma once

#include <array>
#include <concepts>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <limits>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "itch/book.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"
#include "platform/memory.hpp"
#include "platform/timer.hpp"

namespace itch::bench {

struct PassTotals {
  std::uint64_t messages = 0;
  std::uint64_t ticks = 0;
  std::uint64_t rejected = 0;     // apply() refused the message. A clean day has none.
  std::uint64_t top_changes = 0;  // Messages after which a tracked top of book moved.
  std::uint64_t pauses = 0;       // Times the clock stopped for an ageing step.
  std::optional<std::uint64_t> faults = std::nullopt;  // Page faults, when the clock counts them.
};

[[nodiscard]] constexpr bool is_rejection(ApplyStatus status) noexcept {
  return status != ApplyStatus::applied && status != ApplyStatus::no_book_effect;
}

// What a message asks of the book, for per-operation latency (T-LAT-1). A replace is
// its own operation, and a reduce is anything that takes shares off a live order.
enum class OpClass : std::uint8_t { add, del, replace, reduce, other };
inline constexpr std::size_t kOpClasses = 5;
inline constexpr std::array<std::string_view, kOpClasses> kOpNames{"add", "delete", "replace",
                                                                   "reduce", "other"};

[[nodiscard]] inline OpClass op_class(std::span<const std::byte> payload) noexcept {
  if (payload.empty()) {
    return OpClass::other;
  }
  switch (read_char(payload.data())) {
    case AddOrder::kType:
    case AddOrderAttributed::kType: return OpClass::add;
    case OrderDelete::kType: return OpClass::del;
    case OrderReplace::kType: return OpClass::replace;
    case OrderExecuted::kType:
    case OrderExecutedWithPrice::kType:
    case OrderCancel::kType: return OpClass::reduce;
    default: return OpClass::other;
  }
}

// carteret's "book messages" (T-LAT-4): the seven types that change a book and the two
// trade prints. On 12302019 that is 264,478,375 of 268,744,780.
[[nodiscard]] inline bool is_book_message(std::span<const std::byte> payload) noexcept {
  if (payload.empty()) {
    return false;
  }
  const char type = read_char(payload.data());
  return op_class(payload) != OpClass::other || type == Trade::kType || type == CrossTrade::kType;
}

// The messages that open an order, where HeapAger::before_message takes a step.
[[nodiscard]] inline bool adds_order(std::span<const std::byte> payload) noexcept {
  const OpClass op = op_class(payload);
  return op == OpClass::add || op == OpClass::replace;
}

// A book that ages its heap as it goes. See heap_ager.hpp.
template <class Book>
concept AgesHeap = requires(Book& book) { book.age_heap(); };

// The day's mix, counted once by the warm-up.
struct MessageMix {
  std::uint64_t messages = 0;
  std::uint64_t book_messages = 0;
  std::array<std::uint64_t, kOpClasses> by_op{};
};

[[nodiscard]] inline MessageMix count_mix(std::span<const std::byte> input) noexcept {
  FrameReader reader{input};
  MessageMix mix;
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == FrameStatus::message) {
      ++mix.messages;
      mix.book_messages += is_book_message(frame.payload) ? 1U : 0U;
      ++mix.by_op[static_cast<std::size_t>(op_class(frame.payload))];
    }
  }
  return mix;
}

struct NoTopTracking {
  static constexpr bool kActive = false;
  template <class Book>
  [[nodiscard]] static constexpr bool observe(const Book& /*book*/,
                                              std::span<const std::byte> /*payload*/) noexcept {
    return false;
  }
};

// The last top of book seen on each locate, both sides. Sized for every locate up front,
// so that observing never allocates.
class TopTracker {
 public:
  static constexpr bool kActive = true;

  TopTracker() : last_(std::size_t{1} << 16U) {}

  // After a message the book applied: true if either side's best price or shares moved.
  template <class Book>
  [[nodiscard]] bool observe(const Book& book, std::span<const std::byte> payload) noexcept {
    const auto locate = read_be<std::uint16_t>(payload.data() + 1);
    const Top now{.bid = seen(book.best(locate, Side::bid)),
                  .ask = seen(book.best(locate, Side::ask))};
    Top& last = last_[locate];
    if (now == last) {
      return false;
    }
    last = now;
    return true;
  }

 private:
  struct Seen {
    bool present = false;
    Price4 price = 0;
    std::uint64_t shares = 0;
    friend bool operator==(const Seen&, const Seen&) = default;
  };
  struct Top {
    Seen bid;
    Seen ask;
    friend bool operator==(const Top&, const Top&) = default;
  };

  [[nodiscard]] static Seen seen(const TopOfBook& top) noexcept {
    return top.present ? Seen{.present = true, .price = top.price, .shares = top.shares} : Seen{};
  }

  std::vector<Top> last_;
};

struct PlatformClock {
  [[nodiscard]] static std::uint64_t start() noexcept { return ticks_start(); }
  [[nodiscard]] static std::uint64_t stop() noexcept { return ticks_stop(); }
  [[nodiscard]] static std::optional<std::uint64_t> faults() noexcept { return page_faults(); }
};

// A clock that says how many page faults the process has taken.
template <class Clock>
concept CountsFaults = requires(Clock& clock) {
  { clock.faults() } -> std::same_as<std::optional<std::uint64_t>>;
};

template <class Clock>
[[nodiscard]] std::optional<std::uint64_t> faults_now(Clock& clock) {
  if constexpr (CountsFaults<Clock>) {
    return clock.faults();
  } else {
    return std::nullopt;
  }
}

// The faults between two reads, or nothing if either read failed.
[[nodiscard]] inline std::optional<std::uint64_t> faults_between(
    std::optional<std::uint64_t> before, std::optional<std::uint64_t> after) noexcept {
  if (!before || !after || *after < *before) {
    return std::nullopt;
  }
  return *after - *before;
}

template <class Clock, class Book, class Tracker>
[[nodiscard]] PassTotals throughput_pass(std::span<const std::byte> input, Book& book, Clock& clock,
                                         Tracker& tracker) {
  FrameReader reader{input};
  PassTotals totals;
  const std::optional<std::uint64_t> faults_before = faults_now(clock);
  std::uint64_t started = clock.start();
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == FrameStatus::message) {
      if constexpr (AgesHeap<Book>) {
        if (adds_order(frame.payload)) {
          totals.ticks += clock.stop() - started;
          book.age_heap();
          ++totals.pauses;
          started = clock.start();
        }
      }
      ++totals.messages;
      const ApplyStatus status = book.apply(frame.payload);
      totals.rejected += is_rejection(status) ? 1U : 0U;
      if constexpr (Tracker::kActive) {
        if (status == ApplyStatus::applied) {
          totals.top_changes += tracker.observe(book, frame.payload) ? 1U : 0U;
        }
      }
    }
  }
  totals.ticks += clock.stop() - started;
  totals.faults = faults_between(faults_before, faults_now(clock));
  return totals;
}

template <class Clock, class Book>
[[nodiscard]] PassTotals throughput_pass(std::span<const std::byte> input, Book& book,
                                         Clock& clock) {
  NoTopTracking none;
  return throughput_pass(input, book, clock, none);
}

// One message's span is apply() alone, and the frame walk stays outside it. The sink is
// called only after the span has closed, so that whatever it does is not measured. The
// pass's own ticks include that cost, which is what the instrumentation ratio reports.
template <class Clock, class Book, class Sink, class Tracker>
[[nodiscard]] PassTotals latency_pass(std::span<const std::byte> input, Book& book, Clock& clock,
                                      Sink& sink, Tracker& tracker) {
  FrameReader reader{input};
  PassTotals totals;
  const std::optional<std::uint64_t> faults_before = faults_now(clock);
  const std::uint64_t started = clock.start();
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != FrameStatus::message) {
      continue;
    }
    ++totals.messages;
    if constexpr (AgesHeap<Book>) {
      if (adds_order(frame.payload)) {
        book.age_heap();
        ++totals.pauses;
      }
    }
    const OpClass op = op_class(frame.payload);
    const std::uint64_t before = clock.start();
    const ApplyStatus status = book.apply(frame.payload);
    const std::uint64_t after = clock.stop();
    sink.push(after - before, op);
    totals.rejected += is_rejection(status) ? 1U : 0U;
    if constexpr (Tracker::kActive) {
      if (status == ApplyStatus::applied) {
        totals.top_changes += tracker.observe(book, frame.payload) ? 1U : 0U;
      }
    }
  }
  totals.ticks = clock.stop() - started;
  totals.faults = faults_between(faults_before, faults_now(clock));
  return totals;
}

template <class Clock, class Book, class Sink>
[[nodiscard]] PassTotals latency_pass(std::span<const std::byte> input, Book& book, Clock& clock,
                                      Sink& sink) {
  NoTopTracking none;
  return latency_pass(input, book, clock, sink, none);
}

// Latency samples, written straight into a pre-faulted arena, each with its operation.
// HdrHistogram sees them only after the pass, so its recording cost is in no span and no
// cache line of the run. The ticks fill the front of the arena and the operations the
// back, so a sample takes kBytesPerSample.
class SampleBuffer {
 public:
  static constexpr std::size_t kBytesPerSample = sizeof(std::uint32_t) + sizeof(OpClass);

  explicit SampleBuffer(
      std::span<std::byte> memory) noexcept  // NOLINT(google-explicit-constructor)
      : capacity_{memory.size() / kBytesPerSample},
        // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
        samples_{reinterpret_cast<std::uint32_t*>(memory.data())},
        // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
        ops_{reinterpret_cast<OpClass*>(memory.data() + (capacity_ * sizeof(std::uint32_t)))} {}

  void push(std::uint64_t ticks, OpClass op) noexcept {
    constexpr std::uint64_t kMax = std::numeric_limits<std::uint32_t>::max();
    if (count_ < capacity_) {
      samples_[count_] = static_cast<std::uint32_t>(ticks < kMax ? ticks : kMax);
      ops_[count_] = op;
      ++count_;
    }
  }

  [[nodiscard]] std::span<const std::uint32_t> samples() const noexcept {
    return {samples_, count_};
  }
  [[nodiscard]] std::span<const OpClass> ops() const noexcept { return {ops_, count_}; }

 private:
  std::size_t capacity_;
  std::uint32_t* samples_;
  OpClass* ops_;
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

// The input up to and including its `operations`th message the book acts on: the warm-up
// of a pass's code, which needs every path of apply() and few nodes.
[[nodiscard]] inline std::span<const std::byte> leading_operations(
    std::span<const std::byte> input, std::uint64_t operations) noexcept {
  FrameReader reader{input};
  std::uint64_t seen = 0;
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == FrameStatus::message && op_class(frame.payload) != OpClass::other &&
        ++seen == operations) {
      return input.first(
          static_cast<std::size_t>(frame.payload.data() + frame.payload.size() - input.data()));
    }
  }
  return input;
}

struct SessionReport {
  bool warmed = false;
  std::uint64_t warmup_messages = 0;  // Proof the warm-up ran. Published nowhere else.
  std::uint64_t warmup_ticks = 0;
  std::string warmup_book = "this design";  // What the warm-up pass ran on.
  std::uint64_t code_warmup_messages = 0;   // Of each pass's code warm-up, when it ran one.
  MessageMix mix;                           // Counted by the warm-up, outside its timing.
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

// Input gives bytes(), fault_in() and residency(). Book comes fresh from make_book for each
// pass, and so does the top-of-book tracker when tracking is on.
template <class Input, class Clock, class Book>
class ReplaySession {
 public:
  using MakeBook = std::function<std::unique_ptr<Book>()>;
  using Warm = std::function<PassTotals(std::span<const std::byte>)>;

  ReplaySession(Input& input, Clock& clock, MakeBook make_book, bool track_top = false)
      : input_{&input}, clock_{&clock}, make_book_{std::move(make_book)}, track_top_{track_top} {}

  void refuse(std::string why) { report_.refusals.push_back(std::move(why)); }

  // Another book's pass to warm up with, in place of this design's own. A book that takes
  // small blocks from the heap warms up with one that does not, so that its throughput
  // pass is the first of its kind the heap has seen. book names it for the record.
  void warm_with(std::string book, Warm warm) {
    report_.warmup_book = std::move(book);
    warm_ = std::move(warm);
  }

  void warm_up() {
    PassTotals pass;
    if (warm_) {
      pass = warm_(input_->bytes());
    } else {
      const std::unique_ptr<Book> book = make_book_();
      pass = throughput_pass(input_->bytes(), *book, *clock_);
    }
    report_.warmed = true;
    report_.warmup_messages = pass.messages;
    report_.warmup_ticks = pass.ticks;
    report_.mix = count_mix(input_->bytes());
  }

  // Both timed passes, untimed and on books of their own, over the input up to its
  // `operations`th book operation, so that their code is warm before either is timed.
  void warm_code(std::uint64_t operations) {
    const std::span<const std::byte> prefix = leading_operations(input_->bytes(), operations);
    const MessageMix mix = count_mix(prefix);
    std::vector<std::byte> memory(mix.messages * SampleBuffer::kBytesPerSample);
    SampleBuffer samples{std::span<std::byte>{memory}};
    std::optional<TopTracker> tracker;
    if (track_top_) {
      tracker.emplace();
    }
    {
      const std::unique_ptr<Book> book = make_book_();
      static_cast<void>(tracker ? throughput_pass(prefix, *book, *clock_, *tracker)
                                : throughput_pass(prefix, *book, *clock_));
    }
    {
      const std::unique_ptr<Book> book = make_book_();
      static_cast<void>(tracker ? latency_pass(prefix, *book, *clock_, samples, *tracker)
                                : latency_pass(prefix, *book, *clock_, samples));
    }
    report_.code_warmup_messages = mix.messages;
  }

  // The warm-up of a process with no timed pass: the input faulted in and counted, and no
  // book built, so that the untimed pass's book is the first this process has held and
  // the memory it is charged for is the book's own.
  void warm_up_without_book() {
    input_->fault_in();
    report_.residency = input_->residency().describe();
    report_.mix = count_mix(input_->bytes());
    report_.warmed = true;
    report_.warmup_messages = report_.mix.messages;
  }

  [[nodiscard]] bool timed_throughput() {
    if (!may_time()) {
      return false;
    }
    const std::unique_ptr<Book> book = make_book_();
    std::optional<TopTracker> tracker;
    if (track_top_) {
      tracker.emplace();
    }
    if (!input_resident()) {
      return false;
    }
    const PassTotals pass = tracker ? throughput_pass(input_->bytes(), *book, *clock_, *tracker)
                                    : throughput_pass(input_->bytes(), *book, *clock_);
    report_.throughput = pass;
    const bool stayed = input_stayed();
    return note_rejections(pass) && stayed;
  }

  template <class Sink>
  [[nodiscard]] bool timed_latency(Sink& sink) {
    if (!may_time()) {
      return false;
    }
    const std::unique_ptr<Book> book = make_book_();
    std::optional<TopTracker> tracker;
    if (track_top_) {
      tracker.emplace();
    }
    if (!input_resident()) {
      return false;
    }
    const PassTotals pass = tracker ? latency_pass(input_->bytes(), *book, *clock_, sink, *tracker)
                                    : latency_pass(input_->bytes(), *book, *clock_, sink);
    report_.latency = pass;
    const bool stayed = input_stayed();
    return note_rejections(pass) && stayed;
  }

  [[nodiscard]] const SessionReport& report() const noexcept { return report_; }

 private:
  [[nodiscard]] bool may_time() {
    if (!report_.warmed) {
      refuse("no warm-up pass ran before timing");
      return false;
    }
    return report_.publishable();
  }

  [[nodiscard]] bool input_resident() {
    input_->fault_in();
    const Residency residency = input_->residency();
    report_.residency = residency.describe();
    if (!residency.complete()) {
      refuse("input not resident: " + report_.residency);
      return false;
    }
    return true;
  }

  // Touches nothing, so that a page lost during the pass still shows.
  [[nodiscard]] bool input_stayed() {
    const Residency residency = input_->residency();
    report_.residency = residency.describe();
    if (!residency.complete()) {
      refuse("input left memory during the pass: " + report_.residency);
      return false;
    }
    return true;
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
  bool track_top_;
  Warm warm_;
  SessionReport report_;
};

struct NoMemorySampling {
  static constexpr void sample() noexcept {}
};

// The most memory charged to the process at any sample, over a baseline taken when it is
// made. Read gives the bytes now, or nothing when the kernel would not say.
template <class Read>
class MemoryHighWater {
 public:
  explicit MemoryHighWater(Read read) : read_{std::move(read)}, baseline_{read_()} {}

  void sample() noexcept {
    const std::optional<std::uint64_t> now = read_();
    if (!now) {
      failed_ = true;
      return;
    }
    peak_ = peak_ < *now ? *now : peak_;
    ++samples_;
  }

  [[nodiscard]] bool complete() const noexcept { return baseline_.has_value() && !failed_; }
  [[nodiscard]] std::uint64_t baseline() const noexcept { return baseline_.value_or(0); }
  [[nodiscard]] std::uint64_t peak() const noexcept { return peak_; }
  // What the pass added at its height. Zero if it never rose above the baseline.
  [[nodiscard]] std::uint64_t growth() const noexcept {
    return peak_ > baseline() ? peak_ - baseline() : 0;
  }
  [[nodiscard]] std::uint64_t samples() const noexcept { return samples_; }

 private:
  Read read_;
  std::optional<std::uint64_t> baseline_;
  std::uint64_t peak_ = 0;
  std::uint64_t samples_ = 0;
  bool failed_ = false;
};

// Messages between two samples of the process's memory in the untimed pass.
inline constexpr std::uint64_t kMemorySampleEvery = std::uint64_t{1} << 20U;

// The untimed pass of per-message hardware counts: an rdpmc of every event on each side
// of apply(), and the difference handed to the sink after the second read. Each event's
// difference runs from its own read before apply() to its own read after it, so it also
// holds the other events' reads that fall between and the loop; no empty span is taken off.
// Group gives read_fast(span) and event_count(), sink push(event, count). The same pass
// samples memory every kMemorySampleEvery messages and at the end, outside the reads, and
// takes a heap-ageing book's steps outside them too.
template <class Group, class Book, class Sink, class Memory>
[[nodiscard]] std::uint64_t counts_pass(std::span<const std::byte> input, Book& book, Group& group,
                                        Sink& sink, Memory& memory) {
  const std::size_t events = group.event_count();
  std::vector<std::uint64_t> before(events);
  std::vector<std::uint64_t> after(events);
  std::uint64_t missed = 0;
  std::uint64_t seen = 0;
  FrameReader reader{input};
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != FrameStatus::message) {
      continue;
    }
    if (++seen % kMemorySampleEvery == 0) {
      memory.sample();
    }
    if constexpr (AgesHeap<Book>) {
      if (adds_order(frame.payload)) {
        book.age_heap();
      }
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
  memory.sample();
  return missed;
}

template <class Group, class Book, class Sink>
[[nodiscard]] std::uint64_t counts_pass(std::span<const std::byte> input, Book& book, Group& group,
                                        Sink& sink) {
  NoMemorySampling none;
  return counts_pass(input, book, group, sink, none);
}

}  // namespace itch::bench
