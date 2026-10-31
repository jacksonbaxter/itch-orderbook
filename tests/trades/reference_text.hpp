// What the two reference-file parsers share: lines, fields and numbers read from
// untrusted text. Nothing here can throw, assert or read outside the bytes it is given.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <span>
#include <string_view>

#include "itch/messages.hpp"

namespace itch::trades {

[[nodiscard]] inline std::string_view as_text(std::span<const std::byte> bytes) noexcept {
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  return {reinterpret_cast<const char*>(bytes.data()), bytes.size()};
}

// Hands out one line at a time, without its end. A final line with no newline counts. A
// file that ends in a newline has no empty line after it.
class LineCursor {
 public:
  explicit LineCursor(std::string_view text) noexcept : rest_{text} {}

  [[nodiscard]] std::optional<std::string_view> next() noexcept {
    if (rest_.empty()) {
      return std::nullopt;
    }
    const std::size_t end = rest_.find('\n');
    std::string_view line = rest_.substr(0, end);
    rest_.remove_prefix(end == std::string_view::npos ? rest_.size() : end + 1);
    if (line.ends_with('\r')) {
      line.remove_suffix(1);
    }
    ++number_;
    return line;
  }

  // The 1-based number of the line next() last returned.
  [[nodiscard]] std::size_t number() const noexcept { return number_; }

 private:
  std::string_view rest_;
  std::size_t number_ = 0;
};

// Hands out comma-separated fields. The empty text has one empty field.
class FieldCursor {
 public:
  explicit FieldCursor(std::string_view line) noexcept : rest_{line} {}

  [[nodiscard]] std::optional<std::string_view> next() noexcept {
    if (done_) {
      return std::nullopt;
    }
    const std::size_t end = rest_.find(',');
    if (end == std::string_view::npos) {
      done_ = true;
      return rest_;
    }
    const std::string_view field = rest_.substr(0, end);
    rest_.remove_prefix(end + 1);
    return field;
  }

 private:
  std::string_view rest_;
  bool done_ = false;
};

[[nodiscard]] constexpr std::string_view trim(std::string_view text) noexcept {
  while (text.starts_with(' ')) {
    text.remove_prefix(1);
  }
  while (text.ends_with(' ')) {
    text.remove_suffix(1);
  }
  return text;
}

enum class NumberError : std::uint8_t { malformed, too_large };

struct ParsedNumber {
  std::uint64_t value = 0;
  std::optional<NumberError> error;
};

// All digits, at least one, into 64 bits.
[[nodiscard]] constexpr ParsedNumber parse_digits(std::string_view text) noexcept {
  if (text.empty()) {
    return {.value = 0, .error = NumberError::malformed};
  }
  constexpr std::uint64_t kMax = std::numeric_limits<std::uint64_t>::max();
  std::uint64_t value = 0;
  for (const char c : text) {
    if (c < '0' || c > '9') {
      return {.value = 0, .error = NumberError::malformed};
    }
    const auto digit = static_cast<std::uint64_t>(c - '0');
    if (value > (kMax - digit) / 10) {
      return {.value = 0, .error = NumberError::too_large};
    }
    value = value * 10 + digit;
  }
  return {.value = value, .error = std::nullopt};
}

// A symbol as ITCH's Stock field holds it: left-justified and padded with spaces. Only
// printable ASCII without a comma or a space fits, in one to eight characters.
[[nodiscard]] constexpr std::optional<Stock> to_stock(std::string_view text) noexcept {
  if (text.empty() || text.size() > 8) {
    return std::nullopt;
  }
  Stock stock{' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '};
  std::size_t at = 0;
  for (const char c : text) {
    if (c <= ' ' || c > '~' || c == ',') {
      return std::nullopt;
    }
    stock[at++] = c;
  }
  return stock;
}

[[nodiscard]] constexpr std::string_view symbol_view(const Stock& stock) noexcept {
  std::string_view view{stock.data(), stock.size()};
  return view.substr(0, view.find(' '));
}

}  // namespace itch::trades
