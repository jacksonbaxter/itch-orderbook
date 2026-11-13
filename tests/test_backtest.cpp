// The backtester's hand-built scenarios, numbered as in the research evidence's "Test
// scenarios" (T-BT-2). Each states its book, its events and the result by hand: no
// expected value here is computed by the code under test.
//
// Prices are in ITCH's 10^-4 dollars. Unless a test says otherwise the latencies are
// L_md 100 ns, L_compute 10, L_oe 50 and L_resp 70, so an order the strategy decides on at
// local time τ reaches the exchange at τ + 60, and a fill at exchange time t is reported at
// t + 70. The directory message names the traded symbol at t = 1 and lists it on Nasdaq's
// Global Select Market, Tape C.
#include <gtest/gtest.h>

#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <type_traits>
#include <vector>

#include "backtest/market.hpp"
#include "backtest/scripted.hpp"
#include "itch/backtest.hpp"
#include "itch/fees.hpp"
#include "oracle/wire_builder.hpp"
#include "support/encode.hpp"

// ASSERT_TRUE(x.has_value()) guards each access, which the check cannot see.
// NOLINTBEGIN(bugprone-unchecked-optional-access)
namespace {

using itch::Price4;
using itch::Side;
using itch::backtest::Config;
using itch::backtest::Engine;
using itch::backtest::Latency;
using itch::backtest::Nanos;
using itch::backtest::QueueModel;
using itch::backtest::QueueState;
using itch::backtest::Reject;
using itch::backtest::Report;
using itch::backtest::ReportKind;
using itch::backtest::Tif;
using itch::backtest::synthetic::kLocate;
using itch::backtest::synthetic::kSymbol;
using itch::backtest::synthetic::Scripted;
namespace wire = itch::oracle::wire;

constexpr Price4 k999 = 99'900;
constexpr Price4 k1000 = 100'000;
constexpr Price4 k1001 = 100'100;
constexpr Price4 k1002 = 100'200;
constexpr Price4 k1003 = 100'300;

constexpr Latency kLatency{.market_data = 100, .order_entry = 50, .response = 70, .compute = 10};
constexpr itch::BookCapacity kCapacity{
    .orders = 1024, .levels = 256, .index_pages = 4, .index_bits = 16, .level_slots = 4096};

// One bid or ask of $0.0015 a share, as Tape C pays a maker on 2019-12-30, in picodollars.
constexpr itch::Picodollars kMakerCreditC = 1'500'000'000;
constexpr itch::Picodollars kTakerFee = 3'000'000'000;

constexpr int kS = 1;
constexpr int kS2 = 2;

// The two switches of the with-and-without runs, Q-off and L-off.
struct Switches {
  QueueModel queue = QueueModel::exact;
  bool latency_off = false;
};

Config config(Latency latency, std::uint32_t date = 20191230, Switches switches = {}) {
  Config config;
  config.date = date;
  config.latency = latency;
  config.queue = switches.queue;
  config.latency_off = switches.latency_off;
  config.symbols = {kSymbol};
  config.exchange_capacity = kCapacity;
  config.view_capacity = kCapacity;
  config.max_orders = 64;
  config.queue_capacity = 256;
  return config;
}

wire::Bytes printable_execution(std::uint64_t reference, std::uint32_t shares, Price4 price,
                                char printable, Nanos time) {
  return itch::testing::encode(
      itch::OrderExecutedWithPrice{.header = wire::header('C', kLocate, time),
                                   .order_reference = reference,
                                   .executed_shares = shares,
                                   .match_number = 11,
                                   .printable = printable,
                                   .execution_price = price});
}

// One engine and one scripted strategy. The plan is written before the feed reaches it.
class Replay {
 public:
  explicit Replay(Latency latency = kLatency, char category = 'Q', std::uint32_t date = 20191230)
      : engine{config(latency, date)} {
    feed(wire::directory(kLocate, category, 'P', 100, 1));
  }
  // The base latencies of this file with a switch thrown.
  explicit Replay(Switches switches) : engine{config(kLatency, 20191230, switches)} {
    feed(wire::directory(kLocate, 'Q', 'P', 100, 1));
  }

  void feed(const wire::Bytes& message) { engine.feed(message, strategy); }
  // Everything due before `time` runs.
  void advance(Nanos time) { engine.advance(time, strategy); }
  void finish() { engine.finish(strategy); }

  [[nodiscard]] QueueState queue(int name) const {
    const auto state = engine.queue(strategy.id(name));
    EXPECT_TRUE(state.has_value()) << "order " << name << " is not resting";
    return state.value_or(QueueState{});
  }
  [[nodiscard]] std::uint64_t ahead(int name) const { return queue(name).ahead; }
  [[nodiscard]] bool resting(int name) const { return engine.queue(strategy.id(name)).has_value(); }
  [[nodiscard]] std::vector<Report> fills(int name) const {
    return strategy.reports(name, ReportKind::fill);
  }

  Engine<> engine;
  Scripted strategy;
};

struct Fill {
  Price4 price;
  std::uint32_t shares;
  itch::Liquidity liquidity;
  friend bool operator==(const Fill&, const Fill&) = default;
};

std::vector<Fill> fills_of(const std::vector<Report>& reports) {
  std::vector<Fill> out;
  out.reserve(reports.size());
  for (const Report& report : reports) {
    out.push_back({.price = report.price, .shares = report.shares, .liquidity = report.liquidity});
  }
  return out;
}

constexpr itch::Liquidity kMaker = itch::Liquidity::maker;
constexpr itch::Liquidity kTaker = itch::Liquidity::taker;

// ---- Queue position -------------------------------------------------------------------

// 1. Bid level: R1 100, S, R2 100 added later. X and D on R2. ahead stays 100.
TEST(QueuePosition, S01CancelBehindDoesNotAdvance) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 3000));
  EXPECT_EQ(run.ahead(kS), 100U);
  run.feed(wire::cancel(kLocate, 2, 50, 4000));
  EXPECT_EQ(run.ahead(kS), 100U);
  run.feed(wire::erase(kLocate, 2, 5000));
  EXPECT_EQ(run.ahead(kS), 100U);
  run.advance(10'000);
  EXPECT_TRUE(run.fills(kS).empty());
}

// 2. R1 100, R2 100, S. D R1. ahead 100.
TEST(QueuePosition, S02CancelAheadAdvances) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 1001));
  run.advance(3000);
  EXPECT_EQ(run.ahead(kS), 200U);
  run.feed(wire::erase(kLocate, 1, 3000));
  EXPECT_EQ(run.ahead(kS), 100U);
}

// 3. R1 300, S. X R1 100. ahead 200, and R1 keeps its place at the front.
TEST(QueuePosition, S03PartialCancelAhead) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 300, k1000, 1000));
  run.feed(wire::cancel(kLocate, 1, 100, 3000));
  EXPECT_EQ(run.ahead(kS), 200U);
  const auto r1 = run.engine.exchange_book().resting_order(1);
  ASSERT_TRUE(r1.has_value());
  EXPECT_TRUE(r1->at_front);
  EXPECT_EQ(r1->shares, 200U);
}

