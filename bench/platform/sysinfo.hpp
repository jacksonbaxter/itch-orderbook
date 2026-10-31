// Fills the results header. A field that cannot be read is `unavailable: <reason>`, never
// a guess, and nothing here reads a hostname, a username or an address: the host is one
// of three fixed labels, worked out from the OS and the CPU.
//
// Everything reads through SysSource, so that the Linux paths can be tested anywhere.
#pragma once

#include <sys/utsname.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <format>
#include <fstream>
#include <functional>
#include <optional>
#include <regex>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#if defined(__APPLE__)
#include <sys/sysctl.h>
#include <sys/types.h>
#endif

namespace itch::bench {

inline constexpr std::string_view kHostMac = "macOS (M4 Max)";
inline constexpr std::string_view kHostDebian = "debian";
inline constexpr std::string_view kHostUnknown = "unknown";

inline constexpr std::string_view kOsMac = "macOS";
inline constexpr std::string_view kOsLinux = "Linux";

[[nodiscard]] inline std::string unavailable(std::string_view reason) {
  return std::format("unavailable: {}", reason);
}

[[nodiscard]] inline std::string trim(std::string_view text) {
  constexpr std::string_view kSpace = " \t\r\n";
  const std::size_t first = text.find_first_not_of(kSpace);
  if (first == std::string_view::npos) {
    return {};
  }
  return std::string{text.substr(first, text.find_last_not_of(kSpace) - first + 1)};
}

// Takes anything that looks private out of a string from the environment. A compiler's
// flags can carry an SDK or include path, and a vendor string could carry anything.
[[nodiscard]] inline std::string scrub_private(const std::string& text) {
  static const std::regex kHome{R"((/(Users|home)/[A-Za-z0-9._-]+)(/[^\s:;,]*)?)"};
  static const std::regex kAddress{R"(\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b)"};
  return std::regex_replace(std::regex_replace(text, kHome, "<path>"), kAddress, "<address>");
}

struct SysSource {
  std::string_view os;
  std::function<std::optional<std::string>(const std::string&)> file;
  std::function<std::optional<std::string>(const std::string&)> sysctl;
};

[[nodiscard]] inline std::optional<std::string> read_whole_file(const std::string& path) {
  std::ifstream in{path};
  if (!in) {
    return std::nullopt;
  }
  std::stringstream text;
  text << in.rdbuf();
  return text.str();
}

[[nodiscard]] inline std::optional<std::string> read_sysctl(const std::string& name) {
#if defined(__APPLE__)
  std::size_t size = 0;
  if (::sysctlbyname(name.c_str(), nullptr, &size, nullptr, 0) != 0 || size == 0) {
    return std::nullopt;
  }
  std::string value(size, '\0');
  if (::sysctlbyname(name.c_str(), value.data(), &size, nullptr, 0) != 0) {
    return std::nullopt;
  }
  value.resize(size);
  if (const std::size_t end = value.find('\0'); end != std::string::npos) {
    value.resize(end);
  }
  return value;
#else
  static_cast<void>(name);
  return std::nullopt;
#endif
}

[[nodiscard]] inline std::optional<std::int64_t> read_sysctl_number(const std::string& name) {
#if defined(__APPLE__)
  std::int64_t wide = 0;
  std::size_t size = sizeof(wide);
  if (::sysctlbyname(name.c_str(), &wide, &size, nullptr, 0) != 0) {
    return std::nullopt;
  }
  if (size == sizeof(std::int32_t)) {
    std::int32_t narrow = 0;
    size = sizeof(narrow);
    if (::sysctlbyname(name.c_str(), &narrow, &size, nullptr, 0) != 0) {
      return std::nullopt;
    }
    return narrow;
  }
  return wide;
#else
  static_cast<void>(name);
  return std::nullopt;
#endif
}

[[nodiscard]] inline SysSource live_source() {
#if defined(__APPLE__)
  constexpr std::string_view kOs = kOsMac;
#elif defined(__linux__)
  constexpr std::string_view kOs = kOsLinux;
#else
  constexpr std::string_view kOs = "unknown";
#endif
  return {.os = kOs, .file = read_whole_file, .sysctl = [](const std::string& name) {
            // A number comes back from the string read as raw bytes.
            std::optional<std::string> text = read_sysctl(name);
            const auto printable = [](const std::string& value) {
              return !value.empty() && std::ranges::all_of(value, [](char c) { return c >= ' '; });
            };
            if (text && printable(*text)) {
              return text;
            }
            if (const std::optional<std::int64_t> number = read_sysctl_number(name)) {
              return std::optional<std::string>{std::to_string(*number)};
            }
            return std::optional<std::string>{};
          }};
}

[[nodiscard]] inline std::string read_cpu_model(const SysSource& source) {
  if (source.os == kOsMac) {
    const std::optional<std::string> brand = source.sysctl("machdep.cpu.brand_string");
    return brand ? trim(*brand) : unavailable("machdep.cpu.brand_string not readable");
  }
  if (source.os == kOsLinux) {
    const std::optional<std::string> info = source.file("/proc/cpuinfo");
    if (!info) {
      return unavailable("/proc/cpuinfo not readable");
    }
    constexpr std::string_view kKey = "model name";
    const std::size_t at = info->find(kKey);
    const std::size_t colon = at == std::string::npos ? at : info->find(':', at);
    if (colon == std::string::npos) {
      return unavailable("/proc/cpuinfo has no model name");
    }
    const std::size_t end = info->find('\n', colon);
    return trim(std::string_view{*info}.substr(colon + 1, end - colon - 1));
  }
  return unavailable("no CPU query on this platform");
}

// One of three strings, from the OS and the CPU model only.
[[nodiscard]] inline std::string_view host_label(std::string_view os, std::string_view cpu) {
  if (os == kOsMac && cpu == "Apple M4 Max") {
    return kHostMac;
  }
  if (os == kOsLinux && cpu.contains("Ryzen Embedded V1605B")) {
    return kHostDebian;
  }
  return kHostUnknown;
}

// The system name and release, and not the rest of uname: nodename is the hostname.
[[nodiscard]] inline std::string read_kernel() {
  utsname name{};
  if (::uname(&name) != 0) {
    return unavailable("uname failed");
  }
  return std::format("{} {}", static_cast<const char*>(name.sysname),
                     static_cast<const char*>(name.release));
}

namespace detail {

[[nodiscard]] inline std::string cpufreq_path(int core, std::string_view leaf) {
  return std::format("/sys/devices/system/cpu/cpu{}/cpufreq/{}", core < 0 ? 0 : core, leaf);
}

[[nodiscard]] inline std::string read_field(const SysSource& source, const std::string& path) {
  const std::optional<std::string> text = source.file(path);
  if (!text) {
    return unavailable(std::format("{} not readable", path));
  }
  std::string value = trim(*text);
  return value.empty() ? unavailable(std::format("{} is empty", path)) : value;
}

}  // namespace detail

[[nodiscard]] inline std::string read_governor(const SysSource& source, int core) {
  if (source.os != kOsLinux) {
    return unavailable(std::format("{} has no cpufreq governor", source.os));
  }
  return detail::read_field(source, detail::cpufreq_path(core, "scaling_governor"));
}

[[nodiscard]] inline std::string read_frequency(const SysSource& source, int core) {
  if (source.os == kOsLinux) {
    const std::string value =
        detail::read_field(source, detail::cpufreq_path(core, "scaling_cur_freq"));
    return value.starts_with("unavailable") ? value : std::format("{} kHz", value);
  }
  if (source.os == kOsMac && !source.sysctl("hw.cpufrequency")) {
    return unavailable("macOS exposes no core frequency on Apple silicon");
  }
  return unavailable("no frequency query on this platform");
}

[[nodiscard]] inline std::string read_turbo(const SysSource& source) {
  if (source.os != kOsLinux) {
    return unavailable(std::format("{} exposes no boost control", source.os));
  }
  const std::string boost = detail::read_field(source, "/sys/devices/system/cpu/cpufreq/boost");
  if (boost == "1") {
    return "on";
  }
  if (boost == "0") {
    return "off";
  }
  const std::optional<std::string> no_turbo =
      source.file("/sys/devices/system/cpu/intel_pstate/no_turbo");
  if (no_turbo) {
    return trim(*no_turbo) == "0" ? "on" : "off";
  }
  return boost;
}

[[nodiscard]] inline std::string read_smt(const SysSource& source) {
  if (source.os == kOsLinux) {
    const std::string active = detail::read_field(source, "/sys/devices/system/cpu/smt/active");
    if (active == "1") {
      return "on";
    }
    return active == "0" ? "off" : active;
  }
  const std::optional<std::string> physical = source.sysctl("hw.physicalcpu");
  const std::optional<std::string> logical = source.sysctl("hw.logicalcpu");
  if (!physical || !logical) {
    return unavailable("hw.physicalcpu or hw.logicalcpu not readable");
  }
  return std::format("{}: {} physical, {} logical", *physical == *logical ? "none" : "present",
                     *physical, *logical);
}

[[nodiscard]] inline std::string read_isolation(const SysSource& source) {
  if (source.os != kOsLinux) {
    return unavailable(std::format("{} has no isolated cores", source.os));
  }
  const std::optional<std::string> text = source.file("/sys/devices/system/cpu/isolated");
  if (!text) {
    return unavailable("/sys/devices/system/cpu/isolated not readable");
  }
  const std::string cores = trim(*text);
  return cores.empty() ? "none" : cores;
}

// In the order a results file prints them. measure_load_start, measure_load_end and the
// peak memory lines are stamped in afterwards, from what the run really printed.
inline constexpr std::array<std::string_view, 24> kHeaderFields{
    "host",           "cpu",       "kernel",    "compiler", "flags", "commit",    "dataset",
    "dataset_sha256", "timer",     "quantum",   "pair",     "floor", "pinning",   "migrations",
    "core",           "governor",  "frequency", "turbo",    "smt",   "isolation", "counters",
    "rdpmc",          "page_size", "residency"};

using HeaderField = std::pair<std::string, std::string>;

struct HeaderInputs {
  std::string compiler;
  std::string flags;
  std::string commit;
  std::string dataset;
  std::string dataset_sha256;
  std::string timer;
  std::string quantum;
  std::string pair;
  std::string floor;
  std::string pinning;
  std::string migrations;
  std::string core;
  int core_number = -1;
  std::string counters;
  std::string rdpmc;
  std::string page_size;
  std::string residency;
};

[[nodiscard]] inline std::vector<HeaderField> build_header(const SysSource& source,
                                                           const HeaderInputs& in) {
  const std::string cpu = read_cpu_model(source);
  const auto or_unavailable = [](const std::string& value, std::string_view what) {
    return value.empty() ? unavailable(std::format("{} was not supplied", what)) : value;
  };
  std::vector<HeaderField> fields{
      {"host", std::string{host_label(source.os, cpu)}},
      {"cpu", cpu},
      {"kernel", read_kernel()},
      {"compiler", or_unavailable(in.compiler, "the compiler")},
      {"flags", or_unavailable(in.flags, "the flags")},
      {"commit", or_unavailable(in.commit, "the commit")},
      {"dataset", or_unavailable(in.dataset, "the dataset")},
      {"dataset_sha256", or_unavailable(in.dataset_sha256, "the dataset hash")},
      {"timer", or_unavailable(in.timer, "the timer")},
      {"quantum", or_unavailable(in.quantum, "the quantum")},
      {"pair", or_unavailable(in.pair, "the pair cost")},
      {"floor", or_unavailable(in.floor, "the resolution floor")},
      {"pinning", or_unavailable(in.pinning, "the pin outcome")},
      {"migrations", or_unavailable(in.migrations, "the migration count")},
      {"core", or_unavailable(in.core, "the core")},
      {"governor", read_governor(source, in.core_number)},
      {"frequency", read_frequency(source, in.core_number)},
      {"turbo", read_turbo(source)},
      {"smt", read_smt(source)},
      {"isolation", read_isolation(source)},
      {"counters", or_unavailable(in.counters, "the counter status")},
      {"rdpmc", or_unavailable(in.rdpmc, "the rdpmc status")},
      {"page_size", or_unavailable(in.page_size, "the page size")},
      {"residency", or_unavailable(in.residency, "the residency check")},
  };
  for (HeaderField& field : fields) {
    field.second = scrub_private(field.second);
  }
  return fields;
}

}  // namespace itch::bench
