// One market-making session through the backtester, and its attribution report.
//
//     MarkRecorder marks{symbol, capacity};
//     replay(engine, stream, maker, marks);
//     const auto text = format_report({.fills = maker.fills(), .marks = marks, ...});
//
// replay() feeds each real message to the engine, then shows the exchange-side book to the
// mark recorder, so the mark path is the replayed historical book's and the same in every run
// of a switch matrix. The report is plain text, every amount in integer picodollars, so that two
// runs of one configuration can be compared byte for byte.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <format>
#include <span>
#include <string>

#include "itch/backtest.hpp"
#include "itch/book.hpp"
#include "itch/mm/attribution.hpp"
#include "itch/mm/fills.hpp"
#include "itch/mm/ledger.hpp"
#include "itch/mm/marks.hpp"
#include "itch/reader.hpp"

namespace itch::mm {

// A whole BinaryFILE stream through the engine, then finish(). Returns the messages fed.
template <class S, class Book>
std::uint64_t replay(backtest::Engine<Book>& engine, std::span<const std::byte> stream, S& strategy,
                     MarkRecorder& marks) {
  FrameReader reader{stream};
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == FrameStatus::message) {
      engine.feed(frame.payload, strategy);
      marks.observe(engine.exchange_book(), frame.payload);
    }
  }
  engine.finish(strategy);
  return engine.stats().messages;
}

// The amended Rule 605 grid (BENCHMARKS.md, T-MM-1): 50 ms, 1 s, 15 s, 1 min, 5 min.
inline constexpr std::array<Nanos, 5> kMarkoutGrid{50'000'000, 1'000'000'000, 15'000'000'000,
                                                   60'000'000'000, 300'000'000'000};
// The primary horizon, h = 15 s.
inline constexpr Nanos kPrimaryHorizon = 15'000'000'000;

struct ReportInput {
  std::span<const Fill> fills;
  const MarkRecorder* marks = nullptr;
  Window window;
  Nanos horizon = kPrimaryHorizon;
};

[[nodiscard]] inline std::expected<std::string, AttributionError> format_report(
    const ReportInput& input) {
  const MarkPath path = input.marks->path();
  const auto components = attribute(input.fills, path, input.window, input.horizon);
  if (!components) {
    return std::unexpected(components.error());
  }
  std::int64_t shares = 0;
  std::int64_t closing = input.window.opening_position;
  for (const Fill& fill : input.fills) {
    shares += fill.quantity < 0 ? -fill.quantity : fill.quantity;
    closing += fill.quantity;
  }
  const auto ledger = Ledger::total_of(input.fills, input.window.opening_position,
                                       path.at(input.window.open).value_or(0),
                                       path.at(input.window.close).value_or(0));
  std::string out = "market maker attribution, amounts in picodollars\n";
  out += std::format("window_open_ns {}\nwindow_close_ns {}\nopening_position {}\n",
                     input.window.open, input.window.close, input.window.opening_position);
  out += std::format("fills {}\nshares {}\nclosing_position {}\n", input.fills.size(), shares,
                     closing);
  out += std::format("mark_points {}\nmark_hash {:016x}\nmark_held_ns {}\nmark_overflows {}\n",
                     input.marks->points().size(), input.marks->hash(), input.marks->held_nanos(),
                     input.marks->overflows());
  out += std::format("horizon_ns {}\n", input.horizon);
  out += std::format("gross_spread {}\nfees {}\nspread_capture {}\n", components->gross_spread,
                     components->fees, components->spread_capture());
  out += std::format("adverse_selection {}\ninventory {}\ntotal {}\n",
                     components->adverse_selection, components->inventory, components->total());
  out +=
      ledger ? std::format("ledger_total {}\n", *ledger) : std::string{"ledger_total overflow\n"};
  out += std::format("identity {}\n", ledger && *ledger == components->total() ? "holds" : "FAILS");
  for (const Nanos horizon : kMarkoutGrid) {
    const auto at = attribute(input.fills, path, input.window, horizon);
    if (!at) {
      return std::unexpected(at.error());
    }
    out += std::format("markout_ns {} adverse_selection {} inventory {}\n", horizon,
                       at->adverse_selection, at->inventory);
  }
  return out;
}

}  // namespace itch::mm
