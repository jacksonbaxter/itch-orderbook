// The BinaryFILE frame reader: framing, skips, typed failures and safety.
#include <gtest/gtest.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <span>
#include <variant>
#include <vector>

#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "support/encode.hpp"
#include "support/fixtures.hpp"
#include "support/truncation.hpp"

namespace {

using itch::FrameReader;
using itch::FrameStatus;
using itch::fixtures::append_frame;
using itch::fixtures::Bytes;
using itch::fixtures::to_bytes;

Bytes frames_of(std::initializer_list<Bytes> payloads) {
  Bytes stream;
  for (const Bytes& payload : payloads) {
    append_frame(stream, payload);
  }
  return stream;
}

const Bytes kSystem = to_bytes(itch::fixtures::kSystemEvent);
const Bytes kAdd = to_bytes(itch::fixtures::kAddOrder);

TEST(Reader, EmptyInputEndsAtOnce) {
  FrameReader reader{{}};
  const auto frame = reader.next();
  EXPECT_EQ(frame.status, FrameStatus::end_of_input);
  EXPECT_EQ(reader.consumed(), 0U);
  EXPECT_EQ(reader.counters().frames, 0U);
}

TEST(Reader, WalksEveryTypeAndCountsIt) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  FrameReader reader{stream};
  const auto messages = itch::fixtures::all_messages();
  for (std::size_t i = 0; i < messages.size(); ++i) {
    const auto frame = reader.next();
    ASSERT_EQ(frame.status, FrameStatus::message) << "frame " << i;
    EXPECT_TRUE(std::ranges::equal(frame.payload, messages[i])) << "frame " << i;
  }
  EXPECT_EQ(reader.next().status, FrameStatus::message);  // The closing System Event.
  EXPECT_EQ(reader.next().status, FrameStatus::end_of_input);
  EXPECT_EQ(reader.consumed(), stream.size());

  const auto& counters = reader.counters();
  EXPECT_EQ(counters.frames, 24U);
  EXPECT_EQ(counters.messages, 24U);
  EXPECT_EQ(counters.frames_by_type['S'], 2U);
  for (const auto& spec : itch::kMessageSpecs) {
    if (spec.type != 'S') {
      EXPECT_EQ(counters.frames_by_type[static_cast<unsigned char>(spec.type)], 1U)
          << "type " << spec.type;
    }
  }
}

TEST(Reader, FrameOffsetsAddUp) {
  const Bytes stream = frames_of({kSystem, kAdd});
  FrameReader reader{stream};
  const auto first = reader.next();
  const auto second = reader.next();
  EXPECT_EQ(first.offset, 0U);
  EXPECT_EQ(first.payload.size(), 12U);
  EXPECT_EQ(second.offset, 14U);
  EXPECT_EQ(second.payload.size(), 36U);
  EXPECT_EQ(reader.consumed(), 52U);
}

TEST(Reader, UnknownTypeIsSkippedByLengthAndCounted) {
  const Bytes unknown = to_bytes(std::array<std::uint8_t, 7>{0x7A, 1, 2, 3, 4, 5, 6});
  const Bytes stream = frames_of({kSystem, unknown, kAdd});
  FrameReader reader{stream};

  EXPECT_EQ(reader.next().status, FrameStatus::message);
  const auto skipped = reader.next();
  EXPECT_EQ(skipped.status, FrameStatus::unknown_type);
  EXPECT_EQ(skipped.payload.size(), 7U);

  const auto after = reader.next();
  ASSERT_EQ(after.status, FrameStatus::message);
  const auto decoded = itch::decode(after.payload);
  ASSERT_TRUE(decoded);
  EXPECT_TRUE(std::holds_alternative<itch::AddOrder>(*decoded));

  EXPECT_EQ(reader.next().status, FrameStatus::end_of_input);
  EXPECT_EQ(reader.counters().unknown_type, 1U);
  EXPECT_EQ(reader.counters().messages, 2U);
  EXPECT_EQ(reader.counters().frames, 3U);
  EXPECT_EQ(reader.counters().frames_by_type[0x7A], 1U);
}

TEST(Reader, EveryTypeByteKeepsTheStreamInStep) {
  for (unsigned value = 0; value < 256; ++value) {
    const auto type = static_cast<std::byte>(value);
    const Bytes payload{type, std::byte{0}, std::byte{0}};  // Too short for any known type.
    const Bytes stream = frames_of({payload, kAdd});
    FrameReader reader{stream};
    const FrameStatus wanted = itch::message_length(static_cast<char>(value)) == 0
                                   ? FrameStatus::unknown_type
                                   : FrameStatus::bad_length;
    EXPECT_EQ(reader.next().status, wanted) << "type byte " << value;
    EXPECT_EQ(reader.next().status, FrameStatus::message) << "type byte " << value;
    EXPECT_EQ(reader.next().status, FrameStatus::end_of_input) << "type byte " << value;
  }
}

