// Counts the walks of the backtester's simulated levels on T-BT-3's load, for any schedule
// of shadow orders. Untimed: nothing here is a time, and the counts are the same on any host.
//
//     bench_walks --input FILE --out RUN.json --date YYYYMMDD [--symbol SYM]...
//                 [--shadow-orders N] [--shadow-span-ns N] [--shadow-life-ns N]
//                 [--commit TEXT] [--dataset NAME] [--dataset-sha256 HEX]
//
// The input and the engine are bench_backtest's shadow load (backtest_extract.hpp): the
// extract of the symbols, every latency zero, and the ShadowPlacer of backtest_workloads.hpp
// with the schedule given. The extract runs twice, each time with a fresh placer:
//
//   1. through itch::backtest::Engine, as bench_backtest times it;
//   2. through the copy cmake/ItchWalkEngine.cmake makes of that engine, with a count at
//      every walk (walk_counts.hpp), fed one message at a time so that the resting simulated
//      orders can be read after each.
//
// The two must agree on every field of Stats, the event hash and the placer's counts, the
// counts must be consistent, and every side must be empty at the end; otherwise the run is
// refused. scripts/walk_report.py reads the records.
//
// Exit codes: 0 counted, 3 refused, with the record still written, 2 bad usage.
#include <algorithm>
#include <array>
#include <bit>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <format>
#include <fstream>
#include <memory>
#include <optional>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include "backtest_extract.hpp"
#include "backtest_workloads.hpp"
#include "itch/backtest.hpp"
#include "itch/reader.hpp"
#include "mapped_file.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"
#include "run_record.hpp"
#include "walk_counts.hpp"
#include "walk_engine.hpp"

namespace {

using namespace itch::bench;
using itch::backtest::Nanos;
using itch::backtest::Stats;

// Stats is all counters; a new one changes its size and stops the build here, so that the
// comparison below never leaves it out.
constexpr std::size_t kStatsFields = 23;
static_assert(sizeof(Stats) == kStatsFields * sizeof(std::uint64_t));
using StatsWords = std::array<std::uint64_t, kStatsFields>;

struct Options {
  std::string input;
  std::string out;
  std::string commit;
  std::string dataset;
  std::string dataset_sha256;
  std::uint32_t date = 0;
  std::uint32_t shadow_orders = 100'000;
  Nanos shadow_span = ShadowPlacer::kSpan;
  Nanos shadow_life = ShadowPlacer::kLife;
  std::vector<itch::Stock> symbols;
};

[[nodiscard]] std::optional<itch::Stock> parse_symbol(std::string_view text) {
  itch::Stock stock{' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '};
  if (text.empty() || text.size() > stock.size()) {
    return std::nullopt;
  }
  std::ranges::copy(text, stock.begin());
  return stock;
}

template <class Number>
[[nodiscard]] bool parse_number(std::string_view text, Number& value) {
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
  return error == std::errc{} && end == text.data() + text.size();
}

[[nodiscard]] bool take(Options& options, std::string_view flag, std::string_view value) {
  if (flag == "--input") {
    options.input = value;
  } else if (flag == "--out") {
    options.out = value;
  } else if (flag == "--commit") {
    options.commit = value;
  } else if (flag == "--dataset") {
    options.dataset = value;
  } else if (flag == "--dataset-sha256") {
    options.dataset_sha256 = value;
  } else if (flag == "--date") {
    return value.size() == 8 && parse_number(value, options.date);
  } else if (flag == "--shadow-orders") {
    return parse_number(value, options.shadow_orders) && options.shadow_orders != 0;
  } else if (flag == "--shadow-span-ns") {
    return parse_number(value, options.shadow_span) && options.shadow_span != 0;
  } else if (flag == "--shadow-life-ns") {
    return parse_number(value, options.shadow_life) && options.shadow_life != 0;
  } else if (flag == "--symbol") {
    const std::optional<itch::Stock> symbol = parse_symbol(value);
    if (symbol) {
      options.symbols.push_back(*symbol);
    }
    return symbol.has_value();
  } else {
    return false;
  }
  return true;
}

[[nodiscard]] bool parse(std::span<char*> args, Options& options) {
  for (std::size_t i = 0; i + 1 < args.size(); i += 2) {
    if (!take(options, args[i], args[i + 1])) {
      return false;
    }
  }
  if (args.size() % 2 != 0 || options.input.empty() || options.out.empty() || options.date == 0) {
    return false;
  }
  if (options.symbols.empty()) {
    for (const std::string_view name : {"AAPL", "MSFT", "SPY", "TSLA"}) {
      options.symbols.push_back(parse_symbol(name).value_or(itch::Stock{}));
    }
  }
  return true;
}

[[nodiscard]] std::string symbol_list(const std::vector<itch::Stock>& symbols) {
  std::string text;
  for (const itch::Stock& stock : symbols) {
    std::string name{stock.begin(), stock.end()};
    name.erase(name.find_last_not_of(' ') + 1);
    text += (text.empty() ? "" : " ") + name;
  }
  return text;
}

// What one pass did, for the two passes' agreement and the record.
struct Pass {
  StatsWords stats{};
  std::uint64_t event_hash = 0;
  ShadowPlacer::Counts placer;
  std::uint64_t resting_sum = 0;  // Resting simulated orders after each message, summed.
  std::uint64_t resting_max = 0;

