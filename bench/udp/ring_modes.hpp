// U1 to U5: the io_uring modes, each a ReceiveMode on the receiver's socket, on liburing
// 2.15 (io-uring-flamegraph.md, the comparison plan's mode table).
//
//     U1  IORING_OP_RECV single-shot, re-armed after each datagram   DEFER_TASKRUN, wait
//     U2  multishot RECV, provided-buffer ring                         DEFER_TASKRUN, wait
//     U3  multishot RECVMSG, provided buffers, SO_RXQ_OVFL             DEFER_TASKRUN, wait
//     U4  U2 with COOP_TASKRUN and TASKRUN_FLAG                        peek, enter when flagged
//     U5  U2 with SQPOLL, its thread on a third core, no taskrun flag  CQ spin
//
// U2 is the headline pair's ring side. Every mode sets SINGLE_ISSUER, which each of these
// flag sets allows (IU-20, IU-21), registers its ring fd and registers the socket as file 0,
// and none uses SEND_ZC, zcrx, NAPI, SO_BUSY_POLL or IOU_PBUF_RING_INC (IU-24 to IU-31).
//
// Where each step runs. The mode is made on the caller's thread and waits on the receiver's,
// and both SINGLE_ISSUER and a registered ring fd belong to one task: the kernel takes the
// issuer to be whoever enables the ring, and a registered ring fd is an index into the
// registering task's own table. So the ring is created disabled (IORING_SETUP_R_DISABLED),
// with the socket and the buffer ring registered while no task owns it, and the first
// wait(), on the receiver's thread, enables it and registers its fd. A setup call that fails
// on the caller's thread is the mode's setup error, which run_loopback() refuses before the
// sender starts; one that fails on the receiver's thread fails the first wait().
//
// The multishot modes. Each datagram takes one whole provided buffer of slot_bytes, never
// incremental consumption (IU-31), and release() hands it back to the ring. The CQ is twice
// the buffers or the SQ, whichever is more, so that every buffer's completion fits. A multishot
// request still ends when the ring runs out of buffers (-ENOBUFS) or the CQ overflows, and a CQE
// without IORING_CQE_F_MORE says so: the next wait() arms it again, after the batch's buffers have
// gone back, and counts a re-arm (IU-30). While it is down, datagrams queue on the socket,
// so a mode that re-arms often is measuring buffer starvation, which the counters show.
// RECV cannot say a datagram was cut short, so U1, U2, U4 and U5 count one that filled its
// whole buffer as truncated; the tools refuse a packing whose largest datagram does not fit
// with a byte to spare. U3 reads MSG_TRUNC from the kernel's own header.
//
// Waiting. U1 to U3 enter the kernel with GETEVENTS and the idle timeout (EXT_ARG), which
// under DEFER_TASKRUN is also what runs the receive's completions (IU-21). U4 never sleeps:
// it peeks the CQ and enters only when the kernel has flagged task work or an overflow. U5
// spins on the CQ while its SQ thread does the kernel's side; its CPU, and U4's, is 100% by
// construction (IU-46). Both spinning modes keep their own idle clock, as S4 does.
//
// CMake adds this header only to targets that link liburing::liburing, which needs
// -DITCH_LIBURING_ROOT on Linux, so macOS and every default build have no ring backend and
// no code change. Every buffer and both rings are made with the mode; wait() and release()
// allocate nothing.
#pragma once

#ifndef __linux__
#error "udp/ring_modes.hpp is Linux only; CMake adds it to targets that link liburing alone"
#endif

#include <liburing.h>
#include <sys/mman.h>
#include <sys/socket.h>

#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <format>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "udp/receive_mode.hpp"
#include "udp/socket_modes.hpp"

