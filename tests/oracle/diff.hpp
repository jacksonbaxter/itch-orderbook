// The per-message diff of the fast book against the reference, and the run around it.
//
// docs/NOTES.md, "The oracle's proof", says what each piece covers and what it does not.
// After every message, with no sampling:
//
//   1. Both books gave the same status.
//   2. Each level in the touched set T(m), worked out from the reference, is equal:
//      existence, order count, aggregate shares and the FIFO of (reference, shares).
//   3. The best bid and offer of the message's locate are equal, as price, shares and
//      orders.
//   4. Every level the fast book wrote through its choke point is in T(m).
//   5. Every level the fast book moved in its price order, and that is not in T(m), is
//      equal as in 2. A move rewrites only how a level is found, so it must leave the
//      level as it was.
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
#include <tuple>
#include <utility>
#include <vector>

#include "itch/book.hpp"
#include "itch/messages.hpp"
#include "oracle/canonical.hpp"
#include "oracle/invariants.hpp"
#include "oracle/reference_book.hpp"

namespace itch::oracle {

// What a slot check found wrong, the first time in a message.
enum class SlotFault : std::uint8_t {
  none,
  order_outside_pool,    // An order slot past the pool.
  order_not_live,        // A write used an order slot that holds no live order.
  order_on_other_level,  // A write for one level used an order slot of another.
  order_taken_live,      // The pool handed out an order slot that holds a live order.
  order_freed_twice,     // The pool took back an order slot that was not live.
  level_outside_pool,    // A level slot past the pool.
  level_not_live,        // A write stored into a level slot that holds no live level.
  level_of_other_price,  // A write reported for one level stored into another's slot.
  level_taken_live,      // The pool handed out a level slot that holds a live level.
  level_freed_twice,     // The pool took back a level slot that was not live.
};

[[nodiscard]] constexpr std::string_view to_string(SlotFault fault) noexcept {
  switch (fault) {
    case SlotFault::none: return "none";
    case SlotFault::order_outside_pool: return "order slot outside the pool";
    case SlotFault::order_not_live: return "order slot holds no live order";
    case SlotFault::order_on_other_level: return "order slot is another level's";
    case SlotFault::order_taken_live: return "order slot handed out while live";
    case SlotFault::order_freed_twice: return "order slot taken back while free";
    case SlotFault::level_outside_pool: return "level slot outside the pool";
    case SlotFault::level_not_live: return "level slot holds no live level";
    case SlotFault::level_of_other_price: return "level slot is another level's";
    case SlotFault::level_taken_live: return "level slot handed out while live";
    case SlotFault::level_freed_twice: return "level slot taken back while free";
  }
  return "unnamed";
}

[[nodiscard]] constexpr bool is_order_fault(SlotFault fault) noexcept {
  return fault >= SlotFault::order_outside_pool && fault <= SlotFault::order_freed_twice;
}

// The oracle's write policy: records what the choke point wrote in the current message.
// Writes are few, and more than kKept is itself a failure. Moves are kept in full, since
// each one is compared: a flat array can shift a whole side for one message. Their room
// is sized up front for the most a message can move, so more is a failure as well.
//
// It also checks, as each write is made, every pool slot the write goes through. It keeps
// its own record of which order and level slots are live, from the slots each pool hands
// out and takes back, and refuses a slot handed out while live or taken back while free.
// An order slot a write reads links from or stores into must be live and hold an order of
// the level slot the write is for, and that level slot must be live and hold the level the
// write was reported for. So a write misdirected to another level's slot is seen on the
// message that makes it, whatever level the choke point reported.
class WriteRecorder {
 public:
  static constexpr std::size_t kKept = 16;

  void on_level_write(std::uint16_t locate, Side side, Price4 price) noexcept {
    if (count_ < kKept) {
      writes_[count_] = {.locate = locate, .side = side == Side::bid ? 'B' : 'S', .price = price};
    }
    ++count_;
  }
  void on_level_move(std::uint16_t locate, Side side, Price4 price) noexcept {
    if (move_count_ < moves_.size()) {
      moves_[move_count_] = {
          .locate = locate, .side = side == Side::bid ? 'B' : 'S', .price = price};
    }
    ++move_count_;
  }

