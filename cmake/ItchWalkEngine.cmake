# Makes bench_walks' engine: a copy of include/itch/backtest.hpp's Engine with a call into
# itch::bench::WalkCounts (bench/walk_counts.hpp) at every walk of the simulated levels and
# wherever a level or an order joins or leaves a side. Run at build time, in script mode:
#
#     cmake -DSOURCE=include/itch/backtest.hpp -DOUTPUT=walk_engine.hpp -P ItchWalkEngine.cmake
#
# The copy is the engine's text from `template <class Book> class Engine;` to the end of its
# namespace, in itch::backtest::counted, so every type around the engine is the engine's own.
# Each edit below names its place by text that must occur exactly once, and the loops over
# the simulated levels are counted first, so a change to the engine that moves a place or
# adds a walk stops the build here instead of leaving a walk uncounted. bench_walks then
# runs this copy beside the engine and refuses unless both give the same events.

if(NOT SOURCE OR NOT OUTPUT)
  message(FATAL_ERROR "ItchWalkEngine.cmake needs -DSOURCE=backtest.hpp and -DOUTPUT=file")
endif()
file(READ "${SOURCE}" source)

# Every loop over a side's levels or a level's orders. Eight: number_real, execution (two),
# depletion, cross (two), find_sim_level and rest.
set(itch_walk_loops 8)
# Each loop becomes one word and the words are counted: a match ends in `[`, which a CMake
# list would not split at.
string(REGEX REPLACE [=[for \(std::uint32_t [a-z_]+ = (sim_heads_|levels_)\[|while \(worse != kNoIndex]=]
  "ITCH_WALK_LOOP" marked "${source}")
string(REGEX MATCHALL "ITCH_WALK_LOOP" loops "${marked}")
list(LENGTH loops found)
if(NOT found EQUAL itch_walk_loops)
  message(FATAL_ERROR
    "backtest.hpp has ${found} loops over the simulated levels, not ${itch_walk_loops}: "
    "count the new one in cmake/ItchWalkEngine.cmake and bench/walk_counts.hpp")
endif()

set(first [=[
template <class Book>
class Engine;
]=])
set(last "}  // namespace itch::backtest\n")
string(FIND "${source}" "${first}" begin_at)
string(FIND "${source}" "${last}" end_at REVERSE)
if(begin_at EQUAL -1 OR end_at EQUAL -1 OR end_at LESS begin_at)
  message(FATAL_ERROR "backtest.hpp: the engine's text was not found")
endif()
math(EXPR length "${end_at} - ${begin_at}")
string(SUBSTRING "${source}" ${begin_at} ${length} engine)

# Replaces `anchor`, which must occur exactly once, with `replacement`.
function(itch_walk_edit anchor replacement)
  string(FIND "${engine}" "${anchor}" at)
  string(FIND "${engine}" "${anchor}" at_last REVERSE)
  if(at EQUAL -1)
    message(FATAL_ERROR "backtest.hpp: not found:\n${anchor}")
  endif()
  if(NOT at EQUAL at_last)
    message(FATAL_ERROR "backtest.hpp: found more than once:\n${anchor}")
  endif()
  string(REPLACE "${anchor}" "${replacement}" edited "${engine}")
  set(engine "${edited}" PARENT_SCOPE)
endfunction()

# The counts, their accessor and their size.
itch_walk_edit([=[
  [[nodiscard]] std::uint64_t resting_orders() const noexcept { return resting_total_; }
]=] [=[
  [[nodiscard]] std::uint64_t resting_orders() const noexcept { return resting_total_; }
  [[nodiscard]] const itch::bench::WalkCounts& walks() const noexcept { return walk_; }
]=])
itch_walk_edit([=[
        reports_{config.queue_capacity} {
]=] [=[
        reports_{config.queue_capacity},
        walk_{config.max_orders} {
]=])
itch_walk_edit([=[
  EventHash hash_;
};
]=] [=[
  EventHash hash_;
  mutable itch::bench::WalkCounts walk_;  // Mutable: the finds are const.
};
]=])

# number_real: a late add's find, then the orders of the level found.
itch_walk_edit([=[
    const std::uint32_t level = late && model_ == QueueModel::exact && resting_[locate] != 0
]=] [=[
    walk_.at(itch::bench::WalkSite::late_add);
    const std::uint32_t level = late && model_ == QueueModel::exact && resting_[locate] != 0
]=])
itch_walk_edit([=[
      for (std::uint32_t sim = levels_[level].front; sim != kNoIndex; sim = orders_[sim].newer) {
]=] [=[
      for (std::uint32_t sim = levels_[level].front; sim != kNoIndex; sim = orders_[sim].newer) {
        walk_.order();
]=])

# execution.
itch_walk_edit([=[
    for (std::uint32_t level = sim_heads_[side_key(locate, real.side)]; level != kNoIndex;
         level = next_level) {
]=] [=[
    walk_.at(itch::bench::WalkSite::execution);
    walk_.begin(side_key(locate, real.side));
    for (std::uint32_t level = sim_heads_[side_key(locate, real.side)]; level != kNoIndex;
         level = next_level) {
      walk_.level();
]=])
itch_walk_edit([=[
      const bool reached = !better(real.side, traded, price);
      std::uint32_t next = kNoIndex;
      for (std::uint32_t slot = levels_[level].front; slot != kNoIndex; slot = next) {
]=] [=[
      const bool reached = !better(real.side, traded, price);
      std::uint32_t next = kNoIndex;
      for (std::uint32_t slot = levels_[level].front; slot != kNoIndex; slot = next) {
        walk_.order();
]=])

# depletion: a find, then the orders of the level found.
itch_walk_edit([=[
    const std::uint32_t level = find_sim_level(locate, real.side, real.price);
]=] [=[
    walk_.at(itch::bench::WalkSite::depletion);
    const std::uint32_t level = find_sim_level(locate, real.side, real.price);
]=])
itch_walk_edit([=[
    for (std::uint32_t slot = levels_[level].front; slot != kNoIndex; slot = orders_[slot].newer) {
]=] [=[
    for (std::uint32_t slot = levels_[level].front; slot != kNoIndex; slot = orders_[slot].newer) {
      walk_.order();
]=])

# cross.
itch_walk_edit([=[
    for (std::uint32_t level = sim_heads_[side_key(locate, sim_side)]; level != kNoIndex;
         level = next_level) {
]=] [=[
    walk_.at(itch::bench::WalkSite::cross);
    walk_.begin(side_key(locate, sim_side));
    for (std::uint32_t level = sim_heads_[side_key(locate, sim_side)]; level != kNoIndex;
         level = next_level) {
      walk_.level();
]=])
itch_walk_edit([=[
      for (std::uint32_t slot = levels_[level].front; slot != kNoIndex; slot = next) {
        next = orders_[slot].newer;
        const SimOrder& sim = orders_[slot];
]=] [=[
      for (std::uint32_t slot = levels_[level].front; slot != kNoIndex; slot = next) {
        walk_.order();
        next = orders_[slot].newer;
        const SimOrder& sim = orders_[slot];
]=])

# find_sim_level, at the site its caller named; has_sim_level's caller is hidden_print.
itch_walk_edit([=[
                                             Price4 price) const noexcept {
    for (std::uint32_t level = sim_heads_[side_key(locate, side)]; level != kNoIndex;
         level = levels_[level].worse) {
]=] [=[
                                             Price4 price) const noexcept {
    walk_.begin(side_key(locate, side));
    for (std::uint32_t level = sim_heads_[side_key(locate, side)]; level != kNoIndex;
         level = levels_[level].worse) {
      walk_.level();
]=])
itch_walk_edit([=[
  [[nodiscard]] bool has_sim_level(std::uint16_t locate, Side side, Price4 price) const noexcept {
]=] [=[
  [[nodiscard]] bool has_sim_level(std::uint16_t locate, Side side, Price4 price) const noexcept {
    walk_.at(itch::bench::WalkSite::hidden_print);
]=])

# rest: the walk to its place, then a level or an order joining the side.
itch_walk_edit([=[
    std::uint32_t worse = sim_heads_[key];
    std::uint32_t better_level = kNoIndex;
    while (worse != kNoIndex && better(sim.side, levels_[worse].price, sim.price)) {
      better_level = worse;
      worse = levels_[worse].worse;
    }
]=] [=[
    walk_.at(itch::bench::WalkSite::rest);
    walk_.begin(key);
    std::uint32_t worse = sim_heads_[key];
    std::uint32_t better_level = kNoIndex;
    while (worse != kNoIndex && better(sim.side, levels_[worse].price, sim.price)) {
      walk_.level();
      better_level = worse;
      worse = levels_[worse].worse;
    }
    if (worse != kNoIndex) {
      walk_.level();  // The level the loop stopped at.
    }
]=])
itch_walk_edit([=[
    if (level == kNoIndex || levels_[level].price != sim.price) {
      level = free_level_;
]=] [=[
    if (level == kNoIndex || levels_[level].price != sim.price) {
      walk_.add_level(key);
      level = free_level_;
]=])
itch_walk_edit([=[
    ++resting_[sim.locate];
    ++resting_total_;
]=] [=[
    ++resting_[sim.locate];
    ++resting_total_;
    walk_.add_order(key);
]=])

# unrest: an order, and with the last one its level, leaving the side.
itch_walk_edit([=[
    if (at.front == kNoIndex) {
      const std::size_t key = side_key(sim.locate, sim.side);
]=] [=[
    if (at.front == kNoIndex) {
      const std::size_t key = side_key(sim.locate, sim.side);
      walk_.remove_level(key);
]=])
itch_walk_edit([=[
    --resting_[sim.locate];
    --resting_total_;
]=] [=[
    --resting_[sim.locate];
    --resting_total_;
    walk_.remove_order(side_key(sim.locate, sim.side));
]=])

file(WRITE "${OUTPUT}.tmp" "\
// Generated by cmake/ItchWalkEngine.cmake from include/itch/backtest.hpp. Do not edit.
// The engine with a count at every walk of the simulated levels, for bench_walks only.
#pragma once

#include \"itch/backtest.hpp\"
#include \"walk_counts.hpp\"

namespace itch::backtest::counted {

${engine}}  // namespace itch::backtest::counted
")
file(COPY_FILE "${OUTPUT}.tmp" "${OUTPUT}" ONLY_IF_DIFFERENT)
file(REMOVE "${OUTPUT}.tmp")
