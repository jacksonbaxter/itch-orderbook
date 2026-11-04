// Hardware counters. Never fails the build or the run: every event ends as measured,
// unavailable with a reason, or multiplexed with the fraction of time it ran.
//
// Linux: one perf_event_open group, exclude_kernel, read through read() on the group and
// through user-mode rdpmc on each event's mmap page. macOS: thread_selfcounts and
// proc_pid_rusage, cycles and instructions only, and neither can leave out the kernel.
//
// Everything that decides what a reading means sits above the platform split, so that
// the Linux logic is tested on every host. Only the system calls sit below it.
#pragma once

#include <algorithm>
#include <array>
#include <concepts>
#include <cstddef>
#include <cstdint>
#include <format>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#if defined(__linux__) && !defined(__EMSCRIPTEN__)
#include <linux/perf_event.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#elifdef __APPLE__
#include <libproc.h>
#include <sys/resource.h>
#include <unistd.h>

extern "C" int thread_selfcounts(int type, void* buffer, std::size_t bytes);
#endif

namespace itch::bench {

enum class CounterState : std::uint8_t { measured, unavailable, multiplexed };

[[nodiscard]] constexpr std::string_view to_string(CounterState state) noexcept {
  switch (state) {
    case CounterState::measured: return "measured";
    case CounterState::unavailable: return "unavailable";
    case CounterState::multiplexed: return "multiplexed";
  }
  return "unnamed";
}

struct CounterStatus {
  std::string name;
  CounterState state = CounterState::unavailable;
  std::uint64_t value = 0;
  double running_ratio = 0.0;  // Time on a counter over time enabled.
  std::string reason;
};

[[nodiscard]] inline CounterStatus unavailable_counter(std::string name, std::string reason) {
  return {.name = std::move(name),
          .state = CounterState::unavailable,
          .value = 0,
          .running_ratio = 0.0,
          .reason = std::move(reason)};
}

// What one event's reading means. A counter that ran for less than the whole span is
// multiplexed however close to 1 the ratio is: the count is a sample, not a total.
[[nodiscard]] inline CounterStatus classify_counter(std::string name, std::uint64_t value,
                                                    std::uint64_t time_enabled,
                                                    std::uint64_t time_running) {
  if (time_enabled == 0 || time_running == 0) {
    return unavailable_counter(std::move(name), "the group was never scheduled on the PMU");
  }
  const double ratio = static_cast<double>(time_running) / static_cast<double>(time_enabled);
  const CounterState state =
      time_running < time_enabled ? CounterState::multiplexed : CounterState::measured;
  return {.name = std::move(name),
          .state = state,
          .value = value,
          .running_ratio = ratio,
          .reason = {}};
}

// One line of the header's counter status.
[[nodiscard]] inline std::string describe_counter(const CounterStatus& status) {
  switch (status.state) {
    case CounterState::measured: return std::format("{}=measured", status.name);
    case CounterState::multiplexed:
      return std::format("{}=multiplexed ratio {:.4f}", status.name, status.running_ratio);
    case CounterState::unavailable: break;
  }
  return std::format("{}=unavailable: {}", status.name, status.reason);
}

[[nodiscard]] inline std::string describe_counters(std::span<const CounterStatus> statuses) {
  std::string text;
  for (const CounterStatus& status : statuses) {
    text += text.empty() ? "" : "; ";
    text += describe_counter(status);
  }
  return text.empty() ? std::string{"unavailable: no counters on this platform"} : text;
}

// A read() of a group opened with PERF_FORMAT_GROUP, PERF_FORMAT_ID and both times:
// nr, time_enabled, time_running, then nr pairs of value and id.
struct GroupRead {
  std::uint64_t time_enabled = 0;
  std::uint64_t time_running = 0;
  std::vector<std::uint64_t> values;  // In the order of `ids`.
};

[[nodiscard]] inline std::optional<GroupRead> parse_group_read(std::span<const std::uint64_t> words,
                                                               std::span<const std::uint64_t> ids) {
  constexpr std::size_t kHeaderWords = 3;
  if (words.size() < kHeaderWords || words[0] != ids.size() ||
      words.size() < kHeaderWords + (2 * ids.size())) {
    return std::nullopt;
  }
  GroupRead read{.time_enabled = words[1], .time_running = words[2], .values = {}};
  read.values.assign(ids.size(), 0);
  for (std::size_t i = 0; i < ids.size(); ++i) {
    const std::uint64_t value = words[kHeaderWords + (2 * i)];
    const std::uint64_t id = words[kHeaderWords + (2 * i) + 1];
    bool found = false;
    for (std::size_t slot = 0; slot < ids.size(); ++slot) {
      if (ids[slot] == id) {
        read.values[slot] = value;
        found = true;
      }
    }
    if (!found) {
      return std::nullopt;
    }
  }
  return read;
}

// What a counter pass reads from. CounterGroup is one, and a test's mock is another.
template <class Source>
concept CounterSource = requires(Source& source, std::vector<std::uint64_t>& out) {
  { source.event_count() } -> std::convertible_to<std::size_t>;
  { source.read_descriptors(out) } -> std::same_as<bool>;
  { source.read_rdpmc(out) } -> std::same_as<bool>;
};

enum class AgreementState : std::uint8_t { agreed, disagreed, unavailable };

// One event's count over the checked span, by each path.
struct EventAgreement {
  std::uint64_t by_read = 0;
  std::uint64_t by_rdpmc = 0;

