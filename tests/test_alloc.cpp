// The hot path allocates nothing, and the counter that says so can see an allocation.
//
// Each arm of the fast book takes a stream through the frame reader, every order path
// included, in a window that starts straight after construction: it has no warm-up to
// hide behind. The stream makes each arm take its rarer paths too: pages taken, given
// back and run out, references past the paged index's reach, and flat arrays moving to
// bigger blocks.
// The backtester takes a synthetic market with a strategy trading on it, in the same kind
// of window.
// The reference book takes the same stream and must allocate, and each form of new is
// shown reaching its own counter, so a broken counter fails here instead of passing the
// fast book. The map book, the T-DESIGN-1 baseline, allocates by design, and the heap ager
// that its aged run uses is shown making the calls it says it makes, from its seed alone.
#include <gtest/gtest.h>

#include <bit>
#include <cstddef>
#include <cstdint>
#include <new>
#include <print>
#include <random>
#include <span>
#include <string_view>
#include <type_traits>
#include <vector>

#include "backtest/joiner.hpp"
#include "backtest/market.hpp"
#include "itch/backtest.hpp"
#include "itch/book.hpp"
#include "itch/heap_ager.hpp"
#include "itch/lookahead.hpp"
#include "itch/map_book.hpp"
#include "itch/mm/marks.hpp"
#include "itch/mm/session.hpp"
#include "itch/mm/strategy.hpp"
#include "itch/reader.hpp"
#include "oracle/alloc_counter.hpp"
#include "oracle/reference_book.hpp"
#include "oracle/wire_builder.hpp"
#include "support/fixtures.hpp"

namespace {

using itch::oracle::AllocCounts;
using itch::oracle::AllocForm;
using itch::oracle::read_alloc_counts;
using itch::oracle::reset_alloc_counts;
namespace wire = itch::oracle::wire;

// Churns a fixed pool of orders through every order path many times over, so that each
// pool and index recycle, on a stream framed once before any window opens. One new
// reference in five has bit 40 set.
std::vector<std::byte> build_stream() {
  std::vector<std::byte> stream;
  const auto put = [&stream](const wire::Bytes& message) {
    itch::fixtures::append_frame(stream, message);
  };
  for (const auto& message : itch::fixtures::all_messages()) {
    const char type = static_cast<char>(message[0]);
    if (type != 'E' && type != 'C' && type != 'X' && type != 'D' && type != 'U') {
      put(message);
    }
  }
  // The same stream every run.
  // NOLINTNEXTLINE(cert-msc32-c,cert-msc51-cpp,bugprone-random-generator-seed)
  std::mt19937 random{20191230};
  constexpr std::uint32_t kPool = 512;
  std::vector<std::uint64_t> live;
  std::uint64_t next_reference = 1;
  const auto fresh = [&next_reference] {
    const std::uint64_t reference = next_reference++;
    return reference % 5 == 0 ? reference | (std::uint64_t{1} << 40U) : reference;
  };
  for (int step = 0; step < 200000; ++step) {
    const auto price = static_cast<std::uint32_t>(1000 + ((random() % 64) * 100));
    if (live.size() < kPool / 2) {
      const std::uint64_t reference = fresh();
      put((step % 3 == 0 ? wire::add_attributed : wire::add)(
          1, reference, step % 2 == 0 ? 'B' : 'S', 300, price, 1));
      live.push_back(reference);
      continue;
    }
    const std::size_t pick = random() % live.size();
    const std::uint64_t reference = live[pick];
    switch (random() % 5) {
      case 0: put(wire::execute(1, reference, 1)); break;
      case 1: put(wire::execute_with_price(1, reference, 1, 77)); break;
      case 2: put(wire::cancel(1, reference, 1)); break;
      case 3: {
        const std::uint64_t replaced = fresh();
        put(wire::replace(1, reference, replaced, 300, price));
        live[pick] = replaced;
        break;
      }
      default:
        put(wire::erase(1, reference));
        live[pick] = live.back();
        live.pop_back();
        break;
    }
  }
  return stream;
}

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
  if constexpr (std::is_same_v<Levels, itch::FlatLevels>) {
    EXPECT_GT(book.levels().relocations(), 0U) << "no flat array moved to a bigger block";
  }
}

TEST(Allocation, TheFastBookAllocatesNothingAfterConstruction) {
  expect_no_allocation<itch::HashIndex, itch::LinkedLevels>("hash, linked");
  expect_no_allocation<itch::HashIndex, itch::FlatLevels>("hash, flat");
  expect_no_allocation<itch::PagedIndex<>, itch::LinkedLevels>("paged, linked");
  expect_no_allocation<itch::PagedIndex<>, itch::FlatLevels>("paged, flat");
}

TEST(Allocation, TheFastBookLookingAheadAllocatesNothingAfterConstruction) {
  for (const unsigned prefetch : {4U, 16U, 64U}) {
    expect_no_allocation<itch::HashIndex, itch::LinkedLevels>("hash, linked", prefetch);
    expect_no_allocation<itch::HashIndex, itch::FlatLevels>("hash, flat", prefetch);
    expect_no_allocation<itch::PagedIndex<>, itch::LinkedLevels>("paged, linked", prefetch);
    expect_no_allocation<itch::PagedIndex<>, itch::FlatLevels>("paged, flat", prefetch);
  }
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
