// The benchmark clock, one per platform, and the probe that says what it can resolve.
//
// Every run measures its own clock rather than trusting a nominal rate: the probe gives
// the quantum and the cost of an empty pair, and calibrate_hz() the rate against the
// kernel's raw clock. A tick is a platform unit and only calibrated_hz turns it into
// time. On debian perf cycles and TSC ticks are different units.
//
// The platform split lives here and in the other bench/platform headers, never in the
// book or the parser.
#pragma once

#include <algorithm>
#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <format>
#include <numeric>
#include <string>
#include <string_view>

#if defined(__APPLE__)
#include <mach/mach_time.h>
#elif defined(__EMSCRIPTEN__)
#include <emscripten/emscripten.h>
#elif defined(__linux__) && (defined(__x86_64__) || defined(__aarch64__))
#include <time.h>  // NOLINT(modernize-deprecated-headers): clock_gettime, not in <ctime>.
#else
#error "bench/platform/timer.hpp has no clock for this target"
#endif

namespace itch::bench {

// A percentile under this many quanta above the pair cost is not a measurement.
inline constexpr double kResolvableQuanta = 4.0;

#if defined(__APPLE__)

inline constexpr std::string_view kTimerSource = "mach_absolute_time";
inline constexpr bool kTimerPerMessage = true;

[[nodiscard]] inline std::uint64_t ticks_start() noexcept { return mach_absolute_time(); }
[[nodiscard]] inline std::uint64_t ticks_stop() noexcept { return mach_absolute_time(); }

[[nodiscard]] inline double nominal_tick_hz() noexcept {
  mach_timebase_info_data_t base{};
  if (mach_timebase_info(&base) != KERN_SUCCESS || base.numer == 0) {
    return 0.0;
  }
  return 1e9 * static_cast<double>(base.denom) / static_cast<double>(base.numer);
}

#elif defined(__EMSCRIPTEN__)

// WebAssembly has no cycle counter, and the browser clamps this clock. Batches only.
inline constexpr std::string_view kTimerSource = "emscripten_get_now (batches only)";
inline constexpr bool kTimerPerMessage = false;

[[nodiscard]] inline std::uint64_t ticks_start() noexcept {
  return static_cast<std::uint64_t>(emscripten_get_now() * 1e6);
}
[[nodiscard]] inline std::uint64_t ticks_stop() noexcept { return ticks_start(); }
[[nodiscard]] inline double nominal_tick_hz() noexcept { return 1e9; }

#elif defined(__x86_64__)

inline constexpr std::string_view kTimerSource = "lfence;rdtsc and rdtscp;lfence";
inline constexpr bool kTimerPerMessage = true;

[[nodiscard]] inline std::uint64_t ticks_start() noexcept {
  std::uint32_t low = 0;
  std::uint32_t high = 0;
  __asm__ volatile("lfence\n\trdtsc" : "=a"(low), "=d"(high) : : "memory");
  return (std::uint64_t{high} << 32U) | low;
}

[[nodiscard]] inline std::uint64_t ticks_stop() noexcept {
  std::uint32_t low = 0;
  std::uint32_t high = 0;
  __asm__ volatile("rdtscp\n\tlfence" : "=a"(low), "=d"(high) : : "rcx", "memory");
  return (std::uint64_t{high} << 32U) | low;
}

// The TSC's nominal rate has no user-mode read. calibrate_hz() is the only figure here.
[[nodiscard]] inline double nominal_tick_hz() noexcept { return 0.0; }

#else  // Linux arm64.

inline constexpr std::string_view kTimerSource = "isb;cntvct_el0";
inline constexpr bool kTimerPerMessage = true;

[[nodiscard]] inline std::uint64_t ticks_start() noexcept {
  std::uint64_t value = 0;
  __asm__ volatile("isb\n\tmrs %0, cntvct_el0" : "=r"(value) : : "memory");
  return value;
}
[[nodiscard]] inline std::uint64_t ticks_stop() noexcept { return ticks_start(); }

[[nodiscard]] inline double nominal_tick_hz() noexcept {
  std::uint64_t value = 0;
  __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(value));
  return static_cast<double>(value);
}

#endif

// The clock calibration runs against. CLOCK_MONOTONIC_RAW on Linux: the two kernel
// clocks disagree there by 0.1%, and this one is not slewed.
[[nodiscard]] inline std::int64_t reference_ns() noexcept {
#if defined(__linux__) && !defined(__EMSCRIPTEN__)
  timespec now{};
  ::clock_gettime(CLOCK_MONOTONIC_RAW, &now);
  return std::int64_t{now.tv_sec} * 1'000'000'000 + std::int64_t{now.tv_nsec};
#else
  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
#endif
}

