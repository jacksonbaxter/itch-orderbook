// The CrossStats and MIDAS parsers, on hand-written text. Every byte here is invented.
#include <gtest/gtest.h>

#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>

#include "itch/messages.hpp"
#include "trades/cross_stats.hpp"
#include "trades/midas.hpp"
#include "trades/reference_text.hpp"

namespace {

using itch::Stock;
using itch::trades::CrossStatsFailure;
using itch::trades::MidasFailure;
using itch::trades::MidasQuery;
using itch::trades::parse_count;
using itch::trades::parse_cross_stats;
using itch::trades::parse_midas;
using itch::trades::parse_thousands;
using itch::trades::to_stock;
using itch::trades::VolumeError;

std::span<const std::byte> bytes_of(std::string_view text) {
  return std::as_bytes(std::span{text});
}

constexpr std::string_view kHeader =
    "Date      ,Symbol,ListingMarket,OpeningCross,ClosingCross,IntradayCross,\r\n"
    "----------,------,-------------,------------,------------,-------------,\r\n";

std::string cross_file(std::string_view rows) { return std::string{kHeader} + std::string{rows}; }

Stock stock(std::string_view text) { return *to_stock(text); }

CrossStatsFailure cross_failure(const std::string& text) {
  const auto parsed = parse_cross_stats(bytes_of(text));
  EXPECT_FALSE(parsed.has_value());
  return parsed ? CrossStatsFailure::empty_file : parsed.error().failure;
}

TEST(CrossStats, ReadsRowsAndTheStatedTotal) {
  const std::string text = cross_file(
      "12/30/2019,ZZZ   ,Q            ,           7,           0,            0,\r\n"
      "12/30/2019,AAA   ,N            ,          13,         100,            5,\r\n"
      "12/30/2019,TOTAL ,NULL         ,          20,         100,            5,\r\n");
  const auto parsed = parse_cross_stats(bytes_of(text));
  ASSERT_TRUE(parsed.has_value());
  EXPECT_EQ(parsed->date, 20191230U);
  ASSERT_EQ(parsed->rows.size(), 2U);
  EXPECT_EQ(parsed->rows[0].symbol, stock("AAA"));  // Sorted, whatever the file's order.
  EXPECT_EQ(parsed->rows[0].listing, 'N');
  EXPECT_EQ(parsed->rows[0].opening, 13U);
  EXPECT_EQ(parsed->rows[0].closing, 100U);
  EXPECT_EQ(parsed->rows[0].intraday, 5U);
  EXPECT_EQ(parsed->total_opening, 20U);
  EXPECT_EQ(parsed->total_closing, 100U);
  EXPECT_EQ(parsed->total_intraday, 5U);
  ASSERT_NE(parsed->find(stock("ZZZ")), nullptr);
  EXPECT_EQ(parsed->find(stock("ZZZ"))->opening, 7U);
  EXPECT_EQ(parsed->find(stock("MMM")), nullptr);
}

TEST(CrossStats, KeepsAWrongTotalForTheCheckToReport) {
  const std::string text = cross_file(
      "12/30/2019,AAA   ,Q            ,           1,           2,            0,\r\n"
      "12/30/2019,TOTAL ,NULL         ,           9,           9,            9,\r\n");
  const auto parsed = parse_cross_stats(bytes_of(text));
  ASSERT_TRUE(parsed.has_value());
  EXPECT_EQ(parsed->total_opening, 9U);
}

TEST(CrossStats, AcceptsLfOnlyAndAMissingFinalNewline) {
  const std::string text =
      "Date,Symbol,ListingMarket,OpeningCross,ClosingCross,IntradayCross,\n"
      "-,-,-,-,-,-,\n"
      "01/02/2020,AAA,Q,1,2,0,\n"
      "01/02/2020,TOTAL,NULL,1,2,0,";
  const auto parsed = parse_cross_stats(bytes_of(text));
  ASSERT_TRUE(parsed.has_value());
  EXPECT_EQ(parsed->date, 20200102U);
}

TEST(CrossStats, EachBadFileFailsAsItself) {
  EXPECT_EQ(cross_failure(""), CrossStatsFailure::empty_file);
  EXPECT_EQ(cross_failure("Date,Symbol\n"), CrossStatsFailure::bad_header);
  EXPECT_EQ(cross_failure("Date,Sym,ListingMarket,OpeningCross,ClosingCross,IntradayCross,\n"),
            CrossStatsFailure::bad_header);
  EXPECT_EQ(cross_failure("Date,Symbol,ListingMarket,ClosingCross,OpeningCross,IntradayCross,\n"),
            CrossStatsFailure::bad_header);
  EXPECT_EQ(cross_failure("Date,Symbol,ListingMarket,OpeningCross,ClosingCross,IntradayCross,\n"),
            CrossStatsFailure::bad_rule);
  EXPECT_EQ(cross_failure("Date,Symbol,ListingMarket,OpeningCross,ClosingCross,IntradayCross,\n"
                          "-,-,-,x,-,-,\n"),
            CrossStatsFailure::bad_rule);
  // A file of one line with no newline: the failure names the line it has, found by the
  // fuzzer, which holds every error to a line that exists.
  const std::string_view only_header =
      "Date,Symbol,ListingMarket,OpeningCross,ClosingCross,IntradayCross,";
  const auto header_alone = itch::trades::parse_cross_stats(std::as_bytes(std::span{only_header}));
  ASSERT_FALSE(header_alone.has_value());
  EXPECT_EQ(header_alone.error().failure, CrossStatsFailure::bad_rule);
  EXPECT_EQ(header_alone.error().line, 1U);
  EXPECT_EQ(cross_failure(cross_file("")), CrossStatsFailure::no_total);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,AAA,Q,1,2,0,\n")), CrossStatsFailure::no_total);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,AAA,Q,1,2,0\n")),
            CrossStatsFailure::bad_field_count);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,AAA,Q,1,2,0,,\n")),
            CrossStatsFailure::bad_field_count);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,AAA,Q,1,2,0,x\n")),
            CrossStatsFailure::bad_field_count);
  EXPECT_EQ(cross_failure(cross_file("2019-12-30,AAA,Q,1,2,0,\n")), CrossStatsFailure::bad_date);
  EXPECT_EQ(cross_failure(cross_file("13/30/2019,AAA,Q,1,2,0,\n")), CrossStatsFailure::bad_date);
  EXPECT_EQ(cross_failure(cross_file("12/32/2019,AAA,Q,1,2,0,\n")), CrossStatsFailure::bad_date);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,AAA,Q,1,2,0,\n12/31/2019,BBB,Q,1,2,0,\n")),
            CrossStatsFailure::mixed_dates);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,,Q,1,2,0,\n")), CrossStatsFailure::bad_symbol);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,TOOLONGSYM,Q,1,2,0,\n")),
            CrossStatsFailure::bad_symbol);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,A B,Q,1,2,0,\n")), CrossStatsFailure::bad_symbol);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,AAA,NULL,1,2,0,\n")),
            CrossStatsFailure::bad_listing);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,AAA,q,1,2,0,\n")), CrossStatsFailure::bad_listing);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,AAA,Q,-1,2,0,\n")), CrossStatsFailure::bad_number);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,AAA,Q,1.5,2,0,\n")),
            CrossStatsFailure::bad_number);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,AAA,Q,,2,0,\n")), CrossStatsFailure::bad_number);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,AAA,Q,18446744073709551616,2,0,\n")),
            CrossStatsFailure::number_too_large);
  EXPECT_EQ(
      cross_failure(cross_file(
          "12/30/2019,AAA,Q,1,2,0,\n12/30/2019,AAA,Q,1,2,0,\n12/30/2019,TOTAL,NULL,2,4,0,\n")),
      CrossStatsFailure::duplicate_symbol);
  EXPECT_EQ(cross_failure(cross_file("12/30/2019,TOTAL,NULL,0,0,0,\n12/30/2019,AAA,Q,1,2,0,\n")),
            CrossStatsFailure::rows_after_total);
}

