// The replay sender: a prebuilt mold::PacketArray sent in order down a connected socket,
// spin-paced to a target message rate, with a record of what it actually did.
//
// Packet i is due when the messages before it would have gone at the target rate:
//
//     due(i) = start + messages_before(i) * 1e9 / rate   ns
//
// so a heartbeat costs no schedule time, and each send starts at the first clock read
// at or past its due time, with no sleep anywhere (the comparison plan's item 5). The
// lateness of a send is that read less its due time, so a sender that stalls is charged
// for it rather than given a new start. A rate of 0 sends as fast as the socket takes
// packets, which is how the sender's own ceiling is found (task 2) before any run near
// it is trusted.
//
// The clock and the socket are template parameters, so that a test drives the pacing
// with a clock it moves by hand and a socket that only records. The loop allocates
// nothing and reads nothing but the array, the clock and the socket's result. A failed
// send is counted with its errno and not retried: on loopback the packet is gone, and
// the receiver's sequence check will see the gap.
#pragma once

#include <array>
#include <bit>
#include <cerrno>
#include <chrono>
#include <concepts>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <limits>
#include <span>
#include <string_view>

#include "itch/mold_packets.hpp"
#include "platform/affinity.hpp"
#include "udp/socket.hpp"

namespace itch::bench::udp {

template <class Clock>
concept SendClock = requires(Clock& clock) {
  { clock.now_ns() } -> std::same_as<std::uint64_t>;
};

template <class Socket>
concept DatagramSocket = requires(Socket& socket, std::span<const std::byte> datagram) {
  { socket.send(datagram) } -> std::same_as<SendResult>;
};

// steady_clock, which is CLOCK_MONOTONIC on Linux: the clock the comparison plan paces
// and stamps with on both sides.
struct SteadyClock {
  [[nodiscard]] static std::uint64_t now_ns() noexcept {
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());
  }
};

struct SendPlan {
  std::uint64_t messages_per_second = 0;  // 0 sends unpaced.
};

// A schedule's numerator, messages_before * 1e9, has to fit 64 bits.
inline constexpr std::uint64_t kNsPerSecond = 1'000'000'000;
inline constexpr std::uint64_t kMostScheduledMessages =
    std::numeric_limits<std::uint64_t>::max() / kNsPerSecond;

// Bucket 0 holds a lateness of 0 ns, and bucket b > 0 one in [2^(b-1), 2^b) ns. The last
// bucket also takes everything above it.
inline constexpr std::size_t kLatenessBuckets = 40;

[[nodiscard]] constexpr std::size_t lateness_bucket(std::uint64_t late_ns) noexcept {
  const auto width = static_cast<std::size_t>(std::bit_width(late_ns));
  return width < kLatenessBuckets ? width : kLatenessBuckets - 1;
}

struct SendReport {
  bool paced = false;
  std::uint64_t target_rate = 0;  // Messages a second, 0 when unpaced.
  std::uint64_t start_ns = 0;     // The schedule's start.
  std::uint64_t end_ns = 0;       // The clock after the last send returned.
  std::uint64_t packets = 0;      // Sends tried, one per packet of the array.
  std::uint64_t sent_packets = 0;
  std::uint64_t sent_messages = 0;
  std::uint64_t sent_bytes = 0;
  std::uint64_t failed_packets = 0;
  std::uint64_t failed_messages = 0;
  std::uint64_t no_buffers = 0;  // ENOBUFS.
  std::uint64_t refused = 0;     // ECONNREFUSED: nothing was bound at the other end.
  std::uint64_t other_errors = 0;
  int first_error = 0;
  // Lateness of each send's start against its due time, paced runs only.
  std::uint64_t late_max_ns = 0;
  std::uint64_t late_total_ns = 0;
  std::array<std::uint64_t, kLatenessBuckets> lateness{};

  [[nodiscard]] std::uint64_t elapsed_ns() const noexcept { return end_ns - start_ns; }
  // Messages sent a second over the run, from the schedule's start to the last send's end.
  [[nodiscard]] double achieved_rate() const noexcept {
    const std::uint64_t elapsed = elapsed_ns();
    return elapsed == 0 ? 0.0
                        : static_cast<double>(sent_messages) * 1e9 / static_cast<double>(elapsed);
  }
};

enum class SendError : std::uint8_t {
  schedule_overflow,  // More messages than a schedule can number.
};

template <SendClock Clock, DatagramSocket Socket>
[[nodiscard]] std::expected<SendReport, SendError> send_packets(
    const mold::PacketArray& packets, const SendPlan& plan, Clock& clock,
    Socket& socket) noexcept(noexcept(clock.now_ns()) &&
                             noexcept(socket.send(std::span<const std::byte>{}))) {
  const std::uint64_t rate = plan.messages_per_second;
  if (rate != 0 && packets.messages() > kMostScheduledMessages) {
    return std::unexpected(SendError::schedule_overflow);
  }
  SendReport report;
  report.paced = rate != 0;
  report.target_rate = rate;
  std::uint64_t before = 0;
  report.start_ns = clock.now_ns();
  for (std::size_t i = 0; i < packets.size(); ++i) {
    const mold::PacketRef& ref = packets.ref(i);
    if (rate != 0) {
      const std::uint64_t due = report.start_ns + (before * kNsPerSecond / rate);
      std::uint64_t now = clock.now_ns();
      while (now < due) {
        now = clock.now_ns();
      }
      const std::uint64_t late = now - due;
      report.late_max_ns = late > report.late_max_ns ? late : report.late_max_ns;
      report.late_total_ns += late;
      ++report.lateness[lateness_bucket(late)];
    }
    const SendResult result = socket.send(packets.packet(i));
    ++report.packets;
    if (result.sent) {
      ++report.sent_packets;
      report.sent_messages += ref.messages;
      report.sent_bytes += ref.size;
    } else {
      ++report.failed_packets;
      report.failed_messages += ref.messages;
      report.first_error = report.first_error == 0 ? result.error : report.first_error;
      if (result.error == ENOBUFS) {
        ++report.no_buffers;
      } else if (result.error == ECONNREFUSED) {
        ++report.refused;
      } else {
        ++report.other_errors;
      }
    }
    before += ref.messages;
  }
  report.end_ns = clock.now_ns();
  return report;
}

// The sender as its thread runs it: pinned first if a core is asked for, then the loop.
// Pinning is Linux only. Elsewhere pin_current_thread() says it was not done and why,
// and the run goes on unpinned with that on its record, never a claim. The receiver's
// thread is pinned the same way.
struct SenderRun {
  PinOutcome pin;
  std::expected<SendReport, SendError> report = std::unexpected(SendError::schedule_overflow);
};

[[nodiscard]] inline PinOutcome pin_thread(int core) {
  if (core < 0) {
    return {.requested_core = -1, .pinned = false, .attempt = "no core was requested"};
  }
  return pin_current_thread(core);
}

// Whether this build can pin at all: false on macOS, where a requested core is reported
// as unsupported.
inline constexpr bool kCanPin = kPinningRequired;

}  // namespace itch::bench::udp