struct TimerProbe {
  std::uint64_t quantum_ticks = 0;  // 0 when no read ever moved the clock.
  double pair_ticks = 0.0;          // Mean of an empty pair, so finer than a tick.
  double zero_fraction = 0.0;       // Pairs that saw no tick at all.
  std::size_t samples = 0;
};

// The quantum is the greatest common divisor of the observed steps, not the smallest
// step: the TSC on debian moves in 20s and a pair costs about 38 ticks, so the smallest
// step seen is 40 while the clock can still tell 20 apart.
template <class Start, class Stop>
[[nodiscard]] TimerProbe probe_timer(Start start, Stop stop, std::size_t samples) {
  std::uint64_t divisor = 0;
  std::uint64_t total = 0;
  std::size_t zero = 0;
  for (std::size_t i = 0; i < samples; ++i) {
    const std::uint64_t before = start();
    const std::uint64_t after = stop();
    const std::uint64_t step = after - before;
    total += step;
    if (step == 0) {
      ++zero;
    } else {
      divisor = std::gcd(divisor, step);
    }
  }
  const double count = static_cast<double>(samples == 0 ? 1 : samples);
  return {.quantum_ticks = divisor,
          .pair_ticks = static_cast<double>(total) / count,
          .zero_fraction = static_cast<double>(zero) / count,
          .samples = samples};
}

// Ticks per second. The median of a few short windows, so that one preemption does not
// set the rate for the run.
[[nodiscard]] inline double calibrate_hz(std::chrono::milliseconds window) noexcept {
  constexpr std::size_t kWindows = 5;
  std::array<double, kWindows> rates{};
  const std::int64_t window_ns = window.count() * 1'000'000;
  for (double& rate : rates) {
    const std::int64_t wall_start = reference_ns();
    const std::uint64_t tick_start = ticks_start();
    std::int64_t wall_end = wall_start;
    while (wall_end - wall_start < window_ns) {
      wall_end = reference_ns();
    }
    const std::uint64_t tick_end = ticks_stop();
    rate = static_cast<double>(tick_end - tick_start) * 1e9 /
           static_cast<double>(wall_end - wall_start);
  }
  std::ranges::sort(rates);
  return rates[kWindows / 2];
}

struct TimerInfo {
  std::string source;
  double nominal_hz = 0.0;  // 0 when the platform has no nominal rate to read.
  double calibrated_hz = 0.0;
  std::uint64_t quantum_ticks = 0;
  double pair_ticks = 0.0;
  double zero_fraction = 0.0;

  [[nodiscard]] double ticks_to_ns(double ticks) const noexcept {
    return calibrated_hz > 0.0 ? ticks * 1e9 / calibrated_hz : 0.0;
  }
  [[nodiscard]] double quantum_ns() const noexcept {
    return ticks_to_ns(static_cast<double>(quantum_ticks));
  }
  [[nodiscard]] double pair_ns() const noexcept { return ticks_to_ns(pair_ticks); }
  [[nodiscard]] double floor_ticks() const noexcept {
    return kResolvableQuanta * static_cast<double>(quantum_ticks);
  }
  [[nodiscard]] double floor_ns() const noexcept { return ticks_to_ns(floor_ticks()); }
};

// Probes and calibrates. Call once at the start of every run.
[[nodiscard]] inline TimerInfo measure_timer() {
  constexpr std::size_t kSamples = 1U << 18U;
  const TimerProbe probe =
      probe_timer([] { return ticks_start(); }, [] { return ticks_stop(); }, kSamples);
  return {.source = std::string{kTimerSource},
          .nominal_hz = nominal_tick_hz(),
          .calibrated_hz = calibrate_hz(std::chrono::milliseconds{20}),
          .quantum_ticks = probe.quantum_ticks,
          .pair_ticks = probe.pair_ticks,
          .zero_fraction = probe.zero_fraction};
}

[[nodiscard]] inline std::string describe_rate(const TimerInfo& info) {
  std::string text =
      std::format("{}, calibrated {:.3f} MHz", info.source, info.calibrated_hz / 1e6);
  if (info.nominal_hz > 0.0) {
    text += std::format(", nominal {:.3f} MHz", info.nominal_hz / 1e6);
  }
  return text;
}

}  // namespace itch::bench