namespace itch::bench::udp {

enum class RingReceive : std::uint8_t {
  single_shot,        // U1: IORING_OP_RECV into the mode's own buffer, one at a time.
  multishot_recv,     // U2, U4, U5: IORING_OP_RECV with IORING_RECV_MULTISHOT.
  multishot_recvmsg,  // U3: IORING_OP_RECVMSG with IORING_RECV_MULTISHOT.
};

enum class RingWait : std::uint8_t {
  defer_taskrun,  // U1 to U3: DEFER_TASKRUN, wait in the kernel.
  coop_taskrun,   // U4: COOP_TASKRUN and TASKRUN_FLAG, peek, enter when flagged.
  sqpoll,         // U5: SQPOLL, spin on the CQ.
};

// The provided buffers' group: each mode has one ring of one group.
inline constexpr int kBufferGroup = 0;
// The registered socket's index in the ring's file table.
inline constexpr int kSocketFile = 0;
// The kernel's own limit on a provided-buffer ring (IU-30).
inline constexpr std::uint32_t kMostRingBuffers = 32768;

[[nodiscard]] constexpr std::string_view ring_mode_id(RingReceive receive, RingWait wait) noexcept {
  if (receive == RingReceive::single_shot) {
    return "U1";
  }
  if (receive == RingReceive::multishot_recvmsg) {
    return "U3";
  }
  switch (wait) {
    case RingWait::defer_taskrun: return "U2";
    case RingWait::coop_taskrun: return "U4";
    case RingWait::sqpoll: return "U5";
  }
  return "unknown";
}

// The setup flags of each wait. SQPOLL takes none of the taskrun flags (IU-20), and
// DEFER_TASKRUN needs SINGLE_ISSUER (IU-21), which every mode sets.
[[nodiscard]] constexpr unsigned ring_setup_flags(RingWait wait, bool sq_affinity) noexcept {
  unsigned flags = IORING_SETUP_R_DISABLED | IORING_SETUP_SINGLE_ISSUER | IORING_SETUP_CQSIZE;
  switch (wait) {
    case RingWait::defer_taskrun: flags |= IORING_SETUP_DEFER_TASKRUN; break;
    case RingWait::coop_taskrun:
      flags |= IORING_SETUP_COOP_TASKRUN | IORING_SETUP_TASKRUN_FLAG;
      break;
    case RingWait::sqpoll:
      flags |= IORING_SETUP_SQPOLL | (sq_affinity ? IORING_SETUP_SQ_AFF : 0U);
      break;
  }
  return flags;
}

template <RingReceive Receive, RingWait Wait>
class RingMode {
 public:
  static constexpr std::string_view kId = ring_mode_id(Receive, Wait);
  static constexpr bool kMultishot = Receive != RingReceive::single_shot;

  RingMode(int fd, const ModeOptions& options)
      : fd_{fd},
        slot_bytes_{options.slot_bytes},
        buffers_{kMultishot ? options.ring_buffers : 0U},
        idle_timeout_{options.idle_timeout},
        timeout_{.tv_sec = options.idle_timeout.count() / 1000,
                 .tv_nsec = (options.idle_timeout.count() % 1000) * 1'000'000} {
    if constexpr (kMultishot) {
      if (buffers_ == 0 || buffers_ > kMostRingBuffers || (buffers_ & (buffers_ - 1)) != 0 ||
          slot_bytes_ == 0 || slot_bytes_ > kDatagramBuffer) {
        fail_setup("ModeOptions", EINVAL);
        return;
      }
      slots_.resize(static_cast<std::size_t>(buffers_) * slot_bytes_);
    } else {
      slots_.resize(kDatagramBuffer);
    }
    if constexpr (Receive == RingReceive::multishot_recvmsg) {
      control_bytes_ = kRxqOverflowControl;
      enable_rxq_overflow(fd_, counters_);
      if (counters_.setup_error != 0) {
        return;
      }
    }
    io_uring_params params{};
    params.flags = ring_setup_flags(Wait, options.sq_core >= 0);
    const std::uint32_t completions = kMultishot ? buffers_ : 1U;
    params.cq_entries =
        2 * (completions > options.ring_entries ? completions : options.ring_entries);
    if constexpr (Wait == RingWait::sqpoll) {
      params.sq_thread_cpu =
          options.sq_core >= 0 ? static_cast<std::uint32_t>(options.sq_core) : 0U;
      params.sq_thread_idle = options.sq_idle_ms;
    }
    int result = io_uring_queue_init_params(options.ring_entries, &ring_, &params);
    if (result < 0) {
      fail_setup("io_uring_queue_init_params", -result);
      return;
    }
    live_ = true;
    features_ = params.features;
    result = io_uring_register_files(&ring_, &fd_, 1);
    if (result < 0) {
      fail_setup("io_uring_register_files", -result);
      return;
    }
    if constexpr (kMultishot) {
      int error = 0;
      buffer_ring_ = io_uring_setup_buf_ring(&ring_, buffers_, kBufferGroup, 0, &error);
      if (buffer_ring_ == nullptr) {
        fail_setup("io_uring_setup_buf_ring", -error);
        return;
      }
      mask_ = io_uring_buf_ring_mask(buffers_);
      for (std::uint32_t id = 0; id < buffers_; ++id) {
        give_back(static_cast<std::uint16_t>(id));
      }
      publish();
    }
  }

