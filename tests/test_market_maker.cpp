// The market maker: its two quoting models, the strategy on the backtester, the mark path, and
// property tests 15 and 16 of the research evidence (market-maker.md, "Property tests").
//
// The models' expected values are worked from the papers' formulas, written out here. The
// strategy runs on hand-built feeds and on the synthetic market of tests/backtest/market.hpp;
// nothing here reads a Nasdaq file.
#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <print>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "backtest/market.hpp"
#include "itch/backtest.hpp"
#include "itch/book.hpp"
#include "itch/mm/attribution.hpp"
#include "itch/mm/fills.hpp"
#include "itch/mm/ledger.hpp"
#include "itch/mm/marks.hpp"
#include "itch/mm/quoting.hpp"
#include "itch/mm/session.hpp"
#include "itch/mm/strategy.hpp"
#include "itch/reader.hpp"
#include "oracle/wire_builder.hpp"

namespace {

using itch::Price4;
using itch::Side;
using itch::backtest::Nanos;
using itch::mm::ModelParameters;

namespace wire = itch::oracle::wire;
namespace synthetic = itch::backtest::synthetic;

// An optional the test has just asserted is there.
double value(const std::optional<double>& x) {
  EXPECT_TRUE(x.has_value());
  return x.value_or(std::nan(""));
}

// The paper's own numerical experiment (Avellaneda and Stoikov 2008, p. 222).
constexpr ModelParameters kPaper{.gamma = 0.1, .sigma = 2.0, .k = 1.5, .a = 140.0};

// ---- Avellaneda-Stoikov ---------------------------------------------------------------------

TEST(AvellanedaStoikov, ReservationPriceAndSpreadAreEquations29And30) {
  const itch::mm::AvellanedaStoikov model{kPaper};
  // r = s − qγσ²(T − t): 100 − 3 × 0.1 × 4 × 0.5.
  EXPECT_DOUBLE_EQ(model.reservation(100.0, 3.0, 0.5), 99.4);
  EXPECT_DOUBLE_EQ(model.reservation(100.0, -2.0, 0.25), 100.2);
  EXPECT_DOUBLE_EQ(model.reservation(100.0, 5.0, 0.0), 100.0);
  // γσ²(T − t) + (2/γ) ln(1 + γ/k), with (2/0.1) ln(1 + 0.1/1.5) = 20 ln(16/15).
  const double constant = 20.0 * std::log(16.0 / 15.0);
  EXPECT_DOUBLE_EQ(model.spread(0.0), constant);
  EXPECT_DOUBLE_EQ(model.spread(1.0), 0.4 + constant);
  const itch::mm::Targets quote = model.quote(100.0, 1.0, 1.0);
  EXPECT_EQ(value(quote.bid), 99.6 - ((0.4 + constant) / 2.0));
  EXPECT_EQ(value(quote.ask), 99.6 + ((0.4 + constant) / 2.0));
}

// The tables' average spread is eq. 30 averaged over t in [0, 1]: γσ²/2 + (2/γ) ln(1 + γ/k), the
// spread at τ = 1/2. The evidence works these by hand as 1.4908, 1.3489 and 3.0217 (MK-11).
TEST(AvellanedaStoikov, TheContinuousAverageSpreadRoundsToThePapersTables) {
  struct Table {
    double gamma;
    double average;
    double printed;
  };
  for (const Table table : {Table{.gamma = 0.1, .average = 1.4907704227514234, .printed = 1.49},
                            Table{.gamma = 0.01, .average = 1.3489085437337158, .printed = 1.35},
                            Table{.gamma = 1.0, .average = 3.021651247531981, .printed = 3.02}}) {
    ModelParameters parameters = kPaper;
    parameters.gamma = table.gamma;
    const itch::mm::AvellanedaStoikov model{parameters};
    EXPECT_DOUBLE_EQ(model.spread(0.5), table.average) << table.gamma;
    EXPECT_EQ(std::round(model.spread(0.5) * 100.0), std::round(table.printed * 100.0));
  }
}

// ---- GLFT, stationary ------------------------------------------------------------------------

// With Q = 1, M = [[α, −η, 0], [−η, 0, −η], [0, −η, α]]. Its ground state is even, (a, b, a),
// so αa − ηb = λa and −2ηa = λb, and λ² − αλ − 2η² = 0: λ = (α − √(α² + 8η²))/2. Then
// f₀/f₁ = (α − λ)/η, δᵇ(0) = c + (1/k) ln(f₀/f₁) and δᵇ(−1) = c − (1/k) ln(f₀/f₁).
TEST(Glft, OneLotOfInventoryMatchesTheHandSolution) {
  const ModelParameters p{.gamma = 0.05, .sigma = 0.8, .k = 0.6, .a = 2.0};
  const itch::mm::GlftStationary model{p, 1};
  const double alpha = 0.5 * p.k * p.gamma * p.sigma * p.sigma;
  const double eta = p.a * std::pow(1.0 + (p.gamma / p.k), -(1.0 + (p.k / p.gamma)));
  EXPECT_DOUBLE_EQ(model.alpha(), alpha);
  EXPECT_DOUBLE_EQ(model.eta(), eta);
  const double lambda = (alpha - std::sqrt((alpha * alpha) + (8.0 * eta * eta))) / 2.0;
  EXPECT_NEAR(model.eigenvalue(), lambda, 1e-15);
  const double c = std::log(1.0 + (p.gamma / p.k)) / p.gamma;
  const double ratio = (alpha - lambda) / eta;
  EXPECT_NEAR(value(model.bid_depth(0)), c + (std::log(ratio) / p.k), 1e-12);
  EXPECT_NEAR(value(model.bid_depth(-1)), c - (std::log(ratio) / p.k), 1e-12);
  EXPECT_FALSE(model.bid_depth(1));
  EXPECT_FALSE(model.ask_depth(-1));
}

TEST(Glft, QuotesAreSymmetricAndSkewWithInventory) {
  const ModelParameters p{.gamma = 0.01, .sigma = 0.4, .k = 0.3, .a = 0.9};  // GLFT's figures.
  const itch::mm::GlftStationary model{p, 10};
  for (std::int64_t q = -9; q <= 9; ++q) {
    EXPECT_EQ(value(model.ask_depth(q)), value(model.bid_depth(-q))) << q;
  }
  for (std::int64_t q = -9; q <= 8; ++q) {
    EXPECT_LT(value(model.bid_depth(q)), value(model.bid_depth(q + 1))) << q;
    EXPECT_GT(value(model.ask_depth(q)), value(model.ask_depth(q + 1))) << q;
  }
  EXPECT_FALSE(model.bid_depth(10));
  EXPECT_FALSE(model.ask_depth(-10));
  // The row at q = 0 is −2η f₁ = λ f₀, which the recurrence from q = Q never used: a check of
  // the eigenpair to rounding, not an identity.
  const double outer = value(model.bid_depth(0)) - (std::log1p(p.gamma / p.k) / p.gamma);
  EXPECT_NEAR(std::exp(outer * p.k), -2.0 * model.eta() / model.eigenvalue(), 1e-9);
}

// GLFT's closed form (p. 11) approximates the exact quotes near q = 0; at q = 0 it gives the
// stationary spread ψ = (2/γ) ln(1 + γ/k) + ω. Shown, not held to a tolerance: GLFT say it fits
// poorly at large q.
TEST(Glft, TheClosedFormIsGlftsApproximationAsPrinted) {
  const ModelParameters p{.gamma = 0.01, .sigma = 0.4, .k = 0.3, .a = 0.9};
  const itch::mm::GlftApproximation zero = itch::mm::glft_approximation(p, 0);
  const double c = std::log1p(p.gamma / p.k) / p.gamma;
  const double omega = std::sqrt((p.sigma * p.sigma * p.gamma / (2.0 * p.k * p.a)) *
                                 std::pow(1.0 + (p.gamma / p.k), 1.0 + (p.k / p.gamma)));
  EXPECT_DOUBLE_EQ(zero.bid, c + (omega / 2.0));
  EXPECT_DOUBLE_EQ(zero.ask, c + (omega / 2.0));
  const itch::mm::GlftApproximation two = itch::mm::glft_approximation(p, 2);
  EXPECT_DOUBLE_EQ(two.bid, c + (2.5 * omega));
  EXPECT_DOUBLE_EQ(two.ask, c - (1.5 * omega));
  const itch::mm::GlftStationary exact{p, 10};
  std::println("glft q=0: exact {:.6f} approximation {:.6f} ticks", value(exact.bid_depth(0)),
               zero.bid);
}

// ---- The tick grid ---------------------------------------------------------------------------

TEST(Quoter, TargetsRoundAwayFromTheMid) {
  using itch::mm::on_grid;
  EXPECT_EQ(on_grid(999.5, Side::bid, 100), std::optional<Price4>{99'900});
  EXPECT_EQ(on_grid(999.5, Side::ask, 100), std::optional<Price4>{100'000});
  EXPECT_EQ(on_grid(1000.0, Side::bid, 100), std::optional<Price4>{100'000});
  EXPECT_EQ(on_grid(1000.0, Side::ask, 100), std::optional<Price4>{100'000});
  EXPECT_FALSE(on_grid(std::nan(""), Side::bid, 100));
  EXPECT_FALSE(on_grid(HUGE_VAL, Side::ask, 100));
  EXPECT_FALSE(on_grid(0.5, Side::bid, 100));
  EXPECT_FALSE(on_grid(5e7, Side::ask, 100));
}

// ---- The strategy on hand-built feeds --------------------------------------------------------

// A model that quotes a fixed distance from the mid, so that the strategy's own rules are what
// a test sees.
struct Offsets {
  double bid = -1.5;
  double ask = 1.5;

  [[nodiscard]] itch::mm::Targets operator()(double mid, std::int64_t /*position*/,
                                             std::uint32_t /*lot*/, Nanos /*now*/) const noexcept {
    return {.bid = mid + bid, .ask = mid + ask};
  }
};

constexpr std::uint16_t kLocate = 1;
constexpr Nanos kLatencyStep = 1'000;
const itch::backtest::Latency kLatency{.market_data = kLatencyStep,
                                       .order_entry = kLatencyStep,
                                       .response = kLatencyStep,
                                       .compute = 0};

// The switches of the with-and-without runs: Q-off names its rule, L-off zeroes every latency.
struct Switches {
  itch::backtest::QueueModel queue = itch::backtest::QueueModel::exact;
  bool latency_off = false;
};

itch::backtest::Config config(itch::backtest::Latency latency = kLatency, Switches switches = {}) {
  itch::backtest::Config out;
  out.date = 20191230;
  out.latency = latency;
  out.queue = switches.queue;
  out.latency_off = switches.latency_off;
  out.symbols = {synthetic::kSymbol};
  out.exchange_capacity = {.orders = 1U << 16U,
                           .levels = 1024,
                           .index_pages = 64,
                           .index_bits = 18,
                           .level_slots = 1U << 14U};
  out.view_capacity = out.exchange_capacity;
  out.max_orders = 16;
  out.queue_capacity = 1U << 12U;
  return out;
}

itch::mm::QuoterConfig quoter_config(std::int64_t max_lots = 5) {
  return {.symbol = synthetic::kSymbol,
          .lot = 100,
          .tick = 100,
          .max_lots = max_lots,
          .start = 0,
          .stop = 1'000'000'000'000,
          .max_fills = 1U << 16U};
}

template <class Model>
class Feed {
 public:
  explicit Feed(Model model, std::int64_t max_lots = 5)
      : engine{config()}, maker{quoter_config(max_lots), std::move(model)} {}

  void put(const wire::Bytes& payload) {
    engine.feed(payload, maker);
    last = synthetic::time_of(payload);
  }
  // Every event up to and including `later` after the last message.
  void settle(Nanos later = 10 * kLatencyStep) { engine.advance(last + later + 1, maker); }

  void open(Nanos time) {
    put(wire::directory(kLocate, 'Q', 'P', 100, time));
    put(wire::trading_action(kLocate, 'T', time));
    put(wire::system_event('Q', time));
  }

  itch::backtest::Engine<> engine;
  itch::mm::Quoter<Model> maker;
  Nanos last = 0;
};

// Bid $10.00, ask $10.02: the mid is 1001 ticks, so ±1.5 ticks round to $9.99 and $10.03.
TEST(Quoter, QuotesTheModelsTargetsOnTheGrid) {
  Feed<Offsets> feed{Offsets{}};
  feed.open(1'000);
  feed.put(wire::add(kLocate, 1, 'B', 100, 100'000, 2'000));
  feed.put(wire::add(kLocate, 2, 'S', 100, 100'200, 3'000));
  feed.settle();
  EXPECT_EQ(feed.maker.quoted(Side::bid), std::optional<Price4>{99'900});
  EXPECT_EQ(feed.maker.quoted(Side::ask), std::optional<Price4>{100'300});
  EXPECT_EQ(feed.engine.resting_orders(), 2U);
  EXPECT_EQ(feed.maker.counts().sends, 2U);
}

// A message that leaves the rounded targets where they were sends nothing: a replace would cost
// the queue position. A move of the mid replaces the side whose rounded target moved.
TEST(Quoter, RequotesOnlyWhenTheRoundedTargetMoves) {
  Feed<Offsets> feed{Offsets{}};
  feed.open(1'000);
  feed.put(wire::add(kLocate, 1, 'B', 100, 100'000, 2'000));
  feed.put(wire::add(kLocate, 2, 'S', 100, 100'200, 3'000));
  feed.settle();
  feed.put(wire::add(kLocate, 3, 'B', 300, 100'000, 20'000));
  feed.put(wire::add(kLocate, 4, 'S', 50, 100'300, 30'000));
  feed.settle();
  EXPECT_EQ(feed.maker.counts().replaces, 0U);
  // Bid $10.01: the mid is 1001.5, so the bid's target is 1000 and the ask's still rounds to
  // 1003.
  feed.put(wire::add(kLocate, 5, 'B', 100, 100'100, 40'000));
  feed.settle();
  EXPECT_EQ(feed.maker.counts().replaces, 1U);
  EXPECT_EQ(feed.maker.quoted(Side::bid), std::optional<Price4>{100'000});
  EXPECT_EQ(feed.maker.quoted(Side::ask), std::optional<Price4>{100'300});
}

// A target through the far side is held one tick off it: the maker never takes.
TEST(Quoter, NeverPricesThroughTheOtherSide) {
  Feed<Offsets> feed{Offsets{.bid = 3.0, .ask = -3.0}};
  feed.open(1'000);
  feed.put(wire::add(kLocate, 1, 'B', 100, 100'000, 2'000));
  feed.put(wire::add(kLocate, 2, 'S', 100, 100'200, 3'000));
  feed.settle();
  EXPECT_EQ(feed.maker.quoted(Side::bid), std::optional<Price4>{100'100});
  EXPECT_EQ(feed.maker.quoted(Side::ask), std::optional<Price4>{100'100});
  EXPECT_EQ(feed.engine.stats().taker_fills, 0U);
}

// With max_lots 1 and a bid of one lot filled, the position is at the bound: the bid goes and
// the ask stays.
TEST(Quoter, StopsBuyingAtTheInventoryBound) {
  Feed<Offsets> feed{Offsets{.bid = -0.5, .ask = 1.5}, 1};
  feed.open(1'000);
  feed.put(wire::add(kLocate, 1, 'B', 100, 100'000, 2'000));
  feed.put(wire::add(kLocate, 2, 'S', 100, 100'200, 3'000));
  feed.settle();
  ASSERT_EQ(feed.maker.quoted(Side::bid), std::optional<Price4>{100'000});
  feed.put(wire::execute(kLocate, 1, 100, 20'000));  // The real bid ahead of ours.
  feed.put(wire::add(kLocate, 6, 'B', 100, 100'000, 21'000));
  feed.put(wire::execute(kLocate, 6, 100, 22'000));  // Behind ours: a seller met ours first.
  feed.settle();
  EXPECT_EQ(feed.maker.position(), 100);
  ASSERT_EQ(feed.maker.fills().size(), 1U);
  EXPECT_EQ(feed.maker.fills()[0].quantity, 100);
  EXPECT_EQ(feed.maker.fills()[0].price, 100'000U);
  EXPECT_EQ(feed.maker.fills()[0].time, 22'000U);
  EXPECT_LT(feed.maker.fills()[0].fee, 0);  // The add credit.
  feed.put(wire::add(kLocate, 7, 'B', 100, 99'900, 30'000));
  feed.settle();
  EXPECT_FALSE(feed.maker.quoted(Side::bid));
  EXPECT_TRUE(feed.maker.quoted(Side::ask));
}

// ---- The strategy on the synthetic market ----------------------------------------------------

constexpr int kSteps = 60'000;

std::vector<std::byte> market(std::uint32_t seed) {
  return synthetic::frame(synthetic::RandomMarket{seed}.generate(kSteps));
}

// The parameters of the switch matrix: in ticks and seconds, sized so that both models quote
// near the touch of the synthetic market.
constexpr ModelParameters kSynthetic{.gamma = 0.1, .sigma = 5.0, .k = 1.5, .a = 140.0};
constexpr Nanos kHorizonEnd = 200'000'000;  // Past the last synthetic message.

struct Outcome {
  std::vector<itch::mm::Fill> fills;
  std::vector<itch::mm::MarkPoint> marks;
  std::uint64_t mark_hash = 0;
  std::string report;
  std::int64_t max_position = 0;
};

template <class Model>
Outcome run_session(const std::vector<std::byte>& stream, itch::backtest::Latency latency,
                    const Model& model, Switches switches = {}) {
  itch::backtest::Engine<> engine{config(latency, switches)};
  itch::mm::Quoter<Model> maker{quoter_config(), model};
  itch::mm::MarkRecorder marks{synthetic::kSymbol, 1U << 18U};
  static_cast<void>(itch::mm::replay(engine, stream, maker, marks));
  EXPECT_EQ(engine.stats().overflows, 0U);
  EXPECT_EQ(engine.stats().refused_messages, 0U);
  EXPECT_EQ(marks.overflows(), 0U);
  EXPECT_EQ(maker.counts().fill_overflows, 0U);
  Outcome out;
  out.fills.assign(maker.fills().begin(), maker.fills().end());
  out.marks.assign(marks.points().begin(), marks.points().end());
  out.mark_hash = marks.hash();
  std::int64_t position = 0;
  for (const itch::mm::Fill& fill : out.fills) {
    position += fill.quantity;
    out.max_position = std::max(out.max_position, position < 0 ? -position : position);
  }
  // From the first valid mark: no fill can come before the strategy has seen a mid.
  const itch::mm::Window window{.open = out.marks.empty() ? 0 : out.marks.front().time,
                                .close = engine.clock(),
                                .opening_position = 0};
  const auto report = itch::mm::format_report({.fills = out.fills,
                                               .marks = &marks,
                                               .window = window,
                                               .horizon = itch::mm::kPrimaryHorizon});
  EXPECT_TRUE(report.has_value());
  out.report = report.value_or("");
  return out;
}

const itch::backtest::Latency kBaseLatency{};

// An order-sensitive hash of a fill log, to tell two runs' fills apart.
std::uint64_t digest(const std::vector<itch::mm::Fill>& fills) {
  itch::backtest::EventHash hash;
  for (const itch::mm::Fill& fill : fills) {
    hash(fill.time);
    hash(static_cast<std::uint64_t>(fill.quantity));
    hash(fill.price);
    hash(static_cast<std::uint64_t>(fill.fee));
  }
  return hash.digest();
}

// Test 15: every run of a switch matrix sees one mark path. The switches are those of the
// evaluation runs, Base, Q-off under each rule, L-off and both off, and beside them the model
// and γ; the path is also the one a plain book replays. Each switch on its own must change the
// fills, or the equal hashes would prove nothing.
TEST(MarketMaker, P15TheMarkPathIsTheSameInEveryRunOfASwitchMatrix) {
  using itch::backtest::QueueModel;
  const std::vector<std::byte> stream = market(31);
  std::set<std::uint64_t> hashes;
  std::set<std::size_t> fill_counts;
  std::set<std::uint64_t> base_and_single_switches;  // Digests of the fill logs.
  for (const QueueModel queue :
       {QueueModel::exact, QueueModel::conservative, QueueModel::proportional}) {
    for (const bool latency_off : {false, true}) {
      for (const double gamma : {0.01, 0.1}) {
        ModelParameters parameters = kSynthetic;
        parameters.gamma = gamma;
        const Switches switches{.queue = queue, .latency_off = latency_off};
        const Outcome as =
            run_session(stream, kBaseLatency, itch::mm::AsModel{parameters, kHorizonEnd}, switches);
        const Outcome glft =
            run_session(stream, kBaseLatency, itch::mm::GlftModel{parameters, 5}, switches);
        hashes.insert(as.mark_hash);
        hashes.insert(glft.mark_hash);
        fill_counts.insert(as.fills.size());
        fill_counts.insert(glft.fills.size());
        if (gamma == 0.1 && (queue == QueueModel::exact || !latency_off)) {
          base_and_single_switches.insert(digest(as.fills));
        }
      }
    }
  }
  EXPECT_EQ(hashes.size(), 1U);
  EXPECT_GT(fill_counts.size(), 1U) << "the switches changed no fill, so they prove nothing";
  EXPECT_EQ(base_and_single_switches.size(), 4U)
      << "Base, Q-off under each rule and L-off must each give other fills";

  itch::FastBook<> plain{config().exchange_capacity};
  itch::mm::MarkRecorder marks{synthetic::kSymbol, 1U << 18U};
  itch::FrameReader reader{stream};
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    static_cast<void>(plain.apply(frame.payload));
    marks.observe(plain, frame.payload);
  }
  EXPECT_EQ(hashes.count(marks.hash()), 1U);
}

// Test 16: two runs of one configuration give the same report, byte for byte.
TEST(MarketMaker, P16TwoRunsOfOneConfigurationGiveTheSameReport) {
  const std::vector<std::byte> stream = market(32);
  const itch::mm::AsModel model{kSynthetic, kHorizonEnd};
  const Outcome first = run_session(stream, kBaseLatency, model);
  const Outcome second = run_session(stream, kBaseLatency, model);
  EXPECT_FALSE(first.fills.empty());
  EXPECT_EQ(first.report, second.report);
  EXPECT_NE(first.report.find("identity holds"), std::string::npos) << first.report;
  std::println("{}", first.report);
}

// The whole chain on many markets: the engine's fills and fees, the recorded path, and the
// identity, with the inventory bound never passed, under Base and each switch.
TEST(MarketMaker, TheIdentityHoldsOnEveryRunAndInventoryStaysInBounds) {
  using itch::backtest::QueueModel;
  std::uint64_t fills = 0;
  for (std::uint32_t seed = 40; seed < 48; ++seed) {
    const std::vector<std::byte> stream = market(seed);
    for (const Switches switches :
         {Switches{}, Switches{.queue = QueueModel::exact, .latency_off = true},
          Switches{.queue = QueueModel::conservative, .latency_off = false},
          Switches{.queue = QueueModel::proportional, .latency_off = false}}) {
      const Outcome as =
          run_session(stream, kBaseLatency, itch::mm::AsModel{kSynthetic, kHorizonEnd}, switches);
      const Outcome glft =
          run_session(stream, kBaseLatency, itch::mm::GlftModel{kSynthetic, 5}, switches);
      for (const Outcome* each : {&as, &glft}) {
        EXPECT_NE(each->report.find("identity holds"), std::string::npos) << seed;
        EXPECT_LE(each->max_position, 500) << seed;
        fills += each->fills.size();
      }
    }
  }
  EXPECT_GT(fills, 1'000U);
}

}  // namespace
