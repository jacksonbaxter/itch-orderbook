// Holds the counters against a workload with a known answer: a random pointer chase over
// buffers from well under L2 to far above L3, on 4 KB pages, as the book's structures get
// from the allocator. Every span is counted through read() and through rdpmc.
//
//     bench_chase --out RUN.json [--core N] [--commit TEXT] [--loads N] [--sizes 64K,1M,...]
//                 [--raw-event HEX]
//
// --raw-event replaces the fill event, to show a wrong event failing. One process is one
// run, and scripts/chase_report.py reads the records and says what they prove. Exit codes:
// 0 complete, 3 refused, with the reason in the record, 2 bad usage.
#include <sys/mman.h>
#include <unistd.h>

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
#include <vector>

#include "chase.hpp"
#include "platform/affinity.hpp"
#include "platform/counters.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"
#include "run_record.hpp"

namespace {

using namespace itch::bench;

constexpr std::uint64_t kDefaultLoads = 20'000'000;
constexpr std::uint64_t kBaseSeed = 20191230;
constexpr std::uint64_t kWarmLaps = 2;

struct Options {
  std::string out;
  std::string commit;
  int core = -1;
  std::uint64_t loads = kDefaultLoads;
  std::uint64_t raw_event = kRawDramFills;
  std::vector<std::size_t> sizes = default_chase_sizes();
};

bool parse_sizes(std::string_view text, std::vector<std::size_t>& sizes) {
  sizes.clear();
  while (!text.empty()) {
    const std::size_t comma = text.find(',');
    const std::optional<std::size_t> size = parse_chase_size(text.substr(0, comma));
    if (!size || *size < kChaseLineBytes) {
      return false;
    }
    sizes.push_back(*size);
    text = comma == std::string_view::npos ? std::string_view{} : text.substr(comma + 1);
  }
  return !sizes.empty();
}

template <class Number>
bool parse_number(std::string_view text, Number& out, int base = 10) {
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), out, base);
  return error == std::errc{} && end == text.data() + text.size();
}

bool parse(std::span<char*> args, Options& options) {
  for (std::size_t i = 0; i + 1 < args.size(); i += 2) {
    const std::string_view arg{args[i]};
    const std::string_view value{args[i + 1]};
    bool good = true;
    if (arg == "--out") {
      options.out = value;
    } else if (arg == "--commit") {
      options.commit = value;
    } else if (arg == "--core") {
      good = parse_number(value, options.core);
    } else if (arg == "--loads") {
      good = parse_number(value, options.loads);
    } else if (arg == "--sizes") {
      good = parse_sizes(value, options.sizes);
    } else if (arg == "--raw-event") {
      good = parse_number(value, options.raw_event, 16);
    } else {
      good = false;
    }
    if (!good) {
      return false;
    }
  }
  return args.size() % 2 == 0 && !options.out.empty() && options.loads != 0;
}

