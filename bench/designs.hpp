// The book designs bench_replay can measure, by name, for --design.
//
// Every arm of the fast book, named index-levels (tools/book_arms.hpp), and the map book on
// a fresh heap or on one HeapAger ages as it goes. Every design runs the same passes over
// the same input, so that one run can put any of them side by side.
#pragma once

#include <cstdint>
#include <format>
#include <optional>
#include <span>
#include <string>
#include <string_view>

#include "book_arms.hpp"
#include "itch/book.hpp"
#include "itch/heap_ager.hpp"
#include "itch/map_book.hpp"

namespace itch::bench {

enum class DesignKind : std::uint8_t { fast, map_fresh, map_aged };

struct Design {
  DesignKind kind = DesignKind::fast;
  tools::BookArm arm;  // Read only for the fast book.
};

[[nodiscard]] inline std::string design_name(const Design& design) {
  switch (design.kind) {
    case DesignKind::map_fresh: return "map-fresh";
    case DesignKind::map_aged: return "map-aged";
    case DesignKind::fast: break;
  }
  return std::format("{}-{}", tools::to_string(design.arm.index),
                     tools::to_string(design.arm.levels));
}

// INDEX-LEVELS, such as paged-flat or chained-flat, or map-fresh or map-aged. An index
// name has no dash, so the first dash ends it.
[[nodiscard]] inline std::optional<Design> parse_design(std::string_view name) {
  if (name == "map-fresh") {
    return Design{.kind = DesignKind::map_fresh, .arm = {}};
  }
  if (name == "map-aged") {
    return Design{.kind = DesignKind::map_aged, .arm = {}};
  }
  const std::size_t dash = name.find('-');
  if (dash == std::string_view::npos) {
    return std::nullopt;
  }
  const std::optional<tools::IndexArm> index = tools::parse_index_arm(name.substr(0, dash));
  const std::optional<tools::LevelsArm> levels = tools::parse_levels_arm(name.substr(dash + 1));
  if (!index || !levels) {
    return std::nullopt;
  }
  return Design{.kind = DesignKind::fast, .arm = {.index = *index, .levels = *levels}};
}

// The map book on an aged heap. The ager is built first, so the heap is aged before the
// book takes anything from it, and the replay calls age_heap() before each message that
// adds an order, outside whatever it times.
class AgedMapBook {
 public:
  explicit AgedMapBook(const HeapAgeing& ageing = {}) : ager_{ageing} {}

  [[nodiscard]] ApplyStatus apply(std::span<const std::byte> payload) {
    return book_.apply(payload);
  }
  [[nodiscard]] TopOfBook best(std::uint16_t locate, Side side) const noexcept {
    return book_.best(locate, side);
  }
  void age_heap() { ager_.step(); }
  // The ballast the ager holds, which the untimed pass's memory figure includes.
  [[nodiscard]] std::uint64_t ager_held_bytes() const noexcept { return ager_.held_bytes(); }

 private:
  HeapAger ager_;
  MapBook book_;
};

}  // namespace itch::bench
