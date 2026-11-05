// Times SPSC queues for T-SPSC-1 and T-SPSC-2. One process is one repetition:
// scripts/spsc_driver.py starts a fresh one for each and writes the results file.
//
//     bench_spsc --suite queues|ablation --input FILE --out RUN.json [--core N]
//                [--peer-core N] [--rotate N] [--queue NAME]... [--rtt-trips N]
//                [--saturated-items N] [--paced-items N] [--burst-start-ns N]
//                [--burst-span-ns N] [--burst-speedup N] [--commit TEXT] [--dataset NAME]
//                [--dataset-sha256 HEX]
//
// queues (T-SPSC-1 and the comparison): every queue in spsc_queues.hpp this build has, by
//   rtt, saturated, paced at 1 µs and at 10 µs, and burst. --queue keeps only the named.
// ablation (T-SPSC-2): the project ring with one technique taken away at a time, by rtt,
//   saturated and burst; and batch publish against a store per item, by saturated through
//   the batch calls, 32 items a call.
//
// The element is an 8-byte std::uint64_t in rtt, saturated and paced, and the 64-byte
// BurstItem in burst, for every queue; the requested capacity is 2^17 for every queue, as
// in LC-1 and LC-7. Each row runs once a process, in a fresh queue, the order of the
// queues turned by --rotate so that no queue always runs first. burst replays the day's
// messages from --burst-start-ns, 09:30:00 by default, for --burst-span-ns, 2 s, at the
// gaps between their timestamps, copied out of the day before any span opens. The
// counts default to what the results files use; smaller ones, and --burst-speedup, which
// divides every gap, are for the CLI test, whose fixture spans a whole session.
//
// This thread is pinned to --core and the peer to --peer-core where the platform can pin.
//
// Exit codes: 0 publishable, 3 refused, with the record still written, 2 bad usage.
#include <algorithm>
#include <array>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <format>
#include <fstream>
#include <optional>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <type_traits>
#include <utility>
#include <vector>

#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "itch/spsc.hpp"
#include "mapped_file.hpp"
#include "platform/affinity.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"
#include "replay.hpp"
#include "run_record.hpp"
#include "spsc_protocols.hpp"
#include "spsc_queues.hpp"

namespace {

using namespace itch::bench;
using itch::SpscConfig;
using itch::SpscWrap;

constexpr std::size_t kCapacity = std::size_t{1} << 17U;
constexpr std::size_t kBatch = 32;
constexpr std::array<double, 6> kPercentiles{50.0, 90.0, 99.0, 99.9, 99.99, 100.0};

enum class Suite : std::uint8_t { queues, ablation };

struct Options {
  std::string input;
  std::string out;
  std::string commit;
  std::string dataset;
  std::string dataset_sha256;
  Suite suite = Suite::queues;
  bool suite_set = false;
  int core = -1;
  int peer_core = -1;
  std::uint64_t rotate = 0;
  std::vector<std::string> queues;
  std::uint64_t rtt_trips = 1'000'000;
  std::uint64_t saturated_items = 100'000'000;
  std::uint64_t paced_items = 200'000;
  std::uint64_t burst_start_ns = 34'200'000'000'000;  // 09:30:00.
  std::uint64_t burst_span_ns = 2'000'000'000;
  std::uint64_t burst_speedup = 1;
};

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
  } else if (flag == "--suite") {
    options.suite_set = value == "queues" || value == "ablation";
    options.suite = value == "ablation" ? Suite::ablation : Suite::queues;
    return options.suite_set;
  } else if (flag == "--queue") {
    options.queues.emplace_back(value);
    return std::ranges::find(kQueueNames, value) != kQueueNames.end();
  } else if (flag == "--core") {
    return parse_number(value, options.core);
  } else if (flag == "--peer-core") {
    return parse_number(value, options.peer_core);
  } else if (flag == "--rotate") {
    return parse_number(value, options.rotate);
  } else if (flag == "--rtt-trips") {
    return parse_number(value, options.rtt_trips) && options.rtt_trips != 0;
  } else if (flag == "--saturated-items") {
    return parse_number(value, options.saturated_items) && options.saturated_items != 0;
  } else if (flag == "--paced-items") {
    return parse_number(value, options.paced_items) && options.paced_items != 0;
  } else if (flag == "--burst-start-ns") {
    return parse_number(value, options.burst_start_ns);
  } else if (flag == "--burst-span-ns") {
    return parse_number(value, options.burst_span_ns) && options.burst_span_ns != 0;
  } else if (flag == "--burst-speedup") {
    return parse_number(value, options.burst_speedup) && options.burst_speedup != 0;
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
  return args.size() % 2 == 0 && !options.input.empty() && !options.out.empty() &&
         options.suite_set;
}

