// The event-driven backtester: the real feed replayed through the book, with simulated
// orders that queue among the real ones by their order references.
//
//     Engine<Book> engine{config};
//     engine.run(stream, strategy);   // or feed() each message, then finish()
//
// **The limit.** A replayed feed cannot react to the strategy, so market impact is
// absent. Every real order arrives, trades and leaves as it did on the day, whatever the
// simulated orders do: a simulated fill takes nothing off the real book, a simulated quote
// draws no one in, and a race that a real participant lost is not in the feed at all.
// What this measures is how simulated orders would have queued and filled against the
// day as it happened, for orders small enough not to have changed it.
//
// **Two clocks, one time axis.** The ITCH timestamp is the time the matching engine
// acted. The engine merges five kinds of event by time, and on a tie in this order:
//
//     Real     a feed message, applied to the exchange-side book at its timestamp t
//     Arrival  a simulated order, cancel or replace reaching the exchange at
//              t_send + L_oe, where t_send = (the strategy's time) + L_compute
//     Delivery a feed message reaching the strategy at t + L_md
//     Report   an ack, fill or cancel reaching the strategy at (exchange time) + L_resp
//     Wake     a timer the strategy set
//
// Every Real at time t goes before anything else at t, so a real order added in the same
// nanosecond as a simulated one arrives is ahead of it. Inside each kind, events keep the
// order they were made in, which with constant latencies is time order, so each kind is a
// FIFO ring sized in the constructor and nothing is sorted.
//
// **What the strategy can see.** A strategy gets a Context: the time, a book built only
// from Deliveries (the view book), and calls that send, cancel or replace orders and set a
// wake. It holds no handle to the exchange-side book, the feed, or this engine, so it can
// only know what reached it, when it reached it. The exchange-side book is public on the
// Engine for tests and tools.
//
// **The queue.** Simulated orders never enter the real book, so the replayed book is the
// same with and without them. Each real order on a symbol the strategy trades is given an
// arrival number when it is added, and a simulated order is given one when it arrives, from
// the same counter, so "R is ahead of S" is "R's number is lower". `ahead` is set once, on
// arrival, to the shares resting at S's price then, which are all ahead of S. After that it
// only falls: by the shares of an execute, cancel, delete or replace of an order ahead of
// S at S's price. A cancel of an order behind S never moves it.
//
// **Fills.** An execution of real order R at price p, q shares, on S's side reaches S when
// S's price is better than p, or S is at p and R is behind S: in the counterfactual the
// aggressor would have met S first. S then fills at its own price, up to q shares less what
// this execution already gave to other simulated orders, best price and earliest arrival
// first. A reached S with shares still ahead is a queue anomaly, counted and not filled.
// A real order that would have crossed a resting S, an add or replace on the other side at
// a price S reaches, fills S as maker. A simulated order that crosses on arrival walks the
// real opposite side and fills as taker at each level's price. Non-printable executions
// (crosses), Trade messages (hidden size, no reference) and Cross Trades never fill, and
// nothing fills while the symbol is not trading.
//
// **The ledger.** consumed[R] counts real shares of R that a simulated order has already
// traded against, as a crossing add or as a taker. A later taker sees only R's shares less
// consumed[R], and a later execution of R draws first on consumed[R]: those shares were
// already gone in the counterfactual, so their execution is not evidence for any other
// fill. With the per-execution budget above, no real share fills more than one simulated
// share.
//
// **Fees.** Per fill, from fees.hpp, keyed by the configured date, on the tape from the
// Stock Directory's Market Category. A symbol with no tape cannot be traded.
//
// **Session.** Open simulated orders expire at End of Market Hours if the config says so,
// and always at End of System Hours, End of Messages, or the end of input. No order lives
// overnight.
//
// After the constructor, nothing here allocates: every pool and ring is sized from the
// Config. A ring that fills counts an overflow and the run is no longer valid.
#pragma once

#include <algorithm>
#include <array>
#include <bit>
#include <concepts>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <span>
#include <vector>

#include "itch/book.hpp"
#include "itch/book_types.hpp"
#include "itch/fees.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"

namespace itch::backtest {

using Nanos = std::uint64_t;

// Constant latencies, in nanoseconds. The defaults are the base run of BENCHMARKS.md,
// "Latency assumptions": 25 µs each way, from Nasdaq's "sub-50 microseconds" round trip
// split evenly, and 5 µs of compute. They are assumptions, not measured Nasdaq values.
struct Latency {
  Nanos market_data = 25'000;  // L_md: the exchange's message to the strategy.
  Nanos order_entry = 25'000;  // L_oe: the strategy's send to the matching engine.
  Nanos response = 25'000;     // L_resp: the matching engine's report to the strategy.
  Nanos compute = 5'000;       // L_compute: the strategy's decision to its send.
};

enum class Tif : std::uint8_t { day, ioc };

struct OrderId {
  std::uint32_t slot = kNoIndex;
  std::uint32_t generation = 0;

