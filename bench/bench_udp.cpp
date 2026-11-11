// T-URING-1 and T-URING-2: one run of the UDP replay on loopback, the sender against one
// receive mode, with everything the comparison plan asks of a run written to one JSON
// record for scripts/udp_driver.py (io-uring-flamegraph.md, the comparison plan; IU-43,
// IU-46, IU-47).
//
//     bench_udp FILE --mode ID [--rate MSGS_PER_S | --feed-speedup X] [--warmup-ms N]
//               [--first N] [--frames N] [--datagram BYTES] [--max-messages N]
//               [--flush-gap-ns N] [--heartbeat-gap-ns N]
//               [--core N] [--receiver-core N] [--sq-core N] [--receive-buffer BYTES]
//               [--timeout-ms N] [--slot BYTES] [--ring-buffers N]
//               [--max-orders N] [--max-levels N] [--max-pages N] [--max-slots N]
//               [--out FILE] [--commit HASH] [--dataset NAME] [--dataset-sha256 HEX]
//     bench_udp FILE --mode sender [--rate MSGS_PER_S] [--core N] ...
//     bench_udp FILE --peaks [--first N] [--frames N]
//     bench_udp --probe [--sq-core N]
//
// A run. The slice is packed before anything is timed (mold_packets.hpp), the file path's
// book is built from the same frames and kept only as its statuses and canonical hash,
// and the file is then unmapped and its page cache dropped, so that the day and its copy
// are never both resident (NOTES.md, "The UDP replay's portable groundwork", item 7). The
// sender and the mode then run on their own threads, each pinned if a core is given, the
// receiver applying every frame to a book. A run passes when every message arrived once
// and the receiver's book ends with the file path's hash and statuses.
//
// Loss, every layer of IU-43: the sender's failed sends; the sequence tracker's gaps, lost
// messages and duplicates; the mode's truncations, -ENOBUFS completions, CQ overflow and
// SO_RXQ_OVFL (S2, U3); the receiver socket's own drops (SO_MEMINFO, /proc/net/udp's
// column); and /proc/net/snmp's Udp RcvbufErrors and InErrors and softnet_stat's backlog
// drops, read before and after, which are the whole host's. `lossless` is zero on every
// one of them; `delivered` is the sequence alone.
//
// Latency. --rate paces the sender to a message rate and --feed-speedup to each packet's
// ITCH timestamp, divided by the speedup. Each packet's latency runs from its due time on
// the schedule, not from when it was actually sent, so a sender that falls behind is
// charged for it (IU-46), to the receiver's clock read when the wait that returned it
// returned. Both clocks are steady_clock, CLOCK_MONOTONIC, on one host. Packets due in the
// first --warmup-ms are left out of the histogram, and only of the histogram. An unpaced
// run has no schedule, so no latency.
//
// CPU (IU-46). The sender's and the receiver's own threads by getrusage(RUSAGE_THREAD), the
// sender from its first send to its last, the receiver from its first wait to the end of
// session; U5's SQ thread, iou-sqp-<pid>, and every CPU's softirq and irq time from
// /proc/self/task/*/stat and /proc/stat, in USER_HZ ticks, from when both threads are
// ready to the end of session. On loopback the receive softirq runs on the sender's CPU
// (IU-39), so the sender CPU's softirq is the kernel's receive work. This is a syscall
// and copy benchmark on one host, not a network benchmark (IU-47), and the record says so.
//
// --mode sender is the sender's ceiling (the comparison plan's task 2): the sender alone,
// as fast as it can, down a pair whose receiving socket nobody reads, so the kernel drops
// at the socket what does not fit. --peaks prints the slice's busiest second and
// millisecond, which the driver scales to Nasdaq's peaks for the feed-shaped replay.
// --probe sets up every mode this build has on a fresh pair, waits once, and says what
// each did, with the ring features and opcodes the kernel offers.
//
// Exit codes: 0 the run passed, 1 it failed, 2 bad usage, an input that cannot be packed,
// or a mode not built here or whose setup fails.
#include <hdr/hdr_histogram.h>
#include <unistd.h>

#if defined(__linux__)
#include <fcntl.h>
#endif

#include <algorithm>
#include <array>
#include <cerrno>
#include <charconv>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <format>
#include <fstream>
#include <limits>
#include <memory>
#include <optional>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#include "itch/book.hpp"
#include "itch/mold.hpp"
#include "itch/mold_packets.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"
#include "mapped_file.hpp"
#include "platform/sysinfo.hpp"
#include "run_record.hpp"
#include "udp/digest.hpp"
#include "udp/kernel_counters.hpp"
#include "udp/loopback.hpp"
#include "udp/modes.hpp"
#include "udp/sender.hpp"
#include "udp/socket.hpp"

