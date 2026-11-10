// The harness's rules, proved with a fake clock, input and book: what is inside a timed
// span, what the warm-up can reach, and what makes a run refuse to publish.
#include <gtest/gtest.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <format>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <vector>

#include "designs.hpp"
#include "itch/book.hpp"
#include "itch/heap_ager.hpp"
#include "itch/messages.hpp"
#include "order_age.hpp"
#include "replay.hpp"
#include "run_record.hpp"
#include "stage_filter.hpp"
#include "support/encode.hpp"
#include "typed_counts.hpp"

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
  Trace* trace{};
  [[nodiscard]] std::uint64_t start() const {
    trace->events += 's';
    return trace->now;
  }
  [[nodiscard]] std::uint64_t stop() const {
    trace->events += 't';
    return trace->now;
  }
};

// A clock that also counts page faults: each read is an 'f' on the trace, and each apply
// takes `per_apply` faults, so that a pass's count is the applies between its two reads.
struct FaultingClock : FakeClock {
  std::uint64_t* count{};
  bool fails = false;
  [[nodiscard]] std::optional<std::uint64_t> faults() const {
    trace->events += 'f';
    if (fails) {
      return std::nullopt;
    }
    return *count;
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

// Both passes' code runs on a book of its own over the leading operations, and nothing of
// it reaches a timed figure.
TEST(BenchReplay, TheCodeWarmUpRunsBothPassesOnBooksOfTheirOwn) {
  Rig rig;
  rig.input.bytes_ = frames({add(1, 'B', 100, 10), add(2, 'S', 50, 12), remove(1), remove(2)});
  rig.session.warm_up();
  rig.session.warm_code(2);
  EXPECT_EQ(rig.books, 3);  // The warm-up's and one for each pass.
  EXPECT_EQ(rig.session.report().code_warmup_messages, 2U);
  // The warm-up, then the throughput pass and the latency pass over two messages.
  EXPECT_EQ(rig.trace.events,
            "saaaat"
            "saat"
            "ssatsatt");
  ASSERT_TRUE(rig.session.timed_throughput());
  EXPECT_EQ(rig.session.report().throughput->messages, 4U);
}

TEST(BenchReplay, TheLeadingOperationsEndOnTheLastOneAsked) {
  const std::vector<std::byte> input =
      frames({add(1, 'B', 100, 10), add(2, 'S', 50, 12), remove(1), remove(2)});
  EXPECT_EQ(count_mix(leading_operations(input, 3)).messages, 3U);
  EXPECT_EQ(count_mix(leading_operations(input, 1)).messages, 1U);
  EXPECT_EQ(leading_operations(input, 9).size(), input.size());
  const std::vector<std::byte> quiet = system_events(5);
  EXPECT_EQ(leading_operations(quiet, 1).size(), quiet.size());
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
  itch::FastBook<> book{
      {.orders = 16, .levels = 16, .index_pages = 4, .index_bits = 16, .level_slots = 64}};
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
       {"hash-linked", "hash-flat", "paged-linked", "paged-flat", "map-fresh", "map-aged",
        "identity-flat", "chained-flat", "paged-flat-scan16", "paged-flat-bisect"}) {
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
  EXPECT_EQ(seen, (std::vector<std::uint64_t>(10, 5)));
}

TEST(BenchReplay, ADesignNameMustBeOneOfTheDesigns) {
  for (const char* name : {"", "hash", "map", "hash-", "-linked", "paged-hash", "map-old",
                           "identity", "chained-linked-flat", "paged-flat-scan", "paged-scan16",
                           "alloc-", "alloc-pool", "alloc-malloc", "alloc-hash-linked"}) {
    EXPECT_FALSE(parse_design(name).has_value()) << name;
  }
}

// T-ARENA-1: every allocator arm by name, and each one's node book gives the same moves of
// the top as every other design does.
TEST(BenchReplay, EveryAllocatorArmIsADesignAndSeesTheSameMovesOfTheTop) {
  const std::vector<std::byte> input = frames(
      {add(1, 'B', 100, 10), add(2, 'S', 50, 12), add(3, 'B', 70, 11), cancel(3, 70), remove(1)});
  PlatformClock clock;
  for (const itch::tools::AllocArm arm : itch::tools::kAllocArms) {
    const std::string name = std::format("alloc-{}", to_string(arm));
    const std::optional<Design> design = parse_design(name);
    ASSERT_TRUE(design.has_value()) << name;
    EXPECT_EQ(design->kind, DesignKind::alloc);
    EXPECT_EQ(design->allocator, arm);
    EXPECT_EQ(design_name(*design), name);
    TopTracker tracker;
    const std::uint64_t moves = itch::tools::with_alloc_arm(
        arm,
        [&]<class Book> {
          Book book{{.orders = 16, .levels = 16, .pages = itch::PageRequest::base}};
          return throughput_pass(input, book, clock, tracker).top_changes;
        },
        [] { return std::uint64_t{5}; });
    EXPECT_EQ(moves, 5U) << name;
  }
}

TEST(BenchReplay, EachTimedPassCountsItsFaultsOutsideItsClock) {
  const std::vector<std::byte> input = frames({add(1, 'B', 100, 10), remove(1)});
  Trace trace;
  std::uint64_t faults = 7;
  FaultingClock clock{{&trace}, &faults};
  struct FaultingBook {
    FakeBook book;
    std::uint64_t* faults{};
    [[nodiscard]] ApplyStatus apply(std::span<const std::byte> payload) const {
      *faults += 3;
      return book.apply(payload);
    }
  };
  FaultingBook book{.book = {.trace = &trace}, .faults = &faults};
  const PassTotals throughput = throughput_pass(input, book, clock);
  ASSERT_TRUE(throughput.faults.has_value());
  EXPECT_EQ(*throughput.faults, 6U);
  EXPECT_EQ(trace.events, "fsaatf");
  trace.events.clear();
  FakeSink sink{.trace = &trace, .pushed = {}, .ops = {}};
  const PassTotals latency = latency_pass(input, book, clock, sink);
  ASSERT_TRUE(latency.faults.has_value());
  EXPECT_EQ(*latency.faults, 6U);
  EXPECT_TRUE(trace.events.starts_with("fs")) << trace.events;
  EXPECT_TRUE(trace.events.ends_with("tf")) << trace.events;
}

TEST(BenchReplay, AFaultCountThatCannotBeReadIsNoCount) {
  const std::vector<std::byte> input = frames({add(1, 'B', 100, 10)});
  Trace trace;
  std::uint64_t faults = 0;
  FaultingClock clock{{&trace}, &faults, true};
  FakeBook book{.trace = &trace};
  EXPECT_FALSE(throughput_pass(input, book, clock).faults.has_value());
  FakeClock plain{&trace};
  EXPECT_FALSE(throughput_pass(input, book, plain).faults.has_value());
  EXPECT_FALSE(faults_between(5, 4).has_value());
  EXPECT_EQ(faults_between(4, 9), std::optional<std::uint64_t>{5});
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

// Counters that follow the trace's clock: each read costs `read_cost` of its own, and the
// second event counts twice what the first does, so that a span's two figures differ.
struct TraceGroup {
  Trace* trace = nullptr;
  std::uint64_t read_cost = 0;
  bool works = true;
  [[nodiscard]] static std::size_t event_count() { return 2; }
  [[nodiscard]] bool read_fast(std::span<std::uint64_t> out) const {
    trace->now += read_cost;
    out[0] = trace->now;
    out[1] = 2 * trace->now;
    return works;
  }
};

constexpr auto slot_of(char type) { return static_cast<std::uint8_t>(type); }

TEST(TypedCounts, EachSpanIsChargedToItsMessagesType) {
  Trace trace;
  FakeBook book{.trace = &trace};
  TraceGroup group{.trace = &trace};
  TypeTally tally{TraceGroup::event_count()};
  const std::vector<std::byte> input = frames({add(1, 'B', 100, 10), remove(1), add(2, 'S', 1, 9)});
  const TypedPass pass = typed_counts_pass(input, book, group, tally);
  EXPECT_EQ(pass.frames, 3U);
  EXPECT_EQ(pass.missed, 0U);
  EXPECT_EQ(tally.spans(slot_of('A')), 2U);
  EXPECT_EQ(tally.spans(slot_of('D')), 1U);
  EXPECT_EQ(tally.sum(slot_of('A'), 0), 200U);
  EXPECT_EQ(tally.sum(slot_of('A'), 1), 400U);
  EXPECT_EQ(tally.sum(slot_of('D'), 0), 100U);
  EXPECT_EQ(tally.spans(kNoMessageSlot), 0U);
}

TEST(TypedCounts, TheSpansTileTheWholePassReadsIncluded) {
  Trace trace;
  FakeBook book{.trace = &trace};
  TraceGroup group{.trace = &trace, .read_cost = 7};
  TypeTally tally{TraceGroup::event_count()};
  const std::vector<std::byte> input =
      frames({add(1, 'B', 100, 10), sized('P'), cancel(1, 10), remove(1), sized('S')});
  const TypedPass pass = typed_counts_pass(input, book, group, tally);
  // The first read opens the pass and is in no span. Every later one closes a span.
  EXPECT_EQ(pass.whole(0), 5U * (100 + 7));
  EXPECT_EQ(pass.whole(1), 2 * pass.whole(0));
  EXPECT_EQ(tally.total(0), pass.whole(0));
  EXPECT_EQ(tally.total(1), pass.whole(1));
  EXPECT_EQ(tally.sum(slot_of('X'), 0), 107U);
  EXPECT_TRUE(spans_tile(pass, tally));
}

TEST(TypedCounts, AFrameWalkedPastIsChargedToSlotZero) {
  Trace trace;
  FakeBook book{.trace = &trace};
  TraceGroup group{.trace = &trace, .read_cost = 3};
  TypeTally tally{TraceGroup::event_count()};
  const std::vector<std::byte> input = frames({type_only('z'), remove(1)});
  const TypedPass pass = typed_counts_pass(input, book, group, tally);
  EXPECT_EQ(tally.spans(kNoMessageSlot), 1U);
  EXPECT_EQ(tally.sum(kNoMessageSlot, 0), 3U);
  EXPECT_EQ(tally.sum(slot_of('D'), 0), 103U);
  EXPECT_TRUE(spans_tile(pass, tally));
}

TEST(TypedCounts, ATallyThatLosesOrAddsASpanDoesNotTile) {
  Trace trace;
  FakeBook book{.trace = &trace};
  TraceGroup group{.trace = &trace, .read_cost = 7};
  TypeTally tally{TraceGroup::event_count()};
  const std::vector<std::byte> input = frames({add(1, 'B', 100, 10), remove(1)});
  const TypedPass pass = typed_counts_pass(input, book, group, tally);
  ASSERT_TRUE(spans_tile(pass, tally));
  const std::array<std::uint64_t, 2> start{0, 0};
  const std::array<std::uint64_t, 2> now{1, 0};
  tally.add(slot_of('A'), now, start);
  EXPECT_FALSE(spans_tile(pass, tally));
}

TEST(TypedCounts, ReadsThatGiveNothingAreCountedAndRejectionsToo) {
  Trace trace;
  FakeBook book{.trace = &trace, .status = ApplyStatus::unknown_order};
  TraceGroup group{.trace = &trace, .read_cost = 0, .works = false};
  TypeTally tally{TraceGroup::event_count()};
  const std::vector<std::byte> input = frames({add(1, 'B', 100, 10), remove(1)});
  const TypedPass pass = typed_counts_pass(input, book, group, tally);
  EXPECT_EQ(pass.missed, 3U);
  EXPECT_EQ(pass.rejected, 2U);
}

TEST(TypedCounts, AnEmptySpanHoldsTheReadAndNothingElse) {
  Trace trace;
  TraceGroup group{.trace = &trace, .read_cost = 7};
  const EmptySpans empty = empty_spans(group, 1000);
  EXPECT_EQ(empty.spans, 1000U);
  EXPECT_EQ(empty.missed, 0U);
  EXPECT_EQ(empty.sums, (std::vector<std::uint64_t>{7000, 14000}));
}

// Records each stage it is asked to start, as type and stage: "A0" is stage 0 of an add.
struct StageRecorder {
  static constexpr unsigned kPrefetchStages = 4;
  std::array<char, 64>* started;
  std::size_t* count;
  void prefetch_stage(std::span<const std::byte> payload, unsigned stage) const noexcept {
    if (*count + 2 <= started->size()) {
      (*started)[(*count)++] = static_cast<char>(payload[0]);
      (*started)[(*count)++] = static_cast<char>('0' + stage);
    }
  }
};

TEST(StageFilter, OnlyTheStagesAndKindsNamedAreStarted) {
  const std::vector<std::byte> an_add = add(1, 'B', 100, 10);
  const std::vector<std::byte> a_delete = remove(1);
  const std::vector<std::byte> a_trade = sized('P');
  const auto run = [&](unsigned stages, StageKinds kinds) {
    std::array<char, 64> started{};
    std::size_t count = 0;
    const StageRecorder book{.started = &started, .count = &count};
    const StageFilter<StageRecorder> filter{book, stages, kinds};
    for (const auto* payload : {&an_add, &a_delete, &a_trade}) {
      for (unsigned stage = 0; stage < StageFilter<StageRecorder>::kPrefetchStages; ++stage) {
        filter.prefetch_stage(*payload, stage);
      }
    }
    return std::string(started.data(), count);
  };
  EXPECT_EQ(run(*parse_stages("2"), StageKinds::named), "D2");
  EXPECT_EQ(run(*parse_stages("03"), StageKinds::added), "A0A3");
  EXPECT_EQ(run(*parse_stages("0123"), StageKinds::all), "A0A1A2A3D0D1D2D3");
  EXPECT_FALSE(parse_stages("").has_value());
  EXPECT_FALSE(parse_stages("11").has_value());
  EXPECT_FALSE(parse_stages("8").has_value());
  EXPECT_EQ(parse_stage_kinds("named"), StageKinds::named);
  EXPECT_FALSE(parse_stage_kinds("both").has_value());
}

std::vector<std::byte> at_time(std::vector<std::byte> payload, std::uint64_t nanoseconds) {
  for (std::size_t at = 0; at < 6; ++at) {
    payload[5 + at] = static_cast<std::byte>((nanoseconds >> (8U * (5 - at))) & 0xFFU);
  }
  return payload;
}

TEST(OrderAge, EachNamedFrameIsChargedToItsTypeAndTheAgeOfItsOrder) {
  constexpr std::uint64_t kMs = 1'000'000;
  constexpr itch::BookCapacity kCapacity{
      .orders = 64, .levels = 64, .index_pages = 4, .index_bits = 16, .level_slots = 128};
  itch::FastBook<> book{kCapacity};
  const std::vector<std::byte> input = frames({
      at_time(add(1, 'B', 100, 10), 0),
      at_time(add(2, 'S', 100, 20), 900 * kMs),
      at_time(cancel(1, 10), kMs / 2),    // Order 1, half a millisecond old.
      at_time(cancel(2, 10), 950 * kMs),  // Order 2, 50 ms old, at 950 ms in the day.
      sized('S'),
      at_time(remove(1), 3000 * kMs),  // 3 s.
      type_only('z'),
      at_time(remove(9), 3000 * kMs),  // No such order.
  });
  const std::vector<std::uint8_t> slots = age_slots(input, book);
  const std::size_t x_row = *naming_row('X');
  const std::size_t d_row = *naming_row('D');
  ASSERT_EQ(slots.size(), 8U);
  EXPECT_EQ(slots[0], slot_of('A'));
  EXPECT_EQ(slots[1], slot_of('A'));
  EXPECT_EQ(slots[2], age_slot(x_row, AgeClass::under_1ms));
  EXPECT_EQ(slots[3], age_slot(x_row, AgeClass::under_100ms));
  EXPECT_EQ(slots[4], slot_of('S'));
  EXPECT_EQ(slots[5], age_slot(d_row, AgeClass::from_1s));
  EXPECT_EQ(slots[6], kNoMessageSlot);
  EXPECT_EQ(slots[7], age_slot(d_row, AgeClass::unknown));
  const auto named = named_slot(slots[3]);
  ASSERT_TRUE(named.has_value());
  EXPECT_EQ(named->type, 'X');
  EXPECT_EQ(named->age, AgeClass::under_100ms);
  EXPECT_FALSE(named_slot(slot_of('A')).has_value());
}

TEST(OrderAge, AReplacesNewOrderIsAgedFromTheReplace) {
  constexpr std::uint64_t kMs = 1'000'000;
  constexpr itch::BookCapacity kCapacity{
      .orders = 64, .levels = 64, .index_pages = 4, .index_bits = 16, .level_slots = 128};
  itch::FastBook<> book{kCapacity};
  const std::vector<std::byte> replace =
      itch::testing::encode(itch::OrderReplace{.header = header('U', 7),
                                               .original_order_reference = 1,
                                               .new_order_reference = 2,
                                               .shares = 10,
                                               .price = 101});
  const std::vector<std::byte> input =
      frames({at_time(add(1, 'B', 100, 10), 0), at_time(replace, 2000 * kMs),
              at_time(remove(2), (2000 * kMs) + 10)});
  const std::vector<std::uint8_t> slots = age_slots(input, book);
  ASSERT_EQ(slots.size(), 3U);
  EXPECT_EQ(slots[1], age_slot(*naming_row('U'), AgeClass::from_1s));
  EXPECT_EQ(slots[2], age_slot(*naming_row('D'), AgeClass::under_1ms));
}

}  // namespace
// NOLINTEND(bugprone-unchecked-optional-access)