  [[nodiscard]] constexpr bool valid() const noexcept { return slot != kNoIndex; }
  friend bool operator==(const OrderId&, const OrderId&) = default;
};

struct OrderRequest {
  std::uint16_t locate;
  Side side;  // bid buys, ask sells.
  Price4 price;
  std::uint32_t shares;
  Tif tif = Tif::day;
};

enum class ReportKind : std::uint8_t {
  accepted,  // On the book or trading; price and remaining are the order's.
  rejected,
  fill,
  cancelled,  // By the strategy, or the unfilled part of an IOC.
  cancel_rejected,
  replaced,  // At its new price and size, at the back of the queue.
  replace_rejected,
  expired,  // At the end of the session.
};

enum class Reject : std::uint8_t {
  none,
  not_subscribed,  // A locate the config does not trade.
  unknown_tape,    // No tape, so no fees: see fees.hpp.
  halted,          // The symbol is not in state 'T'.
  closed,          // The session has ended.
  zero_shares,
  not_live,  // A cancel or replace that found no resting order: filled, gone, or never.
};

struct Report {
  ReportKind kind;
  Reject reason;
  Liquidity liquidity;
  Side side;
  std::uint16_t locate;
  OrderId order;
  Nanos exchange_time;
  Nanos time;  // When the strategy sees it: exchange_time + L_resp.
  Price4 price;
  std::uint32_t shares;  // Filled, or cancelled or expired.
  std::uint32_t remaining;
  FeeBreakdown fees;
};

struct MarketEvent {
  Nanos time;              // When the strategy sees it: exchange_time + L_md.
  Nanos exchange_time;     // The message's own timestamp.
  std::uint64_t sequence;  // The message's place in the feed, from 0.
  std::span<const std::byte> payload;
};

struct Config {
  std::uint32_t date = 0;  // YYYYMMDD. Picks the fee schedule; a day without one is refused.
  Latency latency{};
  std::vector<Stock> symbols;  // The symbols the strategy sees and trades.
  bool cancel_at_market_close = true;
  BookCapacity exchange_capacity{};
  BookCapacity view_capacity{};
  std::uint32_t max_orders = 1U << 16U;      // Simulated orders live or in flight at once.
  std::uint32_t queue_capacity = 1U << 16U;  // Events in flight in each ring.
};

struct Stats {
  std::uint64_t messages = 0;
  std::uint64_t refused_messages = 0;  // The exchange-side book refused a real message.
  std::uint64_t view_refused = 0;      // The view book refused a delivered one.
  std::uint64_t out_of_order = 0;      // A timestamp before the last, held at the last.
  std::uint64_t deliveries = 0;
  std::uint64_t decisions = 0;
  std::uint64_t arrivals = 0;
  std::uint64_t rejected = 0;
  std::uint64_t fills = 0;
  std::uint64_t filled_shares = 0;
  std::uint64_t maker_fills = 0;
  std::uint64_t taker_fills = 0;
  std::uint64_t crossing_fills = 0;  // Maker fills against a real add that crossed S.
  std::uint64_t queue_anomalies = 0;
  std::uint64_t hidden_prints_at_sim_price = 0;  // A bound on missed fills, not a fill.
  std::uint64_t broken_trades = 0;
  std::uint64_t expired = 0;
  std::uint64_t no_order_slot = 0;
  std::uint64_t after_end = 0;  // Sends refused once the input and its round trip ended.
  std::uint64_t overflows = 0;
  std::uint64_t fee_errors = 0;
};

// A resting simulated order as the exchange side holds it, for tests and tools.
struct QueueState {
  Price4 price;
  std::uint64_t ahead;
  std::uint32_t remaining;
  std::uint64_t arrival;
};

// An order-sensitive mix of 64-bit words: the event log's hash.
class EventHash {
 public:
  void operator()(std::uint64_t word) noexcept {
    word *= 0xFF51AFD7ED558CCDULL;
    word = std::rotl(word, 31);
    word *= 0xC4CEB9FE1A85EC53ULL;
    state_ = (std::rotl(state_ ^ word, 27) * 5ULL) + 0x52DCE729ULL;
    ++words_;
  }
  [[nodiscard]] std::uint64_t digest() const noexcept { return state_ ^ words_; }

 private:
  std::uint64_t state_ = 0x9E3779B97F4A7C15ULL;
  std::uint64_t words_ = 0;
};

// A FIFO of fixed capacity, a power of two.
template <class T>
class Ring {
 public:
  explicit Ring(std::uint32_t capacity) : slots_(std::bit_ceil(std::max(capacity, 2U))) {}

  [[nodiscard]] bool empty() const noexcept { return head_ == tail_; }
  [[nodiscard]] const T& front() const noexcept { return slots_[head_ & mask()]; }
  void pop() noexcept { ++head_; }
  [[nodiscard]] bool push(const T& value) noexcept {
    if (tail_ - head_ == slots_.size()) {
      return false;
    }
    slots_[tail_ & mask()] = value;
    ++tail_;
    return true;
  }

 private:
  [[nodiscard]] std::size_t mask() const noexcept { return slots_.size() - 1; }

  std::vector<T> slots_;
  std::size_t head_ = 0;
  std::size_t tail_ = 0;
};

template <class Book>
class Engine;

// What a strategy may do, and all it may see beyond its own events.
template <class Book = FastBook<>>
class Context {
 public:
  [[nodiscard]] Nanos now() const noexcept { return now_; }
  // Built only from what has been delivered, so it is the exchange's book L_md ago.
  [[nodiscard]] const Book& book() const noexcept { return engine_->view_; }

  // Sends at now() + L_compute. An invalid id means no order slot was free, and nothing
  // was sent.
  OrderId send(const OrderRequest& request) noexcept { return engine_->send(now_, request); }
  // The answer comes back as a Report: cancelled, or cancel_rejected.
  void cancel(OrderId order) noexcept { engine_->cancel(now_, order); }
  // A new price and a new remaining size. The order goes to the back of the queue when it
  // arrives, as an OUCH replace does, and answers replaced or replace_rejected.
  void replace(OrderId order, Price4 price, std::uint32_t shares) noexcept {
    engine_->replace(now_, order, price, shares);
  }
  // One wake at a time; a later call replaces it. A time not after now() wakes 1 ns later.
  void wake_at(Nanos time) noexcept { engine_->wake_at(now_, time); }

 private:
  friend class Engine<Book>;
  Context(Engine<Book>& engine, Nanos now) noexcept : engine_{&engine}, now_{now} {}

