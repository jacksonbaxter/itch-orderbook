// The receive side's work on each datagram, the same in every receive mode: decode the
// MoldUDP64 framing, check the sequence, then walk the new blocks with the file's
// FrameReader and hand each frame to the sink, which is where the book is.
//
// A mode (bench/udp/receive_mode.hpp) only fills buffers with datagrams. Everything after
// that is here, so S1 to S4 and U1 to U5 differ in how a datagram arrives and in nothing
// it leads to. Nothing here allocates or reads a clock.
#pragma once

#include <array>
#include <concepts>
#include <cstddef>
#include <cstdint>
#include <span>
#include <utility>

#include "itch/mold.hpp"
#include "itch/reader.hpp"

namespace itch::mold {

// Takes each new frame of the feed, in sequence order, as the FrameReader gives it.
template <class Sink>
concept FrameSink = requires(Sink& sink, const Frame& frame) {
  { sink(frame) } -> std::same_as<void>;
};

struct FeedCounts {
  std::uint64_t datagrams = 0;
  std::uint64_t bytes = 0;
  std::array<std::uint64_t, kPacketStatuses> by_status{};  // Indexed by PacketStatus.
  std::uint64_t frames = 0;                                // Frames handed to the sink.

  [[nodiscard]] std::uint64_t of(PacketStatus status) const noexcept {
    return by_status[static_cast<std::size_t>(status)];
  }
  [[nodiscard]] std::uint64_t malformed() const noexcept {
    return of(PacketStatus::short_header) + of(PacketStatus::truncated_block) +
           of(PacketStatus::trailing_bytes);
  }
};

template <FrameSink Sink>
class FeedHandler {
 public:
  FeedHandler(Session session, std::uint64_t first_sequence, Sink& sink) noexcept
      : sequence_{session, first_sequence}, sink_{&sink} {}

  void on_datagram(std::span<const std::byte> datagram) noexcept(
      noexcept(std::declval<Sink&>()(std::declval<const Frame&>()))) {
    ++counts_.datagrams;
    counts_.bytes += datagram.size();
    const Packet packet = decode(datagram);
    ++counts_.by_status[static_cast<std::size_t>(packet.status)];
    if (!is_well_formed(packet.status)) {
      return;
    }
    const Admit admit = sequence_.admit(packet.header);
    if (admit.take == 0) {
      return;
    }
    // decode() has checked that the blocks are exactly the count's, each whole, so the
    // walk below meets no terminal state before the last.
    FrameReader reader{packet.blocks};
    for (std::uint16_t skipped = 0; skipped < admit.skip; ++skipped) {
      static_cast<void>(reader.next());
    }
    for (std::uint16_t taken = 0; taken < admit.take; ++taken) {
      (*sink_)(reader.next());
    }
    counts_.frames += admit.take;
  }

  [[nodiscard]] bool ended() const noexcept { return sequence_.ended(); }
  [[nodiscard]] const SequenceTracker& sequence() const noexcept { return sequence_; }
  [[nodiscard]] const FeedCounts& counts() const noexcept { return counts_; }

 private:
  SequenceTracker sequence_;
  Sink* sink_;
  FeedCounts counts_;
};

}  // namespace itch::mold
