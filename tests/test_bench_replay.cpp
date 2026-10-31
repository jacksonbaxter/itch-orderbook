// The harness's rules, proved with a fake clock, input and book: what is inside a timed
// span, what the warm-up can reach, and what makes a run refuse to publish.
#include <gtest/gtest.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <vector>

#include "itch/book.hpp"
#include "replay.hpp"
#include "run_record.hpp"

// ASSERT_TRUE guards each dereference here, which the check cannot see.
// NOLINTBEGIN(bugprone-unchecked-optional-access)
namespace {

using namespace itch::bench;
using itch::ApplyStatus;

std::vector<std::byte> system_events(std::size_t count) {
  std::vector<std::byte> bytes;
  for (std::size_t i = 0; i < count; ++i) {
    bytes.push_back(std::byte{0});
    bytes.push_back(std::byte{12});
    bytes.push_back(std::byte{'S'});
    bytes.insert(bytes.end(), 11, std::byte{0});
  }
  return bytes;
}

// Every clock read and apply goes on one trace, so the order of events is the test.
struct Trace {
  std::string events;
  std::uint64_t now = 0;
};

struct FakeClock {
  Trace* trace;
  [[nodiscard]] std::uint64_t start() const {
    trace->events += 's';
    return trace->now;
  }
  [[nodiscard]] std::uint64_t stop() const {
    trace->events += 't';
    return trace->now;
  }
};

struct FakeBook {
  Trace* trace{};
  ApplyStatus status = ApplyStatus::no_book_effect;
  [[nodiscard]] ApplyStatus apply(std::span<const std::byte> /*payload*/) const {
    trace->events += 'a';
    trace->now += 100;
    return status;
  }
};

struct FakeSink {
  Trace* trace;
  std::vector<std::uint64_t> pushed;
  void push(std::uint64_t ticks) {
    trace->events += 'r';
    pushed.push_back(ticks);
  }
};

struct FakeInput {
  std::vector<std::byte> bytes_;
  Residency residency_{.pages = 4, .resident_pages = 4, .error = {}};
  int checks = 0;
  [[nodiscard]] std::span<const std::byte> bytes() const { return bytes_; }
  [[nodiscard]] Residency residency() {
    ++checks;
    return residency_;
  }
};

struct Rig {
  Trace trace;
  FakeClock clock{&trace};
  FakeInput input{.bytes_ = system_events(3)};
  ApplyStatus status = ApplyStatus::no_book_effect;
  int books = 0;
  ReplaySession<FakeInput, FakeClock, FakeBook> session{input, clock, [this] {
                                                          ++books;
                                                          auto book = std::make_unique<FakeBook>();
                                                          book->trace = &trace;
                                                          book->status = status;
                                                          return book;
                                                        }};
};

TEST(BenchReplay, TheWarmUpReachesNoResult) {
  Rig rig;
  rig.session.warm_up();
  ASSERT_TRUE(rig.session.timed_throughput());
  const SessionReport& report = rig.session.report();
  EXPECT_EQ(report.warmup_messages, 3U);
  EXPECT_EQ(report.throughput->messages, 3U);
  EXPECT_EQ(report.throughput->ticks, 300U);
}

TEST(BenchReplay, EachPassGetsAFreshBook) {
  Rig rig;
  FakeSink sink{.trace = &rig.trace, .pushed = {}};
  rig.session.warm_up();
  ASSERT_TRUE(rig.session.timed_throughput());
  ASSERT_TRUE(rig.session.timed_latency(sink));
  EXPECT_EQ(rig.books, 3);
}

TEST(BenchReplay, NothingIsTimedBeforeTheWarmUp) {
  Rig rig;
  EXPECT_FALSE(rig.session.timed_throughput());
  EXPECT_FALSE(rig.session.report().publishable());
  EXPECT_TRUE(rig.trace.events.empty());
}

TEST(BenchReplay, AnInputNotResidentIsRefusedBeforeAnyTiming) {
  Rig rig;
  rig.input.residency_.resident_pages = 3;
  rig.session.warm_up();
  const std::string after_warmup = rig.trace.events;
  EXPECT_FALSE(rig.session.timed_throughput());
  EXPECT_EQ(rig.trace.events, after_warmup);
  const SessionReport& report = rig.session.report();
  EXPECT_FALSE(report.publishable());
  ASSERT_EQ(report.refusals.size(), 1U);
  EXPECT_EQ(report.refusals[0], "input not resident: 3 of 4 pages resident");
  EXPECT_FALSE(report.throughput.has_value());
}

TEST(BenchReplay, TheLatencyPassChecksResidencyAsWell) {
  Rig rig;
  FakeSink sink{.trace = &rig.trace, .pushed = {}};
  rig.session.warm_up();
  ASSERT_TRUE(rig.session.timed_throughput());
  rig.input.residency_.resident_pages = 0;
  EXPECT_FALSE(rig.session.timed_latency(sink));
  EXPECT_TRUE(sink.pushed.empty());
  EXPECT_EQ(rig.input.checks, 2);
}

TEST(BenchReplay, AResidencyErrorIsNotAPass) {
  Rig rig;
  rig.input.residency_.error = "mincore failed";
  rig.session.warm_up();
  EXPECT_FALSE(rig.session.timed_throughput());
  EXPECT_EQ(rig.session.report().residency, "unavailable: mincore failed");
}

TEST(BenchReplay, AnEarlierRefusalStopsTiming) {
  Rig rig;
  rig.session.refuse("not pinned: mask 0-7");
  rig.session.warm_up();
  EXPECT_FALSE(rig.session.timed_throughput());
  EXPECT_FALSE(rig.session.report().throughput.has_value());
}

TEST(BenchReplay, ARejectedMessageRefusesTheRun) {
  Rig rig;
  rig.status = ApplyStatus::capacity_exhausted;
  rig.session.warm_up();
  EXPECT_FALSE(rig.session.timed_throughput());
  EXPECT_FALSE(rig.session.report().publishable());
}

TEST(BenchReplay, RecordingHappensOutsideEveryTimedSpan) {
  Trace trace;
  FakeClock clock{&trace};
  FakeBook book{.trace = &trace};
  FakeSink sink{.trace = &trace, .pushed = {}};
  const std::vector<std::byte> input = system_events(2);
  const PassTotals totals = latency_pass(input, book, clock, sink);
  EXPECT_EQ(trace.events, "ssatrsatrt");
  EXPECT_EQ(sink.pushed, (std::vector<std::uint64_t>{100, 100}));
  EXPECT_EQ(totals.messages, 2U);
}

TEST(BenchReplay, TheFrameWalkIsOutsideTheSpanAndUnknownFramesAreSkipped) {
  Trace trace;
  FakeClock clock{&trace};
  FakeBook book{.trace = &trace};
  FakeSink sink{.trace = &trace, .pushed = {}};
  std::vector<std::byte> input = system_events(1);
  input.push_back(std::byte{0});
  input.push_back(std::byte{1});
  input.push_back(std::byte{'?'});
  const PassTotals totals = latency_pass(input, book, clock, sink);
  EXPECT_EQ(totals.messages, 1U);
  EXPECT_EQ(sink.pushed.size(), 1U);
}

TEST(BenchReplay, InstrumentationCostIsARatioOfTheTwoPasses) {
  SessionReport report;
  EXPECT_FALSE(report.instrumentation_ratio().has_value());
  report.throughput = PassTotals{.messages = 10, .ticks = 400, .rejected = 0};
  report.latency = PassTotals{.messages = 10, .ticks = 1000, .rejected = 0};
  EXPECT_DOUBLE_EQ(*report.instrumentation_ratio(), 2.5);
}

TimerInfo mac_timer() {
  return {.source = "test",
          .nominal_hz = 24e6,
          .calibrated_hz = 24e6,
          .quantum_ticks = 1,
          .pair_ticks = 0.25,
          .zero_fraction = 0.8};
}

TEST(BenchReplay, APercentileBelowTheFloorCarriesNoValue) {
  const TimerInfo timer = mac_timer();
  EXPECT_FALSE(classify_percentile(50.0, 1.0, timer).value_ns.has_value());
  EXPECT_FALSE(classify_percentile(50.0, 4.0, timer).value_ns.has_value());
}

TEST(BenchReplay, APercentileAboveTheFloorCarriesItsValue) {
  const TimerInfo timer = mac_timer();
  const Percentile p = classify_percentile(99.0, 48.0, timer);
  ASSERT_TRUE(p.value_ns.has_value());
  EXPECT_NEAR(*p.value_ns, 2000.0, 1e-6);
}

TEST(BenchReplay, TheEmptyPairDoesNotCountTowardsTheFloor) {
  TimerInfo timer = mac_timer();
  timer.pair_ticks = 2.0;
  EXPECT_FALSE(classify_percentile(50.0, 5.0, timer).value_ns.has_value());
  EXPECT_TRUE(classify_percentile(50.0, 6.0, timer).value_ns.has_value());
}

TEST(BenchReplay, AClockWithNoQuantumResolvesNothing) {
  TimerInfo timer = mac_timer();
  timer.quantum_ticks = 0;
  EXPECT_FALSE(classify_percentile(50.0, 1e9, timer).value_ns.has_value());
}

TEST(BenchReplay, AnUnresolvedPercentileIsNullInTheRecord) {
  RunRecord record;
  record.latency.percentiles.push_back(classify_percentile(50.0, 1.0, mac_timer()));
  EXPECT_TRUE(to_json(record, mac_timer()).contains(R"("percentile": 50, "value_ns": null)"));
}

TEST(BenchReplay, TheSampleBufferSaturatesAndStaysInBounds) {
  std::array<std::uint32_t, 2> memory{};
  SampleBuffer buffer{std::as_writable_bytes(std::span{memory})};
  buffer.push(5);
  buffer.push(std::uint64_t{1} << 40U);
  buffer.push(7);
  EXPECT_EQ(buffer.samples().size(), 2U);
  EXPECT_EQ(buffer.samples()[0], 5U);
  EXPECT_EQ(buffer.samples()[1], 0xFFFFFFFFU);
}

struct FakeGroup {
  std::uint64_t now = 0;
  bool works = true;
  [[nodiscard]] static std::size_t event_count() { return 2; }
  [[nodiscard]] bool read_fast(std::span<std::uint64_t> out) {
    now += 10;
    out[0] = now;
    out[1] = 2 * now;
    return works;
  }
};

struct CountRecorder {
  std::vector<std::uint64_t> counts;
  void push(std::size_t event, std::uint64_t count) { counts.push_back((event * 1000) + count); }
};

TEST(BenchReplay, CountsPassHandsOnEachEventsDifference) {
  Trace trace;
  FakeBook book{.trace = &trace};
  FakeGroup group;
  CountRecorder sink;
  const std::vector<std::byte> input = system_events(2);
  EXPECT_EQ(counts_pass(input, book, group, sink), 0U);
  EXPECT_EQ(sink.counts, (std::vector<std::uint64_t>{10, 1020, 10, 1020}));
}

TEST(BenchReplay, CountsPassCountsWhatItCouldNotRead) {
  Trace trace;
  FakeBook book{.trace = &trace};
  FakeGroup group{.works = false};
  CountRecorder sink;
  const std::vector<std::byte> input = system_events(2);
  EXPECT_EQ(counts_pass(input, book, group, sink), 2U);
  EXPECT_TRUE(sink.counts.empty());
}

}  // namespace
// NOLINTEND(bugprone-unchecked-optional-access)