  [[nodiscard]] std::uint64_t gap() const noexcept {
    return by_read > by_rdpmc ? by_read - by_rdpmc : by_rdpmc - by_read;
  }
};

struct Agreement {
  AgreementState state = AgreementState::unavailable;
  double worst_relative = 0.0;
  std::uint64_t worst_gap = 0;  // In events. A percentage of a small count says little.
  std::string detail;
  std::vector<EventAgreement> events;
};

// Within this many events, or this fraction, whichever is larger. The two reads cannot
// be simultaneous, so the system call's own user-mode instructions fall between them.
inline constexpr double kAgreementFraction = 0.01;
inline constexpr std::uint64_t kAgreementSlack = 10'000;

[[nodiscard]] inline Agreement compare_counts(std::span<const std::uint64_t> descriptor_before,
                                              std::span<const std::uint64_t> descriptor_after,
                                              std::span<const std::uint64_t> rdpmc_before,
                                              std::span<const std::uint64_t> rdpmc_after) {
  Agreement result{.state = AgreementState::agreed,
                   .worst_relative = 0.0,
                   .worst_gap = 0,
                   .detail = {},
                   .events = {}};
  for (std::size_t i = 0; i < descriptor_before.size(); ++i) {
    const std::uint64_t by_descriptor = descriptor_after[i] - descriptor_before[i];
    const std::uint64_t by_rdpmc = rdpmc_after[i] - rdpmc_before[i];
    const EventAgreement event{.by_read = by_descriptor, .by_rdpmc = by_rdpmc};
    result.events.push_back(event);
    const std::uint64_t gap = event.gap();
    const auto base = static_cast<double>(by_descriptor == 0 ? 1 : by_descriptor);
    const double relative = static_cast<double>(gap) / base;
    result.worst_relative = std::max(relative, result.worst_relative);
    result.worst_gap = std::max(gap, result.worst_gap);
    if (gap > kAgreementSlack && relative > kAgreementFraction) {
      result.state = AgreementState::disagreed;
      result.detail += std::format("event {}: read() {} rdpmc {}; ", i, by_descriptor, by_rdpmc);
    }
  }
  return result;
}

// Runs `work` between a read() and an rdpmc of every event, on both sides.
template <CounterSource Source, class Work>
[[nodiscard]] Agreement cross_check(Source& source, Work&& work) {
  const std::size_t events = source.event_count();
  std::vector<std::uint64_t> descriptor_before(events);
  std::vector<std::uint64_t> descriptor_after(events);
  std::vector<std::uint64_t> rdpmc_before(events);
  std::vector<std::uint64_t> rdpmc_after(events);
  const bool opened =
      events != 0 && source.read_descriptors(descriptor_before) && source.read_rdpmc(rdpmc_before);
  std::forward<Work>(work)();
  const auto unusable = [](std::string detail) {
    return Agreement{.state = AgreementState::unavailable,
                     .worst_relative = 0.0,
                     .worst_gap = 0,
                     .detail = std::move(detail),
                     .events = {}};
  };
  if (events == 0) {
    return unusable("no events");
  }
  if (!opened) {
    return unusable("rdpmc gave no value at the start");
  }
  const bool closed = source.read_rdpmc(rdpmc_after) && source.read_descriptors(descriptor_after);
  if (!closed) {
    return unusable("rdpmc gave no value at the end");
  }
  return compare_counts(descriptor_before, descriptor_after, rdpmc_before, rdpmc_after);
}

[[nodiscard]] inline std::string describe_agreement(const Agreement& agreement) {
  switch (agreement.state) {
    case AgreementState::agreed:
      return std::format("agreed, worst gap {} events, {:.4f}%", agreement.worst_gap,
                         100.0 * agreement.worst_relative);
    case AgreementState::disagreed: return std::format("disagreed: {}", agreement.detail);
    case AgreementState::unavailable: break;
  }
  return std::format("unavailable: {}", agreement.detail);
}

// Each event's count by both paths, for the record.
[[nodiscard]] inline std::string describe_events(const Agreement& agreement,
                                                 std::span<const std::string> names) {
  std::string text;
  for (std::size_t i = 0; i < agreement.events.size() && i < names.size(); ++i) {
    text += std::format("{}{} read() {} rdpmc {}", text.empty() ? "" : "; ", names[i],
                        agreement.events[i].by_read, agreement.events[i].by_rdpmc);
  }
  return text;
}

// Which events a group opens. Plain numbers, so that the encodings are checked on every
// host: the kinds are PERF_TYPE_HARDWARE and PERF_TYPE_RAW, and the hardware configs the
// PERF_COUNT_HW_ values, which the Linux branch below asserts.
enum class EventKind : std::uint8_t { hardware, raw };

struct PerfEventSpec {
  std::string name;
  EventKind kind = EventKind::hardware;
  std::uint64_t config = 0;
};

inline constexpr std::uint64_t kHwCycles = 0;
inline constexpr std::uint64_t kHwInstructions = 1;
inline constexpr std::uint64_t kHwCacheMisses = 3;
inline constexpr std::uint64_t kHwBranchMisses = 5;

// On AMD a raw event is the low 8 bits of the event select, then an 8-bit unit mask.
// PMCx043 unit mask bit 3 (0x08): demand data cache fills from local DRAM.
// PMCx064 unit mask bits 3 and 0 (0x09): data and instruction cache requests missing L2.
[[nodiscard]] constexpr std::uint64_t amd_raw_event(std::uint8_t event,
                                                    std::uint8_t mask) noexcept {
  return (std::uint64_t{mask} << 8U) | event;
}
inline constexpr std::uint64_t kRawDramFills = amd_raw_event(0x43, 0x08);
inline constexpr std::uint64_t kRawL2Misses = amd_raw_event(0x64, 0x09);

inline constexpr std::string_view kCacheMissesName = "cache-misses (L2 misses on Zen 1)";
inline constexpr std::string_view kDramFillsName = "r0843:u (a floor on DRAM demand fills)";

// cache-misses counts L2 misses on Zen 1, so it is named for what it counts. The raw
// event is a last-level stand-in, for debian only: results/chase_debian.md is what lets a
// book number use it, and it means nothing on another CPU.
[[nodiscard]] inline std::vector<PerfEventSpec> events_for_host(std::string_view host_label) {
  std::vector<PerfEventSpec> events{
      {.name = "cycles", .kind = EventKind::hardware, .config = kHwCycles},
      {.name = "instructions", .kind = EventKind::hardware, .config = kHwInstructions},
      {.name = "branch-misses", .kind = EventKind::hardware, .config = kHwBranchMisses},
      {.name = std::string{kCacheMissesName},
       .kind = EventKind::hardware,
       .config = kHwCacheMisses}};
  if (host_label == "debian") {
    events.push_back(
        {.name = std::string{kDramFillsName}, .kind = EventKind::raw, .config = kRawDramFills});
  }
  return events;
}

// What bench_chase counts. The raw form of cache-misses sits beside it, so that a run
// shows the kernel's mapping by counting rather than by reading a table. `dram_fills` is
// a choice so that a deliberately wrong event can be shown failing.
inline constexpr std::string_view kChaseFillsName = "dram_fills";
inline constexpr std::string_view kChaseL2Name = "l2_misses_raw";

[[nodiscard]] inline std::vector<PerfEventSpec> events_for_chase(std::uint64_t dram_fills) {
  return {{.name = "cycles", .kind = EventKind::hardware, .config = kHwCycles},
          {.name = "instructions", .kind = EventKind::hardware, .config = kHwInstructions},
          {.name = std::string{kCacheMissesName},
           .kind = EventKind::hardware,
           .config = kHwCacheMisses},
          {.name = std::string{kChaseFillsName}, .kind = EventKind::raw, .config = dram_fills},
          {.name = std::string{kChaseL2Name}, .kind = EventKind::raw, .config = kRawL2Misses}};
}

#if defined(__linux__) && !defined(__EMSCRIPTEN__)

static_assert(kHwCycles == std::uint64_t{PERF_COUNT_HW_CPU_CYCLES});
static_assert(kHwInstructions == std::uint64_t{PERF_COUNT_HW_INSTRUCTIONS});
static_assert(kHwCacheMisses == std::uint64_t{PERF_COUNT_HW_CACHE_MISSES});
static_assert(kHwBranchMisses == std::uint64_t{PERF_COUNT_HW_BRANCH_MISSES});

#if defined(__x86_64__)
// Fenced both ways, because rdpmc is not serialising: without this a load before it may
// still be in flight, and its count lands on the wrong message.
[[nodiscard]] inline std::uint64_t read_pmc(std::uint32_t counter) noexcept {
  std::uint32_t low = 0;
  std::uint32_t high = 0;
  __asm__ volatile("lfence\n\trdpmc\n\tlfence" : "=a"(low), "=d"(high) : "c"(counter) : "memory");
  return (std::uint64_t{high} << 32U) | low;
}
inline constexpr bool kHasRdpmc = true;
#else
[[nodiscard]] inline std::uint64_t read_pmc(std::uint32_t /*counter*/) noexcept { return 0; }
inline constexpr bool kHasRdpmc = false;
#endif

// The sequence from the comment above struct perf_event_mmap_page in the kernel's
// uapi header. An event not on a counter right now has index 0 and gives nothing here.
[[nodiscard]] inline bool read_mmap_counter(const volatile perf_event_mmap_page* page,
                                            std::uint64_t& out) noexcept {
  std::uint32_t sequence = 0;
  std::uint64_t count = 0;
  bool usable = false;
  do {
    sequence = page->lock;
    __asm__ volatile("" : : : "memory");
    const std::uint32_t index = page->index;
    count = static_cast<std::uint64_t>(page->offset);
    usable = kHasRdpmc && page->cap_user_rdpmc != 0 && index != 0;
    if (usable) {
      const unsigned shift = 64U - page->pmc_width;
      const std::uint64_t raw = read_pmc(index - 1) << shift;
      count += static_cast<std::uint64_t>(static_cast<std::int64_t>(raw) >> shift);
    }
    __asm__ volatile("" : : : "memory");
  } while (page->lock != sequence);
  out = count;
  return usable;
}

class CounterGroup {
 public:
  explicit CounterGroup(std::string_view host_label) : CounterGroup{events_for_host(host_label)} {}
  explicit CounterGroup(const std::vector<PerfEventSpec>& events) {
    for (const PerfEventSpec& spec : events) {
      open_event(spec);
    }
  }
  CounterGroup(const CounterGroup&) = delete;
  CounterGroup& operator=(const CounterGroup&) = delete;
  CounterGroup(CounterGroup&&) = delete;
  CounterGroup& operator=(CounterGroup&&) = delete;
  ~CounterGroup() {
    for (Open& event : open_) {
      if (event.page != nullptr) {
        ::munmap(event.page, kMapBytes);
      }
      ::close(event.fd);
    }
  }

