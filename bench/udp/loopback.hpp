// The sender and a receive mode on one loopback pair, each on its own thread: the shape
// every T-URING run takes. run_loopback() takes any ReceiveMode, made by the caller's
// factory from the receiver's socket; run_loopback_s1() is it with S1, the mode every
// platform has.
//
// Everything that allocates is done before either thread starts its loop: the sockets,
// the mode and its buffers, the feed and each pin, whose record is a string. Each thread then
// says it is ready, and on_ready() runs on the caller's thread before the sender is let
// go, so that a test can open its allocation window there with both loops inside it.
//
// On loopback the receive softirq runs on the sender's CPU (IU-39, IU-47). This measures
// sockets and copies, not a network, and on the Mac it measures nothing at all: the Mac
// runs it as a functional check only.
#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <expected>
#include <thread>
#include <type_traits>
#include <utility>

#include "itch/mold_feed.hpp"
#include "itch/mold_packets.hpp"
#include "udp/receive_mode.hpp"
#include "udp/sender.hpp"
#include "udp/socket.hpp"

namespace itch::bench::udp {

struct LoopbackConfig {
  SendPlan send;
  int sender_core = -1;  // -1 asks for no pin. Only Linux can grant one.
  int receiver_core = -1;
  int receive_buffer = 4 * 1024 * 1024;  // Asked for; what the kernel gave is read back.
  std::chrono::milliseconds idle_timeout{2000};
};

struct LoopbackRun {
  Endpoint receiver_at;
  Endpoint sender_at;
  BufferSize receive_buffer;
  SenderRun sender;
  PinOutcome receiver_pin;
  ReceiveOutcome received;
  ModeCounters mode;
  mold::FeedCounts feed;
  mold::SequenceCounts sequence;
  std::uint64_t next_expected = 0;
};

// The sender's socket as it is.
struct PlainSocket {
  [[nodiscard]] ConnectedSocket operator()(ConnectedSocket socket) const noexcept { return socket; }
};

// A receive mode against the sender over a fresh pair, every frame the feed admits handed
// to sink. make_mode(fd) builds the mode on the receiver's socket, which already has its
// buffer size and timeout. wrap() may put the sender's socket inside another
// DatagramSocket, which is how a negative control loses a packet on the way.
template <mold::FrameSink Sink, class MakeMode, class OnReady, class Wrap = PlainSocket>
  requires ReceiveMode<std::invoke_result_t<MakeMode&, int>>
[[nodiscard]] std::expected<LoopbackRun, SocketError> run_loopback(const mold::PacketArray& packets,
                                                                   const LoopbackConfig& config,
                                                                   MakeMode make_mode, Sink& sink,
                                                                   OnReady on_ready,
                                                                   Wrap wrap = {}) {
  auto pair = open_loopback_pair();
  if (!pair) {
    return std::unexpected(pair.error());
  }
  LoopbackRun run;
  run.receiver_at = pair->receiver_at;
  run.sender_at = pair->sender_at;
  auto buffer = set_receive_buffer(pair->receiver.get(), config.receive_buffer);
  if (!buffer) {
    return std::unexpected(buffer.error());
  }
  run.receive_buffer = *buffer;
  if (auto timeout = set_receive_timeout(pair->receiver.get(), config.idle_timeout); !timeout) {
    return std::unexpected(timeout.error());
  }

  auto mode = make_mode(pair->receiver.get());
  if (const ModeCounters& made = mode.counters(); made.setup_error != 0) {
    return std::unexpected(SocketError{.call = made.setup_call, .error = made.setup_error});
  }
  mold::FeedHandler<Sink> feed{packets.plan().session, packets.plan().first_sequence, sink};
  auto socket = wrap(ConnectedSocket{pair->sender.get()});
  SteadyClock clock;
  std::array<Datagram, kMostBatch> batch{};
  std::atomic<int> ready{0};
  std::atomic<bool> go{false};

  std::thread receiver{[&] {
    run.receiver_pin = pin_thread(config.receiver_core);
    ready.fetch_add(1, std::memory_order_release);
    run.received = receive_until_end(mode, feed, batch);
  }};
  std::thread sender{[&] {
    run.sender.pin = pin_thread(config.sender_core);
    ready.fetch_add(1, std::memory_order_release);
    while (!go.load(std::memory_order_acquire)) {
      std::this_thread::yield();
    }
    run.sender.report = send_packets(packets, config.send, clock, socket);
  }};
  while (ready.load(std::memory_order_acquire) < 2) {
    std::this_thread::yield();
  }
  on_ready();
  go.store(true, std::memory_order_release);
  sender.join();
  receiver.join();

  run.mode = mode.counters();
  run.feed = feed.counts();
  run.sequence = feed.sequence().counts();
  run.next_expected = feed.sequence().next_expected();
  return run;
}

// S1, blocking recvfrom(), against the sender.
template <mold::FrameSink Sink, class OnReady, class Wrap = PlainSocket>
[[nodiscard]] std::expected<LoopbackRun, SocketError> run_loopback_s1(
    const mold::PacketArray& packets, const LoopbackConfig& config, Sink& sink, OnReady on_ready,
    Wrap wrap = {}) {
  return run_loopback(
      packets, config, [](int fd) { return RecvfromMode{fd}; }, sink, std::move(on_ready),
      std::move(wrap));
}

}  // namespace itch::bench::udp
