// A whole day through the backtester, with a trivial strategy trading on the named
// symbols, to show the engine runs end to end on real data. It reports counts and checks,
// never a time: it is not a benchmark, and T-BT-1 and T-BT-3 have their own harness.
//
//     itch_backtest FILE --date YYYYMMDD [--symbol SYM]... [--no-plain-check] [--small]
//
// The strategy is tests/backtest/joiner.hpp: it joins the view book's best bid and ask with
// 100 shares, follows them, cancels and takes now and then, and has no logic worth a PnL.
// The default symbols are AAPL, MSFT, SPY and TSLA. --small sizes every book for a fixture,
// 4,096 orders, instead of a day. The checks, each printed as a line:
//
//   - allocations: none between the end of construction and the end of finish();
//   - replay: the exchange-side book equals, byte for byte, a plain fast book that replayed
//     the same file with no simulated orders (skipped by --no-plain-check);
//   - the engine refused no real message, its view book refused no delivered one, no ring
//     overflowed and no fee failed.
//
// Exit codes: 0 every check passed, 1 a check failed, 2 bad usage or an unreadable file.
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <optional>
#include <print>
#include <span>
#include <string_view>
#include <system_error>
#include <vector>

#include "backtest/joiner.hpp"
#include "itch/backtest.hpp"
#include "itch/book.hpp"
#include "itch/reader.hpp"
#include "mapped_file.hpp"
#include "oracle/alloc_counter.hpp"

namespace {

struct Options {
  const char* path = nullptr;
  std::uint32_t date = 0;
  std::vector<itch::Stock> symbols;
  bool plain_check = true;
  bool small = false;
};

constexpr itch::BookCapacity kSmall{
    .orders = 4096, .levels = 4096, .index_pages = 8, .index_bits = 16, .level_slots = 8192};

[[nodiscard]] std::optional<std::uint32_t> parse_date(std::string_view text) {
  std::uint32_t value = 0;
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
  if (error != std::errc{} || end != text.data() + text.size() || text.size() != 8) {
    return std::nullopt;
  }
  return value;
}

[[nodiscard]] std::optional<itch::Stock> parse_symbol(std::string_view text) {
  itch::Stock stock{' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '};
  if (text.empty() || text.size() > stock.size()) {
    return std::nullopt;
  }
  for (std::size_t i = 0; i < text.size(); ++i) {
    stock[i] = text[i];
  }
  return stock;
}

[[nodiscard]] std::optional<Options> parse_options(std::span<const char* const> args) {
  Options options;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string_view arg = args[i];
    if (arg == "--no-plain-check" || arg == "--small") {
      (arg == "--small" ? options.small : options.plain_check) = arg == "--small";
      continue;
    }
    if (arg == "--date" || arg == "--symbol") {
      if (i + 1 == args.size()) {
        return std::nullopt;
      }
      const std::string_view value = args[++i];
      if (arg == "--date") {
        const auto date = parse_date(value);
        if (!date) {
          return std::nullopt;
        }
        options.date = *date;
      } else {
        const auto symbol = parse_symbol(value);
        if (!symbol) {
          return std::nullopt;
        }
        options.symbols.push_back(*symbol);
      }
      continue;
    }
    if (arg.starts_with("--") || options.path != nullptr) {
      return std::nullopt;
    }
    options.path = args[i];
  }
  if (options.path == nullptr || options.date == 0) {
    return std::nullopt;
  }
  if (options.symbols.empty()) {
    for (const std::string_view name : {"AAPL", "MSFT", "SPY", "TSLA"}) {
      options.symbols.push_back(parse_symbol(name).value_or(itch::Stock{}));
    }
  }
  return options;
}

void check(bool passed, std::string_view name, int& failed) {
  std::println("check {}: {}", name, passed ? "pass" : "FAIL");
  failed += passed ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
  const auto options = parse_options(std::span<const char* const>{argv + 1, argv + argc});
  if (!options) {
    std::println(stderr,
                 "usage: itch_backtest FILE --date YYYYMMDD [--symbol SYM]... [--no-plain-check] "
                 "[--small]");
    return 2;
  }
  const auto file = itch::tools::MappedFile::open(options->path);
  if (!file) {
    std::println(stderr, "itch_backtest: cannot read {}", options->path);
    return 2;
  }
  itch::backtest::Config config;
  config.date = options->date;
  config.symbols = options->symbols;
  // The view book holds only the traded symbols, so it needs far less than a whole day.
  // A reference past its pages falls back to the hash table, slower and never wrong.
  config.view_capacity = {.orders = 1U << 20U,
                          .levels = 1U << 16U,
                          .index_pages = 1U << 15U,
                          .index_bits = 30,
                          .level_slots = 1U << 18U};
  const itch::BookCapacity day{};
  if (options->small) {
    config.exchange_capacity = kSmall;
    config.view_capacity = kSmall;
  }
  itch::backtest::Engine<> engine{config};
  if (!engine.ready()) {
    std::println(stderr, "itch_backtest: no fee schedule for {}", options->date);
    return 2;
  }
  itch::backtest::synthetic::Joiner joiner;

  itch::oracle::reset_alloc_counts();
  const std::uint64_t messages = engine.run(file->bytes(), joiner);
  const std::uint64_t allocations = itch::oracle::read_alloc_counts().total();

  const auto& stats = engine.stats();
  const auto& made = joiner.counts();
  std::println("messages {} deliveries {} out_of_order {}", messages, stats.deliveries,
               stats.out_of_order);
  std::println("decisions {} arrivals {} rejected {} expired {} no_order_slot {} after_end {}",
               stats.decisions, stats.arrivals, stats.rejected, stats.expired, stats.no_order_slot,
               stats.after_end);
  std::println("strategy sends {} replaces {} cancels {} takes {} fills {} rejects {}", made.sends,
               made.replaces, made.cancels, made.takes, made.fills, made.rejects);
  std::println("fills {} shares {} maker {} taker {} crossing {}", stats.fills, stats.filled_shares,
               stats.maker_fills, stats.taker_fills, stats.crossing_fills);
  std::println("queue_anomalies {} hidden_prints_at_sim_price {} broken_trades {}",
               stats.queue_anomalies, stats.hidden_prints_at_sim_price, stats.broken_trades);
  std::println("event_hash {:016x}", engine.event_hash());
  std::println("allocations_after_construction {}", allocations);

  int failed = 0;
  check(allocations == 0, "allocations", failed);
  check(stats.refused_messages == 0, "no refused message", failed);
  check(stats.view_refused == 0, "no refused delivery", failed);
  check(stats.overflows == 0, "no ring overflow", failed);
  check(stats.fee_errors == 0, "no fee error", failed);
  check(stats.fills != 0, "simulated orders traded", failed);
  if (options->plain_check) {
    itch::FastBook<> plain{options->small ? kSmall : day};
    itch::FrameReader reader{file->bytes()};
    for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status);
         frame = reader.next()) {
      if (frame.status == itch::FrameStatus::message) {
        static_cast<void>(plain.apply(frame.payload));
      }
    }
    check(engine.exchange_book() == plain, "replay identical to a plain book", failed);
  }
  return failed == 0 ? 0 : 1;
}