  void start() noexcept {
    if (open_.empty()) {
      return;
    }
    ioctl_group(PERF_EVENT_IOC_RESET);
    ioctl_group(PERF_EVENT_IOC_ENABLE);
    started_ = read_group();
  }

  void stop() noexcept {
    if (open_.empty()) {
      return;
    }
    ioctl_group(PERF_EVENT_IOC_DISABLE);
    stopped_ = read_group();
  }

  // Each event's state over the last start() to stop().
  [[nodiscard]] std::vector<CounterStatus> statuses() const {
    std::vector<CounterStatus> out = failed_;
    for (std::size_t i = 0; i < open_.size(); ++i) {
      if (!started_ || !stopped_) {
        out.push_back(unavailable_counter(open_[i].name, "the group gave no read()"));
        continue;
      }
      out.push_back(classify_counter(open_[i].name, stopped_->values[i] - started_->values[i],
                                     stopped_->time_enabled - started_->time_enabled,
                                     stopped_->time_running - started_->time_running));
    }
    return out;
  }

  // CounterSource.
  [[nodiscard]] std::size_t event_count() const noexcept { return open_.size(); }

  [[nodiscard]] bool read_descriptors(std::vector<std::uint64_t>& out) const {
    const std::optional<GroupRead> read = read_group();
    if (!read) {
      return false;
    }
    out = read->values;
    return true;
  }

