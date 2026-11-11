// MoldUDP64: the header against hand-written bytes, every kind of packet the decoder
// accepts or refuses, the sequence tracker's view of loss, the prebuilt packet array
// against the file's framing byte for byte, and the feed handler between them.
#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <initializer_list>
#include <iterator>
#include <span>
#include <string>
#include <vector>

#include "itch/book.hpp"
#include "itch/mold.hpp"
#include "itch/mold_feed.hpp"
#include "itch/mold_packets.hpp"
#include "itch/reader.hpp"
#include "support/churn.hpp"
#include "support/fixtures.hpp"

namespace {

namespace mold = itch::mold;
using itch::fixtures::append_frame;
using itch::fixtures::Bytes;
using itch::fixtures::to_bytes;
using mold::PacketStatus;

const mold::Session kSession = mold::make_session("SESSION001");

Bytes header_bytes(const mold::Header& header) {
  Bytes out(mold::kHeaderBytes);
  mold::write_header(out.data(), header);
  return out;
}

// A packet of the given messages, its count as given, the blocks framed as BinaryFILE.
Bytes packet_of(std::uint64_t sequence, std::uint16_t count, std::initializer_list<Bytes> blocks,
                mold::Session session = kSession) {
  Bytes out = header_bytes({.session = session, .sequence = sequence, .count = count});
  for (const Bytes& block : blocks) {
    append_frame(out, block);
  }
  return out;
}

Bytes packet_of(std::uint64_t sequence, std::initializer_list<Bytes> blocks) {
  return packet_of(sequence, static_cast<std::uint16_t>(blocks.size()), blocks);
}

const Bytes kSystem = to_bytes(itch::fixtures::kSystemEvent);
const Bytes kAdd = to_bytes(itch::fixtures::kAddOrder);
const Bytes kTrade = to_bytes(itch::fixtures::kTrade);

std::vector<std::pair<std::string, Bytes>> fixture_files() {
  std::vector<std::pair<std::string, Bytes>> files;
  for (const char* dir : {ITCH_ORACLE_FIXTURES, ITCH_TRADE_FIXTURES}) {
    for (const auto& entry : std::filesystem::directory_iterator{dir}) {
      if (entry.path().extension() != ".itch") {
        continue;
      }
      std::ifstream in{entry.path(), std::ios::binary};
      const std::vector<char> text{std::istreambuf_iterator<char>{in}, {}};
      Bytes bytes(text.size());
      std::memcpy(bytes.data(), text.data(), text.size());
      files.emplace_back(entry.path().filename().string(), std::move(bytes));
    }
  }
  std::ranges::sort(files);
  return files;
}

// --- The header -----------------------------------------------------------------------

TEST(MoldHeader, LaysOutSessionSequenceAndCountAsTheSpecSays) {
  const Bytes bytes = header_bytes({.session = mold::make_session("ABCDEFGHIJ"),
                                    .sequence = 0x0102030405060708ULL,
                                    .count = 0x090A});
  const std::array<std::uint8_t, 20> expected{
      'A',  'B',  'C',  'D',  'E',  'F',  'G',  'H',  'I', 'J',  // Session, offset 0
      0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,            // Sequence, offset 10
      0x09, 0x0A,                                                // Count, offset 18
  };
  EXPECT_EQ(bytes, to_bytes(expected));
}

TEST(MoldHeader, ReadsWhatItWrites) {
  const mold::Header header{
      .session = kSession, .sequence = 0xFEDCBA9876543210ULL, .count = 0x1234};
  const Bytes bytes = header_bytes(header);
  EXPECT_EQ(mold::read_header(bytes.data()), header);
}

TEST(MoldHeader, PadsASessionNameWithSpaces) {
  const mold::Session session = mold::make_session("AB");
  EXPECT_EQ(std::string(session.begin(), session.end()), "AB        ");
  const mold::Session cut = mold::make_session("ABCDEFGHIJKL");
  EXPECT_EQ(std::string(cut.begin(), cut.end()), "ABCDEFGHIJ");
}

// --- Decoding -------------------------------------------------------------------------

TEST(MoldDecode, APacketOfMessagesHandsOnExactlyItsBlocks) {
  const Bytes bytes = packet_of(7, {kSystem, kAdd, kTrade});
  const mold::Packet packet = mold::decode(bytes);
  ASSERT_EQ(packet.status, PacketStatus::messages);
  EXPECT_EQ(packet.header.sequence, 7U);
  EXPECT_EQ(packet.header.count, 3U);
  EXPECT_EQ(packet.blocks.data(), bytes.data() + mold::kHeaderBytes);
  EXPECT_EQ(packet.blocks.size(), bytes.size() - mold::kHeaderBytes);
  itch::FrameReader reader{packet.blocks};
  for (const Bytes& want : {kSystem, kAdd, kTrade}) {
    const itch::Frame frame = reader.next();
    ASSERT_EQ(frame.status, itch::FrameStatus::message);
    EXPECT_TRUE(std::ranges::equal(frame.payload, want));
  }
  EXPECT_EQ(reader.next().status, itch::FrameStatus::end_of_input);
}

TEST(MoldDecode, HeartbeatAndEndOfSessionCarryNoBlocks) {
  const Bytes heartbeat = packet_of(9, mold::kHeartbeatCount, {});
  const Bytes end = packet_of(9, mold::kEndOfSessionCount, {});
  EXPECT_EQ(mold::decode(heartbeat).status, PacketStatus::heartbeat);
  EXPECT_EQ(mold::decode(end).status, PacketStatus::end_of_session);
  EXPECT_TRUE(mold::decode(heartbeat).blocks.empty());
  EXPECT_EQ(mold::decode(end).header.sequence, 9U);
}

TEST(MoldDecode, AShortHeaderIsRefused) {
  const Bytes bytes = packet_of(1, {kSystem});
  for (std::size_t size = 0; size < mold::kHeaderBytes; ++size) {
    EXPECT_EQ(mold::decode(std::span{bytes}.first(size)).status, PacketStatus::short_header)
        << size;
  }
}

TEST(MoldDecode, ACountPastTheBlocksIsATruncatedBlock) {
  const Bytes bytes = packet_of(1, 3, {kSystem, kAdd});
  const mold::Packet packet = mold::decode(bytes);
  EXPECT_EQ(packet.status, PacketStatus::truncated_block);
  EXPECT_TRUE(packet.blocks.empty());
}

TEST(MoldDecode, ABlockLengthPastTheEndIsATruncatedBlock) {
  Bytes bytes = packet_of(1, {kSystem, kAdd});
  bytes.pop_back();
  EXPECT_EQ(mold::decode(bytes).status, PacketStatus::truncated_block);
  // One byte of a length prefix where a second block should be.
  Bytes one = packet_of(1, 2, {kSystem});
  one.push_back(std::byte{0});
  EXPECT_EQ(mold::decode(one).status, PacketStatus::truncated_block);
}

TEST(MoldDecode, BytesAfterTheCountsLastBlockAreRefused) {
  const Bytes bytes = packet_of(1, 1, {kSystem, kAdd});
  const mold::Packet packet = mold::decode(bytes);
  EXPECT_EQ(packet.status, PacketStatus::trailing_bytes);
  EXPECT_TRUE(packet.blocks.empty());
  Bytes stray = packet_of(1, {kSystem});
  stray.push_back(std::byte{0x41});
  EXPECT_EQ(mold::decode(stray).status, PacketStatus::trailing_bytes);
}

TEST(MoldDecode, AHeartbeatOrEndOfSessionWithBytesAfterIsRefused) {
  EXPECT_EQ(mold::decode(packet_of(1, mold::kHeartbeatCount, {kSystem})).status,
            PacketStatus::trailing_bytes);
  EXPECT_EQ(mold::decode(packet_of(1, mold::kEndOfSessionCount, {kSystem})).status,
            PacketStatus::trailing_bytes);
}

TEST(MoldDecode, ZeroLengthBlocksAreBlocks) {
  const Bytes bytes = packet_of(1, {Bytes{}, kSystem, Bytes{}});
  const mold::Packet packet = mold::decode(bytes);
  ASSERT_EQ(packet.status, PacketStatus::messages);
  itch::FrameReader reader{packet.blocks};
  EXPECT_EQ(reader.next().status, itch::FrameStatus::zero_length);
  EXPECT_EQ(reader.next().status, itch::FrameStatus::message);
  EXPECT_EQ(reader.next().status, itch::FrameStatus::zero_length);
  EXPECT_EQ(reader.next().status, itch::FrameStatus::end_of_input);
}

// A good packet cut at every byte is refused at every cut but the whole.
TEST(MoldDecode, EveryCutOfAGoodPacketIsRefused) {
  const Bytes bytes = packet_of(1, {kSystem, kAdd, kTrade, kSystem});
  for (std::size_t size = 0; size < bytes.size(); ++size) {
    const PacketStatus status = mold::decode(std::span{bytes}.first(size)).status;
    EXPECT_FALSE(mold::is_well_formed(status)) << "cut at " << size;
  }
  EXPECT_EQ(mold::decode(bytes).status, PacketStatus::messages);
}

// --- The sequence tracker -------------------------------------------------------------

mold::Header header_of(std::uint64_t sequence, std::uint16_t count,
                       mold::Session session = kSession) {
  return {.session = session, .sequence = sequence, .count = count};
}

TEST(MoldSequence, InOrderPacketsAdmitEverything) {
  mold::SequenceTracker tracker{kSession};
  EXPECT_EQ(tracker.next_expected(), 1U);
  const mold::Admit first = tracker.admit(header_of(1, 3));
  EXPECT_EQ(first.skip, 0U);
  EXPECT_EQ(first.take, 3U);
  const mold::Admit second = tracker.admit(header_of(4, 2));
  EXPECT_EQ(second.take, 2U);
  EXPECT_EQ(tracker.next_expected(), 6U);
  EXPECT_EQ(tracker.counts().admitted, 5U);
  EXPECT_EQ(tracker.counts().gaps, 0U);
  EXPECT_EQ(tracker.counts().lost, 0U);
  EXPECT_EQ(tracker.counts().duplicate_messages, 0U);
}

TEST(MoldSequence, AJumpAheadIsAGapAndItsMessagesAreLost) {
  mold::SequenceTracker tracker{kSession};
  static_cast<void>(tracker.admit(header_of(1, 3)));
  const mold::Admit after = tracker.admit(header_of(10, 2));
  EXPECT_EQ(after.skip, 0U);
  EXPECT_EQ(after.take, 2U);
  EXPECT_EQ(tracker.counts().gaps, 1U);
  EXPECT_EQ(tracker.counts().lost, 6U);
  EXPECT_EQ(tracker.next_expected(), 12U);
}

TEST(MoldSequence, APacketAgainIsADuplicateAndAdmitsNothing) {
  mold::SequenceTracker tracker{kSession};
  static_cast<void>(tracker.admit(header_of(1, 3)));
  static_cast<void>(tracker.admit(header_of(4, 2)));
  const mold::Admit again = tracker.admit(header_of(4, 2));
  EXPECT_EQ(again.take, 0U);
  const mold::Admit older = tracker.admit(header_of(1, 3));
  EXPECT_EQ(older.take, 0U);
  EXPECT_EQ(tracker.counts().duplicate_packets, 2U);
  EXPECT_EQ(tracker.counts().duplicate_messages, 5U);
  EXPECT_EQ(tracker.counts().admitted, 5U);
  EXPECT_EQ(tracker.next_expected(), 6U);
}

TEST(MoldSequence, AnOverlapAdmitsOnlyTheNewTail) {
  mold::SequenceTracker tracker{kSession};
  static_cast<void>(tracker.admit(header_of(1, 3)));
  const mold::Admit overlap = tracker.admit(header_of(2, 4));
  EXPECT_EQ(overlap.skip, 2U);
  EXPECT_EQ(overlap.take, 2U);
  EXPECT_EQ(tracker.counts().duplicate_messages, 2U);
  EXPECT_EQ(tracker.counts().duplicate_packets, 0U);
  EXPECT_EQ(tracker.next_expected(), 6U);
}

TEST(MoldSequence, ALateArrivalAfterAGapStaysLost) {
  mold::SequenceTracker tracker{kSession};
  static_cast<void>(tracker.admit(header_of(1, 2)));
  static_cast<void>(tracker.admit(header_of(5, 1)));
  const mold::Admit late = tracker.admit(header_of(3, 2));
  EXPECT_EQ(late.take, 0U);
  EXPECT_EQ(tracker.counts().lost, 2U);
  EXPECT_EQ(tracker.counts().duplicate_messages, 2U);
}

TEST(MoldSequence, AHeartbeatAheadShowsLossAtTheTail) {
  mold::SequenceTracker tracker{kSession};
  static_cast<void>(tracker.admit(header_of(1, 3)));
  const mold::Admit beat = tracker.admit(header_of(6, mold::kHeartbeatCount));
  EXPECT_EQ(beat.take, 0U);
  EXPECT_EQ(tracker.counts().heartbeats, 1U);
  EXPECT_EQ(tracker.counts().gaps, 1U);
  EXPECT_EQ(tracker.counts().lost, 2U);
  EXPECT_EQ(tracker.next_expected(), 6U);
  // A heartbeat at the next expected sequence is no gap.
  static_cast<void>(tracker.admit(header_of(6, mold::kHeartbeatCount)));
  EXPECT_EQ(tracker.counts().gaps, 1U);
}

TEST(MoldSequence, TheEndOfSessionEndsItAndCountsWhatComesAfter) {
  mold::SequenceTracker tracker{kSession};
  static_cast<void>(tracker.admit(header_of(1, 3)));
  EXPECT_FALSE(tracker.ended());
  static_cast<void>(tracker.admit(header_of(5, mold::kEndOfSessionCount)));
  EXPECT_TRUE(tracker.ended());
  EXPECT_EQ(tracker.counts().lost, 1U);
  EXPECT_EQ(tracker.counts().ends, 1U);
  EXPECT_EQ(tracker.admit(header_of(5, 2)).take, 0U);
  static_cast<void>(tracker.admit(header_of(5, mold::kEndOfSessionCount)));
  EXPECT_EQ(tracker.counts().after_end, 2U);
  EXPECT_EQ(tracker.counts().admitted, 3U);
}

TEST(MoldSequence, AnotherSessionIsIgnored) {
  mold::SequenceTracker tracker{kSession};
  const mold::Admit other = tracker.admit(header_of(1, 3, mold::make_session("OTHER")));
  EXPECT_EQ(other.take, 0U);
  EXPECT_EQ(tracker.counts().foreign_session, 1U);
  EXPECT_EQ(tracker.counts().packets, 0U);
  EXPECT_EQ(tracker.next_expected(), 1U);
}

TEST(MoldSequence, ASequencePastTheTopIsOutOfRange) {
  mold::SequenceTracker tracker{kSession};
  const mold::Admit top = tracker.admit(header_of(0xFFFFFFFFFFFFFFFFULL, 2));
  EXPECT_EQ(top.take, 0U);
  EXPECT_EQ(tracker.counts().out_of_range, 1U);
  EXPECT_EQ(tracker.counts().lost, 0U);
}

// --- The packet array -----------------------------------------------------------------

// The blocks of every data packet laid end to end, and the packets checked on the way.
Bytes blocks_of(const mold::PacketArray& packets) {
  Bytes out;
  for (std::size_t i = 0; i < packets.size(); ++i) {
    const mold::Packet packet = mold::decode(packets.packet(i));
    if (packet.status == PacketStatus::messages) {
      out.insert(out.end(), packet.blocks.begin(), packet.blocks.end());
    }
  }
  return out;
}

// Every packet decodes, fits the cap and the count limit, takes up its sequence where
// the last left off, and the array closes on a heartbeat and the end of session.
void expect_well_formed(const mold::PacketArray& packets) {
  const mold::PacketPlan& plan = packets.plan();
  std::uint64_t next = plan.first_sequence;
  std::uint64_t messages = 0;
  ASSERT_GE(packets.size(), 2U);
  for (std::size_t i = 0; i < packets.size(); ++i) {
    SCOPED_TRACE(i);
    const std::span<const std::byte> bytes = packets.packet(i);
    const mold::Packet packet = mold::decode(bytes);
    ASSERT_TRUE(mold::is_well_formed(packet.status));
    EXPECT_LE(bytes.size(), plan.max_datagram);
    EXPECT_EQ(packet.header.session, plan.session);
    EXPECT_EQ(packet.header.sequence, next);
    EXPECT_EQ(packets.ref(i).size, bytes.size());
    if (packet.status == PacketStatus::messages) {
      EXPECT_LE(packet.header.count, plan.max_messages);
      EXPECT_EQ(packets.ref(i).messages, packet.header.count);
      next += packet.header.count;
      messages += packet.header.count;
    } else {
      EXPECT_EQ(packets.ref(i).messages, 0U);
    }
  }
  EXPECT_EQ(mold::decode(packets.packet(packets.size() - 2)).status, PacketStatus::heartbeat);
  EXPECT_EQ(mold::decode(packets.packet(packets.size() - 1)).status, PacketStatus::end_of_session);
  EXPECT_EQ(messages, packets.messages());
  EXPECT_EQ(packets.next_sequence(), next);
  EXPECT_EQ(packets.ref(packets.size() - 1).offset + packets.ref(packets.size() - 1).size,
            packets.bytes().size());
}

std::uint64_t frames_in(std::span<const std::byte> stream) {
  itch::FrameReader reader{stream};
  std::uint64_t frames = 0;
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    ++frames;
  }
  return frames;
}

