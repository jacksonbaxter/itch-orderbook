// The book arms a tool can run, by name, for --index and --levels.
//
// with_book_arm() calls a generic callable with the arm's two policy types, so that one
// binary holds every arm and a run picks one at the command line. The default is the
// book's own default arm.
#pragma once

#include <cstdint>
#include <optional>
#include <string_view>
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

// Calls run.template operator()<Index, Levels>() for the arm, and returns what it does.
template <class Run>
decltype(auto) with_book_arm(const BookArm& arm, const Run& run) {
  const auto with_levels = [&run, &arm]<class Index>() -> decltype(auto) {
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