  Engine<Book>* engine_;
  Nanos now_;
};

template <class S, class Book = FastBook<>>
concept Strategy =
    requires(S& strategy, Context<Book>& context, const MarketEvent& event, const Report& report) {
      { strategy.on_market(context, event) } -> std::same_as<void>;
      { strategy.on_report(context, report) } -> std::same_as<void>;
      { strategy.on_wake(context) } -> std::same_as<void>;
    };

template <class Book = FastBook<>>
class Engine {
 public:
  using Level = Book::Level;

  explicit Engine(const Config& config)
      : latency_{config.latency},
        symbols_{config.symbols},
        cancel_at_close_{config.cancel_at_market_close},
        schedule_{find_fee_schedule(config.date)},
        book_{config.exchange_capacity},
        view_{config.view_capacity},
        arrival_(book_.order_capacity(), 0),
        consumed_(book_.order_capacity(), 0),
        subscribed_(kLocates, 0),
        category_(kLocates, ' '),
        trading_(kLocates, 'T'),
        resting_(kLocates, 0),
        sim_heads_(kSideCount, kNoIndex),
        orders_(config.max_orders),
        levels_(config.max_orders),
        arrivals_{config.queue_capacity},
        deliveries_{config.queue_capacity},
        reports_{config.queue_capacity} {
    for (std::uint32_t slot = 0; slot < orders_.size(); ++slot) {
      orders_[slot].newer = slot + 1 < orders_.size() ? slot + 1 : kNoIndex;
    }
    free_order_ = orders_.empty() ? kNoIndex : 0;
    for (std::uint32_t level = 0; level < levels_.size(); ++level) {
      levels_[level].worse = level + 1 < levels_.size() ? level + 1 : kNoIndex;
    }
    free_level_ = levels_.empty() ? kNoIndex : 0;
  }

  // False when the date has no fee schedule. Such an engine still replays, and refuses
  // every order as closed.
  [[nodiscard]] bool ready() const noexcept { return schedule_ != nullptr; }

  // One real message, in feed order. Everything due before its timestamp runs first.
  template <Strategy<Book> S>
  void feed(std::span<const std::byte> payload, S& strategy) noexcept {
    if (payload.empty() || payload.size() != message_length(read_char(payload.data()))) {
      ++stats_.refused_messages;
      return;
    }
    Nanos time = read_be48(payload.data() + 5);
    if (time < clock_) {
      ++stats_.out_of_order;
      time = clock_;
    }
    advance(time, strategy);
    clock_ = time;
    apply_real(payload, time);
  }

  // Runs every event due before `time`. feed() does this; a test may call it to look at
  // the state just before a message.
  template <Strategy<Book> S>
  void advance(Nanos time, S& strategy) noexcept {
    for (;;) {
      Nanos next = 0;
      const Kind kind = next_event(next);
      if (kind == Kind::none || next >= time) {
        return;
      }
      dispatch(kind, strategy);
    }
  }

  // The end of input. Every pending event runs, and the strategy may still act, until one
  // round trip after the last message: L_md + L_compute + L_oe + L_resp, by which time
  // everything that message caused has reached the strategy. A wake set past that is
  // dropped, so a strategy that always sets one cannot keep the run alive. Then the open
  // orders expire at the time of the last event, their reports are delivered, and any
  // send from then on is refused with an invalid id.
  template <Strategy<Book> S>
  void finish(S& strategy) noexcept {
    wake_limit_ =
        clock_ + latency_.market_data + latency_.compute + latency_.order_entry + latency_.response;
    advance(kNever, strategy);
    close_session(clock_);
    ended_ = true;
    advance(kNever, strategy);
  }

  // A whole BinaryFILE stream, then finish(). Returns the messages fed.
  template <Strategy<Book> S>
  std::uint64_t run(std::span<const std::byte> stream, S& strategy) noexcept {
    FrameReader reader{stream};
    for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
      if (frame.status == FrameStatus::message) {
        feed(frame.payload, strategy);
      }
    }
    finish(strategy);
    return stats_.messages;
  }

  // For tests and tools. A strategy never holds the engine.
  [[nodiscard]] const Book& exchange_book() const noexcept { return book_; }
  [[nodiscard]] const Book& view_book() const noexcept { return view_; }
  [[nodiscard]] const Stats& stats() const noexcept { return stats_; }
  [[nodiscard]] std::uint64_t event_hash() const noexcept { return hash_.digest(); }
  [[nodiscard]] Nanos clock() const noexcept { return clock_; }

  [[nodiscard]] std::optional<QueueState> queue(OrderId order) const noexcept {
    if (!order.valid() || order.slot >= orders_.size()) {
      return std::nullopt;
    }
    const SimOrder& sim = orders_[order.slot];
    if (sim.generation != order.generation || sim.state != OrderState::resting) {
      return std::nullopt;
    }
    return QueueState{
        .price = sim.price, .ahead = sim.ahead, .remaining = sim.remaining, .arrival = sim.arrival};
  }

  // The shares ahead of a resting order, recounted from the book's FIFO at its price: the
  // check on the running count.
  [[nodiscard]] std::optional<std::uint64_t> recount_ahead(OrderId order) const noexcept {
    const std::optional<QueueState> state = queue(order);
    if (!state) {
      return std::nullopt;
    }
    const SimOrder& sim = orders_[order.slot];
    const Level* level = book_.find_level(sim.locate, sim.side, sim.price);
    std::uint64_t ahead = 0;
    for (std::uint32_t real = level == nullptr ? kNoIndex : level->front; real != kNoIndex;
         real = book_.order_at(real).newer) {
      if (arrival_[real] < sim.arrival) {
        ahead += book_.order_at(real).shares;
      }
    }
    return ahead;
  }

  [[nodiscard]] std::uint64_t resting_orders() const noexcept { return resting_total_; }

 private:
  friend class Context<Book>;

  static constexpr std::size_t kLocates = 65536;
  static constexpr Nanos kNever = std::numeric_limits<Nanos>::max();
  static constexpr std::size_t kMaxMessage = 64;

