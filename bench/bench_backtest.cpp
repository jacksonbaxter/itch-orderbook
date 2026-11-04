// Times the backtester over a day, for T-BT-1 and T-BT-3. One process is one repetition:
// scripts/backtest_driver.py starts a fresh one for each and writes the results file.
//
//     bench_backtest --input FILE --out RUN.json --workload quoting|shadow --date YYYYMMDD
//                    [--symbol SYM]... [--shadow-orders N] [--shadow-span-ns N]
//                    [--shadow-life-ns N] [--core N] [--commit TEXT] [--dataset NAME]
//                    [--dataset-sha256 HEX]
//
// quoting (T-BT-1): the whole day, every message applied to the exchange-side book, the
//   queue model on as it always is, the base latencies of BENCHMARKS.md, and the Quoter of
//   backtest_workloads.hpp trading the symbols. The day is mapped, and every page is read
//   before the timed pass and found resident after it.
// shadow (T-BT-3): queuetruth's workload. Before any timing, the day is walked once and the
//   messages of the symbols, with every System Event, are copied into memory; that extract
//   is all the engine sees. The ShadowPlacer places --shadow-orders orders, with every
//   latency zero, and no strategy runs. --shadow-span-ns and --shadow-life-ns shorten its
//   schedule, for the fixture of the CLI test; a results file uses neither.
//
// Each process runs the workload once untimed, as a warm-up, then once timed on a fresh
// engine and strategy: the span is the engine's run() over the whole input, the frame walk,
// every engine event, the strategy and finish() included, and nothing before or after. The
// warm-up and the timed pass must agree on every count and the event hash, or the run is
// refused. The default symbols are AAPL, MSFT, SPY and TSLA.
//
// Exit codes: 0 publishable, 3 refused, with the record still written, 2 bad usage.
#include <sys/mman.h>

#include <algorithm>
#include <array>
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
#include <utility>
#include <vector>

#include "backtest_workloads.hpp"
#include "itch/backtest.hpp"
#include "itch/reader.hpp"
#include "mapped_file.hpp"
#include "platform/affinity.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"
#include "run_record.hpp"

