// The UDP sockets of the replay: one pair on 127.0.0.1, each side bound to a port the
// kernel picks and connect()ed to the other (the comparison plan's item 3, since Brouer
// shows a connected socket changes the result, IU-2 and IU-4). Plain BSD sockets, the
// same calls on macOS and Linux.
//
// Addresses come from getaddrinfo() and getsockname() writes the bound port back into the
// same storage, so no code here casts between sockaddr types. Setup may allocate; send()
// and the receive modes' calls do not.
#pragma once

#include <netdb.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <memory>
#include <span>
#include <string>
#include <string_view>
#include <utility>

namespace itch::bench::udp {

struct SocketError {
  std::string_view call;  // The call that failed.
  int error = 0;          // Its errno, or getaddrinfo's code for that call.
};

// Owns one file descriptor.
class Fd {
 public:
  Fd() noexcept = default;
  explicit Fd(int fd) noexcept : fd_{fd} {}
  Fd(const Fd&) = delete;
  Fd& operator=(const Fd&) = delete;
  Fd(Fd&& other) noexcept : fd_{std::exchange(other.fd_, -1)} {}
  Fd& operator=(Fd&& other) noexcept {
    if (this != &other) {
      reset();
      fd_ = std::exchange(other.fd_, -1);
    }
    return *this;
  }
  ~Fd() { reset(); }

  [[nodiscard]] int get() const noexcept { return fd_; }
  void reset() noexcept {
    if (fd_ >= 0) {
      ::close(fd_);
      fd_ = -1;
    }
  }

 private:
  int fd_ = -1;
};

// A socket's own address, as numbers, for the record.
struct Endpoint {
  std::string host;
  std::string port;
};

struct LoopbackPair {
  Fd receiver;
  Fd sender;
  Endpoint receiver_at;
  Endpoint sender_at;
};

namespace detail {

struct FreeAddrinfo {
  void operator()(addrinfo* info) const noexcept { ::freeaddrinfo(info); }
};
using Addrinfo = std::unique_ptr<addrinfo, FreeAddrinfo>;

// A UDP socket bound to 127.0.0.1 on a port the kernel picks, and its address with that
// port, in storage connect() can take.
struct Bound {
  Fd fd;
  Addrinfo address;
  socklen_t length = 0;
};

[[nodiscard]] inline std::expected<Bound, SocketError> bind_loopback() {
  addrinfo hints{};
  hints.ai_family = AF_INET;
  hints.ai_socktype = SOCK_DGRAM;
  hints.ai_protocol = IPPROTO_UDP;
  hints.ai_flags = AI_NUMERICHOST | AI_NUMERICSERV;
  addrinfo* found = nullptr;
  const int code = ::getaddrinfo("127.0.0.1", "0", &hints, &found);
  if (code != 0) {
    return std::unexpected(SocketError{.call = "getaddrinfo", .error = code});
  }
  Bound bound{.fd = Fd{}, .address = Addrinfo{found}, .length = found->ai_addrlen};
  bound.fd = Fd{::socket(found->ai_family, found->ai_socktype, found->ai_protocol)};
  if (bound.fd.get() < 0) {
    return std::unexpected(SocketError{.call = "socket", .error = errno});
  }
  if (::bind(bound.fd.get(), found->ai_addr, found->ai_addrlen) != 0) {
    return std::unexpected(SocketError{.call = "bind", .error = errno});
  }
  if (::getsockname(bound.fd.get(), found->ai_addr, &bound.length) != 0) {
    return std::unexpected(SocketError{.call = "getsockname", .error = errno});
  }
  return bound;
}

[[nodiscard]] inline Endpoint endpoint_of(const Bound& bound) {
  std::array<char, NI_MAXHOST> host{};
  std::array<char, NI_MAXSERV> port{};
  if (::getnameinfo(bound.address->ai_addr, bound.length, host.data(), host.size(), port.data(),
                    port.size(), NI_NUMERICHOST | NI_NUMERICSERV) != 0) {
    return {.host = "unknown", .port = "unknown"};
  }
  return {.host = host.data(), .port = port.data()};
}

}  // namespace detail

// Two sockets on 127.0.0.1, each connect()ed to the other.
[[nodiscard]] inline std::expected<LoopbackPair, SocketError> open_loopback_pair() {
  auto receiver = detail::bind_loopback();
  if (!receiver) {
    return std::unexpected(receiver.error());
  }
  auto sender = detail::bind_loopback();
  if (!sender) {
    return std::unexpected(sender.error());
  }
  if (::connect(sender->fd.get(), receiver->address->ai_addr, receiver->length) != 0) {
    return std::unexpected(SocketError{.call = "connect sender", .error = errno});
  }
  if (::connect(receiver->fd.get(), sender->address->ai_addr, sender->length) != 0) {
    return std::unexpected(SocketError{.call = "connect receiver", .error = errno});
  }
  LoopbackPair pair{.receiver = std::move(receiver->fd),
                    .sender = std::move(sender->fd),
                    .receiver_at = detail::endpoint_of(*receiver),
                    .sender_at = detail::endpoint_of(*sender)};
  return pair;
}

// SO_RCVBUF as asked and as the kernel then reports it. Linux caps the request at
// rmem_max and doubles it for bookkeeping (IU-42), so the two differ and both go on the
// record.
struct BufferSize {
  int requested = 0;
  int read_back = 0;
};

[[nodiscard]] inline std::expected<BufferSize, SocketError> set_receive_buffer(int fd, int bytes) {
  if (::setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &bytes, sizeof(bytes)) != 0) {
    return std::unexpected(SocketError{.call = "setsockopt SO_RCVBUF", .error = errno});
  }
  int read_back = 0;
  socklen_t length = sizeof(read_back);
  if (::getsockopt(fd, SOL_SOCKET, SO_RCVBUF, &read_back, &length) != 0) {
    return std::unexpected(SocketError{.call = "getsockopt SO_RCVBUF", .error = errno});
  }
  return BufferSize{.requested = bytes, .read_back = read_back};
}

// A blocking receive returns EAGAIN after this long with nothing to read, so that a
// receiver whose end of session was lost still stops.
[[nodiscard]] inline std::expected<void, SocketError> set_receive_timeout(
    int fd, std::chrono::milliseconds timeout) {
  timeval value{};
  value.tv_sec = static_cast<decltype(value.tv_sec)>(timeout.count() / 1000);
  value.tv_usec = static_cast<decltype(value.tv_usec)>((timeout.count() % 1000) * 1000);
  if (::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &value, sizeof(value)) != 0) {
    return std::unexpected(SocketError{.call = "setsockopt SO_RCVTIMEO", .error = errno});
  }
  return {};
}

// The result of one send. error is the errno of a failed send, 0 otherwise.
struct SendResult {
  bool sent = false;
  int error = 0;
};

// The sender's side of a connected pair: send() is one send(2) of one datagram.
class ConnectedSocket {
 public:
  explicit ConnectedSocket(int fd) noexcept : fd_{fd} {}

  [[nodiscard]] SendResult send(std::span<const std::byte> datagram) const noexcept {
    for (;;) {
      const ::ssize_t sent = ::send(fd_, datagram.data(), datagram.size(), 0);
      if (sent >= 0) {
        return {.sent = std::cmp_equal(sent, datagram.size()), .error = 0};
      }
      if (errno != EINTR) {
        return {.sent = false, .error = errno};
      }
    }
  }

 private:
  int fd_;
};

}  // namespace itch::bench::udp
