// Nasdaq's per-symbol cross file, CrossStatsYYYYMMDD.txt. Untrusted input: anything the
// file holds comes back as a value or a typed error, and nothing throws or asserts.
//
//     Date      ,Symbol,ListingMarket,OpeningCross,ClosingCross,IntradayCross,
//     ----------,------,-------------,------------,------------,-------------,
//     12/30/2019,AACG  ,Q            ,          13,           0,            0,
//     12/30/2019,TOTAL ,NULL         ,    19851021,    93930336,            0,
//
// No document defining the columns was found, so what they mean rests on the check they
// feed. The parser does not compare the TOTAL row with the sum of the rows: that is a
// check on the file's content, made and reported by T-TRADE-2 so that a bad row can
// still reach the per-symbol compare.
#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <optional>
#include <span>
#include <string_view>
#include <vector>

#include "itch/messages.hpp"
#include "trades/reference_text.hpp"

namespace itch::trades {

enum class CrossStatsFailure : std::uint8_t {
  empty_file,
  bad_header,
  bad_rule,
  bad_field_count,
  bad_date,
  mixed_dates,
  bad_symbol,
  bad_listing,
  bad_number,
  number_too_large,
  duplicate_symbol,
  rows_after_total,
  no_total,
};

[[nodiscard]] constexpr std::string_view to_string(CrossStatsFailure failure) noexcept {
  switch (failure) {
    case CrossStatsFailure::empty_file: return "empty_file";
    case CrossStatsFailure::bad_header: return "bad_header";
    case CrossStatsFailure::bad_rule: return "bad_rule";
    case CrossStatsFailure::bad_field_count: return "bad_field_count";
    case CrossStatsFailure::bad_date: return "bad_date";
    case CrossStatsFailure::mixed_dates: return "mixed_dates";
    case CrossStatsFailure::bad_symbol: return "bad_symbol";
    case CrossStatsFailure::bad_listing: return "bad_listing";
    case CrossStatsFailure::bad_number: return "bad_number";
    case CrossStatsFailure::number_too_large: return "number_too_large";
    case CrossStatsFailure::duplicate_symbol: return "duplicate_symbol";
    case CrossStatsFailure::rows_after_total: return "rows_after_total";
    case CrossStatsFailure::no_total: return "no_total";
  }
  return "unnamed";
}

struct CrossStatsError {
  CrossStatsFailure failure = CrossStatsFailure::empty_file;
  std::size_t line = 0;  // 1-based. 0 for a failure of the file as a whole.

  friend bool operator==(const CrossStatsError&, const CrossStatsError&) = default;
};

struct CrossStatsRow {
  Stock symbol{};
  char listing = 'Q';
  std::uint64_t opening = 0;
  std::uint64_t closing = 0;
  std::uint64_t intraday = 0;

  friend bool operator==(const CrossStatsRow&, const CrossStatsRow&) = default;
};

struct CrossStatsTable {
  std::uint32_t date = 0;           // YYYYMMDD.
  std::vector<CrossStatsRow> rows;  // Sorted by symbol.
  std::uint64_t total_opening = 0;  // As the TOTAL row states it, not summed here.
  std::uint64_t total_closing = 0;
  std::uint64_t total_intraday = 0;

  friend bool operator==(const CrossStatsTable&, const CrossStatsTable&) = default;

