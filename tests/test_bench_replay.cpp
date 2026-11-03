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

#include "designs.hpp"
#include "itch/book.hpp"
#include "itch/heap_ager.hpp"
#include "itch/messages.hpp"
#include "replay.hpp"
#include "run_record.hpp"
#include "support/encode.hpp"

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
  [[nodiscard]] static itch::TopOfBook best(std::uint16_t /*locate*/, itch::Side /*side*/) {
    return {.present = false, .price = 0, .shares = 0, .orders = 0};
  }
};

// Ages its heap: each step is a 'g' on the trace and a long stretch of the fake clock, so a
// timed figure that held one would show it.
struct FakeAgeingBook : FakeBook {
  int steps = 0;
  void age_heap() {
    trace->events += 'g';
    trace->now += 1000;
    ++steps;
  }
};

struct FakeSink {
  Trace* trace;
  std::vector<std::uint64_t> pushed;
  std::vector<OpClass> ops;
  void push(std::uint64_t ticks, OpClass op) {
    trace->events += 'r';
    pushed.push_back(ticks);
    ops.push_back(op);
  }
};

// Each payload behind its two-byte length, as a BinaryFILE stream holds it.
std::vector<std::byte> frames(const std::vector<std::vector<std::byte>>& payloads) {
  std::vector<std::byte> bytes;
  for (const std::vector<std::byte>& payload : payloads) {
    bytes.push_back(static_cast<std::byte>(payload.size() >> 8U));
    bytes.push_back(static_cast<std::byte>(payload.size() & 0xFFU));
    bytes.insert(bytes.end(), payload.begin(), payload.end());
  }
  return bytes;
}

itch::Header header(char type, std::uint16_t locate) {
  return {.message_type = type, .stock_locate = locate, .tracking_number = 0, .timestamp_ns = 1};
}

std::vector<std::byte> add(std::uint64_t reference, char side, std::uint32_t shares,
                           std::uint32_t price, std::uint16_t locate = 7) {
  return itch::testing::encode(itch::AddOrder{.header = header('A', locate),
                                              .order_reference = reference,
                                              .side = side,
                                              .shares = shares,
                                              .stock = {'T', 'E', 'S', 'T', ' ', ' ', ' ', ' '},
                                              .price = price});
}

std::vector<std::byte> cancel(std::uint64_t reference, std::uint32_t shares,
                              std::uint16_t locate = 7) {
  return itch::testing::encode(itch::OrderCancel{
      .header = header('X', locate), .order_reference = reference, .cancelled_shares = shares});
}

std::vector<std::byte> remove(std::uint64_t reference, std::uint16_t locate = 7) {
  return itch::testing::encode(
      itch::OrderDelete{.header = header('D', locate), .order_reference = reference});
}

std::vector<std::byte> type_only(char type) {
  std::vector<std::byte> payload(1);
  payload[0] = static_cast<std::byte>(type);
  return payload;
}

// A message of this type at its length, every field zero.
std::vector<std::byte> sized(char type) {
  std::vector<std::byte> payload(itch::message_length(type));
  payload[0] = static_cast<std::byte>(type);
  return payload;
}

struct FakeInput {
  std::vector<std::byte> bytes_;
  Residency residency_{.pages = 4, .resident_pages = 4, .error = {}};
  int checks = 0;
  int touches = 0;
  int lost_from_check = 0;  // From this check on, a page is gone. 0 means never.
  bool touching_restores = false;
  [[nodiscard]] std::span<const std::byte> bytes() const { return bytes_; }
  void fault_in() {
    ++touches;
    if (touching_restores) {
      residency_.resident_pages = residency_.pages;
    }
  }
  [[nodiscard]] Residency residency() {
    ++checks;
    if (lost_from_check != 0 && checks >= lost_from_check) {
      residency_.resident_pages = residency_.pages - 1;
    }
    return residency_;
  }
};