// 4. R1 100, S. E R1 60. ahead 40, S unfilled.
TEST(QueuePosition, S04ExecutionAheadAdvancesWithoutFill) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::execute(kLocate, 1, 60, 3000));
  EXPECT_EQ(run.ahead(kS), 40U);
  run.advance(10'000);
  EXPECT_TRUE(run.fills(kS).empty());
}

// 5. R1 100, S. U R1 to a new reference, same price, 80. ahead 0, and the new reference is
// behind S: its execution fills S.
TEST(QueuePosition, S05ReplaceAheadLeavesTheQueue) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::replace(kLocate, 1, 9, 80, k1000, 3000));
  EXPECT_EQ(run.ahead(kS), 0U);
  run.feed(wire::execute(kLocate, 9, 50, 4000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 50, kMaker}}));
  EXPECT_EQ(run.queue(kS).remaining, 50U);
}

// 6. The same, to a new price. ahead 0. Nothing real rests at the old price.
TEST(QueuePosition, S06ReplaceAheadToANewPrice) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::replace(kLocate, 1, 9, 100, k999, 3000));
  EXPECT_EQ(run.ahead(kS), 0U);
  EXPECT_EQ(run.engine.exchange_book().find_level(kLocate, Side::bid, k1000), nullptr);
}

// 7. S at the back, a new add at the same price. ahead unchanged.
TEST(QueuePosition, S07AddBehind) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 500, k1000, 3000));
  run.feed(wire::add_attributed(kLocate, 3, 'B', 700, k1000, 3001));
  EXPECT_EQ(run.ahead(kS), 100U);
}

// 9. R1 ahead executes in full; the refresh appears as a later add, behind S, and its
// execution fills S.
TEST(QueuePosition, S09ReserveRefreshIsBehind) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::execute(kLocate, 1, 100, 3000));
  EXPECT_EQ(run.ahead(kS), 0U);
  run.feed(wire::add(kLocate, 3, 'B', 100, k1000, 3000));
  EXPECT_EQ(run.ahead(kS), 0U);
  run.feed(wire::execute(kLocate, 3, 30, 3500));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 30, kMaker}}));
}

// 49. Not in the evidence's list: found on the real days (docs/NOTES.md, "Queue jumps"). R1 100
// (reference 10), S, R2 100 (reference 20). R0 50 is added with reference 5, below what the
// symbol has shown: an order received before R1 and shown late, as an order held for the
// open is. It goes ahead of S: ahead 150. A late add at another price moves nothing. R0
// executes 50: ahead 100, no fill and no anomaly. R1 executes 100: ahead 0. R2 executes
// 30: S fills 30.
TEST(QueuePosition, S49LateAddGoesAhead) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 10, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 20, 'B', 100, k1000, 3000));
  EXPECT_EQ(run.ahead(kS), 100U);
  run.feed(wire::add(kLocate, 5, 'B', 50, k1000, 3100));
  EXPECT_EQ(run.ahead(kS), 150U);
  run.feed(wire::add_attributed(kLocate, 6, 'B', 70, k999, 3200));
  EXPECT_EQ(run.ahead(kS), 150U);
  EXPECT_EQ(run.engine.recount_ahead(run.strategy.id(kS)), std::optional<std::uint64_t>{150});
  run.feed(wire::execute(kLocate, 5, 50, 4000));
  EXPECT_EQ(run.ahead(kS), 100U);
  run.feed(wire::execute(kLocate, 10, 100, 4100));
  EXPECT_EQ(run.ahead(kS), 0U);
  EXPECT_TRUE(run.fills(kS).empty());
  run.feed(wire::execute(kLocate, 20, 30, 4200));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 30, kMaker}}));
  EXPECT_EQ(run.engine.stats().queue_anomalies, 0U);
  EXPECT_EQ(run.engine.stats().late_adds, 2U);
  EXPECT_EQ(run.engine.stats().late_adds_ahead, 1U);
}

// 49, the other way. R1 100 (reference 10), S, then a late R0 50 (reference 5). R1 executes
// 100, then R0 50. Counting R0 behind S, as adds in feed order would, leaves ahead 0 when
// R0 executes and fills S 50 from shares that were ahead of it. R0 is ahead: nothing fills.
TEST(QueuePosition, S49LateAddDoesNotFillWhatIsAhead) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 10, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 5, 'B', 50, k1000, 3000));
  run.feed(wire::execute(kLocate, 10, 100, 4000));
  EXPECT_EQ(run.ahead(kS), 50U);
  run.feed(wire::execute(kLocate, 5, 50, 4100));
  EXPECT_EQ(run.ahead(kS), 0U);
  run.advance(10'000);
  EXPECT_TRUE(run.fills(kS).empty());
  EXPECT_EQ(run.engine.stats().queue_anomalies, 0U);
}

// ---- Q-off: the two rules of an aggregate feed ------------------------------------------
//
// Feeds like those of S01 to S09, under the rules of "Q-off" in backtest.hpp. The exact
// rule's figure is given beside a step where the rule departs from it.

// Conservative. R1 100 and R2 100, then S: ahead 200. R3 100 joins behind, so the level is
// 300. X R1 50: d − c = 250, ahead stays 200 (exact 150). D R2: 250 − 100 = 150, so ahead
// falls to 150, only because nothing else was left behind (exact 50). D R3, behind: the
// level falls to 50, and so does ahead (exact 50). E R1 30: ahead 20, no fill. R4 joins
// behind; E R4 40 takes the 20 ahead and 20 reach S (exact: R4 is behind S with 20 still
// ahead, an anomaly). A late add, reference 5, does not go ahead (exact: ahead 50), and E of
// it 30 reaches S with nothing ahead: 30.
TEST(QueueOff, ConservativeAdvancesOnlyOnExecutionsAndTheLevelsSize) {
  Replay run{Switches{.queue = QueueModel::conservative}};
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 10, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 20, 'B', 100, k1000, 1001));
  run.advance(3000);
  EXPECT_EQ(run.ahead(kS), 200U);
  run.feed(wire::add(kLocate, 30, 'B', 100, k1000, 3000));
  EXPECT_EQ(run.ahead(kS), 200U);
  run.feed(wire::cancel(kLocate, 10, 50, 3100));
  EXPECT_EQ(run.ahead(kS), 200U);
  run.feed(wire::erase(kLocate, 20, 3200));
  EXPECT_EQ(run.ahead(kS), 150U);
  run.feed(wire::erase(kLocate, 30, 3300));
  EXPECT_EQ(run.ahead(kS), 50U);
  run.feed(wire::execute(kLocate, 10, 30, 3400));
  EXPECT_EQ(run.ahead(kS), 20U);
  run.feed(wire::add(kLocate, 40, 'B', 100, k1000, 3500));
  run.feed(wire::execute(kLocate, 40, 40, 3600));
  EXPECT_EQ(run.ahead(kS), 0U);
  run.feed(wire::add(kLocate, 5, 'B', 50, k1000, 3700));
  EXPECT_EQ(run.ahead(kS), 0U);
  run.feed(wire::execute(kLocate, 5, 30, 3800));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 20, kMaker}, {k1000, 30, kMaker}}));
  EXPECT_EQ(run.queue(kS).remaining, 50U);
  EXPECT_EQ(run.queue(kS).ahead_fraction, 0U);
  EXPECT_EQ(run.engine.stats().queue_anomalies, 0U);
  EXPECT_EQ(run.engine.stats().late_adds_ahead, 0U);
}

