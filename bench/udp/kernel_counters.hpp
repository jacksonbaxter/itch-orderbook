// The kernel's own counts around a T-URING run, every layer of IU-43 that a user can read,
// and the CPU each part of the run took (IU-46).
//
//     /proc/net/snmp      Udp: InDatagrams, InErrors, RcvbufErrors, ...   whole host
//     /proc/net/softnet_stat  column 2, the per-CPU backlog's drops        whole host
//     SO_MEMINFO          SK_MEMINFO_DROPS, the receiver socket's drops    the one socket
//     /proc/stat          each CPU's softirq time, in USER_HZ ticks        whole host
//     /proc/self/task/*/stat  each thread's utime and stime, in ticks      this process
//     getrusage(RUSAGE_THREAD)  the calling thread's CPU, in microseconds  one thread
//
// The socket's drops are what /proc/net/udp prints in its last column (sk_drops), read
// from the socket itself so that no line has to be matched to it. SO_RXQ_OVFL, the same
// count by control message, is the modes' own (S2, U3). The sequence tracker's gaps are
// the receiver's own view, in mold::SequenceCounts.
//
// On loopback the receive softirq runs on the sender's CPU (IU-39), so that CPU's softirq
// time is where the kernel's receive work shows. Whether it is also in the sender thread's
// stime depends on CONFIG_IRQ_TIME_ACCOUNTING, which the smoke test records: without it a
// tick that lands in softirq is charged to both.
//
// The parsers take text, so the tests run them on every platform. The readers are Linux
// only; elsewhere each says it read nothing, and nothing is made up in its place.
#pragma once

#include <array>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include "platform/sysinfo.hpp"

#if defined(__linux__)
#include <linux/sock_diag.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

