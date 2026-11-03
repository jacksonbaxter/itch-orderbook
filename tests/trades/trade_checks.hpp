// Trade-print verification: a whole day through a book, checked against the exchange's
// own prints. T-TRADE-1, T-TRADE-2, T-TRADE-3 and T-TRADE-5, and the per-type table.
//
// The run is templated on the book, so a later book drops in. What it asks of one is the
// TradeBook concept. It does not read a book's internals, and it decides nothing from
// the book's return status alone: each order message is judged by what the book says
// about that order before and after it.
//
// Three parties take part, and each check sets two of them against each other:
//
//   The book       what it holds for an order, through resting_order() and best().
//   The ledger     a separate table of each live order, kept here. It holds what the
//                  feed added and the sum of what the feed deducted, and no remainder.
//   The reference  CrossStats and MIDAS, from outside the feed's own bytes.
//
// The per-order identity (T-TRADE-1). An order here is one reference: A, F and the new
// side of U each open one, carrying `added` shares. Then, for every order:
//
//     added = sum(E) + sum(C) + sum(X) + closing remainder
//
// where the closing remainder is what the book still holds when a D or the old side of
// a U ends the order, and is 0 for an order that ran out. E, C and X come from the feed.
// The remainder comes from the book, so the two sides are different witnesses, and D
// carries no shares for the feed to state. U moves nothing across references: the
// remainder of the old order leaves, and the new order opens with U's own share count.
// Nasdaq sends no D for an order that reaches zero, so the book must drop it at exactly
// that message, and must still hold anything else. The ledger compares after every order
// message. End of day adds the whole-book form of the same identity, and an empty book.
//
// What each comparison is, and why the sides can disagree, is in trades_report.py.
#pragma once

#include <algorithm>
#include <array>
#include <concepts>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <format>
#include <limits>
#include <map>
#include <optional>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

#include "itch/book.hpp"
#include "itch/messages.hpp"
#include "trades/cross_stats.hpp"
#include "trades/midas.hpp"
#include "trades/reference_text.hpp"

namespace itch::trades {

// What a run needs of a book. Everything else about the book is its own business.
template <class B>
concept TradeBook = requires(B& book, const B& view, std::span<const std::byte> payload,
                             std::uint64_t reference, std::uint16_t locate, Side side) {
  { book.apply(payload) } -> std::same_as<ApplyStatus>;
  { view.resting_order(reference) } -> std::same_as<std::optional<RestingOrder>>;
  { view.best(locate, side) } -> std::same_as<TopOfBook>;
  { view.live_orders() } -> std::convertible_to<std::uint64_t>;
  { book.observer().writes() } -> std::convertible_to<std::uint64_t>;
};

// The book's write policy here: how many level writes and moves a message made. A print
// that must not touch the book has to leave this alone.
class WriteCounter {
 public:
  void on_level_write(std::uint16_t /*locate*/, Side /*side*/, Price4 /*price*/) noexcept {
    ++writes_;
  }
  void on_level_move(std::uint16_t /*locate*/, Side /*side*/, Price4 /*price*/) noexcept {
    ++writes_;
  }
  [[nodiscard]] std::uint64_t writes() const noexcept { return writes_; }

