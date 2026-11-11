// Arbitrary datagrams through the MoldUDP64 decoder and the feed.
//
// The input is a run of datagrams, each behind a 2-byte big-endian length, and whatever is
// left at the end, too short for its length, is one datagram more. A second decode of each
// datagram, written here without the decoder, has to agree with it on the status, and an
// accepted packet's blocks have to walk as exactly its count of frames. Each datagram then
// goes to one feed twice: the second time it must hand on nothing, and after each the
// sequence's books must balance, every step of the next expected sequence either admitted
// or lost.
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

#include "fuzz_check.hpp"
#include "itch/mold.hpp"
#include "itch/mold_feed.hpp"
#include "itch/reader.hpp"

namespace {

namespace mold = itch::mold;
using mold::PacketStatus;

constexpr std::uint64_t kFirstSequence = 1;

std::size_t be16(std::span<const std::byte> bytes, std::size_t at) {
  return (std::to_integer<std::size_t>(bytes[at]) << 8U) |
         std::to_integer<std::size_t>(bytes[at + 1]);
}

// The status of a datagram, walked from the spec without the decoder.
PacketStatus expected_status(std::span<const std::byte> datagram) {
  if (datagram.size() < 20) {
    return PacketStatus::short_header;
  }
  const std::size_t count = be16(datagram, 18);
  if (count == 0 || count == 0xFFFF) {
    if (datagram.size() != 20) {
      return PacketStatus::trailing_bytes;
    }
    return count == 0 ? PacketStatus::heartbeat : PacketStatus::end_of_session;
  }
  std::size_t at = 20;
  for (std::size_t block = 0; block < count; ++block) {
    if (datagram.size() - at < 2 || be16(datagram, at) > datagram.size() - at - 2) {
      return PacketStatus::truncated_block;
    }
    at += 2 + be16(datagram, at);
  }
  return at == datagram.size() ? PacketStatus::messages : PacketStatus::trailing_bytes;
}

void check_decode(std::span<const std::byte> datagram) {
  const mold::Packet packet = mold::decode(datagram);
  FUZZ_CHECK(packet.status == expected_status(datagram));
  if (datagram.size() >= mold::kHeaderBytes) {
    std::array<std::byte, mold::kHeaderBytes> again{};
    mold::write_header(again.data(), packet.header);
    FUZZ_CHECK(std::ranges::equal(again, datagram.first(mold::kHeaderBytes)));
  }
  if (packet.status != PacketStatus::messages) {
    FUZZ_CHECK(packet.blocks.empty());
    return;
  }
  FUZZ_CHECK(packet.blocks.data() == datagram.data() + mold::kHeaderBytes);
  FUZZ_CHECK(packet.blocks.size() == datagram.size() - mold::kHeaderBytes);
  itch::FrameReader reader{packet.blocks};
  for (std::uint16_t block = 0; block < packet.header.count; ++block) {
    FUZZ_CHECK(!itch::is_terminal(reader.next().status));
  }
  FUZZ_CHECK(reader.next().status == itch::FrameStatus::end_of_input);
}

struct CountFrames {
  std::uint64_t frames = 0;
  void operator()(const itch::Frame& frame) noexcept {
    FUZZ_CHECK(!itch::is_terminal(frame.status));
    ++frames;
  }
};

template <class Sink>
void check_books(const mold::FeedHandler<Sink>& feed, const CountFrames& sink) {
  const mold::FeedCounts& counts = feed.counts();
  const mold::SequenceCounts& sequence = feed.sequence().counts();
  std::uint64_t statuses = 0;
  for (const std::uint64_t count : counts.by_status) {
    statuses += count;
  }
  FUZZ_CHECK(statuses == counts.datagrams);
  FUZZ_CHECK(sink.frames == counts.frames);
  FUZZ_CHECK(counts.frames == sequence.admitted);
  FUZZ_CHECK(feed.sequence().next_expected() - kFirstSequence == sequence.admitted + sequence.lost);
  FUZZ_CHECK(feed.ended() == (sequence.ends != 0));
  FUZZ_CHECK(sequence.ends <= 1);
}

}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  const auto bytes = std::as_bytes(std::span{data, size});
  CountFrames sink;
  mold::FeedHandler feed{mold::make_session("SESSION001"), kFirstSequence, sink};
  std::size_t at = 0;
  while (at < size) {
    std::span<const std::byte> datagram = bytes.subspan(at);
    if (size - at >= 2 && be16(bytes, at) <= size - at - 2) {
      datagram = bytes.subspan(at + 2, be16(bytes, at));
      at += 2 + datagram.size();
    } else {
      at = size;
    }
    check_decode(datagram);
    feed.on_datagram(datagram);
    check_books(feed, sink);
    const std::uint64_t before = sink.frames;
    feed.on_datagram(datagram);
    FUZZ_CHECK(sink.frames == before);
    check_books(feed, sink);
  }
  return 0;
}