struct Rig {
  Trace trace;
  FakeClock clock{&trace};
  FakeInput input{.bytes_ = system_events(3)};
  ApplyStatus status = ApplyStatus::no_book_effect;
  int books = 0;
  bool book_evicts = false;
  ReplaySession<FakeInput, FakeClock, FakeBook> session{input, clock, [this] {
                                                          ++books;
                                                          if (book_evicts) {
                                                            input.residency_.resident_pages = 3;
                                                          }
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
  FakeSink sink{.trace = &rig.trace, .pushed = {}, .ops = {}};
  rig.session.warm_up();
  ASSERT_TRUE(rig.session.timed_throughput());
  ASSERT_TRUE(rig.session.timed_latency(sink));
  EXPECT_EQ(rig.books, 3);
}

TEST(BenchReplay, AWarmUpWithAnotherBookBuildsNoneOfThisDesign) {
  Rig rig;
  int warmed = 0;
  rig.session.warm_with("other", [&](std::span<const std::byte> /*bytes*/) {
    ++warmed;
    return PassTotals{.messages = 3, .ticks = 1, .rejected = 0, .top_changes = 0, .pauses = 0};
  });
  rig.session.warm_up();
  EXPECT_EQ(warmed, 1);
  EXPECT_EQ(rig.books, 0);
  EXPECT_EQ(rig.session.report().warmup_book, "other");
  ASSERT_TRUE(rig.session.timed_throughput());
  EXPECT_EQ(rig.books, 1);
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
  FakeSink sink{.trace = &rig.trace, .pushed = {}, .ops = {}};
  rig.session.warm_up();
  ASSERT_TRUE(rig.session.timed_throughput());
  rig.input.residency_.resident_pages = 0;
  EXPECT_FALSE(rig.session.timed_latency(sink));
  EXPECT_TRUE(sink.pushed.empty());
  EXPECT_EQ(rig.input.checks, 3);
}

TEST(BenchReplay, PagesAPassesAllocationPushedOutAreBroughtBackBeforeTiming) {
  Rig rig;
  rig.input.residency_.resident_pages = 3;
  rig.input.touching_restores = true;
  rig.session.warm_up();
  EXPECT_TRUE(rig.session.timed_throughput());
  EXPECT_EQ(rig.input.touches, 1);
  EXPECT_TRUE(rig.session.report().publishable());
}

TEST(BenchReplay, ResidencyIsAskedAfterThePassHasItsBook) {
  Rig rig;
  rig.book_evicts = true;
  rig.session.warm_up();
  rig.input.residency_.resident_pages = 4;
  const std::string after_warmup = rig.trace.events;
  EXPECT_FALSE(rig.session.timed_throughput());
  EXPECT_EQ(rig.trace.events, after_warmup);
  EXPECT_FALSE(rig.session.report().throughput.has_value());
}

TEST(BenchReplay, APageLostDuringAPassIsARefusal) {
  Rig rig;
  rig.input.lost_from_check = 2;
  rig.session.warm_up();
  EXPECT_FALSE(rig.session.timed_throughput());
  const SessionReport& report = rig.session.report();
  ASSERT_EQ(report.refusals.size(), 1U);
  EXPECT_EQ(report.refusals[0], "input left memory during the pass: 3 of 4 pages resident");
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
  FakeSink sink{.trace = &trace, .pushed = {}, .ops = {}};
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
  FakeSink sink{.trace = &trace, .pushed = {}, .ops = {}};
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
  alignas(std::uint32_t) std::array<std::byte, (2 * SampleBuffer::kBytesPerSample) + 3> memory{};
  SampleBuffer buffer{memory};
  buffer.push(5, OpClass::add);
  buffer.push(std::uint64_t{1} << 40U, OpClass::reduce);
  buffer.push(7, OpClass::del);
  EXPECT_EQ(buffer.samples().size(), 2U);
  EXPECT_EQ(buffer.samples()[0], 5U);
  EXPECT_EQ(buffer.samples()[1], 0xFFFFFFFFU);
  ASSERT_EQ(buffer.ops().size(), 2U);
  EXPECT_EQ(buffer.ops()[0], OpClass::add);
  EXPECT_EQ(buffer.ops()[1], OpClass::reduce);
}

TEST(BenchReplay, EachMessageTypeHasItsOperation) {
  EXPECT_EQ(op_class(type_only('A')), OpClass::add);
  EXPECT_EQ(op_class(type_only('F')), OpClass::add);
  EXPECT_EQ(op_class(type_only('D')), OpClass::del);
  EXPECT_EQ(op_class(type_only('U')), OpClass::replace);
  EXPECT_EQ(op_class(type_only('E')), OpClass::reduce);
  EXPECT_EQ(op_class(type_only('C')), OpClass::reduce);
  EXPECT_EQ(op_class(type_only('X')), OpClass::reduce);
  for (const char type : {'S', 'R', 'H', 'P', 'Q', 'B', 'I', 'L', '?'}) {
    EXPECT_EQ(op_class(type_only(type)), OpClass::other) << type;
  }
  EXPECT_EQ(op_class({}), OpClass::other);
}

TEST(BenchReplay, BookMessagesAreTheBookTypesAndTheTwoTradePrints) {
  for (const char type : {'A', 'F', 'E', 'C', 'X', 'D', 'U', 'P', 'Q'}) {
    EXPECT_TRUE(is_book_message(type_only(type))) << type;
  }
  for (const char type : {'S', 'R', 'H', 'Y', 'L', 'V', 'W', 'K', 'J', 'h', 'B', 'I', 'N'}) {
    EXPECT_FALSE(is_book_message(type_only(type))) << type;
  }
}

// The replay stops its clock exactly where HeapAger::before_message takes a step.
TEST(BenchReplay, TheClockStopsWhereTheAgerSteps) {
  itch::HeapAger ager{{.seed = 1, .warm_blocks = 0, .ring_blocks = 4}};
  for (int code = 0; code < 128; ++code) {
    const std::vector<std::byte> payload = type_only(static_cast<char>(code));
    const std::uint64_t before = ager.allocations();
    ager.before_message(payload);
    EXPECT_EQ(ager.allocations() != before, adds_order(payload)) << code;
  }
}

TEST(BenchReplay, TheWarmUpCountsTheMix) {
  const std::vector<std::byte> input =
      frames({add(1, 'B', 100, 10), sized('P'), cancel(1, 10), remove(1), sized('S')});
  const MessageMix mix = count_mix(input);
  EXPECT_EQ(mix.messages, 5U);
  EXPECT_EQ(mix.book_messages, 4U);
  EXPECT_EQ(mix.by_op[static_cast<std::size_t>(OpClass::add)], 1U);
  EXPECT_EQ(mix.by_op[static_cast<std::size_t>(OpClass::reduce)], 1U);
  EXPECT_EQ(mix.by_op[static_cast<std::size_t>(OpClass::del)], 1U);
  EXPECT_EQ(mix.by_op[static_cast<std::size_t>(OpClass::other)], 2U);
}

TEST(BenchReplay, TheLatencyPassTagsEachSpanWithItsOperation) {
  Trace trace;
  FakeClock clock{&trace};
  FakeBook book{.trace = &trace};
  FakeSink sink{.trace = &trace, .pushed = {}, .ops = {}};
  const std::vector<std::byte> input = frames({add(1, 'B', 100, 10), cancel(1, 10), remove(1)});
  static_cast<void>(latency_pass(input, book, clock, sink));
  EXPECT_EQ(sink.ops, (std::vector<OpClass>{OpClass::add, OpClass::reduce, OpClass::del}));
}

TEST(BenchReplay, AnAgeingStepIsOutsideTheThroughputTime) {
  Trace trace;
  FakeClock clock{&trace};
  FakeAgeingBook book;
  book.trace = &trace;
  const std::vector<std::byte> input = frames({add(1, 'B', 100, 10), remove(1), add(2, 'S', 1, 9)});
  const PassTotals totals = throughput_pass(input, book, clock);
  EXPECT_EQ(trace.events, "stgsaatgsat");
  EXPECT_EQ(totals.ticks, 300U);
  EXPECT_EQ(totals.pauses, 2U);
  EXPECT_EQ(book.steps, 2);
}

TEST(BenchReplay, AnAgeingStepIsOutsideEveryLatencySpan) {
  Trace trace;
  FakeClock clock{&trace};
  FakeAgeingBook book;
  book.trace = &trace;
  FakeSink sink{.trace = &trace, .pushed = {}, .ops = {}};
  const std::vector<std::byte> input = frames({add(1, 'B', 100, 10), remove(1)});
  const PassTotals totals = latency_pass(input, book, clock, sink);
  EXPECT_EQ(trace.events, "sgsatrsatrt");
  EXPECT_EQ(sink.pushed, (std::vector<std::uint64_t>{100, 100}));
  EXPECT_EQ(totals.pauses, 1U);
}

TEST(BenchReplay, ABookThatDoesNotAgeNeverStopsTheClock) {
  Trace trace;
  FakeClock clock{&trace};
  FakeBook book{.trace = &trace};
  const std::vector<std::byte> input = frames({add(1, 'B', 100, 10), add(2, 'B', 100, 10)});
  const PassTotals totals = throughput_pass(input, book, clock);
  EXPECT_EQ(trace.events, "saat");
  EXPECT_EQ(totals.pauses, 0U);
}

// Each change to either side's best price or shares counts once, and nothing else does.
TEST(BenchReplay, TopTrackingCountsEachMoveOfTheTop) {
  const std::vector<std::byte> input = frames({
      add(1, 'B', 100, 10),    // A bid appears.
      add(2, 'S', 50, 12),     // An ask appears.
      add(3, 'B', 70, 9),      // Below the best bid: no move.
      add(4, 'B', 30, 10),     // Joins the best bid: its shares move.
      cancel(3, 20),           // Below the best bid again: no move.
      cancel(1, 100),          // Takes the best bid's shares down to the other order's.
      remove(9),               // Unknown: not applied, so not observed.
      sized('S'),              // No book effect.
      remove(2),               // The ask goes.
      add(5, 'S', 10, 12, 8),  // Another locate's first ask.
  });
  itch::FastBook<> book{{.orders = 16, .levels = 16}};
  PlatformClock clock;
  TopTracker tracker;
  const PassTotals totals = throughput_pass(input, book, clock, tracker);
  EXPECT_EQ(totals.top_changes, 6U);
  EXPECT_EQ(totals.rejected, 1U);
}

TEST(BenchReplay, EveryDesignSeesTheSameMovesOfTheTop) {
  const std::vector<std::byte> input = frames(
      {add(1, 'B', 100, 10), add(2, 'S', 50, 12), add(3, 'B', 70, 11), cancel(3, 70), remove(1)});
  PlatformClock clock;
  std::vector<std::uint64_t> seen;
  for (const char* name :
       {"hash-linked", "hash-flat", "paged-linked", "paged-flat", "map-fresh", "map-aged"}) {
    const std::optional<Design> design = parse_design(name);
    ASSERT_TRUE(design.has_value()) << name;
    EXPECT_EQ(design_name(*design), name);
    TopTracker tracker;
    if (design->kind == DesignKind::map_fresh) {
      itch::MapBook book;
      seen.push_back(throughput_pass(input, book, clock, tracker).top_changes);
    } else if (design->kind == DesignKind::map_aged) {
      AgedMapBook book{{.seed = 3, .warm_blocks = 64, .ring_blocks = 8}};
      seen.push_back(throughput_pass(input, book, clock, tracker).top_changes);
    } else {
      itch::tools::with_book_arm(design->arm, [&]<class Index, class Levels> {
        itch::FastBook<itch::NoWriteObserver, Index, Levels> book{
            {.orders = 16, .levels = 16, .index_pages = 4, .index_bits = 16, .level_slots = 64}};
        seen.push_back(throughput_pass(input, book, clock, tracker).top_changes);
      });
    }
  }
  EXPECT_EQ(seen, (std::vector<std::uint64_t>(6, 5)));
}

TEST(BenchReplay, ADesignNameMustBeOneOfTheSix) {
  for (const char* name : {"", "hash", "map", "hash-", "-linked", "paged-hash", "map-old"}) {
    EXPECT_FALSE(parse_design(name).has_value()) << name;
  }
}

TEST(BenchReplay, MemoryHighWaterKeepsThePeakOverTheBaseline) {
  std::vector<std::uint64_t> readings{100, 150, 400, 300};
  std::size_t next = 0;
  MemoryHighWater memory{[&] -> std::optional<std::uint64_t> { return readings[next++]; }};
  memory.sample();
  memory.sample();
  memory.sample();
  EXPECT_TRUE(memory.complete());
  EXPECT_EQ(memory.baseline(), 100U);
  EXPECT_EQ(memory.peak(), 400U);
  EXPECT_EQ(memory.growth(), 300U);
  EXPECT_EQ(memory.samples(), 3U);
}

TEST(BenchReplay, AMemoryReadThatFailsLeavesTheFigureIncomplete) {
  bool works = true;
  MemoryHighWater memory{[&] -> std::optional<std::uint64_t> {
    return works ? std::optional<std::uint64_t>{10} : std::nullopt;
  }};
  works = false;
  memory.sample();
  EXPECT_FALSE(memory.complete());
}

struct CountingMemory {
  int samples = 0;
  void sample() { ++samples; }
};

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

TEST(BenchReplay, CountsPassSamplesMemoryOnItsStrideAndAtTheEnd) {
  Trace trace;
  FakeBook book{.trace = &trace};
  FakeGroup group;
  CountRecorder sink;
  CountingMemory memory;
  const std::vector<std::byte> input = system_events(kMemorySampleEvery + 1);
  EXPECT_EQ(counts_pass(input, book, group, sink, memory), 0U);
  EXPECT_EQ(memory.samples, 2);
}

TEST(BenchReplay, CountsPassTakesAgeingStepsOutsideTheReads) {
  Trace trace;
  FakeAgeingBook book;
  book.trace = &trace;
  FakeGroup group;
  CountRecorder sink;
  const std::vector<std::byte> input = frames({add(1, 'B', 100, 10), remove(1)});
  EXPECT_EQ(counts_pass(input, book, group, sink), 0U);
  EXPECT_EQ(book.steps, 1);
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
