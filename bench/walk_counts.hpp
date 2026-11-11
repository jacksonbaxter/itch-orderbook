// The walks of the backtester's simulated levels, counted, for bench_walks.
//
// The engine keeps the simulated orders of one side as a list of levels from the best
// price, each level a FIFO of orders (include/itch/backtest.hpp, SimLevel). Six places walk
// a side's list from its best level, each stopping at the first level past what it wants:
//
//   execution     an E or C on a traded symbol: every level at or better than the executed
//                 order's price, and every simulated order on each.
//   cross         an add or replace on a symbol with a resting simulated order: every level
//                 of the other side the new order reaches, and the orders on each.
//   depletion     an X, D or U on a traded symbol: the level at the order's price, found
//                 from the best, then every simulated order on it.
//   late_add      a late add with a simulated order resting on its symbol: the same find,
//                 then every simulated order on the level found.
//   hidden_print  a Trade message with a simulated order resting on its symbol: the same
//                 find, once on each side.
//   rest          a simulated order joining its side: every level better than its price.
//
// A level step is one level whose price a walk read, the one it stopped at included; an
// order step is one simulated order a walk visited inside a level. A walk of an empty side
// reads the side's head and nothing else, and is counted as a walk of no steps.
//
// The counts come from the engine's own loops: cmake/ItchWalkEngine.cmake copies
// backtest.hpp's engine at build time with a call into WalkCounts at each walk and wherever
// a level or an order joins or leaves a side, and the engine itself has no counter. The
// depth of each side is kept here from those calls, so a walk's depth costs nothing to
// read. Everything is sized in the constructor; nothing allocates after it.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>
#include <vector>

#include "itch/book_types.hpp"

namespace itch::bench {

enum class WalkSite : std::uint8_t { execution, cross, depletion, late_add, hidden_print, rest };

inline constexpr std::size_t kWalkSites = 6;
inline constexpr std::array<std::string_view, kWalkSites> kWalkSiteNames{
    "execution", "cross", "depletion", "late_add", "hidden_print", "rest"};

class WalkCounts {
 public:
  struct Site {
    std::uint64_t walks = 0;
    std::uint64_t nonempty = 0;  // Walks of a side holding at least one level.
    std::uint64_t level_steps = 0;
    std::uint64_t order_steps = 0;
    std::uint64_t orders_on_side = 0;  // Summed over walks: the side's orders at each start.
    std::vector<std::uint64_t> depth;  // Walks by the side's levels at the start.
    std::vector<std::uint64_t> steps;  // Walks by their level steps.
  };

  // `max_levels` bounds the levels on one side, and so a walk's steps: the engine's
  // Config::max_orders, since every level holds at least one order.
  explicit WalkCounts(std::uint32_t max_levels) : levels_(kSideCount, 0), orders_(kSideCount, 0) {
    for (Site& site : sites_) {
      site.depth.assign(std::size_t{max_levels} + 1, 0);
      site.steps.assign(std::size_t{max_levels} + 1, 0);
    }
  }

  // The site of the next find; the engine's find has no site of its own.
  void at(WalkSite site) noexcept { site_ = static_cast<std::size_t>(site); }

  // A walk from the best level of side `key`, at the site last named.
  void begin(std::size_t key) noexcept {
    Site& site = sites_[site_];
    const std::uint32_t levels = levels_[key];
    ++site.walks;
    site.nonempty += levels != 0 ? 1U : 0U;
    site.orders_on_side += orders_[key];
    if (!counted(site.depth, levels)) {
      broken_ = true;
    }
    walking_ = site_;
    walk_steps_ = 0;
    if (!counted(site.steps, 0)) {
      broken_ = true;
    }
  }

  void level() noexcept {
    Site& site = sites_[walking_];
    ++site.level_steps;
    // The walk's place in `steps` moves up by one, so the histogram is right between calls.
    if (walk_steps_ < site.steps.size()) {
      --site.steps[walk_steps_];
    }
    ++walk_steps_;
    if (!counted(site.steps, walk_steps_)) {
      broken_ = true;
    }
  }

  void order() noexcept { ++sites_[walking_].order_steps; }

  void add_level(std::size_t key) noexcept { ++levels_[key]; }
  void add_order(std::size_t key) noexcept { ++orders_[key]; }
  void remove_level(std::size_t key) noexcept { take(levels_[key]); }
  void remove_order(std::size_t key) noexcept { take(orders_[key]); }

  [[nodiscard]] const Site& site(WalkSite site) const noexcept {
    return sites_[static_cast<std::size_t>(site)];
  }
  // False if a side lost a level or an order it did not have, or a depth or a walk went
  // past `max_levels`: then no count here means anything.
  [[nodiscard]] bool consistent() const noexcept { return !broken_; }
  // True when no side holds a level or an order, as at the end of a session.
  [[nodiscard]] bool empty() const noexcept {
    for (std::size_t key = 0; key < kSideCount; ++key) {
      if (levels_[key] != 0 || orders_[key] != 0) {
        return false;
      }
    }
    return true;
  }
  [[nodiscard]] std::uint32_t levels_on(std::size_t key) const noexcept { return levels_[key]; }
  [[nodiscard]] std::uint32_t orders_on(std::size_t key) const noexcept { return orders_[key]; }

 private:
  [[nodiscard]] static bool counted(std::vector<std::uint64_t>& histogram,
                                    std::size_t at) noexcept {
    if (at >= histogram.size()) {
      return false;
    }
    ++histogram[at];
    return true;
  }

  void take(std::uint32_t& count) noexcept {
    if (count == 0) {
      broken_ = true;
      return;
    }
    --count;
  }

  std::array<Site, kWalkSites> sites_;
  std::vector<std::uint32_t> levels_;  // Per side key.
  std::vector<std::uint32_t> orders_;  // Per side key.
  std::size_t site_ = 0;
  std::size_t walking_ = 0;
  std::size_t walk_steps_ = 0;
  bool broken_ = false;
};

}  // namespace itch::bench