 private:
  std::uint64_t writes_ = 0;
};

// Which result a check belongs to. Each has its own bit in the tool's exit status.
enum class Group : std::uint8_t { t1, t2, t3, t5, prints };

[[nodiscard]] constexpr std::string_view to_string(Group group) noexcept {
  switch (group) {
    case Group::t1: return "T-TRADE-1";
    case Group::t2: return "T-TRADE-2";
    case Group::t3: return "T-TRADE-3";
    case Group::t5: return "T-TRADE-5";
    case Group::prints: return "PRINTS";
  }
  return "unnamed";
}

enum class Check : std::uint8_t {
  // T-TRADE-1: the book against the ledger, per order and for the day.
  add_refused,
  add_symbol_mismatch,
  order_refused,
  order_mismatch,
  order_alive_at_zero,
  order_gone_early,
  remainder_mismatch,
  book_not_empty,
  ledger_not_empty,
  orders_unbalanced,
  shares_unbalanced,
  stream_incomplete,
  // E and C: the order is live, on this locate, and holds the printed size.
  e_not_live,
  e_wrong_locate,
  e_over_remaining,
  e_zero_shares,
  c_not_live,
  c_wrong_locate,
  c_over_remaining,
  c_zero_shares,
  c_bad_printable,
  c_price_changed,
  // T-TRADE-5.
  e_not_at_best,
  // P, Q, B and the rest: no book change, and what the print says of itself.
  p_reference_nonzero,
  p_side_not_b,
  p_book_changed,
  p_symbol_mismatch,
  q_book_changed,
  q_symbol_mismatch,
  q_unknown_cross_type,
  b_unknown_match,
  b_wrong_locate,
  b_broken_twice,
  b_book_changed,
  match_shared,
  match_leg_differs,
  other_book_changed,
  // C_N against Q, per cross and side.
  cn_over_cross,
  cn_price,
  cn_no_cross,
  // T-TRADE-2.
  t2_date,
  t2_total_row,
  t2_row_not_in_directory,
  t2_opening,
  t2_closing,
  t2_intraday,
  t2_outside_file,
  // T-TRADE-3.
  t3_row_not_in_directory,
  t3_row_count,
  t3_trade_shares,
  t3_hidden_shares,
  t3_trades,
  t3_hidden_trades,
  t3_odd_lots,
  t3_odd_lot_shares,
};

// A wire byte in a violation's text, quoted if it prints. libstdc++ 14 has no {:?}.
[[nodiscard]] inline std::string wire_char(char value) {
  const auto byte = static_cast<unsigned char>(value);
  return byte >= 0x20U && byte < 0x7FU ? std::format("'{}'", value) : std::format("0x{:02x}", byte);
}

inline constexpr std::size_t kCheckCount = static_cast<std::size_t>(Check::t3_odd_lot_shares) + 1;

struct CheckInfo {
  std::string_view name;
  Group group;
};

inline constexpr std::array<CheckInfo, kCheckCount> kChecks{{
    {.name = "add_refused", .group = Group::t1},
    {.name = "add_symbol_mismatch", .group = Group::t1},
    {.name = "order_refused", .group = Group::t1},
    {.name = "order_mismatch", .group = Group::t1},
    {.name = "order_alive_at_zero", .group = Group::t1},
    {.name = "order_gone_early", .group = Group::t1},
    {.name = "remainder_mismatch", .group = Group::t1},
    {.name = "book_not_empty", .group = Group::t1},
    {.name = "ledger_not_empty", .group = Group::t1},
    {.name = "orders_unbalanced", .group = Group::t1},
    {.name = "shares_unbalanced", .group = Group::t1},
    {.name = "stream_incomplete", .group = Group::t1},
    {.name = "e_not_live", .group = Group::prints},
    {.name = "e_wrong_locate", .group = Group::prints},
    {.name = "e_over_remaining", .group = Group::prints},
    {.name = "e_zero_shares", .group = Group::prints},
    {.name = "c_not_live", .group = Group::prints},
    {.name = "c_wrong_locate", .group = Group::prints},
    {.name = "c_over_remaining", .group = Group::prints},
    {.name = "c_zero_shares", .group = Group::prints},
    {.name = "c_bad_printable", .group = Group::prints},
    {.name = "c_price_changed", .group = Group::prints},
    {.name = "e_not_at_best", .group = Group::t5},
    {.name = "p_reference_nonzero", .group = Group::prints},
    {.name = "p_side_not_b", .group = Group::prints},
    {.name = "p_book_changed", .group = Group::prints},
    {.name = "p_symbol_mismatch", .group = Group::prints},
    {.name = "q_book_changed", .group = Group::prints},
    {.name = "q_symbol_mismatch", .group = Group::prints},
    {.name = "q_unknown_cross_type", .group = Group::prints},
    {.name = "b_unknown_match", .group = Group::prints},
    {.name = "b_wrong_locate", .group = Group::prints},
    {.name = "b_broken_twice", .group = Group::prints},
    {.name = "b_book_changed", .group = Group::prints},
    {.name = "match_shared", .group = Group::prints},
    {.name = "match_leg_differs", .group = Group::prints},
    {.name = "other_book_changed", .group = Group::prints},
    {.name = "cn_over_cross", .group = Group::prints},
    {.name = "cn_price", .group = Group::prints},
    {.name = "cn_no_cross", .group = Group::prints},
    {.name = "t2_date", .group = Group::t2},
    {.name = "t2_total_row", .group = Group::t2},
    {.name = "t2_row_not_in_directory", .group = Group::t2},
    {.name = "t2_opening", .group = Group::t2},
    {.name = "t2_closing", .group = Group::t2},
    {.name = "t2_intraday", .group = Group::t2},
    {.name = "t2_outside_file", .group = Group::t2},
    {.name = "t3_row_not_in_directory", .group = Group::t3},
    {.name = "t3_row_count", .group = Group::t3},
    {.name = "t3_trade_shares", .group = Group::t3},
    {.name = "t3_hidden_shares", .group = Group::t3},
    {.name = "t3_trades", .group = Group::t3},
    {.name = "t3_hidden_trades", .group = Group::t3},
    {.name = "t3_odd_lots", .group = Group::t3},
    {.name = "t3_odd_lot_shares", .group = Group::t3},
}};

[[nodiscard]] constexpr const CheckInfo& info_of(Check check) noexcept {
  return kChecks[static_cast<std::size_t>(check)];
}

// The time windows tried against MIDAS. Half open, in nanoseconds since midnight.
struct TimeWindow {
  std::string_view name;
  std::uint64_t start_ns;
  std::uint64_t end_ns;
};

inline constexpr std::uint64_t kHourNs = 3'600'000'000'000ULL;
inline constexpr std::uint64_t kMinuteNs = 60'000'000'000ULL;

inline constexpr std::array<TimeWindow, 4> kWindows{{
    {.name = "whole_day", .start_ns = 0, .end_ns = 24 * kHourNs},
    {.name = "0400_2000", .start_ns = 4 * kHourNs, .end_ns = 20 * kHourNs},
    {.name = "0930_1600", .start_ns = (9 * kHourNs) + (30 * kMinuteNs), .end_ns = 16 * kHourNs},
    {.name = "0935_1600", .start_ns = (9 * kHourNs) + (35 * kMinuteNs), .end_ns = 16 * kHourNs},
}};

// The window T-TRADE-3 is judged on.
inline constexpr std::size_t kMidasWindow = 3;

// What the odd-lot columns of MIDAS are compared with. The SEC's method page for its Market
// Activity Report: "Until Nov 3, 2025, a round lot is defined as 100 shares for all but
// three tickers. BRK.A and SEB have a round lot of one. BH has a round lot of 10." Its next
// sentence, "All trades involving nine or fewer shares are considered odd lot trades", adds
// nothing at a lot of 100 or 10. At a lot of one it is not what MIDAS does: SEB printed
// below 10 shares on both days and MIDAS gives it no odd lots. So an odd lot is a print
// below the lot. From that date the SEC's lots come in tiers that this file cannot state.
inline constexpr std::uint32_t kSecRuleEndDate = 20251103;  // First date it does not cover.
inline constexpr std::uint32_t kSecDefaultLot = 100;

struct SecLot {
  std::string_view symbol;
  std::uint32_t lot;
};

// BRK.A is spelt as BRK.B is in both the feed and MIDAS. The Nasdaq directory does not list
// it on the days held here and MIDAS has no Nasdaq row for it, so its entry cannot fire.
inline constexpr std::array<SecLot, 3> kSecLots{{
    {.symbol = "BRK.A", .lot = 1},
    {.symbol = "SEB", .lot = 1},
    {.symbol = "BH", .lot = 10},
}};

[[nodiscard]] constexpr std::uint32_t sec_round_lot(std::string_view symbol) noexcept {
  for (const SecLot& entry : kSecLots) {
    if (entry.symbol == symbol) {
      return entry.lot;
    }
  }
  return kSecDefaultLot;
}

// What became of the odd-lot columns. Later dates are never compared on the old rule.
enum class OddLotColumns : std::uint8_t { compared, not_compared_rule_ended };

[[nodiscard]] constexpr std::string_view to_string(OddLotColumns columns) noexcept {
  return columns == OddLotColumns::compared ? "compared" : "not_compared_rule_ended";
}

[[nodiscard]] constexpr OddLotColumns odd_lot_columns_on(std::uint32_t date) noexcept {
  return date < kSecRuleEndDate ? OddLotColumns::compared : OddLotColumns::not_compared_rule_ended;
}

// The classes of print. Each is counted, whether or not a check applies to it.
enum class PrintClass : std::uint8_t { e, c_y, c_n, p, q_o, q_c, q_h, q_i };

inline constexpr std::array<std::string_view, 8> kClassNames{"E",   "C_Y", "C_N", "P",
                                                             "Q_O", "Q_C", "Q_H", "Q_I"};

struct ClassTally {
  std::uint64_t count = 0;
  std::uint64_t shares = 0;
  std::uint64_t zero_share = 0;
  std::uint64_t test_count = 0;
  std::uint64_t test_shares = 0;
};

// What one symbol traded, by class, for the whole day and inside each window.
struct WindowVolume {
  std::uint64_t e_shares = 0;
  std::uint64_t c_y_shares = 0;
  std::uint64_t p_shares = 0;
  std::uint64_t prints = 0;
  std::uint64_t p_prints = 0;
  std::uint64_t odd_lot_prints = 0;
  std::uint64_t odd_lot_shares = 0;
  // Reported beside MIDAS, for information. The check uses the SEC's lot.
  std::uint64_t below_directory_prints = 0;
  std::uint64_t below_directory_shares = 0;
  std::uint64_t below_100_prints = 0;
  std::uint64_t below_100_shares = 0;
  std::uint64_t below_10_prints = 0;
  std::uint64_t below_10_shares = 0;

  [[nodiscard]] std::uint64_t total() const noexcept { return e_shares + c_y_shares + p_shares; }
};

struct SymbolVolume {
  std::array<WindowVolume, kWindows.size()> windows{};
  std::array<std::uint64_t, kClassNames.size()> shares{};
  std::uint64_t broken_shares = 0;
};

// One symbol's directory entry, from R.
struct Listing {
  Stock stock{' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '};
  bool listed = false;
  bool test = false;
  std::uint32_t directory_lot = 100;  // What the feed says. Reported, and not the check.
  std::uint32_t sec_lot = kSecDefaultLot;
};

// One print, kept so that a B can find it and match numbers can be compared.
struct MatchRecord {
  std::uint64_t match = 0;
  std::uint64_t shares = 0;
  std::uint64_t timestamp_ns = 0;
  Price4 price = 0;
  std::uint16_t locate = 0;
  PrintClass print_class = PrintClass::e;
  char side = ' ';  // The resting order's side, for E and C. P says B, and Q has none.
  bool broken = false;
};

struct RunConfig {
  BookCapacity capacity;
  std::size_t detail_limit = 20;  // Per check. All are counted either way.
};

template <class Book>
  requires TradeBook<Book>
class TradeRun {
 public:
  explicit TradeRun(const RunConfig& config)
      : config_{config},
        book_{config.capacity},
        listings_(65536),
        volumes_(65536),
        states_(65536, kNoAction) {
    ledger_.reserve(std::size_t{2} * config.capacity.orders);
    matches_.reserve(std::size_t{1} << 24U);
  }

  void on_message(std::span<const std::byte> payload) {
    ++message_;
    last_event_.reset();
    const std::byte* m = payload.data();
    switch (read_char(m)) {
      case AddOrder::kType: on_add(AddOrder::from_wire(m), payload); break;
      case AddOrderAttributed::kType: on_add(AddOrderAttributed::from_wire(m), payload); break;
      case OrderExecuted::kType: on_execute(OrderExecuted::from_wire(m), payload); break;
      case OrderExecutedWithPrice::kType:
        on_execute_with_price(OrderExecutedWithPrice::from_wire(m), payload);
        break;
      case OrderCancel::kType: on_cancel(OrderCancel::from_wire(m), payload); break;
      case OrderDelete::kType: on_delete(OrderDelete::from_wire(m), payload); break;
      case OrderReplace::kType: on_replace(OrderReplace::from_wire(m), payload); break;
      case Trade::kType: on_trade(Trade::from_wire(m), payload); break;
      case CrossTrade::kType: on_cross(CrossTrade::from_wire(m), payload); break;
      case BrokenTrade::kType: on_broken(BrokenTrade::from_wire(m), payload); break;
      case SystemEvent::kType: on_system(SystemEvent::from_wire(m), payload); break;
      case StockDirectory::kType: on_directory(StockDirectory::from_wire(m), payload); break;
      case StockTradingAction::kType:
        on_trading_action(StockTradingAction::from_wire(m), payload);
        break;
      default: apply_expecting_no_change(payload, Check::other_book_changed); break;
    }
  }

