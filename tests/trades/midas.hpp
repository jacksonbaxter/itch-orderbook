// SEC MIDAS per-security, per-exchange daily rows, such as dec19.csv. Untrusted input:
// anything the file holds comes back as a value or a typed error, and nothing throws or
// asserts.
//
// Every row must have the header's field count and an eight-digit date. Only rows of
// the requested date and exchange are read any further, because other exchanges carry
// values this project has no use for, negative ones included.
//
// Units. Volumes are in thousands of shares with three decimals, so a value resolves to
// one share. The file was written from doubles, so 11.299 can read 11.299000000000001.
// parse_thousands() recovers the whole number of shares from that text with no floating
// point. Digits past the third decimal are representation error, and may only be noise:
// anything further than a millionth of a share from a whole share is a typed failure,
// not rounded. The largest seen on the two pinned days is 3.8e-9 of a share. That bound
// says what counts as a number of shares. It is not a tolerance on a comparison, and
// each comparison made with the result is of integers, for equality.
#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <limits>
#include <optional>
#include <span>
#include <string_view>
#include <vector>

#include "itch/messages.hpp"
#include "trades/reference_text.hpp"

namespace itch::trades {

enum class MidasFailure : std::uint8_t {
  empty_file,
  bad_header,
  missing_column,
  duplicate_column,
  bad_field_count,
  bad_date,
  bad_security,
  bad_ticker,
  bad_count,
  bad_volume,
  not_whole_shares,
  number_too_large,
  no_rows,
};

[[nodiscard]] constexpr std::string_view to_string(MidasFailure failure) noexcept {
  switch (failure) {
    case MidasFailure::empty_file: return "empty_file";
    case MidasFailure::bad_header: return "bad_header";
    case MidasFailure::missing_column: return "missing_column";
    case MidasFailure::duplicate_column: return "duplicate_column";
    case MidasFailure::bad_field_count: return "bad_field_count";
    case MidasFailure::bad_date: return "bad_date";
    case MidasFailure::bad_security: return "bad_security";
    case MidasFailure::bad_ticker: return "bad_ticker";
    case MidasFailure::bad_count: return "bad_count";
    case MidasFailure::bad_volume: return "bad_volume";
    case MidasFailure::not_whole_shares: return "not_whole_shares";
    case MidasFailure::number_too_large: return "number_too_large";
    case MidasFailure::no_rows: return "no_rows";
  }
  return "unnamed";
}

struct MidasError {
  MidasFailure failure = MidasFailure::empty_file;
  std::size_t line = 0;  // 1-based. 0 for a failure of the file as a whole.

  friend bool operator==(const MidasError&, const MidasError&) = default;
};

struct MidasQuery {
  std::uint32_t date = 0;  // YYYYMMDD.
  std::string_view exchange;
};

struct MidasRow {
  Stock symbol{};
  char security = 'S';  // 'S' Stock, 'E' ETF.
  std::uint64_t trades = 0;
  std::uint64_t hidden_trades = 0;
  std::uint64_t odd_lots = 0;
  std::uint64_t trade_shares = 0;
  std::uint64_t hidden_shares = 0;
  std::uint64_t odd_lot_shares = 0;

  friend bool operator==(const MidasRow&, const MidasRow&) = default;
};

struct MidasTable {
  std::vector<MidasRow> rows;  // In file order. A symbol can appear twice.
  std::uint64_t rows_in_file = 0;