  [[nodiscard]] bool read_rdpmc(std::vector<std::uint64_t>& out) const noexcept {
    for (std::size_t i = 0; i < open_.size(); ++i) {
      if (open_[i].page == nullptr || !read_mmap_counter(open_[i].page, out[i])) {
        return false;
      }
    }
    return !open_.empty();
  }

  // For the per-message pass: no allocation, one rdpmc per event.
  [[nodiscard]] bool read_fast(std::span<std::uint64_t> out) const noexcept {
    bool usable = !open_.empty();
    for (std::size_t i = 0; i < open_.size(); ++i) {
      usable = open_[i].page != nullptr && read_mmap_counter(open_[i].page, out[i]) && usable;
    }
    return usable;
  }

  [[nodiscard]] std::vector<std::string> event_names() const {
    std::vector<std::string> names;
    for (const Open& event : open_) {
      names.push_back(event.name);
    }
    return names;
  }

 private:
  static constexpr std::size_t kMapBytes = 4096;
  static constexpr std::size_t kMaxEvents = 6;  // Zen 1's core counters.

  struct Open {
    std::string name;
    int fd;
    std::uint64_t id;
    perf_event_mmap_page* page;
  };

  void open_event(const PerfEventSpec& spec) {
    if (open_.size() == kMaxEvents) {
      failed_.push_back(unavailable_counter(spec.name, "more events than the group can read"));
      return;
    }
    perf_event_attr attr{};
    attr.size = sizeof(attr);
    attr.type = spec.kind == EventKind::raw ? PERF_TYPE_RAW : PERF_TYPE_HARDWARE;
    attr.config = spec.config;
    attr.disabled = open_.empty() ? 1U : 0U;
    attr.exclude_kernel = 1;
    attr.exclude_hv = 1;
    attr.read_format = PERF_FORMAT_GROUP | PERF_FORMAT_ID | PERF_FORMAT_TOTAL_TIME_ENABLED |
                       PERF_FORMAT_TOTAL_TIME_RUNNING;
    const int group = open_.empty() ? -1 : open_.front().fd;
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
    const long fd = ::syscall(SYS_perf_event_open, &attr, 0, -1, group, 0UL);
    if (fd < 0) {
      const int error = errno;
      failed_.push_back(
          unavailable_counter(spec.name, std::format("perf_event_open: {}", std::strerror(error))));
      return;
    }
    const int descriptor = static_cast<int>(fd);
    std::uint64_t id = 0;
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
    if (::ioctl(descriptor, PERF_EVENT_IOC_ID, &id) != 0) {
      const int error = errno;
      ::close(descriptor);
      failed_.push_back(unavailable_counter(
          spec.name, std::format("PERF_EVENT_IOC_ID: {}", std::strerror(error))));
      return;
    }
    void* map = ::mmap(nullptr, kMapBytes, PROT_READ, MAP_SHARED, descriptor, 0);
    open_.push_back(
        {.name = spec.name,
         .fd = descriptor,
         .id = id,
         .page = map == MAP_FAILED ? nullptr : static_cast<perf_event_mmap_page*>(map)});
    ids_.push_back(id);
  }

