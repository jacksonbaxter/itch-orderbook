// Random order flow through both books, compared in full after every message, on every
// arm of the fast book in turn.
//
// Random bytes would almost never name a live order, and a harness that only saw
// refusals would pass trivially. So each 5-byte record of the input is an operation
// against a model of the live orders, which turns it into a well-formed message that
// names a real order. Some operations are invalid on purpose: a dead reference, too many
// shares, another locate, a live reference used again, a bad side. An invalid message is
// not a failure of the harness. It has to be refused the same way by both books, change
// nothing, and raise T-INV-1 exactly once. A valid one must never raise it. Crossed and
// locked books are valid here, as the flow is random, and each is counted against an
// independent count from the model. So every part of the outcome has a right answer:
//
//   - the full diff after each message, with the exhaustive compare on the model's
//     locates, and the whole-book hash at the end of the input
//   - T-INV-1 fires exactly on the invalid operations
//   - T-INV-2 counts exactly what the model counts
//   - the reference's best prices equal the model's
//
// The paged index has 2 pages and a reach of 2^16, so the high references fall back to
// its hash, and so does a new reference whose page cannot be had while two are pinned. The flat
// arena holds every block the model's 8 sides can grow to, and a side past 8 levels moves to a
// bigger one.
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <print>
#include <span>
#include <string_view>
#include <utility>
#include <vector>

#include "fuzz_check.hpp"
#include "itch/book.hpp"
#include "oracle/diff.hpp"
#include "oracle/wire_builder.hpp"
#include "support/fixtures.hpp"

namespace {

namespace wire = itch::oracle::wire;

constexpr std::size_t kRecord = 5;
constexpr std::array<std::uint16_t, 4> kLocates{0, 1, 2, 65535};
constexpr std::uint32_t kCapacity = 8192;
// Never issued as a live order, by construction of the model's references.
constexpr std::uint64_t kDeadBit = 1ULL << 63U;

struct Op {
  std::uint8_t code;
  std::uint8_t pick;
  std::uint8_t shares;
  std::uint8_t price;
  std::uint8_t locate;
};

struct LiveOrder {
  std::uint64_t reference;
  std::uint16_t locate;
  char side;
  std::uint32_t shares;
  std::uint32_t price;
};

// A small ladder, so that orders share levels and FIFOs form, with the extremes on it.
[[nodiscard]] std::uint32_t ladder_price(std::uint8_t byte) {
  if (byte == 255) {
    return 0xFFFFFFFFU;
  }
  if (byte == 254) {
    return 0;
  }
  return 10000U + ((byte % 16U) * 100U);
}

template <class Run>
[[nodiscard]] Run fresh_run() {
  Run run{{.capacity = {.orders = kCapacity,
                        .levels = kCapacity,
                        .index_pages = 2,
                        .index_bits = 16,
                        .level_slots = 1024},
           .hash_every = 0,
           .exhaustive = true,
           .exhaustive_locates = kLocates}};
  run.invariants().set_inv1_detail_limit(0);
  run.invariants().set_inv3_detail_limit(0);
  return run;
}

template <class Run>
class Harness {
 public:
  explicit Harness(Run& oracle) noexcept : oracle_{&oracle} {}

  void run(const Op& op) {
    switch (op.code % 16U) {
      case 0: add(op, false); break;
      case 1: add(op, true); break;
      case 2:
      case 3: deduct(op, 'E'); break;
      case 4: deduct(op, 'C'); break;
      case 5: deduct(op, 'X'); break;
      case 6: erase(op); break;
      case 7: replace(op, ladder_price(op.price)); break;
      case 8: replace(op, live_.empty() ? 0 : live_[pick(op)].price); break;
      case 9: other_message(op); break;
      case 10: dead_reference(op); break;
      case 11: over_deduction(op); break;
      case 12: wrong_locate(op); break;
      case 13: duplicate_add(op); break;
      case 14: replace_onto_live(op); break;
      default: bad_side(op); break;
    }
  }

  void finish() {
    oracle_->finish();
    report();
    FUZZ_CHECK(oracle_->divergences().empty());
    FUZZ_CHECK(oracle_->fast().live_orders() == live_.size());
    FUZZ_CHECK(oracle_->reference().live_orders() == live_.size());
  }

 private:
  [[nodiscard]] std::size_t pick(const Op& op) const { return op.pick % live_.size(); }

  [[nodiscard]] std::uint64_t new_reference(const Op& op) {
    ++issued_;
    // Mostly small, and sometimes high, so that the hash index sees both.
    return op.pick % 8U == 0 ? (issued_ * 0x9E3779B97F4A7C15ULL) & ~kDeadBit : issued_;
  }

