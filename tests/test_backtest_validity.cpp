// The backtester's validity gates, the rest of T-BT-2: scenario 8 (ahead never rises,
// checked against a recount after every message), the full market-data delay check of
// scenario 15, and scenarios 42 to 48 of the research evidence.
//
// The lookahead proof (BT-64) has three parts here, beside the compile-time half in
// test_backtest.cpp:
//   - prefix invariance: every decision up to T + min(L_md, L_resp) is the same on the
//     feed cut after T as on the whole feed;
//   - future perturbation: the same, with every add after T changed;
//   - an oracle control, a strategy that reads the feed's future, which both must catch.
// Nothing at exchange time t can reach the strategy before t + L_md as market data or
// t + L_resp as a report, so a decision at T + min(L_md, L_resp) or before can depend only
// on the feed up to T. A difference there is information from the future.
//
// Every stream is a seeded synthetic market (backtest/market.hpp). The strategies trade,
// so each gate is shown to have had fills, replaces and takes to look at.
#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <print>
#include <random>
#include <span>
#include <vector>

#include "backtest/market.hpp"
#include "backtest/scripted.hpp"
#include "itch/backtest.hpp"
#include "itch/book.hpp"
#include "oracle/canonical.hpp"
#include "oracle/reference_book.hpp"
#include "support/encode.hpp"

