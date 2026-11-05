// Times T-SPSC-3: a day through a parser thread, the SPSC ring and a book thread, against
// the same day through one thread, on the same book. One process is one repetition:
// scripts/spsc_driver.py starts a fresh one for each and writes the results file.
//
//     bench_pipeline --input FILE --out RUN.json [--core N] [--peer-core N] [--rotate N]
//                    [--commit TEXT] [--dataset NAME] [--dataset-sha256 HEX]
//
// Three modes, from bench/pipeline.hpp, each on a fresh paged-flat book, the shipped arm:
// single, the shipped single-threaded replay; pipelined, the parser on the peer thread
// and the book on this one; and decoded, the pipeline's work on one thread with no
// handover. Each process runs every mode once untimed, as a warm-up, then every mode once
// timed, in an order --rotate turns. The day is mapped, and every page is read before the
// warm-ups and again before the timed passes, and found resident after them.
//
// Every pass must leave the same book, by the oracle's canonical hash of it, and give the
// same count of messages and of each status apply() returned (no_book_effect aside, which
// only the single mode can return), or the run is refused.
//
// This thread, the book's, is pinned to --core and the parser to --peer-core where the
// platform can pin.
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
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include "itch/book.hpp"
#include "mapped_file.hpp"
#include "pipeline.hpp"
#include "platform/affinity.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"
#include "run_record.hpp"

