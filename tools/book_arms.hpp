// The book arms a tool can run, by name, for --index and --levels.
//
// with_book_arm() calls a generic callable with the arm's two policy types, so that one
// binary holds every arm and a run picks one at the command line. The default is the
// book's own default arm.
#pragma once

#include <charconv>
#include <cstdint>
#include <optional>
#include <string_view>
#include <system_error>
#include <type_traits>

#include "itch/book.hpp"

namespace itch::tools {

enum class IndexArm : std::uint8_t { hash, paged };
enum class LevelsArm : std::uint8_t { linked, flat };

struct BookArm {
  IndexArm index = std::is_same_v<FastBook<>::Index, HashIndex> ? IndexArm::hash : IndexArm::paged;
  LevelsArm levels =
      std::is_same_v<FastBook<>::LevelArm, LinkedLevels> ? LevelsArm::linked : LevelsArm::flat;
};

[[nodiscard]] constexpr std::string_view to_string(IndexArm arm) noexcept {
  return arm == IndexArm::hash ? "hash" : "paged";
}
[[nodiscard]] constexpr std::string_view to_string(LevelsArm arm) noexcept {
  return arm == LevelsArm::linked ? "linked" : "flat";
}

[[nodiscard]] constexpr std::optional<IndexArm> parse_index_arm(std::string_view name) noexcept {
  if (name == "hash") {
    return IndexArm::hash;
  }
  if (name == "paged") {
    return IndexArm::paged;
  }
  return std::nullopt;
}

[[nodiscard]] constexpr std::optional<LevelsArm> parse_levels_arm(std::string_view name) noexcept {
  if (name == "linked") {
    return LevelsArm::linked;
  }
  if (name == "flat") {
    return LevelsArm::flat;
  }
  return std::nullopt;
}

enum class ArmOption : std::uint8_t { not_an_arm_option, taken, bad_value };

// One option and its value, if it is one every tool that picks an arm takes: --index,
// --levels, and the capacities only an arm reads, --max-pages, --index-bits and
// --max-slots.
[[nodiscard]] constexpr ArmOption parse_arm_option(std::string_view option, std::string_view value,
                                                   BookArm& arm, BookCapacity& capacity) noexcept {
  if (option == "--index" || option == "--levels") {
    const std::optional<IndexArm> index = parse_index_arm(value);
    const std::optional<LevelsArm> levels = parse_levels_arm(value);
    if (option == "--index" && index.has_value()) {
      arm.index = *index;
      return ArmOption::taken;
    }
    if (option == "--levels" && levels.has_value()) {
      arm.levels = *levels;
      return ArmOption::taken;
    }
    return ArmOption::bad_value;
  }
  std::uint32_t* field = nullptr;
  if (option == "--max-pages") {
    field = &capacity.index_pages;
  } else if (option == "--index-bits") {
    field = &capacity.index_bits;
  } else if (option == "--max-slots") {
    field = &capacity.level_slots;
  } else {
    return ArmOption::not_an_arm_option;
  }
  const auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), *field);
  return error == std::errc{} && end == value.data() + value.size() ? ArmOption::taken
                                                                    : ArmOption::bad_value;
}

// Calls run.template operator()<Index, Levels>() for the arm, and returns what it does.
template <class Run>
decltype(auto) with_book_arm(const BookArm& arm, const Run& run) {
  const auto with_levels = [&run, &arm]<class Index> -> decltype(auto) {
    if (arm.levels == LevelsArm::linked) {
      return run.template operator()<Index, LinkedLevels>();
    }
    return run.template operator()<Index, FlatLevels>();
  };
  if (arm.index == IndexArm::hash) {
    return with_levels.template operator()<HashIndex>();
  }
  return with_levels.template operator()<PagedIndex<>>();
}

}  // namespace itch::tools