// The day's messages from start for span, copied out, each due at its timestamp's offset
// from the first's. The walk stops at the first message past the stretch.
[[nodiscard]] BurstPlan plan_burst(std::span<const std::byte> day, const Options& options,
                                   const TimerInfo& timer) {
  BurstPlan plan;
  const std::uint64_t end = options.burst_start_ns + options.burst_span_ns;
  std::uint64_t first = 0;
  itch::FrameReader reader{day};
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != itch::FrameStatus::message) {
      continue;
    }
    const std::uint64_t at = itch::read_header(frame.payload.data()).timestamp_ns;
    if (at < options.burst_start_ns) {
      continue;
    }
    if (at >= end) {
      break;
    }
    if (plan.items.empty()) {
      first = at;
    }
    BurstItem item;
    item.sequence = static_cast<std::uint32_t>(plan.items.size());
    item.length = static_cast<std::uint16_t>(std::min(frame.payload.size(), item.bytes.size()));
    for (std::size_t b = 0; b < item.length; ++b) {
      item.bytes[b] = static_cast<std::uint8_t>(frame.payload[b]);
      plan.byte_sum += item.bytes[b];
    }
    plan.items.push_back(item);
    plan.due_ticks.push_back(static_cast<std::uint64_t>(static_cast<double>(at - first) /
                                                        static_cast<double>(options.burst_speedup) *
                                                        timer.calibrated_hz / 1e9));
  }
  return plan;
}

struct Row {
  std::string queue;
  std::string protocol;
  ProtocolRun run;
};

[[nodiscard]] std::string row_json(const Row& row, const TimerInfo& timer) {
  const double ns = timer.ticks_to_ns(static_cast<double>(row.run.ticks));
  std::string json = std::format(
      R"({{"queue": {}, "protocol": {}, "items": {}, "ns": {:.0f}, "ns_per_item": {:.6f}, )"
      R"("items_per_s": {:.1f}, "errors": {}, "late": {}, "self_core": {}, "peer_core": {}, )"
      R"("peer_pinning": {})",
      json_string(row.queue), json_string(row.protocol), row.run.items, ns,
      row.run.items == 0 ? 0.0 : ns / static_cast<double>(row.run.items),
      ns > 0.0 ? static_cast<double>(row.run.items) * 1e9 / ns : 0.0, row.run.errors, row.run.late,
      row.run.self_core, row.run.peer_core, json_string(pinning_field(row.run.peer_pin)));
  if (row.run.latency) {
    const TickHistogram& latency = *row.run.latency;
    json += std::format(R"(, "latency": {{"samples": {}, "mean_ns": {:.6f}, "percentiles": [)",
                        latency.count(), timer.ticks_to_ns(latency.mean()));
    for (std::size_t i = 0; i < kPercentiles.size(); ++i) {
      const Percentile p = classify_percentile(kPercentiles[i], latency.at(kPercentiles[i]), timer);
      json += std::format(R"({}{{"percentile": {}, "value_ns": {}}})", i == 0 ? "" : ", ",
                          p.percentile, json_number(p.value_ns));
    }
    json += "]}";
  }
  return json + "}";
}

// The queues of this run, in the order --rotate turns them to.
[[nodiscard]] std::vector<std::string> queue_order(const Options& options) {
  std::vector<std::string> names;
  for (const std::string_view name : kQueueNames) {
    const bool wanted =
        options.queues.empty() || std::ranges::find(options.queues, name) != options.queues.end();
    const bool built = with_queue<std::uint64_t>(name, [](auto*) {});
    if (wanted && built) {
      names.emplace_back(name);
    }
  }
  if (!names.empty()) {
    std::ranges::rotate(names,
                        names.begin() + static_cast<std::ptrdiff_t>(options.rotate % names.size()));
  }
  return names;
}

template <class Queue>
void protocol_rows(std::vector<Row>& rows, std::string_view name, const Options& options,
                   const TimerInfo& timer, bool paced_rows) {
  rows.push_back(
      {.queue = std::string{name},
       .protocol = "rtt",
       .run = rtt<Queue>(kCapacity, options.rtt_trips / 10, options.rtt_trips, options.peer_core)});
  rows.push_back({.queue = std::string{name},
                  .protocol = "saturated",
                  .run = saturated<Queue>(kCapacity, options.saturated_items, options.peer_core)});
  if (paced_rows) {
    for (const std::uint64_t gap_ns : {1'000ULL, 10'000ULL}) {
      const auto gap =
          static_cast<std::uint64_t>(static_cast<double>(gap_ns) * timer.calibrated_hz / 1e9);
      rows.push_back({.queue = std::string{name},
                      .protocol = std::format("paced-{}ns", gap_ns),
                      .run = paced<Queue>(kCapacity, options.paced_items / 10, options.paced_items,
                                          gap, options.peer_core)});
    }
  }
}

