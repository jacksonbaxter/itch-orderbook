// parse_cross_stats on arbitrary bytes. Whatever the input, it returns a table or a
// typed error. A table has to survive being written back out: the same rows, in the
// same order, from a second parse of a canonical rendering. An error has to name a line
// that exists.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <format>
#include <span>
#include <string>
#include <string_view>

#include "fuzz_check.hpp"
#include "trades/cross_stats.hpp"

namespace {

using itch::trades::CrossStatsTable;
using itch::trades::parse_cross_stats;
using itch::trades::symbol_view;

std::string render(const CrossStatsTable& table) {
  const std::uint32_t year = table.date / 10000;
  const std::uint32_t month = table.date / 100 % 100;
  const std::uint32_t day = table.date % 100;
  const std::string date = std::format("{:02}/{:02}/{:04}", month, day, year);
  std::string text =
      "Date      ,Symbol,ListingMarket,OpeningCross,ClosingCross,IntradayCross,\r\n"
      "----------,------,-------------,------------,------------,-------------,\r\n";
  for (const auto& row : table.rows) {
    text += std::format("{},{:6},{:13},{:12},{:12},{:13},\r\n", date, symbol_view(row.symbol),
                        row.listing, row.opening, row.closing, row.intraday);
  }
  text += std::format("{},TOTAL ,NULL         ,{:12},{:12},{:13},\r\n", date, table.total_opening,
                      table.total_closing, table.total_intraday);
  return text;
}

}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  const auto bytes = std::as_bytes(std::span{data, size});
  const auto parsed = parse_cross_stats(bytes);
  const auto newlines = static_cast<std::size_t>(std::ranges::count(bytes, std::byte{'\n'}));

  if (!parsed) {
    FUZZ_CHECK(parsed.error().line <= newlines + 1);
    FUZZ_CHECK(!to_string(parsed.error().failure).empty());
    FUZZ_CHECK(to_string(parsed.error().failure) != "unnamed");
    return 0;
  }

  const CrossStatsTable& table = *parsed;
  FUZZ_CHECK(size != 0);
  // The header, the rule, each row and the total, one line each.
  FUZZ_CHECK(table.rows.size() + 3 <= newlines + 1);
  FUZZ_CHECK(table.date >= 101);
  FUZZ_CHECK(std::ranges::is_sorted(table.rows, std::ranges::less{},
                                    &itch::trades::CrossStatsRow::symbol));
  for (const auto& row : table.rows) {
    const std::string_view symbol = symbol_view(row.symbol);
    FUZZ_CHECK(!symbol.empty());
    FUZZ_CHECK(symbol.find(',') == std::string_view::npos);
    FUZZ_CHECK(row.listing >= 'A' && row.listing <= 'Z');
    FUZZ_CHECK(table.find(row.symbol) != nullptr);
    FUZZ_CHECK(*table.find(row.symbol) == row);
  }

  const std::string text = render(table);
  const auto again = parse_cross_stats(std::as_bytes(std::span{text}));
  FUZZ_CHECK(again.has_value());
  FUZZ_CHECK(*again == table);
  return 0;
}
