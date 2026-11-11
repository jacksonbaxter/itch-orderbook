// MoldUDP64 downstream packets, the framing of the UDP replay (T-URING-1, T-URING-2).
//
//     offset 0   Session           10 bytes, alphanumeric
//     offset 10  Sequence Number    8 bytes, big endian: the number of the first message
//     offset 18  Message Count      2 bytes, big endian
//     offset 20  Message Blocks: each a 2-byte big-endian length, then that many bytes
//
// A count of 0 is a heartbeat and 0xFFFF the end of the session. Neither carries a block,
// and each carries the next sequence number expected (IU-35 to IU-37). Sequence numbers
// count messages, not packets: the messages after a packet's first are numbered on from
// it. A message block is byte for byte a BinaryFILE frame (IU-36), so the blocks of a
// packet are walked by the file's own FrameReader, and every mode of the receiver hands
// the book exactly the frames the file path does.
//
// decode() checks the whole packet before anything reads a message: the header, then
// each block's length against the bytes left, block by block for exactly the count, and
// then that nothing follows the last. A packet that fails is refused whole and says why.
// No datagram can make it read outside its bytes or hand on a block it has not checked.
//
// SequenceTracker is the receiver's own view of loss (IU-43's last but one row): from the
// sequence numbers alone it counts the gaps, the messages they skipped and the messages
// that came again, and tells the caller which blocks of a packet are new. A message
// skipped by a gap stays lost, even if it turns up later: the book has moved on without
// it, and a replay that loses a message has failed whatever comes after. The kernel's
// and the socket's own counters are the caller's.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <span>
#include <string_view>

#include "itch/wire.hpp"

namespace itch::mold {

inline constexpr std::size_t kSessionBytes = 10;
inline constexpr std::size_t kSequenceOffset = 10;
inline constexpr std::size_t kCountOffset = 18;
inline constexpr std::size_t kHeaderBytes = 20;
inline constexpr std::size_t kBlockPrefix = 2;
inline constexpr std::uint16_t kHeartbeatCount = 0;
inline constexpr std::uint16_t kEndOfSessionCount = 0xFFFF;
// The most blocks a packet can say it holds: every count but the two above.
inline constexpr std::uint16_t kMostBlocks = kEndOfSessionCount - 1;

using Session = std::array<char, kSessionBytes>;

// A session name, left justified and padded with spaces as ITCH pads alpha fields. Only
// the first 10 characters are kept.
[[nodiscard]] constexpr Session make_session(std::string_view name) noexcept {
  Session session{};
  for (std::size_t i = 0; i < kSessionBytes; ++i) {
    session[i] = i < name.size() ? name[i] : ' ';
  }
  return session;
}

struct Header {
  Session session{};
  std::uint64_t sequence = 0;
  std::uint16_t count = 0;