// Proportional. R1 100 and R2 100, then S: ahead 200. R3 200 joins behind: the level is
// 400. X R3 100 removes 100·200/400 = 50 from ahead: 150 (exact 200). X R1 30, level 300:
// 30·150/300 = 15, so 135 (exact 170). X R3 7, level 270: 7·135/270 = 3.5, so 131.5. E R1
// 70: 61.5, no fill. E R2 64 takes the 61.5 ahead, and 2.5 shares are left, so 2 reach S.
TEST(QueueOff, ProportionalRemovesItsShareOfEachCancel) {
  Replay run{Switches{.queue = QueueModel::proportional}};
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 10, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 20, 'B', 100, k1000, 1001));
  run.advance(3000);
  EXPECT_EQ(run.ahead(kS), 200U);
  run.feed(wire::add(kLocate, 30, 'B', 200, k1000, 3000));
  run.feed(wire::cancel(kLocate, 30, 100, 3100));
  EXPECT_EQ(run.ahead(kS), 150U);
  EXPECT_EQ(run.queue(kS).ahead_fraction, 0U);
  run.feed(wire::cancel(kLocate, 10, 30, 3200));
  EXPECT_EQ(run.ahead(kS), 135U);
  run.feed(wire::cancel(kLocate, 30, 7, 3300));
  EXPECT_EQ(run.ahead(kS), 131U);
  EXPECT_EQ(run.queue(kS).ahead_fraction, 500'000U);
  run.feed(wire::execute(kLocate, 10, 70, 3400));
  EXPECT_EQ(run.ahead(kS), 61U);
  EXPECT_EQ(run.queue(kS).ahead_fraction, 500'000U);
  run.feed(wire::execute(kLocate, 20, 64, 3500));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 2, kMaker}}));
  EXPECT_EQ(run.ahead(kS), 0U);
  EXPECT_EQ(run.queue(kS).ahead_fraction, 0U);
}

// R1 100, then S, then R2 200: the level is 300. X R2 1 removes 100/300 of a share,
// 333,333 millionths rounded down, leaving 99 shares and 666,667 millionths ahead. A late
// add does not go ahead, and deleting the whole level leaves nothing ahead.
TEST(QueueOff, ProportionalRoundsEachRemovalDownInMillionths) {
  Replay run{Switches{.queue = QueueModel::proportional}};
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 10, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 20, 'B', 200, k1000, 3000));
  run.feed(wire::cancel(kLocate, 20, 1, 3100));
  EXPECT_EQ(run.ahead(kS), 99U);
  EXPECT_EQ(run.queue(kS).ahead_fraction, 666'667U);
  run.feed(wire::add(kLocate, 5, 'B', 40, k1000, 3200));
  EXPECT_EQ(run.ahead(kS), 99U);
  run.feed(wire::erase(kLocate, 10, 3300));
  run.feed(wire::erase(kLocate, 20, 3301));
  run.feed(wire::erase(kLocate, 5, 3302));
  EXPECT_EQ(run.ahead(kS), 0U);
  EXPECT_EQ(run.queue(kS).ahead_fraction, 0U);
}

// S50 under each Q-off rule. R1 100 at 10.00, then S 100 at 10.00: ahead 100. R2 100 joins
// behind S, and E R2 30 takes 30 from the front: ahead 70, nothing reaches S. S2 then bids 40
// at 10.01. A printable C executes all 100 of R1. At 10.01, a price better for the seller than
// R1's own, it takes the 70 ahead, and the 30 left over do not reach S: the seller went no
// lower than 10.01, so S at R1's price fills nothing. S2, at the trade's price, fills 40. The
// same C at 10.00 reaches both: S2 fills 40, and S the 30 of the 60 left.
TEST(QueueOff, APrintableCAtABetterPriceDoesNotReachTheRestingPrice) {
  for (const QueueModel model : {QueueModel::conservative, QueueModel::proportional}) {
    for (const Price4 traded : {k1001, k1000}) {
      Replay run{Switches{.queue = model}};
      run.strategy.send(kS, 2000, Side::bid, k1000, 100);
      run.strategy.send(kS2, 4500, Side::bid, k1001, 40);
      run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
      run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 3000));
      run.feed(wire::execute(kLocate, 2, 30, 4000));
      EXPECT_EQ(run.ahead(kS), 70U) << to_string(model);
      run.feed(printable_execution(1, 100, traded, 'Y', 5000));
      run.advance(10'000);
      EXPECT_EQ(fills_of(run.fills(kS2)), (std::vector<Fill>{{k1001, 40, kMaker}}))
          << to_string(model) << " at " << traded;
      if (traded == k1001) {
        EXPECT_TRUE(run.fills(kS).empty()) << to_string(model);
        EXPECT_EQ(run.queue(kS).remaining, 100U) << to_string(model);
      } else {
        EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 30, kMaker}}))
            << to_string(model);
        EXPECT_EQ(run.queue(kS).remaining, 70U) << to_string(model);
      }
      EXPECT_EQ(run.ahead(kS), 0U) << to_string(model) << " at " << traded;
    }
  }
}

// L-off. The config's latencies are this file's base ones, and the switch zeroes all four:
// S decided at 2000 arrives at 2000, behind R1 and R2. Its accept and its fill reach the
// strategy at their exchange times, and every message reaches it at its own timestamp. The run
// equals one with every latency set to 0, and differs from the base run.
TEST(LatencyOff, EveryLatencyIsZero) {
  const auto play = [](Replay& run) {
    run.strategy.send(kS, 2000, Side::bid, k1000, 100);
    run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
    run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 1500));
    run.feed(wire::execute(kLocate, 1, 100, 3000));
    run.feed(wire::execute(kLocate, 2, 100, 3001));
    run.feed(wire::add(kLocate, 3, 'B', 100, k1000, 3002));
    run.feed(wire::execute(kLocate, 3, 40, 3003));
    run.feed(wire::add(kLocate, 4, 'B', 100, k999, 4000));
    run.finish();
  };
  Replay off{Switches{.latency_off = true}};
  play(off);
  const auto accepted = off.strategy.reports(kS, ReportKind::accepted);
  ASSERT_EQ(accepted.size(), 1U);
  EXPECT_EQ(accepted[0].exchange_time, 2000U);
  EXPECT_EQ(accepted[0].time, 2000U);
  const auto fills = off.fills(kS);
  ASSERT_EQ(fills.size(), 1U);
  EXPECT_EQ(fills[0].shares, 40U);
  EXPECT_EQ(fills[0].exchange_time, 3003U);
  EXPECT_EQ(fills[0].time, 3003U);
  EXPECT_FALSE(off.strategy.seen().empty());
  for (const Scripted::Seen& seen : off.strategy.seen()) {
    EXPECT_EQ(seen.time, seen.exchange_time);
  }
  Replay zero{{.market_data = 0, .order_entry = 0, .response = 0, .compute = 0}};
  play(zero);
  EXPECT_EQ(off.engine.event_hash(), zero.engine.event_hash());
  Replay base;
  play(base);
  EXPECT_NE(off.engine.event_hash(), base.engine.event_hash());
}