  void note_malformed() { fail(Check::stream_incomplete, 0, "a malformed frame"); }

  // End of the stream. Whole-day identities, match numbers and the C_N sums.
  void finish(bool stream_ended_cleanly) {
    if (!stream_ended_cleanly || last_event_ != 'C') {
      fail(Check::stream_incomplete, 0, "the stream did not end cleanly on S/C");
    }
    check_lifecycle_totals();
    check_matches();
  }

  void compare_cross_stats(const CrossStatsTable& table, std::uint32_t date) {
    cross_compared_ = true;
    if (table.date != date) {
      fail(Check::t2_date, 0, std::format("file date {}, wanted {}", table.date, date));
    }
    std::uint64_t file_opening = 0;
    std::uint64_t file_closing = 0;
    std::uint64_t file_intraday = 0;
    for (const CrossStatsRow& row : table.rows) {
      file_opening += row.opening;
      file_closing += row.closing;
      file_intraday += row.intraday;
      compare_cross_row(row);
    }
    if (file_opening != table.total_opening || file_closing != table.total_closing ||
        file_intraday != table.total_intraday) {
      fail(Check::t2_total_row, 0,
           std::format("TOTAL row {}/{}/{}, rows sum to {}/{}/{}", table.total_opening,
                       table.total_closing, table.total_intraday, file_opening, file_closing,
                       file_intraday));
    }

    // Q shares the file cannot hold. Test symbols are counted apart, so they are left out
    // of both sides. What remains has to be exactly the halt crosses.
    std::uint64_t feed_q = 0;
    for (const auto print_class :
         {PrintClass::q_o, PrintClass::q_c, PrintClass::q_h, PrintClass::q_i}) {
      const ClassTally& tally = class_tally(print_class);
      feed_q += tally.shares - tally.test_shares;
    }
    const ClassTally& halts = class_tally(PrintClass::q_h);
    const std::uint64_t halt_sum = halts.shares - halts.test_shares;
    const std::uint64_t file_sum = file_opening + file_closing + file_intraday;
    outside_file_ = feed_q - file_sum;
    if (feed_q != file_sum + halt_sum) {
      fail(Check::t2_outside_file, 0,
           std::format("Q {} less the file's {} is {}, type H sums to {}", feed_q, file_sum,
                       outside_file_, halt_sum));
    }
    file_rows_ = table.rows.size();
    file_opening_ = file_opening;
    file_closing_ = file_closing;
  }

  void compare_midas(const MidasTable& table, std::optional<std::uint64_t> expected_rows,
                     std::uint32_t date) {
    midas_compared_ = true;
    odd_lot_columns_ = odd_lot_columns_on(date);
    midas_rows_ = table.rows.size();
    midas_rows_in_file_ = table.rows_in_file;
    if (expected_rows && *expected_rows != table.rows.size()) {
      fail(Check::t3_row_count, 0,
           std::format("{} rows, pinned at {}", table.rows.size(), *expected_rows));
    }
    build_symbol_index();

    std::map<Stock, MidasRow> merged;
    for (const MidasRow& row : table.rows) {
      const auto [at, fresh] = merged.try_emplace(row.symbol, row);
      if (!fresh) {
        ++midas_second_rows_;
        at->second.trades += row.trades;
        at->second.hidden_trades += row.hidden_trades;
        at->second.odd_lots += row.odd_lots;
        at->second.trade_shares += row.trade_shares;
        at->second.hidden_shares += row.hidden_shares;
        at->second.odd_lot_shares += row.odd_lot_shares;
      }
    }

    std::vector<std::uint8_t> covered(listings_.size(), 0);
    for (const auto& [symbol, row] : merged) {
      midas_trade_shares_ += row.trade_shares;
      midas_hidden_shares_ += row.hidden_shares;
      midas_trades_ += row.trades;
      midas_hidden_trades_ += row.hidden_trades;
      const auto found = locate_of_.find(symbol);
      if (found == locate_of_.end()) {
        fail(Check::t3_row_not_in_directory, 0, symbol_view(symbol));
        continue;
      }
      covered[found->second] = 1;
      compare_midas_row(row, found->second);
    }
    tally_outside_midas(covered);
    find_window_edges(covered);
  }

  void print_report(std::FILE* out = stdout) const {
    print_classes(out);
    print_lifecycle(out);
    print_best_price(out);
    print_c_prices(out);
    print_matches(out);
    print_cross_stats(out);
    print_midas(out);
    print_checks(out);
  }

  // One line per symbol that printed anything, for the raw log.
  void write_symbols(std::FILE* out) const {
    for (std::size_t locate = 0; locate < listings_.size(); ++locate) {
      const Listing& listing = listings_[locate];
      const SymbolVolume& volume = volumes_[locate];
      const bool printed = std::ranges::any_of(volume.shares, [](auto s) { return s != 0; });
      if (!listing.listed && !printed) {
        continue;
      }
      std::print(out, "symbol {} locate={} test={}", symbol_view(listing.stock), locate,
                 listing.test ? 1 : 0);
      for (std::size_t c = 0; c < kClassNames.size(); ++c) {
        std::print(out, " {}={}", kClassNames[c], volume.shares[c]);
      }
      for (std::size_t w = 0; w < kWindows.size(); ++w) {
        std::print(out, " {}={}/{}", kWindows[w].name, volume.windows[w].total(),
                   volume.windows[w].p_shares);
      }
      std::println(out, " broken={}", volume.broken_shares);
    }
  }

  [[nodiscard]] std::uint64_t violations(Group group) const noexcept {
    std::uint64_t total = 0;
    for (std::size_t i = 0; i < kCheckCount; ++i) {
      total += kChecks[i].group == group ? counts_[i] : 0;
    }
    return total;
  }
  [[nodiscard]] std::uint64_t violations(Check check) const noexcept {
    return counts_[static_cast<std::size_t>(check)];
  }
  [[nodiscard]] bool cross_compared() const noexcept { return cross_compared_; }
  [[nodiscard]] bool midas_compared() const noexcept { return midas_compared_; }
  [[nodiscard]] std::uint64_t messages() const noexcept { return message_; }

 private:
  static constexpr char kNoAction = '-';
  static constexpr std::size_t kNoPrintable = 2;

  // The ledger's whole record of a live order. It holds no remainder.
  struct LedgerOrder {
    std::uint32_t added = 0;
    std::uint32_t deducted = 0;
    Price4 price = 0;
    std::uint16_t locate = 0;
    Side side = Side::bid;
  };

  // How an order's remaining shares reached the book's view of it.
  enum class Closer : std::uint8_t { execute, cancel };

  struct BookView {
    TopOfBook bid;
    TopOfBook ask;
    std::uint64_t live;
    std::uint64_t writes;

    friend bool operator==(const BookView&, const BookView&) = default;
  };

  struct FarLegs {
    std::uint64_t count = 0;
    std::uint64_t shares = 0;
    std::uint64_t longest_lag_ns = 0;
  };

  struct PriceSplit {
    std::uint64_t equal = 0;
    std::uint64_t different = 0;
    std::uint64_t equal_shares = 0;
    std::uint64_t different_shares = 0;
  };

  [[nodiscard]] static char side_byte(Side side) noexcept { return side == Side::bid ? 'B' : 'S'; }

  [[nodiscard]] static std::string name_of(const Stock& stock) {
    return std::string{symbol_view(stock)};
  }

  [[nodiscard]] ClassTally& class_tally(PrintClass print_class) noexcept {
    return classes_[static_cast<std::size_t>(print_class)];
  }
  [[nodiscard]] const ClassTally& class_tally(PrintClass print_class) const noexcept {
    return classes_[static_cast<std::size_t>(print_class)];
  }

  void fail(Check check, std::uint16_t locate, std::string_view detail) {
    const auto at = static_cast<std::size_t>(check);
    ++counts_[at];
    if (counts_[at] <= config_.detail_limit) {
      std::println("violation {} message={} locate={} symbol={} {}", kChecks[at].name, message_,
                   locate, symbol_view(listings_[locate].stock), detail);
    }
  }

