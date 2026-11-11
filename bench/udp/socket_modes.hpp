// S2, S3 and S4: the socket modes only Linux has, each a ReceiveMode on the receiver's
// socket (io-uring-flamegraph.md, the comparison plan's mode table).
//
//     S2  recvmsg with cmsg                 blocking   one buffer, SO_RXQ_OVFL read back
//     S3  recvmmsg, vlen 64, MSG_WAITFORONE  blocking   the first datagram waits, the rest do not
//     S4  recvmmsg, vlen 64, MSG_DONTWAIT    spin       never sleeps
//
// S3 is the headline pair's socket side. S2 and S3 block under the socket's receive
// timeout, as S1 does; S4 never blocks, so it keeps its own idle clock, read once every
// kSpinsPerClockRead empty calls so that the clock costs the spin almost nothing. A spin
// mode's CPU is 100% by construction, which the results say instead of a CPU figure
// (IU-46).
//
// CMake adds this header to Linux targets only (recvmmsg, MSG_WAITFORONE and SO_RXQ_OVFL
// are Linux calls), so a macOS build has no socket backend beyond S1 and no code change.
// Every buffer is allocated when the mode is made; wait() and release() allocate nothing.
#pragma once

#if !defined(__linux__)
#error "udp/socket_modes.hpp is Linux only; CMake adds it to Linux targets alone"
#endif

#include <sys/socket.h>
#include <sys/types.h>

#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <span>
#include <string_view>
#include <vector>

#include "udp/receive_mode.hpp"

namespace itch::bench::udp {

// How many empty calls a spin mode makes between reads of its idle clock.
inline constexpr std::uint32_t kSpinsPerClockRead = 4096;

// The control buffer one SO_RXQ_OVFL message needs: a cmsghdr and a 32-bit count.
inline constexpr std::size_t kRxqOverflowControl = CMSG_SPACE(sizeof(std::uint32_t));

// The socket's drop count, if a control message in msg carries one.
[[nodiscard]] inline bool read_rxq_overflow(msghdr& msg, std::uint64_t& drops) noexcept {
  for (cmsghdr* cmsg = CMSG_FIRSTHDR(&msg); cmsg != nullptr; cmsg = CMSG_NXTHDR(&msg, cmsg)) {
    if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SO_RXQ_OVFL &&
        cmsg->cmsg_len >= CMSG_LEN(sizeof(std::uint32_t))) {
      std::uint32_t count = 0;
      std::memcpy(&count, CMSG_DATA(cmsg), sizeof(count));
      drops = count > drops ? count : drops;
      return true;
    }
  }
  return false;
}

// Turns on SO_RXQ_OVFL, recording a failure as the mode's setup error.
inline void enable_rxq_overflow(int fd, ModeCounters& counters) noexcept {
  const int on = 1;
  if (::setsockopt(fd, SOL_SOCKET, SO_RXQ_OVFL, &on, sizeof(on)) != 0) {
    counters.setup_call = "setsockopt SO_RXQ_OVFL";
    counters.setup_error = errno;
    return;
  }
  counters.rxq_overflow_set = true;
}

// S2: one blocking recvmsg() per datagram into one buffer, with room for the socket's
// SO_RXQ_OVFL control message, which the mode reads on every datagram.
class RecvmsgMode {
 public:
  static constexpr std::string_view kId = "S2";

  explicit RecvmsgMode(int fd) : fd_{fd}, buffer_(kDatagramBuffer), control_(kRxqOverflowControl) {
    enable_rxq_overflow(fd_, counters_);
  }