// Records each event as the strategy is given it, with the arrivals the engine had run by then:
// an arrival reaches no strategy, so the count is how the order shows where it went.
struct TieOrder {
  struct Seen {
    char what;  // 'M' market, 'R' report, 'W' wake.
    Nanos time;
    char detail;  // The message type, or 'f' fill, 'a' accepted.
    std::uint64_t arrivals;
    friend bool operator==(const Seen&, const Seen&) = default;
  };

  template <class Context>
  void on_market(Context& context, const itch::backtest::MarketEvent& event) {
    const char type = static_cast<char>(event.payload[0]);
    seen.push_back({'M', event.time, type, engine->stats().arrivals});
    if (event.exchange_time == 1) {
      context.send({.locate = kLocate, .side = Side::bid, .price = k1000, .shares = 100});
      context.wake_at(5000);
    }
    if (type == 'E') {
      context.send({.locate = kLocate, .side = Side::bid, .price = k999, .shares = 10});
    }
  }
  template <class Context>
  void on_report(Context& /*context*/, const Report& report) {
    seen.push_back(
        {'R', report.time, report.kind == ReportKind::fill ? 'f' : 'a', engine->stats().arrivals});
  }
  template <class Context>
  void on_wake(Context& context) {
    seen.push_back({'W', context.now(), ' ', engine->stats().arrivals});
  }

  const Engine<>* engine = nullptr;
  std::vector<Seen> seen;
};

// The tie order under L-off, where every latency is zero and so everything a message causes
// falls in its own nanosecond. The directory's delivery at 1 sends S, a bid of 100 at 10.00,
// which arrives at 1 and rests with nothing ahead, and sets a wake at 5000. R1 joins behind S
// at 2000. At 5000 two messages apply first: E R1 40, which fills S 40 and reports it at 5000,
// and an add at 9.99. Pending at 5000 then are two deliveries, the fill report and the wake.
// The first delivery sends S2, which arrives at 5000. In the documented order, arrival,
// delivery, report, wake, S2 arrives before the second delivery reaches the strategy, and its
// accept queues behind the fill report; the wake comes last.
TEST(LatencyOff, EventsOfOneNanosecondReachTheStrategyInTheTieOrder) {
  Engine<> engine{config(kLatency, 20191230, {.latency_off = true})};
  TieOrder strategy;
  strategy.engine = &engine;
  engine.feed(wire::directory(kLocate, 'Q', 'P', 100, 1), strategy);
  engine.feed(wire::add(kLocate, 1, 'B', 100, k1000, 2000), strategy);
  engine.feed(wire::execute(kLocate, 1, 40, 5000), strategy);
  engine.feed(wire::add(kLocate, 2, 'B', 100, k999, 5000), strategy);
  engine.advance(5001, strategy);
  using Seen = TieOrder::Seen;
  EXPECT_EQ(strategy.seen, (std::vector<Seen>{{'M', 1, 'R', 0},
                                              {'R', 1, 'a', 1},
                                              {'M', 2000, 'A', 1},
                                              {'M', 5000, 'E', 1},
                                              {'M', 5000, 'A', 2},
                                              {'R', 5000, 'f', 2},
                                              {'R', 5000, 'a', 2},
                                              {'W', 5000, ' ', 2}}));
  EXPECT_EQ(engine.stats().fills, 1U);
}

// ---- Join time and latency ------------------------------------------------------------

// 10. Decide at 2000, L_compute 0, L_oe 10: S arrives at 2010. A real add at 2009 is ahead,
// one at 2010 is ahead on the tie, one at 2011 is behind.
TEST(JoinAndLatency, S10JoinAtArrival) {
  Replay run{{.market_data = 100, .order_entry = 10, .response = 70, .compute = 0}};
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 2009));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 2010));
  run.feed(wire::add(kLocate, 3, 'B', 100, k1000, 2011));
  EXPECT_EQ(run.queue(kS).ahead, 200U);
  run.feed(wire::erase(kLocate, 3, 3000));
  EXPECT_EQ(run.ahead(kS), 200U);
  run.feed(wire::erase(kLocate, 2, 3001));
  EXPECT_EQ(run.ahead(kS), 100U);
  ASSERT_EQ(run.strategy.reports(kS, ReportKind::accepted).size(), 1U);
  EXPECT_EQ(run.strategy.reports(kS, ReportKind::accepted)[0].exchange_time, 2010U);
}

// 11. The strategy decides on a view of 300 shares; in flight, 100 leave and 50 join.
// ahead is what rests at arrival, 250.
TEST(JoinAndLatency, S11JoinIgnoresTheStaleView) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 1001));
  run.feed(wire::add(kLocate, 3, 'B', 100, k1000, 1002));
  run.feed(wire::erase(kLocate, 1, 2020));
  run.feed(wire::add(kLocate, 4, 'B', 50, k1000, 2030));
  run.advance(3000);
  ASSERT_EQ(run.strategy.sent().size(), 1U);
  EXPECT_EQ(run.strategy.sent()[0].view_shares, 300U);
  EXPECT_EQ(run.ahead(kS), 250U);
}

// 12. Every share at S's price leaves in flight. S arrives with nothing ahead, as the
// best bid, and a sell that reaches the lower real bid fills it first.
TEST(JoinAndLatency, S12LevelGoneOnArrival) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k999, 1001));
  run.feed(wire::erase(kLocate, 1, 2030));
  run.advance(3000);
  EXPECT_EQ(run.ahead(kS), 0U);
  run.feed(wire::execute(kLocate, 2, 40, 3000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 40, kMaker}}));
}

// 13a. An ask at S's limit at decision time is gone on arrival: S rests, unfilled.
TEST(JoinAndLatency, S13MarketableOnDecisionRestsWhenTheAskVanishes) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1001, 50);
  run.feed(wire::add(kLocate, 1, 'S', 100, k1001, 1000));
  run.feed(wire::erase(kLocate, 1, 2030));
  run.advance(10'000);
  EXPECT_TRUE(run.fills(kS).empty());
  EXPECT_EQ(run.ahead(kS), 0U);
}

// 13b. The same as IOC: cancelled whole.
TEST(JoinAndLatency, S13IocCancelsWhenTheAskVanishes) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1001, 50, Tif::ioc);
  run.feed(wire::add(kLocate, 1, 'S', 100, k1001, 1000));
  run.feed(wire::erase(kLocate, 1, 2030));
  run.advance(10'000);
  EXPECT_TRUE(run.fills(kS).empty());
  ASSERT_EQ(run.strategy.reports(kS, ReportKind::cancelled).size(), 1U);
  EXPECT_EQ(run.strategy.reports(kS, ReportKind::cancelled)[0].shares, 50U);
  EXPECT_FALSE(run.resting(kS));
}

