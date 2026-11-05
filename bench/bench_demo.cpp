// The native side of T-WASM-1: the demo page's replay of one symbol's extract, timed as the
// page times it, so that scripts/wasm_bench.py can set the browser's run beside it.
//
//     bench_demo --extract FILE --out RUN.json [--repetitions N] [--commit TEXT]
//                [--dataset NAME] [--dataset-sha256 HEX]
//
// The extract is read into the heap, as the page copies it into linear memory. Then one
// warm-up and N timed repetitions (21 by default), each on a fresh demo::Replay at the book's
// default capacity, built before its span opens: the span is Replay::replay() and nothing
// else, the call the page wraps in performance.now(). Every repetition must leave the
// warm-up's digest and counts, and apply every message without a refusal.
//
// The record carries each repetition's span, the build of its book, untimed, the digest the
// page must also reach, and the process's peak resident memory.
//
// Exit codes: 0 publishable, 3 refused, with the record still written, 2 bad usage.
#include <array>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <format>
#include <fstream>
#include <memory>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include "itch/book_types.hpp"
#include "itch/demo_replay.hpp"
#include "platform/affinity.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"
#include "run_record.hpp"

namespace {

using namespace itch::bench;
using itch::demo::Replay;

struct Options {
  std::string extract;
  std::string out;
  std::string commit;
  std::string dataset;
  std::string dataset_sha256;
  std::uint32_t repetitions = 21;
};

[[nodiscard]] bool take(Options& options, std::string_view flag, std::string_view value) {
  if (flag == "--extract") {
    options.extract = value;
  } else if (flag == "--out") {
    options.out = value;
  } else if (flag == "--commit") {
    options.commit = value;
  } else if (flag == "--dataset") {
    options.dataset = value;
  } else if (flag == "--dataset-sha256") {
    options.dataset_sha256 = value;
  } else if (flag == "--repetitions") {
    const auto [end, error] =
        std::from_chars(value.data(), value.data() + value.size(), options.repetitions);
    return error == std::errc{} && end == value.data() + value.size() && options.repetitions >= 3;
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
  return args.size() % 2 == 0 && !options.extract.empty() && !options.out.empty();
}

[[nodiscard]] std::vector<std::byte> read_whole(const std::string& path) {
  std::ifstream in{path, std::ios::binary | std::ios::ate};
  if (!in) {
    return {};
  }
  std::vector<std::byte> bytes(static_cast<std::size_t>(in.tellg()));
  in.seekg(0);
  in.read(
      reinterpret_cast<char*>(bytes.data()),  // NOLINT(cppcoreguidelines-pro-type-reinterpret-cast)
      static_cast<std::streamsize>(bytes.size()));
  return in ? bytes : std::vector<std::byte>{};
}

// One replay of the whole extract on a fresh book.
struct Pass {
  double build_ns = 0.0;
  double ns = 0.0;
  std::uint64_t messages = 0;
  std::uint64_t digest = 0;
  std::array<std::uint64_t, itch::demo::kStatusCount> statuses{};
};

[[nodiscard]] Pass run_pass(std::span<const std::byte> extract, const TimerInfo& timer) {
  Pass pass;
  const std::uint64_t build_start = ticks_start();
  const auto replay = std::make_unique<Replay>(extract);
  const std::uint64_t start = ticks_start();
  pass.messages = replay->replay();
  const std::uint64_t stop = ticks_stop();
  pass.build_ns = timer.ticks_to_ns(static_cast<double>(start - build_start));
  pass.ns = timer.ticks_to_ns(static_cast<double>(stop - start));
  pass.digest = replay->digest();
  for (std::size_t s = 0; s < pass.statuses.size(); ++s) {
    pass.statuses[s] = replay->status_count(static_cast<itch::ApplyStatus>(s));
  }
  return pass;
}

[[nodiscard]] std::vector<std::string> refusals_of(const Replay& survey, const Pass& warm,
                                                   const std::vector<Pass>& timed) {
  std::vector<std::string> refusals;
  if (survey.extract_status() != itch::demo::ExtractStatus::ok) {
    refusals.push_back(std::format("the extract is not one symbol's: {}",
                                   itch::demo::to_string(survey.extract_status())));
  }
  const std::uint64_t clean = warm.statuses[0] + warm.statuses[1];
  if (warm.messages != survey.messages() || clean != warm.messages) {
    refusals.push_back(std::format("the warm-up applied {} of {} messages, {} without a refusal",
                                   warm.messages, survey.messages(), clean));
  }
  for (std::size_t i = 0; i < timed.size(); ++i) {
    if (timed[i].digest != warm.digest || timed[i].statuses != warm.statuses ||
        timed[i].messages != warm.messages) {
      refusals.push_back(std::format("repetition {} differs from the warm-up", i + 1));
    }
  }
  return refusals;
}

[[nodiscard]] std::string record_json(const Options& options, const Replay& survey,
                                      const std::vector<HeaderField>& header, const Pass& warm,
                                      const std::vector<Pass>& timed,
                                      const std::vector<std::string>& refusals) {
  std::string json = "{\"header\": {";
  for (std::size_t i = 0; i < header.size(); ++i) {
    json += std::format("{}{}: {}", i == 0 ? "" : ", ", json_string(header[i].first),
                        json_string(header[i].second));
  }
  const itch::Stock& symbol = survey.symbol();
  json += std::format(
      R"(}}, "engine": "native", "extract": {{"symbol": {}, "bytes": {}, "messages": {}}}, )"
      R"("digest": "{:016x}", "statuses": {{)",
      json_string(std::string_view{symbol.data(), symbol.size()}), survey.bytes(),
      survey.messages(), warm.digest);
  for (std::size_t s = 0; s < warm.statuses.size(); ++s) {
    json += std::format("{}{}: {}", s == 0 ? "" : ", ",
                        json_string(itch::to_string(static_cast<itch::ApplyStatus>(s))),
                        warm.statuses[s]);
  }
  json += std::format(R"(}}, "warmup": {{"ns": {:.0f}, "build_ns": {:.0f}}}, "repetitions": [)",
                      warm.ns, warm.build_ns);
  for (std::size_t i = 0; i < timed.size(); ++i) {
    json += std::format(R"({}{{"ns": {:.0f}, "build_ns": {:.0f}, "messages": {}}})",
                        i == 0 ? "" : ", ", timed[i].ns, timed[i].build_ns, timed[i].messages);
  }
  json += std::format(R"(], "repetitions_requested": {}, "peak_rss_bytes": {}, "refusals": [)",
                      options.repetitions, peak_rss_bytes());
  for (std::size_t i = 0; i < refusals.size(); ++i) {
    json += (i == 0 ? "" : ", ") + json_string(refusals[i]);
  }
  return json + "]}\n";
}

int run(int argc, char** argv) {
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: bench_demo --extract FILE --out RUN.json [--repetitions N, at least 3] "
                 "[--commit TEXT] [--dataset NAME] [--dataset-sha256 HEX]");
    return 2;
  }
  const std::vector<std::byte> extract = read_whole(options.extract);
  if (extract.empty()) {
    std::println(stderr, "bench_demo: cannot read {}, or it is empty", options.extract);
    return 2;
  }
  const PinOutcome pin = pin_current_thread(-1);
  const TimerInfo timer = measure_timer();
  const auto survey = std::make_unique<Replay>(
      extract, itch::BookCapacity{
                   .orders = 1, .levels = 1, .index_pages = 1, .index_bits = 8, .level_slots = 8});
  MigrationTracker migrations;
  migrations.sample();
  const Pass warm = run_pass(extract, timer);
  std::vector<Pass> timed;
  timed.reserve(options.repetitions);
  for (std::uint32_t i = 0; i < options.repetitions; ++i) {
    timed.push_back(run_pass(extract, timer));
    migrations.sample();
  }
  const std::vector<std::string> refusals = refusals_of(*survey, warm, timed);
  const std::vector<HeaderField> header = build_header(
      live_source(),
      {.compiler = __VERSION__,
       .flags = ITCH_BENCH_FLAGS,
       .commit = options.commit,
       .dataset = options.dataset,
       .dataset_sha256 = options.dataset_sha256,
       .timer = describe_rate(timer),
       .quantum = std::format("{} ticks, {:.3f} ns", timer.quantum_ticks, timer.quantum_ns()),
       .pair = std::format("{:.3f} ticks, {:.3f} ns, not subtracted: one span covers a replay",
                           timer.pair_ticks, timer.pair_ns()),
       .floor = std::format("{:.3f} ns", timer.floor_ns()),
       .pinning = pinning_field(pin),
       .migrations = migrations.describe(),
       .core = unavailable("not pinned, so no core is claimed"),
       .counters = unavailable("bench_demo reads no counters"),
       .rdpmc = unavailable("bench_demo reads no counters"),
       .page_size = std::format("{} bytes, base pages", base_page_size()),
       .residency = unavailable("the extract is read into the heap, not mapped")});
  std::ofstream out{options.out};
  out << record_json(options, *survey, header, warm, timed, refusals);
  if (!out) {
    std::println(stderr, "bench_demo: cannot write {}", options.out);
    return 2;
  }
  for (const std::string& refusal : refusals) {
    std::println(stderr, "bench_demo: refused: {}", refusal);
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
