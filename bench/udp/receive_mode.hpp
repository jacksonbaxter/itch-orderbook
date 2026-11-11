// One interface for every receive mode of T-URING-1, and S1, the one that is portable.
//
//     ID  Mode                                              Wait
//     S1  recvfrom                                          blocking         here
//     S2  recvmsg with cmsg                                 blocking         socket_modes.hpp
//     S3  recvmmsg, vlen 64, MSG_WAITFORONE                 blocking         socket_modes.hpp
//     S4  recvmmsg, vlen 64, MSG_DONTWAIT                   spin             socket_modes.hpp
//     U1  IORING_OP_RECV single-shot, re-armed              DEFER_TASKRUN    ring_modes.hpp
//     U2  multishot RECV, provided-buffer ring              DEFER_TASKRUN    ring_modes.hpp
//     U3  multishot RECVMSG, provided buffers, SO_RXQ_OVFL  DEFER_TASKRUN    ring_modes.hpp
//     U4  U2 with COOP_TASKRUN and TASKRUN_FLAG             peek, enter      ring_modes.hpp
//     U5  U2 with SQPOLL on a third core                    CQ spin          ring_modes.hpp
//
// socket_modes.hpp is Linux only and ring_modes.hpp needs liburing as well; modes.hpp
// names every mode by its ID for the tools.
// (io-uring-flamegraph.md, the comparison plan's mode table. U2 and S3 are the headline
// pair.) A mode does one thing: wait() fills a batch with datagrams, each a view of a
// buffer the mode owns, and release() hands those buffers back once the feed has read
// them, which for U2 to U5 returns them to the provided-buffer ring and for the socket
// modes does nothing. Everything after a datagram arrives is mold::FeedHandler, the
// same code for every mode, so the modes differ in how bytes arrive and in nothing
// they lead to.
//
// A Linux-only mode is a class in its own header that meets ReceiveMode, compiled only
// where its calls exist; CMake, not the preprocessor, decides which headers a target
// sees, so a macOS build has no recvmmsg or io_uring backend and needs no code change.
#pragma once

#include <sys/socket.h>
#include <sys/types.h>

#include <cerrno>
#include <chrono>
#include <concepts>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>
#include <vector>

#include "itch/mold_feed.hpp"

namespace itch::bench::udp {

// A buffer that holds any UDP datagram over IPv4 whole, so that no mode can cut one
// short without saying so.
inline constexpr std::size_t kDatagramBuffer = 65536;
// The batch a mode may fill in one wait(): S3 and S4's vlen.
inline constexpr std::size_t kMostBatch = 64;

struct Datagram {
  std::span<const std::byte> bytes;  // Valid until the mode's release().
};

enum class WaitStatus : std::uint8_t {
  datagrams,     // count datagrams are in the batch.
  idle_timeout,  // Nothing came within the socket's receive timeout.
  failed,        // A call failed; error holds its errno.
};

struct WaitResult {
  WaitStatus status = WaitStatus::failed;
  std::size_t count = 0;
  int error = 0;
};

// What every mode counts about its own calls. A field a mode cannot have stays 0, and
// the mode's results name which fields it fills.
struct ModeCounters {
  std::uint64_t waits = 0;  // wait() calls.
  std::uint64_t calls = 0;  // Receive syscalls or ring enters made by them.
  std::uint64_t datagrams = 0;
  std::uint64_t bytes = 0;
  std::uint64_t interrupted = 0;  // EINTR, retried.
  std::uint64_t timeouts = 0;
  std::uint64_t failures = 0;
  int last_error = 0;
  // Filled by the Linux modes. MSG_TRUNC on a datagram (S2, S3, S4, U3), or a datagram
  // that filled its whole buffer where the call cannot say (U1, U2, U4, U5); the
  // multishot re-arms after a CQE without IORING_CQE_F_MORE; -ENOBUFS completions; and
  // the ring's CQ overflow count (IU-30, IU-43).
  std::uint64_t truncated = 0;
  std::uint64_t rearms = 0;
  std::uint64_t no_buffers = 0;
  std::uint64_t cq_overflow = 0;
  // SO_RXQ_OVFL, for the modes that read a datagram's control messages (S2, U3): whether
  // the option was set, and the most the socket said it had dropped (IU-28, IU-43). The
  // kernel attaches the count only once it is above 0, so a mode that set the option and
  // saw none read 0.
  bool rxq_overflow_set = false;
  std::uint64_t rxq_overflow = 0;
  // A mode whose setup can fail, a ring for instance, says which call failed and its
  // errno here, and run_loopback() then refuses the run before the sender starts.
  std::string_view setup_call;
  int setup_error = 0;
};

// What a Linux mode is built with, beyond its socket. One struct for every mode, each
// reading only its own fields.
struct ModeOptions {
  // Each buffer of a mode that holds many datagrams at once (S3, S4, U2 to U5). Smaller
  // than kDatagramBuffer so that a batch's buffers do not all start 64 KiB apart, on the
  // same cache sets; the tools refuse a packing whose largest datagram does not fit.
  std::size_t slot_bytes = 2048;
  std::uint32_t ring_buffers = 1024;  // Provided buffers, a power of two up to 32768 (IU-30).
  std::uint32_t ring_entries = 64;    // Submission queue entries, U1 to U5.
  // How long a mode that the socket's receive timeout cannot reach (S4, U1 to U5) waits
  // with nothing arriving before it says so.
  std::chrono::milliseconds idle_timeout{2000};
  int sq_core = -1;                 // U5's SQ thread, -1 for none (IORING_SETUP_SQ_AFF).
  std::uint32_t sq_idle_ms = 1000;  // U5's SQ thread sleeps after this long with no work.
};

template <class Mode>
concept ReceiveMode =
    requires(Mode& mode, std::span<Datagram> batch, std::span<const Datagram> done) {
      { Mode::kId } -> std::convertible_to<std::string_view>;
      { mode.wait(batch) } -> std::same_as<WaitResult>;
      { mode.release(done) } -> std::same_as<void>;
      { mode.counters() } -> std::same_as<const ModeCounters&>;
    };

// S1: one blocking recvfrom() per datagram, into one buffer. The receive timeout set
// on the socket ends a wait with nothing to read.
class RecvfromMode {
 public:
  static constexpr std::string_view kId = "S1";

