// The hot path allocates nothing, and the counter that says so can see an allocation.
//
// Each arm of the fast book takes a stream through the frame reader, every order path
// included, in a window that starts straight after construction: it has no warm-up to
// hide behind. The stream makes each arm take its rarer paths too: pages taken, given
// back and run out, references past the paged index's reach, flat arrays moving to
// bigger blocks, and orders removed by an E, C or X of their whole remainder, which the
// reference book counts.
// The demo page's replay takes the same stream, through its timed call and through the
// animation's step(), which also reads each print.
// The backtester takes a synthetic market with a strategy trading on it, in the same kind
// of window. So do the SPSC ring and T-SPSC-3's pipelined replay, the window opening once
// its parser thread is waiting, and the UDP replay: the feed on its own, then the paced
// sender and S1 on loopback, the window opening once both threads are waiting.
// The reference book takes the same stream and must allocate, and each form of new is
// shown reaching its own counter, so a broken counter fails here instead of passing the
// fast book. The map book, the T-DESIGN-1 baseline, allocates by design, and the heap ager
// that its aged run uses is shown making the calls it says it makes, from its seed alone.
// The node book of T-ARENA-1 on a pool arm allocates nothing after construction either,
// and on every other arm is seen allocating: malloc once for each node, std::pmr's
// resources a chunk at a time, and mimalloc not through the global new at all, which is
// why itch_alloc counts mimalloc's calls by the book.
#include <gtest/gtest.h>

#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <new>
#include <print>
#include <random>
#include <span>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

#include "backtest/joiner.hpp"
#include "backtest/market.hpp"
#include "itch/backtest.hpp"
#include "itch/book.hpp"
#include "itch/demo_replay.hpp"
#include "itch/heap_ager.hpp"
#include "itch/lookahead.hpp"
#include "itch/map_book.hpp"
#include "itch/mm/marks.hpp"
#include "itch/mm/session.hpp"
#include "itch/mm/strategy.hpp"
#include "itch/mold_feed.hpp"
#include "itch/mold_packets.hpp"
#include "itch/node_arms.hpp"
#include "itch/node_book.hpp"
#include "itch/reader.hpp"
#include "itch/spsc.hpp"
#include "oracle/alloc_counter.hpp"
#include "oracle/reference_book.hpp"
#include "oracle/wire_builder.hpp"
#include "pipeline.hpp"
#include "support/churn.hpp"
#include "support/fixtures.hpp"
#include "udp/digest.hpp"
#include "udp/loopback.hpp"
#include "udp/modes.hpp"
#ifdef ITCH_HAVE_MIMALLOC
#include "itch/mimalloc_arm.hpp"
#endif

namespace {

using itch::oracle::AllocCounts;
using itch::oracle::AllocForm;
using itch::oracle::read_alloc_counts;
using itch::oracle::reset_alloc_counts;
using itch::testing::build_stream;

template <class Reader, class Apply>
std::uint64_t replay_with(Reader reader, Apply apply) {
  std::uint64_t messages = 0;
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    apply(frame.payload);
    ++messages;
  }
  return messages;
}

// Few pages and a short reach, so that the paged index runs out and falls back, and
// first blocks of 8, so that the flat arrays move as they fill.
constexpr itch::BookCapacity kCapacity{
    .orders = 2048, .levels = 2048, .index_pages = 4, .index_bits = 20, .level_slots = 4096};

template <class Apply>
std::uint64_t replay(std::span<const std::byte> stream, Apply apply) {
  return replay_with(itch::FrameReader{stream}, apply);
}

