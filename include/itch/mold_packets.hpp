// A day, or a slice of one, as MoldUDP64 packets built ahead of time, so that the replay
// sender does nothing in its loop but send (T-URING-1, T-URING-2).
//
// Each frame of the BinaryFILE input becomes one message block, copied byte for byte
// (IU-36), and takes the next sequence number. Frames the parser would skip, unknown
// types and wrong lengths, are copied too, so the receiver's FrameReader meets every
// frame the file's does. So the blocks of each packet are one contiguous run of the
// file, and the packets' blocks laid end to end are the input slice exactly.
//
// The packing, and why:
//
//   - Size cap: a whole datagram of at most 1,400 bytes, header included, by default.
//     Loopback's MTU is far above Ethernet's, so nothing below would split a large
//     datagram, and the replay has to keep to a size a real feed could send (IU-47).
//     1,400 bytes fits a 1,500-byte Ethernet frame with its IP and UDP headers and room
//     to spare, and it is the size IU-44 translates Nasdaq's peaks at: about 41 to 55
//     messages a packet, 23,658 packets/s at the 1 s peak and 62,876 at the 1 ms peak.
//     At that size a 212,992-byte rmem_max, doubled by the kernel, holds about 2.4 to
//     4.8 ms of the 1 ms peak (IU-45), so a receiver that stalls longer loses data.
//     T-URING-2's bisection also names 64-byte payloads (the comparison plan's task 3).
//     The largest ITCH message, Net Order Imbalance at 50 bytes, needs 72 with the header
//     and its prefix, so kSmallestDayCap, 72, is the least a whole day packs under, where
//     a packet holds one or two messages. The cap is a plan field for that run.
//   - Messages a packet: as many as fit under the cap. Real packet density is not
//     published (IU-38, DS-80), so the cap is the one knob, and a count limit is there
//     only to force small packets in a test. 65534 is the most the count field allows.
//   - Time gap: off by default. A packet closes early when the next message's ITCH
//     timestamp is more than flush_gap_ns after its first, which is how a feed-shaped
//     replay would keep a quiet spell from holding a message back. The rate-paced runs
//     want packets of a fixed size, so they leave it off (IU-46's first metric).
//   - Heartbeats: one, wherever consecutive messages are at least heartbeat_gap_ns
//     apart in ITCH time, 1 s by default, as MoldUDP64 sends one a second when idle
//     (IU-37). One per gap, whatever its length, since a paced replay has no idle time
//     for more. Then a heartbeat and the end of session close the array, each with the
//     next sequence number, so that a receiver sees loss at the very tail.
//
// build_packets() walks the input twice, once to size the array and once to fill it, so
// that it allocates once, exactly, and the bytes are written, and so faulted in, before
// any timed loop starts.
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <expected>
#include <limits>
#include <span>
#include <string_view>
#include <vector>

#include "itch/messages.hpp"
#include "itch/mold.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"

