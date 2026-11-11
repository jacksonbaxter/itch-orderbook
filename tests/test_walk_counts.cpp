// bench_walks' counted engine: a hand-built book whose walks are counted by hand, and the
// counted engine against the engine on random markets.
//
// The hand-built book: four simulated bids, A at $10.00, B at $9.99, C at $9.98 and D at
// $9.99 again, arrive in that order with every latency zero, before any real order. Then
// real messages at prices chosen so that each walk stops at a known place. Every expected
// count below is worked out in the comment beside it, not taken from the code.
#include <gtest/gtest.h>

#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "backtest/joiner.hpp"
#include "backtest/market.hpp"
#include "backtest/scripted.hpp"
#include "itch/backtest.hpp"
#include "oracle/wire_builder.hpp"
#include "walk_counts.hpp"
#include "walk_engine.hpp"

namespace {

using itch::Price4;
using itch::Side;
using itch::backtest::Config;
using itch::backtest::Latency;
using itch::backtest::QueueModel;
using itch::backtest::Stats;
using itch::backtest::synthetic::Joiner;
using itch::backtest::synthetic::kLocate;
using itch::backtest::synthetic::kSymbol;
using itch::backtest::synthetic::Message;
using itch::backtest::synthetic::RandomMarket;
using itch::backtest::synthetic::Scripted;
using itch::bench::WalkCounts;
using itch::bench::WalkSite;
namespace wire = itch::oracle::wire;

using Counted = itch::backtest::counted::Engine<>;

constexpr Price4 k950 = 95'000;
constexpr Price4 k997 = 99'700;
constexpr Price4 k998 = 99'800;
constexpr Price4 k999 = 99'900;
constexpr Price4 k1000 = 100'000;

constexpr int kA = 1;
constexpr int kB = 2;
constexpr int kC = 3;
constexpr int kD = 4;

Config config(QueueModel queue = QueueModel::exact, bool latency_off = true) {
  Config config;
  config.date = 20191230;
  config.latency = Latency{.market_data = 100, .order_entry = 50, .response = 70, .compute = 10};
  config.latency_off = latency_off;
  config.queue = queue;
  config.symbols = {kSymbol};
  config.exchange_capacity = {
      .orders = 1024, .levels = 256, .index_pages = 4, .index_bits = 16, .level_slots = 4096};
  config.view_capacity = config.exchange_capacity;
  config.max_orders = 64;
  config.queue_capacity = 256;
  return config;
}

struct Expected {
  std::uint64_t walks;
  std::uint64_t nonempty;
  std::uint64_t level_steps;
  std::uint64_t order_steps;
};

void expect_site(const WalkCounts& walks, WalkSite site, const Expected& expected) {
  const WalkCounts::Site& got = walks.site(site);
  const auto name = itch::bench::kWalkSiteNames[static_cast<std::size_t>(site)];
  EXPECT_EQ(got.walks, expected.walks) << name;
  EXPECT_EQ(got.nonempty, expected.nonempty) << name;
  EXPECT_EQ(got.level_steps, expected.level_steps) << name;
  EXPECT_EQ(got.order_steps, expected.order_steps) << name;
}

class HandBuilt : public ::testing::Test {
 protected:
  HandBuilt() : engine_{config()} {
    strategy_.send(kA, 10, Side::bid, k1000, 100);
    strategy_.send(kB, 20, Side::bid, k999, 100);
    strategy_.send(kC, 30, Side::bid, k998, 100);
    strategy_.send(kD, 40, Side::bid, k999, 100);
    feed(wire::directory(kLocate, 'Q', 'P', 100, 1));
  }

  void feed(const wire::Bytes& message) { engine_.feed(message, strategy_); }
  void finish() { engine_.finish(strategy_); }
  [[nodiscard]] const WalkCounts& walks() const { return engine_.walks(); }
  [[nodiscard]] const Stats& stats() const { return engine_.stats(); }