  enum class OrderState : std::uint8_t { free, in_flight, resting };

  struct SimOrder {
    std::uint64_t ahead = 0;
    std::uint64_t arrival = 0;
    Price4 price = 0;
    std::uint32_t remaining = 0;
    std::uint32_t generation = 0;
    std::uint32_t level = kNoIndex;
    std::uint32_t older = kNoIndex;
    std::uint32_t newer = kNoIndex;  // The free list, while free.
    std::uint16_t locate = 0;
    Side side = Side::bid;
    Tif tif = Tif::day;
    OrderState state = OrderState::free;
  };

  // The simulated orders at one price on one side, oldest arrival first. A side's levels
  // form a list from its best price.
  struct SimLevel {
    Price4 price = 0;
    std::uint32_t better = kNoIndex;
    std::uint32_t worse = kNoIndex;  // The free list, while free.
    std::uint32_t front = kNoIndex;
    std::uint32_t back = kNoIndex;
  };

  enum class Action : std::uint8_t { order, cancel, replace };

  struct Arrival {
    Nanos time;
    OrderId order;
    Action action;
    Price4 price;
    std::uint32_t shares;
  };

  struct Delivery {
    Nanos time;
    Nanos exchange_time;
    std::uint64_t sequence;
    std::size_t length;
    std::array<std::byte, kMaxMessage> bytes;
  };

  // The tie order at one time, after every Real.
  enum class Kind : std::uint8_t { arrival, delivery, report, wake, none };

  // A real order as the book held it just before a message changed it.
  struct RealOrder {
    std::uint32_t slot = kNoIndex;
    Side side = Side::bid;
    Price4 price = 0;
    std::uint32_t shares = 0;
    std::uint64_t arrival = 0;
    std::uint32_t consumed = 0;

    [[nodiscard]] bool found() const noexcept { return slot != kNoIndex; }
  };

  // ---- The event loop -----------------------------------------------------------------

  [[nodiscard]] Kind next_event(Nanos& time) const noexcept {
    Kind kind = Kind::none;
    time = kNever;
    const auto consider = [&kind, &time](bool present, Nanos at, Kind candidate) {
      if (present && (at < time || (at == time && candidate < kind))) {
        time = at;
        kind = candidate;
      }
    };
    consider(!arrivals_.empty(), arrivals_.empty() ? 0 : arrivals_.front().time, Kind::arrival);
    consider(!deliveries_.empty(), deliveries_.empty() ? 0 : deliveries_.front().time,
             Kind::delivery);
    consider(!reports_.empty(), reports_.empty() ? 0 : reports_.front().time, Kind::report);
    consider(wake_pending_ && wake_time_ <= wake_limit_, wake_time_, Kind::wake);
    return kind;
  }

  template <class S>
  void dispatch(Kind kind, S& strategy) noexcept {
    switch (kind) {
      case Kind::arrival: {
        const Arrival arrival = arrivals_.front();
        arrivals_.pop();
        clock_ = std::max(clock_, arrival.time);
        arrive(arrival);
        break;
      }
      case Kind::delivery: {
        const Delivery delivery = deliveries_.front();
        deliveries_.pop();
        clock_ = std::max(clock_, delivery.time);
        const std::span<const std::byte> payload{delivery.bytes.data(), delivery.length};
        const ApplyStatus status = view_.apply(payload);
        stats_.view_refused +=
            status == ApplyStatus::applied || status == ApplyStatus::no_book_effect ? 0U : 1U;
        Context<Book> context{*this, delivery.time};
        strategy.on_market(context, MarketEvent{.time = delivery.time,
                                                .exchange_time = delivery.exchange_time,
                                                .sequence = delivery.sequence,
                                                .payload = payload});
        break;
      }
      case Kind::report: {
        const Report report = reports_.front();
        reports_.pop();
        clock_ = std::max(clock_, report.time);
        Context<Book> context{*this, report.time};
        strategy.on_report(context, report);
        break;
      }
      case Kind::wake: {
        wake_pending_ = false;
        clock_ = std::max(clock_, wake_time_);
        Context<Book> context{*this, wake_time_};
        strategy.on_wake(context);
        break;
      }
      case Kind::none: break;
    }
  }

  // ---- The strategy's calls -----------------------------------------------------------

  OrderId send(Nanos now, const OrderRequest& request) noexcept {
    if (ended_) {
      ++stats_.after_end;
      return {};
    }
    if (free_order_ == kNoIndex) {
      ++stats_.no_order_slot;
      return {};
    }
    const std::uint32_t slot = free_order_;
    SimOrder& sim = orders_[slot];
    const OrderId order{.slot = slot, .generation = sim.generation};
    if (!queue_arrival(now, {.time = 0,
                             .order = order,
                             .action = Action::order,
                             .price = request.price,
                             .shares = request.shares})) {
      return {};
    }
    free_order_ = sim.newer;
    sim.state = OrderState::in_flight;
    sim.locate = request.locate;
    sim.side = request.side;
    sim.price = request.price;
    sim.remaining = request.shares;
    sim.tif = request.tif;
    sim.ahead = 0;
    sim.level = kNoIndex;
    sim.older = kNoIndex;
    sim.newer = kNoIndex;
    log_decision(now, Action::order, order, request.price, request.shares);
    return order;
  }

  void cancel(Nanos now, OrderId order) noexcept {
    if (!ended_ &&
        queue_arrival(
            now, {.time = 0, .order = order, .action = Action::cancel, .price = 0, .shares = 0})) {
      log_decision(now, Action::cancel, order, 0, 0);
    }
  }

  void replace(Nanos now, OrderId order, Price4 price, std::uint32_t shares) noexcept {
    if (!ended_ && queue_arrival(now, {.time = 0,
                                       .order = order,
                                       .action = Action::replace,
                                       .price = price,
                                       .shares = shares})) {
      log_decision(now, Action::replace, order, price, shares);
    }
  }

