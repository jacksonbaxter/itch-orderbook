// What this machine can measure, run at the start of each session. Exits 0 even when a
// counter or the pin is unavailable, because that is the answer, not a failure.
//
//     bench_probe [--core N]
//     bench_probe --check FILE [--core N]    only the lines scripts/machine_check.sh reads
//     bench_probe --cross-core PEER [--core N] [--trips N]
//                                            whether the two cores' clocks agree
//                                            (platform/cross_core.hpp); exits 1 if not,
//                                            or if either thread could not be pinned
//                                            where pinning is required
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <format>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "mapped_file.hpp"
#include "platform/affinity.hpp"
#include "platform/counters.hpp"
#include "platform/cross_core.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"

namespace {

using namespace itch::bench;

struct Options {
  int core = -1;
  int cross_core = -1;
  std::size_t trips = 1'000'000;
  std::string check_file;
};

template <class Number>
bool parse_number(std::string_view value, Number& out) {
  const auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), out);
  return error == std::errc{} && end == value.data() + value.size();
}

bool parse(std::span<char*> args, Options& options) {
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string_view arg{args[i]};
    if (i + 1 == args.size()) {
      return false;
    }
    const std::string_view value{args[++i]};
    if (arg == "--core") {
      if (!parse_number(value, options.core)) {
        return false;
      }
    } else if (arg == "--cross-core") {
      if (!parse_number(value, options.cross_core) || options.cross_core < 0) {
        return false;
      }
    } else if (arg == "--trips") {
      if (!parse_number(value, options.trips) || options.trips == 0) {
        return false;
      }
    } else if (arg == "--check") {
      options.check_file = value;
    } else {
      return false;
    }
  }
  return true;
}

int run_check(const Options& options) {
  const TimerInfo timer = measure_timer();
  std::println("timer_quantum_ns: {:.3f}", timer.quantum_ns());
  std::println("timer_calibrated_mhz: {:.3f}", timer.calibrated_hz / 1e6);
  const auto file = itch::tools::MappedFile::open(options.check_file.c_str());
  if (!file) {
    std::println("resident_fraction: 0.000000");
    std::println("residency: unavailable: the input could not be mapped");
    return 1;
  }
  const Residency residency = resident(file->bytes());
  std::println("resident_fraction: {:.6f}", residency.fraction());
  std::println("residency: {}", residency.describe());
  return timer.quantum_ticks == 0 ? 1 : 0;
}

int run_cross_core(const Options& options) {
  const TimerInfo timer = measure_timer();
  std::vector<CrossCoreTrip> buffer(options.trips);
  const CrossCoreRun run = check_cross_core(options.core, options.cross_core, buffer);
  const OffsetBound bound = combine(run.self_started, run.peer_started);
  const auto ns = [&](std::int64_t ticks) { return timer.ticks_to_ns(static_cast<double>(ticks)); };
  const bool pinned = run.self_pin.pinned && run.peer_pin.pinned;
  std::println("cross_core_timer: {}", describe_rate(timer));
  std::println("cross_core_pinning: this thread {}; the peer {}", pinning_field(run.self_pin),
               pinning_field(run.peer_pin));
  std::println("cross_core_trips: {} started on each core", options.trips);
  std::println(
      "cross_core_offset: core {}'s clock minus core {}'s within [{}, {}] ticks, "
      "[{:.1f}, {:.1f}] ns",
      options.cross_core, options.core, bound.low, bound.high, ns(bound.low), ns(bound.high));
  std::println("cross_core_started_here: [{}, {}] ticks; started on the peer: [{}, {}] ticks",
               run.self_started.low, run.self_started.high, -run.peer_started.high,
               -run.peer_started.low);
  std::println("cross_core_fastest_round_trip: {} ticks, {:.1f} ns", bound.fastest_round_trip,
               ns(static_cast<std::int64_t>(bound.fastest_round_trip)));
  std::println("cross_core_backwards: {} of {} trips read a later stamp below an earlier one",
               bound.backwards, 2 * options.trips);
  if (kPinningRequired && !pinned) {
    std::println("cross_core_verdict: unpinned: the two threads were not both pinned");
    return 1;
  }
  if (!bound.consistent()) {
    std::println(
        "cross_core_verdict: disagree: no single offset fits every trip, so a clock "
        "went backwards between the cores");
    return 1;
  }
  if (!bound.holds_zero()) {
    std::println("cross_core_verdict: disagree: the offset is bounded away from 0");
    return 1;
  }
  std::println(
      "cross_core_verdict: agree: the bound holds 0, {:.1f} ns wide, and every "
      "one-way latency between these cores carries at most that much error from the clocks",
      ns(bound.high - bound.low));
  return 0;
}

void spin_for_counters() {
  volatile std::uint64_t sink = 0;
  for (std::uint64_t i = 0; i < 2'000'000; ++i) {
    sink = sink + i;
  }
}

int run(int argc, char** argv) {
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: bench_probe [--core N] [--check FILE] [--cross-core PEER [--trips N]]");
    return 2;
  }
  if (!options.check_file.empty()) {
    return run_check(options);
  }
  if (options.cross_core >= 0) {
    return run_cross_core(options);
  }

  const SysSource source = live_source();
  const std::string cpu = read_cpu_model(source);
  const PinOutcome pin = pin_current_thread(options.core);
  const TimerInfo timer = measure_timer();
  std::println("host: {}", host_label(source.os, cpu));
  std::println("cpu: {}", cpu);
  std::println("kernel: {}", read_kernel());
  std::println("timer: {}", describe_rate(timer));
  std::println("timer_quantum: {} ticks, {:.3f} ns", timer.quantum_ticks, timer.quantum_ns());
  std::println("timer_empty_pair: {:.3f} ticks, {:.3f} ns, {:.1f}% of pairs saw no tick",
               timer.pair_ticks, timer.pair_ns(), 100.0 * timer.zero_fraction);
  std::println("timer_floor: {:.3f} ns ({} quanta)", timer.floor_ns(), kResolvableQuanta);
  std::println("per_message_timing: {}", kTimerPerMessage ? "yes" : "no, batches only");
  std::println("pin: {}", pinning_field(pin));

  MigrationTracker migrations;
  migrations.sample();
  CounterGroup group{host_label(source.os, cpu)};
  group.start();
  spin_for_counters();
  group.stop();
  migrations.sample();
  std::println("current_core: {}", migrations.last_core());
  std::println("counters: {}", describe_counters(group.statuses()));
  std::println("rdpmc: {}", probe_rdpmc(group));
  std::println("perf_event_paranoid: {}",
               trim(read_whole_file("/proc/sys/kernel/perf_event_paranoid")
                        .value_or(unavailable("no such file on this host"))));

  const std::optional<Arena> arena = Arena::make(std::size_t{64} * 1024 * 1024);
  std::println("arena: {}",
               arena ? describe_arena(arena->report()) : unavailable("the mapping failed"));
  std::println("governor: {}", read_governor(source, options.core));
  std::println("frequency: {}", read_frequency(source, options.core));
  std::println("turbo: {}", read_turbo(source));
  std::println("smt: {}", read_smt(source));
  std::println("isolation: {}", read_isolation(source));
  return 0;
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