// 13c. No ask at S's limit at decision; one appears in flight. S crosses it on arrival.
TEST(JoinAndLatency, S13MarketableOnArrivalCrosses) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1001, 50);
  run.feed(wire::add(kLocate, 1, 'S', 100, k1002, 1000));
  run.feed(wire::add(kLocate, 2, 'S', 100, k1001, 2030));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1001, 50, kTaker}}));
  EXPECT_FALSE(run.resting(kS));
}

// 14. A fill at exchange time 5000 reaches the strategy at 5000 + L_resp and no sooner,
// though market data from after it arrives first.
TEST(JoinAndLatency, S14FillReportDelay) {
  Replay run{{.market_data = 10, .order_entry = 50, .response = 50, .compute = 10}};
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 3000));
  run.feed(wire::execute(kLocate, 2, 30, 5000));
  run.feed(wire::add(kLocate, 3, 'B', 100, k999, 5001));
  run.advance(10'000);
  const auto fills = run.fills(kS);
  ASSERT_EQ(fills.size(), 1U);
  EXPECT_EQ(fills[0].exchange_time, 5000U);
  EXPECT_EQ(fills[0].time, 5050U);
  // The accepted report at 2060 + 50 is the only report before the fill.
  for (const Scripted::Seen& seen : run.strategy.seen()) {
    if (seen.time < 5050) {
      EXPECT_LE(seen.reports_before, 1U) << "at " << seen.time;
    }
  }
  bool saw_the_later_add = false;
  for (const Scripted::Seen& seen : run.strategy.seen()) {
    if (seen.exchange_time == 5001) {
      saw_the_later_add = true;
      EXPECT_EQ(seen.time, 5011U);
      EXPECT_EQ(seen.reports_before, 1U);
    }
  }
  EXPECT_TRUE(saw_the_later_add);
}

// 15. Every message reaches the strategy exactly L_md after its timestamp, and none
// sooner. The full check, against a book rebuilt at each callback, is in
// test_backtest_validity.cpp.
TEST(JoinAndLatency, S15MarketDataDelay) {
  Replay run;
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 1050));
  run.advance(1100);
  // Only the directory, at 1 + 100, has been delivered before 1100.
  ASSERT_EQ(run.strategy.seen().size(), 1U);
  run.advance(1101);
  ASSERT_EQ(run.strategy.seen().size(), 2U);
  EXPECT_EQ(run.strategy.seen()[1].time, 1100U);
  EXPECT_EQ(run.strategy.seen()[1].exchange_time, 1000U);
  EXPECT_EQ(run.engine.view_book().find_level(kLocate, Side::bid, k1000)->aggregate_shares, 100U);
  run.advance(1151);
  EXPECT_EQ(run.engine.view_book().find_level(kLocate, Side::bid, k1000)->aggregate_shares, 200U);
}

// 16. A cancel is sent; a fill happens before it arrives. The fill stands and the cancel
// is answered too late.
TEST(JoinAndLatency, S16CancelRace) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.strategy.cancel(kS, 4000);
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 3000));
  run.feed(wire::execute(kLocate, 2, 100, 4030));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 100, kMaker}}));
  const auto rejected = run.strategy.reports(kS, ReportKind::cancel_rejected);
  ASSERT_EQ(rejected.size(), 1U);
  EXPECT_EQ(rejected[0].reason, Reject::not_live);
  EXPECT_EQ(rejected[0].exchange_time, 4060U);
  EXPECT_TRUE(run.strategy.reports(kS, ReportKind::cancelled).empty());
}

// 17. A replace of S re-joins at the replace's arrival, behind everything resting then.
TEST(JoinAndLatency, S17ReplaceOnSLosesItsPlace) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.strategy.replace(kS, 4000, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 3000));
  run.feed(wire::execute(kLocate, 1, 100, 3500));
  EXPECT_EQ(run.ahead(kS), 0U);
  run.feed(wire::add(kLocate, 3, 'B', 100, k1000, 4030));
  run.advance(5000);
  EXPECT_EQ(run.ahead(kS), 200U);
  const auto replaced = run.strategy.reports(kS, ReportKind::replaced);
  ASSERT_EQ(replaced.size(), 1U);
  EXPECT_EQ(replaced[0].exchange_time, 4060U);
}

// ---- Fills and partial fills ----------------------------------------------------------

// 18. R1 100, S 100, R2 100. Execute R1 100, then R2 30. S fills 30, 70 remain.
TEST(Fills, S18ThroughAheadThenPartial) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 3000));
  run.feed(wire::execute(kLocate, 1, 100, 4000));
  EXPECT_TRUE(run.fills(kS).empty());
  run.feed(wire::execute(kLocate, 2, 30, 4000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 30, kMaker}}));
  EXPECT_EQ(run.queue(kS).remaining, 70U);
}

// 19. Execute 300 behind S, with 100 remaining. S fills 100.
TEST(Fills, S19NoOverfill) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 2, 'B', 300, k1000, 3000));
  run.feed(wire::execute(kLocate, 2, 300, 4000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 100, kMaker}}));
  EXPECT_FALSE(run.resting(kS));
}

// 20. One aggressor executes two orders behind S. S's 150 fill as 100 then 50, never
// more than was traded behind it.
TEST(Fills, S20SeveralExecutionsOfOneAggressor) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 150);
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 3000));
  run.feed(wire::add(kLocate, 3, 'B', 100, k1000, 3001));
  run.feed(wire::execute(kLocate, 2, 100, 4000));
  run.feed(wire::execute(kLocate, 3, 100, 4000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)),
            (std::vector<Fill>{{k1000, 100, kMaker}, {k1000, 50, kMaker}}));
}

// 21. S bids 10.01; a real execution at 10.00 for 50. S fills 50 at 10.01, its own price.
TEST(Fills, S21BetterPricedSFillsAtItsLimit) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1001, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::execute(kLocate, 1, 50, 3000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1001, 50, kMaker}}));
}

// 22. 10.01: R1 100, then S 50. 10.00: R2 100. Execute R1 100 and R2 80. S fills 50 at
// 10.01.
TEST(Fills, S22SweepThroughSsLevel) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1001, 50);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1001, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 1001));
  run.feed(wire::execute(kLocate, 1, 100, 4000));
  run.feed(wire::execute(kLocate, 2, 80, 4000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1001, 50, kMaker}}));
}

// 23. S1 50 then S2 50, behind R1 and ahead of R2. One execution of 70 on R2: S1 gets 50,
// S2 20. Never more than 70 in all.
TEST(Fills, S23TwoSimulatedOrdersOneExecution) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 50);
  run.strategy.send(kS2, 2100, Side::bid, k1000, 50);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 3000));
  EXPECT_EQ(run.ahead(kS), 100U);
  EXPECT_EQ(run.ahead(kS2), 100U);
  run.feed(wire::execute(kLocate, 1, 100, 4000));
  run.feed(wire::execute(kLocate, 2, 70, 4000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 50, kMaker}}));
  EXPECT_EQ(fills_of(run.fills(kS2)), (std::vector<Fill>{{k1000, 20, kMaker}}));
}