namespace {

using namespace itch::bench;
using itch::backtest::Config;
using itch::backtest::Engine;
using itch::backtest::Latency;
using itch::backtest::Nanos;
using itch::backtest::Stats;

enum class Workload : std::uint8_t { quoting, shadow };

struct Options {
  std::string input;
  std::string out;
  std::string commit;
  std::string dataset;
  std::string dataset_sha256;
  Workload workload = Workload::quoting;
  bool workload_set = false;
  std::uint32_t date = 0;
  std::uint32_t shadow_orders = 100'000;
  Nanos shadow_span = ShadowPlacer::kSpan;
  Nanos shadow_life = ShadowPlacer::kLife;
  int core = -1;
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
  } else if (flag == "--workload") {
    options.workload_set = value == "quoting" || value == "shadow";
    options.workload = value == "shadow" ? Workload::shadow : Workload::quoting;
    return options.workload_set;
  } else if (flag == "--date") {
    return value.size() == 8 && parse_number(value, options.date);
  } else if (flag == "--shadow-orders") {
    return parse_number(value, options.shadow_orders) && options.shadow_orders != 0;
  } else if (flag == "--shadow-span-ns") {
    return parse_number(value, options.shadow_span) && options.shadow_span != 0;
  } else if (flag == "--shadow-life-ns") {
    return parse_number(value, options.shadow_life) && options.shadow_life != 0;
  } else if (flag == "--core") {
    return parse_number(value, options.core);
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
  if (args.size() % 2 != 0 || options.input.empty() || options.out.empty() ||
      !options.workload_set || options.date == 0) {
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

// The messages of the symbols and every System Event, framed as in the file, in one arena.
struct Extract {
  std::optional<Arena> arena;
  std::uint64_t input_messages = 0;
  std::uint64_t messages = 0;
  std::uint64_t bytes = 0;
  double ns = 0.0;
};

[[nodiscard]] bool kept(std::span<const std::byte> payload, std::vector<std::uint8_t>& locates,
                        const std::vector<itch::Stock>& symbols) {
  const std::byte* m = payload.data();
  const char type = itch::read_char(m);
  const auto locate = itch::read_be<std::uint16_t>(m + 1);
  if (type == itch::StockDirectory::kType) {
    const itch::StockDirectory directory = itch::StockDirectory::from_wire(m);
    locates[locate] = std::ranges::find(symbols, directory.stock) != symbols.end() ? 1U : 0U;
  }
  return type == itch::SystemEvent::kType || locates[locate] != 0;
}

[[nodiscard]] Extract extract(std::span<const std::byte> day,
                              const std::vector<itch::Stock>& symbols, const TimerInfo& timer) {
  Extract out;
  const std::uint64_t start = ticks_start();
  std::vector<std::uint8_t> locates(std::size_t{1} << 16U, 0);
  std::byte* base = nullptr;  // The arena's, from the second pass.
  for (int pass = 0; pass < 2; ++pass) {
    std::size_t at = 0;
    itch::FrameReader reader{day};
    for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status);
         frame = reader.next()) {
      if (frame.status != itch::FrameStatus::message) {
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

// What one pass of the workload did, for the record and the warm-up's agreement.
struct Pass {
  Stats stats;
  std::uint64_t event_hash = 0;
  std::uint64_t strategy_events = 0;
  std::uint64_t strategy_reports = 0;
  std::uint64_t wakes = 0;
  std::uint64_t sends = 0;
  std::uint64_t replaces = 0;
  std::uint64_t cancels = 0;
  std::uint64_t skipped = 0;
  std::uint64_t strategy_fills = 0;
  double ns = 0.0;

  [[nodiscard]] bool same_as(const Pass& other) const noexcept {
    return event_hash == other.event_hash && stats.messages == other.stats.messages &&
           stats.decisions == other.stats.decisions && stats.fills == other.stats.fills &&
           strategy_events == other.strategy_events && wakes == other.wakes;
  }
};

[[nodiscard]] Config engine_config(const Options& options) {
  Config config;
  config.date = options.date;
  config.symbols = options.symbols;
  if (options.workload == Workload::shadow) {
    config.latency = Latency{.market_data = 0, .order_entry = 0, .response = 0, .compute = 0};
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

template <class Strategy>
[[nodiscard]] Pass timed_run(const Options& options, std::span<const std::byte> input,
                             Strategy& strategy) {
  const auto engine = std::make_unique<Engine<>>(engine_config(options));
  Pass pass;
  const std::uint64_t start = ticks_start();
  static_cast<void>(engine->run(input, strategy));
  const std::uint64_t stop = ticks_stop();
  pass.ns = static_cast<double>(stop - start);  // Ticks here; the caller converts.
  pass.stats = engine->stats();
  pass.event_hash = engine->event_hash();
  return pass;
}

[[nodiscard]] Pass run_pass(const Options& options, std::span<const std::byte> input,
                            const TimerInfo& timer) {
  Pass pass;
  if (options.workload == Workload::quoting) {
    Quoter quoter;
    pass = timed_run(options, input, quoter);
    const Quoter::Counts& made = quoter.counts();
    pass.strategy_events = made.events;
    pass.strategy_reports = made.reports;
    pass.sends = made.sends;
    pass.replaces = made.replaces;
    pass.cancels = made.cancels;
    pass.strategy_fills = made.fills;
  } else {
    ShadowPlacer placer{options.symbols, options.shadow_orders, options.shadow_span,
                        options.shadow_life};
    pass = timed_run(options, input, placer);
    const ShadowPlacer::Counts& made = placer.counts();
    pass.strategy_events = made.events;
    pass.strategy_reports = made.reports;
    pass.wakes = made.wakes;
    pass.sends = made.placed;
    pass.cancels = made.cancels;
    pass.skipped = made.skipped;
    pass.strategy_fills = made.fills;
  }
  pass.ns = timer.ticks_to_ns(pass.ns);
  return pass;
}

// Reads every page of an input, to bring back any that were pushed out. The volatile
// store keeps the reads.
void touch(std::span<const std::byte> bytes) {
  std::byte seen{0};
  for (std::size_t at = 0; at < bytes.size(); at += base_page_size()) {
    seen |= bytes[at];
  }
  const volatile std::byte sink = seen;
  static_cast<void>(sink);
}

[[nodiscard]] std::string pass_json(const Pass& pass) {
  const Stats& s = pass.stats;
  return std::format(
      R"({{"ns": {:.0f}, "messages": {}, "deliveries": {}, "arrivals": {}, "decisions": {}, )"
      R"("reports": {}, "wakes": {}, "fills": {}, "filled_shares": {}, "maker_fills": {}, )"
      R"("taker_fills": {}, "crossing_fills": {}, "queue_anomalies": {}, "late_adds": {}, )"
      R"("late_adds_ahead": {}, "hidden_prints_at_sim_price": {}, "rejected": {}, )"
      R"("expired": {}, "after_end": {}, "no_order_slot": {}, "refused_messages": {}, )"
      R"("view_refused": {}, "out_of_order": {}, "overflows": {}, "fee_errors": {}, )"
      R"("strategy_events": {}, "sends": {}, "replaces": {}, "cancels": {}, "skipped": {}, )"
      R"("strategy_fills": {}, "event_hash": "{:016x}"}})",
      pass.ns, s.messages, s.deliveries, s.arrivals, s.decisions, pass.strategy_reports, pass.wakes,
      s.fills, s.filled_shares, s.maker_fills, s.taker_fills, s.crossing_fills, s.queue_anomalies,
      s.late_adds, s.late_adds_ahead, s.hidden_prints_at_sim_price, s.rejected, s.expired,
      s.after_end, s.no_order_slot, s.refused_messages, s.view_refused, s.out_of_order, s.overflows,
      s.fee_errors, pass.strategy_events, pass.sends, pass.replaces, pass.cancels, pass.skipped,
      pass.strategy_fills, pass.event_hash);
}

[[nodiscard]] std::vector<std::string> refusals_of(const Pass& warm, const Pass& timed) {
  std::vector<std::string> out;
  if (!warm.same_as(timed)) {
    out.emplace_back("the warm-up and the timed pass disagree");
  }
  const Stats& s = timed.stats;
  if (s.refused_messages != 0 || s.view_refused != 0) {
    out.emplace_back("a book refused a message");
  }
  if (s.overflows != 0 || s.no_order_slot != 0) {
    out.emplace_back("a ring overflowed or no order slot was free");
  }
  if (s.fee_errors != 0) {
    out.emplace_back("a fee failed");
  }
  if (s.fills == 0) {
    out.emplace_back("no simulated order traded");
  }
  return out;
}

// The input the engine runs on: the extract for the shadow workload, else the mapped day.
struct Input {
  std::optional<Extract> cut;
  std::span<const std::byte> bytes;
  std::string field;
};

[[nodiscard]] Input prepare_input(const Options& options, std::span<const std::byte> day,
                                  const TimerInfo& timer) {
  Input input;
  if (options.workload == Workload::shadow) {
    input.cut.emplace(extract(day, options.symbols, timer));
    const Extract& cut = *input.cut;
    if (cut.arena) {
      input.bytes = {cut.arena->data(), cut.bytes};
      input.field = std::format(
          "extract: the messages of {} and every System Event, {} of the day's {} messages, {} "
          "bytes, copied into anonymous memory before the warm-up, in {:.3f} s that no figure "
          "includes; {}",
          symbol_list(options.symbols), cut.messages, cut.input_messages, cut.bytes, cut.ns / 1e9,
          describe_arena(cut.arena->report()));
    }
    return input;
  }
  // MappedFile asks for sequential access, and a kernel may then drop pages behind the
  // reader. Here both passes need them to stay.
  ::posix_madvise(const_cast<std::byte*>(day.data()), day.size(),  // NOLINT
                  POSIX_MADV_NORMAL);
  touch(day);
  input.bytes = day;
  input.field =
      "mapped: the whole day, read-only, MAP_PRIVATE, posix_madvise(POSIX_MADV_NORMAL), every "
      "page read before the warm-up and again before the timed pass";
  return input;
}

[[nodiscard]] std::string core_field(const PinOutcome& pin, const MigrationTracker& migrations) {
  if (pin.pinned) {
    return std::format("{}", pin.requested_core);
  }
  if (migrations.last_core() < 0) {
    return unavailable("the current core could not be read");
  }
  return std::format("unpinned, last ran on core {}", migrations.last_core());
}

struct Outcome {
  std::vector<HeaderField> header;
  std::array<Residency, 3> residency;  // Before the warm-up, before and after the timed pass.
  std::uint64_t migrations = 0;
  Pass warm;
  Pass timed;
  std::vector<std::string> refusals;
};

[[nodiscard]] std::string record_json(const Options& options, const Input& input,
                                      const Outcome& outcome) {
  std::string json = "{\"header\": {";
  for (std::size_t i = 0; i < outcome.header.size(); ++i) {
    json += std::format("{}{}: {}", i == 0 ? "" : ", ", json_string(outcome.header[i].first),
                        json_string(outcome.header[i].second));
  }
  const bool shadow = options.workload == Workload::shadow;
  const Latency latency = engine_config(options).latency;
  const std::optional<Extract>& cut = input.cut;
  json += std::format(
      R"(}}, "workload": {}, "symbols": {}, "input": {}, "date": {}, "shadow_orders": {}, )"
      R"("shadow_span_ns": {}, "shadow_life_ns": {}, )"
      R"("latency_ns": {{"market_data": {}, "order_entry": {}, "response": {}, "compute": {}}}, )"
      R"("input_messages": {}, "residency": [{}, {}, {}], "migrations": {}, )"
      R"("extract": {}, "peak_rss_bytes": {}, "warmup": {}, "timed": {}, "refusals": [)",
      json_string(shadow ? "shadow" : "quoting"), json_string(symbol_list(options.symbols)),
      json_string(scrub_private(input.field)), options.date, shadow ? options.shadow_orders : 0,
      shadow ? options.shadow_span : 0, shadow ? options.shadow_life : 0, latency.market_data,
      latency.order_entry, latency.response, latency.compute,
      cut ? cut->input_messages : outcome.timed.stats.messages,
      json_string(outcome.residency[0].describe()), json_string(outcome.residency[1].describe()),
      json_string(outcome.residency[2].describe()), outcome.migrations,
      cut ? std::format(R"({{"messages": {}, "bytes": {}, "ns": {:.0f}}})", cut->messages,
                        cut->bytes, cut->ns)
          : std::string{"null"},
      peak_rss_bytes(), pass_json(outcome.warm), pass_json(outcome.timed));
  for (std::size_t i = 0; i < outcome.refusals.size(); ++i) {
    json += (i == 0 ? "" : ", ") + json_string(outcome.refusals[i]);
  }
  return json + "]}\n";
}

[[nodiscard]] Outcome measure(const Options& options, const Input& input, const TimerInfo& timer,
                              const PinOutcome& pin) {
  Outcome outcome;
  MigrationTracker migrations;
  migrations.sample();
  if (kPinningRequired && !pin.pinned) {
    outcome.refusals.push_back(pinning_field(pin));
  }
  outcome.residency[0] = resident(input.bytes);
  outcome.warm = run_pass(options, input.bytes, timer);
  migrations.sample();
  touch(input.bytes);
  outcome.residency[1] = resident(input.bytes);
  outcome.timed = run_pass(options, input.bytes, timer);
  outcome.residency[2] = resident(input.bytes);
  migrations.sample();
  outcome.migrations = migrations.migrations();

  for (const Residency& check : outcome.residency) {
    if (!check.complete()) {
      outcome.refusals.push_back(std::format("input not resident: {}", check.describe()));
    }
  }
  for (std::string& reason : refusals_of(outcome.warm, outcome.timed)) {
    outcome.refusals.push_back(std::move(reason));
  }
  outcome.header = build_header(
      live_source(),
      {.compiler = __VERSION__,
       .flags = ITCH_BENCH_FLAGS,
       .commit = options.commit,
       .dataset = options.dataset,
       .dataset_sha256 = options.dataset_sha256,
       .timer = describe_rate(timer),
       .quantum = std::format("{} ticks, {:.3f} ns", timer.quantum_ticks, timer.quantum_ns()),
       .pair = std::format("{:.3f} ticks, {:.3f} ns, not subtracted: one span covers the run",
                           timer.pair_ticks, timer.pair_ns()),
       .floor = std::format("{:.3f} ns", timer.floor_ns()),
       .pinning = pinning_field(pin),
       .migrations = migrations.describe(),
       .core = core_field(pin, migrations),
       .core_number = options.core,
       .counters = unavailable("bench_backtest reads no counters"),
       .rdpmc = unavailable("bench_backtest reads no counters"),
       .page_size = std::format("{} bytes, base pages", base_page_size()),
       .residency = outcome.residency[2].describe()});
  return outcome;
}

int run(int argc, char** argv) {
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: bench_backtest --input FILE --out RUN.json --workload quoting|shadow "
                 "--date YYYYMMDD [--symbol SYM]... [--shadow-orders N] [--shadow-span-ns N] "
                 "[--shadow-life-ns N] [--core N] [--commit TEXT] [--dataset NAME] "
                 "[--dataset-sha256 HEX]");
    return 2;
  }
  if (itch::find_fee_schedule(options.date) == nullptr) {
    std::println(stderr, "bench_backtest: no fee schedule for {}", options.date);
    return 2;
  }
  const auto file = itch::tools::MappedFile::open(options.input.c_str());
  if (!file) {
    std::println(stderr, "bench_backtest: cannot read {}", options.input);
    return 2;
  }
  const PinOutcome pin = pin_current_thread(options.core);
  const TimerInfo timer = measure_timer();
  const Input input = prepare_input(options, file->bytes(), timer);
  if (input.bytes.empty()) {
    std::println(stderr, "bench_backtest: no arena for the extract, or an empty input");
    return 2;
  }
  const Outcome outcome = measure(options, input, timer, pin);
  std::ofstream out{options.out};
  out << record_json(options, input, outcome);
  if (!out) {
    std::println(stderr, "bench_backtest: cannot write {}", options.out);
    return 2;
  }
  for (const std::string& refusal : outcome.refusals) {
    std::println(stderr, "bench_backtest: refused: {}", refusal);
  }
  return outcome.refusals.empty() ? 0 : 3;
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