  void ioctl_group(unsigned long request) const noexcept {
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
    ::ioctl(open_.front().fd, request, PERF_IOC_FLAG_GROUP);
  }

  [[nodiscard]] std::optional<GroupRead> read_group() const {
    constexpr std::size_t kMaxWords = 3 + (2 * kMaxEvents);
    std::array<std::uint64_t, kMaxWords> words{};
    const ::ssize_t got = ::read(open_.front().fd, words.data(), sizeof(words));
    if (got <= 0) {
      return std::nullopt;
    }
    return parse_group_read(
        std::span{words}.first(static_cast<std::size_t>(got) / sizeof(words[0])), ids_);
  }

  std::vector<Open> open_;
  std::vector<std::uint64_t> ids_;
  std::vector<CounterStatus> failed_;
  std::optional<GroupRead> started_;
  std::optional<GroupRead> stopped_;
};

#elifdef __APPLE__

class CounterGroup {
 public:
  explicit CounterGroup(std::string_view /*host_label*/) {}
  explicit CounterGroup(const std::vector<PerfEventSpec>& /*events*/) {}

  void start() noexcept {
    thread_before_ = read_thread();
    process_before_ = read_process();
  }

  void stop() noexcept {
    thread_after_ = read_thread();
    process_after_ = read_process();
  }