// IU-36: the blocks are the file's frames, byte for byte, on every fixture day.
TEST(MoldPackets, TheBlocksAreTheFileByteForByte) {
  const auto files = fixture_files();
  ASSERT_GE(files.size(), 8U);
  for (const auto& [name, bytes] : files) {
    SCOPED_TRACE(name);
    const auto packets = mold::build_packets(bytes, {});
    ASSERT_TRUE(packets.has_value());
    expect_well_formed(*packets);
    EXPECT_EQ(blocks_of(*packets), bytes);
    EXPECT_EQ(packets->messages(), frames_in(bytes));
  }
}

TEST(MoldPackets, TheChurnStreamPacksByteForByteAtEveryCap) {
  const Bytes stream = itch::testing::build_stream();
  for (const std::size_t cap : {mold::kSmallestDayCap, std::size_t{100}, std::size_t{1400},
                                std::size_t{9000}, mold::kMostDatagram}) {
    SCOPED_TRACE(cap);
    const auto packets = mold::build_packets(stream, {.max_datagram = cap});
    ASSERT_TRUE(packets.has_value());
    expect_well_formed(*packets);
    EXPECT_EQ(blocks_of(*packets), stream);
  }
}

// A packet closes only when the next block would not fit, so each data packet but the
// last before a heartbeat is within one block of the cap.
TEST(MoldPackets, EachPacketIsFilledToTheCap) {
  const Bytes stream = itch::testing::build_stream();
  const auto packets = mold::build_packets(stream, {.heartbeat_gap_ns = 0});
  ASSERT_TRUE(packets.has_value());
  std::size_t data_packets = 0;
  for (std::size_t i = 0; i + 1 < packets->size(); ++i) {
    const mold::Packet next = mold::decode(packets->packet(i + 1));
    if (packets->ref(i).messages == 0 || next.status != PacketStatus::messages) {
      continue;
    }
    ++data_packets;
    const std::size_t next_block =
        mold::kBlockPrefix + itch::read_be<std::uint16_t>(next.blocks.data());
    EXPECT_GT(packets->ref(i).size + next_block, mold::kDefaultDatagram) << i;
  }
  EXPECT_GT(data_packets, 1000U);
}

