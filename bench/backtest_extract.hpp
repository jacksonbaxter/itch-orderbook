// T-BT-3's input and engine, shared by bench_backtest, which times them, and bench_walks,
// which counts the walks of the simulated levels on them.
//
// The extract is the messages of the traded symbols and every System Event, cut from a day
// in two walks of it, one to count and one to copy, into one arena of anonymous memory,
// framed as in the file. backtest_config() is the engine's configuration for both loads.
#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <utility>
#include <vector>

#include "itch/backtest.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"
#include "platform/memory.hpp"
#include "platform/timer.hpp"

namespace itch::bench {

// The messages of the symbols and every System Event, framed as in the file, in one arena.
struct Extract {
  std::optional<Arena> arena;
  std::uint64_t input_messages = 0;
  std::uint64_t messages = 0;
  std::uint64_t bytes = 0;
  double ns = 0.0;
};

[[nodiscard]] inline bool kept(std::span<const std::byte> payload,
                               std::vector<std::uint8_t>& locates,
                               const std::vector<Stock>& symbols) {
  const std::byte* m = payload.data();
  const char type = read_char(m);
  const auto locate = read_be<std::uint16_t>(m + 1);
  if (type == StockDirectory::kType) {
    const StockDirectory directory = StockDirectory::from_wire(m);
    locates[locate] = std::ranges::find(symbols, directory.stock) != symbols.end() ? 1U : 0U;
  }
  return type == SystemEvent::kType || locates[locate] != 0;
}

[[nodiscard]] inline Extract extract(std::span<const std::byte> day,
                                     const std::vector<Stock>& symbols, const TimerInfo& timer) {
  Extract out;
  const std::uint64_t start = ticks_start();
  std::vector<std::uint8_t> locates(std::size_t{1} << 16U, 0);
  std::byte* base = nullptr;  // The arena's, from the second pass.
  for (int pass = 0; pass < 2; ++pass) {
    std::size_t at = 0;
    FrameReader reader{day};
    for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
      if (frame.status != FrameStatus::message) {
        continue;
      }
      out.input_messages += pass == 0 ? 1U : 0U;
      if (!kept(frame.payload, locates, symbols)) {
        continue;
      }
      const std::size_t framed = frame.payload.size() + 2;
      if (pass == 0) {
        ++out.messages;
        out.bytes += framed;
        continue;
      }
      // The two-byte big-endian length, then the payload.
      base[at] = static_cast<std::byte>(frame.payload.size() >> 8U);
      base[at + 1] = static_cast<std::byte>(frame.payload.size() & 0xFFU);
      std::ranges::copy(frame.payload, base + at + 2);
      at += framed;
    }
    if (pass == 0) {
      std::optional<Arena> made = Arena::make(out.bytes, PageHint::base, Prefault::now);
      if (!made) {
        return out;
      }
      base = made->data();
      out.arena.emplace(std::move(*made));
    }
  }
  out.ns = timer.ticks_to_ns(static_cast<double>(ticks_stop() - start));
  return out;
}

// The engine for either load. `zero_latency` is the shadow load's: every latency zero.
[[nodiscard]] inline backtest::Config backtest_config(std::uint32_t date,
                                                      const std::vector<Stock>& symbols,
                                                      bool zero_latency) {
  backtest::Config config;
  config.date = date;
  config.symbols = symbols;
  if (zero_latency) {
    config.latency =
        backtest::Latency{.market_data = 0, .order_entry = 0, .response = 0, .compute = 0};
  }
  // The view book holds only the traded symbols. A reference past its pages falls back to
  // the hash table, slower and never wrong. As itch_backtest sizes it.
  config.view_capacity = {.orders = 1U << 20U,
                          .levels = 1U << 16U,
                          .index_pages = 1U << 15U,
                          .index_bits = 30,
                          .level_slots = 1U << 18U};
  config.max_orders = 1U << 17U;
  config.queue_capacity = 1U << 17U;
  return config;
}

}  // namespace itch::bench