  void wake_at(Nanos now, Nanos time) noexcept {
    wake_time_ = time > now ? time : now + 1;
    wake_pending_ = true;
    hash_(4);
    hash_(now);
    hash_(wake_time_);
  }

  [[nodiscard]] bool queue_arrival(Nanos now, Arrival arrival) noexcept {
    arrival.time = now + latency_.compute + latency_.order_entry;
    if (!arrivals_.push(arrival)) {
      ++stats_.overflows;
      return false;
    }
    return true;
  }

  void log_decision(Nanos now, Action action, OrderId order, Price4 price,
                    std::uint32_t shares) noexcept {
    ++stats_.decisions;
    hash_(1);
    hash_(now);
    hash_(static_cast<std::uint64_t>(action));
    hash_(order.slot);
    hash_(order.generation);
    hash_(price);
    hash_(shares);
  }

  // ---- Real messages, at the exchange -------------------------------------------------

  void apply_real(std::span<const std::byte> payload, Nanos time) noexcept {
    ++stats_.messages;
    const std::byte* m = payload.data();
    const char type = read_char(m);
    const std::uint16_t locate = read_be<std::uint16_t>(m + 1);
    switch (type) {
      case AddOrder::kType: {
        const AddOrder add = AddOrder::from_wire(m);
        real_add(payload, locate, add.order_reference, add.side, add.price, time);
        break;
      }
      case AddOrderAttributed::kType: {
        const AddOrderAttributed add = AddOrderAttributed::from_wire(m);
        real_add(payload, locate, add.order_reference, add.side, add.price, time);
        break;
      }
      case OrderExecuted::kType: {
        const OrderExecuted e = OrderExecuted::from_wire(m);
        const RealOrder real = before(locate, e.order_reference);
        if (apply_book(payload) && real.found()) {
          execution(locate, real, e.executed_shares, true, time);
        }
        break;
      }
      case OrderExecutedWithPrice::kType: {
        const OrderExecutedWithPrice c = OrderExecutedWithPrice::from_wire(m);
        const RealOrder real = before(locate, c.order_reference);
        if (apply_book(payload) && real.found()) {
          execution(locate, real, c.executed_shares, c.printable == 'Y', time);
        }
        break;
      }
      case OrderCancel::kType: {
        const OrderCancel x = OrderCancel::from_wire(m);
        const RealOrder real = before(locate, x.order_reference);
        if (apply_book(payload) && real.found()) {
          depletion(locate, real, x.cancelled_shares);
        }
        break;
      }
      case OrderDelete::kType: {
        const OrderDelete d = OrderDelete::from_wire(m);
        const RealOrder real = before(locate, d.order_reference);
        if (apply_book(payload) && real.found()) {
          depletion(locate, real, real.shares);
        }
        break;
      }
      case OrderReplace::kType: {
        const OrderReplace u = OrderReplace::from_wire(m);
        const RealOrder real = before(locate, u.original_order_reference);
        if (!apply_book(payload)) {
          break;
        }
        const std::uint32_t slot = number_real(locate, u.new_order_reference);
        if (real.found()) {
          depletion(locate, real, real.shares);
          cross(locate, real.side, u.price, slot, time);
        }
        break;
      }
      case StockDirectory::kType: {
        const StockDirectory directory = StockDirectory::from_wire(m);
        category_[locate] = directory.market_category;
        subscribed_[locate] =
            std::ranges::find(symbols_, directory.stock) != symbols_.end() ? 1U : 0U;
        static_cast<void>(apply_book(payload));
        break;
      }
      case StockTradingAction::kType:
        trading_[locate] = StockTradingAction::from_wire(m).trading_state;
        static_cast<void>(apply_book(payload));
        break;
      case SystemEvent::kType: {
        static_cast<void>(apply_book(payload));
        system_event(SystemEvent::from_wire(m).event_code, time);
        break;
      }
      case Trade::kType: {
        if (resting_[locate] != 0) {
          const Price4 price = Trade::from_wire(m).price;
          if (has_sim_level(locate, Side::bid, price) || has_sim_level(locate, Side::ask, price)) {
            ++stats_.hidden_prints_at_sim_price;
          }
        }
        static_cast<void>(apply_book(payload));
        break;
      }
      case BrokenTrade::kType:
        ++stats_.broken_trades;
        static_cast<void>(apply_book(payload));
        break;
      default: static_cast<void>(apply_book(payload)); break;
    }
    if (type == SystemEvent::kType || subscribed_[locate] != 0) {
      deliver(payload, time);
    }
    ++sequence_;
  }

  [[nodiscard]] bool apply_book(std::span<const std::byte> payload) noexcept {
    const ApplyStatus status = book_.apply(payload);
    if (status == ApplyStatus::applied) {
      return true;
    }
    stats_.refused_messages += status == ApplyStatus::no_book_effect ? 0U : 1U;
    return false;
  }

  void deliver(std::span<const std::byte> payload, Nanos time) noexcept {
    Delivery delivery{.time = time + latency_.market_data,
                      .exchange_time = time,
                      .sequence = sequence_,
                      .length = payload.size(),
                      .bytes = {}};
    std::ranges::copy(payload, delivery.bytes.begin());
    if (deliveries_.push(delivery)) {
      ++stats_.deliveries;
    } else {
      ++stats_.overflows;
    }
  }

  void real_add(std::span<const std::byte> payload, std::uint16_t locate, std::uint64_t reference,
                char side, Price4 price, Nanos time) noexcept {
    if (!apply_book(payload)) {
      return;
    }
    const std::uint32_t slot = number_real(locate, reference);
    if (slot != kNoIndex) {
      cross(locate, side == 'B' ? Side::bid : Side::ask, price, slot, time);
    }
  }

