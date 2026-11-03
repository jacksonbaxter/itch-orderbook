// The fee schedule, scenarios 37 to 40 of the research evidence, and the tape mapping.
//
// Every expected amount is worked by hand from the rates in BENCHMARKS.md and the
// evidence's fee table, in picodollars (10^-12 of a dollar), and written out here. None is
// computed from the table under test.
#include <gtest/gtest.h>

#include <cstdint>

#include "itch/book_types.hpp"
#include "itch/fees.hpp"

namespace {

using itch::FeeBreakdown;
using itch::FeeError;
using itch::FillTerms;
using itch::Liquidity;
using itch::Side;
using itch::Tape;

constexpr itch::Price4 kFifty = 500'000;     // $50.00
constexpr itch::Price4 kHalfDollar = 5'000;  // $0.50

FeeBreakdown fees(std::uint32_t date, const FillTerms& fill) {
  const itch::FeeSchedule* schedule = itch::find_fee_schedule(date);
  EXPECT_NE(schedule, nullptr) << date;
  if (schedule == nullptr) {
    return {};
  }
  const auto result = itch::fill_fees(*schedule, fill);
  EXPECT_TRUE(result.has_value()) << date;
  return result.value_or(FeeBreakdown{});
}

FillTerms terms(Tape tape, Liquidity liquidity, Side side, itch::Price4 price, std::uint32_t shares,
                bool regular_hours = true) {
  return {.tape = tape,
          .liquidity = liquidity,
          .side = side,
          .price = price,
          .shares = shares,
          .regular_hours = regular_hours};
}

// The ITCH 5.0 spec lists each code as a listing market; Regulation NMS assigns tapes by
// listing market. Anything else has no tape.
TEST(Tape, MarketCategoryGivesTheTape) {
  EXPECT_EQ(itch::tape_of('Q'), Tape::c);
  EXPECT_EQ(itch::tape_of('G'), Tape::c);
  EXPECT_EQ(itch::tape_of('S'), Tape::c);
  EXPECT_EQ(itch::tape_of('N'), Tape::a);
  EXPECT_EQ(itch::tape_of('A'), Tape::b);
  EXPECT_EQ(itch::tape_of('P'), Tape::b);
  EXPECT_EQ(itch::tape_of('Z'), Tape::b);
  EXPECT_EQ(itch::tape_of('V'), Tape::b);
  EXPECT_EQ(itch::tape_of(' '), Tape::unknown);
  EXPECT_EQ(itch::tape_of('M'), Tape::unknown);
  EXPECT_EQ(itch::tape_of('X'), Tape::unknown);
  int known = 0;
  for (int code = 0; code < 256; ++code) {
    known += itch::tape_of(static_cast<char>(code)) == Tape::unknown ? 0 : 1;
  }
  EXPECT_EQ(known, 8);
}

TEST(Tape, AFillWithNoTapeIsRefused) {
  const auto result = itch::fill_fees(*itch::find_fee_schedule(20191230),
                                      terms(Tape::unknown, Liquidity::maker, Side::bid, kFifty, 1));
  ASSERT_FALSE(result.has_value());
  EXPECT_EQ(result.error(), FeeError::unknown_tape);
}

// 37. The same fill on each date, tape by tape: 100 shares at $50.
TEST(Fees, S37DateKeyedRates) {
  // Add: $0.0020 on Tapes A and B and $0.0015 on C in 2019; $0.0018 and $0.0013 in 2026.
  for (const std::uint32_t date : {20191030U, 20191230U}) {
    for (const Tape tape : {Tape::a, Tape::b}) {
      EXPECT_EQ(fees(date, terms(tape, Liquidity::maker, Side::bid, kFifty, 100)),
                (FeeBreakdown{.exchange = -200'000'000'000, .section31 = 0, .taf = 0, .cat = 0}));
    }
    EXPECT_EQ(fees(date, terms(Tape::c, Liquidity::maker, Side::bid, kFifty, 100)),
              (FeeBreakdown{.exchange = -150'000'000'000, .section31 = 0, .taf = 0, .cat = 0}));
    // Remove: $0.0030 on every tape.
    for (const Tape tape : {Tape::a, Tape::b, Tape::c}) {
      EXPECT_EQ(fees(date, terms(tape, Liquidity::taker, Side::bid, kFifty, 100)),
                (FeeBreakdown{.exchange = 300'000'000'000, .section31 = 0, .taf = 0, .cat = 0}));
    }
  }
  // 2026 adds the CAT fee, $0.000001 a share: 100,000,000 on 100 shares.
  for (const Tape tape : {Tape::a, Tape::b}) {
    EXPECT_EQ(
        fees(20260612, terms(tape, Liquidity::maker, Side::bid, kFifty, 100)),
        (FeeBreakdown{.exchange = -180'000'000'000, .section31 = 0, .taf = 0, .cat = 100'000'000}));
  }
  EXPECT_EQ(
      fees(20260612, terms(Tape::c, Liquidity::maker, Side::bid, kFifty, 100)),
      (FeeBreakdown{.exchange = -130'000'000'000, .section31 = 0, .taf = 0, .cat = 100'000'000}));
  EXPECT_EQ(
      fees(20260612, terms(Tape::c, Liquidity::taker, Side::bid, kFifty, 100)),
      (FeeBreakdown{.exchange = 300'000'000'000, .section31 = 0, .taf = 0, .cat = 100'000'000}));
  // A day with no sourced rates has none.
  EXPECT_EQ(itch::find_fee_schedule(20200102), nullptr);
  EXPECT_EQ(itch::find_fee_schedule(0), nullptr);
}

// 38. Under $1: 1,000 shares at $0.50 is $500 of volume. Removing pays 0.30%, $1.50;
// adding earns nothing. On 2026-06-12 outside regular hours, removing pays 0.15% ($0.75)
// and adding earns 5 bp ($0.25).
TEST(Fees, S38UnderOneDollar) {
  EXPECT_EQ(fees(20191230, terms(Tape::c, Liquidity::taker, Side::bid, kHalfDollar, 1000)),
            (FeeBreakdown{.exchange = 1'500'000'000'000, .section31 = 0, .taf = 0, .cat = 0}));
  EXPECT_EQ(fees(20191230, terms(Tape::c, Liquidity::maker, Side::bid, kHalfDollar, 1000)),
            (FeeBreakdown{.exchange = 0, .section31 = 0, .taf = 0, .cat = 0}));
  EXPECT_EQ(fees(20191230, terms(Tape::a, Liquidity::taker, Side::bid, kHalfDollar, 1000, false)),
            (FeeBreakdown{.exchange = 1'500'000'000'000, .section31 = 0, .taf = 0, .cat = 0}));
  EXPECT_EQ(fees(20260612, terms(Tape::c, Liquidity::taker, Side::bid, kHalfDollar, 1000)),
            (FeeBreakdown{
                .exchange = 1'500'000'000'000, .section31 = 0, .taf = 0, .cat = 1'000'000'000}));
  EXPECT_EQ(
      fees(20260612, terms(Tape::c, Liquidity::taker, Side::bid, kHalfDollar, 1000, false)),
      (FeeBreakdown{.exchange = 750'000'000'000, .section31 = 0, .taf = 0, .cat = 1'000'000'000}));
  EXPECT_EQ(
      fees(20260612, terms(Tape::c, Liquidity::maker, Side::bid, kHalfDollar, 1000, false)),
      (FeeBreakdown{.exchange = -250'000'000'000, .section31 = 0, .taf = 0, .cat = 1'000'000'000}));
  // Exactly $1 is not under $1.
  EXPECT_EQ(fees(20191230, terms(Tape::c, Liquidity::taker, Side::bid, itch::kOneDollar, 1000)),
            (FeeBreakdown{.exchange = 3'000'000'000'000, .section31 = 0, .taf = 0, .cat = 0}));
}

// 39. Section 31 and TAF fall on a sell only. 100 shares sold at $50 is $5,000: Section 31
// at $20.70 per $1M is $0.1035, and TAF at $0.000119 a share is $0.0119.
TEST(Fees, S39SellsOnly) {
  EXPECT_EQ(fees(20191230, terms(Tape::c, Liquidity::maker, Side::ask, kFifty, 100)),
            (FeeBreakdown{.exchange = -150'000'000'000,
                          .section31 = 103'500'000'000,
                          .taf = 11'900'000'000,
                          .cat = 0}));
  EXPECT_EQ(fees(20191230, terms(Tape::c, Liquidity::maker, Side::bid, kFifty, 100)),
            (FeeBreakdown{.exchange = -150'000'000'000, .section31 = 0, .taf = 0, .cat = 0}));
  // 2026: $20.60 per $1M is $0.103, and $0.000195 a share is $0.0195.
  EXPECT_EQ(fees(20260612, terms(Tape::a, Liquidity::taker, Side::ask, kFifty, 100)),
            (FeeBreakdown{.exchange = 300'000'000'000,
                          .section31 = 103'000'000'000,
                          .taf = 19'500'000'000,
                          .cat = 100'000'000}));
}

// 40. TAF stops at $5.95 a trade in 2019 and $9.79 in 2026. 100,000 shares would be
// $11.90 and $19.50.
TEST(Fees, S40TafCap) {
  EXPECT_EQ(fees(20191230, terms(Tape::c, Liquidity::taker, Side::ask, kFifty, 100'000)).taf,
            5'950'000'000'000);
  EXPECT_EQ(fees(20260612, terms(Tape::c, Liquidity::taker, Side::ask, kFifty, 100'000)).taf,
            9'790'000'000'000);
  // Below the cap it is per share: 49,999 shares is $5.949881.
  EXPECT_EQ(fees(20191230, terms(Tape::c, Liquidity::taker, Side::ask, kFifty, 49'999)).taf,
            5'949'881'000'000);
}

TEST(Fees, ARefusedOverflowRatherThanAWrap) {
  const auto result =
      itch::fill_fees(*itch::find_fee_schedule(20191230),
                      terms(Tape::c, Liquidity::taker, Side::ask, 0xFFFFFFFFU, 0xFFFFFFFFU));
  ASSERT_FALSE(result.has_value());
  EXPECT_EQ(result.error(), FeeError::overflow);
}

}  // namespace