TEST(MoldPackets, TheMessageLimitHoldsEachPacketToIt) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  const auto packets = mold::build_packets(stream, {.max_messages = 1, .heartbeat_gap_ns = 0});
  ASSERT_TRUE(packets.has_value());
  expect_well_formed(*packets);
  EXPECT_EQ(packets->size(), frames_in(stream) + 2);
  EXPECT_EQ(blocks_of(*packets), stream);
}

TEST(MoldPackets, ASliceTakesItsFramesAndNumbersThemFromTheFirstSequence) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  const auto packets =
      mold::build_packets(stream, {.first_sequence = 101, .first_frame = 5, .frame_limit = 7});
  ASSERT_TRUE(packets.has_value());
  expect_well_formed(*packets);
  EXPECT_EQ(packets->messages(), 7U);
  EXPECT_EQ(packets->next_sequence(), 108U);
  itch::FrameReader reader{stream};
  for (int i = 0; i < 5; ++i) {
    static_cast<void>(reader.next());
  }
  const std::size_t from = reader.consumed();
  for (int i = 0; i < 7; ++i) {
    static_cast<void>(reader.next());
  }
  const Bytes want(stream.begin() + static_cast<std::ptrdiff_t>(from),
                   stream.begin() + static_cast<std::ptrdiff_t>(reader.consumed()));
  EXPECT_EQ(blocks_of(*packets), want);
}