template <class BurstQueue>
void burst_row(std::vector<Row>& rows, std::string_view name, const Options& options,
               const BurstPlan& plan) {
  rows.push_back({.queue = std::string{name},
                  .protocol = "burst",
                  .run = burst<BurstQueue>(kCapacity, plan, options.peer_core)});
}

void queue_rows(std::vector<Row>& rows, const Options& options, const TimerInfo& timer,
                const BurstPlan& plan) {
  for (const std::string& name : queue_order(options)) {
    static_cast<void>(with_queue<std::uint64_t>(name, [&](auto* tag) {
      using Queue = std::remove_pointer_t<decltype(tag)>;
      protocol_rows<Queue>(rows, name, options, timer, true);
    }));
    static_cast<void>(with_queue<BurstItem>(name, [&](auto* tag) {
      using Queue = std::remove_pointer_t<decltype(tag)>;
      burst_row<Queue>(rows, name, options, plan);
    }));
  }
}

// The project ring with one technique taken away, by name. Padding is tried at 8 (none),
// 64, 128 and 256 bytes, leaving out the one the ring ships with.
template <SpscConfig Config>
struct Variant {
  static constexpr SpscConfig kConfig = Config;
};

template <class Run>
void each_variant(const Run& run) {
  run("shipped", Variant<SpscConfig{}>{});
  run("uncached", Variant<SpscConfig{.cached_indices = false}>{});
  run("modulo", Variant<SpscConfig{.wrap = SpscWrap::modulo}>{});
  run("uncached-modulo", Variant<SpscConfig{.cached_indices = false, .wrap = SpscWrap::modulo}>{});
  run("padding-8", Variant<SpscConfig{.padding = 8}>{});
  if constexpr (itch::kCacheLine != 64) {
    run("padding-64", Variant<SpscConfig{.padding = 64}>{});
  }
  if constexpr (itch::kCacheLine != 128) {
    run("padding-128", Variant<SpscConfig{.padding = 128}>{});
  }
  run("padding-256", Variant<SpscConfig{.padding = 256}>{});
}