  // Gives a new real order on a traded locate its arrival number and an empty ledger.
  [[nodiscard]] std::uint32_t number_real(std::uint16_t locate, std::uint64_t reference) noexcept {
    if (subscribed_[locate] == 0) {
      return kNoIndex;
    }
    const std::uint32_t slot = book_.order_slot(reference);
    if (slot != kNoIndex) {
      arrival_[slot] = ++arrival_counter_;
      consumed_[slot] = 0;
    }
    return slot;
  }

  // The real order a message names, read before the book changes it. Only a traded locate
  // needs it: its queues, and the ledger, which every execution draws on.
  [[nodiscard]] RealOrder before(std::uint16_t locate, std::uint64_t reference) const noexcept {
    if (subscribed_[locate] == 0) {
      return {};
    }
    const std::uint32_t slot = book_.order_slot(reference);
    if (slot == kNoIndex) {
      return {};
    }
    const BookOrder& order = book_.order_at(slot);
    const Level& level = book_.level_at(order.level);
    if (level.locate != locate) {
      return {};
    }
    return {.slot = slot,
            .side = level.side,
            .price = level.price,
            .shares = order.shares,
            .arrival = arrival_[slot],
            .consumed = consumed_[slot]};
  }

  [[nodiscard]] bool can_fill(std::uint16_t locate) const noexcept {
    return trading_[locate] == 'T';
  }

  // An execution of R: depletion ahead of S, a fill of S where it reaches S.
  void execution(std::uint16_t locate, const RealOrder& real, std::uint32_t shares, bool printable,
                 Nanos time) noexcept {
    const std::uint32_t drawn = std::min(shares, real.consumed);
    consumed_[real.slot] = real.consumed - drawn;
    std::uint32_t budget = shares - drawn;
    const bool fills = printable && can_fill(locate);
    std::uint32_t next_level = kNoIndex;
    for (std::uint32_t level = sim_heads_[side_key(locate, real.side)]; level != kNoIndex;
         level = next_level) {
      next_level = levels_[level].worse;
      const Price4 price = levels_[level].price;
      if (better(real.side, real.price, price)) {
        break;
      }
      const bool at_price = price == real.price;
      std::uint32_t next = kNoIndex;
      for (std::uint32_t slot = levels_[level].front; slot != kNoIndex; slot = next) {
        next = orders_[slot].newer;
        SimOrder& sim = orders_[slot];
        if (at_price && sim.arrival > real.arrival) {
          sim.ahead -= std::min<std::uint64_t>(sim.ahead, shares);
          continue;
        }
        if (!fills) {
          continue;
        }
        if (sim.ahead != 0) {
          ++stats_.queue_anomalies;
          continue;
        }
        const std::uint32_t take = std::min(sim.remaining, budget);
        if (take == 0) {
          continue;
        }
        budget -= take;
        fill(slot, take, sim.price, Liquidity::maker, time);
      }
    }
  }

  // A cancel, delete or replace of R: depletion ahead of S at R's price, and nothing else.
  void depletion(std::uint16_t locate, const RealOrder& real, std::uint32_t shares) noexcept {
    const std::uint32_t level = find_sim_level(locate, real.side, real.price);
    if (level == kNoIndex) {
      return;
    }
    for (std::uint32_t slot = levels_[level].front; slot != kNoIndex; slot = orders_[slot].newer) {
      SimOrder& sim = orders_[slot];
      if (sim.arrival > real.arrival) {
        sim.ahead -= std::min<std::uint64_t>(sim.ahead, shares);
      }
    }
  }

  // A real order on `side` at `price` that a resting simulated order on the other side
  // reaches would have traded with it.
  void cross(std::uint16_t locate, Side side, Price4 price, std::uint32_t real,
             Nanos time) noexcept {
    if (real == kNoIndex || resting_[locate] == 0 || !can_fill(locate)) {
      return;
    }
    const Side sim_side = side == Side::bid ? Side::ask : Side::bid;
    std::uint32_t next_level = kNoIndex;
    for (std::uint32_t level = sim_heads_[side_key(locate, sim_side)]; level != kNoIndex;
         level = next_level) {
      next_level = levels_[level].worse;
      if (!reaches(sim_side, levels_[level].price, price)) {
        return;
      }
      std::uint32_t next = kNoIndex;
      for (std::uint32_t slot = levels_[level].front; slot != kNoIndex; slot = next) {
        next = orders_[slot].newer;
        const SimOrder& sim = orders_[slot];
        if (sim.ahead != 0) {
          ++stats_.queue_anomalies;
          continue;
        }
        const std::uint32_t available = book_.order_at(real).shares - consumed_[real];
        const std::uint32_t take = std::min(sim.remaining, available);
        if (take == 0) {
          return;
        }
        consumed_[real] += take;
        ++stats_.crossing_fills;
        fill(slot, take, sim.price, Liquidity::maker, time);
      }
    }
  }

  void system_event(char code, Nanos time) noexcept {
    switch (code) {
      case 'Q': regular_hours_ = true; break;
      case 'M':
        regular_hours_ = false;
        if (cancel_at_close_) {
          close_session(time);
        }
        break;
      case 'E':
      case 'C': close_session(time); break;
      default: break;
    }
  }

  void close_session(Nanos time) noexcept {
    closed_ = true;
    for (std::uint32_t slot = 0; slot < orders_.size(); ++slot) {
      if (orders_[slot].state == OrderState::resting) {
        ++stats_.expired;
        report(ReportKind::expired, Reject::none, slot, time, orders_[slot].price,
               orders_[slot].remaining, Liquidity::maker, {});
        unrest(slot);
        release(slot);
      }
    }
  }

  // ---- Simulated orders, at the exchange ----------------------------------------------