  // The level slot a write reported for `id` stored into, read after the write: no write
  // but open changes a level's identity, and open sets it from `id`.
  template <class Levels>
  void on_level_slot(const itch::LevelId& id, std::uint32_t level, const Levels& levels) noexcept {
    ++level_slot_checks_;
    if (level >= level_live_.size()) {
      fault(SlotFault::level_outside_pool, level, kNoIndex);
      return;
    }
    if (level_live_[level] == 0) {
      fault(SlotFault::level_not_live, level, kNoIndex);
      return;
    }
    const auto& held = levels[level];
    if (held.locate != id.locate || held.side != id.side || held.price != id.price) {
      fault(SlotFault::level_of_other_price, level, kNoIndex);
    }
  }
  // An order slot a write for level slot `level` reads links from or stores into, before
  // the store.
  void on_order_slot(std::uint32_t level, std::uint32_t order,
                     const std::vector<BookOrder>& orders) noexcept {
    ++order_slot_checks_;
    if (order >= order_live_.size()) {
      fault(SlotFault::order_outside_pool, order, level);
      return;
    }
    if (order_live_[order] == 0) {
      fault(SlotFault::order_not_live, order, level);
      return;
    }
    if (orders[order].level != level) {
      fault(SlotFault::order_on_other_level, order, level);
    }
  }
  void on_level_take(std::uint32_t level) noexcept {
    take(level_live_, level, SlotFault::level_outside_pool, SlotFault::level_taken_live);
    ++level_slot_checks_;
  }
  void on_level_give_back(std::uint32_t level) noexcept {
    give_back(level_live_, level, SlotFault::level_outside_pool, SlotFault::level_freed_twice);
    ++level_slot_checks_;
  }
  void on_order_take(std::uint32_t order) noexcept {
    take(order_live_, order, SlotFault::order_outside_pool, SlotFault::order_taken_live);
    ++order_slot_checks_;
  }
  void on_order_give_back(std::uint32_t order) noexcept {
    give_back(order_live_, order, SlotFault::order_outside_pool, SlotFault::order_freed_twice);
    ++order_slot_checks_;
  }

  // A message moves at most three entries for each level on one side, on the flat arm: a
  // replace's removal shifts the side, and its add moves the side to a bigger block and
  // shifts it again. The linked arm relinks at most four neighbours.
  void reserve_moves(std::uint32_t levels) {
    moves_.assign((std::size_t{levels} * 3U) + 4U, LevelId{});
  }
  // One flag for each slot of each pool, all free, as the book's pools start.
  void reserve_slots(std::uint32_t orders, std::uint32_t levels) {
    order_live_.assign(orders, 0);
    level_live_.assign(levels, 0);
  }
  void clear() noexcept {
    count_ = 0;
    move_count_ = 0;
    order_slot_checks_ = 0;
    level_slot_checks_ = 0;
    faults_ = 0;
    first_fault_ = {};
  }
  [[nodiscard]] std::size_t count() const noexcept { return count_; }
  [[nodiscard]] std::span<const LevelId> kept() const noexcept {
    return std::span{writes_}.first(count_ < kKept ? count_ : kKept);
  }
  [[nodiscard]] std::size_t move_count() const noexcept { return move_count_; }
  [[nodiscard]] std::span<const LevelId> moves() const noexcept {
    return std::span{moves_}.first(move_count_ < moves_.size() ? move_count_ : moves_.size());
  }

  // The first slot check that failed in this message, with the slot and the level slot the
  // write was for (kNoIndex for a pool's own call), and how many failed.
  struct Fault {
    SlotFault kind = SlotFault::none;
    std::uint32_t slot = kNoIndex;
    std::uint32_t level = kNoIndex;