  [[nodiscard]] bool same_as(const Pass& other) const noexcept {
    const ShadowPlacer::Counts& a = placer;
    const ShadowPlacer::Counts& b = other.placer;
    return stats == other.stats && event_hash == other.event_hash && a.events == b.events &&
           a.placed == b.placed && a.skipped == b.skipped && a.cancels == b.cancels &&
           a.fills == b.fills && a.filled_shares == b.filled_shares && a.reports == b.reports &&
           a.wakes == b.wakes;
  }
};

[[nodiscard]] ShadowPlacer placer_for(const Options& options) {
  return ShadowPlacer{options.symbols, options.shadow_orders, options.shadow_span,
                      options.shadow_life};
}

[[nodiscard]] Pass engine_pass(const Options& options, std::span<const std::byte> input) {
  const auto engine = std::make_unique<itch::backtest::Engine<>>(
      backtest_config(options.date, options.symbols, true));
  ShadowPlacer placer = placer_for(options);
  static_cast<void>(engine->run(input, placer));
  return {.stats = std::bit_cast<StatsWords>(engine->stats()),
          .event_hash = engine->event_hash(),
          .placer = placer.counts()};
}

using Counted = itch::backtest::counted::Engine<>;

// The counted engine, fed as Engine::run() feeds, with the resting orders read after each
// message. Returns the engine for its counts.
[[nodiscard]] std::unique_ptr<Counted> counted_pass(const Options& options,
                                                    std::span<const std::byte> input, Pass& pass) {
  auto engine = std::make_unique<Counted>(backtest_config(options.date, options.symbols, true));
  ShadowPlacer placer = placer_for(options);
  itch::FrameReader reader{input};
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == itch::FrameStatus::message) {
      engine->feed(frame.payload, placer);
      const std::uint64_t resting = engine->resting_orders();
      pass.resting_sum += resting;
      pass.resting_max = std::max(pass.resting_max, resting);
    }
  }
  engine->finish(placer);
  pass.stats = std::bit_cast<StatsWords>(engine->stats());
  pass.event_hash = engine->event_hash();
  pass.placer = placer.counts();
  return engine;
}

// The nonzero entries of a histogram, as [[value, walks], ...].
[[nodiscard]] std::string histogram_json(const std::vector<std::uint64_t>& histogram) {
  std::string json = "[";
  bool first = true;
  for (std::size_t value = 0; value < histogram.size(); ++value) {
    if (histogram[value] != 0) {
      json += std::format("{}[{}, {}]", first ? "" : ", ", value, histogram[value]);
      first = false;
    }
  }
  return json + "]";
}

[[nodiscard]] std::string sites_json(const WalkCounts& walks) {
  std::string json = "{";
  for (std::size_t index = 0; index < kWalkSites; ++index) {
    const WalkCounts::Site& site = walks.site(static_cast<WalkSite>(index));
    json +=
        std::format(R"({}{}: {{"walks": {}, "nonempty": {}, "level_steps": {}, "order_steps": {}, )"
                    R"("orders_on_side": {}, "depth": {}, "steps": {}}})",
                    index == 0 ? "" : ", ", json_string(kWalkSiteNames[index]), site.walks,
                    site.nonempty, site.level_steps, site.order_steps, site.orders_on_side,
                    histogram_json(site.depth), histogram_json(site.steps));
  }
  return json + "}";
}