  void arrive(const Arrival& arrival) noexcept {
    ++stats_.arrivals;
    const Nanos time = arrival.time;
    const std::uint32_t slot = arrival.order.slot;
    if (slot >= orders_.size()) {
      report_unknown(arrival.action == Action::replace ? ReportKind::replace_rejected
                                                       : ReportKind::cancel_rejected,
                     arrival, time);
      return;
    }
    SimOrder& sim = orders_[slot];
    const bool live = sim.generation == arrival.order.generation;
    switch (arrival.action) {
      case Action::order: {
        const Reject reason = admit(sim.locate, sim.remaining);
        if (reason != Reject::none) {
          ++stats_.rejected;
          report(ReportKind::rejected, reason, slot, time, sim.price, sim.remaining,
                 Liquidity::maker, {});
          release(slot);
          return;
        }
        sim.arrival = ++arrival_counter_;
        report(ReportKind::accepted, Reject::none, slot, time, sim.price, sim.remaining,
               Liquidity::maker, {});
        enter(slot, time);
        return;
      }
      case Action::cancel: {
        if (!live || sim.state != OrderState::resting) {
          report_unknown(ReportKind::cancel_rejected, arrival, time);
          return;
        }
        report(ReportKind::cancelled, Reject::none, slot, time, sim.price, sim.remaining,
               Liquidity::maker, {});
        unrest(slot);
        release(slot);
        return;
      }
      case Action::replace: {
        if (!live || sim.state != OrderState::resting) {
          report_unknown(ReportKind::replace_rejected, arrival, time);
          return;
        }
        const Reject reason = admit(sim.locate, arrival.shares);
        if (reason != Reject::none) {
          report(ReportKind::replace_rejected, reason, slot, time, sim.price, sim.remaining,
                 Liquidity::maker, {});
          return;
        }
        unrest(slot);
        sim.price = arrival.price;
        sim.remaining = arrival.shares;
        sim.arrival = ++arrival_counter_;
        report(ReportKind::replaced, Reject::none, slot, time, sim.price, sim.remaining,
               Liquidity::maker, {});
        enter(slot, time);
        return;
      }
    }
  }

  [[nodiscard]] Reject admit(std::uint16_t locate, std::uint32_t shares) const noexcept {
    if (closed_ || schedule_ == nullptr) {
      return Reject::closed;
    }
    if (subscribed_[locate] == 0) {
      return Reject::not_subscribed;
    }
    if (tape_of(category_[locate]) == Tape::unknown) {
      return Reject::unknown_tape;
    }
    if (!can_fill(locate)) {
      return Reject::halted;
    }
    if (shares == 0) {
      return Reject::zero_shares;
    }
    return Reject::none;
  }

  // An order or replace that has just arrived: takes what it reaches on the real opposite
  // side, then rests behind every real order at its price, or cancels if IOC.
  void enter(std::uint32_t slot, Nanos time) noexcept {
    SimOrder& sim = orders_[slot];
    const Side opposite = sim.side == Side::bid ? Side::ask : Side::bid;
    book_.walk_side(sim.locate, opposite, [&](const Level& level) {
      if (!reaches(sim.side, sim.price, level.price)) {
        return false;
      }
      for (std::uint32_t real = level.front; real != kNoIndex && sim.remaining != 0;
           real = book_.order_at(real).newer) {
        const std::uint32_t shares = book_.order_at(real).shares;
        const std::uint32_t available = shares - std::min(shares, consumed_[real]);
        const std::uint32_t take = std::min(sim.remaining, available);
        if (take != 0) {
          consumed_[real] += take;
          fill(slot, take, level.price, Liquidity::taker, time);
        }
      }
      return sim.remaining != 0;
    });
    if (sim.remaining == 0) {
      release(slot);
      return;
    }
    if (sim.tif == Tif::ioc) {
      report(ReportKind::cancelled, Reject::none, slot, time, sim.price, sim.remaining,
             Liquidity::maker, {});
      release(slot);
      return;
    }
    rest(slot);
  }

  void fill(std::uint32_t slot, std::uint32_t shares, Price4 price, Liquidity liquidity,
            Nanos time) noexcept {
    SimOrder& sim = orders_[slot];
    const std::expected<FeeBreakdown, FeeError> fees =
        fill_fees(*schedule_, {.tape = tape_of(category_[sim.locate]),
                               .liquidity = liquidity,
                               .side = sim.side,
                               .price = price,
                               .shares = shares,
                               .regular_hours = regular_hours_});
    stats_.fee_errors += fees ? 0U : 1U;
    sim.remaining -= shares;
    ++stats_.fills;
    stats_.filled_shares += shares;
    ++(liquidity == Liquidity::maker ? stats_.maker_fills : stats_.taker_fills);
    report(ReportKind::fill, Reject::none, slot, time, price, shares, liquidity,
           fees ? *fees : FeeBreakdown{});
    if (sim.remaining == 0 && sim.state == OrderState::resting) {
      unrest(slot);
      release(slot);
    }
  }

  void report(ReportKind kind, Reject reason, std::uint32_t slot, Nanos time, Price4 price,
              std::uint32_t shares, Liquidity liquidity, const FeeBreakdown& fees) noexcept {
    const SimOrder& sim = orders_[slot];
    push_report({.kind = kind,
                 .reason = reason,
                 .liquidity = liquidity,
                 .side = sim.side,
                 .locate = sim.locate,
                 .order = {.slot = slot, .generation = sim.generation},
                 .exchange_time = time,
                 .time = time + latency_.response,
                 .price = price,
                 .shares = shares,
                 .remaining = sim.remaining,
                 .fees = fees});
  }

  // A cancel or replace for an id that is not resting: its slot may be free or reused.
  void report_unknown(ReportKind kind, const Arrival& arrival, Nanos time) noexcept {
    push_report({.kind = kind,
                 .reason = Reject::not_live,
                 .liquidity = Liquidity::maker,
                 .side = Side::bid,
                 .locate = 0,
                 .order = arrival.order,
                 .exchange_time = time,
                 .time = time + latency_.response,
                 .price = arrival.price,
                 .shares = arrival.shares,
                 .remaining = 0,
                 .fees = {}});
  }