// 24. An execution behind S while shares remain ahead of it: no fill, one anomaly.
TEST(Fills, S24AnomalyGuard) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 3000));
  run.feed(wire::execute(kLocate, 2, 30, 4000));
  run.advance(10'000);
  EXPECT_TRUE(run.fills(kS).empty());
  EXPECT_EQ(run.engine.stats().queue_anomalies, 1U);
  EXPECT_EQ(run.ahead(kS), 100U);
}

// 25. S bids 10.01 inside 10.00 by 10.03. A real sell of 40 at 10.01 is added: S fills 40
// as maker. The ledger then stops those 40 shares filling anything again: an IOC buy
// finds none, and their later execution fills nothing. A new real sell is new liquidity.
TEST(Fills, S25CrossingAdd) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1001, 100);
  run.strategy.send(kS2, 4000, Side::bid, k1001, 100, Tif::ioc);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'S', 100, k1003, 1001));
  run.feed(wire::add(kLocate, 3, 'S', 40, k1001, 3000));
  EXPECT_EQ(run.engine.stats().crossing_fills, 1U);
  run.feed(wire::execute(kLocate, 3, 40, 5000));
  run.feed(wire::add(kLocate, 4, 'S', 20, k1001, 6000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1001, 40, kMaker}, {k1001, 20, kMaker}}));
  EXPECT_TRUE(run.fills(kS2).empty());
  ASSERT_EQ(run.strategy.reports(kS2, ReportKind::cancelled).size(), 1U);
  EXPECT_EQ(run.strategy.reports(kS2, ReportKind::cancelled)[0].shares, 100U);
  EXPECT_EQ(run.queue(kS).remaining, 40U);
}

// 26. Real cancels clear everything ahead. S is unfilled: nothing fills it without an
// execution or a crossing add.
TEST(Fills, S26DeleteOfTheLevel) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 1001));
  run.feed(wire::erase(kLocate, 1, 3000));
  run.feed(wire::erase(kLocate, 2, 3001));
  EXPECT_EQ(run.ahead(kS), 0U);
  run.feed(wire::add(kLocate, 3, 'B', 100, k1000, 5000));
  run.feed(wire::erase(kLocate, 3, 6000));
  run.advance(10'000);
  EXPECT_TRUE(run.fills(kS).empty());
  EXPECT_EQ(run.queue(kS).remaining, 100U);
}

// 27. S buys 500 at 10.01 with 300 offered there: 100 and 200 fill as taker. As IOC the
// other 200 are cancelled; as a day order they rest, with nothing ahead.
TEST(Fills, S27AggressivePartial) {
  for (const Tif tif : {Tif::ioc, Tif::day}) {
    Replay run;
    run.strategy.send(kS, 2000, Side::bid, k1001, 500, tif);
    run.feed(wire::add(kLocate, 1, 'S', 100, k1001, 1000));
    run.feed(wire::add(kLocate, 2, 'S', 200, k1001, 1001));
    run.advance(10'000);
    EXPECT_EQ(fills_of(run.fills(kS)),
              (std::vector<Fill>{{k1001, 100, kTaker}, {k1001, 200, kTaker}}));
    if (tif == Tif::ioc) {
      ASSERT_EQ(run.strategy.reports(kS, ReportKind::cancelled).size(), 1U);
      EXPECT_EQ(run.strategy.reports(kS, ReportKind::cancelled)[0].shares, 200U);
      EXPECT_FALSE(run.resting(kS));
    } else {
      EXPECT_EQ(run.queue(kS).remaining, 200U);
      EXPECT_EQ(run.queue(kS).ahead, 0U);
      EXPECT_EQ(run.queue(kS).price, k1001);
    }
  }
}

// 28. S1 takes 150 of 300 offered; S2 then finds only the 150 S1 left.
TEST(Fills, S28LedgerAcrossSimulatedTakers) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1001, 150, Tif::ioc);
  run.strategy.send(kS2, 3000, Side::bid, k1001, 300, Tif::ioc);
  run.feed(wire::add(kLocate, 1, 'S', 100, k1001, 1000));
  run.feed(wire::add(kLocate, 2, 'S', 200, k1001, 1001));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)),
            (std::vector<Fill>{{k1001, 100, kTaker}, {k1001, 50, kTaker}}));
  EXPECT_EQ(fills_of(run.fills(kS2)), (std::vector<Fill>{{k1001, 150, kTaker}}));
  ASSERT_EQ(run.strategy.reports(kS2, ReportKind::cancelled).size(), 1U);
  EXPECT_EQ(run.strategy.reports(kS2, ReportKind::cancelled)[0].shares, 150U);
}

// 28, the other half of the ledger. S2 offers 100 at 10.01 before R1 does, so S2 is ahead
// of R1. S takes 60 of R1's 100 as taker. A real buyer then executes all 100 of R1: 60 of
// those shares are the ones S already took, so only the other 40 can fill S2.
TEST(Fills, S28AnExecutionDrawsOnConsumedSharesFirst) {
  Replay run;
  run.strategy.send(kS2, 1000, Side::ask, k1001, 100);
  run.strategy.send(kS, 3000, Side::bid, k1001, 60, Tif::ioc);
  run.feed(wire::add(kLocate, 1, 'S', 100, k1001, 2000));
  run.feed(wire::execute(kLocate, 1, 100, 5000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1001, 60, kTaker}}));
  EXPECT_EQ(fills_of(run.fills(kS2)), (std::vector<Fill>{{k1001, 40, kMaker}}));
  EXPECT_EQ(run.queue(kS2).remaining, 60U);
}

// 29. S crosses three levels. Each fill at its level's price, each with the remove fee on
// its own shares.
TEST(Fills, S29DepthWalk) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1003, 250, Tif::ioc);
  run.feed(wire::add(kLocate, 1, 'S', 100, k1001, 1000));
  run.feed(wire::add(kLocate, 2, 'S', 100, k1002, 1001));
  run.feed(wire::add(kLocate, 3, 'S', 100, k1003, 1002));
  run.advance(10'000);
  const auto fills = run.fills(kS);
  EXPECT_EQ(fills_of(fills),
            (std::vector<Fill>{{k1001, 100, kTaker}, {k1002, 100, kTaker}, {k1003, 50, kTaker}}));
  ASSERT_EQ(fills.size(), 3U);
  // $0.0030 a share; a buy pays no Section 31 or TAF.
  EXPECT_EQ(fills[0].fees.total(), 100 * kTakerFee);
  EXPECT_EQ(fills[1].fees.total(), 100 * kTakerFee);
  EXPECT_EQ(fills[2].fees.total(), 50 * kTakerFee);
}

// 41. One order fills as taker and then, resting, as maker: each fill is charged its own
// way.
TEST(Fills, S41MakerOrTakerPerFill) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1001, 200);
  run.feed(wire::add(kLocate, 1, 'S', 100, k1001, 1000));
  run.feed(wire::add(kLocate, 2, 'S', 50, k1001, 3000));
  run.advance(10'000);
  const auto fills = run.fills(kS);
  EXPECT_EQ(fills_of(fills), (std::vector<Fill>{{k1001, 100, kTaker}, {k1001, 50, kMaker}}));
  ASSERT_EQ(fills.size(), 2U);
  EXPECT_EQ(fills[0].fees.total(), 100 * kTakerFee);
  EXPECT_EQ(fills[1].fees.total(), -50 * kMakerCreditC);
}