TEST(CrossStats, ReportsTheLineOfAFailure) {
  const std::string text = cross_file("12/30/2019,AAA,Q,1,2,0,\n12/30/2019,BBB,Q,x,2,0,\n");
  const auto parsed = parse_cross_stats(bytes_of(text));
  ASSERT_FALSE(parsed.has_value());
  EXPECT_EQ(parsed.error().line, 4U);
}

TEST(CrossStats, TakesTheLargestShareCount) {
  const std::string text =
      cross_file("12/30/2019,AAA,Q,18446744073709551615,0,0,\n12/30/2019,TOTAL,NULL,0,0,0,\n");
  const auto parsed = parse_cross_stats(bytes_of(text));
  ASSERT_TRUE(parsed.has_value());
  EXPECT_EQ(parsed->rows[0].opening, 18446744073709551615ULL);
}

TEST(Thousands, AWholeShareIsExact) {
  EXPECT_EQ(parse_thousands("0.0").shares, 0U);
  EXPECT_EQ(parse_thousands("0.001").shares, 1U);
  EXPECT_EQ(parse_thousands("1.143").shares, 1143U);
  EXPECT_EQ(parse_thousands("1.1").shares, 1100U);
  EXPECT_EQ(parse_thousands("12").shares, 12000U);
  EXPECT_EQ(parse_thousands("5466.975").shares, 5466975U);
}