    friend bool operator==(const Fault&, const Fault&) = default;
  };
  [[nodiscard]] std::uint64_t order_slot_checks() const noexcept { return order_slot_checks_; }
  [[nodiscard]] std::uint64_t level_slot_checks() const noexcept { return level_slot_checks_; }
  [[nodiscard]] std::uint64_t faults() const noexcept { return faults_; }
  [[nodiscard]] const Fault& first_fault() const noexcept { return first_fault_; }
  [[nodiscard]] bool order_live(std::uint32_t order) const noexcept {
    return order < order_live_.size() && order_live_[order] != 0;
  }
  [[nodiscard]] bool level_live(std::uint32_t level) const noexcept {
    return level < level_live_.size() && level_live_[level] != 0;
  }

  friend bool operator==(const WriteRecorder&, const WriteRecorder&) = default;

 private:
  void fault(SlotFault kind, std::uint32_t slot, std::uint32_t level) noexcept {
    if (faults_ == 0) {
      first_fault_ = {.kind = kind, .slot = slot, .level = level};
    }
    ++faults_;
  }
  void take(std::vector<std::uint8_t>& live, std::uint32_t slot, SlotFault outside,
            SlotFault taken_live) noexcept {
    if (slot >= live.size()) {
      fault(outside, slot, kNoIndex);
      return;
    }
    if (live[slot] != 0) {
      fault(taken_live, slot, kNoIndex);
    }
    live[slot] = 1;
  }
  void give_back(std::vector<std::uint8_t>& live, std::uint32_t slot, SlotFault outside,
                 SlotFault freed_twice) noexcept {
    if (slot >= live.size()) {
      fault(outside, slot, kNoIndex);
      return;
    }
    if (live[slot] == 0) {
      fault(freed_twice, slot, kNoIndex);
    }
    live[slot] = 0;
  }

  std::array<LevelId, kKept> writes_{};
  std::size_t count_ = 0;
  std::vector<LevelId> moves_;
  std::size_t move_count_ = 0;
  std::vector<std::uint8_t> order_live_;
  std::vector<std::uint8_t> level_live_;
  std::uint64_t order_slot_checks_ = 0;
  std::uint64_t level_slot_checks_ = 0;
  std::uint64_t faults_ = 0;
  Fault first_fault_;
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
  order_slot,
  level_slot,
  index,
  arena,
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
    case DivergenceKind::order_slot: return "order-slot";
    case DivergenceKind::level_slot: return "level-slot";
    case DivergenceKind::index: return "index";
    case DivergenceKind::arena: return "arena";
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
  std::uint64_t move_reports = 0;
  std::uint64_t moved_checks = 0;        // Moved levels outside T(m), compared in full.
  std::uint64_t empty_moved_checks = 0;  // Moved levels both books agree are gone.
  std::uint64_t order_slot_checks = 0;   // Order slots a write used, handed out or took back.
  std::uint64_t level_slot_checks = 0;   // Level slots, the same.
  std::uint64_t slot_violations = 0;
  std::uint64_t index_checks = 0;  // References a message named, looked up in both books.
  std::uint64_t arena_checks = 0;  // Flat sides seen on a new block, checked against the rest.
  std::uint64_t periodic_hash_checks = 0;
  std::uint64_t final_hash_checks = 0;
  std::uint64_t production_hash_checks = 0;
  std::uint64_t exhaustive_checks = 0;

  friend bool operator==(const DiffCounters&, const DiffCounters&) = default;
};

struct OracleConfig {
  BookCapacity capacity{};
  std::uint64_t hash_every = 1'000'000;  // 0 keeps only the final hash.
  bool exhaustive = false;
  // For the fuzzer: which locates the exhaustive walk covers. Empty means all of them.
  std::span<const std::uint16_t> exhaustive_locates;
};

// One arm of the fast book against the reference. OracleRun is the default arm.
template <class OrderIndex = FastBook<>::Index, class Levels = FastBook<>::LevelArm>
class BasicOracleRun {
 public:
  using Fast = FastBook<WriteRecorder, OrderIndex, Levels>;
  using Production = FastBook<NoWriteObserver, OrderIndex, Levels>;
  using Level = Fast::Level;
  static constexpr bool kFlat = requires(const Levels& levels) { levels.slots_used(); };

  struct ArenaBlock {
    std::uint32_t base = 0;
    std::uint32_t capacity = 0;
  };