// ---- Fees in the engine ---------------------------------------------------------------

// NYSE Arca lists on Tape B, which earns $0.0020 a share to add on 2019-12-30.
TEST(FeesInTheEngine, TheTapeComesFromTheDirectory) {
  Replay run{kLatency, 'P'};
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 3000));
  run.feed(wire::execute(kLocate, 2, 100, 4000));
  run.advance(10'000);
  const auto fills = run.fills(kS);
  ASSERT_EQ(fills.size(), 1U);
  EXPECT_EQ(fills[0].fees,
            (itch::FeeBreakdown{.exchange = -200'000'000'000, .section31 = 0, .taf = 0, .cat = 0}));
}

// A maker sell of 100 at $10.00 on Tape C: the $0.0015 credit, Section 31 on $1,000 at
// $20.70 per $1M ($0.0207), and TAF at $0.000119 a share ($0.0119).
TEST(FeesInTheEngine, ASellPaysSection31AndTaf) {
  Replay run;
  run.strategy.send(kS, 2000, Side::ask, k1000, 100);
  run.feed(wire::add(kLocate, 2, 'S', 100, k1000, 3000));
  run.feed(wire::execute(kLocate, 2, 100, 4000));
  run.advance(10'000);
  const auto fills = run.fills(kS);
  ASSERT_EQ(fills.size(), 1U);
  EXPECT_EQ(fills[0].fees, (itch::FeeBreakdown{.exchange = -150'000'000'000,
                                               .section31 = 20'700'000'000,
                                               .taf = 11'900'000'000,
                                               .cat = 0}));
}

// Market Category M is in no spec list, so it has no tape and no fees, and is not traded.
TEST(FeesInTheEngine, ASymbolWithNoTapeIsNotTraded) {
  Replay run{kLatency, 'M'};
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.advance(10'000);
  const auto rejected = run.strategy.reports(kS, ReportKind::rejected);
  ASSERT_EQ(rejected.size(), 1U);
  EXPECT_EQ(rejected[0].reason, Reject::unknown_tape);
}

// A day with no sourced fee schedule replays, and trades nothing.
TEST(FeesInTheEngine, ADayWithNoScheduleTradesNothing) {
  Replay run{kLatency, 'Q', 20200102};
  EXPECT_FALSE(run.engine.ready());
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.advance(10'000);
  const auto rejected = run.strategy.reports(kS, ReportKind::rejected);
  ASSERT_EQ(rejected.size(), 1U);
  EXPECT_EQ(rejected[0].reason, Reject::closed);
}

// ---- ITCH edge cases ------------------------------------------------------------------

// 30. A Trade message at S's price: no fill, no change to ahead, one count.
TEST(ItchEdges, S30HiddenTradeAtSsPrice) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::trade(kLocate, 100, k1000, 3000));
  run.advance(10'000);
  EXPECT_EQ(run.ahead(kS), 100U);
  EXPECT_TRUE(run.fills(kS).empty());
  EXPECT_EQ(run.engine.stats().hidden_prints_at_sim_price, 1U);
}

// 31. A non-printable execution, as a cross makes, takes shares from ahead but fills
// nothing. The same execution printable does.
TEST(ItchEdges, S31NonPrintableExecutionDoesNotFill) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 3000));
  run.feed(printable_execution(1, 100, k1000, 'N', 4000));
  EXPECT_EQ(run.ahead(kS), 0U);
  run.feed(printable_execution(2, 50, k1000, 'N', 4001));
  run.advance(5000);
  EXPECT_TRUE(run.fills(kS).empty());
  run.feed(printable_execution(2, 50, k1000, 'Y', 5000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 50, kMaker}}));
}

// 50. Not in the evidence's list: found on the real day (docs/NOTES.md, "Queue jumps"). R1 100,
// S at 10.00, R2 100 behind; S2 bids 10.01. A printable C executes 50 of R2 at 10.01, a
// price better for the seller than R2's 10.00, as every printable C in regular trading on
// 12302019 did: the seller reached 10.01 and no lower. S2, at the trade's price, fills 50;
// S, at 10.00 with R1 still ahead, is not reached, so no fill and no anomaly. Then R1
// executes in full and a second C on R2 at 10.01 fills S2's other 50 and still not S.
TEST(ItchEdges, S50PrintableCReachesOnlyItsPrice) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.strategy.send(kS2, 2000, Side::bid, k1001, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 3000));
  run.feed(printable_execution(2, 50, k1001, 'Y', 4000));
  EXPECT_EQ(run.ahead(kS), 100U);
  EXPECT_EQ(run.engine.stats().queue_anomalies, 0U);
  run.feed(wire::execute(kLocate, 1, 100, 4100));
  EXPECT_EQ(run.ahead(kS), 0U);
  run.feed(printable_execution(2, 50, k1001, 'Y', 4200));
  run.advance(10'000);
  EXPECT_TRUE(run.fills(kS).empty());
  EXPECT_EQ(fills_of(run.fills(kS2)),
            (std::vector<Fill>{{k1001, 50, kMaker}, {k1001, 50, kMaker}}));
  EXPECT_EQ(run.engine.stats().queue_anomalies, 0U);
}

// 32. A Cross Trade at S's price changes nothing for S.
TEST(ItchEdges, S32CrossPrintHasNoEffect) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::cross(kLocate, 5000, k1000, 3000));
  run.advance(10'000);
  EXPECT_EQ(run.ahead(kS), 100U);
  EXPECT_TRUE(run.fills(kS).empty());
}

// 33. While halted nothing fills, and a new order is refused. Trading resumes and an
// execution behind S fills it.
TEST(ItchEdges, S33Halt) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.strategy.send(kS2, 4000, Side::bid, k999, 100);
  run.feed(wire::add(kLocate, 2, 'B', 200, k1000, 3000));
  run.feed(wire::trading_action(kLocate, 'H', 3500));
  run.feed(wire::execute(kLocate, 2, 50, 4000));
  run.advance(5000);
  EXPECT_TRUE(run.fills(kS).empty());
  const auto rejected = run.strategy.reports(kS2, ReportKind::rejected);
  ASSERT_EQ(rejected.size(), 1U);
  EXPECT_EQ(rejected[0].reason, Reject::halted);
  run.feed(wire::trading_action(kLocate, 'T', 5000));
  run.feed(wire::execute(kLocate, 2, 50, 6000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 50, kMaker}}));
}

// 33, a crossing add. S bids 10.01 inside 10.00 by 10.03, as in S25. While halted, a real sell
// of 40 at 10.01 is added: it would cross S, but nothing fills. Trading resumes and a new sell
// of 20 at 10.01 crosses S, which fills 20 as maker.
TEST(ItchEdges, S33HaltedCrossingAddDoesNotFill) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1001, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::add(kLocate, 2, 'S', 100, k1003, 1001));
  run.feed(wire::trading_action(kLocate, 'H', 2500));
  run.feed(wire::add(kLocate, 3, 'S', 40, k1001, 3000));
  run.advance(4000);
  EXPECT_TRUE(run.fills(kS).empty());
  EXPECT_EQ(run.engine.stats().crossing_fills, 0U);
  EXPECT_EQ(run.queue(kS).remaining, 100U);
  run.feed(wire::trading_action(kLocate, 'T', 5000));
  run.feed(wire::add(kLocate, 4, 'S', 20, k1001, 6000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1001, 20, kMaker}}));
  EXPECT_EQ(run.engine.stats().crossing_fills, 1U);
}