TEST(MoldPackets, ASlicePastTheEndHoldsOnlyTheClose) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  const auto packets = mold::build_packets(stream, {.first_frame = 1000});
  ASSERT_TRUE(packets.has_value());
  EXPECT_EQ(packets->size(), 2U);
  EXPECT_EQ(packets->messages(), 0U);
  expect_well_formed(*packets);
}

// Two messages a second apart in ITCH time get a heartbeat between them, and only one.
TEST(MoldPackets, AQuietSpellInItchTimeGetsOneHeartbeat) {
  Bytes early = kSystem;
  Bytes late = kSystem;
  const std::uint64_t at = 1'000'000;
  const std::uint64_t after = at + (5 * mold::kDefaultHeartbeatGapNs);
  for (std::size_t i = 0; i < 6; ++i) {
    early[5 + i] = static_cast<std::byte>(at >> (40U - (8U * i)));
    late[5 + i] = static_cast<std::byte>(after >> (40U - (8U * i)));
  }
  Bytes stream;
  append_frame(stream, early);
  append_frame(stream, early);
  append_frame(stream, late);
  const auto packets = mold::build_packets(stream, {});
  ASSERT_TRUE(packets.has_value());
  expect_well_formed(*packets);
  ASSERT_EQ(packets->size(), 5U);
  EXPECT_EQ(packets->ref(0).messages, 2U);
  EXPECT_EQ(mold::decode(packets->packet(1)).status, PacketStatus::heartbeat);
  EXPECT_EQ(mold::decode(packets->packet(1)).header.sequence, 3U);
  EXPECT_EQ(packets->ref(2).messages, 1U);
  EXPECT_EQ(packets->ref(2).timestamp_ns, after);
  EXPECT_EQ(packets->heartbeats(), 2U);
  const auto without = mold::build_packets(stream, {.heartbeat_gap_ns = 0});
  ASSERT_TRUE(without.has_value());
  EXPECT_EQ(without->size(), 3U);
}