[[nodiscard]] std::string record_json(const Options& options, const Extract& cut, const Pass& plain,
                                      const Pass& counted, const WalkCounts& walks,
                                      const std::vector<std::string>& refusals) {
  const SysSource source = live_source();
  const std::string cpu = read_cpu_model(source);
  const auto stats = std::bit_cast<Stats>(counted.stats);
  const ShadowPlacer::Counts& placer = counted.placer;
  std::string json = std::format(
      R"({{"header": {{"host": {}, "cpu": {}, "kernel": {}, "compiler": {}, "flags": {}, )"
      R"("commit": {}, "dataset": {}, "dataset_sha256": {}}}, )"
      R"("symbols": {}, "date": {}, "shadow_orders": {}, "shadow_span_ns": {}, )"
      R"("shadow_life_ns": {}, "input_messages": {}, )"
      R"("engine": {{"messages": {}, "deliveries": {}, "arrivals": {}, "reports": {}, )"
      R"("wakes": {}, "fills": {}, "crossing_fills": {}, "expired": {}, "placed": {}, )"
      R"("skipped": {}, "cancels": {}, "event_hash": "{:016x}"}}, )"
      R"("resting_after_message": {{"sum": {}, "max": {}}}, "identical": {}, )"
      R"("consistent": {}, "empty_at_end": {}, "sites": {}, "peak_rss_bytes": {}, )"
      R"("refusals": [)",
      json_string(host_label(source.os, cpu)), json_string(cpu), json_string(read_kernel()),
      json_string(__VERSION__), json_string(ITCH_BENCH_FLAGS), json_string(options.commit),
      json_string(options.dataset), json_string(options.dataset_sha256),
      json_string(symbol_list(options.symbols)), options.date, options.shadow_orders,
      options.shadow_span, options.shadow_life, cut.input_messages, stats.messages,
      stats.deliveries, stats.arrivals, placer.reports, placer.wakes, stats.fills,
      stats.crossing_fills, stats.expired, placer.placed, placer.skipped, placer.cancels,
      counted.event_hash, counted.resting_sum, counted.resting_max,
      plain.same_as(counted) ? "true" : "false", walks.consistent() ? "true" : "false",
      walks.empty() ? "true" : "false", sites_json(walks), peak_rss_bytes());
  for (std::size_t i = 0; i < refusals.size(); ++i) {
    json += (i == 0 ? "" : ", ") + json_string(refusals[i]);
  }
  return json + "]}\n";
}

int run(int argc, char** argv) {
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: bench_walks --input FILE --out RUN.json --date YYYYMMDD [--symbol SYM]... "
                 "[--shadow-orders N] [--shadow-span-ns N] [--shadow-life-ns N] [--commit TEXT] "
                 "[--dataset NAME] [--dataset-sha256 HEX]");
    return 2;
  }
  if (itch::find_fee_schedule(options.date) == nullptr) {
    std::println(stderr, "bench_walks: no fee schedule for {}", options.date);
    return 2;
  }
  const auto file = itch::tools::MappedFile::open(options.input.c_str());
  if (!file) {
    std::println(stderr, "bench_walks: cannot read {}", options.input);
    return 2;
  }
  const TimerInfo timer = measure_timer();
  const Extract cut = extract(file->bytes(), options.symbols, timer);
  if (!cut.arena || cut.bytes == 0) {
    std::println(stderr, "bench_walks: no arena for the extract, or an empty extract");
    return 2;
  }
  const std::span<const std::byte> input{cut.arena->data(), cut.bytes};

  const Pass plain = engine_pass(options, input);
  Pass counted;
  const std::unique_ptr<Counted> engine = counted_pass(options, input, counted);
  const WalkCounts& walks = engine->walks();

  std::vector<std::string> refusals;
  if (!plain.same_as(counted)) {
    refusals.emplace_back("the counted engine and the engine disagree");
  }
  if (!walks.consistent()) {
    refusals.emplace_back("a side lost a level or an order it did not hold, or a count overran");
  }
  if (!walks.empty()) {
    refusals.emplace_back("a side still holds a simulated order at the end");
  }
  const auto stats = std::bit_cast<Stats>(counted.stats);
  if (stats.refused_messages != 0 || stats.view_refused != 0 || stats.overflows != 0 ||
      stats.no_order_slot != 0 || stats.fee_errors != 0) {
    refusals.emplace_back(
        "a book refused a message, a ring overflowed, no slot was free or a "
        "fee failed");
  }

  std::ofstream out{options.out};
  out << record_json(options, cut, plain, counted, walks, refusals);
  if (!out) {
    std::println(stderr, "bench_walks: cannot write {}", options.out);
    return 2;
  }
  for (const std::string& refusal : refusals) {
    std::println(stderr, "bench_walks: refused: {}", refusal);
  }
  return refusals.empty() ? 0 : 3;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    return run(argc, argv);
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
