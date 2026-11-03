// Trading fees for one simulated fill, keyed by date, in integers.
//
// Every amount is in picodollars, 10^-12 of a dollar, which holds each rate in the tables
// below exactly: $0.0030 a share is 3,000,000,000, and Section 31's $20.70 per $1M on a
// notional of price × shares, with the price in ITCH's 10^-4 dollars, is 2,070 for each
// unit of that notional. A cost is positive and a credit negative. Nothing is rounded
// per fill: the SEC and FINRA assess on totals, and a sum of exact fills rounds once.
//
// The tier is the base tier on every day: "All other firms" to add, "All MPIDs" to
// remove. A tier needs a month's share of consolidated volume that one replayed day by a
// small participant cannot show, and in 2019 Nasdaq set it after the month ended, so
// assuming a better one would be lookahead (BENCHMARKS.md, "Fees for the report day").
// Rates are for displayed, non-retail, non-midpoint orders, which is all the backtester
// sends.
//
// Sources, as BENCHMARKS.md and the research evidence give them:
//   - Add and remove: Nasdaq's price list, Wayback captures of 2019-10-22 and 2019-11-16
//     for 2019-10-30, 2019-12-07 and 2020-02-01 for 2019-12-30, and 2026-06-11 and
//     2026-06-16 for 2026-06-12. Each pair agrees.
//   - Section 31: FR 2019-04800 ($20.70 per $1M from 2019-04-16), FR 2026-04233 ($20.60
//     per $1M from 2026-04-04).
//   - FINRA TAF: FR 2020-23141 ($0.000119 a share sold, at most $5.95 a trade, unchanged
//     since 2012), FR 2026-19393 ($0.000195, at most $9.79, in 2026). FINRA's own page
//     still showed $0.000166 in 2026; the SEC-published filing is used.
//   - CAT fee 2026-1: FR 2026-09599, $0.000001 an executed equivalent share from
//     2026-05-01. None was found for 2019.
// 2019-12-30 is the report day and its rates are VERIFIED in BENCHMARKS.md. The other two
// days carry the evidence's SUBAGENT status until a report uses them.
#pragma once

#include <array>
#include <cstdint>
#include <expected>
#include <string_view>

#include "itch/book_types.hpp"
#include "itch/messages.hpp"

namespace itch {

using Picodollars = std::int64_t;

// The consolidated tape a security trades on, which sets its add credit.
//
// The ITCH 5.0 spec (April 28, 2023, p. 5) gives Market Category as the listing market,
// and never names a tape: Q, G and S are Nasdaq's three tiers, N is NYSE, A is NYSE
// American, P is NYSE Arca, Z is BATS Z, V is IEX, and a space is "Not available". The
// tapes are defined by listing market in the SEC's Regulation NMS release of 2024 (89 FR
// 81693): "Tape A for securities listed on the New York Stock Exchange", "Tape B for
// securities listed on exchanges other than NYSE and Nasdaq", "Tape C for securities
// listed on Nasdaq". Together these give carteret's mapping. A space, and a code the spec
// does not list, such as the M seen in 2025 files (DS-64), have no tape.
enum class Tape : std::uint8_t { a, b, c, unknown };

[[nodiscard]] constexpr Tape tape_of(char market_category) noexcept {
  switch (market_category) {
    case 'Q':
    case 'G':
    case 'S': return Tape::c;
    case 'N': return Tape::a;
    case 'A':
    case 'P':
    case 'Z':
    case 'V': return Tape::b;
    default: return Tape::unknown;
  }
}

enum class Liquidity : std::uint8_t { maker, taker };

// One day's rates. Per-share rates apply at a price of $1 or more; below that, the
// exchange charges a share of dollar volume, held as picodollars per unit of price4 ×
// shares (1 unit is 10^-4 of a dollar, so 0.30% is 300,000).
struct FeeSchedule {
  std::uint32_t date;  // YYYYMMDD
  Picodollars remove_per_share;
  Picodollars add_credit_tape_ab;
  Picodollars add_credit_tape_c;
  Picodollars remove_under_dollar_per_notional;
  Picodollars add_credit_under_dollar_per_notional;
  // Outside regular hours, where a day's price list sets them apart. Zero means the
  // regular rate applies.
  Picodollars premarket_remove_under_dollar_per_notional;
  Picodollars premarket_add_credit_under_dollar_per_notional;
  Picodollars section31_per_notional;  // Sells only.
  Picodollars taf_per_share;           // Sells only.
  Picodollars taf_cap;                 // Per trade, which here is per fill.
  Picodollars cat_per_share;           // Every fill.
};

inline constexpr Price4 kOneDollar = 10'000;

inline constexpr std::array<FeeSchedule, 3> kFeeSchedules{{
    {.date = 20191030,
     .remove_per_share = 3'000'000'000,
     .add_credit_tape_ab = 2'000'000'000,
     .add_credit_tape_c = 1'500'000'000,
     .remove_under_dollar_per_notional = 300'000,
     .add_credit_under_dollar_per_notional = 0,
     .premarket_remove_under_dollar_per_notional = 0,
     .premarket_add_credit_under_dollar_per_notional = 0,
     .section31_per_notional = 2'070,
     .taf_per_share = 119'000'000,
     .taf_cap = 5'950'000'000'000,
     .cat_per_share = 0},
    {.date = 20191230,
     .remove_per_share = 3'000'000'000,
     .add_credit_tape_ab = 2'000'000'000,
     .add_credit_tape_c = 1'500'000'000,
     .remove_under_dollar_per_notional = 300'000,
     .add_credit_under_dollar_per_notional = 0,
     .premarket_remove_under_dollar_per_notional = 0,
     .premarket_add_credit_under_dollar_per_notional = 0,
     .section31_per_notional = 2'070,
     .taf_per_share = 119'000'000,
     .taf_cap = 5'950'000'000'000,
     .cat_per_share = 0},
    {.date = 20260612,
     .remove_per_share = 3'000'000'000,
     .add_credit_tape_ab = 1'800'000'000,
     .add_credit_tape_c = 1'300'000'000,
     .remove_under_dollar_per_notional = 300'000,
     .add_credit_under_dollar_per_notional = 0,
     .premarket_remove_under_dollar_per_notional = 150'000,
     .premarket_add_credit_under_dollar_per_notional = 50'000,
     .section31_per_notional = 2'060,
     .taf_per_share = 195'000'000,
     .taf_cap = 9'790'000'000'000,
     .cat_per_share = 1'000'000},
}};

// The schedule for a trading day, or nullptr for a day with no sourced rates. No day
// borrows another's: a run on an unlisted day has no fees to charge and is refused.
[[nodiscard]] constexpr const FeeSchedule* find_fee_schedule(std::uint32_t date) noexcept {
  for (const FeeSchedule& schedule : kFeeSchedules) {
    if (schedule.date == date) {
      return &schedule;
    }
  }
  return nullptr;
}

struct FillTerms {
  Tape tape;
  Liquidity liquidity;
  Side side;  // bid is a buy, ask a sell.
  Price4 price;
  std::uint32_t shares;
  bool regular_hours;
};

struct FeeBreakdown {
  Picodollars exchange;  // The remove fee, or the add credit as a negative.
  Picodollars section31;
  Picodollars taf;
  Picodollars cat;