  [[nodiscard]] BookView view_of(std::uint16_t locate) {
    return {.bid = book_.best(locate, Side::bid),
            .ask = book_.best(locate, Side::ask),
            .live = book_.live_orders(),
            .writes = book_.observer().writes()};
  }

  // A message that must leave the book as it found it.
  void apply_expecting_no_change(std::span<const std::byte> payload, Check check) {
    const std::uint16_t locate = read_header(payload.data()).stock_locate;
    const BookView before = view_of(locate);
    const ApplyStatus status = book_.apply(payload);
    if (status != ApplyStatus::no_book_effect || view_of(locate) != before) {
      fail(check, locate, std::format("status {}", to_string(status)));
    }
  }

  [[nodiscard]] bool symbol_differs(std::uint16_t locate, const Stock& stock) const noexcept {
    return listings_[locate].stock != stock;
  }

  void account_print(PrintClass print_class, std::uint16_t locate, std::uint64_t shares,
                     std::uint64_t timestamp_ns, std::uint64_t match, Price4 price, char side) {
    ClassTally& tally = class_tally(print_class);
    ++tally.count;
    tally.shares += shares;
    tally.zero_share += shares == 0 ? 1U : 0U;
    if (listings_[locate].test) {
      ++tally.test_count;
      tally.test_shares += shares;
    }

    SymbolVolume& volume = volumes_[locate];
    volume.shares[static_cast<std::size_t>(print_class)] += shares;
    if (print_class == PrintClass::e || print_class == PrintClass::c_y ||
        print_class == PrintClass::p) {
      for (std::size_t w = 0; w < kWindows.size(); ++w) {
        if (in_window(kWindows[w], timestamp_ns)) {
          add_to_window(volume.windows[w], print_class, shares, listings_[locate]);
        }
      }
    }
    matches_.push_back({.match = match,
                        .shares = shares,
                        .timestamp_ns = timestamp_ns,
                        .price = price,
                        .locate = locate,
                        .print_class = print_class,
                        .side = side,
                        .broken = false});
  }

  [[nodiscard]] static bool in_window(const TimeWindow& window, std::uint64_t ns) noexcept {
    return ns >= window.start_ns && ns < window.end_ns;
  }

  static void add_to_window(WindowVolume& window, PrintClass print_class, std::uint64_t shares,
                            const Listing& listing) {
    switch (print_class) {
      case PrintClass::e: window.e_shares += shares; break;
      case PrintClass::c_y: window.c_y_shares += shares; break;
      default:
        window.p_shares += shares;
        ++window.p_prints;
        break;
    }
    ++window.prints;
    if (shares < listing.sec_lot) {
      ++window.odd_lot_prints;
      window.odd_lot_shares += shares;
    }
    if (shares < listing.directory_lot) {
      ++window.below_directory_prints;
      window.below_directory_shares += shares;
    }
    if (shares < 100) {
      ++window.below_100_prints;
      window.below_100_shares += shares;
    }
    if (shares < 10) {
      ++window.below_10_prints;
      window.below_10_shares += shares;
    }
  }

  // The book against the ledger, after a message that changed this order.
  void compare_with_ledger(std::uint64_t reference, const LedgerOrder& entry) {
    const std::uint32_t remaining = entry.added - entry.deducted;
    const std::optional<RestingOrder> order = book_.resting_order(reference);
    if (remaining == 0) {
      if (order) {
        fail(Check::order_alive_at_zero, entry.locate,
             std::format("reference {} holds {} after its last share", reference, order->shares));
      }
      return;
    }
    if (!order) {
      fail(Check::order_gone_early, entry.locate,
           std::format("reference {} is gone with {} left", reference, remaining));
      return;
    }
    if (order->shares != remaining || order->price != entry.price || order->side != entry.side ||
        order->locate != entry.locate) {
      fail(Check::order_mismatch, entry.locate,
           std::format("reference {}: book {} {} @{} locate {}, ledger {} {} @{} locate {}",
                       reference, side_byte(order->side), order->shares, order->price,
                       order->locate, side_byte(entry.side), remaining, entry.price, entry.locate));
    }
  }

  void open_in_ledger(std::uint64_t reference, const LedgerOrder& entry) {
    ledger_[reference] = entry;
    ++orders_opened_;
    shares_added_ += entry.added;
    compare_with_ledger(reference, entry);
  }

  void on_system(const SystemEvent& m, std::span<const std::byte> payload) {
    apply_expecting_no_change(payload, Check::other_book_changed);
    last_event_ = m.event_code;
    if (m.event_code == 'Q') {
      market_open_ = true;
    } else if (m.event_code == 'M') {
      market_open_ = false;
    } else if (m.event_code == 'C') {
      check_book_empty();
    }
  }

  void on_directory(const StockDirectory& m, std::span<const std::byte> payload) {
    apply_expecting_no_change(payload, Check::other_book_changed);
    Listing& listing = listings_[m.header.stock_locate];
    listing.stock = m.stock;
    listing.listed = true;
    listing.test = m.authenticity == 'T';
    listing.directory_lot = m.round_lot_size;
    listing.sec_lot = sec_round_lot(symbol_view(m.stock));
  }

  void on_trading_action(const StockTradingAction& m, std::span<const std::byte> payload) {
    apply_expecting_no_change(payload, Check::other_book_changed);
    states_[m.header.stock_locate] = m.trading_state;
  }

  template <class Add>
  void on_add(const Add& m, std::span<const std::byte> payload) {
    const std::uint16_t locate = m.header.stock_locate;
    if (symbol_differs(locate, m.stock)) {
      fail(Check::add_symbol_mismatch, locate, symbol_view(m.stock));
    }
    const ApplyStatus status = book_.apply(payload);
    if (status != ApplyStatus::applied) {
      fail(Check::add_refused, locate,
           std::format("reference {}: {}", m.order_reference, to_string(status)));
      return;
    }
    open_in_ledger(m.order_reference, {.added = m.shares,
                                       .deducted = 0,
                                       .price = m.price,
                                       .locate = locate,
                                       .side = m.side == 'B' ? Side::bid : Side::ask});
  }

  // The checks E and C share. What the print found, or nothing if it hit no order.
  [[nodiscard]] std::optional<RestingOrder> find_hit(std::uint16_t locate, std::uint64_t reference,
                                                     std::uint32_t shares, Check not_live,
                                                     Check wrong_locate, Check over_remaining,
                                                     Check zero_shares) {
    const std::optional<RestingOrder> order = book_.resting_order(reference);
    if (!order) {
      fail(not_live, locate, std::format("reference {}", reference));
      return std::nullopt;
    }
    if (order->locate != locate) {
      fail(wrong_locate, locate, std::format("reference {} rests on {}", reference, order->locate));
      return std::nullopt;
    }
    if (shares > order->shares) {
      fail(over_remaining, locate,
           std::format("reference {}: printed {}, remaining {}", reference, shares, order->shares));
      return std::nullopt;
    }
    if (shares == 0) {
      fail(zero_shares, locate, std::format("reference {}", reference));
    }
    return order;
  }

  // Applies a deduction the book should take, and checks it against the ledger.
  void apply_deduction(std::span<const std::byte> payload, std::uint16_t locate,
                       std::uint64_t reference, std::uint32_t shares, Closer closer) {
    const ApplyStatus status = book_.apply(payload);
    if (status != ApplyStatus::applied) {
      fail(Check::order_refused, locate,
           std::format("reference {}: {}", reference, to_string(status)));
      return;
    }
    const auto found = ledger_.find(reference);
    if (found == ledger_.end()) {
      fail(Check::order_mismatch, locate,
           std::format("reference {}: not in the ledger", reference));
      return;
    }
    LedgerOrder& entry = found->second;
    if (shares > entry.added - entry.deducted) {
      fail(Check::order_mismatch, locate,
           std::format("reference {}: {} from {} left in the ledger", reference, shares,
                       entry.added - entry.deducted));
      return;
    }
    entry.deducted += shares;
    (closer == Closer::execute ? shares_executed_ : shares_cancelled_) += shares;
    compare_with_ledger(reference, entry);
    if (entry.added == entry.deducted) {
      ++(closer == Closer::execute ? orders_ran_out_on_execute_ : orders_ran_out_on_cancel_);
      ledger_.erase(found);
    }
  }