// With `prefetch` messages of look-ahead (itch/lookahead.hpp), the reader is made inside
// the window, so that its ring and every stage it asks of the book are counted too.
template <class Index, class Levels>
void expect_no_allocation(std::string_view name, unsigned prefetch = 0) {
  SCOPED_TRACE(name);
  SCOPED_TRACE(prefetch);
  using Book = itch::FastBook<itch::NoWriteObserver, Index, Levels>;
  const std::vector<std::byte> stream = build_stream();
  Book book{kCapacity};
  std::uint64_t refused = 0;
  reset_alloc_counts();
  const std::uint64_t messages =
      replay_with(itch::LookaheadReader<Book>{stream, book, prefetch},
                  [&book, &refused](std::span<const std::byte> payload) {
                    const itch::ApplyStatus status = book.apply(payload);
                    refused += status == itch::ApplyStatus::applied ||
                                       status == itch::ApplyStatus::no_book_effect
                                   ? 0U
                                   : 1U;
                  });
  const AllocCounts counts = read_alloc_counts();
  std::println("fast book, {}, look-ahead {}: {} messages, {} refused, {} allocations", name,
               prefetch, messages, refused, counts.total());
  EXPECT_EQ(counts.total(), 0U) << "allocation in the hot path";
  EXPECT_EQ(refused, 0U) << "the stream did not exercise the book";
  EXPECT_GT(messages, 200000U);
  if constexpr (std::is_same_v<Index, itch::PagedIndex<>>) {
    EXPECT_GT(book.index().fallback_inserts(), 0U) << "the paged index never fell back";
    EXPECT_EQ(book.index().peak_live_pages(), 4U) << "the paged index never ran out of pages";
  }
  if constexpr (requires { book.levels().relocations(); }) {
    EXPECT_GT(book.levels().relocations(), 0U) << "no flat array moved to a bigger block";
  }
}

TEST(Allocation, TheFastBookAllocatesNothingAfterConstruction) {
  expect_no_allocation<itch::HashIndex, itch::LinkedLevels>("hash, linked");
  expect_no_allocation<itch::HashIndex, itch::FlatLevels>("hash, flat");
  expect_no_allocation<itch::PagedIndex<>, itch::LinkedLevels>("paged, linked");
  expect_no_allocation<itch::PagedIndex<>, itch::FlatLevels>("paged, flat");
  expect_no_allocation<itch::IdentityHashIndex, itch::FlatLevels>("identity, flat");
  expect_no_allocation<itch::ChainedIndex, itch::FlatLevels>("chained, flat");
  expect_no_allocation<itch::PagedIndex<>, itch::Scan16FlatLevels>("paged, flat-scan16");
  expect_no_allocation<itch::PagedIndex<>, itch::BisectFlatLevels>("paged, flat-bisect");
}

// The stream every window here is built on removes orders by every message that can: an E,
// a C or an X of an order's whole remainder, besides D and U. Counted by the reference book,
// which shares no code with the fast book: the messages after which it holds one order fewer.
TEST(Allocation, TheChurnStreamEmptiesOrdersByEveryExecutionAndCancel) {
  const std::vector<std::byte> stream = build_stream();
  itch::oracle::ReferenceBook reference;
  std::array<std::uint64_t, 3> emptied{};
  constexpr std::string_view kTypes = "ECX";
  replay(stream, [&reference, &emptied, kTypes](std::span<const std::byte> payload) {
    const std::size_t before = reference.live_orders();
    static_cast<void>(reference.apply(payload));
    const std::size_t at = kTypes.find(static_cast<char>(payload[0]));
    if (at != std::string_view::npos && reference.live_orders() + 1 == before) {
      ++emptied.at(at);
    }
  });
  std::println("churn stream: orders emptied by E {}, by C {}, by X {}", emptied[0], emptied[1],
               emptied[2]);
  for (std::size_t at = 0; at < kTypes.size(); ++at) {
    EXPECT_GE(emptied.at(at), 1000U) << "too few orders emptied by " << kTypes[at];
  }
}

TEST(Allocation, TheFastBookLookingAheadAllocatesNothingAfterConstruction) {
  for (const unsigned prefetch : {4U, 16U, 64U}) {
    expect_no_allocation<itch::HashIndex, itch::LinkedLevels>("hash, linked", prefetch);
    expect_no_allocation<itch::HashIndex, itch::FlatLevels>("hash, flat", prefetch);
    expect_no_allocation<itch::PagedIndex<>, itch::LinkedLevels>("paged, linked", prefetch);
    expect_no_allocation<itch::PagedIndex<>, itch::FlatLevels>("paged, flat", prefetch);
    expect_no_allocation<itch::IdentityHashIndex, itch::FlatLevels>("identity, flat", prefetch);
    expect_no_allocation<itch::ChainedIndex, itch::FlatLevels>("chained, flat", prefetch);
  }
}