  explicit RecvfromMode(int fd) : fd_{fd}, buffer_(kDatagramBuffer) {}

  [[nodiscard]] WaitResult wait(std::span<Datagram> batch) noexcept {
    ++counters_.waits;
    if (batch.empty()) {
      return {.status = WaitStatus::failed, .count = 0, .error = EINVAL};
    }
    for (;;) {
      ++counters_.calls;
      const ::ssize_t got = ::recvfrom(fd_, buffer_.data(), buffer_.size(), 0, nullptr, nullptr);
      if (got >= 0) {
        const auto size = static_cast<std::size_t>(got);
        ++counters_.datagrams;
        counters_.bytes += size;
        batch[0] = {.bytes = std::span<const std::byte>{buffer_}.first(size)};
        return {.status = WaitStatus::datagrams, .count = 1, .error = 0};
      }
      const int error = errno;
      if (error == EINTR) {
        ++counters_.interrupted;
        continue;
      }
      if (error == EAGAIN || error == EWOULDBLOCK) {
        ++counters_.timeouts;
        return {.status = WaitStatus::idle_timeout, .count = 0, .error = error};
      }
      ++counters_.failures;
      counters_.last_error = error;
      return {.status = WaitStatus::failed, .count = 0, .error = error};
    }
  }

  // One buffer, read again on the next wait(): nothing to hand back.
  void release(std::span<const Datagram> /*done*/) noexcept {}

  [[nodiscard]] const ModeCounters& counters() const noexcept { return counters_; }

 private:
  int fd_;
  std::vector<std::byte> buffer_;
  ModeCounters counters_;
};

static_assert(ReceiveMode<RecvfromMode>);

enum class ReceiveEnd : std::uint8_t {
  end_of_session,  // The feed saw its end of session.
  idle_timeout,    // Nothing arrived within the timeout before an end of session.
  failed,          // A receive call failed.
};

[[nodiscard]] constexpr std::string_view to_string(ReceiveEnd end) noexcept {
  switch (end) {
    case ReceiveEnd::end_of_session: return "end of session";
    case ReceiveEnd::idle_timeout: return "idle timeout";
    case ReceiveEnd::failed: return "failed";
  }
  return "unknown";
}

struct ReceiveOutcome {
  ReceiveEnd end = ReceiveEnd::failed;
  int error = 0;
};

// The receive loop, the same for every mode: wait, hand each datagram to the feed in
// arrival order, release, until the feed has seen its end of session. It allocates
// nothing; the batch is the caller's.
template <ReceiveMode Mode, mold::FrameSink Sink>
[[nodiscard]] ReceiveOutcome receive_until_end(
    Mode& mode, mold::FeedHandler<Sink>& feed,
    std::span<Datagram> batch) noexcept(noexcept(mode.wait(batch)) &&
                                        noexcept(mode.release(std::span<const Datagram>{})) &&
                                        noexcept(feed.on_datagram(std::span<const std::byte>{}))) {
  while (!feed.ended()) {
    const WaitResult result = mode.wait(batch);
    if (result.status == WaitStatus::idle_timeout) {
      return {.end = ReceiveEnd::idle_timeout, .error = result.error};
    }
    if (result.status == WaitStatus::failed) {
      return {.end = ReceiveEnd::failed, .error = result.error};
    }
    const auto arrived = std::span<const Datagram>{batch.first(result.count)};
    for (const Datagram& datagram : arrived) {
      feed.on_datagram(datagram.bytes);
    }
    mode.release(arrived);
  }
  return {.end = ReceiveEnd::end_of_session, .error = 0};
}

}  // namespace itch::bench::udp