TEST(MoldPackets, TheFlushGapClosesAPacketEarly) {
  Bytes stream;
  for (std::uint64_t ts : {100ULL, 150ULL, 400ULL, 450ULL}) {
    Bytes message = kSystem;
    for (std::size_t i = 0; i < 6; ++i) {
      message[5 + i] = static_cast<std::byte>(ts >> (40U - (8U * i)));
    }
    append_frame(stream, message);
  }
  const auto packets = mold::build_packets(stream, {.flush_gap_ns = 100});
  ASSERT_TRUE(packets.has_value());
  expect_well_formed(*packets);
  ASSERT_EQ(packets->size(), 4U);
  EXPECT_EQ(packets->ref(0).messages, 2U);
  EXPECT_EQ(packets->ref(1).messages, 2U);
  EXPECT_EQ(packets->ref(1).timestamp_ns, 400U);
  const auto off = mold::build_packets(stream, {});
  ASSERT_TRUE(off.has_value());
  EXPECT_EQ(off->size(), 3U);
}

TEST(MoldPackets, ATruncatedInputIsRefusedWhereItEnds) {
  Bytes stream = itch::fixtures::all_messages_stream();
  const std::size_t whole = stream.size();
  stream.pop_back();
  const auto packets = mold::build_packets(stream, {});
  ASSERT_FALSE(packets.has_value());
  EXPECT_EQ(packets.error().error, mold::BuildError::truncated_input);
  EXPECT_EQ(packets.error().offset, whole - 14);  // The closing System Event's frame.
}