  void on_execute(const OrderExecuted& m, std::span<const std::byte> payload) {
    const std::uint16_t locate = m.header.stock_locate;
    const std::optional<RestingOrder> order =
        find_hit(locate, m.order_reference, m.executed_shares, Check::e_not_live,
                 Check::e_wrong_locate, Check::e_over_remaining, Check::e_zero_shares);
    if (!order) {
      return;
    }
    check_best_price(*order, locate);
    apply_deduction(payload, locate, m.order_reference, m.executed_shares, Closer::execute);
    account_print(PrintClass::e, locate, m.executed_shares, m.header.timestamp_ns, m.match_number,
                  order->price, side_byte(order->side));
  }

  // T-TRADE-5, before the print takes the order away. Every E is counted by where it fell.
  void check_best_price(const RestingOrder& order, std::uint16_t locate) {
    const char state = states_[locate];
    const TopOfBook best = book_.best(locate, order.side);
    const bool at_best = best.present && best.price == order.price;
    if (!market_open_ || state != 'T') {
      ++outside_population_[{market_open_, state}];
      outside_at_best_ += at_best ? 1U : 0U;
      return;
    }
    ++in_population_;
    at_best_ += at_best ? 1U : 0U;
    at_front_ += at_best && order.at_front ? 1U : 0U;
    if (!at_best) {
      fail(Check::e_not_at_best, locate,
           std::format("order at {}, best {}", order.price, best.price));
    }
  }

  void on_execute_with_price(const OrderExecutedWithPrice& m, std::span<const std::byte> payload) {
    const std::uint16_t locate = m.header.stock_locate;
    const std::optional<RestingOrder> order =
        find_hit(locate, m.order_reference, m.executed_shares, Check::c_not_live,
                 Check::c_wrong_locate, Check::c_over_remaining, Check::c_zero_shares);
    if (!order) {
      return;
    }
    if (m.printable != 'Y' && m.printable != 'N') {
      fail(Check::c_bad_printable, locate, std::format("printable {}", wire_char(m.printable)));
    }
    const std::size_t printable = m.printable == 'Y' ? 0 : 1;
    PriceSplit& split = c_prices_[printable];
    const bool same = m.execution_price == order->price;
    (same ? split.equal : split.different) += 1;
    (same ? split.equal_shares : split.different_shares) += m.executed_shares;

    apply_deduction(payload, locate, m.order_reference, m.executed_shares, Closer::execute);
    const std::optional<RestingOrder> after = book_.resting_order(m.order_reference);
    if (after && after->price != order->price) {
      fail(Check::c_price_changed, locate,
           std::format("reference {}: price {} became {}", m.order_reference, order->price,
                       after->price));
    }
    account_print(m.printable == 'Y' ? PrintClass::c_y : PrintClass::c_n, locate, m.executed_shares,
                  m.header.timestamp_ns, m.match_number, m.execution_price, side_byte(order->side));
  }

  void on_cancel(const OrderCancel& m, std::span<const std::byte> payload) {
    ++cancels_;
    apply_deduction(payload, m.header.stock_locate, m.order_reference, m.cancelled_shares,
                    Closer::cancel);
  }

  // What an order still held, as the book says, against the ledger's own arithmetic.
  void close_with_remainder(std::uint64_t reference, std::uint16_t locate, std::uint32_t remainder,
                            const LedgerOrder& entry) {
    if (entry.added != entry.deducted + remainder) {
      fail(Check::remainder_mismatch, locate,
           std::format("reference {}: added {}, deducted {}, book holds {}", reference, entry.added,
                       entry.deducted, remainder));
    }
  }

  void on_delete(const OrderDelete& m, std::span<const std::byte> payload) {
    const std::uint16_t locate = m.header.stock_locate;
    const std::optional<RestingOrder> order = book_.resting_order(m.order_reference);
    const ApplyStatus status = book_.apply(payload);
    const auto found = ledger_.find(m.order_reference);
    if (status != ApplyStatus::applied || !order || found == ledger_.end()) {
      fail(Check::order_refused, locate,
           std::format("D reference {}: {}", m.order_reference, to_string(status)));
      return;
    }
    close_with_remainder(m.order_reference, locate, order->shares, found->second);
    shares_deleted_ += order->shares;
    ++orders_deleted_;
    ledger_.erase(found);
    if (book_.resting_order(m.order_reference)) {
      fail(Check::order_alive_at_zero, locate,
           std::format("reference {} survived its D", m.order_reference));
    }
  }

  void on_replace(const OrderReplace& m, std::span<const std::byte> payload) {
    const std::uint16_t locate = m.header.stock_locate;
    const std::optional<RestingOrder> order = book_.resting_order(m.original_order_reference);
    const ApplyStatus status = book_.apply(payload);
    const auto found = ledger_.find(m.original_order_reference);
    if (status != ApplyStatus::applied || !order || found == ledger_.end()) {
      fail(Check::order_refused, locate,
           std::format("U reference {}: {}", m.original_order_reference, to_string(status)));
      return;
    }
    close_with_remainder(m.original_order_reference, locate, order->shares, found->second);
    const LedgerOrder replacement{.added = m.shares,
                                  .deducted = 0,
                                  .price = m.price,
                                  .locate = found->second.locate,
                                  .side = found->second.side};
    shares_replaced_out_ += order->shares;
    ++orders_replaced_;
    ledger_.erase(found);
    if (book_.resting_order(m.original_order_reference)) {
      fail(Check::order_alive_at_zero, locate,
           std::format("reference {} survived its U", m.original_order_reference));
    }
    open_in_ledger(m.new_order_reference, replacement);
  }

  void on_trade(const Trade& m, std::span<const std::byte> payload) {
    const std::uint16_t locate = m.header.stock_locate;
    if (m.order_reference != 0) {
      fail(Check::p_reference_nonzero, locate, std::format("reference {}", m.order_reference));
    }
    if (m.side != 'B') {
      fail(Check::p_side_not_b, locate, std::format("side {}", wire_char(m.side)));
    }
    if (symbol_differs(locate, m.stock)) {
      fail(Check::p_symbol_mismatch, locate, name_of(m.stock));
    }
    apply_expecting_no_change(payload, Check::p_book_changed);
    account_print(PrintClass::p, locate, m.shares, m.header.timestamp_ns, m.match_number, m.price,
                  m.side);
  }

  void on_cross(const CrossTrade& m, std::span<const std::byte> payload) {
    const std::uint16_t locate = m.header.stock_locate;
    if (symbol_differs(locate, m.stock)) {
      fail(Check::q_symbol_mismatch, locate, name_of(m.stock));
    }
    apply_expecting_no_change(payload, Check::q_book_changed);
    PrintClass print_class = PrintClass::q_o;
    switch (m.cross_type) {
      case 'O': print_class = PrintClass::q_o; break;
      case 'C': print_class = PrintClass::q_c; break;
      case 'H': print_class = PrintClass::q_h; break;
      case 'I': print_class = PrintClass::q_i; break;
      default:
        fail(Check::q_unknown_cross_type, locate,
             std::format("cross type {}", wire_char(m.cross_type)));
        return;
    }
    account_print(print_class, locate, m.shares, m.header.timestamp_ns, m.match_number,
                  m.cross_price, ' ');
  }

  // A B is rare, so a walk of the print log costs nothing that matters.
  void on_broken(const BrokenTrade& m, std::span<const std::byte> payload) {
    const std::uint16_t locate = m.header.stock_locate;
    ++breaks_;
    apply_expecting_no_change(payload, Check::b_book_changed);
    const auto found = std::ranges::find(matches_, m.match_number, &MatchRecord::match);
    if (found == matches_.end()) {
      fail(Check::b_unknown_match, locate, std::format("match {}", m.match_number));
      return;
    }
    if (found->locate != locate) {
      fail(Check::b_wrong_locate, locate,
           std::format("match {} was on locate {}", m.match_number, found->locate));
      return;
    }
    if (found->broken) {
      fail(Check::b_broken_twice, locate, std::format("match {}", m.match_number));
      return;
    }
    found->broken = true;
    volumes_[locate].broken_shares += found->shares;
    std::println("broken_trade match={} class={} symbol={} shares={} printed_at={}", m.match_number,
                 kClassNames[static_cast<std::size_t>(found->print_class)],
                 symbol_view(listings_[locate].stock), found->shares, found->timestamp_ns);
    ++breaks_matched_;
    broken_shares_ += found->shares;
  }

