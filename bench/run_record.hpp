// One process's record of a run, as JSON for scripts/bench_driver.py. The driver reads
// these and nothing else, so what is not in here cannot reach a results file.
#pragma once

#include <cstddef>
#include <cstdint>
#include <format>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "platform/counters.hpp"
#include "platform/sysinfo.hpp"
#include "replay.hpp"

namespace itch::bench {

[[nodiscard]] inline std::string json_string(std::string_view text) {
  std::string out = "\"";
  for (const char c : text) {
    if (c == '"' || c == '\\') {
      out += '\\';
      out += c;
    } else if (static_cast<unsigned char>(c) < 0x20) {
      out += std::format("\\u{:04x}", static_cast<unsigned>(c));
    } else {
      out += c;
    }
  }
  return out + '"';
}

[[nodiscard]] inline std::string json_number(std::optional<double> value) {
  return value ? std::format("{:.6f}", *value) : std::string{"null"};
}

struct LatencyRecord {
  std::vector<Percentile> percentiles;
  double mean_ns = 0.0;  // A mean over many spans, so finer than the quantum.
  std::optional<double> max_ns;
  std::uint64_t samples = 0;
};

struct PerMessageCount {
  std::string name;
  double mean = 0.0;
  std::uint64_t p50 = 0;
  std::uint64_t p99 = 0;
};

struct CounterRecord {
  std::vector<CounterStatus> statuses;
  std::string agreement;
  bool per_message_published = false;
  std::vector<PerMessageCount> per_message;
};

struct RunRecord {
  std::vector<HeaderField> header;
  SessionReport session;
  LatencyRecord latency;
  CounterRecord counters;
  double quantum_ns = 0.0;
  double pair_ns = 0.0;
  double floor_ns = 0.0;
  double calibrated_hz = 0.0;
  std::uint64_t peak_rss_bytes = 0;
  std::uint64_t migrations = 0;
  bool pinned = false;
};

namespace detail {

[[nodiscard]] inline std::string pass_json(const std::optional<PassTotals>& pass,
                                           const TimerInfo& timer) {
  if (!pass) {
    return "null";
  }
  const double seconds = timer.ticks_to_ns(static_cast<double>(pass->ticks)) / 1e9;
  const double rate = seconds > 0.0 ? static_cast<double>(pass->messages) / seconds : 0.0;
  return std::format(R"({{"messages": {}, "seconds": {:.6f}, "messages_per_s": {:.1f}}})",
                     pass->messages, seconds, rate);
}

}  // namespace detail

[[nodiscard]] inline std::string to_json(const RunRecord& record, const TimerInfo& timer) {
  std::string out = "{\n  \"header\": {";
  for (std::size_t i = 0; i < record.header.size(); ++i) {
    out += std::format("{}\n    {}: {}", i == 0 ? "" : ",", json_string(record.header[i].first),
                       json_string(record.header[i].second));
  }
  out += "\n  },\n";
  const SessionReport& session = record.session;
  out += std::format("  \"warmup\": {{\"messages\": {}}},\n", session.warmup_messages);
  out += std::format("  \"throughput\": {},\n", detail::pass_json(session.throughput, timer));
  out += std::format("  \"latency_pass\": {},\n", detail::pass_json(session.latency, timer));
  out += std::format("  \"instrumentation_ratio\": {},\n",
                     json_number(session.instrumentation_ratio()));
  out += "  \"latency\": {\"percentiles\": [";
  for (std::size_t i = 0; i < record.latency.percentiles.size(); ++i) {
    const Percentile& p = record.latency.percentiles[i];
    out += std::format(R"({}{{"percentile": {}, "value_ns": {}}})", i == 0 ? "" : ", ",
                       p.percentile, json_number(p.value_ns));
  }
  out +=
      std::format(R"(], "mean_ns": {:.6f}, "max_ns": {}, "samples": {}}},)", record.latency.mean_ns,
                  json_number(record.latency.max_ns), record.latency.samples);
  out += "\n  \"counters\": {\"status\": [";
  const std::vector<CounterStatus>& statuses = record.counters.statuses;
  for (std::size_t i = 0; i < statuses.size(); ++i) {
    const CounterStatus& s = statuses[i];
    out +=
        std::format(R"({}{{"name": {}, "state": {}, "value": {}, "ratio": {:.6f}, "reason": {}}})",
                    i == 0 ? "" : ", ", json_string(s.name), json_string(to_string(s.state)),
                    s.value, s.running_ratio, json_string(s.reason));
  }
  out += std::format(R"(], "agreement": {}, "per_message_published": {}, "per_message": [)",
                     json_string(record.counters.agreement),
                     record.counters.per_message_published ? "true" : "false");
  if (record.counters.per_message_published) {
    const std::vector<PerMessageCount>& counts = record.counters.per_message;
    for (std::size_t i = 0; i < counts.size(); ++i) {
      out += std::format(R"({}{{"name": {}, "mean": {:.6f}, "p50": {}, "p99": {}}})",
                         i == 0 ? "" : ", ", json_string(counts[i].name), counts[i].mean,
                         counts[i].p50, counts[i].p99);
    }
  }
  out += "]},\n";
  out += std::format(
      R"(  "timer": {{"quantum_ns": {:.6f}, "pair_ns": {:.6f}, "floor_ns": {:.6f}, "calibrated_hz": {:.1f}}},)",
      record.quantum_ns, record.pair_ns, record.floor_ns, record.calibrated_hz);
  out += std::format("\n  \"pinned\": {},\n  \"migrations\": {},\n  \"peak_rss_bytes\": {},\n",
                     record.pinned ? "true" : "false", record.migrations, record.peak_rss_bytes);
  out += "  \"refusals\": [";
  for (std::size_t i = 0; i < session.refusals.size(); ++i) {
    out += std::format("{}{}", i == 0 ? "" : ", ", json_string(session.refusals[i]));
  }
  out += "]\n}\n";
  return out;
}

}  // namespace itch::bench