TEST(Thousands, FloatNoiseResolvesToTheNearestWholeShare) {
  EXPECT_EQ(parse_thousands("11.299000000000001").shares, 11299U);
  EXPECT_EQ(parse_thousands("19.977999999999998").shares, 19978U);
  EXPECT_EQ(parse_thousands("0.9999999999999999").shares, 1000U);
  EXPECT_EQ(parse_thousands("999.9999999999999").shares, 1000000U);
  EXPECT_FALSE(parse_thousands("0.14300000000000002").error.has_value());
}

TEST(Thousands, APartShareIsNotRounded) {
  EXPECT_EQ(parse_thousands("1.2345").error, VolumeError::not_whole_shares);
  EXPECT_EQ(parse_thousands("1.234001").error, VolumeError::not_whole_shares);
  EXPECT_EQ(parse_thousands("1.234999").error, VolumeError::not_whole_shares);
  EXPECT_EQ(parse_thousands("1.2340000010").error, VolumeError::not_whole_shares);
  EXPECT_EQ(parse_thousands("1.2349999989").error, VolumeError::not_whole_shares);
}

TEST(Thousands, WhatIsNotANumberIsMalformed) {
  for (const std::string_view text : {"", ".", "1.", ".5", "-1.0", "+1.0", "1e3", "nan", "1.2.3",
                                      " 1.0", "1.0 ", "1,0", "0x10"}) {
    EXPECT_EQ(parse_thousands(text).error, VolumeError::malformed) << text;
  }
}

TEST(Thousands, RefusesWhatDoesNotFit) {
  EXPECT_EQ(parse_thousands("18446744073709551616").error, VolumeError::too_large);
  EXPECT_EQ(parse_thousands("18446744073709551.0").error, VolumeError::too_large);
  EXPECT_FALSE(parse_thousands("18446744073709550.0").error.has_value());
}

TEST(Count, ReadsTheFilesForm) {
  EXPECT_EQ(parse_count("64805.0").value, 64805U);
  EXPECT_EQ(parse_count("7").value, 7U);
  EXPECT_FALSE(parse_count("7.00").error.has_value());
  EXPECT_TRUE(parse_count("7.5").error.has_value());
  EXPECT_TRUE(parse_count("7.").error.has_value());
  EXPECT_TRUE(parse_count("").error.has_value());
  EXPECT_TRUE(parse_count("-1.0").error.has_value());
}

constexpr std::string_view kMidasHeader =
    "Date,Security,Ticker,Exchange,McapRank,TurnRank,VolatilityRank,PriceRank,Cancels,Trades,"
    "LitTrades,OddLots,Hidden,TradesForHidden,OrderVol('000),TradeVol('000),LitVol('000),"
    "OddLotVol('000),HiddenVol('000),TradeVolForHidden('000)\n";