void ablation_rows(std::vector<Row>& rows, const Options& options, const TimerInfo& timer,
                   const BurstPlan& plan) {
  std::vector<std::pair<std::string, std::size_t>> order;
  std::size_t index = 0;
  each_variant([&](std::string_view name, auto) { order.emplace_back(name, index++); });
  std::ranges::rotate(order,
                      order.begin() + static_cast<std::ptrdiff_t>(options.rotate % order.size()));
  for (const auto& entry : order) {
    each_variant([&](std::string_view name, auto variant) {
      if (name != entry.first) {
        return;
      }
      constexpr SpscConfig kConfig = decltype(variant)::kConfig;
      protocol_rows<ProjectQueue<std::uint64_t, kConfig>>(rows, name, options, timer, false);
      burst_row<ProjectQueue<BurstItem, kConfig>>(rows, name, options, plan);
    });
  }
  const std::string protocol = std::format("saturated-batch-{}", kBatch);
  const bool batch_first = options.rotate % 2 == 0;
  for (int turn = 0; turn < 2; ++turn) {
    if ((turn == 0) == batch_first) {
      rows.push_back({.queue = "batch-publish",
                      .protocol = protocol,
                      .run = saturated_batch<itch::SpscRing<std::uint64_t>>(
                          kCapacity, options.saturated_items, kBatch, options.peer_core)});
    } else {
      rows.push_back(
          {.queue = "publish-each",
           .protocol = protocol,
           .run =
               saturated_batch<itch::SpscRing<std::uint64_t, SpscConfig{.batch_publish = false}>>(
                   kCapacity, options.saturated_items, kBatch, options.peer_core)});
    }
  }
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

// Each element through `render`, joined with ", ".
template <class Range, class Render>
[[nodiscard]] std::string joined(const Range& range, const Render& render) {
  std::string text;
  for (const auto& element : range) {
    text += (text.empty() ? "" : ", ") + render(element);
  }
  return text;
}

[[nodiscard]] std::string header_json(const std::vector<HeaderField>& header) {
  return joined(header, [](const HeaderField& field) {
    return std::format("{}: {}", json_string(field.first), json_string(field.second));
  });
}

// Every reason the run cannot be published.
[[nodiscard]] std::vector<std::string> refusals_of(const PinOutcome& pin,
                                                   const std::vector<Row>& rows) {
  std::vector<std::string> refusals;
  if (kPinningRequired && !pin.pinned) {
    refusals.push_back(pinning_field(pin));
  }
  for (const Row& row : rows) {
    if (row.run.errors != 0) {
      refusals.push_back(
          std::format("{} {}: {} items wrong", row.queue, row.protocol, row.run.errors));
    }
    if (kPinningRequired && !row.run.peer_pin.pinned) {
      refusals.push_back(std::format("{} {}: the peer was not pinned: {}", row.queue, row.protocol,
                                     pinning_field(row.run.peer_pin)));
    }
  }
  return refusals;
}

int run(int argc, char** argv) {
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: bench_spsc --suite queues|ablation --input FILE --out RUN.json "
                 "[--core N] [--peer-core N] [--rotate N] [--queue NAME]... [--rtt-trips N] "
                 "[--saturated-items N] [--paced-items N] [--burst-start-ns N] "
                 "[--burst-span-ns N] [--burst-speedup N] [--commit TEXT] [--dataset NAME] "
                 "[--dataset-sha256 HEX]");
    return 2;
  }
  const auto file = itch::tools::MappedFile::open(options.input.c_str());
  if (!file) {
    std::println(stderr, "bench_spsc: cannot read {}", options.input);
    return 2;
  }
  const PinOutcome pin = pin_current_thread(options.core);
  const TimerInfo timer = measure_timer();
  MigrationTracker migrations;
  migrations.sample();
  const BurstPlan plan = plan_burst(file->bytes(), options, timer);
  if (plan.items.empty()) {
    std::println(stderr, "bench_spsc: no message in the burst stretch");
    return 2;
  }
  std::vector<Row> rows;
  if (options.suite == Suite::queues) {
    queue_rows(rows, options, timer, plan);
  } else {
    ablation_rows(rows, options, timer, plan);
  }
  migrations.sample();
  const std::vector<std::string> refusals = refusals_of(pin, rows);
  const std::vector<HeaderField> header = build_header(
      live_source(),
      {.compiler = __VERSION__,
       .flags = ITCH_BENCH_FLAGS,
       .commit = options.commit,
       .dataset = options.dataset,
       .dataset_sha256 = options.dataset_sha256,
       .timer = describe_rate(timer),
       .quantum = std::format("{} ticks, {:.3f} ns", timer.quantum_ticks, timer.quantum_ns()),
       .pair = std::format("{:.3f} ticks, {:.3f} ns, not subtracted from a one-way latency: "
                           "its two reads are on two cores",
                           timer.pair_ticks, timer.pair_ns()),
       .floor = std::format("{:.3f} ns", timer.floor_ns()),
       .pinning = pinning_field(pin),
       .migrations = migrations.describe(),
       .core = core_field(pin, migrations),
       .core_number = options.core,
       .counters = unavailable("bench_spsc reads no counters"),
       .rdpmc = unavailable("bench_spsc reads no counters"),
       .page_size = std::format("{} bytes, base pages", base_page_size()),
       .residency = resident(file->bytes()).describe()});
  std::string json = "{\"header\": {" + header_json(header);
  json += std::format(
      R"(}}, "suite": {}, "capacity": {}, "batch": {}, "element_bytes": 8, )"
      R"("burst_element_bytes": {}, "cache_line": {}, "peer_core": {}, "rotate": {}, )"
      R"("burst": {{"start_ns": {}, "span_ns": {}, "speedup": {}, "messages": {}, "byte_sum": {}}}, )"
      R"("migrations": {}, "peak_rss_bytes": {}, "comparison_queues": {}, "rows": [)",
      json_string(options.suite == Suite::queues ? "queues" : "ablation"), kCapacity, kBatch,
      sizeof(BurstItem), itch::kCacheLine, options.peer_core, options.rotate,
      options.burst_start_ns, options.burst_span_ns, options.burst_speedup, plan.items.size(),
      plan.byte_sum, migrations.migrations(), peak_rss_bytes(),
      kHaveComparisonQueues ? "true" : "false");
  for (std::size_t i = 0; i < rows.size(); ++i) {
    json += (i == 0 ? "\n  " : ",\n  ") + row_json(rows[i], timer);
  }
  json += "], \"refusals\": [" + joined(refusals, json_string) + "]}\n";
  std::ofstream out{options.out};
  out << json;
  if (!out) {
    std::println(stderr, "bench_spsc: cannot write {}", options.out);
    return 2;
  }
  for (const std::string& refusal : refusals) {
    std::println(stderr, "bench_spsc: refused: {}", refusal);
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
