// parse_midas on arbitrary bytes, and parse_thousands on the text a double prints.
//
// Whatever the input, parse_midas returns a table or a typed error, and a table has to
// survive being written back out. The first eight bytes also seed a property with a
// right answer worked out here: n shares, as thousands in a double and printed with the
// shortest round-trip form, the way the file was written, read back as exactly n. So
// does a sum of two such doubles, which is where the file's noise comes from.
#include <algorithm>
#include <array>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <format>
#include <span>
#include <string>
#include <string_view>

#include "fuzz_check.hpp"
#include "trades/midas.hpp"

namespace {

using itch::trades::MidasQuery;
using itch::trades::MidasRow;
using itch::trades::MidasTable;
using itch::trades::parse_midas;
using itch::trades::parse_thousands;
using itch::trades::symbol_view;

constexpr MidasQuery kQuery{.date = 20191230, .exchange = "Nasdaq"};

// A billion shares is far above any symbol's day on one venue, and it keeps the error of
// a double, even after one addition, inside the parser's millionth of a share.
constexpr std::uint64_t kShareMask = (1ULL << 30U) - 1;

std::string render(const MidasTable& table) {
  std::string text =
      "Date,Security,Ticker,Exchange,Trades,Hidden,OddLots,TradeVol('000),HiddenVol('000),"
      "OddLotVol('000)\n";
  const auto volume = [](std::uint64_t shares) {
    return std::format("{}.{:03}", shares / 1000, shares % 1000);
  };
  for (const MidasRow& row : table.rows) {
    text += std::format("20191230,{},{},Nasdaq,{}.0,{}.0,{}.0,{},{},{}\n",
                        row.security == 'S' ? "Stock" : "ETF", symbol_view(row.symbol), row.trades,
                        row.hidden_trades, row.odd_lots, volume(row.trade_shares),
                        volume(row.hidden_shares), volume(row.odd_lot_shares));
  }
  return text;
}

std::string shortest(double value) {
  std::array<char, 64> buffer{};
  const auto end =
      std::to_chars(buffer.data(), buffer.data() + buffer.size(), value, std::chars_format::fixed);
  return std::string{buffer.data(), end.ptr};
}

void check_double_text(std::span<const std::uint8_t> input) {
  if (input.size() < 2 * sizeof(std::uint64_t)) {
    return;
  }
  std::uint64_t first = 0;
  std::uint64_t second = 0;
  std::memcpy(&first, input.data(), sizeof first);
  std::memcpy(&second, input.data() + sizeof first, sizeof second);
  first &= kShareMask;
  second &= kShareMask;

  const auto single = parse_thousands(shortest(static_cast<double>(first) / 1000.0));
  FUZZ_CHECK(!single.error.has_value());
  FUZZ_CHECK(single.shares == first);

  const double sum = (static_cast<double>(first) / 1000.0) + (static_cast<double>(second) / 1000.0);
  const auto summed = parse_thousands(shortest(sum));
  FUZZ_CHECK(!summed.error.has_value());
  FUZZ_CHECK(summed.shares == first + second);
}

}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  check_double_text(std::span{data, size});

  const auto bytes = std::as_bytes(std::span{data, size});
  const auto parsed = parse_midas(bytes, kQuery);
  const auto newlines = static_cast<std::size_t>(std::ranges::count(bytes, std::byte{'\n'}));

  if (!parsed) {
    FUZZ_CHECK(parsed.error().line <= newlines + 1);
    FUZZ_CHECK(to_string(parsed.error().failure) != "unnamed");
    return 0;
  }

  const MidasTable& table = *parsed;
  FUZZ_CHECK(!table.rows.empty());
  FUZZ_CHECK(table.rows.size() <= table.rows_in_file);
  FUZZ_CHECK(table.rows_in_file <= newlines);  // A header line comes first.
  for (const MidasRow& row : table.rows) {
    FUZZ_CHECK(row.security == 'S' || row.security == 'E');
    FUZZ_CHECK(!symbol_view(row.symbol).empty());
  }

  const std::string text = render(table);
  const auto again = parse_midas(std::as_bytes(std::span{text}), kQuery);
  FUZZ_CHECK(again.has_value());
  FUZZ_CHECK(again->rows == table.rows);
  FUZZ_CHECK(again->rows_in_file == table.rows.size());
  return 0;
}