// A row of the shape above. Only these values are read.
std::string midas_row(std::string_view date, std::string_view security, std::string_view ticker,
                      std::string_view exchange, std::string_view trades, std::string_view hidden,
                      std::string_view odd_lots, std::string_view trade_vol,
                      std::string_view hidden_vol, std::string_view odd_lot_vol) {
  std::string row;
  for (const std::string_view field :
       {date, security, ticker, exchange, std::string_view{"10.0"}, std::string_view{""},
        std::string_view{"-1.0"}, std::string_view{"9.0"}, std::string_view{"1.0"}, trades,
        std::string_view{"1.0"}, odd_lots, hidden, std::string_view{"1.0"}, std::string_view{"1.0"},
        trade_vol, std::string_view{"1.0"}, odd_lot_vol, hidden_vol}) {
    row += field;
    row += ',';
  }
  row += "1.0\n";
  return row;
}

std::string good_row(std::string_view ticker = "AAA") {
  return midas_row("20191230", "Stock", ticker, "Nasdaq", "12.0", "3.0", "4.0", "1.5", "0.25",
                   "0.075");
}

constexpr MidasQuery kQuery{.date = 20191230, .exchange = "Nasdaq"};

MidasFailure midas_failure(const std::string& text) {
  const auto parsed = parse_midas(bytes_of(text), kQuery);
  EXPECT_FALSE(parsed.has_value());
  return parsed ? MidasFailure::empty_file : parsed.error().failure;
}

std::string bad_row(std::string_view security, std::string_view ticker, std::string_view trades,
                    std::string_view trade_vol) {
  return std::string{kMidasHeader} + midas_row("20191230", security, ticker, "Nasdaq", trades,
                                               "1.0", "1.0", trade_vol, "1.0", "1.0");
}

TEST(Midas, ReadsTheRowsOfOneDayAndExchange) {
  const std::string text =
      std::string{kMidasHeader} + good_row() +
      midas_row("20191230", "ETF", "BBB", "Nasdaq", "1.0", "0.0", "1.0", "0.001", "0.0", "0.001") +
      midas_row("20191230", "Stock", "AAA", "Arca", "99.0", "9.0", "9.0", "-3.5", "x", "") +
      midas_row("20191231", "Stock", "AAA", "Nasdaq", "99.0", "9.0", "9.0", "9.0", "9.0", "9.0");
  const auto parsed = parse_midas(bytes_of(text), kQuery);
  ASSERT_TRUE(parsed.has_value());
  EXPECT_EQ(parsed->rows_in_file, 4U);
  ASSERT_EQ(parsed->rows.size(), 2U);
  const auto& row = parsed->rows[0];
  EXPECT_EQ(row.symbol, stock("AAA"));
  EXPECT_EQ(row.security, 'S');
  EXPECT_EQ(row.trades, 12U);
  EXPECT_EQ(row.hidden_trades, 3U);
  EXPECT_EQ(row.odd_lots, 4U);
  EXPECT_EQ(row.trade_shares, 1500U);
  EXPECT_EQ(row.hidden_shares, 250U);
  EXPECT_EQ(row.odd_lot_shares, 75U);
  EXPECT_EQ(parsed->rows[1].security, 'E');
}

TEST(Midas, FindsColumnsByName) {
  const std::string text =
      "Exchange,Hidden,OddLots,Ticker,Trades,HiddenVol('000),Security,TradeVol('000),Date,"
      "OddLotVol('000)\n"
      "Nasdaq,1.0,2.0,AAA,3.0,0.5,Stock,0.75,20191230,0.25\n";
  const auto parsed = parse_midas(bytes_of(text), kQuery);
  ASSERT_TRUE(parsed.has_value());
  EXPECT_EQ(parsed->rows[0].trades, 3U);
  EXPECT_EQ(parsed->rows[0].trade_shares, 750U);
  EXPECT_EQ(parsed->rows[0].hidden_shares, 500U);
}

