// The per-message diff of the fast book against the reference, and the run around it.
//
// NOTES.md, "The oracle's proof", says what each piece covers and what it does not.
// After every message, with no sampling:
//
//   1. Both books gave the same status.
//   2. Each level in the touched set T(m), worked out from the reference, is equal:
//      existence, order count, aggregate shares and the FIFO of (reference, shares).
//   3. The best bid and offer of the message's locate are equal, as price, shares and
//      orders.
//   4. Every level the fast book wrote through its choke point is in T(m).
//
// Every hash_every messages and at the end of the stream, a canonical walk of each whole
// book must agree. With exhaustive set, that walk runs in full after every message, as a
// control on the fixtures and in the fuzzer.
//
// The first divergence ends the run, because everything after it is noise. T-INV-1 and
// T-INV-2 failures do not: a message both books refused leaves them equal.
#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <format>
#include <memory>
#include <optional>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "itch/book.hpp"
#include "itch/messages.hpp"
#include "oracle/canonical.hpp"
#include "oracle/invariants.hpp"
#include "oracle/reference_book.hpp"

namespace itch::oracle {

// The oracle's write policy: records what the choke point wrote in the current message.
class WriteRecorder {
 public:
  static constexpr std::size_t kKept = 16;

  void on_level_write(std::uint16_t locate, Side side, Price4 price) noexcept {
    if (count_ < kKept) {
      writes_[count_] = {.locate = locate, .side = side == Side::bid ? 'B' : 'S', .price = price};
    }
    ++count_;
  }
  void clear() noexcept { count_ = 0; }
  [[nodiscard]] std::size_t count() const noexcept { return count_; }
  [[nodiscard]] std::span<const LevelId> kept() const noexcept {
    return std::span{writes_}.first(count_ < kKept ? count_ : kKept);
  }

 private:
  std::array<LevelId, kKept> writes_{};
  std::size_t count_ = 0;
};

using OracleFastBook = FastBook<WriteRecorder>;

enum class DivergenceKind : std::uint8_t {
  status,
  level_presence,
  order_count,
  aggregate,
  fifo,
  bbo,
  write_set,
  periodic_hash,
  final_hash,
  exhaustive,
  production_hash,
};

[[nodiscard]] constexpr std::string_view to_string(DivergenceKind kind) noexcept {
  switch (kind) {
    case DivergenceKind::status: return "status";
    case DivergenceKind::level_presence: return "level-presence";
    case DivergenceKind::order_count: return "order-count";
    case DivergenceKind::aggregate: return "aggregate";
    case DivergenceKind::fifo: return "fifo";
    case DivergenceKind::bbo: return "bbo";
    case DivergenceKind::write_set: return "write-set";
    case DivergenceKind::periodic_hash: return "periodic-hash";
    case DivergenceKind::final_hash: return "final-hash";
    case DivergenceKind::exhaustive: return "exhaustive";
    case DivergenceKind::production_hash: return "production-hash";
  }
  return "unnamed";
}

struct Divergence {
  DivergenceKind kind;
  std::uint64_t message;
  char type;
  std::string detail;
};

[[nodiscard]] constexpr bool same_status(ApplyStatus fast, RefStatus reference) noexcept {
  switch (reference) {
    case RefStatus::applied: return fast == ApplyStatus::applied;
    case RefStatus::no_book_effect: return fast == ApplyStatus::no_book_effect;
    case RefStatus::unknown_order: return fast == ApplyStatus::unknown_order;
    case RefStatus::locate_mismatch: return fast == ApplyStatus::locate_mismatch;
    case RefStatus::over_deduction: return fast == ApplyStatus::over_deduction;
    case RefStatus::duplicate_reference: return fast == ApplyStatus::duplicate_reference;
    case RefStatus::bad_side: return fast == ApplyStatus::bad_side;
  }
  return false;
}

struct DiffCounters {
  std::uint64_t messages_diffed = 0;
  std::uint64_t status_checks = 0;
  std::uint64_t touched_checks = 0;
  std::uint64_t empty_touched_checks = 0;  // Touched levels both books agree are gone.
  std::uint64_t bbo_checks = 0;
  std::uint64_t write_set_checks = 0;
  std::uint64_t write_set_writes = 0;
  std::uint64_t write_set_violations = 0;
  std::uint64_t periodic_hash_checks = 0;
  std::uint64_t final_hash_checks = 0;
  std::uint64_t production_hash_checks = 0;
  std::uint64_t exhaustive_checks = 0;
};

struct OracleConfig {
  BookCapacity capacity{};
  std::uint64_t hash_every = 1'000'000;  // 0 keeps only the final hash.
  bool exhaustive = false;
  // For the fuzzer: which locates the exhaustive walk covers. Empty means all of them.
  std::span<const std::uint16_t> exhaustive_locates{};
};

class OracleRun {
 public:
  explicit OracleRun(const OracleConfig& config)
      : config_{config},
        fast_{config.capacity},
        production_{config.capacity},
        reference_{std::make_unique<ReferenceBook>()} {}

