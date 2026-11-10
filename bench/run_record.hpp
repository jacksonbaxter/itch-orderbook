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

// The spans of one operation's messages (T-LAT-1).
struct OpLatency {
  std::string_view op;
  std::vector<Percentile> percentiles;
  double mean_ns = 0.0;
  std::uint64_t samples = 0;
};

struct LatencyRecord {
  std::vector<Percentile> percentiles;
  double mean_ns = 0.0;  // A mean over many spans, so finer than the quantum.
  std::optional<double> max_ns;
  std::uint64_t samples = 0;
  std::vector<OpLatency> operations;  // add, delete, replace and reduce.
};

// The book's memory (T-BOOK-3), from the untimed pass: what the process was charged
// before the book was built, after it was built, and at the most over the whole day.
struct MemoryRecord {
  std::string source;
  bool complete = false;    // Every read answered.
  bool first_book = false;  // No other book was built in this process before this one.
  std::uint64_t baseline_bytes = 0;
  std::uint64_t built_bytes = 0;
  std::uint64_t peak_bytes = 0;
  std::uint64_t book_bytes = 0;  // peak_bytes less baseline_bytes.
  std::uint64_t samples = 0;
  std::optional<std::uint64_t> ager_held_bytes;  // An aged heap's ballast, inside book_bytes.
};

struct PerMessageCount {
  std::string name;
  double mean = 0.0;
  std::uint64_t p50 = 0;
  std::uint64_t p99 = 0;
  std::uint64_t sum = 0;  // Over apply() alone, so under the pass's own count.
};

struct CounterRecord {
  std::vector<CounterStatus> statuses;
  std::string agreement;
  std::vector<std::string> agreement_names;
  std::vector<EventAgreement> agreement_events;  // The whole pass, by read() and by rdpmc.
  bool per_message_published = false;
  std::vector<PerMessageCount> per_message;
};

struct RunRecord {
  std::vector<HeaderField> header;
  SessionReport session;
  LatencyRecord latency;
  CounterRecord counters;
  MemoryRecord memory;
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
  return std::format(
      R"({{"messages": {}, "seconds": {:.6f}, "messages_per_s": {:.1f}, "top_changes": {}, )"
      R"("pauses": {}, "faults": {}}})",
      pass->messages, seconds, rate, pass->top_changes, pass->pauses,
      pass->faults ? std::format("{}", *pass->faults) : std::string{"null"});
}

[[nodiscard]] inline std::string percentiles_json(const std::vector<Percentile>& percentiles) {
  std::string out = "[";
  for (std::size_t i = 0; i < percentiles.size(); ++i) {
    const Percentile& p = percentiles[i];
    out += std::format(R"({}{{"percentile": {}, "value_ns": {}}})", i == 0 ? "" : ", ",
                       p.percentile, json_number(p.value_ns));
  }
  return out + "]";
}

[[nodiscard]] inline std::string mix_json(const MessageMix& mix) {
  std::string out =
      std::format(R"({{"messages": {}, "book_messages": {})", mix.messages, mix.book_messages);
  for (std::size_t op = 0; op < kOpClasses; ++op) {
    out += std::format(", {}: {}", json_string(kOpNames[op]), mix.by_op[op]);
  }
  return out + "}";
}

[[nodiscard]] inline std::string latency_json(const LatencyRecord& latency) {
  std::string out = std::format(R"({{"percentiles": {})", percentiles_json(latency.percentiles));
  out += std::format(R"(, "mean_ns": {:.6f}, "max_ns": {}, "samples": {}, "operations": [)",
                     latency.mean_ns, json_number(latency.max_ns), latency.samples);
  for (std::size_t i = 0; i < latency.operations.size(); ++i) {
    const OpLatency& op = latency.operations[i];
    out += std::format(R"({}{{"op": {}, "samples": {}, "mean_ns": {:.6f}, "percentiles": {}}})",
                       i == 0 ? "" : ", ", json_string(op.op), op.samples, op.mean_ns,
                       percentiles_json(op.percentiles));
  }
  return out + "]}";
}

[[nodiscard]] inline std::string memory_json(const MemoryRecord& memory) {
  return std::format(
      R"({{"source": {}, "complete": {}, "first_book": {}, "baseline_bytes": {}, )"
      R"("built_bytes": {}, "peak_bytes": {}, "book_bytes": {}, "samples": {}, )"
      R"("ager_held_bytes": {}}})",
      json_string(memory.source), memory.complete ? "true" : "false",
      memory.first_book ? "true" : "false", memory.baseline_bytes, memory.built_bytes,
      memory.peak_bytes, memory.book_bytes, memory.samples,
      memory.ager_held_bytes ? std::format("{}", *memory.ager_held_bytes) : std::string{"null"});
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
  out += std::format(R"(  "warmup": {{"messages": {}, "book": {}}},)", session.warmup_messages,
                     json_string(session.warmup_book));
  out += std::format("\n  \"mix\": {},\n", detail::mix_json(session.mix));
  out += std::format("  \"throughput\": {},\n", detail::pass_json(session.throughput, timer));
  out += std::format("  \"latency_pass\": {},\n", detail::pass_json(session.latency, timer));
  out += std::format("  \"instrumentation_ratio\": {},\n",
                     json_number(session.instrumentation_ratio()));
  out += std::format("  \"latency\": {},", detail::latency_json(record.latency));
  out += std::format("\n  \"memory\": {},", detail::memory_json(record.memory));
  out += "\n  \"counters\": {\"status\": [";
  const std::vector<CounterStatus>& statuses = record.counters.statuses;
  for (std::size_t i = 0; i < statuses.size(); ++i) {
    const CounterStatus& s = statuses[i];
    out +=
        std::format(R"({}{{"name": {}, "state": {}, "value": {}, "ratio": {:.6f}, "reason": {}}})",
                    i == 0 ? "" : ", ", json_string(s.name), json_string(to_string(s.state)),
                    s.value, s.running_ratio, json_string(s.reason));
  }
  out += std::format(R"(], "agreement": {}, "agreement_events": [)",
                     json_string(record.counters.agreement));
  const std::vector<EventAgreement>& paths = record.counters.agreement_events;
  for (std::size_t i = 0; i < paths.size() && i < record.counters.agreement_names.size(); ++i) {
    out += std::format(R"({}{{"name": {}, "read": {}, "rdpmc": {}}})", i == 0 ? "" : ", ",
                       json_string(record.counters.agreement_names[i]), paths[i].by_read,
                       paths[i].by_rdpmc);
  }
  out += std::format(R"(], "per_message_published": {}, "per_message": [)",
                     record.counters.per_message_published ? "true" : "false");
  if (record.counters.per_message_published) {
    const std::vector<PerMessageCount>& counts = record.counters.per_message;
    for (std::size_t i = 0; i < counts.size(); ++i) {
      out += std::format(R"({}{{"name": {}, "mean": {:.6f}, "p50": {}, "p99": {}, "sum": {}}})",
                         i == 0 ? "" : ", ", json_string(counts[i].name), counts[i].mean,
                         counts[i].p50, counts[i].p99, counts[i].sum);
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