// Base pages by request: transparent hugepages are on by default on debian, and a chase
// on them would take fewer TLB misses than the book does.
class ChaseBuffer {
 public:
  explicit ChaseBuffer(std::size_t bytes) : bytes_{bytes} {
    void* base = ::mmap(nullptr, bytes_, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (base == MAP_FAILED) {
      return;
    }
    base_ = base;
#ifdef MADV_NOHUGEPAGE
    no_hugepages_ = ::madvise(base_, bytes_, MADV_NOHUGEPAGE) == 0;
#endif
  }
  ChaseBuffer(const ChaseBuffer&) = delete;
  ChaseBuffer& operator=(const ChaseBuffer&) = delete;
  ChaseBuffer(ChaseBuffer&&) = delete;
  ChaseBuffer& operator=(ChaseBuffer&&) = delete;
  ~ChaseBuffer() {
    if (base_ != nullptr) {
      ::munmap(base_, bytes_);
    }
  }

  [[nodiscard]] std::span<ChaseLine> lines() const noexcept {
    return {static_cast<ChaseLine*>(base_), base_ == nullptr ? 0 : bytes_ / kChaseLineBytes};
  }
  [[nodiscard]] bool no_hugepages() const noexcept { return no_hugepages_; }

 private:
  std::size_t bytes_;
  void* base_ = nullptr;
  bool no_hugepages_ = false;
};

struct ChaseEvent {
  std::string name;
  EventAgreement paths;
  CounterStatus status;
};

struct ChaseRow {
  std::size_t bytes = 0;
  std::size_t lines = 0;
  std::string pages;
  double ns_per_load = 0.0;
  std::string agreement;
  std::vector<ChaseEvent> events;
};

std::string pages_field(const ChaseBuffer& buffer) {
  const std::optional<std::size_t> huge_kb = hugepage_kb_of(buffer.lines().data());
  return std::format("{} bytes per page, MADV_NOHUGEPAGE {}, {}", base_page_size(),
                     buffer.no_hugepages() ? "accepted" : "not available",
                     huge_kb ? std::format("{} kB in transparent hugepages", *huge_kb)
                             : unavailable("no account of hugepages"));
}

// Warms the buffer to its steady state, then counts one span.
ChaseRow chase_one_size(std::size_t bytes, const Options& options, const TimerInfo& timer,
                        CounterGroup& group) {
  ChaseRow row{.bytes = bytes,
               .lines = bytes / kChaseLineBytes,
               .pages = {},
               .ns_per_load = 0.0,
               .agreement = {},
               .events = {}};
  const ChaseBuffer buffer{bytes};
  const std::span<ChaseLine> lines = buffer.lines();
  if (lines.empty()) {
    row.agreement = unavailable("the buffer could not be mapped");
    return row;
  }
  link_single_cycle(lines, kBaseSeed + bytes);
  row.pages = pages_field(buffer);
  const ChaseLine* at = chase(lines.data(), kWarmLaps * row.lines);

  std::uint64_t started = 0;
  std::uint64_t stopped = 0;
  group.start();
  const Agreement agreement = cross_check(group, [&] {
    started = ticks_start();
    at = chase(at, options.loads);
    stopped = ticks_stop();
  });
  group.stop();
  row.ns_per_load = timer.ticks_to_ns(static_cast<double>(stopped - started)) /
                    static_cast<double>(options.loads);
  row.agreement = describe_agreement(agreement);

  const std::vector<std::string> names = group.event_names();
  const std::vector<CounterStatus> statuses = group.statuses();
  for (std::size_t i = 0; i < names.size() && i < agreement.events.size(); ++i) {
    for (const CounterStatus& status : statuses) {
      if (status.name == names[i]) {
        row.events.push_back({.name = names[i], .paths = agreement.events[i], .status = status});
      }
    }
  }
  std::println("{:>10} {:>8.2f} ns/load {}", bytes, row.ns_per_load, row.agreement);
  static_cast<void>(std::fflush(stdout));
  // A use of the pointer, so that the loop is not dropped.
  return at == nullptr ? ChaseRow{} : row;
}

std::string event_json(const ChaseEvent& event) {
  return std::format(R"({{"name": {}, "read": {}, "rdpmc": {}, "state": {}, "ratio": {:.6f}}})",
                     json_string(event.name), event.paths.by_read, event.paths.by_rdpmc,
                     json_string(to_string(event.status.state)), event.status.running_ratio);
}

std::string row_json(const ChaseRow& row) {
  std::string events;
  for (const ChaseEvent& event : row.events) {
    events += std::format("{}{}", events.empty() ? "" : ", ", event_json(event));
  }
  return std::format(
      R"(    {{"bytes": {}, "lines": {}, "pages": {}, "ns_per_load": {:.3f}, "agreement": {}, "events": [{}]}})",
      row.bytes, row.lines, json_string(row.pages), row.ns_per_load, json_string(row.agreement),
      events);
}

std::string record_json(const std::vector<HeaderField>& header, const Options& options,
                        const std::vector<ChaseRow>& rows, const MigrationTracker& migrations,
                        bool pinned, const std::vector<std::string>& refusals) {
  std::string out = "{\n  \"header\": {";
  for (std::size_t i = 0; i < header.size(); ++i) {
    out += std::format("{}\n    {}: {}", i == 0 ? "" : ",", json_string(header[i].first),
                       json_string(header[i].second));
  }
  out += std::format("\n  }},\n  \"raw_event\": \"0x{:04x}\",\n  \"loads\": {},\n",
                     options.raw_event, options.loads);
  out += "  \"rows\": [\n";
  for (std::size_t i = 0; i < rows.size(); ++i) {
    out += row_json(rows[i]) + (i + 1 == rows.size() ? "\n" : ",\n");
  }
  out += std::format("  ],\n  \"pinned\": {},\n  \"migrations\": {},\n  \"peak_rss_bytes\": {},\n",
                     pinned ? "true" : "false", migrations.migrations(), peak_rss_bytes());
  out += "  \"refusals\": [";
  for (std::size_t i = 0; i < refusals.size(); ++i) {
    out += std::format("{}{}", i == 0 ? "" : ", ", json_string(refusals[i]));
  }
  return out + "]\n}\n";
}

std::vector<CounterStatus> last_statuses(const std::vector<ChaseRow>& rows) {
  std::vector<CounterStatus> out;
  if (!rows.empty()) {
    for (const ChaseEvent& event : rows.back().events) {
      out.push_back(event.status);
    }
  }
  return out;
}

int run(int argc, char** argv) {
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: bench_chase --out RUN.json [--core N] [--commit TEXT] [--loads N] "
                 "[--sizes 64K,1M,...] [--raw-event HEX]");
    return 2;
  }
  const SysSource source = live_source();
  const PinOutcome pin = pin_current_thread(options.core);
  const TimerInfo timer = measure_timer();
  MigrationTracker migrations;
  migrations.sample();

  std::vector<std::string> refusals;
  if (kPinningRequired && !pin.pinned) {
    refusals.push_back(pinning_field(pin));
  }
  CounterGroup group{events_for_chase(options.raw_event)};
  std::vector<ChaseRow> rows;
  if (refusals.empty()) {
    for (const std::size_t bytes : options.sizes) {
      rows.push_back(chase_one_size(bytes, options, timer, group));
      migrations.sample();
    }
  }

  const std::string pages = rows.empty() ? unavailable("no buffer was mapped") : rows.front().pages;
  const std::vector<HeaderField> header = build_header(
      source,
      {.compiler = __VERSION__,
       .flags = ITCH_BENCH_FLAGS,
       .commit = options.commit,
       .dataset = "none: a synthetic pointer chase",
       .dataset_sha256 = "none",
       .timer = describe_rate(timer),
       .quantum = std::format("{} ticks, {:.3f} ns", timer.quantum_ticks, timer.quantum_ns()),
       .pair = std::format("{:.3f} ticks, {:.3f} ns, not subtracted from any figure",
                           timer.pair_ticks, timer.pair_ns()),
       .floor = std::format("{:.3f} ns", timer.floor_ns()),
       .pinning = pinning_field(pin),
       .migrations = migrations.describe(),
       .core = pin.pinned ? std::format("{}", pin.requested_core) : "not pinned",
       .core_number = options.core,
       .counters = describe_counters(last_statuses(rows)),
       .rdpmc = rows.empty() ? unavailable("nothing ran") : rows.back().agreement,
       .page_size = pages,
       .residency = "not applicable: no input file"});

  std::ofstream out{options.out};
  out << record_json(header, options, rows, migrations, pin.pinned, refusals);
  if (!out) {
    std::println(stderr, "bench_chase: cannot write {}", options.out);
    return 2;
  }
  for (const std::string& refusal : refusals) {
    std::println(stderr, "bench_chase: refused: {}", refusal);
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