  [[nodiscard]] const CrossStatsRow* find(const Stock& symbol) const noexcept {
    const auto at =
        std::ranges::lower_bound(rows, symbol, std::ranges::less{}, &CrossStatsRow::symbol);
    return at != rows.end() && at->symbol == symbol ? &*at : nullptr;
  }
};

namespace cross_stats_detail {

constexpr std::size_t kFields = 7;  // Every line ends in a comma, so the last one is empty.
constexpr std::array<std::string_view, kFields - 1> kColumns{
    "Date", "Symbol", "ListingMarket", "OpeningCross", "ClosingCross", "IntradayCross"};

[[nodiscard]] constexpr std::optional<std::array<std::string_view, kFields>> split(
    std::string_view line) noexcept {
  std::array<std::string_view, kFields> fields;
  FieldCursor cursor{line};
  for (std::string_view& field : fields) {
    const auto next = cursor.next();
    if (!next) {
      return std::nullopt;
    }
    field = *next;
  }
  if (cursor.next() || !fields.back().empty()) {
    return std::nullopt;
  }
  return fields;
}

[[nodiscard]] constexpr bool is_rule(std::string_view field) noexcept {
  return !field.empty() && field.find_first_not_of('-') == std::string_view::npos;
}

// MM/DD/YYYY to YYYYMMDD.
[[nodiscard]] constexpr std::optional<std::uint32_t> parse_date(std::string_view text) noexcept {
  if (text.size() != 10 || text[2] != '/' || text[5] != '/') {
    return std::nullopt;
  }
  const ParsedNumber month = parse_digits(slice(text, 0, 2));
  const ParsedNumber day = parse_digits(slice(text, 3, 2));
  const ParsedNumber year = parse_digits(slice(text, 6, 4));
  if (month.error || day.error || year.error || month.value < 1 || month.value > 12 ||
      day.value < 1 || day.value > 31) {
    return std::nullopt;
  }
  return static_cast<std::uint32_t>((year.value * 10000) + (month.value * 100) + day.value);
}

// One of the three share columns, or the failure it raises.
[[nodiscard]] constexpr std::optional<CrossStatsFailure> read_shares(std::string_view field,
                                                                     std::uint64_t& out) noexcept {
  const ParsedNumber parsed = parse_digits(trim(field));
  if (!parsed.error) {
    out = parsed.value;
    return std::nullopt;
  }
  return *parsed.error == NumberError::malformed ? CrossStatsFailure::bad_number
                                                 : CrossStatsFailure::number_too_large;
}

// The header line and the rule under it, or what is wrong with them.
[[nodiscard]] constexpr std::optional<CrossStatsError> read_preamble(LineCursor& lines) noexcept {
  const auto header = lines.next();
  if (!header) {
    return CrossStatsError{.failure = CrossStatsFailure::empty_file, .line = 0};
  }
  const auto columns = split(*header);
  if (!columns || !std::ranges::equal(kColumns, std::span{*columns}.first(6), {}, {}, trim)) {
    return CrossStatsError{.failure = CrossStatsFailure::bad_header, .line = lines.number()};
  }
  const auto rule = lines.next();
  if (!rule) {
    return CrossStatsError{.failure = CrossStatsFailure::bad_rule, .line = lines.number()};
  }
  const auto dashes = split(*rule);
  if (!dashes || !std::ranges::all_of(std::span{*dashes}.first(6), is_rule)) {
    return CrossStatsError{.failure = CrossStatsFailure::bad_rule, .line = lines.number()};
  }
  return std::nullopt;
}

// A row's date, which every row of a file shares.
[[nodiscard]] constexpr std::optional<CrossStatsFailure> read_date(std::string_view field,
                                                                   std::uint32_t& date) noexcept {
  const auto parsed = parse_date(trim(field));
  if (!parsed) {
    return CrossStatsFailure::bad_date;
  }
  if (date != 0 && *parsed != date) {
    return CrossStatsFailure::mixed_dates;
  }
  date = *parsed;
  return std::nullopt;
}

// The symbol and listing market of a row that is not the total.
[[nodiscard]] constexpr std::optional<CrossStatsFailure> read_identity(
    std::string_view symbol_text, std::string_view listing_text, CrossStatsRow& row) noexcept {
  const auto symbol = to_stock(symbol_text);
  if (!symbol) {
    return CrossStatsFailure::bad_symbol;
  }
  row.symbol = *symbol;
  if (listing_text.size() != 1 || listing_text[0] < 'A' || listing_text[0] > 'Z') {
    return CrossStatsFailure::bad_listing;
  }
  row.listing = listing_text[0];
  return std::nullopt;
}

}  // namespace cross_stats_detail

[[nodiscard]] inline std::expected<CrossStatsTable, CrossStatsError> parse_cross_stats(
    std::span<const std::byte> bytes) {
  namespace detail = cross_stats_detail;
  const auto fail = [](CrossStatsFailure failure, std::size_t line) {
    return std::unexpected(CrossStatsError{.failure = failure, .line = line});
  };

  LineCursor lines{as_text(bytes)};
  if (const auto error = detail::read_preamble(lines)) {
    return std::unexpected(*error);
  }

  CrossStatsTable table;
  bool saw_total = false;
  while (const auto line = lines.next()) {
    if (saw_total) {
      return fail(CrossStatsFailure::rows_after_total, lines.number());
    }
    const auto fields = detail::split(*line);
    if (!fields) {
      return fail(CrossStatsFailure::bad_field_count, lines.number());
    }
    if (const auto failure = detail::read_date((*fields)[0], table.date)) {
      return fail(*failure, lines.number());
    }

    const std::string_view symbol_text = trim((*fields)[1]);
    const std::string_view listing_text = trim((*fields)[2]);
    saw_total = symbol_text == "TOTAL" && listing_text == "NULL";
    CrossStatsRow row;
    if (const auto failure =
            saw_total ? std::nullopt : detail::read_identity(symbol_text, listing_text, row)) {
      return fail(*failure, lines.number());
    }
    for (const auto& [field, out] :
         {std::pair{(*fields)[3], &row.opening}, std::pair{(*fields)[4], &row.closing},
          std::pair{(*fields)[5], &row.intraday}}) {
      if (const auto failure = detail::read_shares(field, *out)) {
        return fail(*failure, lines.number());
      }
    }
    if (saw_total) {
      table.total_opening = row.opening;
      table.total_closing = row.closing;
      table.total_intraday = row.intraday;
    } else {
      table.rows.push_back(row);
    }
  }
  if (!saw_total) {
    return fail(CrossStatsFailure::no_total, 0);
  }

  std::ranges::sort(table.rows, std::ranges::less{}, &CrossStatsRow::symbol);
  if (std::ranges::adjacent_find(table.rows, std::ranges::equal_to{}, &CrossStatsRow::symbol) !=
      table.rows.end()) {
    return fail(CrossStatsFailure::duplicate_symbol, 0);
  }
  return table;
}

}  // namespace itch::trades
