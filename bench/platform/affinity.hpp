// Pinning, and an honest account of it. pin_current_thread() returns what happened, and
// nothing outside this file decides that a thread is pinned: a claim is made only from
// an affinity read back from the kernel as exactly the core that was asked for.
#pragma once

#include <cstddef>
#include <cstdint>
#include <format>
#include <optional>
#include <span>
#include <string>
#include <utility>
#include <vector>

#if defined(__linux__) && !defined(__EMSCRIPTEN__)
#include <pthread.h>
#include <sched.h>

#include <cstring>
#elif defined(__APPLE__)
#include <mach/mach.h>
#include <mach/thread_policy.h>
#include <pthread.h>
#include <pthread/qos.h>
#include <sys/qos.h>
#endif

namespace itch::bench {

struct PinOutcome {
  int requested_core = -1;  // -1 when no core was asked for.
  bool pinned = false;
  std::string attempt;  // What was tried and what each call returned.
};

// A thread is pinned when the set call succeeded and the set read back is exactly the
// one core. A wider mask, or an empty one, is a failure however the call returned.
[[nodiscard]] inline PinOutcome judge_pin(int requested_core, int set_result,
                                          std::span<const int> readback) {
  PinOutcome outcome{.requested_core = requested_core, .pinned = false, .attempt = {}};
  outcome.pinned = requested_core >= 0 && set_result == 0 && readback.size() == 1 &&
                   readback.front() == requested_core;
  std::string mask;
  for (const int core : readback) {
    mask += std::format("{}{}", mask.empty() ? "" : ",", core);
  }
  outcome.attempt =
      std::format("pthread_setaffinity_np returned {}, read back [{}]", set_result, mask);
  return outcome;
}

// The results header's pinning field.
[[nodiscard]] inline std::string pinning_field(const PinOutcome& outcome) {
  if (outcome.pinned) {
    return std::format("pinned to core {}: {}", outcome.requested_core, outcome.attempt);
  }
  return std::format("not pinned: {}", outcome.attempt);
}

#if defined(__linux__) && !defined(__EMSCRIPTEN__)

[[nodiscard]] inline PinOutcome pin_current_thread(int core) {
  if (core < 0) {
    return {.requested_core = core, .pinned = false, .attempt = "no core was requested"};
  }
  cpu_set_t wanted;
  CPU_ZERO(&wanted);
  CPU_SET(static_cast<std::size_t>(core), &wanted);
  const int set_result = ::pthread_setaffinity_np(::pthread_self(), sizeof(wanted), &wanted);
  cpu_set_t actual;
  CPU_ZERO(&actual);
  std::vector<int> readback;
  if (::pthread_getaffinity_np(::pthread_self(), sizeof(actual), &actual) == 0) {
    for (int candidate = 0; candidate < CPU_SETSIZE; ++candidate) {
      if (CPU_ISSET(static_cast<std::size_t>(candidate), &actual)) {
        readback.push_back(candidate);
      }
    }
  }
  return judge_pin(core, set_result, readback);
}

[[nodiscard]] inline int current_core() noexcept { return ::sched_getcpu(); }

// A failed pin is fatal for publishing here. On macOS it is a recorded fact.
inline constexpr bool kPinningRequired = true;

#elif defined(__APPLE__)

// macOS has no way to pin. thread_policy_set is tried so that the code it returns is on
// record, 46 (KERN_NOT_SUPPORTED) on Apple silicon, and a high QoS class asks for the
// performance cores, which is a hint and never a pin.
[[nodiscard]] inline PinOutcome pin_current_thread(int core) {
  const int qos = ::pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
  thread_affinity_policy_data_t policy{.affinity_tag = core < 0 ? 1 : core + 1};
  const kern_return_t code =
      ::thread_policy_set(::mach_thread_self(), THREAD_AFFINITY_POLICY,
                          // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
                          reinterpret_cast<thread_policy_t>(&policy), THREAD_AFFINITY_POLICY_COUNT);
  return {.requested_core = core,
          .pinned = false,
          .attempt = std::format("thread_policy_set returned code {}, "
                                 "pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE) "
                                 "returned {}",
                                 code, qos)};
}

[[nodiscard]] inline int current_core() noexcept {
  std::size_t core = 0;
  return ::pthread_cpu_number_np(&core) == 0 ? static_cast<int>(core) : -1;
}

inline constexpr bool kPinningRequired = false;

#else

[[nodiscard]] inline PinOutcome pin_current_thread(int core) {
  return {.requested_core = core, .pinned = false, .attempt = "no affinity call on this platform"};
}
[[nodiscard]] inline int current_core() noexcept { return -1; }
inline constexpr bool kPinningRequired = false;

#endif

// Sampled between messages, outside every timed span.
class MigrationTracker {
 public:
  void sample(int core) noexcept {
    ++samples_;
    if (core < 0) {
      return;
    }
    if (last_ >= 0 && core != last_) {
      ++migrations_;
    }
    first_ = first_ < 0 ? core : first_;
    last_ = core;
  }
  void sample() noexcept { sample(current_core()); }

  [[nodiscard]] std::uint64_t migrations() const noexcept { return migrations_; }
  [[nodiscard]] int first_core() const noexcept { return first_; }
  [[nodiscard]] int last_core() const noexcept { return last_; }

  [[nodiscard]] std::string describe() const {
    if (last_ < 0) {
      return "unavailable: the current core could not be read";
    }
    return std::format("{} seen in {} samples, core {} to core {}", migrations_, samples_, first_,
                       last_);
  }

 private:
  std::uint64_t migrations_ = 0;
  std::uint64_t samples_ = 0;
  int first_ = -1;
  int last_ = -1;
};

}  // namespace itch::bench