TEST(Reader, BadLengthForAKnownTypeIsCountedAndDoesNotDesynchronise) {
  for (const std::size_t size :
       {std::size_t{1}, std::size_t{35}, std::size_t{37}, std::size_t{300}}) {
    Bytes wrong(size, std::byte{0x41});  // Type 'A', and more 'A's as bait.
    const Bytes stream = frames_of({wrong, kSystem});
    FrameReader reader{stream};

    const auto bad = reader.next();
    EXPECT_EQ(bad.status, FrameStatus::bad_length) << "size " << size;
    EXPECT_EQ(bad.payload.size(), size);

    const auto next = reader.next();
    EXPECT_EQ(next.status, FrameStatus::message) << "size " << size;
    EXPECT_EQ(next.offset, size + 2);
    EXPECT_EQ(reader.next().status, FrameStatus::end_of_input);
    EXPECT_EQ(reader.counters().bad_length, 1U);
    EXPECT_EQ(reader.counters().messages, 1U);
  }
}

TEST(Reader, ABadLengthPayloadCannotInjectFrames) {
  // The payload of a bad frame holds a whole valid frame. The reader must not see it.
  Bytes wrong{std::byte{0x41}};
  const Bytes inner = frames_of({kSystem});
  wrong.insert(wrong.end(), inner.begin(), inner.end());
  const Bytes stream = frames_of({wrong});
  FrameReader reader{stream};
  EXPECT_EQ(reader.next().status, FrameStatus::bad_length);
  EXPECT_EQ(reader.next().status, FrameStatus::end_of_input);
  EXPECT_EQ(reader.counters().messages, 0U);
}

TEST(Reader, ZeroLengthFrameIsCountedAndWalkedPast) {
  Bytes stream = frames_of({kSystem});
  append_frame(stream, {});
  append_frame(stream, kAdd);
  append_frame(stream, {});
  FrameReader reader{stream};

  EXPECT_EQ(reader.next().status, FrameStatus::message);
  const auto zero = reader.next();
  EXPECT_EQ(zero.status, FrameStatus::zero_length);
  EXPECT_TRUE(zero.payload.empty());
  EXPECT_EQ(zero.offset, 14U);
  EXPECT_EQ(reader.next().status, FrameStatus::message);
  EXPECT_EQ(reader.next().status, FrameStatus::zero_length);
  EXPECT_EQ(reader.next().status, FrameStatus::end_of_input);
  EXPECT_EQ(reader.counters().zero_length, 2U);
  EXPECT_EQ(reader.counters().frames, 2U);
}

TEST(Reader, OversizedLengthBeyondTheInputIsATypedFailure) {
  Bytes stream = frames_of({kSystem});
  stream.push_back(std::byte{0xFF});
  stream.push_back(std::byte{0xFF});
  stream.push_back(std::byte{0x41});  // 65535 asked for, 1 present.
  FrameReader reader{stream};

  EXPECT_EQ(reader.next().status, FrameStatus::message);
  const auto end = reader.next();
  EXPECT_EQ(end.status, FrameStatus::truncated_payload);
  EXPECT_EQ(end.offset, 14U);
  EXPECT_TRUE(end.payload.empty());
  EXPECT_EQ(reader.consumed(), 14U);
  EXPECT_EQ(reader.counters().truncated_payload, 1U);
}

TEST(Reader, OversizedLengthThatFitsIsSkippedWhole) {
  const Bytes huge(65535, std::byte{0x41});  // Known type, maximum frame length.
  const Bytes unknown(65535, std::byte{0x7A});
  const Bytes stream = frames_of({huge, unknown, kSystem});
  FrameReader reader{stream};
  EXPECT_EQ(reader.next().status, FrameStatus::bad_length);
  EXPECT_EQ(reader.next().status, FrameStatus::unknown_type);
  EXPECT_EQ(reader.next().status, FrameStatus::message);
  EXPECT_EQ(reader.next().status, FrameStatus::end_of_input);
  EXPECT_EQ(reader.consumed(), stream.size());
}

TEST(Reader, LengthOneShortOfTheInputIsATypedFailure) {
  Bytes stream{std::byte{0x00}, std::byte{0x0C}};  // Asks for 12.
  const Bytes eleven(kSystem.begin(), kSystem.end() - 1);
  stream.insert(stream.end(), eleven.begin(), eleven.end());
  FrameReader reader{stream};
  EXPECT_EQ(reader.next().status, FrameStatus::truncated_payload);
}

TEST(Reader, TerminalStatesStayAndCountOnce) {
  const Bytes one_byte{std::byte{0x00}};
  FrameReader reader{one_byte};
  for (int i = 0; i < 3; ++i) {
    EXPECT_EQ(reader.next().status, FrameStatus::truncated_prefix);
  }
  EXPECT_EQ(reader.counters().truncated_prefix, 1U);
  EXPECT_EQ(reader.consumed(), 0U);
}