  void check_book_empty() {
    book_checked_empty_ = true;
    if (book_.live_orders() != 0) {
      fail(Check::book_not_empty, 0, std::format("{} live orders", book_.live_orders()));
    }
    if (!ledger_.empty()) {
      fail(Check::ledger_not_empty, 0, std::format("{} orders", ledger_.size()));
    }
  }

  // The per-order identity, summed, with the live remainder as the last term.
  void check_lifecycle_totals() {
    const std::uint64_t closed =
        orders_ran_out_on_execute_ + orders_ran_out_on_cancel_ + orders_deleted_ + orders_replaced_;
    if (orders_opened_ != closed + ledger_.size()) {
      fail(Check::orders_unbalanced, 0,
           std::format("{} opened, {} closed, {} live", orders_opened_, closed, ledger_.size()));
    }
    std::uint64_t live_shares = 0;
    for (const auto& [reference, entry] : ledger_) {
      live_shares += entry.added - entry.deducted;
    }
    const std::uint64_t left =
        shares_executed_ + shares_cancelled_ + shares_deleted_ + shares_replaced_out_ + live_shares;
    if (shares_added_ != left) {
      fail(Check::shares_unbalanced, 0,
           std::format("{} shares added, {} accounted for", shares_added_, left));
    }
  }

  [[nodiscard]] static bool is_cross(PrintClass print_class) noexcept {
    return print_class >= PrintClass::q_o;
  }

  // A match number names one trade, so two printed records must never share one. A C_N
  // may share with one print, and is then the far leg of that trade: same instant, symbol,
  // shares and price, on the other side where the print has a side. A C_N with a number
  // of its own has to belong to a cross, which check_cross_legs() settles.
  void check_matches() {
    std::ranges::stable_sort(matches_, std::ranges::less{}, &MatchRecord::match);
    std::vector<const MatchRecord*> cross_legs;
    for (auto at = matches_.begin(); at != matches_.end();) {
      const auto end = std::find_if(at, matches_.end(),
                                    [&](const MatchRecord& r) { return r.match != at->match; });
      const auto legs = std::ranges::count(at, end, PrintClass::c_n, &MatchRecord::print_class);
      if (end - at == 1) {
        if (legs == 1) {
          cross_legs.push_back(&*at);
        }
      } else if (end - at == 2 && legs == 1) {
        check_far_leg(at->print_class == PrintClass::c_n ? *at : *(at + 1),
                      at->print_class == PrintClass::c_n ? *(at + 1) : *at);
      } else {
        ++shared_match_numbers_;
        fail(Check::match_shared, at->locate,
             std::format("match {} on {} records", at->match, end - at));
      }
      at = end;
    }
    check_cross_legs(cross_legs);
  }

  void check_far_leg(const MatchRecord& leg, const MatchRecord& print) {
    ++far_legs_[static_cast<std::size_t>(print.print_class)].count;
    far_legs_[static_cast<std::size_t>(print.print_class)].shares += leg.shares;
    const bool sided = print.print_class != PrintClass::p;
    if (leg.timestamp_ns != print.timestamp_ns || leg.locate != print.locate ||
        leg.shares != print.shares || leg.price != print.price || is_cross(print.print_class) ||
        (sided && leg.side == print.side)) {
      fail(Check::match_leg_differs, leg.locate,
           std::format("match {}: C_N {} @{} {} at {}, {} {} @{} {} at {}", leg.match, leg.shares,
                       leg.price, leg.side, leg.timestamp_ns,
                       kClassNames[static_cast<std::size_t>(print.print_class)], print.shares,
                       print.price, print.side, print.timestamp_ns));
    }
  }

  // Each C_N with its own match number, against the last cross on its symbol that traded
  // shares, at or before it. It must carry that cross's price, and the C_N of one side
  // must not sum past the cross. The sum can fall short, because a cross also fills
  // interest ITCH never showed.
  void check_cross_legs(const std::vector<const MatchRecord*>& cross_legs) {
    std::map<std::uint16_t, std::vector<const MatchRecord*>> crosses;
    for (const MatchRecord& record : matches_) {
      if (is_cross(record.print_class) && record.shares != 0) {
        crosses[record.locate].push_back(&record);
      }
    }
    for (auto& [locate, list] : crosses) {
      std::ranges::sort(list, {}, [](const MatchRecord* r) { return r->timestamp_ns; });
    }

    std::map<std::pair<const MatchRecord*, char>, std::uint64_t> filled;
    for (const MatchRecord* leg : cross_legs) {
      const auto found = crosses.find(leg->locate);
      if (found == crosses.end()) {
        fail(Check::cn_no_cross, leg->locate, std::format("match {}", leg->match));
        continue;
      }
      const auto after =
          std::ranges::upper_bound(found->second, leg->timestamp_ns, {},
                                   [](const MatchRecord* r) { return r->timestamp_ns; });
      if (after == found->second.begin()) {
        fail(Check::cn_no_cross, leg->locate,
             std::format("match {} at {} precedes every cross", leg->match, leg->timestamp_ns));
        continue;
      }
      const MatchRecord& cross = **(after - 1);
      if (leg->price != cross.price) {
        fail(Check::cn_price, leg->locate,
             std::format("match {}: C_N @{}, cross @{}", leg->match, leg->price, cross.price));
      }
      filled[{&cross, leg->side}] += leg->shares;
      FarLegs& tally = cross_legs_[static_cast<std::size_t>(cross.print_class)];
      ++tally.count;
      tally.shares += leg->shares;
      const std::uint64_t lag = leg->timestamp_ns - cross.timestamp_ns;
      tally.longest_lag_ns = lag > tally.longest_lag_ns ? lag : tally.longest_lag_ns;
    }
    for (const auto& [key, shares] : filled) {
      ++cross_sides_filled_;
      cross_sides_exact_ += shares == key.first->shares ? 1U : 0U;
      if (shares > key.first->shares) {
        fail(Check::cn_over_cross, key.first->locate,
             std::format("side {}: C_N {} over a cross of {}", key.second, shares,
                         key.first->shares));
      }
    }
  }

  void build_symbol_index() {
    if (!locate_of_.empty()) {
      return;
    }
    for (std::size_t locate = 0; locate < listings_.size(); ++locate) {
      if (listings_[locate].listed) {
        locate_of_[listings_[locate].stock] = static_cast<std::uint16_t>(locate);
      }
    }
  }

  void compare_cross_row(const CrossStatsRow& row) {
    build_symbol_index();
    const auto found = locate_of_.find(row.symbol);
    if (found == locate_of_.end()) {
      fail(Check::t2_row_not_in_directory, 0, name_of(row.symbol));
      return;
    }
    const std::uint16_t locate = found->second;
    const auto& shares = volumes_[locate].shares;
    const auto compare = [&](Check check, PrintClass print_class, std::uint64_t file) {
      const std::uint64_t feed = shares[static_cast<std::size_t>(print_class)];
      if (feed != file) {
        fail(check, locate, std::format("feed {}, file {}", feed, file));
      }
    };
    compare(Check::t2_opening, PrintClass::q_o, row.opening);
    compare(Check::t2_closing, PrintClass::q_c, row.closing);
    compare(Check::t2_intraday, PrintClass::q_i, row.intraday);
    ++cross_rows_compared_;
  }

  void compare_midas_row(const MidasRow& row, std::uint16_t locate) {
    const WindowVolume& pinned = volumes_[locate].windows[kMidasWindow];
    ++midas_symbols_compared_;
    if (pinned.total() != row.trade_shares) {
      fail(Check::t3_trade_shares, locate,
           std::format("feed {}, MIDAS {}", pinned.total(), row.trade_shares));
    }
    if (pinned.p_shares != row.hidden_shares) {
      fail(Check::t3_hidden_shares, locate,
           std::format("feed {}, MIDAS {}", pinned.p_shares, row.hidden_shares));
    }
    if (pinned.prints != row.trades) {
      fail(Check::t3_trades, locate, std::format("feed {}, MIDAS {}", pinned.prints, row.trades));
    }
    if (pinned.p_prints != row.hidden_trades) {
      fail(Check::t3_hidden_trades, locate,
           std::format("feed {}, MIDAS {}", pinned.p_prints, row.hidden_trades));
    }
    const bool odd_lots_compared = odd_lot_columns_ == OddLotColumns::compared;
    if (odd_lots_compared) {
      compare_odd_lots(row, locate, pinned);
    }
    if (listings_[locate].directory_lot != 100 || listings_[locate].sec_lot != kSecDefaultLot) {
      other_lots_.push_back(
          {.locate = locate, .odd_lots = row.odd_lots, .odd_lot_shares = row.odd_lot_shares});
    }
    fit_windows(row, locate, odd_lots_compared);
  }

