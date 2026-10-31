// T-INV-1 and T-INV-2, read from the reference book and the feed.
//
// T-INV-1: every execute, cancel, delete and replace names a live order. Any message
// the reference had to refuse is a violation, with no tolerance.
//
// T-INV-2: the book is never crossed. The check runs after every message, on that
// message's locate. A crossed observation fails it. A locked one is counted apart.
// Nothing here excuses an observation. Each one is characterised by symbol, time and
// trading state, and the tallies by message type let a reader rebuild the populations
// that others publish: all observations, and market open with the symbol in state T.
//
// Trading state comes from H, market hours from S, and symbols from R. A symbol with no
// H yet has state '-'. Spec 1.2.2 says to treat such a symbol as halted, so it counts as
// not in T, and it is shown apart so that reliance on the default stays visible.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <format>
#include <map>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "itch/messages.hpp"
#include "oracle/reference_book.hpp"

namespace itch::oracle {

inline constexpr std::uint64_t kRegularOpenNs = 34'200'000'000'000ULL;   // 09:30:00
inline constexpr std::uint64_t kRegularCloseNs = 57'600'000'000'000ULL;  // 16:00:00
inline constexpr std::uint64_t kResumptionWindowNs = 50'000'000ULL;      // Published figure, 50 ms.
inline constexpr std::size_t kInv1DetailLimit = 20;

enum class Scope : std::uint8_t { all = 0, open_and_trading = 1 };

struct Tally {
  std::uint64_t checks = 0;
  std::uint64_t two_sided = 0;
  std::uint64_t crossed = 0;
  std::uint64_t locked = 0;
};

// One bucket of crossed and locked observations.
struct Pair {
  std::uint64_t crossed = 0;
  std::uint64_t locked = 0;
};

struct SymbolStats {
  Pair count;
  std::uint64_t first_ns = 0;
  std::uint64_t last_ns = 0;
  std::uint32_t deepest = 0;
  std::map<char, Pair> by_state;
};

[[nodiscard]] inline std::string clock_time(std::uint64_t ns) {
  const std::uint64_t seconds = ns / 1'000'000'000ULL;
  return std::format("{:02}:{:02}:{:02}.{:09}", seconds / 3600, (seconds / 60) % 60, seconds % 60,
                     ns % 1'000'000'000ULL);
}

class Invariants {
 public:
  Invariants()
      : state_(65536, kNoAction),
        symbol_(65536),
        resumed_ns_(65536, 0),
        window_open_(65536, 0),
        tallies_(256) {}

  // How many T-INV-1 violations are printed in full. All of them are counted.
  void set_inv1_detail_limit(std::size_t limit) noexcept { inv1_detail_limit_ = limit; }

  // Writes one line per crossed or locked observation. Null keeps only the summary.
  void set_observation_log(std::FILE* log) noexcept { log_ = log; }

  // Before the books see the message, so that an observation sees its own state change.
  void on_feed(std::span<const std::byte> payload) {
    switch (read_char(payload.data())) {
      case StockDirectory::kType: on_directory(StockDirectory::from_wire(payload.data())); break;
      case StockTradingAction::kType:
        on_trading_action(StockTradingAction::from_wire(payload.data()));
        break;
      case SystemEvent::kType: on_system_event(SystemEvent::from_wire(payload.data())); break;
      case CrossTrade::kType:
        on_cross(CrossTrade::from_wire(payload.data()).header.stock_locate);
        break;
      case OperationalHalt::kType: ++operational_halts_; break;
      default: break;
    }
  }

  // T-INV-1. Every refused message counts, in full, whatever its type.
  void on_outcome(std::uint64_t message, char type, const RefOutcome& outcome) {
    if (outcome.status == RefStatus::applied || outcome.status == RefStatus::no_book_effect) {
      return;
    }
    ++inv1_violations_;
    ++inv1_by_status_[to_string(outcome.status)];
    if (inv1_details_ < inv1_detail_limit_) {
      ++inv1_details_;
      std::println("t_inv1_violation message={} type={} locate={} reference={} status={}", message,
                   type, outcome.locate, outcome.reference, to_string(outcome.status));
    }
  }

  // T-INV-2, after the books have taken the message.
  void observe(std::uint64_t message, char type, std::uint16_t locate, std::uint64_t timestamp_ns,
               const RefTop& bid, const RefTop& ask) {
    const char state = state_[locate];
    const bool continuous = market_open_ && state == 'T';
    Tally& all = tallies_[static_cast<unsigned char>(type)][0];
    Tally& open = tallies_[static_cast<unsigned char>(type)][1];
    ++all.checks;
    open.checks += continuous ? 1U : 0U;
    if (!bid.present || !ask.present || bid.price < ask.price) {
      all.two_sided += bid.present && ask.present ? 1U : 0U;
      open.two_sided += continuous && bid.present && ask.present ? 1U : 0U;
      window_open_[locate] = 0;
      return;
    }
    ++all.two_sided;
    open.two_sided += continuous ? 1U : 0U;
    const bool crossed = bid.price > ask.price;
    (crossed ? all.crossed : all.locked) += 1;
    if (continuous) {
      (crossed ? open.crossed : open.locked) += 1;
    }
    record(message, type, locate, timestamp_ns, bid.price, ask.price, crossed);
  }

  [[nodiscard]] std::uint64_t inv1_violations() const noexcept { return inv1_violations_; }
  [[nodiscard]] std::uint64_t crossed() const noexcept { return all_.crossed; }
  [[nodiscard]] std::uint64_t locked() const noexcept { return all_.locked; }
  [[nodiscard]] char trading_state(std::uint16_t locate) const noexcept { return state_[locate]; }
  [[nodiscard]] bool market_open() const noexcept { return market_open_; }

  void print_report() const {
    std::println("t_inv1_violations {}", inv1_violations_);
    for (const auto& [status, count] : inv1_by_status_) {
      std::println("t_inv1_by_status {} {}", status, count);
    }
    std::println("t_inv2_crossed {}", all_.crossed);
    std::println("t_inv2_locked {}", all_.locked);
    print_scope_tallies();
    std::println("t_inv2_deepest_crossing {}", deepest_);
    std::println("t_inv2_crossed_symbols {}", symbol_count(true));
    std::println("t_inv2_locked_symbols {}", symbol_count(false));
    std::println("t_inv2_not_in_t crossed={} locked={}", not_in_t_.crossed, not_in_t_.locked);
    std::println("t_inv2_no_action_yet crossed={} locked={}", no_action_.crossed,
                 no_action_.locked);
    std::println("t_inv2_in_reopening_window crossed={} locked={}", in_window_.crossed,
                 in_window_.locked);
    std::println("t_inv2_in_t_outside_window crossed={} locked={}", outside_window_.crossed,
                 outside_window_.locked);
    std::println("t_inv2_market_open crossed={} locked={}", market_hours_.crossed,
                 market_hours_.locked);
    std::println("t_inv2_market_open_and_t crossed={} locked={}", open_and_t_.crossed,
                 open_and_t_.locked);
    std::println("t_inv2_clock_0930_1600 crossed={} locked={}", clock_hours_.crossed,
                 clock_hours_.locked);
    std::println("t_inv2_in_t_within_50ms_of_resumption crossed={} locked={}", within_50ms_.crossed,
                 within_50ms_.locked);
    std::println("t_inv2_in_t_later_than_50ms crossed={} locked={}", beyond_50ms_.crossed,
                 beyond_50ms_.locked);
    std::println("t_inv2_in_t_never_resumed crossed={} locked={}", never_resumed_.crossed,
                 never_resumed_.locked);
    std::println("t_inv2_reopening_windows {}", windows_opened_);
    std::println("t_inv2_longest_since_resume_ns {}", longest_since_resume_);
    std::println("operational_halt_messages {}", operational_halts_);
    for (const auto& [state, pair] : by_state_) {
      std::println("t_inv2_by_state {} crossed={} locked={}", state, pair.crossed, pair.locked);
    }
    for (const auto& [key, pair] : by_cause_) {
      std::println("t_inv2_cause type={:c} cause={} crossed={} locked={}", key.first, key.second,
                   pair.crossed, pair.locked);
    }
    for (const auto& [locate, stats] : symbols_) {
      print_symbol(locate, stats);
    }
  }

 private:
  static constexpr char kNoAction = '-';

  void on_directory(const StockDirectory& m) { symbol_[m.header.stock_locate] = m.stock; }

  void on_trading_action(const StockTradingAction& m) {
    const std::uint16_t locate = m.header.stock_locate;
    if (m.trading_state == 'T' && state_[locate] != 'T' && state_[locate] != kNoAction) {
      resumed_ns_[locate] = m.header.timestamp_ns;
      window_open_[locate] = 1;
      ++windows_opened_;
    }
    state_[locate] = m.trading_state;
  }

  void on_system_event(const SystemEvent& m) {
    if (m.event_code == 'Q') {
      market_open_ = true;
    } else if (m.event_code == 'M') {
      market_open_ = false;
    }
  }

  void on_cross(std::uint16_t locate) { window_open_[locate] = 0; }

  static void bump(Pair& pair, bool crossed) { (crossed ? pair.crossed : pair.locked) += 1; }

  void record(std::uint64_t message, char type, std::uint16_t locate, std::uint64_t timestamp_ns,
              std::uint32_t bid, std::uint32_t ask, bool crossed) {
    const char state = state_[locate];
    const bool in_window = state == 'T' && window_open_[locate] != 0;
    const bool in_clock = timestamp_ns >= kRegularOpenNs && timestamp_ns < kRegularCloseNs;
    const std::uint32_t depth = crossed ? bid - ask : 0;
    const std::uint64_t since_resume =
        resumed_ns_[locate] == 0 ? 0 : timestamp_ns - resumed_ns_[locate];
    tally_population(locate, state, in_window, in_clock, since_resume, crossed);
    bump(by_cause_[{type, cause_of(state, in_window)}], crossed);
    if (in_clock) {
      bump(by_cause_[{type, "clock"}], crossed);
    }
    deepest_ = depth > deepest_ ? depth : deepest_;

    SymbolStats& stats = symbols_[locate];
    if (stats.count.crossed + stats.count.locked == 0) {
      stats.first_ns = timestamp_ns;
    }
    stats.last_ns = timestamp_ns;
    stats.deepest = depth > stats.deepest ? depth : stats.deepest;
    bump(stats.count, crossed);
    bump(stats.by_state[state], crossed);

    if (log_ != nullptr) {
      std::println(log_,
                   "{} message={} type={} symbol={} locate={} time={} ts={} state={} "
                   "market_open={} in_window={} since_resume_ns={} bid={} ask={} depth={}",
                   crossed ? "crossed" : "locked", message, type, symbol_name(locate), locate,
                   clock_time(timestamp_ns), timestamp_ns, state, market_open_ ? 1 : 0,
                   in_window ? 1 : 0, since_resume, bid, ask, depth);
    }
  }

  // The populations one observation falls in.
  void tally_population(std::uint16_t locate, char state, bool in_window, bool in_clock,
                        std::uint64_t since_resume, bool crossed) {
    bump(all_, crossed);
    if (state != 'T') {
      bump(not_in_t_, crossed);
    } else if (in_window) {
      bump(in_window_, crossed);
    } else {
      bump(outside_window_, crossed);
    }
    if (state == kNoAction) {
      bump(no_action_, crossed);
    }
    if (state == 'T') {
      if (resumed_ns_[locate] == 0) {
        bump(never_resumed_, crossed);
      } else {
        bump(since_resume <= kResumptionWindowNs ? within_50ms_ : beyond_50ms_, crossed);
      }
    }
    if (market_open_) {
      bump(market_hours_, crossed);
    }
    if (market_open_ && state == 'T') {
      bump(open_and_t_, crossed);
    }
    if (in_clock) {
      bump(clock_hours_, crossed);
    }
    bump(by_state_[state], crossed);
    if (state == 'T' && since_resume > longest_since_resume_) {
      longest_since_resume_ = since_resume;
    }
  }

  [[nodiscard]] static std::string_view cause_of(char state, bool in_window) noexcept {
    if (state != 'T') {
      return "not_t";
    }
    return in_window ? "window" : "continuous";
  }

  [[nodiscard]] std::string symbol_name(std::uint16_t locate) const {
    std::string name;
    for (const char c : symbol_[locate]) {
      if (c != ' ' && c != '\0') {
        name.push_back(c);
      }
    }
    return name.empty() ? "?" : name;
  }

  [[nodiscard]] std::uint64_t symbol_count(bool crossed) const {
    std::uint64_t count = 0;
    for (const auto& [locate, stats] : symbols_) {
      count += (crossed ? stats.count.crossed : stats.count.locked) != 0 ? 1U : 0U;
    }
    return count;
  }

  void print_scope_tallies() const {
    for (std::size_t type = 0; type < tallies_.size(); ++type) {
      for (std::size_t scope = 0; scope < 2; ++scope) {
        const Tally& tally = tallies_[type][scope];
        if (tally.checks != 0) {
          std::println(
              "t_inv2_tally type={:c} scope={} checks={} two_sided={} crossed={} locked={}",
              static_cast<char>(type), scope == 0 ? "all" : "open_t", tally.checks, tally.two_sided,
              tally.crossed, tally.locked);
        }
      }
    }
  }

  void print_symbol(std::uint16_t locate, const SymbolStats& stats) const {
    std::string states;
    for (const auto& [state, pair] : stats.by_state) {
      states += std::format("{}:{}/{},", state, pair.crossed, pair.locked);
    }
    if (!states.empty()) {
      states.pop_back();
    }
    std::println(
        "t_inv2_symbol {} locate={} crossed={} locked={} first={} last={} deepest={} "
        "states={}",
        symbol_name(locate), locate, stats.count.crossed, stats.count.locked,
        clock_time(stats.first_ns), clock_time(stats.last_ns), stats.deepest, states);
  }

  std::vector<char> state_;
  std::vector<Stock> symbol_;
  std::vector<std::uint64_t> resumed_ns_;
  std::vector<std::uint8_t> window_open_;
  std::vector<std::array<Tally, 2>> tallies_;
  bool market_open_ = false;
  std::FILE* log_ = nullptr;

  std::uint64_t inv1_violations_ = 0;
  std::size_t inv1_details_ = 0;
  std::size_t inv1_detail_limit_ = kInv1DetailLimit;
  std::map<std::string_view, std::uint64_t> inv1_by_status_;

  Pair all_;
  Pair not_in_t_;
  Pair no_action_;
  Pair in_window_;
  Pair outside_window_;
  Pair within_50ms_;
  Pair beyond_50ms_;
  Pair never_resumed_;
  Pair market_hours_;
  Pair open_and_t_;
  Pair clock_hours_;
  std::map<char, Pair> by_state_;
  std::map<std::pair<char, std::string_view>, Pair> by_cause_;
  std::map<std::uint16_t, SymbolStats> symbols_;
  std::uint32_t deepest_ = 0;
  std::uint64_t windows_opened_ = 0;
  std::uint64_t longest_since_resume_ = 0;
  std::uint64_t operational_halts_ = 0;
};

}  // namespace itch::oracle