// Exact-size heap copies, so that ASan sees any read past the cut.
TEST(Reader, TruncationAtEveryByteOffsetIsATypedFailure) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  const auto boundaries = itch::testing::frame_boundaries(stream);
  ASSERT_EQ(boundaries.size(), 25U);
  ASSERT_EQ(boundaries.back(), stream.size());

  int failures = 0;
  for (std::size_t cut = 0; cut <= stream.size(); ++cut) {
    const Bytes prefix(stream.begin(), stream.begin() + static_cast<std::ptrdiff_t>(cut));
    failures += itch::testing::check_prefix(prefix, stream, boundaries);
  }
  EXPECT_EQ(failures, 0);
  std::cout << "compared " << stream.size() + 1 << " cut points over " << boundaries.size() - 1
            << " frames\n";
}

TEST(Reader, ExtremeLocateAndReferenceRoundTrip) {
  // Add Order at locate 65535 with reference 2^64-1.
  const std::array<std::uint8_t, 36> add{
      0x41,                                            // Message Type 'A'
      0xFF, 0xFF,                                      // Stock Locate 65535
      0xFF, 0xFF,                                      // Tracking Number 65535
      0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,              // Timestamp 2^48-1
      0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,  // Order Reference 2^64-1
      0x53,                                            // Side 'S'
      0xFF, 0xFF, 0xFF, 0xFF,                          // Shares 2^32-1
      0x54, 0x45, 0x53, 0x54, 0x41, 0x20, 0x20, 0x20,  // Stock "TESTA   "
      0xFF, 0xFF, 0xFF, 0xFF,                          // Price 2^32-1
  };
  const std::array<std::uint8_t, 35> replace{
      0x55,                                            // Message Type 'U'
      0xFF, 0xFF,                                      // Stock Locate 65535
      0x00, 0x01,                                      // Tracking Number
      0x00, 0x00, 0x00, 0x00, 0x00, 0x01,              // Timestamp
      0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,  // Original Reference 2^64-1
      0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFE,  // New Reference 2^64-2
      0x00, 0x00, 0x00, 0x01,                          // Shares 1
      0x00, 0x00, 0x00, 0x01,                          // Price 1
  };
  const Bytes add_bytes = to_bytes(add);
  const Bytes replace_bytes = to_bytes(replace);
  const Bytes stream = frames_of({add_bytes, replace_bytes});
  FrameReader reader{stream};

  const auto first = reader.next();
  ASSERT_EQ(first.status, FrameStatus::message);
  const auto decoded_add = itch::decode(first.payload);
  ASSERT_TRUE(decoded_add);
  const auto& order = std::get<itch::AddOrder>(*decoded_add);
  EXPECT_EQ(order.header.stock_locate, 65535U);
  EXPECT_EQ(order.header.tracking_number, 65535U);
  EXPECT_EQ(order.header.timestamp_ns, (std::uint64_t{1} << 48U) - 1);
  EXPECT_EQ(order.order_reference, UINT64_MAX);
  EXPECT_EQ(order.shares, UINT32_MAX);
  EXPECT_EQ(order.price, UINT32_MAX);
  EXPECT_EQ(itch::testing::encode(*decoded_add), add_bytes);

  const auto second = reader.next();
  ASSERT_EQ(second.status, FrameStatus::message);
  const auto decoded_replace = itch::decode(second.payload);
  ASSERT_TRUE(decoded_replace);
  const auto& replaced = std::get<itch::OrderReplace>(*decoded_replace);
  EXPECT_EQ(replaced.header.stock_locate, 65535U);
  EXPECT_EQ(replaced.original_order_reference, UINT64_MAX);
  EXPECT_EQ(replaced.new_order_reference, UINT64_MAX - 1);
  EXPECT_EQ(itch::testing::encode(*decoded_replace), replace_bytes);
}

TEST(Reader, EightByteShareCountsTakeTheirFullRange) {
  auto cross = itch::fixtures::kCrossTrade;
  for (std::size_t offset = 11; offset < 19; ++offset) {
    cross[offset] = 0xFF;
  }
  const Bytes bytes = to_bytes(cross);
  const auto decoded = itch::decode(bytes);
  ASSERT_TRUE(decoded);
  EXPECT_EQ(std::get<itch::CrossTrade>(*decoded).shares, UINT64_MAX);
}

// The spec names only B and D after End of System Hours, and real files also have X
// there (DS-71). The reader holds no state, so no order of messages can trip it.
TEST(Reader, OrderMessagesAfterEndOfSystemHoursAreOrdinaryFrames) {
  auto end_of_system = itch::fixtures::kSystemEvent;
  end_of_system[11] = 0x45;  // Event Code 'E'.
  const Bytes stream = frames_of({to_bytes(end_of_system), to_bytes(itch::fixtures::kOrderCancel),
                                  to_bytes(itch::fixtures::kOrderDelete)});
  FrameReader reader{stream};
  for (int i = 0; i < 3; ++i) {
    EXPECT_EQ(reader.next().status, FrameStatus::message);
  }
  EXPECT_EQ(reader.next().status, FrameStatus::end_of_input);
}

TEST(Reader, StatusNames) {
  EXPECT_EQ(itch::to_string(FrameStatus::truncated_payload), "truncated_payload");
  EXPECT_EQ(itch::to_string(itch::DecodeError::bad_length), "bad_length");
}

}  // namespace