  void compare_odd_lots(const MidasRow& row, std::uint16_t locate, const WindowVolume& pinned) {
    if (pinned.odd_lot_prints != row.odd_lots) {
      fail(Check::t3_odd_lots, locate,
           std::format("feed {}, MIDAS {}", pinned.odd_lot_prints, row.odd_lots));
    }
    if (pinned.odd_lot_shares != row.odd_lot_shares) {
      fail(Check::t3_odd_lot_shares, locate,
           std::format("feed {}, MIDAS {}", pinned.odd_lot_shares, row.odd_lot_shares));
    }
  }

  // How each window fares on this symbol. Reported, and only the pinned one is judged.
  void fit_windows(const MidasRow& row, std::uint16_t locate, bool odd_lots_compared) {
    for (std::size_t w = 0; w < kWindows.size(); ++w) {
      const WindowVolume& window = volumes_[locate].windows[w];
      WindowFit& fit = fits_[w];
      fit.equal_shares += window.total() == row.trade_shares ? 1U : 0U;
      fit.equal_hidden += window.p_shares == row.hidden_shares ? 1U : 0U;
      fit.equal_prints += window.prints == row.trades ? 1U : 0U;
      fit.equal_hidden_prints += window.p_prints == row.hidden_trades ? 1U : 0U;
      fit.equal_odd_lots += odd_lots_compared && window.odd_lot_prints == row.odd_lots ? 1U : 0U;
      fit.equal_odd += odd_lots_compared && window.odd_lot_shares == row.odd_lot_shares ? 1U : 0U;
      fit.below += window.total() < row.trade_shares ? 1U : 0U;
      fit.feed_shares += window.total();
      fit.feed_hidden += window.p_shares;
      fit.feed_prints += window.prints;
    }
  }

  void tally_outside_midas(const std::vector<std::uint8_t>& covered) {
    for (std::size_t locate = 0; locate < listings_.size(); ++locate) {
      if (!listings_[locate].listed || covered[locate] != 0) {
        continue;
      }
      const WindowVolume& window = volumes_[locate].windows[kMidasWindow];
      if (listings_[locate].test) {
        ++outside_midas_test_symbols_;
      } else {
        ++outside_midas_symbols_;
        outside_midas_shares_ += window.total();
      }
    }
  }

  // How tightly the prints pin the window: the prints either side of each bound, on
  // symbols MIDAS covers. Equality can say no more about a bound than this.
  void find_window_edges(const std::vector<std::uint8_t>& covered) {
    const TimeWindow& window = kWindows[kMidasWindow];
    for (const MatchRecord& record : matches_) {
      const bool counted = record.print_class == PrintClass::e ||
                           record.print_class == PrintClass::c_y ||
                           record.print_class == PrintClass::p;
      if (!counted || covered[record.locate] == 0) {
        continue;
      }
      const std::uint64_t at = record.timestamp_ns;
      if (at < window.start_ns) {
        edges_.last_before = std::max(edges_.last_before, at);
      } else if (at < window.end_ns) {
        edges_.first_inside = std::min(edges_.first_inside, at);
        edges_.last_inside = std::max(edges_.last_inside, at);
      } else {
        edges_.first_after = std::min(edges_.first_after, at);
      }
    }
  }

  void print_classes(std::FILE* out) const {
    for (std::size_t c = 0; c < kClassNames.size(); ++c) {
      const ClassTally& tally = classes_[c];
      std::println(out, "class {} count={} shares={} zero_share={} test_count={} test_shares={}",
                   kClassNames[c], tally.count, tally.shares, tally.zero_share, tally.test_count,
                   tally.test_shares);
    }
  }

  void print_lifecycle(std::FILE* out) const {
    std::println(out, "t1_orders_opened {}", orders_opened_);
    std::println(out, "t1_orders_ran_out_on_execute {}", orders_ran_out_on_execute_);
    std::println(out, "t1_orders_ran_out_on_cancel {}", orders_ran_out_on_cancel_);
    std::println(out, "t1_orders_deleted {}", orders_deleted_);
    std::println(out, "t1_orders_replaced {}", orders_replaced_);
    std::println(out, "t1_orders_live_end {}", ledger_.size());
    std::println(out, "t1_shares_added {}", shares_added_);
    std::println(out, "t1_shares_executed {}", shares_executed_);
    std::println(out, "t1_shares_cancelled {}", shares_cancelled_);
    std::println(out, "t1_shares_deleted {}", shares_deleted_);
    std::println(out, "t1_shares_replaced_out {}", shares_replaced_out_);
    std::println(out, "t1_book_checked_empty {}", book_checked_empty_ ? 1 : 0);
    std::println(out, "t1_cancels {}", cancels_);
  }

  void print_best_price(std::FILE* out) const {
    std::println(out, "t5_population {}", in_population_);
    std::println(out, "t5_at_best {}", at_best_);
    std::println(out, "t5_at_front {}", at_front_);
    std::println(out, "t5_outside_at_best {}", outside_at_best_);
    for (const auto& [key, count] : outside_population_) {
      std::println(out, "t5_outside market_open={} state={} count={}", key.first ? 1 : 0,
                   key.second, count);
    }
  }

  void print_c_prices(std::FILE* out) const {
    static constexpr std::array<std::string_view, 2> kNames{"Y", "N"};
    for (std::size_t i = 0; i < kNames.size(); ++i) {
      const PriceSplit& split = c_prices_[i];
      std::println(
          out, "c_price printable={} equal={} different={} equal_shares={} different_shares={}",
          kNames[i], split.equal, split.different, split.equal_shares, split.different_shares);
    }
  }

  void print_matches(std::FILE* out) const {
    std::println(out, "match_records {}", matches_.size());
    std::println(out, "match_shared_numbers {}", shared_match_numbers_);
    for (std::size_t c = 0; c < far_legs_.size(); ++c) {
      if (far_legs_[c].count != 0) {
        std::println(out, "cn_far_leg print={} count={} shares={}", kClassNames[c],
                     far_legs_[c].count, far_legs_[c].shares);
      }
    }
    for (std::size_t c = 0; c < cross_legs_.size(); ++c) {
      if (cross_legs_[c].count != 0) {
        std::println(out, "cn_cross_leg cross={} count={} shares={} longest_lag_ns={}",
                     kClassNames[c], cross_legs_[c].count, cross_legs_[c].shares,
                     cross_legs_[c].longest_lag_ns);
      }
    }
    std::println(out, "cn_cross_sides_filled {}", cross_sides_filled_);
    std::println(out, "cn_cross_sides_exact {}", cross_sides_exact_);
    std::println(out, "b_messages {}", breaks_);
    std::println(out, "b_matched {}", breaks_matched_);
    std::println(out, "b_shares {}", broken_shares_);
    std::println(out, "b_net_printed_shares {}", printed_shares() - broken_shares_);
  }

  // E, C_Y and P over the whole day, before any break.
  [[nodiscard]] std::uint64_t printed_shares() const noexcept {
    return class_tally(PrintClass::e).shares + class_tally(PrintClass::c_y).shares +
           class_tally(PrintClass::p).shares;
  }

  void print_cross_stats(std::FILE* out) const {
    if (!cross_compared_) {
      return;
    }
    std::println(out, "t2_file_rows {}", file_rows_);
    std::println(out, "t2_rows_compared {}", cross_rows_compared_);
    std::println(out, "t2_file_opening {}", file_opening_);
    std::println(out, "t2_file_closing {}", file_closing_);
    std::println(out, "t2_outside_file {}", outside_file_);
  }

