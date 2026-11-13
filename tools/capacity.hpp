// --max-orders and --max-levels, read the same way by every tool that sizes a book from the
// command line. No benchmark includes this header.
#pragma once

#include <charconv>
#include <cstdint>
#include <string_view>
#include <system_error>

#include "itch/book_types.hpp"

namespace itch::tools {

enum class CapacityOption : std::uint8_t { not_a_capacity, taken, bad_value };

// --max-orders and --max-levels, the capacities every book reads, each from 1 to 2^32 - 1. A
// value past 32 bits would wrap, and a book for 0 orders sizes its hash index at one slot,
// whose shift is then 64 (book_index.hpp, node_book.hpp).
[[nodiscard]] constexpr CapacityOption parse_capacity_option(std::string_view option,
                                                             std::string_view value,
                                                             BookCapacity& capacity) noexcept {
  std::uint32_t* field = nullptr;
  if (option == "--max-orders") {
    field = &capacity.orders;
  } else if (option == "--max-levels") {
    field = &capacity.levels;
  } else {
    return CapacityOption::not_a_capacity;
  }
  std::uint32_t parsed = 0;
  const auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), parsed);
  if (error != std::errc{} || end != value.data() + value.size() || parsed == 0) {
    return CapacityOption::bad_value;
  }
  *field = parsed;
  return CapacityOption::taken;
}

}  // namespace itch::tools