  [[nodiscard]] std::vector<CounterStatus> statuses() const {
    std::vector<CounterStatus> out;
    const auto delta = [](const std::optional<Pair>& before, const std::optional<Pair>& after,
                          std::uint64_t Pair::* field) -> std::optional<std::uint64_t> {
      if (!before || !after) {
        return std::nullopt;
      }
      return (*after).*field - (*before).*field;
    };
    const auto add = [&out](std::string name, std::optional<std::uint64_t> value,
                            std::string_view failure) {
      if (!value) {
        out.push_back(unavailable_counter(std::move(name), std::string{failure}));
      } else if (*value == 0) {
        out.push_back(unavailable_counter(std::move(name), "the call returned zero"));
      } else {
        out.push_back({.name = std::move(name),
                       .state = CounterState::measured,
                       .value = *value,
                       .running_ratio = 1.0,
                       .reason = {}});
      }
    };
    add("cycles (thread_selfcounts, user+kernel)",
        delta(thread_before_, thread_after_, &Pair::cycles), "thread_selfcounts failed");
    add("instructions (thread_selfcounts, user+kernel)",
        delta(thread_before_, thread_after_, &Pair::instructions), "thread_selfcounts failed");
    add("cycles (proc_pid_rusage, user+kernel)",
        delta(process_before_, process_after_, &Pair::cycles), "proc_pid_rusage failed");
    add("instructions (proc_pid_rusage, user+kernel)",
        delta(process_before_, process_after_, &Pair::instructions), "proc_pid_rusage failed");
    return out;
  }