  void print_midas(std::FILE* out) const {
    if (!midas_compared_) {
      return;
    }
    std::println(out, "t3_window {}", kWindows[kMidasWindow].name);
    std::println(out, "t3_odd_lot_columns {}", to_string(odd_lot_columns_));
    std::println(out, "t3_other_round_lots {}",
                 std::ranges::count_if(listings_, [](const Listing& l) {
                   return l.listed && l.directory_lot != 100;
                 }));
    print_round_lots(out);
    std::println(out, "t3_rows {}", midas_rows_);
    std::println(out, "t3_rows_in_file {}", midas_rows_in_file_);
    std::println(out, "t3_second_rows {}", midas_second_rows_);
    std::println(out, "t3_symbols_compared {}", midas_symbols_compared_);
    std::println(out, "t3_midas_trade_shares {}", midas_trade_shares_);
    std::println(out, "t3_midas_hidden_shares {}", midas_hidden_shares_);
    std::println(out, "t3_midas_trades {}", midas_trades_);
    std::println(out, "t3_midas_hidden_trades {}", midas_hidden_trades_);
    std::println(out, "t3_outside_symbols {}", outside_midas_symbols_);
    std::println(out, "t3_outside_shares {}", outside_midas_shares_);
    std::println(out, "t3_outside_test_symbols {}", outside_midas_test_symbols_);
    std::println(out, "t3_edge_start last_before={} first_inside={}", edges_.last_before,
                 edges_.first_inside);
    std::println(out, "t3_edge_end last_inside={} first_after={}", edges_.last_inside,
                 edges_.first_after);
    for (std::size_t w = 0; w < kWindows.size(); ++w) {
      const WindowFit& fit = fits_[w];
      std::println(out,
                   "t3_fit window={} equal_shares={} equal_hidden={} equal_prints={} "
                   "equal_hidden_prints={} equal_odd_lots={} equal_odd_shares={} feed_below={} "
                   "feed_shares={} feed_hidden={} feed_prints={}",
                   kWindows[w].name, fit.equal_shares, fit.equal_hidden, fit.equal_prints,
                   fit.equal_hidden_prints, fit.equal_odd_lots, fit.equal_odd, fit.below,
                   fit.feed_shares, fit.feed_hidden, fit.feed_prints);
    }
  }

  // A compared symbol that either lot sets apart, and the state of the SEC's three entries.
  void print_round_lots(std::FILE* out) const {
    const bool odd_lots_compared = odd_lot_columns_ == OddLotColumns::compared;
    for (const OtherLot& other : other_lots_) {
      const Listing& listing = listings_[other.locate];
      const WindowVolume& pinned = volumes_[other.locate].windows[kMidasWindow];
      const std::string under_sec =
          odd_lots_compared ? std::format("sec_lot={} feed_below_sec={}/{}", listing.sec_lot,
                                          pinned.odd_lot_prints, pinned.odd_lot_shares)
                            : std::string{"sec_lot=none feed_below_sec=none"};
      std::println(out,
                   "t3_round_lot symbol={} lot={} {} midas={}/{} feed_below_lot={}/{} "
                   "feed_below_100={}/{} feed_below_10={}/{}",
                   name_of(listing.stock), listing.directory_lot, under_sec, other.odd_lots,
                   other.odd_lot_shares, pinned.below_directory_prints,
                   pinned.below_directory_shares, pinned.below_100_prints, pinned.below_100_shares,
                   pinned.below_10_prints, pinned.below_10_shares);
    }
    for (const SecLot& entry : kSecLots) {
      const auto named = [&entry](const Listing& l) {
        return l.listed && symbol_view(l.stock) == entry.symbol;
      };
      const auto compared = [&](const OtherLot& other) { return named(listings_[other.locate]); };
      std::println(out, "t3_sec_lot symbol={} lot={} listed={} compared={}", entry.symbol,
                   entry.lot, std::ranges::any_of(listings_, named) ? 1 : 0,
                   std::ranges::any_of(other_lots_, compared) ? 1 : 0);
    }
  }

  void print_checks(std::FILE* out) const {
    for (std::size_t i = 0; i < kCheckCount; ++i) {
      std::println(out, "check {} group={} violations={}", kChecks[i].name,
                   to_string(kChecks[i].group), counts_[i]);
    }
  }

  struct WindowEdges {
    std::uint64_t last_before = 0;
    std::uint64_t first_inside = std::numeric_limits<std::uint64_t>::max();
    std::uint64_t last_inside = 0;
    std::uint64_t first_after = std::numeric_limits<std::uint64_t>::max();
  };

  // How one window fares against MIDAS, in symbols that agree.
  // A compared symbol with a lot other than 100 on either rule, and what MIDAS says of it.
  struct OtherLot {
    std::uint16_t locate = 0;
    std::uint64_t odd_lots = 0;
    std::uint64_t odd_lot_shares = 0;
  };

  struct WindowFit {
    std::uint64_t equal_shares = 0;
    std::uint64_t equal_hidden = 0;
    std::uint64_t equal_prints = 0;
    std::uint64_t equal_hidden_prints = 0;
    std::uint64_t equal_odd_lots = 0;
    std::uint64_t equal_odd = 0;
    std::uint64_t below = 0;
    std::uint64_t feed_shares = 0;
    std::uint64_t feed_hidden = 0;
    std::uint64_t feed_prints = 0;
  };

  RunConfig config_;
  Book book_;
  std::vector<Listing> listings_;
  std::vector<SymbolVolume> volumes_;
  std::vector<char> states_;
  std::unordered_map<std::uint64_t, LedgerOrder> ledger_;
  std::vector<MatchRecord> matches_;
  std::map<Stock, std::uint16_t> locate_of_;

  std::array<std::uint64_t, kCheckCount> counts_{};
  std::array<ClassTally, kClassNames.size()> classes_{};
  std::array<PriceSplit, 2> c_prices_{};
  std::array<WindowFit, kWindows.size()> fits_{};
  WindowEdges edges_;
  std::map<std::pair<bool, char>, std::uint64_t> outside_population_;
  std::array<FarLegs, kClassNames.size()> far_legs_{};
  std::array<FarLegs, kClassNames.size()> cross_legs_{};

  std::uint64_t message_ = 0;
  std::optional<char> last_event_;
  bool market_open_ = false;
  bool book_checked_empty_ = false;
  bool cross_compared_ = false;
  bool midas_compared_ = false;
  OddLotColumns odd_lot_columns_ = OddLotColumns::compared;

  std::uint64_t orders_opened_ = 0;
  std::uint64_t orders_ran_out_on_execute_ = 0;
  std::uint64_t orders_ran_out_on_cancel_ = 0;
  std::uint64_t orders_deleted_ = 0;
  std::uint64_t orders_replaced_ = 0;
  std::uint64_t shares_added_ = 0;
  std::uint64_t shares_executed_ = 0;
  std::uint64_t shares_cancelled_ = 0;
  std::uint64_t shares_deleted_ = 0;
  std::uint64_t shares_replaced_out_ = 0;
  std::uint64_t cancels_ = 0;

  std::uint64_t in_population_ = 0;
  std::uint64_t at_best_ = 0;
  std::uint64_t at_front_ = 0;
  std::uint64_t outside_at_best_ = 0;

  std::uint64_t breaks_ = 0;
  std::uint64_t breaks_matched_ = 0;
  std::uint64_t broken_shares_ = 0;
  std::uint64_t shared_match_numbers_ = 0;
  std::uint64_t cross_sides_filled_ = 0;
  std::uint64_t cross_sides_exact_ = 0;

  std::uint64_t file_rows_ = 0;
  std::uint64_t cross_rows_compared_ = 0;
  std::uint64_t file_opening_ = 0;
  std::uint64_t file_closing_ = 0;
  std::uint64_t outside_file_ = 0;

  std::uint64_t midas_rows_ = 0;
  std::uint64_t midas_rows_in_file_ = 0;
  std::uint64_t midas_second_rows_ = 0;
  std::uint64_t midas_symbols_compared_ = 0;
  std::vector<OtherLot> other_lots_;
  std::uint64_t midas_trade_shares_ = 0;
  std::uint64_t midas_hidden_shares_ = 0;
  std::uint64_t midas_trades_ = 0;
  std::uint64_t midas_hidden_trades_ = 0;
  std::uint64_t outside_midas_symbols_ = 0;
  std::uint64_t outside_midas_shares_ = 0;
  std::uint64_t outside_midas_test_symbols_ = 0;
};

}  // namespace itch::trades