namespace {

namespace udp = itch::bench::udp;
namespace mold = itch::mold;
using itch::bench::json_string;

inline constexpr std::string_view kNotANetwork =
    "loopback: the receive softirq runs on the sender's CPU, so this measures syscalls and "
    "copies on one host, not a network (IU-47)";

struct Options {
  const char* path = nullptr;
  std::string_view mode;
  bool peaks = false;
  bool probe = false;
  mold::PacketPlan plan;
  udp::LoopbackConfig loopback;
  udp::ModeOptions mode_options;
  double feed_speedup = 0.0;  // 0 paces by --rate instead.
  std::uint64_t warmup_ms = 1000;
  itch::BookCapacity capacity;
  std::string out;
  std::string commit;
  std::string dataset;
  std::string dataset_sha256;
};

[[nodiscard]] std::optional<std::uint64_t> parse_number(std::string_view text) {
  std::uint64_t value = 0;
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
  if (error != std::errc{} || end != text.data() + text.size()) {
    return std::nullopt;
  }
  return value;
}

[[nodiscard]] std::optional<double> parse_decimal(std::string_view text) {
  double value = 0.0;
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
  if (error != std::errc{} || end != text.data() + text.size() || !(value > 0.0)) {
    return std::nullopt;
  }
  return value;
}

[[nodiscard]] bool take_number(std::string_view arg, std::uint64_t number, Options& options) {
  constexpr std::uint64_t kMostInt = std::numeric_limits<int>::max();
  constexpr std::uint64_t kMost32 = std::numeric_limits<std::uint32_t>::max();
  if (arg == "--first") {
    options.plan.first_frame = number;
  } else if (arg == "--frames") {
    options.plan.frame_limit = number;
  } else if (arg == "--datagram") {
    options.plan.max_datagram = static_cast<std::size_t>(number);
  } else if (arg == "--max-messages" && number <= mold::kMostBlocks) {
    options.plan.max_messages = static_cast<std::uint16_t>(number);
  } else if (arg == "--flush-gap-ns") {
    options.plan.flush_gap_ns = number;
  } else if (arg == "--heartbeat-gap-ns") {
    options.plan.heartbeat_gap_ns = number;
  } else if (arg == "--rate") {
    options.loopback.send.messages_per_second = number;
  } else if (arg == "--warmup-ms") {
    options.warmup_ms = number;
  } else if (arg == "--core" && number <= kMostInt) {
    options.loopback.sender_core = static_cast<int>(number);
  } else if (arg == "--receiver-core" && number <= kMostInt) {
    options.loopback.receiver_core = static_cast<int>(number);
  } else if (arg == "--sq-core" && number <= kMostInt) {
    options.mode_options.sq_core = static_cast<int>(number);
  } else if (arg == "--receive-buffer" && number <= kMostInt) {
    options.loopback.receive_buffer = static_cast<int>(number);
  } else if (arg == "--timeout-ms" && number != 0 && number <= kMostInt) {
    options.loopback.idle_timeout = std::chrono::milliseconds{number};
  } else if (arg == "--slot" && number != 0 && number <= udp::kDatagramBuffer) {
    options.mode_options.slot_bytes = static_cast<std::size_t>(number);
  } else if (arg == "--ring-buffers" && number != 0 && number <= udp::kMostRingBuffersAsked) {
    options.mode_options.ring_buffers = static_cast<std::uint32_t>(number);
  } else if (arg == "--max-orders" && number <= kMost32) {
    options.capacity.orders = static_cast<std::uint32_t>(number);
  } else if (arg == "--max-levels" && number <= kMost32) {
    options.capacity.levels = static_cast<std::uint32_t>(number);
  } else if (arg == "--max-pages" && number <= kMost32) {
    options.capacity.index_pages = static_cast<std::uint32_t>(number);
  } else if (arg == "--max-slots" && number <= kMost32) {
    options.capacity.level_slots = static_cast<std::uint32_t>(number);
  } else {
    return false;
  }
  return true;
}

[[nodiscard]] bool take_text(std::string_view arg, std::string_view value, Options& options) {
  if (arg == "--mode") {
    options.mode = value;
  } else if (arg == "--out") {
    options.out = value;
  } else if (arg == "--commit") {
    options.commit = value;
  } else if (arg == "--dataset") {
    options.dataset = value;
  } else if (arg == "--dataset-sha256") {
    options.dataset_sha256 = value;
  } else if (arg == "--feed-speedup") {
    const std::optional<double> speedup = parse_decimal(value);
    if (!speedup) {
      return false;
    }
    options.feed_speedup = *speedup;
  } else {
    return false;
  }
  return true;
}

[[nodiscard]] std::optional<Options> parse_options(std::span<const char* const> args) {
  Options options;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string_view arg = args[i];
    if (!arg.starts_with("--")) {
      if (options.path != nullptr) {
        return std::nullopt;
      }
      options.path = args[i];
      continue;
    }
    if (arg == "--peaks" || arg == "--probe") {
      (arg == "--peaks" ? options.peaks : options.probe) = true;
      continue;
    }
    if (i + 1 == args.size()) {
      return std::nullopt;
    }
    const std::string_view value = args[++i];
    if (take_text(arg, value, options)) {
      continue;
    }
    const std::optional<std::uint64_t> number = parse_number(value);
    if (!number || !take_number(arg, *number, options)) {
      return std::nullopt;
    }
  }
  const bool paced_twice =
      options.feed_speedup > 0.0 && options.loopback.send.messages_per_second != 0;
  const bool run = !options.peaks && !options.probe;
  if (paced_twice || (options.peaks && options.probe) ||
      (!options.probe && options.path == nullptr) ||
      (run && options.mode != "sender" && !udp::is_mode(options.mode))) {
    return std::nullopt;
  }
  options.mode_options.idle_timeout = options.loopback.idle_timeout;
  return options;
}

// --- The pieces of a run -------------------------------------------------------------

// The receiver's sink: every frame to the book, its answer counted. No per-frame hash,
// so that the receiver does a replay's work and no more; the two books are compared by
// their canonical hash once the run is over.
struct BookSink {
  itch::FastBook<>* book;
  std::array<std::uint64_t, udp::kApplyStatuses> statuses{};
  std::uint64_t frames = 0;

  void operator()(const itch::Frame& frame) noexcept {
    ++statuses[static_cast<std::size_t>(book->apply(frame.payload))];
    ++frames;
  }
};

struct BookResult {
  std::uint64_t frames = 0;
  std::array<std::uint64_t, udp::kApplyStatuses> statuses{};
  std::uint64_t end_hash = 0;

  friend bool operator==(const BookResult&, const BookResult&) = default;
};

[[nodiscard]] BookResult finish(const BookSink& sink) {
  return {.frames = sink.frames, .statuses = sink.statuses, .end_hash = udp::book_hash(*sink.book)};
}

