// Every receive mode of T-URING-1 by its ID, and which of them this build has. A tool
// names a mode on its command line, and with_mode() hands the caller a factory for it,
// the factory run_loopback() takes, so that a tool has one code path for every mode.
//
// What a build has is CMake's choice, given to the compiler as two switches:
// ITCH_UDP_SOCKET_MODES on Linux for S2 to S4, and ITCH_UDP_RING_MODES where the target
// links liburing::liburing for U1 to U5. A macOS build has S1 alone, with no code change,
// and says so of every other mode rather than putting anything in its place.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string_view>
#include <utility>

#include "udp/receive_mode.hpp"

#if defined(ITCH_UDP_SOCKET_MODES) && ITCH_UDP_SOCKET_MODES
#include "udp/socket_modes.hpp"
#define ITCH_UDP_HAS_SOCKET_MODES 1
#else
#define ITCH_UDP_HAS_SOCKET_MODES 0
#endif
#if defined(ITCH_UDP_RING_MODES) && ITCH_UDP_RING_MODES
#include "udp/ring_modes.hpp"
#define ITCH_UDP_HAS_RING_MODES 1
#else
#define ITCH_UDP_HAS_RING_MODES 0
#endif

namespace itch::bench::udp {

inline constexpr std::array<std::string_view, 9> kModeIds{"S1", "S2", "S3", "S4", "U1",
                                                          "U2", "U3", "U4", "U5"};
inline constexpr bool kHasSocketModes = ITCH_UDP_HAS_SOCKET_MODES != 0;
inline constexpr bool kHasRingModes = ITCH_UDP_HAS_RING_MODES != 0;

// Room a buffer needs beyond the datagram: U3's kernel header and control message, with
// some to spare, and the byte that tells a whole buffer from a cut datagram. A packing
// whose largest datagram plus this does not fit a slot is refused for every mode, so that
// every mode receives the same packets.
inline constexpr std::size_t kSlotOverhead = 64;

[[nodiscard]] constexpr bool is_mode(std::string_view id) noexcept {
  for (const std::string_view known : kModeIds) {
    if (known == id) {
      return true;
    }
  }
  return false;
}

// The modes that hold many datagrams at once, each in a buffer of ModeOptions::slot_bytes.
[[nodiscard]] constexpr bool uses_slots(std::string_view id) noexcept {
  return id == "S3" || id == "S4" || id == "U2" || id == "U3" || id == "U4" || id == "U5";
}

// The most provided buffers a tool takes on its command line: the kernel's limit (IU-30).
inline constexpr std::uint32_t kMostRingBuffersAsked = 32768;

[[nodiscard]] constexpr bool mode_built(std::string_view id) noexcept {
  if (id == "S1") {
    return true;
  }
  if (id == "S2" || id == "S3" || id == "S4") {
    return kHasSocketModes;
  }
  return is_mode(id) && kHasRingModes;
}

// Why a mode this build lacks is missing, for the record.
[[nodiscard]] constexpr std::string_view why_not_built(std::string_view id) noexcept {
  if (!is_mode(id)) {
    return "no such mode";
  }
  if (id == "S2" || id == "S3" || id == "S4") {
    return "Linux only: recvmmsg, MSG_WAITFORONE and SO_RXQ_OVFL";
  }
  return "io_uring: Linux with -DITCH_LIBURING_ROOT only";
}

// Calls visit(factory) for the mode named id, where factory(fd) makes the mode on the
// receiver's socket, and returns what visit returns; nullopt for a mode not built here.
// visit must return one type for every mode.
template <class Visit>
[[nodiscard]] auto with_mode(std::string_view id, const ModeOptions& options, Visit visit)
    -> std::optional<decltype(visit([](int fd) { return RecvfromMode{fd}; }))> {
  if (id == "S1") {
    return visit([](int fd) { return RecvfromMode{fd}; });
  }
#if ITCH_UDP_HAS_SOCKET_MODES
  if (id == "S2") {
    return visit([](int fd) { return RecvmsgMode{fd}; });
  }
  if (id == "S3") {
    return visit([&options](int fd) { return RecvmmsgWaitMode{fd, options}; });
  }
  if (id == "S4") {
    return visit([&options](int fd) { return RecvmmsgSpinMode{fd, options}; });
  }
#endif
#if ITCH_UDP_HAS_RING_MODES
  if (id == "U1") {
    return visit([&options](int fd) { return RecvSingleShotMode{fd, options}; });
  }
  if (id == "U2") {
    return visit([&options](int fd) { return RecvMultishotMode{fd, options}; });
  }
  if (id == "U3") {
    return visit([&options](int fd) { return RecvmsgMultishotMode{fd, options}; });
  }
  if (id == "U4") {
    return visit([&options](int fd) { return RecvMultishotCoopMode{fd, options}; });
  }
  if (id == "U5") {
    return visit([&options](int fd) { return RecvMultishotSqpollMode{fd, options}; });
  }
#endif
  static_cast<void>(options);
  return std::nullopt;
}

}  // namespace itch::bench::udp