  friend bool operator==(const MidasTable&, const MidasTable&) = default;
};

enum class VolumeError : std::uint8_t { malformed, not_whole_shares, too_large };

struct ParsedVolume {
  std::uint64_t shares = 0;
  std::optional<VolumeError> error;
};

// Thousands of shares, as text, to a whole number of shares.
[[nodiscard]] constexpr ParsedVolume parse_thousands(std::string_view text) noexcept {
  constexpr std::size_t kShareDigits = 3;
  constexpr std::size_t kNoiseDigits = 6;  // A millionth of a share.
  constexpr std::uint64_t kMaxWhole = std::numeric_limits<std::uint64_t>::max() / 1000 - 1;

  const std::size_t dot = text.find('.');
  const std::string_view whole_text = text.substr(0, dot);
  const std::string_view fraction =
      dot == std::string_view::npos ? std::string_view{} : text.substr(dot + 1);
  const ParsedNumber whole = parse_digits(whole_text);
  if (whole.error) {
    return {.shares = 0,
            .error = *whole.error == NumberError::too_large ? VolumeError::too_large
                                                            : VolumeError::malformed};
  }
  if (whole.value > kMaxWhole) {
    return {.shares = 0, .error = VolumeError::too_large};
  }
  if (dot != std::string_view::npos && fraction.empty()) {
    return {.shares = 0, .error = VolumeError::malformed};
  }
  if (fraction.find_first_not_of("0123456789") != std::string_view::npos) {
    return {.shares = 0, .error = VolumeError::malformed};
  }

  std::uint64_t shares = whole.value * 1000;
  std::uint64_t place = 100;
  for (const char c : fraction.substr(0, kShareDigits)) {
    shares += static_cast<std::uint64_t>(c - '0') * place;
    place /= 10;
  }
  const std::string_view noise = fraction.substr(std::min(fraction.size(), kShareDigits));
  const auto noise_is = [&](char digit) {
    return noise.size() >= kNoiseDigits && noise.find_first_not_of(digit) >= kNoiseDigits;
  };
  if (noise.find_first_not_of('0') == std::string_view::npos || noise_is('0')) {
    return {.shares = shares, .error = std::nullopt};
  }
  if (noise_is('9')) {
    return {.shares = shares + 1, .error = std::nullopt};
  }
  return {.shares = 0, .error = VolumeError::not_whole_shares};
}

// A count, which the file writes as 64805.0. Only zeros may follow the point.
[[nodiscard]] constexpr ParsedNumber parse_count(std::string_view text) noexcept {
  const std::size_t dot = text.find('.');
  if (dot != std::string_view::npos) {
    const std::string_view fraction = text.substr(dot + 1);
    if (fraction.empty() || fraction.find_first_not_of('0') != std::string_view::npos) {
      return {.value = 0, .error = NumberError::malformed};
    }
  }
  return parse_digits(text.substr(0, dot));
}

namespace midas_detail {

// The columns read, by name, and which field holds each.
struct Columns {
  std::size_t fields = 0;
  std::size_t date = 0;
  std::size_t security = 0;
  std::size_t ticker = 0;
  std::size_t exchange = 0;
  std::size_t trades = 0;
  std::size_t hidden = 0;
  std::size_t odd_lots = 0;
  std::size_t trade_vol = 0;
  std::size_t hidden_vol = 0;
  std::size_t odd_lot_vol = 0;
};

inline constexpr std::size_t kNamedColumns = 10;
inline constexpr std::array<std::string_view, kNamedColumns> kNames{
    "Date",   "Security", "Ticker",         "Exchange",        "Trades",
    "Hidden", "OddLots",  "TradeVol('000)", "HiddenVol('000)", "OddLotVol('000)"};

[[nodiscard]] constexpr std::array<std::size_t Columns::*, kNamedColumns> members() noexcept {
  return {&Columns::date,       &Columns::security,   &Columns::ticker,   &Columns::exchange,
          &Columns::trades,     &Columns::hidden,     &Columns::odd_lots, &Columns::trade_vol,
          &Columns::hidden_vol, &Columns::odd_lot_vol};
}

[[nodiscard]] constexpr std::expected<Columns, MidasFailure> read_header(
    std::string_view line) noexcept {
  Columns columns;
  std::array<bool, kNamedColumns> seen{};
  FieldCursor cursor{line};
  while (const auto field = cursor.next()) {
    if (field->empty()) {
      return std::unexpected(MidasFailure::bad_header);
    }
    for (std::size_t i = 0; i < kNamedColumns; ++i) {
      if (*field == kNames[i]) {
        if (seen[i]) {
          return std::unexpected(MidasFailure::duplicate_column);
        }
        seen[i] = true;
        columns.*members()[i] = columns.fields;
      }
    }
    ++columns.fields;
  }
  if (!std::ranges::all_of(seen, [](bool found) { return found; })) {
    return std::unexpected(MidasFailure::missing_column);
  }
  return columns;
}

[[nodiscard]] constexpr std::optional<MidasFailure> read_volume(std::string_view text,
                                                                std::uint64_t& out) noexcept {
  const ParsedVolume parsed = parse_thousands(text);
  if (!parsed.error) {
    out = parsed.shares;
    return std::nullopt;
  }
  switch (*parsed.error) {
    case VolumeError::malformed: return MidasFailure::bad_volume;
    case VolumeError::not_whole_shares: return MidasFailure::not_whole_shares;
    case VolumeError::too_large: return MidasFailure::number_too_large;
  }
  return MidasFailure::bad_volume;
}

[[nodiscard]] constexpr std::optional<MidasFailure> read_count(std::string_view text,
                                                               std::uint64_t& out) noexcept {
  const ParsedNumber parsed = parse_count(text);
  if (!parsed.error) {
    out = parsed.value;
    return std::nullopt;
  }
  return *parsed.error == NumberError::too_large ? MidasFailure::number_too_large
                                                 : MidasFailure::bad_count;
}

// The rest of a row of the requested day and exchange.
[[nodiscard]] constexpr std::expected<MidasRow, MidasFailure> read_row(
    const Columns& columns, std::span<const std::string_view> fields) noexcept {
  MidasRow row;
  const std::string_view security = fields[columns.security];
  if (security == "Stock") {
    row.security = 'S';
  } else if (security == "ETF") {
    row.security = 'E';
  } else {
    return std::unexpected(MidasFailure::bad_security);
  }
  const auto symbol = to_stock(fields[columns.ticker]);
  if (!symbol) {
    return std::unexpected(MidasFailure::bad_ticker);
  }
  row.symbol = *symbol;
  for (const auto& [index, out] :
       {std::pair{columns.trades, &row.trades}, std::pair{columns.hidden, &row.hidden_trades},
        std::pair{columns.odd_lots, &row.odd_lots}}) {
    if (const auto failure = read_count(fields[index], *out)) {
      return std::unexpected(*failure);
    }
  }
  for (const auto& [index, out] : {std::pair{columns.trade_vol, &row.trade_shares},
                                   std::pair{columns.hidden_vol, &row.hidden_shares},
                                   std::pair{columns.odd_lot_vol, &row.odd_lot_shares}}) {
    if (const auto failure = read_volume(fields[index], *out)) {
      return std::unexpected(*failure);
    }
  }
  return row;
}

}  // namespace midas_detail

[[nodiscard]] inline std::expected<MidasTable, MidasError> parse_midas(
    std::span<const std::byte> bytes, const MidasQuery& query) {
  namespace detail = midas_detail;
  const auto fail = [](MidasFailure failure, std::size_t line) {
    return std::unexpected(MidasError{.failure = failure, .line = line});
  };

  LineCursor lines{as_text(bytes)};
  const auto header = lines.next();
  if (!header) {
    return fail(MidasFailure::empty_file, 0);
  }
  const auto columns = detail::read_header(*header);
  if (!columns) {
    return fail(columns.error(), 1);
  }

  MidasTable table;
  std::vector<std::string_view> fields(columns->fields);
  while (const auto line = lines.next()) {
    FieldCursor cursor{*line};
    std::size_t count = 0;
    while (const auto field = cursor.next()) {
      if (count < fields.size()) {
        fields[count] = *field;
      }
      ++count;
    }
    if (count != fields.size()) {
      return fail(MidasFailure::bad_field_count, lines.number());
    }
    ++table.rows_in_file;
    const std::string_view date_text = fields[columns->date];
    const ParsedNumber date = parse_digits(date_text);
    if (date.error || date_text.size() != 8) {
      return fail(MidasFailure::bad_date, lines.number());
    }
    if (date.value != query.date || fields[columns->exchange] != query.exchange) {
      continue;
    }
    const auto row = detail::read_row(*columns, fields);
    if (!row) {
      return fail(row.error(), lines.number());
    }
    table.rows.push_back(*row);
  }
  if (table.rows.empty()) {
    return fail(MidasFailure::no_rows, 0);
  }
  return table;
}

}  // namespace itch::trades