  // No user-mode counter read on macOS, so a cross-check reports unavailable.
  // NOLINTBEGIN(readability-convert-member-functions-to-static): the same calls as Linux.
  [[nodiscard]] std::size_t event_count() const noexcept { return 0; }
  [[nodiscard]] bool read_descriptors(std::vector<std::uint64_t>& /*out*/) const noexcept {
    return false;
  }
  [[nodiscard]] bool read_rdpmc(std::vector<std::uint64_t>& /*out*/) const noexcept {
    return false;
  }
  [[nodiscard]] bool read_fast(std::span<std::uint64_t> /*out*/) const noexcept { return false; }
  [[nodiscard]] std::vector<std::string> event_names() const { return {}; }
  // NOLINTEND(readability-convert-member-functions-to-static)

 private:
  struct Pair {
    std::uint64_t instructions = 0;
    std::uint64_t cycles = 0;
  };

  [[nodiscard]] static std::optional<Pair> read_thread() noexcept {
    static constexpr int kInstructionsAndCycles = 1;  // THSC_CPI in resource_private.h.
    Pair counts;
    if (thread_selfcounts(kInstructionsAndCycles, &counts, sizeof(counts)) != 0) {
      return std::nullopt;
    }
    return counts;
  }

  [[nodiscard]] static std::optional<Pair> read_process() noexcept {
    rusage_info_v4 info{};
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
    if (proc_pid_rusage(::getpid(), RUSAGE_INFO_V4, reinterpret_cast<rusage_info_t*>(&info)) != 0) {
      return std::nullopt;
    }
    return Pair{.instructions = info.ri_instructions, .cycles = info.ri_cycles};
  }

  std::optional<Pair> thread_before_;
  std::optional<Pair> thread_after_;
  std::optional<Pair> process_before_;
  std::optional<Pair> process_after_;
};

#else

class CounterGroup {
 public:
  explicit CounterGroup(std::string_view /*host_label*/) {}
  explicit CounterGroup(const std::vector<PerfEventSpec>& /*events*/) {}
  void start() const noexcept {}
  void stop() const noexcept {}
  [[nodiscard]] std::vector<CounterStatus> statuses() const { return {}; }  // NOLINT
  // NOLINTBEGIN(readability-convert-member-functions-to-static): the same calls as Linux.
  [[nodiscard]] std::size_t event_count() const noexcept { return 0; }
  [[nodiscard]] bool read_descriptors(std::vector<std::uint64_t>& /*out*/) const noexcept {
    return false;
  }
  [[nodiscard]] bool read_rdpmc(std::vector<std::uint64_t>& /*out*/) const noexcept {
    return false;
  }
  [[nodiscard]] bool read_fast(std::span<std::uint64_t> /*out*/) const noexcept { return false; }
  [[nodiscard]] std::vector<std::string> event_names() const { return {}; }
  // NOLINTEND(readability-convert-member-functions-to-static)
};

#endif

// Whether user-mode rdpmc works here, tried on a short loop against read().
// The Linux branch below drives the group and formats its answer, which tidy cannot see
// from macOS.
// NOLINTNEXTLINE(misc-const-correctness,modernize-use-string-view)
[[nodiscard]] inline std::string probe_rdpmc(CounterGroup& group) {
#if defined(__linux__) && !defined(__EMSCRIPTEN__)
  if (group.event_count() == 0) {
    return "unavailable: no event opened";
  }
  group.start();
  volatile std::uint64_t sink = 0;
  const Agreement agreement = cross_check(group, [&sink] {
    for (std::uint64_t i = 0; i < 2'000'000; ++i) {
      sink = sink + i;
    }
  });
  group.stop();
  if (agreement.state == AgreementState::agreed) {
    return std::format("works: {}; {}", describe_agreement(agreement),
                       describe_events(agreement, group.event_names()));
  }
  return std::format("unavailable: {}", describe_agreement(agreement));
#else
  static_cast<void>(group);
  return "unavailable: no user-mode rdpmc on this platform";
#endif
}

}  // namespace itch::bench