  [[nodiscard]] constexpr Picodollars total() const noexcept {
    return exchange + section31 + taf + cat;
  }
  friend bool operator==(const FeeBreakdown&, const FeeBreakdown&) = default;
};

enum class FeeError : std::uint8_t { unknown_tape, overflow };

[[nodiscard]] constexpr std::string_view to_string(FeeError error) noexcept {
  switch (error) {
    case FeeError::unknown_tape: return "unknown_tape";
    case FeeError::overflow: return "overflow";
  }
  return "unnamed";
}

// The fees on one fill. Each part is computed in 128 bits and refused if it does not fit
// in 64, which no real fill comes near: 10^6 shares at $200,000 is 4.1 × 10^15.
[[nodiscard]] constexpr std::expected<FeeBreakdown, FeeError> fill_fees(
    const FeeSchedule& schedule, const FillTerms& fill) noexcept {
  if (fill.tape == Tape::unknown) {
    return std::unexpected(FeeError::unknown_tape);
  }
  using Wide = __int128;
  const Wide shares = fill.shares;
  const Wide notional = Wide{fill.price} * shares;
  Wide exchange = 0;
  if (fill.price >= kOneDollar) {
    if (fill.liquidity == Liquidity::taker) {
      exchange = schedule.remove_per_share * shares;
    } else {
      exchange =
          -(fill.tape == Tape::c ? schedule.add_credit_tape_c : schedule.add_credit_tape_ab) *
          shares;
    }
  } else {
    const bool premarket = !fill.regular_hours;
    if (fill.liquidity == Liquidity::taker) {
      const Picodollars rate = premarket && schedule.premarket_remove_under_dollar_per_notional != 0
                                   ? schedule.premarket_remove_under_dollar_per_notional
                                   : schedule.remove_under_dollar_per_notional;
      exchange = rate * notional;
    } else {
      const Picodollars rate =
          premarket && schedule.premarket_add_credit_under_dollar_per_notional != 0
              ? schedule.premarket_add_credit_under_dollar_per_notional
              : schedule.add_credit_under_dollar_per_notional;
      exchange = -(rate * notional);
    }
  }
  Wide section31 = 0;
  Wide taf = 0;
  if (fill.side == Side::ask) {
    section31 = schedule.section31_per_notional * notional;
    taf = schedule.taf_per_share * shares;
    taf = taf > schedule.taf_cap ? Wide{schedule.taf_cap} : taf;
  }
  const Wide cat = schedule.cat_per_share * shares;
  constexpr Wide kMax = Wide{INT64_MAX} / 4;
  for (const Wide part : {exchange, section31, taf, cat}) {
    if (part > kMax || part < -kMax) {
      return std::unexpected(FeeError::overflow);
    }
  }
  return FeeBreakdown{.exchange = static_cast<Picodollars>(exchange),
                      .section31 = static_cast<Picodollars>(section31),
                      .taf = static_cast<Picodollars>(taf),
                      .cat = static_cast<Picodollars>(cat)};
}

}  // namespace itch