  explicit BasicOracleRun(const OracleConfig& config)
      : config_{config},
        fast_{config.capacity},
        production_{config.capacity},
        reference_{std::make_unique<ReferenceBook>()} {
    fast_.observer().reserve_moves(config.capacity.levels);
    fast_.observer().reserve_slots(config.capacity.orders, config.capacity.levels);
    if constexpr (kFlat) {
      arena_owner_.assign(config.capacity.level_slots, 0);
      arena_seen_.assign(kSideCount, ArenaBlock{});
    }
  }

  // Back to the state of `fresh`, a run that has seen nothing, for a caller that starts
  // a stream too often to build the books each time. Assigning a book copies all of it,
  // its free lists and the bytes of its unused slots too. The reference book's orders
  // point into its own lists, so it is built anew and never assigned. A member added to
  // this class has to be added here.
  void restart(const BasicOracleRun& fresh) {
    config_ = fresh.config_;
    fast_ = fresh.fast_;
    production_ = fresh.production_;
    reference_ = std::make_unique<ReferenceBook>();
    invariants_ = fresh.invariants_;
    counters_ = fresh.counters_;
    divergences_ = fresh.divergences_;
    message_ = fresh.message_;
    type_ = fresh.type_;
    last_hash_ = fresh.last_hash_;
    moved_ = fresh.moved_;
    arena_owner_ = fresh.arena_owner_;
    arena_seen_ = fresh.arena_seen_;
  }

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
    compare_moves(outcome.touched);
    compare_slots();
    compare_index(payload);
    compare_arena(outcome.locate);

    invariants_.on_outcome(message_, type_, outcome);
    invariants_.observe(message_, type_, outcome.locate, Invariants::timestamp(payload), bid, ask);

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
  [[nodiscard]] const Fast& fast() const noexcept { return fast_; }
  [[nodiscard]] const Production& production() const noexcept { return production_; }
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
    compare_level(id, counters_.touched_checks, counters_.empty_touched_checks);
  }