namespace {

using itch::Price4;
using itch::Side;
using itch::backtest::Config;
using itch::backtest::Engine;
using itch::backtest::Latency;
using itch::backtest::MarketEvent;
using itch::backtest::Nanos;
using itch::backtest::OrderId;
using itch::backtest::OrderRequest;
using itch::backtest::Report;
using itch::backtest::ReportKind;
using itch::backtest::Tif;
using itch::backtest::synthetic::kLocate;
using itch::backtest::synthetic::kSymbol;
using itch::backtest::synthetic::kTick;
using itch::backtest::synthetic::Message;
using itch::backtest::synthetic::Null;
using itch::backtest::synthetic::RandomMarket;

constexpr Latency kLatency{
    .market_data = 3'000, .order_entry = 2'000, .response = 2'500, .compute = 500};
constexpr itch::BookCapacity kCapacity{
    .orders = 4096, .levels = 512, .index_pages = 8, .index_bits = 16, .level_slots = 8192};
constexpr std::array<std::uint16_t, 1> kLocates{kLocate};

Config config(Latency latency = kLatency) {
  Config config;
  config.date = 20191230;
  config.latency = latency;
  config.symbols = {kSymbol};
  config.exchange_capacity = kCapacity;
  config.view_capacity = kCapacity;
  config.max_orders = 256;
  config.queue_capacity = 4096;
  return config;
}

template <class Book>
std::uint64_t book_hash(const Book& book) {
  itch::oracle::WordHash hash;
  itch::oracle::emit_fast(book, std::span<const std::uint16_t>{kLocates}, hash);
  return hash.value();
}

// Joins the best bid and the best ask of the view book with 100 shares each, follows them
// with replaces, and every sixteenth event takes 100 at the far side with an IOC. Every
// call it makes is logged with its time.
//
// Given `future`, it becomes the oracle control: it bids at the price of the last add the
// feed will carry in the next 100 µs, which it reads from the feed. No honest strategy could.
class Quoter {
 public:
  struct Decision {
    Nanos time;
    int op;
    Side side;
    Price4 price;
    std::uint32_t shares;
    friend bool operator==(const Decision&, const Decision&) = default;
  };

  explicit Quoter(const std::vector<Message>* future = nullptr, Nanos market_data = 0)
      : future_{future}, market_data_{market_data} {}

  template <class Context>
  void on_market(Context& context, const MarketEvent& /*event*/) {
    ++events_;
    quote(context);
    if (events_ % 16 == 0) {
      take(context);
    }
  }

  template <class Context>
  void on_report(Context& context, const Report& report) {
    for (Mine& mine : mine_) {
      if (!mine.id.valid() || report.order != mine.id) {
        continue;
      }
      switch (report.kind) {
        case ReportKind::accepted:
        case ReportKind::replaced: mine.busy = false; break;
        case ReportKind::fill:
          if (report.remaining == 0) {
            mine = {};
          }
          break;
        case ReportKind::replace_rejected:
          mine.busy = false;
          if (report.reason == itch::backtest::Reject::not_live) {
            mine = {};
          }
          break;
        case ReportKind::rejected:
          stopped_ = report.reason == itch::backtest::Reject::closed;
          mine = {};
          break;
        case ReportKind::cancelled:
        case ReportKind::cancel_rejected:
        case ReportKind::expired: mine = {}; break;
      }
    }
    if (report.kind == ReportKind::fill) {
      ++fills_;
    }
    quote(context);
  }

  template <class Context>
  void on_wake(Context& /*context*/) {}

  [[nodiscard]] const std::vector<Decision>& decisions() const { return decisions_; }
  [[nodiscard]] std::uint64_t fills() const { return fills_; }
  [[nodiscard]] std::vector<OrderId> resting_candidates() const {
    return {mine_[0].id, mine_[1].id};
  }

 private:
  struct Mine {
    OrderId id{};
    Price4 price = 0;
    bool busy = false;
  };

  template <class Context>
  void quote(Context& context) {
    if (stopped_) {
      return;
    }
    for (const Side side : {Side::bid, Side::ask}) {
      const itch::TopOfBook top = context.book().best(kLocate, side);
      if (!top.present) {
        continue;
      }
      Price4 target = top.price;
      if (side == Side::bid && future_ != nullptr) {
        target = peek(context.now()).value_or(target);
      }
      Mine& mine = mine_[static_cast<std::size_t>(side)];
      if (mine.busy) {
        continue;
      }
      if (!mine.id.valid()) {
        mine.id = context.send({.locate = kLocate, .side = side, .price = target, .shares = 100});
        mine.price = target;
        mine.busy = mine.id.valid();
        decisions_.push_back({context.now(), 0, side, target, 100});
      } else if (mine.price != target) {
        context.replace(mine.id, target, 100);
        mine.price = target;
        mine.busy = true;
        decisions_.push_back({context.now(), 1, side, target, 100});
      }
    }
  }

  template <class Context>
  void take(Context& context) {
    const Side side = events_ % 32 == 0 ? Side::bid : Side::ask;
    const itch::TopOfBook far =
        context.book().best(kLocate, side == Side::bid ? Side::ask : Side::bid);
    if (!far.present) {
      return;
    }
    static_cast<void>(context.send(
        {.locate = kLocate, .side = side, .price = far.price, .shares = 100, .tif = Tif::ioc}));
    decisions_.push_back({context.now(), 2, side, far.price, 100});
  }

  // The price of the last add stamped up to 100 µs past now, read from the feed itself:
  // the book's price a little ahead of time, which no honest strategy could know.
  [[nodiscard]] std::optional<Price4> peek(Nanos now) const {
    std::optional<Price4> price;
    for (const Message& message : *future_) {
      if (message.time > now + kOracleHorizon) {
        break;
      }
      if (message.time + market_data_ > now && static_cast<char>(message.payload[0]) == 'A') {
        price = itch::AddOrder::from_wire(message.payload.data()).price;
      }
    }
    return price;
  }

  static constexpr Nanos kOracleHorizon = 100'000;

  const std::vector<Message>* future_;
  Nanos market_data_;
  std::array<Mine, 2> mine_{};
  std::uint64_t events_ = 0;
  std::uint64_t fills_ = 0;
  bool stopped_ = false;
  std::vector<Decision> decisions_;
};

std::vector<Message> market(std::uint32_t seed, int steps = 3000) {
  return RandomMarket{seed}.generate(steps);
}

// The decisions a fresh Quoter makes on `messages`, honest or as the oracle on them.
std::vector<Quoter::Decision> decisions_on(const std::vector<Message>& messages, bool oracle) {
  Engine<> engine{config()};
  Quoter quoter{oracle ? &messages : nullptr, kLatency.market_data};
  for (const Message& message : messages) {
    engine.feed(message.payload, quoter);
  }
  engine.finish(quoter);
  return quoter.decisions();
}

[[nodiscard]] std::vector<Quoter::Decision> up_to(const std::vector<Quoter::Decision>& all,
                                                  Nanos until) {
  std::vector<Quoter::Decision> out;
  for (const Quoter::Decision& decision : all) {
    if (decision.time <= until) {
      out.push_back(decision);
    }
  }
  return out;
}

constexpr Nanos kWindow = std::min(kLatency.market_data, kLatency.response);

// Cuts after the message at each of 20 places through the feed, keeping every message at
// that same nanosecond.
std::vector<Nanos> cuts(const std::vector<Message>& messages) {
  std::vector<Nanos> out;
  for (std::size_t i = 1; i <= 20; ++i) {
    out.push_back(messages[(messages.size() * i / 21)].time);
  }
  return out;
}

std::vector<Message> prefix(const std::vector<Message>& messages, Nanos cut) {
  std::vector<Message> out;
  for (const Message& message : messages) {
    if (message.time <= cut) {
      out.push_back(message);
    }
  }
  return out;
}

// Every add after the cut doubles its shares and moves a tick away from mid. References
// and executions stay valid: no execution or cancel is larger than the original order.
std::vector<Message> perturbed(const std::vector<Message>& messages, Nanos cut) {
  std::vector<Message> out = messages;
  for (Message& message : out) {
    if (message.time > cut && static_cast<char>(message.payload[0]) == 'A') {
      itch::AddOrder add = itch::AddOrder::from_wire(message.payload.data());
      add.shares *= 2;
      add.price = add.side == 'B' ? add.price - kTick : add.price + kTick;
      message.payload = itch::testing::encode(add);
    }
  }
  return out;
}

// Where the decisions on `other` first part from those on `messages` within the window
// after `cut`, if they do.
std::optional<std::size_t> divergence(const std::vector<Message>& messages,
                                      const std::vector<Message>& other, Nanos cut, bool oracle) {
  const auto whole = up_to(decisions_on(messages, oracle), cut + kWindow);
  const auto cut_run = up_to(decisions_on(other, oracle), cut + kWindow);
  const std::size_t common = std::min(whole.size(), cut_run.size());
  for (std::size_t i = 0; i < common; ++i) {
    if (whole[i] != cut_run[i]) {
      return i;
    }
  }
  if (whole.size() != cut_run.size()) {
    return common;
  }
  return std::nullopt;
}

// ---- 42 and 43, with 44 as their control ----------------------------------------------

TEST(Lookahead, S42PrefixInvariance) {
  for (const std::uint32_t seed : {1U, 2U, 3U}) {
    const std::vector<Message> messages = market(seed);
    std::size_t compared = 0;
    for (const Nanos cut : cuts(messages)) {
      const auto where = divergence(messages, prefix(messages, cut), cut, false);
      EXPECT_FALSE(where.has_value())
          << "seed " << seed << " cut " << cut << " decision " << where.value_or(0);
      compared += up_to(decisions_on(prefix(messages, cut), false), cut + kWindow).size();
    }
    EXPECT_GT(compared, 1000U) << "too few decisions to compare";
  }
}

TEST(Lookahead, S43FuturePerturbation) {
  for (const std::uint32_t seed : {4U, 5U, 6U}) {
    const std::vector<Message> messages = market(seed);
    for (const Nanos cut : cuts(messages)) {
      const auto where = divergence(messages, perturbed(messages, cut), cut, false);
      EXPECT_FALSE(where.has_value()) << "seed " << seed << " cut " << cut;
    }
    // The perturbation changes what comes after: the decisions over the whole run differ.
    const Nanos middle = cuts(messages)[10];
    EXPECT_NE(decisions_on(messages, false), decisions_on(perturbed(messages, middle), false));
  }
}

// 44. The oracle reads the future. Both checks must catch it, at every cut.
TEST(Lookahead, S44OracleControlIsCaught) {
  const std::vector<Message> messages = market(7);
  int caught_by_prefix = 0;
  int caught_by_perturbation = 0;
  for (const Nanos cut : cuts(messages)) {
    caught_by_prefix += divergence(messages, prefix(messages, cut), cut, true).has_value() ? 1 : 0;
    caught_by_perturbation +=
        divergence(messages, perturbed(messages, cut), cut, true).has_value() ? 1 : 0;
  }
  std::println("oracle caught at {} of 20 cuts by prefix, {} of 20 by perturbation",
               caught_by_prefix, caught_by_perturbation);
  EXPECT_EQ(caught_by_prefix, 20);
  EXPECT_EQ(caught_by_perturbation, 20);
}

// ---- 15, in full: what the strategy sees, and when -----------------------------------

// At every callback, the view book must be the book of every message stamped before
// now - L_md, and of no later one; at a delivery, of the messages up to the one delivered,
// since a burst of one nanosecond arrives one message at a time. A mirror built from the
// feed itself is the reference.
class ViewChecker {
 public:
  explicit ViewChecker(const std::vector<Message>& messages)
      : messages_{&messages}, mirror_{kCapacity} {}

  template <class Context>
  void on_market(Context& context, const MarketEvent& event) {
    late_or_early_ += event.time == event.exchange_time + kLatency.market_data ? 0U : 1U;
    while (next_ <= event.sequence && next_ < messages_->size()) {
      apply_next();
    }
    check(context);
  }
  template <class Context>
  void on_report(Context& context, const Report& /*report*/) {
    catch_up(context.now());
    check(context);
  }
  template <class Context>
  void on_wake(Context& context) {
    catch_up(context.now());
    check(context);
  }

  std::uint64_t checks = 0;
  std::uint64_t mismatches = 0;
  std::uint64_t late_or_early_ = 0;

 private:
  void apply_next() {
    static_cast<void>(mirror_.apply((*messages_)[next_].payload));
    ++next_;
  }
  void catch_up(Nanos now) {
    while (next_ < messages_->size() && (*messages_)[next_].time + kLatency.market_data <= now) {
      apply_next();
    }
  }

  template <class Context>
  void check(Context& context) {
    ++checks;
    // Nothing stamped before now - L_md may still be missing.
    const bool complete = next_ == messages_->size() ||
                          (*messages_)[next_].time + kLatency.market_data >= context.now();
    mismatches += complete && book_hash(context.book()) == book_hash(mirror_) ? 0U : 1U;
  }

  const std::vector<Message>* messages_;
  itch::FastBook<> mirror_;
  std::size_t next_ = 0;
};

TEST(Lookahead, S15TheStrategySeesTheBookExactlyLmdLate) {
  const std::vector<Message> messages = market(8);
  Engine<> engine{config()};
  ViewChecker checker{messages};
  for (const Message& message : messages) {
    engine.feed(message.payload, checker);
  }
  engine.finish(checker);
  EXPECT_EQ(checker.late_or_early_, 0U);
  EXPECT_EQ(checker.mismatches, 0U);
  EXPECT_GE(checker.checks, messages.size());
}

// ---- 8: ahead never rises, and always equals a recount --------------------------------

// Before each message, every order that has arrived since the last is given the set of
// real references resting at its price: the orders ahead of it. After each message, its
// ahead must be the shares those references still hold, never more than before, and the
// engine's own recount must agree.
TEST(QueueProperty, S08AheadNeverRisesAndMatchesARecount) {
  std::uint64_t checked = 0;
  std::uint64_t fell = 0;
  for (const std::uint32_t seed : {11U, 12U, 13U, 14U}) {
    const std::vector<Message> messages = market(seed, 4000);
    Engine<> engine{config()};
    Quoter quoter;
    struct Watched {
      std::uint64_t arrival;
      Side side;
      Price4 price;
      std::vector<std::uint64_t> ahead_of_it;
      std::uint64_t last;
    };
    std::map<std::uint32_t, Watched> watched;  // By order slot.
    for (const Message& message : messages) {
      engine.advance(message.time, quoter);
      const std::vector<OrderId> candidates = quoter.resting_candidates();
      for (std::size_t index = 0; index < candidates.size(); ++index) {
        const OrderId id = candidates[index];
        const auto state = engine.queue(id);
        if (!state ||
            (watched.contains(id.slot) && watched.at(id.slot).arrival == state->arrival)) {
          continue;
        }
        // The quoter's first order is its bid, the second its ask.
        const Side side = index == 0 ? Side::bid : Side::ask;
        Watched entry{.arrival = state->arrival,
                      .side = side,
                      .price = state->price,
                      .ahead_of_it = {},
                      .last = state->ahead};
        std::uint64_t shares = 0;
        const auto* level = engine.exchange_book().find_level(kLocate, side, state->price);
        for (std::uint32_t at = level == nullptr ? itch::kNoIndex : level->front;
             at != itch::kNoIndex; at = engine.exchange_book().order_at(at).newer) {
          entry.ahead_of_it.push_back(engine.exchange_book().order_at(at).reference);
          shares += engine.exchange_book().order_at(at).shares;
        }
        EXPECT_EQ(shares, state->ahead) << "ahead on arrival is not what rested at its price";
        watched[id.slot] = entry;
      }
      engine.feed(message.payload, quoter);
      for (const OrderId id : quoter.resting_candidates()) {
        const auto state = engine.queue(id);
        if (!state || !watched.contains(id.slot) || watched.at(id.slot).arrival != state->arrival) {
          continue;
        }
        Watched& entry = watched.at(id.slot);
        std::uint64_t expected = 0;
        for (const std::uint64_t reference : entry.ahead_of_it) {
          const auto real = engine.exchange_book().resting_order(reference);
          if (real && real->price == entry.price) {
            expected += real->shares;
          }
        }
        EXPECT_EQ(state->ahead, expected) << "seed " << seed << " at " << message.time;
        EXPECT_EQ(engine.recount_ahead(id), expected);
        EXPECT_LE(state->ahead, entry.last) << "ahead rose";
        fell += state->ahead < entry.last ? 1U : 0U;
        entry.last = state->ahead;
        ++checked;
      }
    }
    engine.finish(quoter);
    EXPECT_GT(quoter.fills(), 20U) << "seed " << seed;
  }
  std::println("checked {} queue states, ahead fell {} times", checked, fell);
  EXPECT_GT(checked, 10'000U);
  EXPECT_GT(fell, 500U);
}

// ---- 45: a random strategy makes no money; the oracle makes a great deal --------------

// Buys or sells 100 at random times with an IOC at the far side of the view book, and
// with `oracle_mid` set, picks the side from the feed's last mid instead of a coin.
class Taker {
 public:
  Taker(std::uint32_t seed, std::optional<Price4> oracle_mid)
      : random_{seed}, oracle_mid_{oracle_mid} {}

  template <class Context>
  void on_market(Context& context, const MarketEvent& /*event*/) {
    if (!started_) {
      started_ = true;
      context.wake_at(context.now() + 1 + (random_() % 50'000));
    }
  }
  template <class Context>
  void on_report(Context& /*context*/, const Report& report) {
    if (report.kind != ReportKind::fill) {
      return;
    }
    const std::int64_t notional = std::int64_t{report.price} * report.shares;
    cash += report.side == Side::bid ? -notional : notional;
    position +=
        report.side == Side::bid ? std::int64_t{report.shares} : -std::int64_t{report.shares};
    fees += report.fees.total();
  }
  template <class Context>
  void on_wake(Context& context) {
    const itch::TopOfBook bid = context.book().best(kLocate, Side::bid);
    const itch::TopOfBook ask = context.book().best(kLocate, Side::ask);
    if (bid.present && ask.present) {
      bool buy = random_() % 2 == 0;
      if (oracle_mid_) {
        buy = *oracle_mid_ * 2 > bid.price + ask.price;
      }
      static_cast<void>(context.send({.locate = kLocate,
                                      .side = buy ? Side::bid : Side::ask,
                                      .price = buy ? ask.price : bid.price,
                                      .shares = 100,
                                      .tif = Tif::ioc}));
    }
    context.wake_at(context.now() + 1 + (random_() % 50'000));
  }

  std::int64_t cash = 0;  // Price4 × shares.
  std::int64_t position = 0;
  itch::Picodollars fees = 0;

 private:
  std::mt19937 random_;
  std::optional<Price4> oracle_mid_;
  bool started_ = false;
};

// Net, in picodollars, marked at the last mid.
itch::Picodollars net(const Taker& taker, Price4 mid) {
  constexpr std::int64_t kPicoPerPrice4 = 100'000'000;
  return ((taker.cash + (taker.position * std::int64_t{mid})) * kPicoPerPrice4) - taker.fees;
}

Price4 last_mid(const std::vector<Message>& messages) {
  itch::FastBook<> book{kCapacity};
  for (const Message& message : messages) {
    static_cast<void>(book.apply(message.payload));
  }
  return (book.best(kLocate, Side::bid).price + book.best(kLocate, Side::ask).price) / 2;
}

TEST(Controls, S45RandomStrategyMakesNoMoneyAndTheOracleDoes) {
  constexpr int kSeeds = 32;
  itch::Picodollars random_total = 0;
  itch::Picodollars oracle_total = 0;
  int oracle_wins = 0;
  for (int seed = 0; seed < kSeeds; ++seed) {
    const std::vector<Message> messages = market(100 + static_cast<std::uint32_t>(seed), 4000);
    const Price4 mid = last_mid(messages);
    Taker random{static_cast<std::uint32_t>(seed), std::nullopt};
    Taker oracle{static_cast<std::uint32_t>(seed), mid};
    {
      Engine<> engine{config()};
      for (const Message& message : messages) {
        engine.feed(message.payload, random);
      }
      engine.finish(random);
    }
    {
      Engine<> engine{config()};
      for (const Message& message : messages) {
        engine.feed(message.payload, oracle);
      }
      engine.finish(oracle);
    }
    random_total += net(random, mid);
    oracle_total += net(oracle, mid);
    oracle_wins += net(oracle, mid) > 0 ? 1 : 0;
  }
  std::println("mean net per seed: random {} pico$, oracle {} pico$; oracle up on {} of {}",
               random_total / kSeeds, oracle_total / kSeeds, oracle_wins, kSeeds);
  EXPECT_LT(random_total, 0);
  EXPECT_GT(oracle_total, 0);
  EXPECT_GE(oracle_wins, kSeeds * 3 / 4);
}

// ---- 46: the replayed book is the same with simulated orders and without -------------

TEST(ReplayUnchanged, S46TheRealBookIsIdenticalAfterEveryMessage) {
  for (const std::uint32_t seed : {21U, 22U}) {
    const std::vector<Message> messages = market(seed, 4000);
    Engine<> trading{config()};
    Engine<> idle{config()};
    itch::FastBook<> plain{kCapacity};
    Quoter quoter;
    Null null;
    std::uint64_t steps = 0;
    for (const Message& message : messages) {
      trading.feed(message.payload, quoter);
      idle.feed(message.payload, null);
      static_cast<void>(plain.apply(message.payload));
      const std::uint64_t expected = book_hash(plain);
      ASSERT_EQ(book_hash(trading.exchange_book()), expected) << "at " << message.time;
      ASSERT_EQ(book_hash(idle.exchange_book()), expected) << "at " << message.time;
      ++steps;
    }
    trading.finish(quoter);
    idle.finish(null);
    // The whole table, every byte of every pool, not only the walk of locate 1.
    EXPECT_TRUE(trading.exchange_book() == plain);
    EXPECT_TRUE(idle.exchange_book() == plain);
    EXPECT_EQ(steps, messages.size());
    EXPECT_GT(trading.stats().fills, 50U) << "the simulated orders did not trade";
    EXPECT_GT(trading.stats().taker_fills, 5U);
    EXPECT_GT(trading.stats().maker_fills, 5U);
  }
}

// ---- 47: the same input gives the same event log --------------------------------------

std::uint64_t event_hash(const std::vector<Message>& messages) {
  Engine<> engine{config()};
  Quoter quoter;
  for (const Message& message : messages) {
    engine.feed(message.payload, quoter);
  }
  engine.finish(quoter);
  EXPECT_GT(engine.stats().fills, 0U);
  return engine.event_hash();
}

TEST(Determinism, S47IdenticalEventLogHashOnRepeat) {
  const std::vector<Message> messages = market(31);
  const std::uint64_t first = event_hash(messages);
  EXPECT_EQ(event_hash(messages), first);
  EXPECT_EQ(event_hash(market(31)), first);
  EXPECT_NE(event_hash(market(32)), first);
  // A change to the second half of the feed changes the log.
  EXPECT_NE(event_hash(perturbed(messages, messages[messages.size() / 2].time)), first);
}

// ---- 48: with no orders nothing happens, and the book is the reference's -------------

TEST(ZeroSize, S48NoOrdersNoPnlAndTheReferenceBook) {
  const std::vector<Message> messages = market(41, 4000);
  Engine<> engine{config()};
  Null null;
  itch::oracle::ReferenceBook reference;
  for (const Message& message : messages) {
    engine.feed(message.payload, null);
    static_cast<void>(reference.apply(message.payload));
  }
  engine.finish(null);
  EXPECT_EQ(engine.stats().decisions, 0U);
  EXPECT_EQ(engine.stats().fills, 0U);
  EXPECT_EQ(engine.stats().refused_messages, 0U);
  EXPECT_EQ(engine.stats().messages, messages.size());
  itch::oracle::WordList fast;
  itch::oracle::WordList slow;
  itch::oracle::emit_fast(engine.exchange_book(), fast);
  itch::oracle::emit_reference(reference, slow);
  EXPECT_GT(fast.words().size(), 100U);
  EXPECT_EQ(fast.words(), slow.words());
}

}  // namespace