  friend bool operator==(const Header&, const Header&) = default;
};

// Reads the 20 header bytes at src.
[[nodiscard]] inline Header read_header(const std::byte* src) noexcept {
  Header header;
  std::memcpy(header.session.data(), src, kSessionBytes);
  header.sequence = read_be<std::uint64_t>(src + kSequenceOffset);
  header.count = read_be<std::uint16_t>(src + kCountOffset);
  return header;
}

// Writes the 20 header bytes at dst.
inline void write_header(std::byte* dst, const Header& header) noexcept {
  std::memcpy(dst, header.session.data(), kSessionBytes);
  for (std::size_t i = 0; i < 8; ++i) {
    dst[kSequenceOffset + i] = static_cast<std::byte>(header.sequence >> (56U - (8U * i)));
  }
  dst[kCountOffset] = static_cast<std::byte>(header.count >> 8U);
  dst[kCountOffset + 1] = static_cast<std::byte>(header.count & 0xFFU);
}

enum class PacketStatus : std::uint8_t {
  // Well formed.
  messages,        // 1 to 65534 blocks, exactly as many as the count says.
  heartbeat,       // Count 0 and nothing after the header.
  end_of_session,  // Count 0xFFFF and nothing after the header.
  // Refused whole.
  short_header,     // Fewer than 20 bytes.
  truncated_block,  // A block the count asks for is not whole in the packet.
  trailing_bytes,   // Bytes after the count's last block, or after a heartbeat's header.
};
inline constexpr std::size_t kPacketStatuses = 6;

[[nodiscard]] constexpr bool is_well_formed(PacketStatus status) noexcept {
  return status <= PacketStatus::end_of_session;
}

// A stable name for logs and tools.
[[nodiscard]] constexpr std::string_view to_string(PacketStatus status) noexcept {
  switch (status) {
    case PacketStatus::messages: return "messages";
    case PacketStatus::heartbeat: return "heartbeat";
    case PacketStatus::end_of_session: return "end_of_session";
    case PacketStatus::short_header: return "short_header";
    case PacketStatus::truncated_block: return "truncated_block";
    case PacketStatus::trailing_bytes: return "trailing_bytes";
  }
  return "unknown";
}

struct Packet {
  PacketStatus status = PacketStatus::short_header;
  Header header;  // As read, whenever there were 20 bytes to read it from.
  // The blocks, exactly header.count of them and nothing else, when status is messages.
  // Empty otherwise.
  std::span<const std::byte> blocks;
};

[[nodiscard]] inline Packet decode(std::span<const std::byte> datagram) noexcept {
  Packet packet;
  if (datagram.size() < kHeaderBytes) {
    return packet;
  }
  packet.header = read_header(datagram.data());
  const std::size_t size = datagram.size();
  if (packet.header.count == kHeartbeatCount || packet.header.count == kEndOfSessionCount) {
    if (size != kHeaderBytes) {
      packet.status = PacketStatus::trailing_bytes;
    } else {
      packet.status = packet.header.count == kHeartbeatCount ? PacketStatus::heartbeat
                                                             : PacketStatus::end_of_session;
    }
    return packet;
  }
  std::size_t at = kHeaderBytes;
  for (std::uint16_t block = 0; block < packet.header.count; ++block) {
    if (size - at < kBlockPrefix) {
      packet.status = PacketStatus::truncated_block;
      return packet;
    }
    const std::size_t length = read_be<std::uint16_t>(datagram.data() + at);
    if (length > size - at - kBlockPrefix) {
      packet.status = PacketStatus::truncated_block;
      return packet;
    }
    at += kBlockPrefix + length;
  }
  if (at != size) {
    packet.status = PacketStatus::trailing_bytes;
    return packet;
  }
  packet.status = PacketStatus::messages;
  packet.blocks = datagram.subspan(kHeaderBytes);
  return packet;
}

struct SequenceCounts {
  std::uint64_t packets = 0;             // Well-formed packets of the session, before its end.
  std::uint64_t admitted = 0;            // Messages handed on, each once and in order.
  std::uint64_t gaps = 0;                // Times the sequence jumped ahead of the next expected.
  std::uint64_t lost = 0;                // Messages those jumps skipped.
  std::uint64_t duplicate_packets = 0;   // Packets with no message past the next expected.
  std::uint64_t duplicate_messages = 0;  // Messages at a sequence already passed.
  std::uint64_t heartbeats = 0;
  std::uint64_t ends = 0;             // End-of-session packets, the one that ended it included.
  std::uint64_t after_end = 0;        // Well-formed packets after the end, ignored.
  std::uint64_t foreign_session = 0;  // Well-formed packets of another session, ignored.
  std::uint64_t out_of_range = 0;     // Packets whose last sequence would pass 2^64 - 1.
};

// Which blocks of a packet are new: skip the first `skip`, then hand on the next `take`,
// which are the rest of the packet. Both are 0 for a packet with nothing new.
struct Admit {
  std::uint16_t skip = 0;
  std::uint16_t take = 0;
};

class SequenceTracker {
 public:
  explicit SequenceTracker(Session session, std::uint64_t first_sequence = 1) noexcept
      : session_{session}, next_{first_sequence} {}

  // For a well-formed packet's header. A heartbeat or an end of session admits nothing,
  // but a sequence ahead of the next expected is still a gap: that is how loss at the
  // tail of a run is seen.
  [[nodiscard]] Admit admit(const Header& header) noexcept {
    if (header.session != session_) {
      ++counts_.foreign_session;
      return {};
    }
    if (ended_) {
      ++counts_.after_end;
      return {};
    }
    ++counts_.packets;
    const bool control = header.count == kHeartbeatCount || header.count == kEndOfSessionCount;
    const std::uint64_t count = control ? 0 : header.count;
    if (count > std::numeric_limits<std::uint64_t>::max() - header.sequence) {
      ++counts_.out_of_range;
      return {};
    }
    if (header.sequence > next_) {
      ++counts_.gaps;
      counts_.lost += header.sequence - next_;
      next_ = header.sequence;
    }
    if (control) {
      if (header.count == kHeartbeatCount) {
        ++counts_.heartbeats;
      } else {
        ++counts_.ends;
        ended_ = true;
      }
      return {};
    }
    const std::uint64_t end = header.sequence + count;
    if (end <= next_) {
      ++counts_.duplicate_packets;
      counts_.duplicate_messages += count;
      return {};
    }
    const auto skip = static_cast<std::uint16_t>(next_ - header.sequence);
    counts_.duplicate_messages += skip;
    const auto take = static_cast<std::uint16_t>(count - skip);
    counts_.admitted += take;
    next_ = end;
    return {.skip = skip, .take = take};
  }

  [[nodiscard]] std::uint64_t next_expected() const noexcept { return next_; }
  [[nodiscard]] bool ended() const noexcept { return ended_; }
  [[nodiscard]] const Session& session() const noexcept { return session_; }
  [[nodiscard]] const SequenceCounts& counts() const noexcept { return counts_; }

 private:
  Session session_;
  std::uint64_t next_;
  bool ended_ = false;
  SequenceCounts counts_;
};

}  // namespace itch::mold