// What the receiver's thread records as datagrams arrive, matched to the packet array by
// sequence and count: the clock read when each packet's wait returned, and the CPU and
// kernel counts at the end of session.
struct Stamps {
  std::vector<std::uint64_t> arrival;         // One a packet, 0 until it arrives.
  std::vector<std::uint64_t> first_sequence;  // One a packet.
  std::vector<std::uint16_t> count;           // One a packet, as its header says.
  std::size_t cursor = 0;
  std::uint64_t unmatched = 0;
  udp::ThreadCpu receiver_start;
  udp::ThreadCpu receiver_end;
  bool ended = false;
  std::uint64_t end_ns = 0;
  udp::KernelSnapshot at_end;
};

inline constexpr std::size_t kMatchAhead = 64;

[[nodiscard]] Stamps make_stamps(const mold::PacketArray& packets) {
  Stamps stamps;
  stamps.arrival.assign(packets.size(), 0);
  stamps.first_sequence.reserve(packets.size());
  stamps.count.reserve(packets.size());
  std::uint64_t sequence = packets.plan().first_sequence;
  for (std::size_t i = 0; i < packets.size(); ++i) {
    const mold::PacketRef& ref = packets.ref(i);
    stamps.first_sequence.push_back(sequence);
    stamps.count.push_back(i + 1 == packets.size() ? mold::kEndOfSessionCount : ref.messages);
    sequence += ref.messages;
  }
  return stamps;
}

// A mode with a clock read after each wait that returns datagrams, each stamped on its
// packet. Everything else is the inner mode's. At the end of session it reads the
// receiver thread's CPU and the kernel's counts once, which is after the loop's work.
template <class Inner>
class StampingMode {
 public:
  static constexpr std::string_view kId = Inner::kId;

  template <class Make>
  StampingMode(Make& make, int fd, Stamps& stamps) : inner_{make(fd)}, stamps_{&stamps} {}

  [[nodiscard]] udp::WaitResult wait(std::span<udp::Datagram> batch) {
    if (!started_) {
      started_ = true;
      stamps_->receiver_start = udp::thread_cpu();
    }
    const udp::WaitResult result = inner_.wait(batch);
    if (result.status == udp::WaitStatus::datagrams && result.count > 0) {
      const std::uint64_t now = udp::SteadyClock::now_ns();
      for (const udp::Datagram& datagram : batch.first(result.count)) {
        stamp(datagram.bytes, now);
      }
    }
    return result;
  }

  void release(std::span<const udp::Datagram> done) { inner_.release(done); }

  [[nodiscard]] const udp::ModeCounters& counters() const noexcept { return inner_.counters(); }

 private:
  void stamp(std::span<const std::byte> bytes, std::uint64_t now) {
    Stamps& s = *stamps_;
    if (bytes.size() < mold::kHeaderBytes) {
      ++s.unmatched;
      return;
    }
    const mold::Header header = mold::read_header(bytes.data());
    const std::size_t stop = std::min(s.arrival.size(), s.cursor + kMatchAhead);
    std::size_t at = s.cursor;
    while (at < stop && (s.first_sequence[at] != header.sequence || s.count[at] != header.count)) {
      ++at;
    }
    if (at == stop) {
      ++s.unmatched;
    } else {
      s.arrival[at] = s.arrival[at] == 0 ? now : s.arrival[at];
      s.cursor = at + 1;
    }
    if (header.count == mold::kEndOfSessionCount && !s.ended) {
      s.ended = true;
      s.end_ns = now;
      s.receiver_end = udp::thread_cpu();
      s.at_end = udp::read_kernel_snapshot();
    }
  }

  Inner inner_;
  Stamps* stamps_;
  bool started_ = false;
};

// The sender's socket, with its thread's CPU read at the first send and after the last.
struct SenderCpu {
  udp::ThreadCpu start;
  udp::ThreadCpu end;
};

class CpuSocket {
 public:
  CpuSocket(udp::ConnectedSocket socket, SenderCpu& cpu, std::size_t total) noexcept
      : socket_{socket}, cpu_{&cpu}, total_{total} {}

  [[nodiscard]] udp::SendResult send(std::span<const std::byte> datagram) noexcept {
    if (sends_ == 0) {
      cpu_->start = udp::thread_cpu();
    }
    const udp::SendResult result = socket_.send(datagram);
    if (++sends_ == total_) {
      cpu_->end = udp::thread_cpu();
    }
    return result;
  }

 private:
  udp::ConnectedSocket socket_;
  SenderCpu* cpu_;
  std::size_t total_;
  std::size_t sends_ = 0;
};