  RingMode(const RingMode&) = delete;
  RingMode& operator=(const RingMode&) = delete;
  RingMode(RingMode&&) = delete;
  RingMode& operator=(RingMode&&) = delete;

  // The issuer may already have exited, in which case the kernel refuses to unregister the
  // buffer ring from this thread; the mapping is then dropped here and the ring with the
  // ring's own teardown.
  ~RingMode() {
    if (buffer_ring_ != nullptr &&
        io_uring_free_buf_ring(&ring_, buffer_ring_, buffers_, kBufferGroup) != 0) {
      ::munmap(buffer_ring_, static_cast<std::size_t>(buffers_) * sizeof(io_uring_buf));
    }
    if (live_) {
      io_uring_queue_exit(&ring_);
    }
  }

  [[nodiscard]] WaitResult wait(std::span<Datagram> batch) noexcept {
    ++counters_.waits;
    if (batch.empty()) {
      return {.status = WaitStatus::failed, .count = 0, .error = EINVAL};
    }
    if (!enabled_ && !enable()) {
      return failure(counters_.last_error);
    }
    for (;;) {
      const std::size_t count = harvest(batch);
      if (count > 0) {
        counters_.datagrams += count;
        return {.status = WaitStatus::datagrams, .count = count, .error = 0};
      }
      if (!armed_ && failed_ == 0) {
        arm();
      }
      if (failed_ != 0) {
        return failure(failed_);
      }
      const WaitResult waited = block();
      if (waited.status != WaitStatus::datagrams) {
        return waited;
      }
    }
  }

  // Each datagram's buffer goes back to the ring, found from where its bytes are. U1's one
  // buffer is read again when the next wait() arms the next receive.
  void release(std::span<const Datagram> done) noexcept {
    if constexpr (kMultishot) {
      for (const Datagram& datagram : done) {
        const auto offset = static_cast<std::size_t>(datagram.bytes.data() - slots_.data());
        give_back(static_cast<std::uint16_t>(offset / slot_bytes_));
      }
      publish();
    }
  }

  [[nodiscard]] const ModeCounters& counters() const noexcept { return counters_; }
  // What io_uring_setup() said the kernel offers (IORING_FEAT_*), for the record.
  [[nodiscard]] unsigned features() const noexcept { return features_; }

 private:
  void fail_setup(std::string_view call, int error) noexcept {
    counters_.setup_call = call;
    counters_.setup_error = error;
    counters_.last_error = error;
  }

  [[nodiscard]] WaitResult failure(int error) noexcept {
    ++counters_.failures;
    counters_.last_error = error;
    return {.status = WaitStatus::failed, .count = 0, .error = error};
  }