  void report() const {
    for (const itch::oracle::Divergence& d : oracle_->divergences()) {
      std::println(stderr, "DIVERGENCE kind={} message={} type={} {}", to_string(d.kind), d.message,
                   d.type, d.detail);
    }
  }

  // Sends one message and checks everything that has a right answer for it.
  void send(const wire::Bytes& message, std::uint16_t locate, bool valid) {
    expected_violations_ += valid ? 0U : 1U;
    const bool same = oracle_->on_message(message);
    report();
    FUZZ_CHECK(same);
    FUZZ_CHECK(oracle_->invariants().inv1_violations() == expected_violations_);

    const auto [bid, ask] = model_best(locate);
    const itch::oracle::RefTop reference_bid = oracle_->reference().best(locate, 'B');
    const itch::oracle::RefTop reference_ask = oracle_->reference().best(locate, 'S');
    FUZZ_CHECK(reference_bid.present == (bid >= 0));
    FUZZ_CHECK(reference_ask.present == (ask >= 0));
    FUZZ_CHECK(bid < 0 || std::cmp_equal(reference_bid.price, bid));
    FUZZ_CHECK(ask < 0 || std::cmp_equal(reference_ask.price, ask));
    if (bid >= 0 && ask >= 0) {
      expected_crossed_ += bid > ask ? 1U : 0U;
      expected_locked_ += bid == ask ? 1U : 0U;
    }
    FUZZ_CHECK(oracle_->invariants().crossed() == expected_crossed_);
    FUZZ_CHECK(oracle_->invariants().locked() == expected_locked_);
  }

  [[nodiscard]] std::pair<std::int64_t, std::int64_t> model_best(std::uint16_t locate) const {
    std::int64_t bid = -1;
    std::int64_t ask = -1;
    for (const LiveOrder& order : live_) {
      if (order.locate != locate) {
        continue;
      }
      const auto price = static_cast<std::int64_t>(order.price);
      if (order.side == 'B') {
        bid = std::max(bid, price);
      } else {
        ask = ask < 0 ? price : std::min(ask, price);
      }
    }
    return {bid, ask};
  }

  void add(const Op& op, bool attributed) {
    if (live_.size() + 2 >= kCapacity) {
      return;
    }
    const LiveOrder order{.reference = new_reference(op),
                          .locate = kLocates[op.locate % kLocates.size()],
                          .side = (op.pick & 1U) != 0 ? 'B' : 'S',
                          .shares = 1U + op.shares,
                          .price = ladder_price(op.price)};
    live_.push_back(order);
    send((attributed ? wire::add_attributed : wire::add)(order.locate, order.reference, order.side,
                                                         order.shares, order.price, 1),
         order.locate, true);
  }

  void deduct(const Op& op, char type) {
    if (live_.empty()) {
      add(op, false);
      return;
    }
    const std::size_t at = pick(op);
    const LiveOrder order = live_[at];
    // High bit of the byte takes all of it, the rest a part of it.
    const std::uint32_t shares =
        (op.shares & 0x80U) != 0 ? order.shares : 1U + (op.shares % order.shares);
    const std::uint32_t taken = std::min(shares, order.shares);
    live_[at].shares -= taken;
    if (live_[at].shares == 0) {
      live_[at] = live_.back();
      live_.pop_back();
    }
    wire::Bytes message;
    if (type == 'E') {
      message = wire::execute(order.locate, order.reference, taken);
    } else if (type == 'C') {
      message = wire::execute_with_price(order.locate, order.reference, taken, 1);
    } else {
      message = wire::cancel(order.locate, order.reference, taken);
    }
    send(message, order.locate, true);
  }

  void erase(const Op& op) {
    if (live_.empty()) {
      add(op, false);
      return;
    }
    const std::size_t at = pick(op);
    const LiveOrder order = live_[at];
    live_[at] = live_.back();
    live_.pop_back();
    send(wire::erase(order.locate, order.reference), order.locate, true);
  }

  void replace(const Op& op, std::uint32_t price) {
    if (live_.empty()) {
      add(op, false);
      return;
    }
    const std::size_t at = pick(op);
    const LiveOrder old = live_[at];
    live_[at].reference = new_reference(op);
    live_[at].shares = 1U + op.shares;
    live_[at].price = price;
    send(wire::replace(old.locate, old.reference, live_[at].reference, live_[at].shares, price),
         old.locate, true);
  }