namespace itch::mold {

inline constexpr std::size_t kDefaultDatagram = 1400;
// The largest UDP payload over IPv4: 65,535 less the IP and UDP headers.
inline constexpr std::size_t kMostDatagram = 65507;
inline constexpr std::uint64_t kDefaultHeartbeatGapNs = 1'000'000'000;
// The least cap under which every ITCH 5.0 message fits a packet alone.
inline constexpr std::size_t kSmallestDayCap = [] {
  std::size_t largest = 0;
  for (const MessageSpec& spec : kMessageSpecs) {
    largest = spec.length > largest ? spec.length : largest;
  }
  return kHeaderBytes + kBlockPrefix + largest;
}();
static_assert(kSmallestDayCap == 72);

struct PacketPlan {
  Session session = make_session("REPLAY0001");
  std::uint64_t first_sequence = 1;
  std::size_t max_datagram = kDefaultDatagram;  // Bytes, header included.
  std::uint16_t max_messages = kMostBlocks;
  std::uint64_t first_frame = 0;  // Frames of the input to leave out before the slice.
  std::uint64_t frame_limit = std::numeric_limits<std::uint64_t>::max();
  std::uint64_t flush_gap_ns = 0;                           // 0 leaves it off.
  std::uint64_t heartbeat_gap_ns = kDefaultHeartbeatGapNs;  // 0 leaves them out.
};

enum class BuildError : std::uint8_t {
  bad_plan,         // A cap below one block, a count outside 1 to 65534, or no sequence room.
  oversized_frame,  // A frame that cannot fit a datagram under the cap.
  truncated_input,  // The input ends inside a frame.
};

[[nodiscard]] constexpr std::string_view to_string(BuildError error) noexcept {
  switch (error) {
    case BuildError::bad_plan: return "bad_plan";
    case BuildError::oversized_frame: return "oversized_frame";
    case BuildError::truncated_input: return "truncated_input";
  }
  return "unknown";
}

struct BuildFailure {
  BuildError error = BuildError::bad_plan;
  std::size_t offset = 0;  // Where in the input the frame that failed starts.
};

struct PacketRef {
  std::uint64_t offset = 0;        // Into PacketArray::bytes().
  std::uint64_t timestamp_ns = 0;  // ITCH time of the first message, or the last before.
  std::uint16_t size = 0;          // Datagram bytes, header included.
  std::uint16_t messages = 0;      // Blocks. 0 for a heartbeat and the end of session.
};

class PacketArray {
 public:
  [[nodiscard]] std::size_t size() const noexcept { return refs_.size(); }
  [[nodiscard]] const PacketRef& ref(std::size_t i) const noexcept { return refs_[i]; }
  [[nodiscard]] std::span<const std::byte> packet(std::size_t i) const noexcept {
    return std::span<const std::byte>{bytes_}.subspan(refs_[i].offset, refs_[i].size);
  }
  [[nodiscard]] std::span<const std::byte> bytes() const noexcept { return bytes_; }
  [[nodiscard]] std::span<const PacketRef> refs() const noexcept { return refs_; }
  [[nodiscard]] const PacketPlan& plan() const noexcept { return plan_; }
  [[nodiscard]] std::uint64_t messages() const noexcept { return messages_; }
  [[nodiscard]] std::uint64_t heartbeats() const noexcept { return heartbeats_; }
  // The sequence the closing heartbeat and end of session carry: one past the last message.
  [[nodiscard]] std::uint64_t next_sequence() const noexcept {
    return plan_.first_sequence + messages_;
  }