  // On the receiver's thread: make it the ring's one issuer, then register the ring fd in
  // its table.
  [[nodiscard]] bool enable() noexcept {
    int result = io_uring_enable_rings(&ring_);
    if (result < 0) {
      fail_setup("io_uring_enable_rings", -result);
      return false;
    }
    result = io_uring_register_ring_fd(&ring_);
    if (result != 1) {
      fail_setup("io_uring_register_ring_fd", result < 0 ? -result : EINVAL);
      return false;
    }
    enabled_ = true;
    return true;
  }

  [[nodiscard]] std::byte* slot(std::uint32_t id) noexcept {
    return slots_.data() + (static_cast<std::size_t>(id) * slot_bytes_);
  }

  void give_back(std::uint16_t id) noexcept {
    io_uring_buf_ring_add(buffer_ring_, slot(id), static_cast<unsigned>(slot_bytes_), id, mask_,
                          staged_++);
  }

  void publish() noexcept {
    if (staged_ != 0) {
      io_uring_buf_ring_advance(buffer_ring_, staged_);
      staged_ = 0;
    }
  }

  // One receive SQE, submitted by the next block(). The first arm is not a re-arm.
  void arm() noexcept {
    io_uring_sqe* sqe = io_uring_get_sqe(&ring_);
    if (sqe == nullptr) {  // Only one SQE is ever outstanding, so the SQ cannot be full.
      failed_ = EBUSY;
      return;
    }
    if constexpr (Receive == RingReceive::single_shot) {
      io_uring_prep_recv(sqe, kSocketFile, slots_.data(), slots_.size(), 0);
      sqe->flags = static_cast<std::uint8_t>(sqe->flags | IOSQE_FIXED_FILE);
    } else {
      if constexpr (Receive == RingReceive::multishot_recv) {
        io_uring_prep_recv_multishot(sqe, kSocketFile, nullptr, 0, 0);
      } else {
        message_ = {};
        message_.msg_controllen = control_bytes_;
        io_uring_prep_recvmsg_multishot(sqe, kSocketFile, &message_, 0);
      }
      sqe->flags = static_cast<std::uint8_t>(sqe->flags | IOSQE_FIXED_FILE | IOSQE_BUFFER_SELECT);
      sqe->buf_group = static_cast<std::uint16_t>(kBufferGroup);
      counters_.rearms += arms_ > 0 ? 1U : 0U;
    }
    ++arms_;
    armed_ = true;
    unsubmitted_ = true;
  }

  // Takes what the CQ holds, up to the batch, without entering the kernel.
  [[nodiscard]] std::size_t harvest(std::span<Datagram> batch) noexcept {
    const unsigned ready = io_uring_cq_ready(&ring_);
    const unsigned head = *ring_.cq.khead;
    unsigned seen = 0;
    std::size_t count = 0;
    while (seen < ready && count < batch.size()) {
      const io_uring_cqe& cqe = ring_.cq.cqes[(head + seen) & ring_.cq.ring_mask];
      ++seen;
      take(cqe, batch, count);
    }
    io_uring_cq_advance(&ring_, seen);
    publish();
    counters_.cq_overflow = __atomic_load_n(ring_.cq.koverflow, __ATOMIC_RELAXED);
    return count;
  }