  // Messages that must leave both books alone.
  void other_message(const Op& op) {
    const std::uint16_t locate = kLocates[op.locate % kLocates.size()];
    switch (op.pick % 6U) {
      case 0: send(wire::trade(locate, op.shares, ladder_price(op.price)), locate, true); break;
      case 1: send(wire::cross(locate, op.shares, ladder_price(op.price)), locate, true); break;
      case 2: send(wire::broken(locate), locate, true); break;
      case 3: send(wire::trading_action(locate, "THPQ"[op.shares % 4U]), locate, true); break;
      case 4: send(wire::system_event("OSQMEC"[op.shares % 6U]), 0, true); break;
      default: {
        const auto messages = itch::fixtures::all_messages();
        const wire::Bytes& message = messages[op.shares % messages.size()];
        const char type = static_cast<char>(message[0]);
        if (!std::string_view{"AFECXDU"}.contains(type)) {
          send(message, itch::read_header(message.data()).stock_locate, true);
        }
        break;
      }
    }
  }

  void dead_reference(const Op& op) {
    const std::uint16_t locate = kLocates[op.locate % kLocates.size()];
    const std::uint64_t dead = kDeadBit | op.pick;
    switch (op.shares % 5U) {
      case 0: send(wire::execute(locate, dead, 1), locate, false); break;
      case 1: send(wire::execute_with_price(locate, dead, 1, 1), locate, false); break;
      case 2: send(wire::cancel(locate, dead, 1), locate, false); break;
      case 3: send(wire::erase(locate, dead), locate, false); break;
      default: send(wire::replace(locate, dead, dead + 1, 1, 1), locate, false); break;
    }
  }

  void over_deduction(const Op& op) {
    if (live_.empty()) {
      add(op, false);
      return;
    }
    const LiveOrder& order = live_[pick(op)];
    const std::uint32_t shares = order.shares + 1U + op.shares;
    send((op.price & 1U) != 0 ? wire::execute(order.locate, order.reference, shares)
                              : wire::cancel(order.locate, order.reference, shares),
         order.locate, false);
  }

  void wrong_locate(const Op& op) {
    if (live_.empty()) {
      add(op, false);
      return;
    }
    const LiveOrder& order = live_[pick(op)];
    const auto other = static_cast<std::uint16_t>(order.locate + 1U);
    switch (op.shares % 3U) {
      case 0: send(wire::execute(other, order.reference, 1), other, false); break;
      case 1: send(wire::erase(other, order.reference), other, false); break;
      default: send(wire::replace(other, order.reference, kDeadBit, 1, 1), other, false); break;
    }
  }

  void duplicate_add(const Op& op) {
    if (live_.empty()) {
      add(op, false);
      return;
    }
    const LiveOrder& order = live_[pick(op)];
    send(wire::add(order.locate, order.reference, 'B', 1, ladder_price(op.price)), order.locate,
         false);
  }

  void replace_onto_live(const Op& op) {
    if (live_.empty()) {
      add(op, false);
      return;
    }
    const LiveOrder& order = live_[pick(op)];
    const LiveOrder& target = live_[op.shares % live_.size()];
    send(wire::replace(order.locate, order.reference, target.reference, 1, 1), order.locate, false);
  }

  void bad_side(const Op& op) {
    const std::uint16_t locate = kLocates[op.locate % kLocates.size()];
    send(wire::add(locate, kDeadBit | op.pick, 'Z', 1, 1), locate, false);
  }

  Run* oracle_;
  std::vector<LiveOrder> live_;
  std::uint64_t issued_ = 0;
  std::uint64_t expected_violations_ = 0;
  std::uint64_t expected_crossed_ = 0;
  std::uint64_t expected_locked_ = 0;
};

// One input through one arm.
template <class Index, class Levels>
void run_arm(std::span<const std::uint8_t> input) {
  using Run = itch::oracle::BasicOracleRun<Index, Levels>;
  // Building the books took 40% of an input and freeing them 20%. So one run lasts the
  // whole process, and restart() puts it back as it was. The model is built anew each
  // time. tests/test_book.cpp holds restart() to a run that was never used.
  static const Run fresh = fresh_run<Run>();
  static Run oracle = fresh_run<Run>();
  oracle.restart(fresh);

  Harness<Run> harness{oracle};
  for (std::size_t at = 0; at + kRecord <= input.size(); at += kRecord) {
    harness.run({.code = input[at],
                 .pick = input[at + 1],
                 .shares = input[at + 2],
                 .price = input[at + 3],
                 .locate = input[at + 4]});
  }
  harness.finish();
}

}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  const std::span input{data, size};
  run_arm<itch::HashIndex, itch::LinkedLevels>(input);
  run_arm<itch::HashIndex, itch::FlatLevels>(input);
  run_arm<itch::PagedIndex<>, itch::LinkedLevels>(input);
  run_arm<itch::PagedIndex<>, itch::FlatLevels>(input);
  run_arm<itch::IdentityHashIndex, itch::FlatLevels>(input);
  run_arm<itch::ChainedIndex, itch::FlatLevels>(input);
  run_arm<itch::PagedIndex<>, itch::Scan16FlatLevels>(input);
  run_arm<itch::PagedIndex<>, itch::BisectFlatLevels>(input);
  return 0;
}