  // Takes one whole message. False once the books have diverged.
  bool on_message(std::span<const std::byte> payload) {
    const Header header = read_header(payload.data());
    message_ = counters_.messages_diffed + 1;
    type_ = header.message_type;
    invariants_.on_feed(payload);

    fast_.observer().clear();
    const ApplyStatus fast_status = fast_.apply(payload);
    static_cast<void>(production_.apply(payload));
    const RefOutcome outcome = reference_->apply(payload);

    ++counters_.messages_diffed;
    compare_status(fast_status, outcome);
    for (std::size_t i = 0; i < outcome.touched.count; ++i) {
      compare_level(outcome.touched.levels[i]);
    }
    const RefTop bid = reference_->best(outcome.locate, 'B');
    const RefTop ask = reference_->best(outcome.locate, 'S');
    compare_top(outcome.locate, 'B', bid);
    compare_top(outcome.locate, 'S', ask);
    compare_write_set(outcome.touched);

    invariants_.on_outcome(message_, type_, outcome);
    invariants_.observe(message_, type_, outcome.locate, header.timestamp_ns, bid, ask);

    if (config_.hash_every != 0 && counters_.messages_diffed % config_.hash_every == 0) {
      compare_hash(DivergenceKind::periodic_hash, counters_.periodic_hash_checks);
    }
    if (config_.exhaustive) {
      compare_exhaustive();
    }
    return divergences_.empty();
  }

  // The end-of-stream hash. Call once, after the last message.
  void finish() {
    if (divergences_.empty()) {
      compare_hash(DivergenceKind::final_hash, counters_.final_hash_checks);
    }
  }

  [[nodiscard]] const std::vector<Divergence>& divergences() const noexcept { return divergences_; }
  [[nodiscard]] const DiffCounters& counters() const noexcept { return counters_; }
  [[nodiscard]] const Invariants& invariants() const noexcept { return invariants_; }
  [[nodiscard]] Invariants& invariants() noexcept { return invariants_; }
  [[nodiscard]] const OracleFastBook& fast() const noexcept { return fast_; }
  [[nodiscard]] const ReferenceBook& reference() const noexcept { return *reference_; }

 private:
  void diverge(DivergenceKind kind, std::string detail) {
    divergences_.push_back(
        {.kind = kind, .message = message_, .type = type_, .detail = std::move(detail)});
  }

  void compare_status(ApplyStatus fast, const RefOutcome& outcome) {
    ++counters_.status_checks;
    if (!same_status(fast, outcome.status)) {
      diverge(DivergenceKind::status,
              std::format("fast={} reference={}", to_string(fast), to_string(outcome.status)));
    }
  }

  [[nodiscard]] static std::string name_of(const LevelId& id) {
    return std::format("locate={} side={:c} price={}", id.locate, id.side, id.price);
  }

  void compare_level(const LevelId& id) {
    const ReferenceBook::Fifo* expected = reference_->level(id);
    const BookLevel* actual =
        fast_.find_level(id.locate, id.side == 'B' ? Side::bid : Side::ask, id.price);
    if (expected == nullptr && actual == nullptr) {
      ++counters_.empty_touched_checks;
      return;
    }
    if (expected == nullptr || actual == nullptr) {
      diverge(DivergenceKind::level_presence, std::format("{} fast={} reference={}", name_of(id),
                                                          actual != nullptr, expected != nullptr));
      return;
    }
    ++counters_.touched_checks;
    std::uint64_t shares = 0;
    std::uint64_t orders = 0;
    for (const ReferenceBook::Resting& order : *expected) {
      shares += order.shares;
      ++orders;
    }
    if (actual->order_count != orders) {
      diverge(DivergenceKind::order_count,
              std::format("{} fast={} reference={}", name_of(id), actual->order_count, orders));
    }
    if (actual->aggregate_shares != shares) {
      diverge(DivergenceKind::aggregate, std::format("{} fast={} reference={}", name_of(id),
                                                     actual->aggregate_shares, shares));
    }
    compare_fifo(id, *actual, *expected);
  }