  void push_report(const Report& report) noexcept {
    hash_(2);
    hash_(static_cast<std::uint64_t>(report.kind));
    hash_(static_cast<std::uint64_t>(report.reason));
    hash_(report.order.slot);
    hash_(report.order.generation);
    hash_(report.exchange_time);
    hash_(report.price);
    hash_(report.shares);
    hash_(report.remaining);
    hash_(static_cast<std::uint64_t>(report.fees.total()));
    if (!reports_.push(report)) {
      ++stats_.overflows;
    }
  }

  // ---- Simulated queues ---------------------------------------------------------------

  // True if `a` is a better price than `b` on this side.
  [[nodiscard]] static bool better(Side side, Price4 a, Price4 b) noexcept {
    return side == Side::bid ? a > b : a < b;
  }
  // True if an order on `side` at `limit` trades with one on the other side at `price`.
  [[nodiscard]] static bool reaches(Side side, Price4 limit, Price4 price) noexcept {
    return side == Side::bid ? limit >= price : limit <= price;
  }

  [[nodiscard]] std::uint32_t find_sim_level(std::uint16_t locate, Side side,
                                             Price4 price) const noexcept {
    for (std::uint32_t level = sim_heads_[side_key(locate, side)]; level != kNoIndex;
         level = levels_[level].worse) {
      if (levels_[level].price == price) {
        return level;
      }
      if (better(side, price, levels_[level].price)) {
        return kNoIndex;
      }
    }
    return kNoIndex;
  }

  [[nodiscard]] bool has_sim_level(std::uint16_t locate, Side side, Price4 price) const noexcept {
    return find_sim_level(locate, side, price) != kNoIndex;
  }

  // Joins the back of its price's simulated FIFO, behind every real order there now.
  void rest(std::uint32_t slot) noexcept {
    SimOrder& sim = orders_[slot];
    const Level* real = book_.find_level(sim.locate, sim.side, sim.price);
    sim.ahead = real == nullptr ? 0 : real->aggregate_shares;
    const std::size_t key = side_key(sim.locate, sim.side);
    std::uint32_t worse = sim_heads_[key];
    std::uint32_t better_level = kNoIndex;
    while (worse != kNoIndex && better(sim.side, levels_[worse].price, sim.price)) {
      better_level = worse;
      worse = levels_[worse].worse;
    }
    std::uint32_t level = worse;
    if (level == kNoIndex || levels_[level].price != sim.price) {
      level = free_level_;
      free_level_ = levels_[level].worse;
      levels_[level] = {.price = sim.price,
                        .better = better_level,
                        .worse = worse,
                        .front = kNoIndex,
                        .back = kNoIndex};
      (better_level == kNoIndex ? sim_heads_[key] : levels_[better_level].worse) = level;
      if (worse != kNoIndex) {
        levels_[worse].better = level;
      }
    }
    SimLevel& at = levels_[level];
    sim.level = level;
    sim.older = at.back;
    sim.newer = kNoIndex;
    (at.back == kNoIndex ? at.front : orders_[at.back].newer) = slot;
    at.back = slot;
    sim.state = OrderState::resting;
    ++resting_[sim.locate];
    ++resting_total_;
  }

  void unrest(std::uint32_t slot) noexcept {
    SimOrder& sim = orders_[slot];
    SimLevel& at = levels_[sim.level];
    (sim.older == kNoIndex ? at.front : orders_[sim.older].newer) = sim.newer;
    (sim.newer == kNoIndex ? at.back : orders_[sim.newer].older) = sim.older;
    if (at.front == kNoIndex) {
      const std::size_t key = side_key(sim.locate, sim.side);
      (at.better == kNoIndex ? sim_heads_[key] : levels_[at.better].worse) = at.worse;
      if (at.worse != kNoIndex) {
        levels_[at.worse].better = at.better;
      }
      at.worse = free_level_;
      free_level_ = sim.level;
    }
    sim.level = kNoIndex;
    sim.state = OrderState::in_flight;
    --resting_[sim.locate];
    --resting_total_;
  }

  void release(std::uint32_t slot) noexcept {
    SimOrder& sim = orders_[slot];
    sim.state = OrderState::free;
    ++sim.generation;
    sim.newer = free_order_;
    free_order_ = slot;
  }

  Latency latency_;
  std::vector<Stock> symbols_;
  bool cancel_at_close_;
  const FeeSchedule* schedule_;

  Book book_;
  Book view_;
  std::vector<std::uint64_t> arrival_;   // Per real order slot.
  std::vector<std::uint32_t> consumed_;  // Per real order slot.
  std::vector<std::uint8_t> subscribed_;
  std::vector<char> category_;
  std::vector<char> trading_;
  std::vector<std::uint32_t> resting_;  // Resting simulated orders per locate.
  std::vector<std::uint32_t> sim_heads_;

  std::vector<SimOrder> orders_;
  std::vector<SimLevel> levels_;
  std::uint32_t free_order_ = kNoIndex;
  std::uint32_t free_level_ = kNoIndex;
  std::uint64_t resting_total_ = 0;

  Ring<Arrival> arrivals_;
  Ring<Delivery> deliveries_;
  Ring<Report> reports_;
  bool wake_pending_ = false;
  Nanos wake_time_ = 0;
  Nanos wake_limit_ = kNever;
  bool ended_ = false;

  Nanos clock_ = 0;
  std::uint64_t sequence_ = 0;
  std::uint64_t arrival_counter_ = 0;
  bool regular_hours_ = false;
  bool closed_ = false;
  Stats stats_;
  EventHash hash_;
};

}  // namespace itch::backtest
