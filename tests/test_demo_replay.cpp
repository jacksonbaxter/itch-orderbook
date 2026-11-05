// The browser demo's replay and its C interface, natively, on the hand-built extract of
// support/demo_stream.hpp. src/demo_smoke.cpp holds the wasm build to the same digest.
#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>
#include <vector>

#include "demo_engine.hpp"
#include "itch/book_types.hpp"
#include "itch/demo_replay.hpp"
#include "oracle/wire_builder.hpp"
#include "support/demo_stream.hpp"
#include "support/fixtures.hpp"

namespace {

using itch::ApplyStatus;
using itch::Side;
using itch::demo::DepthLevel;
using itch::demo::ExtractStatus;
using itch::demo::Print;
using itch::demo::Replay;
namespace wire = itch::oracle::wire;

// Small, so that each test builds its book in no time. The engine's own test below runs
// the default capacity the page uses.
constexpr itch::BookCapacity kSmall{
    .orders = 1024, .levels = 256, .index_pages = 64, .index_bits = 20, .level_slots = 4096};

bool same_print(const Print& got, const Print& want) {
  return got.timestamp_ns == want.timestamp_ns && got.shares == want.shares &&
         got.price == want.price && got.kind == want.kind && got.side == want.side;
}

TEST(DemoReplay, TheSurveyFindsTheOneSymbol) {
  const auto stream = itch::testing::demo_stream();
  const Replay replay{stream, kSmall};
  EXPECT_EQ(replay.extract_status(), ExtractStatus::ok);
  EXPECT_EQ(replay.messages(), 20U);
  EXPECT_EQ(replay.applied(), 0U);
  EXPECT_EQ(replay.locate(), itch::testing::kDemoLocate);
  EXPECT_EQ(std::string_view(replay.symbol().data(), replay.symbol().size()), "SYMR    ");
  EXPECT_EQ(replay.first_timestamp_ns(), 1'000U);
  EXPECT_EQ(replay.bytes(), stream.size());
}

TEST(DemoReplay, ReplayAppliesEveryMessageAndCountsEachStatus) {
  const auto stream = itch::testing::demo_stream();
  Replay replay{stream, kSmall};
  EXPECT_EQ(replay.replay(), 20U);
  EXPECT_TRUE(replay.done());
  EXPECT_EQ(replay.replay(), 0U);
  EXPECT_EQ(replay.status_count(ApplyStatus::applied), 12U);
  EXPECT_EQ(replay.status_count(ApplyStatus::no_book_effect), 7U);
  EXPECT_EQ(replay.status_count(ApplyStatus::unknown_order), 1U);
  EXPECT_EQ(replay.prints(), 0U) << "replay() is the timed call and reads no prints";
}

TEST(DemoReplay, StepKeepsEachPrintTheBookCanPrice) {
  const auto stream = itch::testing::demo_stream();
  Replay replay{stream, kSmall};
  while (!replay.done()) {
    ASSERT_GT(replay.step(3), 0U);
  }
  EXPECT_EQ(replay.last_timestamp_ns(), 20'000U);
  const std::array<Print, 5> want{{
      {.timestamp_ns = 9'000, .shares = 100, .price = 1'000'000, .kind = 'E', .side = 'B'},
      {.timestamp_ns = 10'000, .shares = 50, .price = 1'000'100, .kind = 'C', .side = 'S'},
      {.timestamp_ns = 12'000, .shares = 70, .price = 1'000'150, .kind = 'P', .side = 'B'},
      {.timestamp_ns = 16'000, .shares = 200, .price = 1'000'000, .kind = 'E', .side = 'B'},
      {.timestamp_ns = 18'000, .shares = 900, .price = 1'000'050, .kind = 'Q', .side = ' '},
  }};
  ASSERT_EQ(replay.prints(), want.size());
  for (std::size_t i = 0; i < want.size(); ++i) {
    EXPECT_TRUE(same_print(replay.print(i), want[i])) << "print " << i;
  }
}

TEST(DemoReplay, DepthIsBestFirstAndStopsAtTheBuffer) {
  const auto stream = itch::testing::demo_stream();
  Replay replay{stream, kSmall};
  replay.replay();
  std::array<DepthLevel, 4> out{};
  ASSERT_EQ(replay.depth(Side::bid, out), 2U);
  EXPECT_EQ(out[0].price, 999'900U);
  EXPECT_EQ(out[0].shares, 150U);
  EXPECT_EQ(out[0].orders, 1U);
  EXPECT_EQ(out[1].price, 999'800U);
  EXPECT_EQ(out[1].shares, 150U);
  ASSERT_EQ(replay.depth(Side::ask, out), 1U);
  EXPECT_EQ(out[0].price, 1'000'200U);
  EXPECT_EQ(out[0].shares, 350U);
  EXPECT_EQ(replay.depth(Side::bid, std::span<DepthLevel>{out}.first(1)), 1U);
  EXPECT_EQ(out[0].price, 999'900U);
}

TEST(DemoReplay, StepAndReplayLeaveTheSameBook) {
  const auto stream = itch::testing::demo_stream();
  Replay whole{stream, kSmall};
  whole.replay();
  Replay stepped{stream, kSmall};
  while (!stepped.done()) {
    stepped.step(1);
  }
  EXPECT_EQ(whole.digest(), stepped.digest());
  EXPECT_EQ(whole.digest(), itch::testing::kDemoStreamDigest)
      << std::hex << "set kDemoStreamDigest to 0x" << whole.digest();
}

TEST(DemoReplay, TheDigestSeesOneShareMoved) {
  auto stream = itch::testing::demo_stream();
  Replay before{stream, kSmall};
  before.replay();
  itch::fixtures::append_frame(stream, wire::cancel(itch::testing::kDemoLocate, 21, 1, 21'000));
  Replay after{stream, kSmall};
  after.replay();
  EXPECT_NE(before.digest(), after.digest());
}

TEST(DemoReplay, RefusesAnExtractThatIsNotOneSymbol) {
  auto two = itch::testing::demo_stream();
  itch::fixtures::append_frame(two, wire::add(7, 500, 'B', 100, 1'000'000, 30'000));
  EXPECT_EQ((Replay{two, kSmall}.extract_status()), ExtractStatus::many_symbols);

  auto second_directory = itch::testing::demo_stream();
  itch::fixtures::append_frame(second_directory, wire::directory(7, 'Q', 'P', 100, 30'000));
  EXPECT_EQ((Replay{second_directory, kSmall}.extract_status()), ExtractStatus::many_symbols);

  std::vector<std::byte> none;
  itch::fixtures::append_frame(none, wire::system_event('O', 1));
  EXPECT_EQ((Replay{none, kSmall}.extract_status()), ExtractStatus::no_symbol);

  auto cut = itch::testing::demo_stream();
  cut.pop_back();
  EXPECT_EQ((Replay{cut, kSmall}.extract_status()), ExtractStatus::bad_framing);
}

std::uint64_t engine_digest() {
  return (static_cast<std::uint64_t>(demo_digest_high()) << 32U) |
         static_cast<std::uint64_t>(demo_digest_low());
}

TEST(DemoEngine, TheCInterfaceRunsTheSameReplay) {
  const auto stream = itch::testing::demo_stream();
  EXPECT_EQ(demo_input(static_cast<double>(itch::demo::kMaxInputBytes) + 1.0), nullptr);
  EXPECT_EQ(demo_input(-1.0), nullptr);
  std::byte* input = demo_input(static_cast<double>(stream.size()));
  ASSERT_NE(input, nullptr);
  std::ranges::copy(stream, input);
  ASSERT_EQ(demo_load(), 0);
  EXPECT_EQ(demo_messages(), 20.0);
  EXPECT_EQ(std::string_view(demo_symbol()), "SYMR    ");
  EXPECT_EQ(demo_replay(), 20.0);
  EXPECT_EQ(demo_applied(), 20.0);
  EXPECT_EQ(demo_status(static_cast<int>(ApplyStatus::unknown_order)), 1.0);
  EXPECT_EQ(demo_status(99), 0.0);
  EXPECT_EQ(engine_digest(), itch::testing::kDemoStreamDigest);

  ASSERT_EQ(demo_load(), 0);  // A fresh book over the same input.
  EXPECT_EQ(demo_applied(), 0.0);
  while (demo_applied() < demo_messages()) {
    ASSERT_GT(demo_step(4.0), 0.0);
  }
  EXPECT_EQ(engine_digest(), itch::testing::kDemoStreamDigest);
  EXPECT_EQ(demo_prints(), 5.0);
  const Print* first = demo_print(0.0);
  ASSERT_NE(first, nullptr);
  EXPECT_EQ(first->kind, 'E');
  EXPECT_EQ(demo_print(5.0), nullptr);
  ASSERT_EQ(demo_depth(1, 8), 1);
  EXPECT_EQ(demo_depth_buffer()[0].price, 1'000'200U);
  EXPECT_EQ(demo_depth(2, 8), 0);
  EXPECT_EQ(demo_memory_bytes(), static_cast<double>(itch::demo::kEngineMemoryBytes));
}

}  // namespace
