// No read ever leaves the bytes it was given. Input sits flush against a PROT_NONE
// page, so that one byte too many is a fault, even in a build with no sanitizer.
//
// Each case runs in a forked child, which turns the fault into a reported result
// naming what caused it.
#include <gtest/gtest.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <span>
#include <vector>

#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"
#include "support/encode.hpp"
#include "support/fixtures.hpp"
#include "support/guard_page.hpp"
#include "support/truncation.hpp"

namespace {

using itch::FrameReader;
using itch::FrameStatus;
using itch::fixtures::Bytes;
using itch::testing::GuardedRegion;
using itch::testing::round_trips;
using itch::testing::run_in_child;

constexpr std::size_t kRegion = 4096;

// The negative control. If this read survived, every pass below would mean nothing.
TEST(GuardPage, ControlAReadOfTheGuardPageFaults) {
  GuardedRegion region{kRegion};
  const auto result = run_in_child([&region] {
    const volatile std::byte* guard = region.rear_guard();
    return std::to_integer<int>(*guard);
  });
  EXPECT_TRUE(result.signal.has_value()) << "the guard page did not fault, so it guards nothing";
}

TEST(GuardPage, ControlAnEightByteReadOfASixByteFieldFaults) {
  GuardedRegion region{kRegion};
  const std::array<std::byte, 6> field{};
  const auto placed = region.flush_against_end(field);
  // The result decides the exit code, because a read nobody uses gets optimised away.
  const auto result =
      run_in_child([placed] { return itch::read_be<std::uint64_t>(placed.data()) == 0 ? 0 : 1; });
  EXPECT_TRUE(result.signal.has_value()) << "an over-read of 2 bytes went unseen";
}

TEST(GuardPage, ReadBe48ReadsExactlySixBytes) {
  GuardedRegion region{kRegion};
  const auto field =
      itch::fixtures::to_bytes(std::array<std::uint8_t, 6>{0x01, 0x23, 0x45, 0x67, 0x89, 0xAB});
  for (const auto placed : {region.flush_against_end(field), region.flush_against_start(field)}) {
    const auto result = run_in_child(
        [placed] { return itch::read_be48(placed.data()) == 0x0123456789ABULL ? 0 : 1; });
    EXPECT_TRUE(result.clean()) << "read_be48: " << result.describe();
  }
}

TEST(GuardPage, EveryTypeDecodesFlushAgainstTheEnd) {
  GuardedRegion region{kRegion};
  for (const Bytes& message : itch::fixtures::all_messages()) {
    const auto placed = region.flush_against_end(message);
    const auto result = run_in_child([placed] { return round_trips(placed) ? 0 : 1; });
    EXPECT_TRUE(result.clean()) << "type " << itch::read_char(message.data()) << ": "
                                << result.describe();
  }
}

TEST(GuardPage, EveryTypeDecodesFlushAgainstTheStart) {
  GuardedRegion region{kRegion};
  for (const Bytes& message : itch::fixtures::all_messages()) {
    const auto placed = region.flush_against_start(message);
    const auto result = run_in_child([placed] { return round_trips(placed) ? 0 : 1; });
    EXPECT_TRUE(result.clean()) << "type " << itch::read_char(message.data()) << ": "
                                << result.describe();
  }
}

TEST(GuardPage, FinalFrameOfEveryTypeWalksFlushAgainstTheEnd) {
  GuardedRegion region{kRegion};
  for (const Bytes& message : itch::fixtures::all_messages()) {
    Bytes stream;
    itch::fixtures::append_frame(stream, itch::fixtures::to_bytes(itch::fixtures::kAddOrder));
    itch::fixtures::append_frame(stream, message);
    const auto placed = region.flush_against_end(stream);
    const auto result = run_in_child([placed] {
      FrameReader reader{placed};
      int failures = 0;
      for (int i = 0; i < 2; ++i) {
        const auto frame = reader.next();
        failures += frame.status == FrameStatus::message && round_trips(frame.payload) ? 0 : 1;
      }
      return failures + (reader.next().status == FrameStatus::end_of_input ? 0 : 1);
    });
    EXPECT_TRUE(result.clean()) << "type " << itch::read_char(message.data()) << ": "
                                << result.describe();
  }
}

TEST(GuardPage, TruncationAtEveryByteOffsetStaysInBounds) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  const auto boundaries = itch::testing::frame_boundaries(stream);
  GuardedRegion region{stream.size()};
  const auto result = run_in_child([&] {
    int failures = 0;
    for (std::size_t cut = 0; cut <= stream.size(); ++cut) {
      const auto prefix = std::span{stream}.first(cut);
      failures += itch::testing::check_prefix(region.flush_against_end(prefix), prefix, boundaries);
    }
    return failures;
  });
  EXPECT_TRUE(result.clean()) << result.describe();
  std::cout << "compared " << stream.size() + 1 << " cut points against the guard page\n";
}

TEST(GuardPage, MalformedFramesStayInBounds) {
  GuardedRegion region{kRegion};
  // Each ends flush against the guard: a known type one byte short, a length that
  // asks for more than remains, a stray byte, and a bare length prefix.
  const std::vector<Bytes> streams{
      {std::byte{0x00}, std::byte{0x0B}, std::byte{0x53}, std::byte{0}, std::byte{0}, std::byte{0},
       std::byte{0}, std::byte{0}, std::byte{0}, std::byte{0}, std::byte{0}, std::byte{0},
       std::byte{0}},
      {std::byte{0xFF}, std::byte{0xFF}, std::byte{0x41}},
      {std::byte{0xFF}},
      {std::byte{0x00}, std::byte{0x0C}},
  };
  for (const Bytes& stream : streams) {
    const auto placed = region.flush_against_end(stream);
    const auto result = run_in_child([placed] {
      FrameReader reader{placed};
      for (int i = 0; i < 4; ++i) {
        const auto frame = reader.next();
        if (itch::is_terminal(frame.status)) {
          return 0;
        }
        if (frame.status == FrameStatus::message) {
          return 1;
        }
      }
      return 1;
    });
    EXPECT_TRUE(result.clean()) << "stream of " << stream.size() << " bytes: " << result.describe();
  }
}

}  // namespace