  void compare_level(const LevelId& id, std::uint64_t& present, std::uint64_t& absent) {
    const ReferenceBook::Fifo* expected = reference_->level(id);
    const Level* actual =
        fast_.find_level(id.locate, id.side == 'B' ? Side::bid : Side::ask, id.price);
    if (expected == nullptr && actual == nullptr) {
      ++absent;
      return;
    }
    if (expected == nullptr || actual == nullptr) {
      diverge(DivergenceKind::level_presence, std::format("{} fast={} reference={}", name_of(id),
                                                          actual != nullptr, expected != nullptr));
      return;
    }
    ++present;
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

  void compare_fifo(const LevelId& id, const Level& actual, const ReferenceBook::Fifo& expected) {
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

  // Each level moved and not touched, once, however many times the message moved it.
  void compare_moves(const TouchedSet& touched) {
    std::vector<LevelId>& moved = moved_;
    const std::span<const LevelId> reported = fast_.observer().moves();
    counters_.move_reports += fast_.observer().move_count();
    if (fast_.observer().move_count() > reported.size()) {
      diverge(DivergenceKind::write_set,
              std::format("{} moves for one message, only {} are possible",
                          fast_.observer().move_count(), reported.size()));
    }
    moved.assign(reported.begin(), reported.end());
    const auto order = [](const LevelId& a, const LevelId& b) {
      return std::tie(a.locate, a.side, a.price) < std::tie(b.locate, b.side, b.price);
    };
    std::ranges::sort(moved, order);
    const auto [first, last] = std::ranges::unique(moved);
    moved.erase(first, last);
    for (const LevelId& id : moved) {
      if (!touched.contains(id)) {
        const std::size_t before = divergences_.size();
        compare_level(id, counters_.moved_checks, counters_.empty_moved_checks);
        for (std::size_t at = before; at < divergences_.size(); ++at) {
          divergences_[at].detail.insert(0, "moved ");
        }
      }
    }
  }

  // What the recorder found as the message's writes went through each slot.
  void compare_slots() {
    const WriteRecorder& recorder = fast_.observer();
    counters_.order_slot_checks += recorder.order_slot_checks();
    counters_.level_slot_checks += recorder.level_slot_checks();
    if (recorder.faults() == 0) {
      return;
    }
    counters_.slot_violations += recorder.faults();
    const WriteRecorder::Fault& first = recorder.first_fault();
    diverge(
        is_order_fault(first.kind) ? DivergenceKind::order_slot : DivergenceKind::level_slot,
        std::format("{}: slot {}{}, {} failed in this message", to_string(first.kind), first.slot,
                    first.level == kNoIndex ? std::string{}
                                            : std::format(" for level slot {}", first.level),
                    recorder.faults()));
  }

  // Each reference the message names, as each book holds it after the message: gone in
  // both, or live in both on the same level with the same shares, and the fast index finding
  // a live slot that holds it.
  void compare_index(std::span<const std::byte> payload) {
    const NamedReferences named = ReferenceBook::named_references(payload);
    for (std::size_t i = 0; i < named.count; ++i) {
      const std::uint64_t reference = named.references[i];
      ++counters_.index_checks;
      const std::optional<RefOrder> expected = reference_->order(reference);
      const std::uint32_t slot = fast_.order_slot(reference);
      if (!expected.has_value() || slot == kNoIndex) {
        if (expected.has_value() || slot != kNoIndex) {
          diverge(DivergenceKind::index,
                  std::format("reference={} fast={} reference={}", reference,
                              slot == kNoIndex ? "gone" : std::format("slot {}", slot),
                              expected.has_value() ? "live" : "gone"));
        }
        continue;
      }
      const WriteRecorder& recorder = fast_.observer();
      if (!recorder.order_live(slot) || !recorder.level_live(fast_.order_at(slot).level)) {
        diverge(
            DivergenceKind::index,
            std::format("reference={} fast=slot {}, which holds no live order", reference, slot));
        continue;
      }
      const BookOrder& order = fast_.order_at(slot);
      const Level& level = fast_.level_at(order.level);
      const char side = level.side == Side::bid ? 'B' : 'S';
      if (order.reference != reference || order.shares != expected->shares ||
          level.locate != expected->locate || side != expected->side ||
          level.price != expected->price) {
        diverge(DivergenceKind::index,
                std::format("reference={} fast=({},{},{:c},{},{}) reference=({},{},{:c},{},{})",
                            reference, order.reference, level.locate, side, level.price,
                            order.shares, reference, expected->locate, expected->side,
                            expected->price, expected->shares));
      }
    }
  }

  // The flat arm's arena: every side's block is its own. Only the message's locate can
  // have changed its sides, so each of its two sides is held to its count, and a side found
  // on a block it was not on before has to have found every entry of it unowned.
  void compare_arena(std::uint16_t locate) {
    if constexpr (kFlat) {
      for (const Side side : {Side::bid, Side::ask}) {
        const std::size_t key = side_key(locate, side);
        const auto& top = fast_.sides()[key];
        ArenaBlock& seen = arena_seen_[key];
        if (top.count > top.capacity) {
          diverge(DivergenceKind::arena,
                  std::format("locate={} side={:c} holds {} entries in a "
                              "block of {}",
                              locate, side == Side::bid ? 'B' : 'S', top.count, top.capacity));
          continue;
        }
        if (top.base == seen.base && top.capacity == seen.capacity) {
          continue;
        }
        ++counters_.arena_checks;
        claim_block(seen, 0);
        const auto owner = static_cast<std::uint32_t>(key + 1);
        const ArenaBlock now{.base = top.base, .capacity = top.capacity};
        if (now.capacity > arena_owner_.size() || now.base > arena_owner_.size() - now.capacity) {
          diverge(DivergenceKind::arena,
                  std::format("locate={} side={:c} block {} of {} is past the arena of {}", locate,
                              side == Side::bid ? 'B' : 'S', now.base, now.capacity,
                              arena_owner_.size()));
          continue;
        }
        for (std::uint32_t at = now.base; at < now.base + now.capacity; ++at) {
          if (arena_owner_[at] != 0) {
            diverge(DivergenceKind::arena,
                    std::format("locate={} side={:c} block {} of {} holds entry {} of side "
                                "record {}",
                                locate, side == Side::bid ? 'B' : 'S', now.base, now.capacity, at,
                                arena_owner_[at] - 1));
            break;
          }
        }
        seen = now;
        claim_block(seen, owner);
      }
    }
  }

  void claim_block(const ArenaBlock& block, std::uint32_t owner) {
    if (block.capacity == 0 || block.capacity > arena_owner_.size() ||
        block.base > arena_owner_.size() - block.capacity) {
      return;
    }
    for (std::uint32_t at = block.base; at < block.base + block.capacity; ++at) {
      arena_owner_[at] = owner;
    }
  }

  // A list in a sound book ends within its pool: a side's levels within the level pool, a
  // level's FIFO within the order pool, each link naming a slot of it. One that does not is
  // a cycle or a stray link, which a canonical walk would follow until its own far limit.
  // So each whole-book walk is preceded by this one, bounded by the book's capacity, and a
  // list past it is a divergence. A sound book's words, and so its hash, are unchanged.
  template <class Book>
  [[nodiscard]] std::optional<std::string> endless_list(const Book& book,
                                                        std::span<const std::uint16_t> locates,
                                                        std::string_view name) const {
    std::optional<std::string> found;
    const auto side_of = [&](std::uint16_t locate, Side side) {
      std::uint64_t levels = 0;
      book.walk_side(locate, side, [&](const typename Book::Level& level) {
        if (++levels > config_.capacity.levels) {
          found = std::format("{} book: locate={} side={:c} has more levels than its pool of {}",
                              name, locate, side == Side::bid ? 'B' : 'S', config_.capacity.levels);
          return false;
        }
        std::uint64_t orders = 0;
        for (std::uint32_t order = level.front; order != kNoIndex;
             order = book.order_at(order).newer) {
          if (order >= book.order_capacity() || ++orders > config_.capacity.orders) {
            found = std::format(
                "{} book: locate={} side={:c} price={} has a FIFO that does not "
                "end within its pool of {}",
                name, locate, side == Side::bid ? 'B' : 'S', level.price, book.order_capacity());
            return false;
          }
        }
        return true;
      });
    };
    if (locates.empty()) {
      const auto sides = book.sides();
      for (std::size_t key = 0; key < sides.size() && !found; ++key) {
        if (!sides[key].empty()) {
          side_of(static_cast<std::uint16_t>(key >> 1U), (key & 1U) == 0 ? Side::bid : Side::ask);
        }
      }
    } else {
      for (std::size_t at = 0; at < locates.size() && !found; ++at) {
        side_of(locates[at], Side::bid);
        if (!found) {
          side_of(locates[at], Side::ask);
        }
      }
    }
    return found;
  }

  void compare_hash(DivergenceKind kind, std::uint64_t& counter) {
    for (const auto& endless : {endless_list(fast_, {}, "fast"),
                                endless_list(production_, {}, "default instantiation")}) {
      if (endless.has_value()) {
        ++counter;
        diverge(kind, *endless);
        return;
      }
    }
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
    if (const auto endless = endless_list(fast_, config_.exhaustive_locates, "fast")) {
      ++counters_.exhaustive_checks;
      diverge(DivergenceKind::exhaustive, *endless);
      return;
    }
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
  Fast fast_;
  Production production_;  // The instantiation that ships, held to the same hash.
  std::unique_ptr<ReferenceBook> reference_;
  Invariants invariants_;
  DiffCounters counters_;
  std::vector<Divergence> divergences_;
  std::uint64_t message_ = 0;
  char type_ = '\0';
  std::uint64_t last_hash_ = 0;
  std::vector<LevelId> moved_;  // Scratch for compare_moves, kept to reuse its storage.
  // The flat arm's arena as the oracle has seen it: the side record that owns each entry, plus
  // one, or 0 for none, and each side record's block.
  std::vector<std::uint32_t> arena_owner_;
  std::vector<ArenaBlock> arena_seen_;
};

using OracleRun = BasicOracleRun<>;

}  // namespace itch::oracle