 private:
  Counted engine_;
  Scripted strategy_;
};

// Each order's rest walks the bid side from $10.00: A finds it empty, no step; B steps on A,
// better, and stops at the end, 1; C steps on A and B, 2; D steps on A, better, then B, at
// its price, where the loop stops, 2. Depths 0, 1, 2 and 3 levels.
TEST_F(HandBuilt, EachRestWalksTheLevelsBetterThanItsPrice) {
  feed(wire::add(kLocate, 1, 'B', 100, k997, 100));
  expect_site(walks(), WalkSite::rest,
              {.walks = 4, .nonempty = 3, .level_steps = 5, .order_steps = 0});
  const WalkCounts::Site& rest = walks().site(WalkSite::rest);
  for (std::size_t depth = 0; depth < 4; ++depth) {
    EXPECT_EQ(rest.depth[depth], 1U) << "depth " << depth;
  }
  EXPECT_EQ(rest.steps[0], 1U);
  EXPECT_EQ(rest.steps[1], 1U);
  EXPECT_EQ(rest.steps[2], 2U);
  // The orders resting at each walk's start: 0, 1, 2 and 3.
  EXPECT_EQ(rest.orders_on_side, 6U);
  const std::size_t bids = itch::side_key(kLocate, Side::bid);
  EXPECT_EQ(walks().levels_on(bids), 3U);
  EXPECT_EQ(walks().orders_on(bids), 4U);
}

// A real add can cross only the other side, here the empty ask side: one walk, no step.
TEST_F(HandBuilt, AnAddWalksTheOtherSide) {
  feed(wire::add(kLocate, 1, 'B', 100, k997, 100));
  expect_site(walks(), WalkSite::cross,
              {.walks = 1, .nonempty = 0, .level_steps = 0, .order_steps = 0});
  EXPECT_EQ(walks().site(WalkSite::cross).depth[0], 1U);
}

// A delete at $9.97 finds every level better and stops at the end: 3 steps, no order. A
// cancel at $9.99 steps on A, then B, found, and visits B's orders, B and D: 2 steps and 2.
TEST_F(HandBuilt, ADepletionFindsItsLevelThenVisitsItsOrders) {
  feed(wire::add(kLocate, 1, 'B', 100, k997, 100));
  feed(wire::erase(kLocate, 1, 110));
  expect_site(walks(), WalkSite::depletion,
              {.walks = 1, .nonempty = 1, .level_steps = 3, .order_steps = 0});
  feed(wire::add(kLocate, 2, 'B', 100, k999, 120));
  feed(wire::cancel(kLocate, 2, 50, 130));
  expect_site(walks(), WalkSite::depletion,
              {.walks = 2, .nonempty = 2, .level_steps = 5, .order_steps = 2});
  EXPECT_EQ(walks().site(WalkSite::depletion).depth[3], 2U);
}

// A print at $9.98 finds C on the bid side in 3 steps and the ask side is not looked at. A
// print at $9.50 steps past all 3 bid levels, then finds the ask side empty: 2 walks.
TEST_F(HandBuilt, AHiddenPrintLooksOnEachSideUntilItFindsALevel) {
  feed(wire::add(kLocate, 1, 'B', 100, k997, 100));
  feed(wire::trade(kLocate, 100, k998, 140));
  expect_site(walks(), WalkSite::hidden_print,
              {.walks = 1, .nonempty = 1, .level_steps = 3, .order_steps = 0});
  feed(wire::trade(kLocate, 100, k950, 145));
  expect_site(walks(), WalkSite::hidden_print,
              {.walks = 3, .nonempty = 2, .level_steps = 6, .order_steps = 0});
}

// An execution of a real bid at $9.99 visits A, better, with its one order, which fills 50;
// then B, at the price, with B and D, which the 50 shares no longer reach; then C, worse,
// where it stops: 3 steps and 3 orders.
TEST_F(HandBuilt, AnExecutionWalksEveryLevelAtOrBetterThanItsPrice) {
  feed(wire::add(kLocate, 2, 'B', 50, k999, 120));
  feed(wire::execute(kLocate, 2, 50, 150));
  expect_site(walks(), WalkSite::execution,
              {.walks = 1, .nonempty = 1, .level_steps = 3, .order_steps = 3});
  EXPECT_EQ(stats().fills, 1U);
  EXPECT_EQ(stats().filled_shares, 50U);
}

// A late add, reference 5 after 10 was shown, at $9.99: the find steps on A, then B, found,
// and its shares join `ahead` of B and D, 2 orders.
TEST_F(HandBuilt, ALateAddFindsItsLevelAndVisitsItsOrders) {
  feed(wire::add(kLocate, 10, 'B', 100, k950, 160));
  feed(wire::add(kLocate, 5, 'B', 100, k999, 170));
  expect_site(walks(), WalkSite::late_add,
              {.walks = 1, .nonempty = 1, .level_steps = 2, .order_steps = 2});
  EXPECT_EQ(stats().late_adds_ahead, 1U);
}

// At the end every order expires and leaves its side, so the counts close at zero.
TEST_F(HandBuilt, EverySideIsEmptyAtTheEnd) {
  feed(wire::add(kLocate, 1, 'B', 100, k997, 100));
  EXPECT_FALSE(walks().empty());
  finish();
  EXPECT_TRUE(walks().empty());
  EXPECT_TRUE(walks().consistent());
  EXPECT_EQ(stats().expired, 4U);
}

// A side that loses an order it does not hold makes the counts inconsistent.
TEST(WalkCountsAlone, LosingWhatASideDoesNotHoldIsInconsistent) {
  WalkCounts walks{4};
  walks.add_level(7);
  walks.remove_level(7);
  EXPECT_TRUE(walks.consistent());
  walks.remove_order(7);
  EXPECT_FALSE(walks.consistent());
}

// A walk past the bound its histograms were sized for makes the counts inconsistent.
TEST(WalkCountsAlone, AWalkPastItsBoundIsInconsistent) {
  WalkCounts walks{2};
  walks.at(WalkSite::rest);
  walks.begin(3);
  walks.level();
  walks.level();
  EXPECT_TRUE(walks.consistent());
  walks.level();
  EXPECT_FALSE(walks.consistent());
}

using StatsWords = std::array<std::uint64_t, sizeof(Stats) / sizeof(std::uint64_t)>;

// The counted engine is the engine: on random markets, under every queue rule, with and
// without latency, the Joiner gets the same stats and event hash from both.
TEST(CountedEngine, GivesTheEnginesEventsOnRandomMarkets) {
  for (const QueueModel queue :
       {QueueModel::exact, QueueModel::conservative, QueueModel::proportional}) {
    for (const bool latency_off : {false, true}) {
      for (const std::uint32_t seed : {3U, 17U, 29U}) {
        const std::vector<Message> messages = RandomMarket{seed}.generate(3000);
        itch::backtest::Engine<> plain{config(queue, latency_off)};
        Counted counted{config(queue, latency_off)};
        Joiner joiner;
        Joiner again;
        for (const Message& message : messages) {
          plain.feed(message.payload, joiner);
          counted.feed(message.payload, again);
        }
        plain.finish(joiner);
        counted.finish(again);
        EXPECT_EQ(std::bit_cast<StatsWords>(plain.stats()),
                  std::bit_cast<StatsWords>(counted.stats()))
            << "seed " << seed;
        EXPECT_EQ(plain.event_hash(), counted.event_hash()) << "seed " << seed;
        EXPECT_GT(plain.stats().fills, 0U);
        EXPECT_TRUE(counted.walks().consistent());
        EXPECT_TRUE(counted.walks().empty());
        EXPECT_GT(counted.walks().site(WalkSite::execution).level_steps, 0U);
      }
    }
  }
}

}  // namespace