namespace {

using namespace itch::bench;

constexpr std::array<std::string_view, 3> kModes{"single", "pipelined", "decoded"};

struct Options {
  std::string input;
  std::string out;
  std::string commit;
  std::string dataset;
  std::string dataset_sha256;
  int core = -1;
  int peer_core = -1;
  std::uint64_t rotate = 0;
};

template <class Number>
[[nodiscard]] bool parse_number(std::string_view text, Number& value) {
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
  return error == std::errc{} && end == text.data() + text.size();
}

[[nodiscard]] bool parse(std::span<char*> args, Options& options) {
  if (args.size() % 2 != 0) {
    return false;
  }
  for (std::size_t i = 0; i < args.size(); i += 2) {
    const std::string_view flag = args[i];
    const std::string_view value = args[i + 1];
    bool good = true;
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
    } else if (flag == "--core") {
      good = parse_number(value, options.core);
    } else if (flag == "--peer-core") {
      good = parse_number(value, options.peer_core);
    } else if (flag == "--rotate") {
      good = parse_number(value, options.rotate);
    } else {
      good = false;
    }
    if (!good) {
      return false;
    }
  }
  return !options.input.empty() && !options.out.empty();
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

struct Pass {
  std::string_view mode;
  ReplayCounts counts;
  std::uint64_t hash = 0;
  std::uint64_t live_orders = 0;
  std::uint64_t live_levels = 0;
  double ns = 0.0;
};

[[nodiscard]] Pass run_pass(std::string_view mode, std::span<const std::byte> day,
                            const Options& options, const TimerInfo& timer) {
  const auto book = std::make_unique<itch::FastBook<>>();
  Pass pass;
  pass.mode = mode;
  if (mode == "single") {
    pass.counts = replay_single(day, *book);
  } else if (mode == "decoded") {
    pass.counts = replay_decoded(day, *book);
  } else {
    const auto ring = std::make_unique<OpRing>(kPipelineCapacity);
    pass.counts = replay_pipelined(day, *book, *ring, options.peer_core, [] {});
  }
  pass.ns = timer.ticks_to_ns(static_cast<double>(pass.counts.ticks));
  pass.hash = book_hash(*book);
  pass.live_orders = book->live_orders();
  pass.live_levels = book->live_levels();
  return pass;
}

[[nodiscard]] std::string pass_json(const Pass& pass) {
  const ReplayCounts& c = pass.counts;
  std::string statuses;
  for (std::size_t i = 0; i < kStatuses; ++i) {
    statuses += std::format("{}{}: {}", i == 0 ? "" : ", ",
                            json_string(itch::to_string(static_cast<itch::ApplyStatus>(i))),
                            c.by_status[i]);
  }
  return std::format(
      R"({{"mode": {}, "ns": {:.0f}, "messages": {}, "ops": {}, "statuses": {{{}}}, )"
      R"("full_waits": {}, "empty_waits": {}, "book_hash": "{:016x}", "live_orders": {}, )"
      R"("live_levels": {}, "parser_pinning": {}}})",
      json_string(pass.mode), pass.ns, c.messages, c.ops, statuses, c.full_waits, c.empty_waits,
      pass.hash, pass.live_orders, pass.live_levels,
      json_string(pass.mode == "pipelined" ? pinning_field(c.parser_pin) : "no parser thread"));
}

// Every pass against the first: the book's hash, the messages, and each status the book
// returned on an op it was given.
[[nodiscard]] std::vector<std::string> disagreements(const std::vector<Pass>& passes) {
  std::vector<std::string> out;
  const Pass& first = passes.front();
  for (const Pass& pass : passes) {
    if (pass.hash != first.hash) {
      out.push_back(std::format("{} left book {:016x}, {} left {:016x}", pass.mode, pass.hash,
                                first.mode, first.hash));
    }
    if (pass.counts.messages != first.counts.messages) {
      out.push_back(std::format("{} walked {} messages, {} walked {}", pass.mode,
                                pass.counts.messages, first.mode, first.counts.messages));
    }
    for (std::size_t i = 0; i < kStatuses; ++i) {
      if (i != static_cast<std::size_t>(itch::ApplyStatus::no_book_effect) &&
          pass.counts.by_status[i] != first.counts.by_status[i]) {
        out.push_back(std::format("{} and {} differ on {}", pass.mode, first.mode,
                                  itch::to_string(static_cast<itch::ApplyStatus>(i))));
      }
    }
  }
  return out;
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
                                                   const std::vector<Pass>& warm,
                                                   const std::vector<Pass>& timed,
                                                   const std::array<Residency, 3>& residency) {
  std::vector<std::string> refusals;
  if (kPinningRequired && !pin.pinned) {
    refusals.push_back(pinning_field(pin));
  }
  for (const Pass& pass : timed) {
    if (kPinningRequired && pass.mode == "pipelined" && !pass.counts.parser_pin.pinned) {
      refusals.push_back("the parser thread was not pinned: " +
                         pinning_field(pass.counts.parser_pin));
    }
  }
  for (const Residency& check : residency) {
    if (!check.complete()) {
      refusals.push_back(std::format("input not resident: {}", check.describe()));
    }
  }
  std::vector<Pass> every{warm};
  every.insert(every.end(), timed.begin(), timed.end());
  for (std::string& reason : disagreements(every)) {
    refusals.push_back(std::move(reason));
  }
  return refusals;
}

int run(int argc, char** argv) {
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: bench_pipeline --input FILE --out RUN.json [--core N] [--peer-core N] "
                 "[--rotate N] [--commit TEXT] [--dataset NAME] [--dataset-sha256 HEX]");
    return 2;
  }
  const auto file = itch::tools::MappedFile::open(options.input.c_str());
  if (!file || file->bytes().empty()) {
    std::println(stderr, "bench_pipeline: cannot read {}", options.input);
    return 2;
  }
  const std::span<const std::byte> day = file->bytes();
  const PinOutcome pin = pin_current_thread(options.core);
  const TimerInfo timer = measure_timer();
  MigrationTracker migrations;
  migrations.sample();
  std::vector<std::string_view> order{kModes.begin(), kModes.end()};
  std::ranges::rotate(order,
                      order.begin() + static_cast<std::ptrdiff_t>(options.rotate % order.size()));

  // MappedFile asks for sequential access, and a kernel may then drop pages behind the
  // reader. Here every pass needs them to stay.
  ::posix_madvise(const_cast<std::byte*>(day.data()), day.size(),  // NOLINT
                  POSIX_MADV_NORMAL);
  touch(day);
  std::array<Residency, 3> residency;
  residency[0] = resident(day);
  std::vector<Pass> warm;
  for (const std::string_view mode : order) {
    warm.push_back(run_pass(mode, day, options, timer));
    migrations.sample();
  }
  touch(day);
  residency[1] = resident(day);
  std::vector<Pass> timed;
  for (const std::string_view mode : order) {
    timed.push_back(run_pass(mode, day, options, timer));
    migrations.sample();
  }
  residency[2] = resident(day);

  const std::vector<std::string> refusals = refusals_of(pin, warm, timed, residency);

  const std::vector<HeaderField> header = build_header(
      live_source(),
      {.compiler = __VERSION__,
       .flags = ITCH_BENCH_FLAGS,
       .commit = options.commit,
       .dataset = options.dataset,
       .dataset_sha256 = options.dataset_sha256,
       .timer = describe_rate(timer),
       .quantum = std::format("{} ticks, {:.3f} ns", timer.quantum_ticks, timer.quantum_ns()),
       .pair = std::format("{:.3f} ticks, {:.3f} ns, not subtracted: one span covers the day",
                           timer.pair_ticks, timer.pair_ns()),
       .floor = std::format("{:.3f} ns", timer.floor_ns()),
       .pinning = pinning_field(pin),
       .migrations = migrations.describe(),
       .core = core_field(pin, migrations),
       .core_number = options.core,
       .counters = unavailable("bench_pipeline reads no counters"),
       .rdpmc = unavailable("bench_pipeline reads no counters"),
       .page_size = std::format("{} bytes, base pages", base_page_size()),
       .residency = residency[2].describe()});
  std::string json = "{\"header\": {" + header_json(header);
  json += std::format(
      R"(}}, "suite": "pipeline", "design": "paged-flat", "capacity": {}, "batch": {}, )"
      R"("op_bytes": {}, "cache_line": {}, "peer_core": {}, "rotate": {}, )"
      R"("input": "mapped: the whole day, read-only, MAP_PRIVATE, posix_madvise(POSIX_MADV_NORMAL), every page read before the warm-ups and again before the timed passes", )"
      R"("residency": [{}, {}, {}], "migrations": {}, "peak_rss_bytes": {}, "warmup": [)",
      kPipelineCapacity, kPipelineBatch, sizeof(itch::BookOp), itch::kCacheLine, options.peer_core,
      options.rotate, json_string(residency[0].describe()), json_string(residency[1].describe()),
      json_string(residency[2].describe()), migrations.migrations(), peak_rss_bytes());
  json += joined(warm, pass_json) + "], \"timed\": [" + joined(timed, pass_json) +
          "], \"refusals\": [" + joined(refusals, json_string) + "]}\n";
  std::ofstream out{options.out};
  out << json;
  if (!out) {
    std::println(stderr, "bench_pipeline: cannot write {}", options.out);
    return 2;
  }
  for (const std::string& refusal : refusals) {
    std::println(stderr, "bench_pipeline: refused: {}", refusal);
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