namespace itch::bench::udp {

struct UdpSnmp {
  std::uint64_t in_datagrams = 0;
  std::uint64_t no_ports = 0;
  std::uint64_t in_errors = 0;
  std::uint64_t out_datagrams = 0;
  std::uint64_t rcvbuf_errors = 0;
  std::uint64_t sndbuf_errors = 0;
};

struct SoftnetRow {
  std::uint64_t processed = 0;
  std::uint64_t dropped = 0;  // Column 2: the backlog was full (IU-43).
  std::uint64_t squeezed = 0;
};

struct CpuTicks {
  int cpu = -1;
  std::uint64_t user = 0;
  std::uint64_t system = 0;
  std::uint64_t softirq = 0;
  std::uint64_t irq = 0;
  std::uint64_t idle = 0;
};

struct TaskTicks {
  int tid = -1;
  std::string comm;
  std::uint64_t utime = 0;
  std::uint64_t stime = 0;
  int processor = -1;  // The CPU it last ran on.
};

namespace detail {

[[nodiscard]] inline std::vector<std::string_view> split_words(std::string_view line) {
  std::vector<std::string_view> words;
  std::size_t at = 0;
  while (at < line.size()) {
    while (at < line.size() && (line[at] == ' ' || line[at] == '\t')) {
      ++at;
    }
    const std::size_t start = at;
    while (at < line.size() && line[at] != ' ' && line[at] != '\t') {
      ++at;
    }
    if (at > start) {
      words.push_back(line.substr(start, at - start));
    }
  }
  return words;
}

[[nodiscard]] inline std::vector<std::string_view> split_lines(std::string_view text) {
  std::vector<std::string_view> lines;
  std::size_t start = 0;
  while (start < text.size()) {
    const std::size_t end = text.find('\n', start);
    const std::size_t stop = end == std::string_view::npos ? text.size() : end;
    lines.push_back(text.substr(start, stop - start));
    start = stop + 1;
  }
  return lines;
}

[[nodiscard]] inline std::optional<std::uint64_t> number(std::string_view word, int base = 10) {
  std::uint64_t value = 0;
  const auto [end, error] = std::from_chars(word.data(), word.data() + word.size(), value, base);
  if (error != std::errc{} || end != word.data() + word.size()) {
    return std::nullopt;
  }
  return value;
}

}  // namespace detail

// The Udp: pair of lines in /proc/net/snmp, the first naming the columns and the second
// giving them. A missing column is a parse failure, never a 0.
[[nodiscard]] inline std::optional<UdpSnmp> parse_udp_snmp(std::string_view text) {
  const std::vector<std::string_view> lines = detail::split_lines(text);
  for (std::size_t i = 0; i + 1 < lines.size(); ++i) {
    const std::vector<std::string_view> names = detail::split_words(lines[i]);
    const std::vector<std::string_view> values = detail::split_words(lines[i + 1]);
    if (names.empty() || names[0] != "Udp:" || values.empty() || values[0] != "Udp:" ||
        names.size() != values.size() || detail::number(names.size() > 1 ? names[1] : "x")) {
      continue;
    }
    UdpSnmp snmp;
    int found = 0;
    for (std::size_t column = 1; column < names.size(); ++column) {
      const std::optional<std::uint64_t> value = detail::number(values[column]);
      if (!value) {
        return std::nullopt;
      }
      const std::string_view name = names[column];
      std::uint64_t* field = name == "InDatagrams"    ? &snmp.in_datagrams
                             : name == "NoPorts"      ? &snmp.no_ports
                             : name == "InErrors"     ? &snmp.in_errors
                             : name == "OutDatagrams" ? &snmp.out_datagrams
                             : name == "RcvbufErrors" ? &snmp.rcvbuf_errors
                             : name == "SndbufErrors" ? &snmp.sndbuf_errors
                                                      : nullptr;
      if (field != nullptr) {
        *field = *value;
        ++found;
      }
    }
    if (found != 6) {
      return std::nullopt;
    }
    return snmp;
  }
  return std::nullopt;
}

// /proc/net/softnet_stat: one line a CPU, each column eight hex digits.
[[nodiscard]] inline std::optional<std::vector<SoftnetRow>> parse_softnet(std::string_view text) {
  std::vector<SoftnetRow> rows;
  for (const std::string_view line : detail::split_lines(text)) {
    const std::vector<std::string_view> words = detail::split_words(line);
    if (words.empty()) {
      continue;
    }
    if (words.size() < 3) {
      return std::nullopt;
    }
    const auto processed = detail::number(words[0], 16);
    const auto dropped = detail::number(words[1], 16);
    const auto squeezed = detail::number(words[2], 16);
    if (!processed || !dropped || !squeezed) {
      return std::nullopt;
    }
    rows.push_back({.processed = *processed, .dropped = *dropped, .squeezed = *squeezed});
  }
  if (rows.empty()) {
    return std::nullopt;
  }
  return rows;
}

// The cpuN lines of /proc/stat: user nice system idle iowait irq softirq ..., in USER_HZ
// ticks. The all-CPU "cpu" line is left out.
[[nodiscard]] inline std::optional<std::vector<CpuTicks>> parse_proc_stat(std::string_view text) {
  std::vector<CpuTicks> cpus;
  for (const std::string_view line : detail::split_lines(text)) {
    const std::vector<std::string_view> words = detail::split_words(line);
    if (words.empty() || !words[0].starts_with("cpu") || words[0] == "cpu") {
      continue;
    }
    const auto cpu = detail::number(words[0].substr(3));
    if (!cpu || words.size() < 8) {
      return std::nullopt;
    }
    std::vector<std::uint64_t> ticks;
    for (std::size_t column = 1; column < 8; ++column) {
      const auto value = detail::number(words[column]);
      if (!value) {
        return std::nullopt;
      }
      ticks.push_back(*value);
    }
    cpus.push_back({.cpu = static_cast<int>(*cpu),
                    .user = ticks[0] + ticks[1],
                    .system = ticks[2],
                    .softirq = ticks[6],
                    .irq = ticks[5],
                    .idle = ticks[3] + ticks[4]});
  }
  if (cpus.empty()) {
    return std::nullopt;
  }
  return cpus;
}

// One /proc/<pid>/task/<tid>/stat line. The command name is in parentheses and may hold
// spaces or parentheses itself, so the fields after it are counted from the last ')'.
[[nodiscard]] inline std::optional<TaskTicks> parse_task_stat(std::string_view line) {
  const std::size_t open = line.find('(');
  const std::size_t close = line.rfind(')');
  if (open == std::string_view::npos || close == std::string_view::npos || close < open) {
    return std::nullopt;
  }
  std::string_view head = line.substr(0, open);
  while (!head.empty() && head.back() == ' ') {
    head.remove_suffix(1);
  }
  const auto tid = detail::number(head);
  const std::vector<std::string_view> rest = detail::split_words(line.substr(close + 1));
  // rest[0] is field 3 (state); utime is field 14, stime 15, processor 39.
  constexpr std::size_t kUtime = 14 - 3;
  constexpr std::size_t kStime = 15 - 3;
  constexpr std::size_t kProcessor = 39 - 3;
  if (!tid || rest.size() <= kProcessor) {
    return std::nullopt;
  }
  const auto utime = detail::number(rest[kUtime]);
  const auto stime = detail::number(rest[kStime]);
  const auto processor = detail::number(rest[kProcessor]);
  if (!utime || !stime || !processor) {
    return std::nullopt;
  }
  return TaskTicks{.tid = static_cast<int>(*tid),
                   .comm = std::string{line.substr(open + 1, close - open - 1)},
                   .utime = *utime,
                   .stime = *stime,
                   .processor = static_cast<int>(*processor)};
}

// The host's counts at one moment. Each part is empty when it could not be read.
struct KernelSnapshot {
  std::optional<UdpSnmp> udp;
  std::optional<std::vector<SoftnetRow>> softnet;
  std::optional<std::vector<CpuTicks>> cpus;
  std::vector<TaskTicks> tasks;  // This process's threads.
};

[[nodiscard]] inline KernelSnapshot read_kernel_snapshot() {
  KernelSnapshot snapshot;
#if defined(__linux__)
  if (const auto text = read_whole_file("/proc/net/snmp")) {
    snapshot.udp = parse_udp_snmp(*text);
  }
  if (const auto text = read_whole_file("/proc/net/softnet_stat")) {
    snapshot.softnet = parse_softnet(*text);
  }
  if (const auto text = read_whole_file("/proc/stat")) {
    snapshot.cpus = parse_proc_stat(*text);
  }
  std::error_code error;
  for (const auto& entry : std::filesystem::directory_iterator{"/proc/self/task", error}) {
    if (const auto text = read_whole_file((entry.path() / "stat").string())) {
      if (auto task = parse_task_stat(*text)) {
        snapshot.tasks.push_back(std::move(*task));
      }
    }
  }
#endif
  return snapshot;
}

// USER_HZ, the unit of every tick above.
[[nodiscard]] inline long ticks_per_second() noexcept {
#if defined(__linux__)
  return ::sysconf(_SC_CLK_TCK);
#else
  return 0;
#endif
}

// The socket's sk_drops since it was made, /proc/net/udp's drops column.
[[nodiscard]] inline std::optional<std::uint64_t> socket_drops(int fd) noexcept {
#if defined(__linux__)
  constexpr auto kDrops = static_cast<std::size_t>(SK_MEMINFO_DROPS);
  std::array<std::uint32_t, static_cast<std::size_t>(SK_MEMINFO_VARS)> info{};
  socklen_t length = sizeof(info);
  if (::getsockopt(fd, SOL_SOCKET, SO_MEMINFO, info.data(), &length) != 0 ||
      length < (kDrops + 1) * sizeof(std::uint32_t)) {
    return std::nullopt;
  }
  return info[kDrops];
#else
  static_cast<void>(fd);
  return std::nullopt;
#endif
}

// The calling thread's CPU so far, user and system, in microseconds.
struct ThreadCpu {
  bool read = false;
  std::uint64_t user_us = 0;
  std::uint64_t system_us = 0;
};

[[nodiscard]] inline ThreadCpu thread_cpu() noexcept {
#if defined(__linux__)
  rusage usage{};
  if (::getrusage(RUSAGE_THREAD, &usage) != 0) {
    return {};
  }
  const auto micros = [](const timeval& value) {
    return (static_cast<std::uint64_t>(value.tv_sec) * 1'000'000U) +
           static_cast<std::uint64_t>(value.tv_usec);
  };
  return {.read = true, .user_us = micros(usage.ru_utime), .system_us = micros(usage.ru_stime)};
#else
  return {};
#endif
}

}  // namespace itch::bench::udp