TEST(Midas, KeepsASecondRowForASymbol) {
  const std::string text = std::string{kMidasHeader} + good_row() + good_row();
  const auto parsed = parse_midas(bytes_of(text), kQuery);
  ASSERT_TRUE(parsed.has_value());
  EXPECT_EQ(parsed->rows.size(), 2U);
}

TEST(Midas, EachBadFileFailsAsItself) {
  EXPECT_EQ(midas_failure(""), MidasFailure::empty_file);
  EXPECT_EQ(midas_failure("\n"), MidasFailure::bad_header);
  EXPECT_EQ(midas_failure("Date,Ticker\n"), MidasFailure::missing_column);
  EXPECT_EQ(midas_failure(std::string{"Date,"} + std::string{kMidasHeader}),
            MidasFailure::duplicate_column);
  EXPECT_EQ(midas_failure(std::string{kMidasHeader}), MidasFailure::no_rows);
  EXPECT_EQ(midas_failure(std::string{kMidasHeader} + "20191230,Stock\n"),
            MidasFailure::bad_field_count);
  EXPECT_EQ(midas_failure(std::string{kMidasHeader} + good_row().substr(0, 40) + "\n"),
            MidasFailure::bad_field_count);
  EXPECT_EQ(
      midas_failure(std::string{kMidasHeader} + midas_row("2019123", "Stock", "A", "Nasdaq", "1.0",
                                                          "1.0", "1.0", "1", "1", "1")),
      MidasFailure::bad_date);
  EXPECT_EQ(
      midas_failure(std::string{kMidasHeader} + midas_row("2019123x", "Stock", "A", "Arca", "1.0",
                                                          "1.0", "1.0", "1", "1", "1")),
      MidasFailure::bad_date);
  EXPECT_EQ(midas_failure(bad_row("Bond", "AAA", "1.0", "1.0")), MidasFailure::bad_security);
  EXPECT_EQ(midas_failure(bad_row("Stock", "", "1.0", "1.0")), MidasFailure::bad_ticker);
  EXPECT_EQ(midas_failure(bad_row("Stock", "TOOLONGSYM", "1.0", "1.0")), MidasFailure::bad_ticker);
  EXPECT_EQ(midas_failure(bad_row("Stock", "AAA", "1.5", "1.0")), MidasFailure::bad_count);
  EXPECT_EQ(midas_failure(bad_row("Stock", "AAA", "", "1.0")), MidasFailure::bad_count);
  EXPECT_EQ(midas_failure(bad_row("Stock", "AAA", "1.0", "")), MidasFailure::bad_volume);
  EXPECT_EQ(midas_failure(bad_row("Stock", "AAA", "1.0", "-1.0")), MidasFailure::bad_volume);
  EXPECT_EQ(midas_failure(bad_row("Stock", "AAA", "1.0", "1.2345")),
            MidasFailure::not_whole_shares);
  EXPECT_EQ(midas_failure(bad_row("Stock", "AAA", "99999999999999999999", "1.0")),
            MidasFailure::number_too_large);
  EXPECT_EQ(midas_failure(bad_row("Stock", "AAA", "1.0", "99999999999999999999")),
            MidasFailure::number_too_large);
}

TEST(Midas, ReportsTheLineOfAFailure) {
  const std::string text =
      std::string{kMidasHeader} + good_row() + bad_row("Bond", "AAA", "1.0", "1.0");
  const auto parsed = parse_midas(bytes_of(text.substr(kMidasHeader.size())), kQuery);
  ASSERT_FALSE(parsed.has_value());
  const std::string doubled =
      std::string{kMidasHeader} + good_row() +
      midas_row("20191230", "Bond", "A", "Nasdaq", "1.0", "1.0", "1.0", "1.0", "1.0", "1.0");
  const auto failed = parse_midas(bytes_of(doubled), kQuery);
  ASSERT_FALSE(failed.has_value());
  EXPECT_EQ(failed.error().line, 3U);
}

}  // namespace