// A cap one under the least that holds every type refuses the stream at its Net Order
// Imbalance, the largest message.
TEST(MoldPackets, AFrameTooBigForTheCapIsRefused) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  const auto packets = mold::build_packets(stream, {.max_datagram = mold::kSmallestDayCap - 1});
  ASSERT_FALSE(packets.has_value());
  EXPECT_EQ(packets.error().error, mold::BuildError::oversized_frame);
  EXPECT_EQ(static_cast<char>(stream[packets.error().offset + 2]), 'I');
  EXPECT_TRUE(mold::build_packets(stream, {.max_datagram = mold::kSmallestDayCap}).has_value());
}

TEST(MoldPackets, APlanOutsideTheFormatIsRefused) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  for (const mold::PacketPlan& plan : {mold::PacketPlan{.max_datagram = mold::kHeaderBytes + 1},
                                       mold::PacketPlan{.max_datagram = mold::kMostDatagram + 1},
                                       mold::PacketPlan{.max_messages = 0},
                                       mold::PacketPlan{.max_messages = mold::kEndOfSessionCount},
                                       mold::PacketPlan{.first_sequence = 0xFFFFFFFFFFFFFFF0ULL}}) {
    const auto packets = mold::build_packets(stream, plan);
    ASSERT_FALSE(packets.has_value());
    EXPECT_EQ(packets.error().error, mold::BuildError::bad_plan);
  }
}