  void take(const io_uring_cqe& cqe, std::span<Datagram> batch, std::size_t& count) noexcept {
    const int result = cqe.res;
    if constexpr (!kMultishot) {
      armed_ = false;
      if (result < 0) {
        failed_ = -result;
        return;
      }
      counters_.bytes += static_cast<std::size_t>(result);
      batch[count++] = {
          .bytes = std::span<const std::byte>{slots_}.first(static_cast<std::size_t>(result))};
    } else {
      if ((cqe.flags & IORING_CQE_F_MORE) == 0) {
        armed_ = false;
      }
      if ((cqe.flags & IORING_CQE_F_BUFFER) == 0) {
        if (result == -ENOBUFS) {
          ++counters_.no_buffers;
        } else if (result < 0) {
          failed_ = -result;
        }
        return;
      }
      const auto id = static_cast<std::uint16_t>(cqe.flags >> IORING_CQE_BUFFER_SHIFT);
      if (result < 0) {
        give_back(id);
        failed_ = -result;
        return;
      }
      const auto filled = static_cast<std::size_t>(result);
      std::span<const std::byte> bytes{slot(id), filled < slot_bytes_ ? filled : slot_bytes_};
      if constexpr (Receive == RingReceive::multishot_recvmsg) {
        io_uring_recvmsg_out* out = io_uring_recvmsg_validate(slot(id), result, &message_);
        if (out == nullptr) {
          ++counters_.truncated;
          give_back(id);
          return;
        }
        counters_.truncated += (out->flags & MSG_TRUNC) != 0 ? 1U : 0U;
        for (cmsghdr* cmsg = io_uring_recvmsg_cmsg_firsthdr(out, &message_); cmsg != nullptr;
             cmsg = io_uring_recvmsg_cmsg_nexthdr(out, &message_, cmsg)) {
          if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SO_RXQ_OVFL &&
              cmsg->cmsg_len >= CMSG_LEN(sizeof(std::uint32_t))) {
            std::uint32_t drops = 0;
            std::memcpy(&drops, CMSG_DATA(cmsg), sizeof(drops));
            counters_.rxq_overflow =
                drops > counters_.rxq_overflow ? drops : counters_.rxq_overflow;
          }
        }
        const unsigned room = io_uring_recvmsg_payload_length(out, result, &message_);
        bytes = {static_cast<const std::byte*>(io_uring_recvmsg_payload(out, &message_)),
                 out->payloadlen < room ? out->payloadlen : room};
      } else {
        counters_.truncated += filled >= slot_bytes_ ? 1U : 0U;
      }
      counters_.bytes += bytes.size();
      batch[count++] = {.bytes = bytes};
    }
  }

  // Submits what is armed and waits for at least one CQE, each wait its own way. Returns
  // datagrams, with a count of 0, once the CQ may hold something.
  [[nodiscard]] WaitResult block() noexcept {
    constexpr WaitResult kGo{.status = WaitStatus::datagrams, .count = 0, .error = 0};
    if constexpr (Wait == RingWait::defer_taskrun) {
      for (;;) {
        io_uring_cqe* unused = nullptr;
        ++counters_.calls;
        const int result = io_uring_submit_and_wait_timeout(&ring_, &unused, 1, &timeout_, nullptr);
        unsubmitted_ = false;
        if (result >= 0) {
          return kGo;
        }
        if (result == -EINTR) {
          ++counters_.interrupted;
          continue;
        }
        if (result == -ETIME) {
          ++counters_.timeouts;
          return {.status = WaitStatus::idle_timeout, .count = 0, .error = ETIME};
        }
        return failure(-result);
      }
    } else {
      if (unsubmitted_) {
        ++counters_.calls;
        const int result = io_uring_submit(&ring_);
        if (result < 0) {
          return failure(-result);
        }
        unsubmitted_ = false;
      }
      std::uint32_t empty = 0;
      auto idle_since = std::chrono::steady_clock::time_point{};
      for (;;) {
        if (io_uring_cq_ready(&ring_) > 0) {
          return kGo;
        }
        // U4's completions wait as task work until it enters; either wait's overflow needs
        // an enter to flush.
        const unsigned flags = __atomic_load_n(ring_.sq.kflags, __ATOMIC_ACQUIRE);
        constexpr unsigned kNeedsEnter =
            (Wait == RingWait::coop_taskrun ? IORING_SQ_TASKRUN : 0U) | IORING_SQ_CQ_OVERFLOW;
        if ((flags & kNeedsEnter) != 0) {
          ++counters_.calls;
          const int result = io_uring_get_events(&ring_);
          if (result < 0 && result != -EINTR && result != -EAGAIN && result != -EBUSY) {
            return failure(-result);
          }
          continue;
        }
        if (++empty % kSpinsPerClockRead != 0) {
          continue;
        }
        const auto now = std::chrono::steady_clock::now();
        if (empty == kSpinsPerClockRead) {
          idle_since = now;
        } else if (now - idle_since >= idle_timeout_) {
          ++counters_.timeouts;
          return {.status = WaitStatus::idle_timeout, .count = 0, .error = ETIME};
        }
      }
    }
  }

