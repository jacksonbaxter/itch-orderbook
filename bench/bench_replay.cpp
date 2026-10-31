// Replays a day or a fixture through the book, with Google Benchmark running the two
// timed passes and HdrHistogram holding the latency distribution.
//
//     bench_replay --input FILE --out RUN.json [--core N] [--commit TEXT] [--dataset NAME]
//                  [--dataset-sha256 HEX] [--orders N] [--levels N] [--benchmark_...]
//
// One process is one repetition: scripts/bench_driver.py starts a fresh one for each.
// The record goes to --out whatever happens. Exit codes: 0 publishable, 3 refused, 2 bad
// usage. A refused run is a record of a refusal, never a number.
#include <benchmark/benchmark.h>
#include <hdr/hdr_histogram.h>
#include <sys/mman.h>

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
#include <vector>

#include "itch/book.hpp"
#include "mapped_file.hpp"
#include "platform/affinity.hpp"
#include "platform/counters.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"
#include "replay.hpp"
#include "run_record.hpp"

namespace {

using namespace itch::bench;
using Book = itch::FastBook<>;
using Session = ReplaySession<class ReplayInput, PlatformClock, Book>;

class ReplayInput {
 public:
  explicit ReplayInput(std::span<const std::byte> bytes) noexcept : bytes_{bytes} {}
  [[nodiscard]] std::span<const std::byte> bytes() const noexcept { return bytes_; }
  [[nodiscard]] Residency residency() const { return resident(bytes_); }

 private:
  std::span<const std::byte> bytes_;
};

// A histogram of ticks. Wide enough for a span of four seconds at any clock in use.
class Histogram {
 public:
  Histogram() { hdr_init(1, 10'000'000'000, 3, &histogram_); }
  Histogram(const Histogram&) = delete;
  Histogram& operator=(const Histogram&) = delete;
  Histogram(Histogram&&) = delete;
  Histogram& operator=(Histogram&&) = delete;
  ~Histogram() {
    if (histogram_ != nullptr) {
      hdr_close(histogram_);
    }
  }
  void record(std::uint64_t value) noexcept {
    hdr_record_value(histogram_, static_cast<std::int64_t>(value));
  }
  [[nodiscard]] double at(double percentile) const noexcept {
    return static_cast<double>(hdr_value_at_percentile(histogram_, percentile));
  }
  [[nodiscard]] double highest() const noexcept { return static_cast<double>(hdr_max(histogram_)); }

 private:
  hdr_histogram* histogram_ = nullptr;
};

struct Options {
  std::string input;
  std::string out;
  std::string commit;
  std::string dataset;
  std::string dataset_sha256;
  int core = -1;
  itch::BookCapacity capacity;
};

template <class Number>
bool parse_number(std::string_view text, Number& out) {
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), out);
  return error == std::errc{} && end == text.data() + text.size();
}

bool parse(std::span<char*> args, Options& options) {
  for (std::size_t i = 0; i + 1 < args.size(); i += 2) {
    const std::string_view arg{args[i]};
    const std::string_view value{args[i + 1]};
    if (arg == "--input") {
      options.input = value;
    } else if (arg == "--out") {
      options.out = value;
    } else if (arg == "--commit") {
      options.commit = value;
    } else if (arg == "--dataset") {
      options.dataset = value;
    } else if (arg == "--dataset-sha256") {
      options.dataset_sha256 = value;
    } else if (arg == "--core") {
      if (!parse_number(value, options.core)) {
        return false;
      }
    } else if (arg == "--orders") {
      if (!parse_number(value, options.capacity.orders)) {
        return false;
      }
    } else if (arg == "--levels") {
      if (!parse_number(value, options.capacity.levels)) {
        return false;
      }
    } else {
      return false;
    }
  }
  return args.size() % 2 == 0 && !options.input.empty() && !options.out.empty();
}

void run_throughput(benchmark::State& state, Session* session, const TimerInfo* timer) {
  for (const auto iteration : state) {
    static_cast<void>(iteration);
    if (!session->timed_throughput()) {
      state.SkipWithError("the run was refused");
      return;
    }
    const PassTotals pass = session->report().throughput.value_or(PassTotals{});
    const double seconds = timer->ticks_to_ns(static_cast<double>(pass.ticks)) / 1e9;
    state.SetIterationTime(seconds);
    state.counters["msg_per_s"] = static_cast<double>(pass.messages) / seconds;
  }
}

void run_latency(benchmark::State& state, Session* session, const TimerInfo* timer,
                 SampleBuffer* samples) {
  for (const auto iteration : state) {
    static_cast<void>(iteration);
    if (!session->timed_latency(*samples)) {
      state.SkipWithError("the run was refused");
      return;
    }
    const PassTotals pass = session->report().latency.value_or(PassTotals{});
    state.SetIterationTime(timer->ticks_to_ns(static_cast<double>(pass.ticks)) / 1e9);
  }
}

LatencyRecord summarise(const SampleBuffer& samples, const TimerInfo& timer) {
  static constexpr std::array kPercentiles{50.0, 90.0, 99.0, 99.9, 99.99};
  LatencyRecord record;
  record.samples = samples.samples().size();
  if (record.samples == 0) {
    return record;
  }
  Histogram histogram;
  std::uint64_t sum = 0;
  for (const std::uint32_t ticks : samples.samples()) {
    histogram.record(ticks);
    sum += ticks;
  }
  for (const double percentile : kPercentiles) {
    record.percentiles.push_back(classify_percentile(percentile, histogram.at(percentile), timer));
  }
  record.max_ns = classify_percentile(100.0, histogram.highest(), timer).value_ns;
  record.mean_ns =
      timer.ticks_to_ns(static_cast<double>(sum) / static_cast<double>(record.samples));
  return record;
}

// Per-message counts on one histogram for each event.
class CountSink {
 public:
  explicit CountSink(std::size_t events) : histograms_(events), sums_(events, 0) {}
  void push(std::size_t event, std::uint64_t count) noexcept {
    histograms_[event].record(count);
    sums_[event] += count;
  }
  [[nodiscard]] std::vector<PerMessageCount> summary(std::span<const std::string> names,
                                                     std::uint64_t messages) const {
    std::vector<PerMessageCount> out;
    for (std::size_t event = 0; event < names.size() && messages != 0; ++event) {
      out.push_back({.name = names[event],
                     .mean = static_cast<double>(sums_[event]) / static_cast<double>(messages),
                     .p50 = static_cast<std::uint64_t>(histograms_[event].at(50.0)),
                     .p99 = static_cast<std::uint64_t>(histograms_[event].at(99.0))});
    }
    return out;
  }