 private:
  friend std::expected<PacketArray, BuildFailure> build_packets(std::span<const std::byte>,
                                                                const PacketPlan&);
  PacketPlan plan_;
  std::vector<std::byte> bytes_;
  std::vector<PacketRef> refs_;
  std::uint64_t messages_ = 0;
  std::uint64_t heartbeats_ = 0;
};

namespace detail {

// The bytes from 5 of a known message: every ITCH type has its timestamp there.
inline constexpr std::size_t kTimestampOffset = 5;
inline constexpr std::size_t kTimestampEnd = kTimestampOffset + 6;

// One walk of the slice, calling emit(header, blocks, timestamp_ns) for each packet in
// order, blocks empty for a heartbeat and the end of session.
template <class Emit>
[[nodiscard]] std::expected<void, BuildFailure> pack(std::span<const std::byte> input,
                                                     const PacketPlan& plan, const Emit& emit) {
  FrameReader reader{input};
  bool slice_reached = true;
  for (std::uint64_t skipped = 0; skipped < plan.first_frame; ++skipped) {
    const Frame frame = reader.next();
    if (frame.status == FrameStatus::end_of_input) {
      slice_reached = false;
      break;
    }
    if (is_terminal(frame.status)) {
      return std::unexpected(
          BuildFailure{.error = BuildError::truncated_input, .offset = frame.offset});
    }
  }

  std::uint64_t next = plan.first_sequence;
  std::uint64_t last_ts = 0;
  bool have_last_ts = false;
  bool open = false;
  std::size_t open_start = 0;
  std::size_t open_bytes = 0;
  std::uint16_t open_count = 0;
  std::uint64_t open_sequence = 0;
  std::uint64_t open_ts = 0;
  bool have_open_ts = false;
  const auto close = [&] {
    if (open) {
      emit(Header{.session = plan.session, .sequence = open_sequence, .count = open_count},
           input.subspan(open_start, open_bytes), open_ts);
      open = false;
    }
  };
  const auto control = [&](std::uint16_t count) {
    emit(Header{.session = plan.session, .sequence = next, .count = count},
         std::span<const std::byte>{}, last_ts);
  };

  for (std::uint64_t taken = 0; slice_reached && taken < plan.frame_limit; ++taken) {
    const Frame frame = reader.next();
    if (is_terminal(frame.status)) {
      if (frame.status != FrameStatus::end_of_input) {
        return std::unexpected(
            BuildFailure{.error = BuildError::truncated_input, .offset = frame.offset});
      }
      break;
    }
    const std::size_t block = kBlockPrefix + frame.payload.size();
    if (kHeaderBytes + block > plan.max_datagram) {
      return std::unexpected(
          BuildFailure{.error = BuildError::oversized_frame, .offset = frame.offset});
    }
    const bool has_ts =
        frame.status == FrameStatus::message && frame.payload.size() >= kTimestampEnd;
    const std::uint64_t ts = has_ts ? read_be48(frame.payload.data() + kTimestampOffset) : 0;
    if (has_ts && have_last_ts && plan.heartbeat_gap_ns != 0 && ts > last_ts &&
        ts - last_ts >= plan.heartbeat_gap_ns) {
      close();
      control(kHeartbeatCount);
    }
    if (open &&
        (kHeaderBytes + open_bytes + block > plan.max_datagram || open_count == plan.max_messages ||
         (plan.flush_gap_ns != 0 && has_ts && have_open_ts && ts > open_ts &&
          ts - open_ts > plan.flush_gap_ns))) {
      close();
    }
    if (!open) {
      open = true;
      open_start = frame.offset;
      open_bytes = 0;
      open_count = 0;
      open_sequence = next;
      open_ts = last_ts;
      have_open_ts = false;
    }
    open_bytes += block;
    ++open_count;
    ++next;
    if (has_ts) {
      last_ts = ts;
      have_last_ts = true;
      if (!have_open_ts) {
        open_ts = ts;
        have_open_ts = true;
      }
    }
  }
  close();
  control(kHeartbeatCount);
  control(kEndOfSessionCount);
  return {};
}

}  // namespace detail

[[nodiscard]] inline std::expected<PacketArray, BuildFailure> build_packets(
    std::span<const std::byte> input, const PacketPlan& plan) {
  if (plan.max_datagram < kHeaderBytes + kBlockPrefix || plan.max_datagram > kMostDatagram ||
      plan.max_messages == 0 || plan.max_messages > kMostBlocks ||
      plan.first_sequence > std::numeric_limits<std::uint64_t>::max() - input.size()) {
    return std::unexpected(BuildFailure{.error = BuildError::bad_plan, .offset = 0});
  }
  std::size_t packets = 0;
  std::size_t bytes = 0;
  const auto sized = detail::pack(
      input, plan, [&](const Header&, std::span<const std::byte> blocks, std::uint64_t) {
        ++packets;
        bytes += kHeaderBytes + blocks.size();
      });
  if (!sized) {
    return std::unexpected(sized.error());
  }

  PacketArray array;
  array.plan_ = plan;
  array.bytes_.resize(bytes);
  array.refs_.reserve(packets);
  std::size_t at = 0;
  const auto filled = detail::pack(
      input, plan, [&](const Header& header, std::span<const std::byte> blocks, std::uint64_t ts) {
        write_header(array.bytes_.data() + at, header);
        if (!blocks.empty()) {
          std::memcpy(array.bytes_.data() + at + kHeaderBytes, blocks.data(), blocks.size());
        }
        const std::size_t size = kHeaderBytes + blocks.size();
        const bool control = header.count == kHeartbeatCount || header.count == kEndOfSessionCount;
        array.refs_.push_back({.offset = at,
                               .timestamp_ns = ts,
                               .size = static_cast<std::uint16_t>(size),
                               .messages = control ? std::uint16_t{0} : header.count});
        array.messages_ += control ? 0U : header.count;
        array.heartbeats_ += header.count == kHeartbeatCount ? 1U : 0U;
        at += size;
      });
  if (!filled) {
    return std::unexpected(filled.error());
  }
  return array;
}

}  // namespace itch::mold