  int fd_;
  std::size_t slot_bytes_;
  std::uint32_t buffers_;
  std::chrono::milliseconds idle_timeout_;
  __kernel_timespec timeout_;
  std::vector<std::byte> slots_;
  std::size_t control_bytes_ = 0;
  msghdr message_{};
  io_uring ring_{};
  io_uring_buf_ring* buffer_ring_ = nullptr;
  int mask_ = 0;
  int staged_ = 0;
  unsigned features_ = 0;
  std::uint64_t arms_ = 0;
  int failed_ = 0;
  bool live_ = false;
  bool enabled_ = false;
  bool armed_ = false;
  bool unsubmitted_ = false;
  ModeCounters counters_;
};

using RecvSingleShotMode = RingMode<RingReceive::single_shot, RingWait::defer_taskrun>;    // U1
using RecvMultishotMode = RingMode<RingReceive::multishot_recv, RingWait::defer_taskrun>;  // U2
using RecvmsgMultishotMode =
    RingMode<RingReceive::multishot_recvmsg, RingWait::defer_taskrun>;                        // U3
using RecvMultishotCoopMode = RingMode<RingReceive::multishot_recv, RingWait::coop_taskrun>;  // U4
using RecvMultishotSqpollMode = RingMode<RingReceive::multishot_recv, RingWait::sqpoll>;      // U5

static_assert(ReceiveMode<RecvSingleShotMode>);
static_assert(ReceiveMode<RecvMultishotMode>);
static_assert(ReceiveMode<RecvmsgMultishotMode>);
static_assert(ReceiveMode<RecvMultishotCoopMode>);
static_assert(ReceiveMode<RecvMultishotSqpollMode>);

// What the kernel offers, for the smoke test: the receive opcodes its probe lists, and
// the features a plain ring reports (IU-29 asks that every line be checked on the host).
struct RingProbe {
  std::string summary;
};

[[nodiscard]] inline RingProbe probe_ring() {
  RingProbe out;
  io_uring_probe* probe = io_uring_get_probe();
  if (probe == nullptr) {
    out.summary = "io_uring_get_probe failed";
  } else {
    out.summary =
        std::format("last_op {} RECV {} RECVMSG {}", probe->last_op,
                    io_uring_opcode_supported(probe, IORING_OP_RECV) != 0 ? "yes" : "no",
                    io_uring_opcode_supported(probe, IORING_OP_RECVMSG) != 0 ? "yes" : "no");
    io_uring_free_probe(probe);
  }
  io_uring_params params{};
  io_uring ring{};
  const int result = io_uring_queue_init_params(8, &ring, &params);
  if (result < 0) {
    out.summary += std::format(", a plain ring failed: errno {}", -result);
    return out;
  }
  out.summary += std::format(", features {:#x}:", params.features);
  constexpr std::array<std::pair<unsigned, std::string_view>, 8> kNames{{
      {IORING_FEAT_FAST_POLL, "FAST_POLL"},
      {IORING_FEAT_NATIVE_WORKERS, "NATIVE_WORKERS"},
      {IORING_FEAT_EXT_ARG, "EXT_ARG"},
      {IORING_FEAT_NODROP, "NODROP"},
      {IORING_FEAT_SUBMIT_STABLE, "SUBMIT_STABLE"},
      {IORING_FEAT_REG_REG_RING, "REG_REG_RING"},
      {IORING_FEAT_RECVSEND_BUNDLE, "RECVSEND_BUNDLE"},
      {IORING_FEAT_MIN_TIMEOUT, "MIN_TIMEOUT"},
  }};
  for (const auto& [bit, name] : kNames) {
    out.summary += std::format(" {}={}", name, (params.features & bit) != 0 ? "yes" : "no");
  }
  io_uring_queue_exit(&ring);
  return out;
}

}  // namespace itch::bench::udp