// --- The feed handler -----------------------------------------------------------------

// Records every frame it is handed.
struct Recorder {
  std::vector<Bytes> payloads;
  std::vector<itch::FrameStatus> statuses;
  void operator()(const itch::Frame& frame) {
    payloads.emplace_back(frame.payload.begin(), frame.payload.end());
    statuses.push_back(frame.status);
  }
};

Recorder frames_of_file(std::span<const std::byte> stream) {
  Recorder recorder;
  itch::FrameReader reader{stream};
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    recorder(frame);
  }
  return recorder;
}

TEST(MoldFeed, EveryPacketInOrderGivesTheFilesFrames) {
  for (const auto& [name, bytes] : fixture_files()) {
    SCOPED_TRACE(name);
    const auto packets = mold::build_packets(bytes, {.max_datagram = 200});
    ASSERT_TRUE(packets.has_value());
    Recorder recorder;
    mold::FeedHandler feed{packets->plan().session, 1, recorder};
    for (std::size_t i = 0; i < packets->size(); ++i) {
      feed.on_datagram(packets->packet(i));
    }
    const Recorder want = frames_of_file(bytes);
    EXPECT_EQ(recorder.payloads, want.payloads);
    EXPECT_EQ(recorder.statuses, want.statuses);
    EXPECT_TRUE(feed.ended());
    EXPECT_EQ(feed.counts().frames, packets->messages());
    EXPECT_EQ(feed.counts().malformed(), 0U);
    EXPECT_EQ(feed.sequence().counts().lost, 0U);
  }
}

TEST(MoldFeed, ADroppedPacketIsAGapOfItsMessages) {
  const Bytes stream = itch::testing::build_stream();
  const auto packets = mold::build_packets(stream, {});
  ASSERT_TRUE(packets.has_value());
  Recorder recorder;
  mold::FeedHandler feed{packets->plan().session, 1, recorder};
  constexpr std::size_t kDropped = 10;
  for (std::size_t i = 0; i < packets->size(); ++i) {
    if (i != kDropped) {
      feed.on_datagram(packets->packet(i));
    }
  }
  EXPECT_EQ(feed.sequence().counts().gaps, 1U);
  EXPECT_EQ(feed.sequence().counts().lost, packets->ref(kDropped).messages);
  EXPECT_EQ(feed.counts().frames, packets->messages() - packets->ref(kDropped).messages);
}

TEST(MoldFeed, ATrailingLossIsSeenAtTheClose) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  const auto packets = mold::build_packets(stream, {.max_messages = 4, .heartbeat_gap_ns = 0});
  ASSERT_TRUE(packets.has_value());
  Recorder recorder;
  mold::FeedHandler feed{packets->plan().session, 1, recorder};
  const std::size_t last_data = packets->size() - 3;
  for (std::size_t i = 0; i < packets->size(); ++i) {
    if (i != last_data) {
      feed.on_datagram(packets->packet(i));
    }
  }
  EXPECT_TRUE(feed.ended());
  EXPECT_EQ(feed.sequence().counts().lost, packets->ref(last_data).messages);
}