// 33, a replace. S rests at 10.00 behind R1's 100. Halted at 2500, the strategy replaces S to
// 50 at 9.99 at 3000; it arrives at 3060 and is refused as halted. S keeps its price, size and
// place, and an execution behind it after the resumption fills it at 10.00.
TEST(ItchEdges, S33ReplaceWhileHaltedIsRefused) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.strategy.replace(kS, 3000, k999, 50);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 1000));
  run.feed(wire::trading_action(kLocate, 'H', 2500));
  run.advance(4000);
  const auto refused = run.strategy.reports(kS, ReportKind::replace_rejected);
  ASSERT_EQ(refused.size(), 1U);
  EXPECT_EQ(refused[0].reason, Reject::halted);
  EXPECT_EQ(refused[0].exchange_time, 3060U);
  EXPECT_EQ(refused[0].price, k1000);
  EXPECT_EQ(refused[0].remaining, 100U);
  EXPECT_TRUE(run.strategy.reports(kS, ReportKind::replaced).empty());
  EXPECT_EQ(run.queue(kS).price, k1000);
  EXPECT_EQ(run.queue(kS).remaining, 100U);
  EXPECT_EQ(run.ahead(kS), 100U);
  run.feed(wire::trading_action(kLocate, 'T', 5000));
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 5100));
  run.feed(wire::execute(kLocate, 1, 100, 6000));
  run.feed(wire::execute(kLocate, 2, 60, 6100));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 60, kMaker}}));
}

// 34. A Broken Trade does not undo a fill. It is counted.
TEST(ItchEdges, S34BrokenTradeDoesNotUndoAFill) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 2, 'B', 100, k1000, 3000));
  run.feed(wire::execute(kLocate, 2, 30, 4000));
  run.feed(wire::broken(kLocate, 5000));
  run.advance(10'000);
  EXPECT_EQ(fills_of(run.fills(kS)), (std::vector<Fill>{{k1000, 30, kMaker}}));
  EXPECT_EQ(run.queue(kS).remaining, 70U);
  EXPECT_EQ(run.engine.stats().broken_trades, 1U);
}

// 35. End of Market Hours expires the open order; a later one is refused as closed, and
// nothing is left resting.
TEST(ItchEdges, S35EndOfDay) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.strategy.send(kS2, 9100, Side::bid, k1000, 100);
  run.feed(wire::system_event('Q', 1000));
  run.feed(wire::system_event('M', 9000));
  run.finish();
  const auto expired = run.strategy.reports(kS, ReportKind::expired);
  ASSERT_EQ(expired.size(), 1U);
  EXPECT_EQ(expired[0].exchange_time, 9000U);
  EXPECT_EQ(expired[0].shares, 100U);
  const auto rejected = run.strategy.reports(kS2, ReportKind::rejected);
  ASSERT_EQ(rejected.size(), 1U);
  EXPECT_EQ(rejected[0].reason, Reject::closed);
  EXPECT_EQ(run.engine.resting_orders(), 0U);
}

// A strategy that always sets another wake, and sends on each, cannot keep the run alive
// after its input: wakes run for one round trip past the last message, L_md + L_compute +
// L_oe + L_resp = 230 ns, and a send after the close is refused.
struct Insomniac {
  template <class Context>
  void on_market(Context& context, const itch::backtest::MarketEvent& /*event*/) {
    context.wake_at(context.now() + 50);
  }
  template <class Context>
  void on_report(Context& /*context*/, const Report& /*report*/) {}
  template <class Context>
  void on_wake(Context& context) {
    last_wake = context.now();
    refused +=
        context.send({.locate = kLocate, .side = Side::bid, .price = k1000, .shares = 1}).valid()
            ? 0
            : 1;
    context.wake_at(context.now() + 50);
  }
  Nanos last_wake = 0;
  int refused = 0;
};

TEST(ItchEdges, FinishEndsARunThatAlwaysWakes) {
  Engine<> engine{config(kLatency)};
  Insomniac strategy;
  engine.feed(wire::directory(kLocate, 'Q', 'P', 100, 1), strategy);
  engine.feed(wire::add(kLocate, 1, 'B', 100, k999, 1000), strategy);
  engine.finish(strategy);
  // Woken every 50 ns from 151; the add's delivery at 1100 moves the next wake from 1101
  // to 1150, then 1200, and 1250 is past 1000 + 230.
  EXPECT_EQ(strategy.last_wake, 1200U);
  EXPECT_EQ(strategy.refused, 0);
  EXPECT_EQ(engine.resting_orders(), 0U);
  EXPECT_GT(engine.stats().expired, 0U);
}

// 36. Three messages share S's arrival nanosecond: all apply first, in file order, so
// only R2 is ahead. The strategy sees them in file order too.
TEST(ItchEdges, S36SameTimestampBurst) {
  Replay run;
  run.strategy.send(kS, 2000, Side::bid, k1000, 100);
  run.feed(wire::add(kLocate, 1, 'B', 100, k1000, 2060));
  run.feed(wire::add(kLocate, 2, 'B', 70, k1000, 2060));
  run.feed(wire::erase(kLocate, 1, 2060));
  run.advance(3000);
  EXPECT_EQ(run.ahead(kS), 70U);
  std::vector<std::uint64_t> burst;
  for (const Scripted::Seen& seen : run.strategy.seen()) {
    if (seen.exchange_time == 2060) {
      EXPECT_EQ(seen.time, 2160U);
      burst.push_back(seen.sequence);
    }
  }
  EXPECT_EQ(burst, (std::vector<std::uint64_t>{1, 2, 3}));
}

// ---- What the strategy cannot reach ---------------------------------------------------

// BT-64 check 1, the structural half of the lookahead proof: a Context has no route to
// the exchange-side book, the feed or the engine. Each line would fail to compile if it
// were written in a strategy.
using Context = itch::backtest::Context<>;
template <class C>
concept ReadsTheExchangeBook = requires(C& context) { context.exchange_book(); };
template <class C>
concept ReadsTheEngine = requires(C& context) { context.engine_; };
template <class C>
concept FeedsMessages = requires(C& context) { context.feed(std::span<const std::byte>{}); };
template <class C>
concept ReadsTheViewBook = requires(C& context) { context.book(); };
static_assert(!ReadsTheExchangeBook<Context>);
static_assert(!ReadsTheEngine<Context>);
static_assert(!FeedsMessages<Context>);
static_assert(!std::is_constructible_v<Context, Engine<>&, Nanos>);
static_assert(ReadsTheViewBook<Context>);
static_assert(itch::backtest::Strategy<Scripted>);

}  // namespace
// NOLINTEND(bugprone-unchecked-optional-access)
