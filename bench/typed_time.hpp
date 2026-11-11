// Time by message type, for a clock that cannot time one message: the Mac's ticks are
// 41.667 ns, its floor 166.667 ns, and a message of the book is about 45 ns there. So a
// span here is a block of frames, never one message, and the cost of each type comes from
// a fit, not from a reading.
//
// The pass reads the clock once after every `block` frames, and once before the first. The
// blocks tile the pass as typed_counts.hpp's spans do: each runs from the read after the
// previous block to the read after its own, so it holds its frames' walks, their apply()
// calls, one read and the count of its types, and the blocks' ticks add up to the last
// reading less the first, exactly. Each block is counted by type, in the columns below, and
// what a least-squares fit of a block's ticks on its counts needs is summed as the pass goes:
// for every pair of columns the sum over blocks of the product of their counts, and for
// every column the sum of its count times the block's ticks. Those sums are integers, kept
// exactly, so the fit can be worked again from the record alone (scripts/types_report.py).
//
// What the fit gives a type is its marginal cost where it sits in the day: the ticks a
// block takes for each more message of that type, with the other types of the block held.
// It assumes a message's cost depends on its type alone, so the record keeps the sums of
// each quarter of the pass apart, and a type whose cost moves from quarter to quarter shows
// it. Every block holds `block` frames but the last, so the fitted costs times the counts
// add up to the pass less what the short last block leaves out of that.
//
// Nothing platform-specific is in here, so a test drives it with a fake clock and book.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>

#include "itch/reader.hpp"
#include "itch/wire.hpp"
#include "replay.hpp"

namespace itch::bench {

// One column for each type that names or adds an order, one for each of the two trade and
// imbalance types common enough to fit on their own, and one for everything else, frames
// the reader walks past without decoding among them.
inline constexpr std::string_view kTimedTypes = "AFDUECXPIL";
inline constexpr std::size_t kTimedColumns = kTimedTypes.size() + 1;
inline constexpr std::size_t kOtherColumn = kTimedTypes.size();
inline constexpr std::size_t kTimedQuarters = 4;

[[nodiscard]] constexpr std::array<std::uint8_t, 256> timed_columns() noexcept {
  std::array<std::uint8_t, 256> columns{};
  columns.fill(static_cast<std::uint8_t>(kOtherColumn));
  for (std::size_t column = 0; column < kTimedTypes.size(); ++column) {
    columns[static_cast<std::uint8_t>(kTimedTypes[column])] = static_cast<std::uint8_t>(column);
  }
  return columns;
}

inline constexpr std::array<std::uint8_t, 256> kColumnOf = timed_columns();

using BlockCounts = std::array<std::uint32_t, kTimedColumns>;

// What the fit needs from the blocks of one quarter of the pass.
struct BlockSums {
  std::uint64_t blocks = 0;
  std::uint64_t ticks = 0;          // Every block's ticks.
  std::uint64_t ticks_squared = 0;  // Every block's ticks squared, for the fit's residual.
  std::uint64_t under_floor = 0;    // Blocks that took fewer ticks than the timer's floor.
  std::uint64_t most_ticks = 0;     // The longest block.
  std::array<std::uint64_t, kTimedColumns> counts{};  // Each column's messages.
  std::array<std::uint64_t, kTimedColumns> cross{};   // Each column's count times ticks.
  std::array<std::uint64_t, kTimedColumns * kTimedColumns> gram{};  // Count times count.

  void add(const BlockCounts& block, std::uint64_t taken, std::uint64_t floor_ticks) noexcept {
    ++blocks;
    ticks += taken;
    ticks_squared += taken * taken;
    under_floor += taken < floor_ticks ? 1U : 0U;
    most_ticks = most_ticks < taken ? taken : most_ticks;
    std::array<std::uint8_t, kTimedColumns> present{};
    std::size_t found = 0;
    for (std::size_t column = 0; column < kTimedColumns; ++column) {
      if (block[column] != 0) {
        present[found++] = static_cast<std::uint8_t>(column);
      }
    }
    for (std::size_t i = 0; i < found; ++i) {
      const std::uint64_t mine = block[present[i]];
      counts[present[i]] += mine;
      cross[present[i]] += mine * taken;
      for (std::size_t j = 0; j < found; ++j) {
        gram[(present[i] * kTimedColumns) + present[j]] += mine * block[present[j]];
      }
    }
  }
};

struct TypedTime {
  std::uint32_t block = 0;
  std::uint64_t first = 0;  // The reading before the first frame.
  std::uint64_t last = 0;   // And after the last block.
  std::uint64_t frames = 0;
  std::uint64_t messages = 0;
  std::uint64_t rejected = 0;
  std::array<BlockSums, kTimedQuarters> quarters{};

  [[nodiscard]] std::uint64_t whole() const noexcept { return last - first; }
  [[nodiscard]] std::uint64_t block_ticks() const noexcept {
    std::uint64_t out = 0;
    for (const BlockSums& quarter : quarters) {
      out += quarter.ticks;
    }
    return out;
  }
  // The blocks add up to the pass. They must by construction: a block lost, counted twice
  // or started from the wrong reading breaks it.
  [[nodiscard]] bool tiled() const noexcept { return block_ticks() == whole(); }
};

// The pass. `frames` is how many frames the input holds, from a walk before it, so that
// each block goes to the quarter its first frame falls in. Clock is called with no
// arguments for a reading.
template <class Book, class Clock>
[[nodiscard]] TypedTime typed_time_pass(std::span<const std::byte> input, Book& book,
                                        std::uint32_t block, std::uint64_t frames,
                                        std::uint64_t floor_ticks, Clock& clock) {
  TypedTime pass{.block = block};
  BlockCounts counts{};
  std::uint32_t in_block = 0;
  std::size_t quarter = 0;
  FrameReader reader{input};
  pass.first = clock();
  std::uint64_t opened = pass.first;
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    std::size_t column = kOtherColumn;
    if (frame.status == FrameStatus::message) {
      column = kColumnOf[static_cast<std::uint8_t>(read_char(frame.payload.data()))];
      ++pass.messages;
      pass.rejected += is_rejection(book.apply(frame.payload)) ? 1U : 0U;
    }
    ++counts[column];
    if (in_block++ == 0 && frames != 0) {
      quarter = static_cast<std::size_t>((pass.frames * kTimedQuarters) / frames);
      quarter = quarter < kTimedQuarters ? quarter : kTimedQuarters - 1;
    }
    ++pass.frames;
    if (in_block == block) {
      const std::uint64_t now = clock();
      pass.quarters[quarter].add(counts, now - opened, floor_ticks);
      opened = now;
      counts = {};
      in_block = 0;
    }
  }
  if (in_block != 0) {
    const std::uint64_t now = clock();
    pass.quarters[quarter].add(counts, now - opened, floor_ticks);
    opened = now;
  }
  pass.last = opened;
  return pass;
}

}  // namespace itch::bench