// Every packet twice, and then the whole array again: the frames handed on are the
// file's, once each.
TEST(MoldFeed, DuplicatesAreCountedAndNeverHandedOn) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  const auto packets = mold::build_packets(stream, {.max_messages = 3});
  ASSERT_TRUE(packets.has_value());
  Recorder recorder;
  mold::FeedHandler feed{packets->plan().session, 1, recorder};
  for (std::size_t i = 0; i + 1 < packets->size(); ++i) {
    feed.on_datagram(packets->packet(i));
    feed.on_datagram(packets->packet(i));
  }
  for (std::size_t i = 0; i + 1 < packets->size(); ++i) {
    feed.on_datagram(packets->packet(i));
  }
  EXPECT_EQ(recorder.payloads, frames_of_file(stream).payloads);
  EXPECT_EQ(feed.sequence().counts().duplicate_messages, 2 * packets->messages());
  EXPECT_EQ(feed.sequence().counts().lost, 0U);
}

TEST(MoldFeed, AnOverlappingPacketHandsOnOnlyItsNewBlocks) {
  Recorder recorder;
  mold::FeedHandler feed{kSession, 1, recorder};
  feed.on_datagram(packet_of(1, {kSystem, kAdd}));
  feed.on_datagram(packet_of(2, {kAdd, kTrade, kSystem}));
  EXPECT_EQ(recorder.payloads, (std::vector<Bytes>{kSystem, kAdd, kTrade, kSystem}));
  EXPECT_EQ(feed.sequence().counts().duplicate_messages, 1U);
}

TEST(MoldFeed, AMalformedPacketIsCountedAndHandsOnNothing) {
  Recorder recorder;
  mold::FeedHandler feed{kSession, 1, recorder};
  feed.on_datagram(packet_of(1, 3, {kSystem, kAdd}));
  feed.on_datagram(packet_of(1, 1, {kSystem, kAdd}));
  const Bytes whole = packet_of(1, {kSystem});
  feed.on_datagram(std::span{whole}.first(10));
  EXPECT_TRUE(recorder.payloads.empty());
  EXPECT_EQ(feed.counts().of(PacketStatus::truncated_block), 1U);
  EXPECT_EQ(feed.counts().of(PacketStatus::trailing_bytes), 1U);
  EXPECT_EQ(feed.counts().of(PacketStatus::short_header), 1U);
  EXPECT_EQ(feed.counts().malformed(), 3U);
  // The sequence is untouched, so the good packet that follows is in order.
  feed.on_datagram(packet_of(1, {kSystem}));
  EXPECT_EQ(feed.sequence().counts().gaps, 0U);
  EXPECT_EQ(recorder.payloads.size(), 1U);
}

// The book after the feed is the book after the file.
TEST(MoldFeed, TheBookAfterTheFeedIsTheBookAfterTheFile) {
  const Bytes stream = itch::testing::build_stream();
  constexpr itch::BookCapacity kCapacity{
      .orders = 2048, .levels = 2048, .index_pages = 4, .index_bits = 20, .level_slots = 4096};
  itch::FastBook<> by_file{kCapacity};
  itch::FastBook<> by_feed{kCapacity};
  std::vector<itch::ApplyStatus> file_statuses;
  std::vector<itch::ApplyStatus> feed_statuses;
  itch::FrameReader reader{stream};
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    file_statuses.push_back(by_file.apply(frame.payload));
  }
  const auto apply = [&](const itch::Frame& frame) {
    feed_statuses.push_back(by_feed.apply(frame.payload));
  };
  const auto packets = mold::build_packets(stream, {});
  ASSERT_TRUE(packets.has_value());
  mold::FeedHandler feed{packets->plan().session, 1, apply};
  for (std::size_t i = 0; i < packets->size(); ++i) {
    feed.on_datagram(packets->packet(i));
  }
  EXPECT_EQ(feed_statuses, file_statuses);
  EXPECT_GT(file_statuses.size(), 200000U);
}

}  // namespace