 private:
  std::vector<Histogram> histograms_;
  std::vector<std::uint64_t> sums_;
};

// Untimed, and apart from the timing passes. Hardware events never fail a run.
CounterRecord counter_pass(std::span<const std::byte> input, const Options& options,
                           CounterGroup& group, std::uint64_t messages) {
  const auto book = std::make_unique<Book>(options.capacity);
  CountSink sink{group.event_count()};
  CounterRecord record;
  std::uint64_t missed = 0;
  group.start();
  const Agreement agreement =
      cross_check(group, [&] { missed = counts_pass(input, *book, group, sink); });
  group.stop();
  record.statuses = group.statuses();
  record.agreement = group.event_count() == 0 ? unavailable("no user-mode rdpmc on this host")
                                              : describe_agreement(agreement);
  record.per_message_published = agreement.state == AgreementState::agreed && missed == 0;
  if (record.per_message_published) {
    record.per_message = sink.summary(group.event_names(), messages);
  }
  return record;
}

std::string core_field(const PinOutcome& pin, const MigrationTracker& migrations) {
  if (pin.pinned) {
    return std::format("{}", pin.requested_core);
  }
  if (migrations.last_core() < 0) {
    return unavailable("the current core could not be read");
  }
  return std::format("unpinned, last ran on core {}", migrations.last_core());
}

int run(int argc, char** argv) {
  benchmark::Initialize(&argc, argv);
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: bench_replay --input FILE --out RUN.json [--core N] "
                 "[--commit TEXT] [--dataset NAME] [--dataset-sha256 HEX] "
                 "[--orders N] [--levels N]");
    return 2;
  }
  const auto file = itch::tools::MappedFile::open(options.input.c_str());
  if (!file) {
    std::println(stderr, "bench_replay: cannot map {}", options.input);
    return 2;
  }

  // MappedFile asks for sequential access, and a kernel may then drop pages behind the
  // reader. Here every pass needs them to stay.
  ::posix_madvise(const_cast<std::byte*>(file->bytes().data()),  // NOLINT
                  file->bytes().size(), POSIX_MADV_NORMAL);

  const SysSource source = live_source();
  const std::string_view host = host_label(source.os, read_cpu_model(source));
  const PinOutcome pin = pin_current_thread(options.core);
  const TimerInfo timer = measure_timer();
  MigrationTracker migrations;
  migrations.sample();

  ReplayInput input{file->bytes()};
  PlatformClock clock;
  Session session{input, clock, [&] { return std::make_unique<Book>(options.capacity); }};
  if (kPinningRequired && !pin.pinned) {
    session.refuse(pinning_field(pin));
  }
  session.warm_up();
  migrations.sample();

  const std::uint64_t messages = session.report().warmup_messages;
  const std::optional<Arena> arena = Arena::make(messages * sizeof(std::uint32_t));
  if (!arena) {
    session.refuse("no arena for the latency samples");
  }
  SampleBuffer samples{arena ? std::span<std::byte>{arena->data(), arena->size()}
                             : std::span<std::byte>{}};

  benchmark::RegisterBenchmark("replay/throughput", run_throughput, &session, &timer)
      ->UseManualTime()
      ->Iterations(1)
      ->Unit(benchmark::kMillisecond);
  benchmark::RegisterBenchmark("replay/latency", run_latency, &session, &timer, &samples)
      ->UseManualTime()
      ->Iterations(1)
      ->Unit(benchmark::kMillisecond);
  benchmark::RunSpecifiedBenchmarks();
  benchmark::Shutdown();
  migrations.sample();

  CounterGroup group{host};
  RunRecord record;
  if (session.report().publishable()) {
    record.latency = summarise(samples, timer);
    record.counters = counter_pass(input.bytes(), options, group, messages);
  } else {
    record.counters.agreement = "unavailable: the run was refused";
  }
  migrations.sample();

  record.session = session.report();
  record.quantum_ns = timer.quantum_ns();
  record.pair_ns = timer.pair_ns();
  record.floor_ns = timer.floor_ns();
  record.calibrated_hz = timer.calibrated_hz;
  record.peak_rss_bytes = peak_rss_bytes();
  record.migrations = migrations.migrations();
  record.pinned = pin.pinned;
  record.header = build_header(
      source,
      {.compiler = __VERSION__,
       .flags = ITCH_BENCH_FLAGS,
       .commit = options.commit,
       .dataset = options.dataset,
       .dataset_sha256 = options.dataset_sha256,
       .timer = describe_rate(timer),
       .quantum = std::format("{} ticks, {:.3f} ns", timer.quantum_ticks, timer.quantum_ns()),
       .pair = std::format("{:.3f} ticks, {:.3f} ns, not subtracted from any figure",
                           timer.pair_ticks, timer.pair_ns()),
       .floor = std::format("{:.3f} ns", timer.floor_ns()),
       .pinning = pinning_field(pin),
       .migrations = migrations.describe(),
       .core = core_field(pin, migrations),
       .core_number = options.core,
       .counters = describe_counters(record.counters.statuses),
       .rdpmc = record.counters.agreement,
       .page_size = arena ? describe_arena(arena->report()) : std::string{},
       .residency = session.report().residency});

  std::ofstream out{options.out};
  out << to_json(record, timer);
  if (!out) {
    std::println(stderr, "bench_replay: cannot write {}", options.out);
    return 2;
  }
  for (const std::string& refusal : session.report().refusals) {
    std::println(stderr, "bench_replay: refused: {}", refusal);
  }
  return session.report().publishable() ? 0 : 3;
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