  [[nodiscard]] WaitResult wait(std::span<Datagram> batch) noexcept {
    ++counters_.waits;
    if (batch.empty()) {
      return {.status = WaitStatus::failed, .count = 0, .error = EINVAL};
    }
    for (;;) {
      iovec vector{.iov_base = buffer_.data(), .iov_len = buffer_.size()};
      msghdr msg{};
      msg.msg_iov = &vector;
      msg.msg_iovlen = 1;
      msg.msg_control = control_.data();
      msg.msg_controllen = control_.size();
      ++counters_.calls;
      const ::ssize_t got = ::recvmsg(fd_, &msg, 0);
      if (got >= 0) {
        const auto size = static_cast<std::size_t>(got);
        ++counters_.datagrams;
        counters_.bytes += size;
        counters_.truncated += (msg.msg_flags & MSG_TRUNC) != 0 ? 1U : 0U;
        static_cast<void>(read_rxq_overflow(msg, counters_.rxq_overflow));
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

  void release(std::span<const Datagram> /*done*/) noexcept {}

  [[nodiscard]] const ModeCounters& counters() const noexcept { return counters_; }

 private:
  int fd_;
  std::vector<std::byte> buffer_;
  std::vector<std::byte> control_;
  ModeCounters counters_;
};

// recvmmsg() into kMostBatch buffers of slot_bytes each, one call filling as many as are
// queued. Blocking is S3 (MSG_WAITFORONE: wait for the first, take the rest only if they
// are already there) and spinning is S4 (MSG_DONTWAIT: never wait).
template <bool Spin>
class RecvmmsgMode {
 public:
  static constexpr std::string_view kId = Spin ? "S4" : "S3";

  RecvmmsgMode(int fd, const ModeOptions& options)
      : fd_{fd},
        slot_bytes_{options.slot_bytes},
        idle_timeout_{options.idle_timeout},
        slots_(kMostBatch * options.slot_bytes),
        vectors_(kMostBatch),
        headers_(kMostBatch) {}

  [[nodiscard]] WaitResult wait(std::span<Datagram> batch) noexcept {
    ++counters_.waits;
    if (batch.empty()) {
      return {.status = WaitStatus::failed, .count = 0, .error = EINVAL};
    }
    const std::size_t vlen = batch.size() < kMostBatch ? batch.size() : kMostBatch;
    std::uint32_t empty = 0;
    auto idle_since = std::chrono::steady_clock::time_point{};
    for (;;) {
      for (std::size_t i = 0; i < vlen; ++i) {
        vectors_[i] = {.iov_base = slot(i), .iov_len = slot_bytes_};
        headers_[i] = {};
        headers_[i].msg_hdr.msg_iov = &vectors_[i];
        headers_[i].msg_hdr.msg_iovlen = 1;
      }
      ++counters_.calls;
      const int got =
          ::recvmmsg(fd_, headers_.data(), static_cast<unsigned>(vlen), kFlags, nullptr);
      if (got > 0) {
        const auto count = static_cast<std::size_t>(got);
        for (std::size_t i = 0; i < count; ++i) {
          const std::size_t size = headers_[i].msg_len;
          counters_.bytes += size;
          counters_.truncated += (headers_[i].msg_hdr.msg_flags & MSG_TRUNC) != 0 ? 1U : 0U;
          batch[i] = {.bytes = std::span<const std::byte>{slot(i),
                                                          size < slot_bytes_ ? size : slot_bytes_}};
        }
        counters_.datagrams += count;
        return {.status = WaitStatus::datagrams, .count = count, .error = 0};
      }
      const int error = got == 0 ? EAGAIN : errno;
      if (error == EINTR) {
        ++counters_.interrupted;
        continue;
      }
      if (error != EAGAIN && error != EWOULDBLOCK) {
        ++counters_.failures;
        counters_.last_error = error;
        return {.status = WaitStatus::failed, .count = 0, .error = error};
      }
      if constexpr (!Spin) {
        ++counters_.timeouts;
        return {.status = WaitStatus::idle_timeout, .count = 0, .error = error};
      } else {
        if (++empty % kSpinsPerClockRead != 0) {
          continue;
        }
        const auto now = std::chrono::steady_clock::now();
        if (empty == kSpinsPerClockRead) {
          idle_since = now;
        } else if (now - idle_since >= idle_timeout_) {
          ++counters_.timeouts;
          return {.status = WaitStatus::idle_timeout, .count = 0, .error = error};
        }
      }
    }
  }

  // The slots are filled again from the first on the next wait().
  void release(std::span<const Datagram> /*done*/) noexcept {}

  [[nodiscard]] const ModeCounters& counters() const noexcept { return counters_; }

 private:
  static constexpr int kFlags = Spin ? MSG_DONTWAIT : MSG_WAITFORONE;

  [[nodiscard]] std::byte* slot(std::size_t i) noexcept {
    return slots_.data() + (i * slot_bytes_);
  }

  int fd_;
  std::size_t slot_bytes_;
  std::chrono::milliseconds idle_timeout_;
  std::vector<std::byte> slots_;
  std::vector<iovec> vectors_;
  std::vector<mmsghdr> headers_;
  ModeCounters counters_;
};

using RecvmmsgWaitMode = RecvmmsgMode<false>;
using RecvmmsgSpinMode = RecvmmsgMode<true>;

static_assert(ReceiveMode<RecvmsgMode>);
static_assert(ReceiveMode<RecvmmsgWaitMode>);
static_assert(ReceiveMode<RecvmmsgSpinMode>);

}  // namespace itch::bench::udp