  void compare_fifo(const LevelId& id, const BookLevel& actual,
                    const ReferenceBook::Fifo& expected) {
    std::uint32_t at = actual.front;
    std::size_t position = 0;
    for (const ReferenceBook::Resting& order : expected) {
      if (at == kNoIndex) {
        diverge(DivergenceKind::fifo,
                std::format("{} position={} fast=end reference=({},{})", name_of(id), position,
                            order.reference, order.shares));
        return;
      }
      const BookOrder& fast_order = fast_.order_at(at);
      if (fast_order.reference != order.reference || fast_order.shares != order.shares) {
        diverge(
            DivergenceKind::fifo,
            std::format("{} position={} fast=({},{}) reference=({},{})", name_of(id), position,
                        fast_order.reference, fast_order.shares, order.reference, order.shares));
        return;
      }
      at = fast_order.newer;
      ++position;
    }
    if (at != kNoIndex) {
      diverge(DivergenceKind::fifo,
              std::format("{} position={} fast=({},{}) reference=end", name_of(id), position,
                          fast_.order_at(at).reference, fast_.order_at(at).shares));
    }
  }

  void compare_top(std::uint16_t locate, char side, const RefTop& expected) {
    ++counters_.bbo_checks;
    const TopOfBook actual = fast_.best(locate, side == 'B' ? Side::bid : Side::ask);
    const bool same =
        actual.present == expected.present &&
        (!expected.present || (actual.price == expected.price && actual.shares == expected.shares &&
                               actual.orders == expected.orders));
    if (!same) {
      diverge(DivergenceKind::bbo,
              std::format("locate={} side={:c} fast=({},{},{},{}) reference=({},{},{},{})", locate,
                          side, actual.present, actual.price, actual.shares, actual.orders,
                          expected.present, expected.price, expected.shares, expected.orders));
    }
  }

  void compare_write_set(const TouchedSet& touched) {
    ++counters_.write_set_checks;
    const WriteRecorder& recorder = fast_.observer();
    counters_.write_set_writes += recorder.count();
    for (const LevelId& write : recorder.kept()) {
      if (!touched.contains(write)) {
        ++counters_.write_set_violations;
        diverge(DivergenceKind::write_set, std::format("{} is not touched", name_of(write)));
      }
    }
    if (recorder.count() > WriteRecorder::kKept) {
      ++counters_.write_set_violations;
      diverge(DivergenceKind::write_set,
              std::format("{} writes for one message, only {} are possible", recorder.count(),
                          WriteRecorder::kKept));
    }
  }

  void compare_hash(DivergenceKind kind, std::uint64_t& counter) {
    WordHash fast;
    WordHash reference;
    WordHash production;
    emit_fast(fast_, fast);
    emit_reference(*reference_, reference);
    emit_fast(production_, production);
    ++counter;
    ++counters_.production_hash_checks;
    last_hash_ = reference.value();
    if (fast.value() != reference.value() || fast.words() != reference.words()) {
      diverge(kind, std::format("fast={:016x}/{} reference={:016x}/{}", fast.value(), fast.words(),
                                reference.value(), reference.words()));
    }
    if (production.value() != fast.value()) {
      diverge(DivergenceKind::production_hash,
              std::format("default instantiation={:016x} oracle instantiation={:016x}",
                          production.value(), fast.value()));
    }
  }

  void compare_exhaustive() {
    WordList fast;
    WordList reference;
    if (config_.exhaustive_locates.empty()) {
      emit_fast(fast_, fast);
      emit_reference(*reference_, reference);
    } else {
      emit_fast(fast_, config_.exhaustive_locates, fast);
      emit_reference(*reference_, config_.exhaustive_locates, reference);
    }
    ++counters_.exhaustive_checks;
    if (fast.words() != reference.words()) {
      std::size_t at = 0;
      const std::size_t limit = std::min(fast.words().size(), reference.words().size());
      while (at < limit && fast.words()[at] == reference.words()[at]) {
        ++at;
      }
      diverge(DivergenceKind::exhaustive,
              std::format("first difference at word {} of {} and {}", at, fast.words().size(),
                          reference.words().size()));
    }
  }

 public:
  [[nodiscard]] std::uint64_t last_hash() const noexcept { return last_hash_; }

 private:
  OracleConfig config_;
  OracleFastBook fast_;
  FastBook<> production_;  // The instantiation that ships, held to the same hash.
  std::unique_ptr<ReferenceBook> reference_;
  Invariants invariants_;
  DiffCounters counters_;
  std::vector<Divergence> divergences_;
  std::uint64_t message_ = 0;
  char type_ = '\0';
  std::uint64_t last_hash_ = 0;
};

}  // namespace itch::oracle