// The demo's replay, whose header the page's engine compiles too: replay(), the call
// T-WASM-1 times, and step(), which looks up each executed order before applying it.
TEST(Allocation, TheDemoReplayAllocatesNothingAfterConstruction) {
  const std::vector<std::byte> stream = build_stream();
  itch::demo::Replay whole{stream, kCapacity};
  itch::demo::Replay stepped{stream, kCapacity};
  reset_alloc_counts();
  const std::uint64_t replayed = whole.replay();
  std::uint64_t stepped_through = 0;
  while (!stepped.done()) {
    stepped_through += stepped.step(1000);
  }
  const AllocCounts counts = read_alloc_counts();
  std::println("demo replay: {} messages, {} prints, {} allocations", replayed, stepped.prints(),
               counts.total());
  EXPECT_EQ(counts.total(), 0U) << "allocation in the hot path";
  EXPECT_GT(replayed, 200000U);
  EXPECT_EQ(stepped_through, replayed);
  EXPECT_GT(stepped.prints(), 0U) << "the stream made no print";
}

// The backtester, with a strategy that sends, replaces, cancels and takes on every path,
// over a synthetic market long enough that every ring wraps and every pool recycles. The
// window opens straight after construction, as the book's does, and covers finish(): the
// session's close and its reports.
TEST(Allocation, TheBacktesterAllocatesNothingAfterConstruction) {
  const auto messages = itch::backtest::synthetic::RandomMarket{20191230}.generate(200'000);
  const std::vector<std::byte> stream = itch::backtest::synthetic::frame(messages);
  itch::backtest::Config config;
  config.date = 20191230;
  config.symbols = {itch::backtest::synthetic::kSymbol};
  config.latency = {.market_data = 3'000, .order_entry = 2'000, .response = 2'500, .compute = 500};
  config.exchange_capacity = {.orders = 1U << 17U,
                              .levels = 1024,
                              .index_pages = 64,
                              .index_bits = 18,
                              .level_slots = 1U << 14U};
  config.view_capacity = config.exchange_capacity;
  config.max_orders = 64;
  config.queue_capacity = 1024;
  itch::backtest::Engine<> engine{config};
  itch::backtest::synthetic::Joiner joiner;
  reset_alloc_counts();
  const std::uint64_t messages_fed = engine.run(stream, joiner);
  const AllocCounts counts = read_alloc_counts();
  const auto& stats = engine.stats();
  const auto& made = joiner.counts();
  std::println(
      "backtester: {} messages, {} decisions, {} fills ({} maker, {} taker), {} sends, {} "
      "replaces, {} cancels, {} takes, {} allocations",
      messages_fed, stats.decisions, stats.fills, stats.maker_fills, stats.taker_fills, made.sends,
      made.replaces, made.cancels, made.takes, counts.total());
  EXPECT_EQ(counts.total(), 0U) << "allocation in the backtester's replay";
  EXPECT_EQ(messages_fed, messages.size());
  EXPECT_EQ(stats.refused_messages, 0U);
  EXPECT_EQ(stats.overflows, 0U);
  EXPECT_GT(stats.maker_fills, 1000U);
  EXPECT_GT(stats.taker_fills, 1000U);
  EXPECT_GT(made.replaces, 1000U);
  EXPECT_GT(made.cancels, 1000U);
  // More deliveries than one ring holds, so each ring wrapped.
  EXPECT_GT(stats.deliveries, 100U * config.queue_capacity);
}

// The market maker on the same market, with both models, each switch and the mark recorder: the
// replay, the strategy's quoting and fill log, and the path, all after construction. The
// attribution itself runs after the session and is not on this path.
TEST(Allocation, TheMarketMakerAllocatesNothingAfterConstruction) {
  const auto messages = itch::backtest::synthetic::RandomMarket{20191231}.generate(200'000);
  const std::vector<std::byte> stream = itch::backtest::synthetic::frame(messages);
  itch::backtest::Config config;
  config.date = 20191230;
  config.symbols = {itch::backtest::synthetic::kSymbol};
  config.latency = {.market_data = 3'000, .order_entry = 2'000, .response = 2'500, .compute = 500};
  config.exchange_capacity = {.orders = 1U << 17U,
                              .levels = 1024,
                              .index_pages = 64,
                              .index_bits = 18,
                              .level_slots = 1U << 14U};
  config.view_capacity = config.exchange_capacity;
  config.max_orders = 64;
  config.queue_capacity = 1024;
  const itch::mm::ModelParameters parameters{.gamma = 0.1, .sigma = 5.0, .k = 1.5, .a = 140.0};
  const itch::mm::QuoterConfig quoting{.symbol = itch::backtest::synthetic::kSymbol,
                                       .lot = 100,
                                       .tick = 100,
                                       .max_lots = 5,
                                       .start = 0,
                                       .stop = 1'000'000'000,
                                       .max_fills = 1U << 16U};
  const auto check = [&](const auto& model, std::string_view name,
                         std::uint64_t least_fills = 1000) {
    itch::backtest::Engine<> engine{config};
    itch::mm::Quoter maker{quoting, model};
    itch::mm::MarkRecorder marks{itch::backtest::synthetic::kSymbol, 1U << 20U};
    reset_alloc_counts();
    const std::uint64_t fed = itch::mm::replay(engine, stream, maker, marks);
    const AllocCounts counts = read_alloc_counts();
    std::println(
        "market maker, {}: {} messages, {} fills, {} sends, {} replaces, {} cancels, {} "
        "mark points, {} allocations",
        name, fed, maker.counts().fills, maker.counts().sends, maker.counts().replaces,
        maker.counts().cancels, marks.points().size(), counts.total());
    EXPECT_EQ(counts.total(), 0U) << "allocation in the market maker's replay, " << name;
    EXPECT_EQ(fed, messages.size());
    EXPECT_EQ(engine.stats().overflows, 0U);
    EXPECT_EQ(marks.overflows(), 0U);
    EXPECT_EQ(maker.counts().fill_overflows, 0U);
    EXPECT_GT(maker.counts().fills, least_fills) << name;
    EXPECT_GT(maker.counts().replaces, 1000U) << name;
  };
  check(itch::mm::AsModel{parameters, 1'000'000'000}, "Avellaneda-Stoikov");
  check(itch::mm::GlftModel{parameters, 5}, "GLFT");
  // The switches of the with-and-without runs, each on its own.
  config.queue = itch::backtest::QueueModel::conservative;
  check(itch::mm::AsModel{parameters, 1'000'000'000}, "Avellaneda-Stoikov, Q-off conservative");
  config.queue = itch::backtest::QueueModel::proportional;
  check(itch::mm::AsModel{parameters, 1'000'000'000}, "Avellaneda-Stoikov, Q-off proportional");
  config.queue = itch::backtest::QueueModel::exact;
  config.latency_off = true;
  // With no latency its quotes keep up with the mid, and this market fills them less often.
  check(itch::mm::AsModel{parameters, 1'000'000'000}, "Avellaneda-Stoikov, L-off", 500);
}

// The SPSC ring's single and batch calls on one thread, then T-SPSC-3's pipelined replay:
// a parser thread pushing decoded ops through a small ring to the paged-flat book on this
// one, the window opening once the parser thread is started and waiting.
TEST(Allocation, TheRingAndThePipelineAllocateNothingAfterConstruction) {
  itch::SpscRing<std::uint64_t> ring{64};
  std::array<std::uint64_t, 16> batch{};
  std::uint64_t moved = 0;
  reset_alloc_counts();
  for (std::uint64_t round = 0; round < 10'000; ++round) {
    moved += ring.try_push(round) ? 1U : 0U;
    moved += ring.try_push_batch(batch);
    moved += ring.try_pop_batch(batch);
    moved += ring.try_pop(batch[0]) ? 1U : 0U;
  }
  const AllocCounts by_ring = read_alloc_counts();
  std::println("ring: {} items moved, {} allocations", moved, by_ring.total());
  EXPECT_EQ(by_ring.total(), 0U) << "allocation in the ring";
  EXPECT_GT(moved, 100U * ring.capacity());

  // The checkpoint's hash walk, mid-stream, runs inside the counted span too.
  constexpr std::uint64_t kCheckpointOp = 100'000;
  const std::vector<std::byte> stream = build_stream();
  itch::FastBook<> book{kCapacity};
  itch::bench::OpRing ops{64};
  const itch::bench::ReplayCounts counts = itch::bench::replay_pipelined(
      stream, book, ops, -1, [] { reset_alloc_counts(); }, kCheckpointOp);
  const AllocCounts by_pipeline = read_alloc_counts();
  std::println("pipeline: {} messages, {} ops, {} full waits, {} allocations", counts.messages,
               counts.ops, counts.full_waits, by_pipeline.total());
  EXPECT_EQ(by_pipeline.total(), 0U) << "allocation in the pipelined replay";
  EXPECT_EQ(counts.of(itch::ApplyStatus::applied), counts.ops);
  EXPECT_GT(counts.ops, 100U * ops.capacity()) << "the ring did not wrap";
  EXPECT_TRUE(counts.checkpoint_reached) << "the checkpoint's hash walk was not counted";
}

// The UDP replay's two loops. First the feed alone, every packet of the churn stream
// through the framing, the sequence check and the book. Then the same packets down a
// loopback pair: the sender paced on its thread, S1 on its own, the book's hash taken at
// a checkpoint on the receiver's thread, all inside the window.
TEST(Allocation, TheUdpReplayAllocatesNothingAfterConstruction) {
  const std::vector<std::byte> stream = build_stream();
  const auto packets = itch::mold::build_packets(stream, {});
  ASSERT_TRUE(packets.has_value());

  itch::FastBook<> fed{kCapacity};
  std::uint64_t refused = 0;
  const auto apply = [&fed, &refused](const itch::Frame& frame) {
    const itch::ApplyStatus status = fed.apply(frame.payload);
    refused += status == itch::ApplyStatus::applied || status == itch::ApplyStatus::no_book_effect
                   ? 0U
                   : 1U;
  };
  itch::mold::FeedHandler feed{packets->plan().session, 1, apply};
  reset_alloc_counts();
  for (std::size_t i = 0; i < packets->size(); ++i) {
    feed.on_datagram(packets->packet(i));
  }
  const AllocCounts by_feed = read_alloc_counts();
  std::println("udp feed: {} packets, {} frames, {} refused, {} allocations", packets->size(),
               feed.counts().frames, refused, by_feed.total());
  EXPECT_EQ(by_feed.total(), 0U) << "allocation in the feed";
  EXPECT_EQ(feed.counts().frames, packets->messages());
  EXPECT_EQ(refused, 0U);

  itch::FastBook<> received{kCapacity};
  itch::bench::udp::BookDigest digest{received, packets->messages() / 2};
  const auto run = itch::bench::udp::run_loopback_s1(
      *packets, {.send = {.messages_per_second = 2'000'000}}, digest, [] { reset_alloc_counts(); });
  const AllocCounts by_loopback = read_alloc_counts();
  ASSERT_TRUE(run.has_value());
  ASSERT_TRUE(run->sender.report.has_value());
  std::println("udp loopback: {} packets sent, {} datagrams, {} frames, {} lost, {} allocations",
               run->sender.report->sent_packets, run->mode.datagrams, run->feed.frames,
               run->sequence.lost, by_loopback.total());
  EXPECT_EQ(by_loopback.total(), 0U) << "allocation in the sender or the receiver";
  EXPECT_EQ(run->sender.report->sent_packets, packets->size());
  EXPECT_GT(run->mode.datagrams, packets->size() / 2) << "the receiver took too few packets";
  EXPECT_TRUE(digest.finish().checkpoint_reached) << "the checkpoint's hash walk was not counted";
}

// Every receive mode this build has, S1 to S4 on Linux and U1 to U5 with liburing, on the
// same paced replay: the mode's wait() and release() inside the window with the sender and
// the book. Each mode's buffers and ring are made before the window opens.
TEST(Allocation, EveryReceiveModeAllocatesNothingAfterConstruction) {
  const std::vector<std::byte> stream = build_stream();
  const auto packets = itch::mold::build_packets(stream, {});
  ASSERT_TRUE(packets.has_value());
  std::size_t modes = 0;
  for (const std::string_view id : itch::bench::udp::kModeIds) {
    if (!itch::bench::udp::mode_built(id)) {
      continue;
    }
    ++modes;
    itch::FastBook<> received{kCapacity};
    itch::bench::udp::BookDigest digest{received, packets->messages() / 2};
    const auto run = itch::bench::udp::with_mode(id, {}, [&](auto make) {
      return itch::bench::udp::run_loopback(*packets, {.send = {.messages_per_second = 2'000'000}},
                                            make, digest, [] { reset_alloc_counts(); });
    });
    const AllocCounts by_mode = read_alloc_counts();
    ASSERT_TRUE(run.has_value()) << id;
    // NOLINTNEXTLINE(bugprone-unchecked-optional-access): the assertion above guards it.
    const auto& outcome = *run;
    ASSERT_TRUE(outcome.has_value()) << id << ": " << outcome.error().call;
    std::println("udp {}: {} datagrams, {} frames, {} lost, {} allocations", id,
                 outcome->mode.datagrams, outcome->feed.frames, outcome->sequence.lost,
                 by_mode.total());
    EXPECT_EQ(by_mode.total(), 0U) << "allocation in mode " << id;
    EXPECT_GT(outcome->mode.datagrams, packets->size() / 2) << id << " took too few packets";
  }
  EXPECT_GE(modes, 1U);
}

TEST(Allocation, TheControlBookIsSeenAllocating) {
  const std::vector<std::byte> stream = build_stream();
  itch::oracle::ReferenceBook book;
  reset_alloc_counts();
  const std::uint64_t messages = replay(stream, [&book](std::span<const std::byte> payload) {
    static_cast<void>(book.apply(payload));
  });
  const AllocCounts counts = read_alloc_counts();
  std::println("control book: {} messages, {} allocations", messages, counts.total());
  EXPECT_GT(counts.total(), messages / 4) << "control book made almost no allocation";
}

TEST(Allocation, TheMapBookAllocatesOnTheHotPath) {
  const std::vector<std::byte> stream = build_stream();
  itch::MapBook book;
  reset_alloc_counts();
  std::uint64_t refused = 0;
  const std::uint64_t messages =
      replay(stream, [&book, &refused](std::span<const std::byte> payload) {
        const itch::ApplyStatus status = book.apply(payload);
        refused +=
            status == itch::ApplyStatus::applied || status == itch::ApplyStatus::no_book_effect
                ? 0U
                : 1U;
      });
  const AllocCounts counts = read_alloc_counts();
  std::println("map book: {} messages, {} allocations", messages, counts.total());
  EXPECT_EQ(refused, 0U);
  EXPECT_GT(counts.total(), messages / 4) << "the map book made almost no allocation";
}

// The churn stream through a node book on one arm, counted from straight after
// construction. Returns the global new's calls and the book's nodes made.
template <class Arm>
std::pair<std::uint64_t, std::uint64_t> node_book_counts(std::string_view name) {
  SCOPED_TRACE(name);
  const std::vector<std::byte> stream = build_stream();
  itch::NodeBook<Arm> book{
      {.orders = 2048, .levels = 2048, .pages = itch::PageRequest::huge_if_offered}};
  std::uint64_t refused = 0;
  reset_alloc_counts();
  const std::uint64_t messages =
      replay(stream, [&book, &refused](std::span<const std::byte> payload) {
        const itch::ApplyStatus status = book.apply(payload);
        refused +=
            status == itch::ApplyStatus::applied || status == itch::ApplyStatus::no_book_effect
                ? 0U
                : 1U;
      });
  const AllocCounts counts = read_alloc_counts();
  std::println("node book, {}: {} messages, {} refused, {} allocations, {} nodes made", name,
               messages, refused, counts.total(), book.nodes_made());
  EXPECT_EQ(refused, 0U) << "the stream did not exercise the book";
  EXPECT_GT(messages, 200000U);
  EXPECT_GT(book.nodes_made(), messages / 4);
  return {counts.total(), book.nodes_made()};
}

TEST(Allocation, TheNodeBookOnAPoolAllocatesNothingAfterConstruction) {
  EXPECT_EQ(node_book_counts<itch::PoolIndexArm>("pool-index").first, 0U);
  EXPECT_EQ(node_book_counts<itch::PoolPointerArm>("pool-pointer").first, 0U);
  EXPECT_EQ(node_book_counts<itch::PoolFifoArm>("pool-fifo").first, 0U);
}

TEST(Allocation, TheNodeBookOnAHeapIsSeenAllocating) {
  const auto [malloc_calls, malloc_nodes] = node_book_counts<itch::MallocArm>("malloc");
  EXPECT_EQ(malloc_calls, malloc_nodes) << "malloc's arm takes one call for each node";
  const auto [pool_calls, pool_nodes] = node_book_counts<itch::PmrPoolArm>("pmr-pool");
  EXPECT_GT(pool_calls, 0U);
  EXPECT_LT(pool_calls, pool_nodes) << "a pool resource takes its upstream a chunk at a time";
  const auto [mono_calls, mono_nodes] = node_book_counts<itch::PmrMonotonicArm>("pmr-monotonic");
  EXPECT_GT(mono_calls, 0U);
  EXPECT_LT(mono_calls, mono_nodes);
#ifdef ITCH_HAVE_MIMALLOC
  const auto [mi_calls, mi_nodes] = node_book_counts<itch::MimallocArm>("mimalloc");
  EXPECT_EQ(mi_calls, 0U) << "mi_new reached the global new's counter";
  EXPECT_GT(mi_nodes, 0U);
#endif
}

TEST(HeapAgeing, WarmUpLeavesHalfItsBlocksAsBallast) {
  const itch::HeapAger ager{{.seed = 1, .warm_blocks = 1000, .ring_blocks = 10}};
  EXPECT_EQ(ager.allocations(), 1000U);
  EXPECT_EQ(ager.releases(), 500U);
  EXPECT_GE(ager.held_bytes(), 500U * itch::HeapAger::kSizeStep);
  EXPECT_LE(ager.held_bytes(), 500U * itch::HeapAger::kSizeStep * itch::HeapAger::kSizeSteps);
}

TEST(HeapAgeing, EachMessageThatAddsAnOrderMovesOneBlockThroughNew) {
  const auto messages = itch::fixtures::all_messages();
  itch::HeapAger ager{{.seed = 1, .warm_blocks = 0, .ring_blocks = 4}};
  reset_alloc_counts();
  for (const auto& message : messages) {
    ager.before_message(message);
  }
  // all_messages() holds one of each of the 23 types: A, F and U add an order.
  EXPECT_EQ(read_alloc_counts().total(), 3U);
  EXPECT_EQ(ager.allocations(), 3U);
  for (int i = 0; i < 100; ++i) {
    ager.step();
  }
  EXPECT_EQ(read_alloc_counts().total(), 103U);
  // A ring of four never holds more than four blocks.
  EXPECT_GE(ager.releases(), 99U);
  EXPECT_LE(ager.allocations() - ager.releases(), 4U);
}

TEST(HeapAgeing, TheSeedAloneFixesEveryDraw) {
  constexpr itch::HeapAgeing kAgeing{.seed = 5, .warm_blocks = 256, .ring_blocks = 16};
  itch::HeapAger first{kAgeing};
  itch::HeapAger second{kAgeing};
  itch::HeapAger other{{.seed = 6, .warm_blocks = 256, .ring_blocks = 16}};
  EXPECT_EQ(first.peek(), second.peek());
  EXPECT_NE(first.peek(), other.peek());
  for (int i = 0; i < 100; ++i) {
    first.step();
    second.step();
    other.step();
  }
  EXPECT_EQ(first.peek(), second.peek());
  EXPECT_EQ(first.held_bytes(), second.held_bytes());
  EXPECT_EQ(first.releases(), second.releases());
  EXPECT_NE(first.peek(), other.peek());
}

// A plain call to the allocation function, which no compiler may remove as it may a
// new-expression.
TEST(Allocation, EveryFormOfNewReachesItsOwnCounter) {
  const auto expect_one = [](AllocForm form) {
    const AllocCounts counts = read_alloc_counts();
    EXPECT_EQ(counts.of(form), 1U) << itch::oracle::kAllocFormNames[static_cast<std::size_t>(form)];
    EXPECT_EQ(counts.total(), 1U) << itch::oracle::kAllocFormNames[static_cast<std::size_t>(form)];
    reset_alloc_counts();
  };
  constexpr std::align_val_t kAlign{128};

  reset_alloc_counts();
  ::operator delete(::operator new(16));
  expect_one(AllocForm::plain);
  ::operator delete[](::operator new[](16));
  expect_one(AllocForm::array);
  ::operator delete(::operator new(16, std::nothrow));
  expect_one(AllocForm::nothrow);
  ::operator delete[](::operator new[](16, std::nothrow));
  expect_one(AllocForm::array_nothrow);
  ::operator delete(::operator new(16, kAlign), kAlign);
  expect_one(AllocForm::aligned);
  ::operator delete[](::operator new[](16, kAlign), kAlign);
  expect_one(AllocForm::array_aligned);
  ::operator delete(::operator new(16, kAlign, std::nothrow), kAlign);
  expect_one(AllocForm::aligned_nothrow);
  ::operator delete[](::operator new[](16, kAlign, std::nothrow), kAlign);
  expect_one(AllocForm::array_aligned_nothrow);
}

TEST(Allocation, AnAlignedRequestIsAligned) {
  constexpr std::align_val_t kAlign{4096};
  void* memory = ::operator new(16, kAlign);
  EXPECT_EQ(std::bit_cast<std::uintptr_t>(memory) % 4096, 0U);
  ::operator delete(memory, kAlign);
}

}  // namespace