class Histogram {
 public:
  Histogram() { hdr_init(1, 60'000'000'000, 3, &histogram_); }
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
  [[nodiscard]] std::int64_t at(double percentile) const noexcept {
    return hdr_value_at_percentile(histogram_, percentile);
  }
  [[nodiscard]] std::int64_t lowest() const noexcept { return hdr_min(histogram_); }
  [[nodiscard]] std::int64_t highest() const noexcept { return hdr_max(histogram_); }
  [[nodiscard]] double mean() const noexcept { return hdr_mean(histogram_); }
  [[nodiscard]] std::int64_t count() const noexcept { return histogram_->total_count; }

 private:
  hdr_histogram* histogram_ = nullptr;
};

// --- The record ------------------------------------------------------------------------

// One JSON object built field by field, in the order the fields are added.
class Json {
 public:
  void raw(std::string_view key, std::string_view value) {
    text_ += std::format("{}{}: {}", text_.empty() ? "" : ", ", json_string(key), value);
  }
  void text(std::string_view key, std::string_view value) { raw(key, json_string(value)); }
  void number(std::string_view key, std::uint64_t value) { raw(key, std::format("{}", value)); }
  void signed_number(std::string_view key, std::int64_t value) {
    raw(key, std::format("{}", value));
  }
  void decimal(std::string_view key, double value) { raw(key, std::format("{:.3f}", value)); }
  void boolean(std::string_view key, bool value) { raw(key, value ? "true" : "false"); }
  void null(std::string_view key) { raw(key, "null"); }
  void object(std::string_view key, const Json& value) { raw(key, value.str()); }
  [[nodiscard]] std::string str() const { return "{" + text_ + "}"; }

 private:
  std::string text_;
};

[[nodiscard]] Json header_of(const Options& options, int core) {
  const itch::bench::SysSource source = itch::bench::live_source();
  const std::vector<itch::bench::HeaderField> fields = itch::bench::build_header(
      source,
      {.compiler = __VERSION__,
       .flags = ITCH_BENCH_FLAGS,
       .commit = options.commit,
       .dataset = options.dataset,
       .dataset_sha256 = options.dataset_sha256,
       .timer = "steady_clock (CLOCK_MONOTONIC on Linux), the sender's and receiver's alike",
       .quantum = "not measured by this tool: latency is microseconds, far above it",
       .pair = "not measured by this tool",
       .floor = "not measured by this tool",
       .pinning = "per thread, in pins",
       .migrations = "not counted",
       .core = core >= 0 ? std::format("{}", core) : std::string{},
       .core_number = core,
       .counters = "none: kernel counts only",
       .rdpmc = "not used",
       .page_size = "not recorded",
       .residency = "the slice is packed into memory before the run"});
  Json header;
  for (const auto& [key, value] : fields) {
    header.text(key, value);
  }
  return header;
}

[[nodiscard]] Json pin_json(const itch::bench::PinOutcome& pin) {
  Json out;
  out.signed_number("requested_core", pin.requested_core);
  out.boolean("pinned", pin.pinned);
  out.text("attempt", pin.attempt);
  return out;
}

[[nodiscard]] Json plan_json(const Options& options, const mold::PacketArray& packets) {
  Json plan;
  plan.number("first_frame", options.plan.first_frame);
  plan.number("messages", packets.messages());
  plan.number("packets", packets.size());
  plan.number("heartbeats", packets.heartbeats());
  plan.number("bytes", packets.bytes().size());
  plan.number("max_datagram", options.plan.max_datagram);
  plan.number("max_messages", options.plan.max_messages);
  plan.number("flush_gap_ns", options.plan.flush_gap_ns);
  plan.number("heartbeat_gap_ns", options.plan.heartbeat_gap_ns);
  plan.text("destination", "127.0.0.1, unicast, both sockets connect()ed");
  return plan;
}

[[nodiscard]] Json send_json(const udp::SendReport& sent) {
  Json out;
  out.boolean("paced", sent.paced);
  out.number("target_rate", sent.target_rate);
  out.number("packets", sent.packets);
  out.number("sent_packets", sent.sent_packets);
  out.number("sent_messages", sent.sent_messages);
  out.number("sent_bytes", sent.sent_bytes);
  out.number("failed_packets", sent.failed_packets);
  out.number("failed_messages", sent.failed_messages);
  out.number("enobufs", sent.no_buffers);
  out.number("econnrefused", sent.refused);
  out.number("other_errors", sent.other_errors);
  out.signed_number("first_error", sent.first_error);
  out.number("elapsed_ns", sent.elapsed_ns());
  out.decimal("achieved_messages_per_s", sent.achieved_rate());
  out.decimal("achieved_packets_per_s", sent.elapsed_ns() == 0
                                            ? 0.0
                                            : static_cast<double>(sent.sent_packets) * 1e9 /
                                                  static_cast<double>(sent.elapsed_ns()));
  out.number("late_max_ns", sent.late_max_ns);
  out.number("late_total_ns", sent.late_total_ns);
  std::string buckets = "[";
  for (std::size_t i = 0; i < sent.lateness.size(); ++i) {
    buckets += std::format("{}{}", i == 0 ? "" : ", ", sent.lateness[i]);
  }
  out.raw("lateness_log2_buckets", buckets + "]");
  return out;
}

[[nodiscard]] Json mode_json(const udp::ModeCounters& mode) {
  Json out;
  out.number("waits", mode.waits);
  out.number("calls", mode.calls);
  out.number("datagrams", mode.datagrams);
  out.number("bytes", mode.bytes);
  out.number("interrupted", mode.interrupted);
  out.number("timeouts", mode.timeouts);
  out.number("failures", mode.failures);
  out.signed_number("last_error", mode.last_error);
  out.number("truncated", mode.truncated);
  out.number("rearms", mode.rearms);
  out.number("no_buffers", mode.no_buffers);
  out.number("cq_overflow", mode.cq_overflow);
  if (mode.rxq_overflow_set) {
    out.number("rxq_overflow", mode.rxq_overflow);
  } else {
    out.null("rxq_overflow");
  }
  return out;
}

struct HostDeltas {
  std::optional<std::uint64_t> rcvbuf_errors;
  std::optional<std::uint64_t> in_errors;
  std::optional<std::uint64_t> softnet_dropped;
};

[[nodiscard]] HostDeltas host_deltas(const udp::KernelSnapshot& before,
                                     const udp::KernelSnapshot& after) {
  HostDeltas deltas;
  if (before.udp && after.udp) {
    deltas.rcvbuf_errors = after.udp->rcvbuf_errors - before.udp->rcvbuf_errors;
    deltas.in_errors = after.udp->in_errors - before.udp->in_errors;
  }
  if (before.softnet && after.softnet && before.softnet->size() == after.softnet->size()) {
    std::uint64_t dropped = 0;
    for (std::size_t i = 0; i < after.softnet->size(); ++i) {
      dropped += (*after.softnet)[i].dropped - (*before.softnet)[i].dropped;
    }
    deltas.softnet_dropped = dropped;
  }
  return deltas;
}

void optional_number(Json& json, std::string_view key, std::optional<std::uint64_t> value) {
  if (value) {
    json.number(key, *value);
  } else {
    json.null(key);
  }
}

// Each CPU's softirq and irq ticks, and each thread's, between two snapshots.
[[nodiscard]] Json cpu_ticks_json(const udp::KernelSnapshot& before,
                                  const udp::KernelSnapshot& after) {
  Json out;
  out.signed_number("ticks_per_second", udp::ticks_per_second());
  if (before.cpus && after.cpus && before.cpus->size() == after.cpus->size()) {
    Json softirq;
    Json irq;
    Json busy;
    for (std::size_t i = 0; i < after.cpus->size(); ++i) {
      const udp::CpuTicks& a = (*after.cpus)[i];
      const udp::CpuTicks& b = (*before.cpus)[i];
      const std::string key = std::format("cpu{}", a.cpu);
      softirq.number(key, a.softirq - b.softirq);
      irq.number(key, a.irq - b.irq);
      busy.number(
          key, (a.user + a.system + a.irq + a.softirq) - (b.user + b.system + b.irq + b.softirq));
    }
    out.object("softirq", softirq);
    out.object("irq", irq);
    out.object("busy", busy);
  } else {
    out.null("softirq");
    out.null("irq");
    out.null("busy");
  }
  Json threads;
  for (const udp::TaskTicks& task : after.tasks) {
    Json one;
    one.text("comm", task.comm);
    one.signed_number("processor", task.processor);
    const auto earlier = std::ranges::find(before.tasks, task.tid, &udp::TaskTicks::tid);
    one.number("utime", task.utime - (earlier != before.tasks.end() ? earlier->utime : 0));
    one.number("stime", task.stime - (earlier != before.tasks.end() ? earlier->stime : 0));
    threads.object(std::format("{}", task.tid), one);
  }
  out.object("threads", threads);
  return out;
}

[[nodiscard]] Json thread_cpu_json(const udp::ThreadCpu& start, const udp::ThreadCpu& end) {
  Json out;
  if (!start.read || !end.read) {
    out.null("user_us");
    out.null("system_us");
    return out;
  }
  out.number("user_us", end.user_us - start.user_us);
  out.number("system_us", end.system_us - start.system_us);
  return out;
}

[[nodiscard]] Json latency_json(const Stamps& stamps, std::span<const std::uint64_t> due,
                                std::uint64_t start_ns, std::uint64_t warmup_ns) {
  Histogram histogram;
  std::uint64_t excluded = 0;
  std::uint64_t missing = 0;
  for (std::size_t i = 0; i < stamps.arrival.size(); ++i) {
    if (due[i] < warmup_ns) {
      ++excluded;
      continue;
    }
    if (stamps.arrival[i] == 0) {
      ++missing;
      continue;
    }
    const std::uint64_t scheduled = start_ns + due[i];
    histogram.record(stamps.arrival[i] > scheduled ? stamps.arrival[i] - scheduled : 0);
  }
  Json out;
  out.text("from", "each packet's due time on the schedule, not its actual send (IU-46)");
  out.text("to", "the receiver's clock read when the wait that returned the packet returned");
  out.number("warmup_ns", warmup_ns);
  out.number("warmup_packets", excluded);
  out.number("missing_packets", missing);
  out.number("unmatched_datagrams", stamps.unmatched);
  out.signed_number("samples", histogram.count());
  if (histogram.count() == 0) {
    return out;
  }
  out.signed_number("min_ns", histogram.lowest());
  out.signed_number("p50_ns", histogram.at(50.0));
  out.signed_number("p90_ns", histogram.at(90.0));
  out.signed_number("p99_ns", histogram.at(99.0));
  out.signed_number("p99_9_ns", histogram.at(99.9));
  out.signed_number("p99_99_ns", histogram.at(99.99));
  out.signed_number("max_ns", histogram.highest());
  out.decimal("mean_ns", histogram.mean());
  return out;
}

bool write_record(const Options& options, const Json& record) {
  const std::string text = record.str() + "\n";
  if (options.out.empty()) {
    std::print("{}", text);
    return true;
  }
  std::ofstream out{options.out};
  out << text;
  return static_cast<bool>(out);
}

// Drops the file's pages from the page cache once it is packed (Linux; elsewhere nothing).
[[nodiscard]] std::string drop_page_cache(const char* path) {
#if defined(__linux__)
  const int fd = ::open(path, O_RDONLY | O_CLOEXEC);
  if (fd < 0) {
    return std::format("open failed, errno {}", errno);
  }
  const int result = ::posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED);
  ::close(fd);
  return result == 0 ? std::string{"posix_fadvise(POSIX_FADV_DONTNEED) after packing"}
                     : std::format("posix_fadvise failed, {}", result);
#else
  static_cast<void>(path);
  return "not dropped: no posix_fadvise here";
#endif
}

// --- Modes ---------------------------------------------------------------------------

[[nodiscard]] std::optional<mold::PacketArray> pack(const Options& options,
                                                    std::span<const std::byte> bytes) {
  auto packets = mold::build_packets(bytes, options.plan);
  if (!packets) {
    std::println(stderr, "bench_udp: cannot pack {}: {} at byte {}", options.path,
                 mold::to_string(packets.error().error), packets.error().offset);
    return std::nullopt;
  }
  return std::move(*packets);
}

int run_peaks(const Options& options) {
  const auto file = itch::tools::MappedFile::open(options.path);
  if (!file) {
    std::println(stderr, "bench_udp: cannot open {}", options.path);
    return 2;
  }
  // Message times in the slice, then the most that fall in any 1 s and any 1 ms.
  std::vector<std::uint64_t> times;
  itch::FrameReader reader{file->bytes()};
  std::uint64_t index = 0;
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    if (index >= options.plan.first_frame &&
        index - options.plan.first_frame < options.plan.frame_limit && frame.payload.size() >= 11) {
      times.push_back(itch::read_be48(frame.payload.data() + 5));
    }
    ++index;
  }
  std::println("bench_udp_peaks_messages {}", times.size());
  for (const auto& [label, width] : {std::pair{"1s", std::uint64_t{1'000'000'000}},
                                     std::pair{"1ms", std::uint64_t{1'000'000}}}) {
    std::size_t best = 0;
    std::size_t best_start = 0;
    std::size_t start = 0;
    for (std::size_t end = 0; end < times.size(); ++end) {
      while (times[end] >= times[start] + width) {
        ++start;
      }
      if (end - start + 1 > best) {
        best = end - start + 1;
        best_start = start;
      }
    }
    std::println("bench_udp_peak_{} {} messages from frame {} at {} ns", label, best,
                 options.plan.first_frame + best_start, times.empty() ? 0 : times[best_start]);
  }
  return 0;
}

int run_sender(const Options& options) {
  std::optional<mold::PacketArray> packets;
  {
    const auto file = itch::tools::MappedFile::open(options.path);
    if (!file) {
      std::println(stderr, "bench_udp: cannot open {}", options.path);
      return 2;
    }
    packets = pack(options, file->bytes());
    if (!packets) {
      return 2;
    }
  }
  const std::string dropped = drop_page_cache(options.path);
  auto pair = udp::open_loopback_pair();
  if (!pair) {
    std::println(stderr, "bench_udp: {} failed: errno {}", pair.error().call, pair.error().error);
    return 2;
  }
  const auto buffer =
      udp::set_receive_buffer(pair->receiver.get(), options.loopback.receive_buffer);
  if (!buffer) {
    std::println(stderr, "bench_udp: {} failed: errno {}", buffer.error().call,
                 buffer.error().error);
    return 2;
  }
  udp::SenderRun run;
  SenderCpu cpu;
  CpuSocket socket{udp::ConnectedSocket{pair->sender.get()}, cpu, packets->size()};
  const udp::KernelSnapshot before = udp::read_kernel_snapshot();
  std::thread sender{[&] {
    run.pin = udp::pin_thread(options.loopback.sender_core);
    udp::SteadyClock clock;
    run.report = udp::send_packets(*packets, options.loopback.send, clock, socket);
  }};
  sender.join();
  const udp::KernelSnapshot after = udp::read_kernel_snapshot();
  const std::optional<std::uint64_t> socket_dropped = udp::socket_drops(pair->receiver.get());

  Json record;
  record.text("tool", "bench_udp");
  record.text("kind", "sender");
  record.text("comparability", kNotANetwork);
  record.text("ceiling_method",
              "the sender alone, unpaced unless --rate, the receiving socket "
              "open and never read, so the kernel drops at it what does not fit");
  record.object("header", header_of(options, options.loopback.sender_core));
  record.object("plan", plan_json(options, *packets));
  record.text("page_cache", dropped);
  Json pins;
  pins.object("sender", pin_json(run.pin));
  record.object("pins", pins);
  Json buffer_json;
  buffer_json.number("requested", static_cast<std::uint64_t>(buffer->requested));
  buffer_json.number("read_back", static_cast<std::uint64_t>(buffer->read_back));
  record.object("receive_buffer", buffer_json);
  if (!run.report) {
    std::println(stderr, "bench_udp: the schedule cannot number this many messages");
    return 2;
  }
  record.object("send", send_json(*run.report));
  const HostDeltas deltas = host_deltas(before, after);
  Json drops;
  optional_number(drops, "rcvbuf_errors", deltas.rcvbuf_errors);
  optional_number(drops, "in_errors", deltas.in_errors);
  optional_number(drops, "softnet_dropped", deltas.softnet_dropped);
  optional_number(drops, "socket_drops", socket_dropped);
  record.object("drops", drops);
  Json cpu_json;
  cpu_json.object("sender", thread_cpu_json(cpu.start, cpu.end));
  cpu_json.object("kernel", cpu_ticks_json(before, after));
  record.object("cpu", cpu_json);
  const bool whole = run.report->sent_packets == packets->size();
  record.text("verdict", whole ? "PASS" : "FAIL");
  std::println("bench_udp_sender_messages_per_s {:.0f}", run.report->achieved_rate());
  std::println("bench_udp_verdict {}", whole ? "PASS" : "FAIL");
  if (!write_record(options, record)) {
    std::println(stderr, "bench_udp: cannot write {}", options.out);
    return 2;
  }
  return whole ? 0 : 1;
}

int run_receive(const Options& options) {
  if (!udp::mode_built(options.mode)) {
    std::println(stderr, "bench_udp: mode {} is not built here: {}", options.mode,
                 udp::why_not_built(options.mode));
    return 2;
  }
  if (udp::uses_slots(options.mode) &&
      options.plan.max_datagram + udp::kSlotOverhead > options.mode_options.slot_bytes) {
    std::println(stderr, "bench_udp: a {}-byte datagram does not fit a {}-byte slot",
                 options.plan.max_datagram, options.mode_options.slot_bytes);
    return 2;
  }
  // The file path first, kept only as its result, then the packets, then the file goes.
  std::optional<mold::PacketArray> packets;
  BookResult by_file;
  {
    const auto file = itch::tools::MappedFile::open(options.path);
    if (!file) {
      std::println(stderr, "bench_udp: cannot open {}", options.path);
      return 2;
    }
    {
      const auto book = std::make_unique<itch::FastBook<>>(options.capacity);
      BookSink sink{.book = book.get()};
      udp::replay_frames(file->bytes(), options.plan.first_frame, options.plan.frame_limit, sink);
      by_file = finish(sink);
    }
    packets = pack(options, file->bytes());
    if (!packets) {
      return 2;
    }
  }
  const std::string dropped = drop_page_cache(options.path);

  const bool feed = options.feed_speedup > 0.0;
  const std::vector<std::uint64_t> due =
      feed ? udp::feed_schedule(*packets, options.feed_speedup)
           : udp::rate_schedule(*packets, options.loopback.send.messages_per_second);
  udp::LoopbackConfig config = options.loopback;
  if (feed) {
    config.send.due_ns = due;
  }
  Stamps stamps = make_stamps(*packets);
  SenderCpu sender_cpu;
  const auto book = std::make_unique<itch::FastBook<>>(options.capacity);
  BookSink sink{.book = book.get()};
  udp::Fd held;  // The receiver's socket, kept open past the run to read its drops.
  udp::KernelSnapshot before;
  std::uint64_t ready_ns = 0;
  const auto on_ready = [&] {
    before = udp::read_kernel_snapshot();
    ready_ns = udp::SteadyClock::now_ns();
  };
  const auto wrap = [&](udp::ConnectedSocket socket) {
    return CpuSocket{socket, sender_cpu, packets->size()};
  };
  const auto ran = udp::with_mode(options.mode, options.mode_options, [&](auto make) {
    using Mode = decltype(make(0));
    return udp::run_loopback(
        *packets, config,
        [&](int fd) {
          held = udp::Fd{::dup(fd)};
          return StampingMode<Mode>{make, fd, stamps};
        },
        sink, on_ready, wrap);
  });
  if (!ran) {
    return 2;
  }
  const auto& loop = *ran;
  if (!loop) {
    std::println(stderr, "bench_udp: {} failed: errno {}", loop.error().call, loop.error().error);
    return 2;
  }
  const udp::KernelSnapshot after = udp::read_kernel_snapshot();
  const std::optional<std::uint64_t> socket_dropped =
      held.get() >= 0 ? udp::socket_drops(held.get()) : std::nullopt;
  held.reset();
  const BookResult by_udp = finish(sink);

  if (!loop->sender.report) {
    std::println(
        stderr, "bench_udp: the schedule cannot be sent: {}",
        loop->sender.report.error() == udp::SendError::schedule_mismatch ? "mismatch" : "overflow");
    return 2;
  }
  const udp::SendReport& sent = *loop->sender.report;
  const udp::ModeCounters& mode = loop->mode;
  const mold::SequenceCounts& sequence = loop->sequence;
  const HostDeltas deltas = host_deltas(before, after);

  const bool delivered = loop->received.end == udp::ReceiveEnd::end_of_session &&
                         sent.failed_packets == 0 && sent.sent_packets == packets->size() &&
                         loop->feed.malformed() == 0 && sequence.gaps == 0 && sequence.lost == 0 &&
                         sequence.duplicate_messages == 0 && sequence.foreign_session == 0 &&
                         sequence.out_of_range == 0 && sequence.admitted == packets->messages() &&
                         loop->next_expected == packets->next_sequence();
  const bool book_match = by_udp == by_file && by_file.frames == packets->messages();
  const bool counters_zero = mode.truncated == 0 && mode.no_buffers == 0 && mode.cq_overflow == 0 &&
                             mode.rxq_overflow == 0 && socket_dropped.value_or(1) == 0 &&
                             deltas.rcvbuf_errors.value_or(1) == 0 &&
                             deltas.in_errors.value_or(1) == 0 &&
                             deltas.softnet_dropped.value_or(1) == 0;
  const bool lossless = delivered && counters_zero;
  const bool pass = delivered && book_match;

  Json record;
  record.text("tool", "bench_udp");
  record.text("kind", "receive");
  record.text("mode", options.mode);
  record.text("comparability", kNotANetwork);
  record.object("header", header_of(options, options.loopback.receiver_core));
  record.object("plan", plan_json(options, *packets));
  record.text("page_cache", dropped);
  Json schedule;
  schedule.text("kind", feed ? "feed" : (sent.paced ? "rate" : "unpaced"));
  schedule.number("rate", feed ? 0 : options.loopback.send.messages_per_second);
  schedule.decimal("feed_speedup", options.feed_speedup);
  schedule.number("span_ns", due.empty() ? 0 : due.back());
  record.object("schedule", schedule);
  Json pins;
  pins.object("sender", pin_json(loop->sender.pin));
  pins.object("receiver", pin_json(loop->receiver_pin));
  pins.signed_number("sq_core", options.mode_options.sq_core);
  record.object("pins", pins);
  Json buffer_json;
  buffer_json.number("requested", static_cast<std::uint64_t>(loop->receive_buffer.requested));
  buffer_json.number("read_back", static_cast<std::uint64_t>(loop->receive_buffer.read_back));
  record.object("receive_buffer", buffer_json);
  Json options_json;
  options_json.number("slot_bytes", options.mode_options.slot_bytes);
  options_json.number("ring_buffers", options.mode_options.ring_buffers);
  options_json.number("ring_entries", options.mode_options.ring_entries);
  options_json.number("idle_timeout_ms",
                      static_cast<std::uint64_t>(options.mode_options.idle_timeout.count()));
  options_json.number("sq_idle_ms", options.mode_options.sq_idle_ms);
  record.object("mode_options", options_json);
  record.object("send", send_json(sent));
  Json received;
  received.text("end", udp::to_string(loop->received.end));
  received.signed_number("error", loop->received.error);
  received.object("mode", mode_json(mode));
  received.number("feed_datagrams", loop->feed.datagrams);
  received.number("malformed", loop->feed.malformed());
  received.number("admitted", sequence.admitted);
  received.number("gaps", sequence.gaps);
  received.number("lost", sequence.lost);
  received.number("duplicate_messages", sequence.duplicate_messages);
  received.number("heartbeats", sequence.heartbeats);
  received.number("foreign_session", sequence.foreign_session);
  received.number("next_expected", loop->next_expected);
  received.number("receive_window_ns", stamps.end_ns > ready_ns ? stamps.end_ns - ready_ns : 0);
  record.object("receive", received);
  Json drops;
  drops.number("failed_sends", sent.failed_packets);
  drops.number("tracker_gaps", sequence.gaps);
  drops.number("tracker_lost", sequence.lost);
  drops.number("tracker_duplicates", sequence.duplicate_messages);
  drops.number("truncated", mode.truncated);
  drops.number("no_buffers", mode.no_buffers);
  drops.number("cq_overflow", mode.cq_overflow);
  if (mode.rxq_overflow_set) {
    drops.number("rxq_overflow", mode.rxq_overflow);
  } else {
    drops.null("rxq_overflow");
  }
  optional_number(drops, "socket_drops", socket_dropped);
  optional_number(drops, "rcvbuf_errors", deltas.rcvbuf_errors);
  optional_number(drops, "in_errors", deltas.in_errors);
  optional_number(drops, "softnet_dropped", deltas.softnet_dropped);
  record.object("drops", drops);
  record.boolean("delivered", delivered);
  record.boolean("lossless", lossless);
  Json book_json;
  book_json.number("file_frames", by_file.frames);
  book_json.number("udp_frames", by_udp.frames);
  book_json.text("file_end_hash", std::format("{:016x}", by_file.end_hash));
  book_json.text("udp_end_hash", std::format("{:016x}", by_udp.end_hash));
  book_json.boolean("match", book_match);
  record.object("book", book_json);
  if (sent.paced) {
    record.object("latency",
                  latency_json(stamps, due, sent.start_ns, options.warmup_ms * 1'000'000));
  } else {
    record.null("latency");
  }
  Json cpu;
  cpu.object("sender", thread_cpu_json(sender_cpu.start, sender_cpu.end));
  cpu.number("sender_window_ns", sent.elapsed_ns());
  cpu.object("receiver", thread_cpu_json(stamps.receiver_start, stamps.receiver_end));
  cpu.number("window_ns", stamps.end_ns > ready_ns ? stamps.end_ns - ready_ns : 0);
  cpu.object("kernel", stamps.ended ? cpu_ticks_json(before, stamps.at_end) : Json{});
  record.object("cpu", cpu);
  record.text("verdict", pass ? "PASS" : "FAIL");

  std::println("bench_udp_mode {}", options.mode);
  std::println("bench_udp_delivered {}", delivered ? "yes" : "no");
  std::println("bench_udp_lossless {}", lossless ? "yes" : "no");
  std::println("bench_udp_book {}", book_match ? "matches the file" : "differs from the file");
  std::println("bench_udp_verdict {}", pass ? "PASS" : "FAIL");
  if (!write_record(options, record)) {
    std::println(stderr, "bench_udp: cannot write {}", options.out);
    return 2;
  }
  return pass ? 0 : 1;
}

// Every mode this build has, set up on a fresh pair and waited on once with nothing sent:
// a mode that works times out, and one that does not says which call failed.
int run_probe(const Options& options) {
  udp::ModeOptions probe_options = options.mode_options;
  probe_options.idle_timeout = std::chrono::milliseconds{20};
  for (const std::string_view id : udp::kModeIds) {
    if (!udp::mode_built(id)) {
      std::println("bench_udp_probe {} not built: {}", id, udp::why_not_built(id));
      continue;
    }
    auto pair = udp::open_loopback_pair();
    if (!pair || !udp::set_receive_timeout(pair->receiver.get(), probe_options.idle_timeout)) {
      std::println("bench_udp_probe {} no socket", id);
      continue;
    }
    const auto said = udp::with_mode(id, probe_options, [&](auto make) {
      auto mode = make(pair->receiver.get());
      if (mode.counters().setup_error != 0) {
        return std::format("setup failed: {} errno {}", mode.counters().setup_call,
                           mode.counters().setup_error);
      }
      std::array<udp::Datagram, udp::kMostBatch> batch{};
      const udp::WaitResult result = mode.wait(batch);
      if (result.status == udp::WaitStatus::idle_timeout) {
        return std::string{"ok: set up, and an empty wait timed out"};
      }
      return std::format(
          "wait failed: {} errno {}",
          mode.counters().setup_call.empty() ? "receive" : mode.counters().setup_call,
          result.error);
    });
    std::println("bench_udp_probe {} {}", id, said.value_or("not built"));
  }
#if ITCH_UDP_HAS_RING_MODES
  const udp::RingProbe ring = udp::probe_ring();
  std::println("bench_udp_probe_ring {}", ring.summary);
#endif
  return 0;
}

}  // namespace

int main(int argc, const char* const* argv) {
  try {
    const auto options = parse_options(std::span{argv + 1, static_cast<std::size_t>(argc - 1)});
    if (!options) {
      static_cast<void>(std::fputs(
          "usage: bench_udp FILE --mode ID|sender [--rate MSGS_PER_S | --feed-speedup X] "
          "[--warmup-ms N] [--first N] [--frames N] [--datagram BYTES] [--max-messages N] "
          "[--flush-gap-ns N] [--heartbeat-gap-ns N] [--core N] [--receiver-core N] "
          "[--sq-core N] [--receive-buffer BYTES] [--timeout-ms N] [--slot BYTES] "
          "[--ring-buffers N] [--max-orders N] [--max-levels N] [--max-pages N] "
          "[--max-slots N] [--out FILE] [--commit HASH] [--dataset NAME] "
          "[--dataset-sha256 HEX]\n"
          "       bench_udp FILE --peaks [--first N] [--frames N]\n"
          "       bench_udp --probe [--sq-core N]\n",
          stderr));
      return 2;
    }
    if (options->probe) {
      return run_probe(*options);
    }
    if (options->peaks) {
      return run_peaks(*options);
    }
    return options->mode == "sender" ? run_sender(*options) : run_receive(*options);
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
