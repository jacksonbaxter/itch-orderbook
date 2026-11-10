// The book arms a tool can run, by name, for --index and --levels.
//
// with_book_arm() calls a generic callable with the arm's two policy types, so that one
// binary holds every arm and a run picks one at the command line. The default is the
// book's own default arm.
//
// hash and paged, linked and flat are the arms the book was designed with. identity,
// chained, flat-scan16 and flat-bisect repeat carteret's layout experiments: the identity
// hash and separate chaining each against hash, and a search that stops walking after 16
// entries or at once against flat's walk of the whole side (book_index.hpp,
// book_levels.hpp).
#pragma once

#include <array>
#include <charconv>
#include <cstdint>
#include <format>
#include <optional>
#include <string>
#include <string_view>
#include <system_error>
#include <type_traits>

#include "itch/book.hpp"

namespace itch::tools {

enum class IndexArm : std::uint8_t { hash, paged, identity, chained };
enum class LevelsArm : std::uint8_t { linked, flat, flat_scan16, flat_bisect };

inline constexpr std::array<std::string_view, 4> kIndexArmNames{"hash", "paged", "identity",
                                                                "chained"};
inline constexpr std::array<std::string_view, 4> kLevelsArmNames{"linked", "flat", "flat-scan16",
                                                                 "flat-bisect"};

struct BookArm {
  IndexArm index = std::is_same_v<FastBook<>::Index, HashIndex> ? IndexArm::hash : IndexArm::paged;
  LevelsArm levels =
      std::is_same_v<FastBook<>::LevelArm, LinkedLevels> ? LevelsArm::linked : LevelsArm::flat;
};

// Each arm's name is the entry of its enumerator's value in the names above.
[[nodiscard]] constexpr std::string_view to_string(IndexArm arm) noexcept {
  std::size_t at = 0;
  for (const std::string_view name : kIndexArmNames) {
    if (at++ == static_cast<std::size_t>(arm)) {
      return name;
    }
  }
  return {};
}
[[nodiscard]] constexpr std::string_view to_string(LevelsArm arm) noexcept {
  std::size_t at = 0;
  for (const std::string_view name : kLevelsArmNames) {
    if (at++ == static_cast<std::size_t>(arm)) {
      return name;
    }
  }
  return {};
}

[[nodiscard]] constexpr std::optional<IndexArm> parse_index_arm(std::string_view name) noexcept {
  std::uint8_t at = 0;
  for (const std::string_view arm : kIndexArmNames) {
    if (name == arm) {
      return static_cast<IndexArm>(at);
    }
    ++at;
  }
  return std::nullopt;
}

[[nodiscard]] constexpr std::optional<LevelsArm> parse_levels_arm(std::string_view name) noexcept {
  std::uint8_t at = 0;
  for (const std::string_view arm : kLevelsArmNames) {
    if (name == arm) {
      return static_cast<LevelsArm>(at);
    }
    ++at;
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

// The arm's two policies as built, with the capacities each reads.
[[nodiscard]] inline std::string describe_arm(const BookArm& arm, const BookCapacity& capacity) {
  std::string index;
  switch (arm.index) {
    case IndexArm::hash: index = std::format("HashIndex for {} orders", capacity.orders); break;
    case IndexArm::identity:
      index = std::format("IdentityHashIndex for {} orders", capacity.orders);
      break;
    case IndexArm::chained:
      index = std::format("ChainedIndex for {} orders", capacity.orders);
      break;
    case IndexArm::paged:
      index =
          std::format("PagedIndex<8>, {} pages of 256, reach 2^{}, a fallback hash for {} orders",
                      capacity.index_pages, capacity.index_bits, capacity.orders);
      break;
  }
  switch (arm.levels) {
    case LevelsArm::linked: return index + "; LinkedLevels";
    case LevelsArm::flat_scan16:
      return std::format("{}; Scan16FlatLevels, {} slots", index, capacity.level_slots);
    case LevelsArm::flat_bisect:
      return std::format("{}; BisectFlatLevels, {} slots", index, capacity.level_slots);
    case LevelsArm::flat: break;
  }
  return std::format("{}; FlatLevels, {} slots", index, capacity.level_slots);
}

// Calls run.template operator()<Index, Levels>() for the arm, and returns what it does.
template <class Run>
decltype(auto) with_book_arm(const BookArm& arm, const Run& run) {
  const auto with_levels = [&run, &arm]<class Index> -> decltype(auto) {
    switch (arm.levels) {
      case LevelsArm::linked: return run.template operator()<Index, LinkedLevels>();
      case LevelsArm::flat_scan16: return run.template operator()<Index, Scan16FlatLevels>();
      case LevelsArm::flat_bisect: return run.template operator()<Index, BisectFlatLevels>();
      case LevelsArm::flat: break;
    }
    return run.template operator()<Index, FlatLevels>();
  };
  switch (arm.index) {
    case IndexArm::hash: return with_levels.template operator()<HashIndex>();
    case IndexArm::identity: return with_levels.template operator()<IdentityHashIndex>();
    case IndexArm::chained: return with_levels.template operator()<ChainedIndex>();
    case IndexArm::paged: break;
  }
  return with_levels.template operator()<PagedIndex<>>();
}

}  // namespace itch::tools
