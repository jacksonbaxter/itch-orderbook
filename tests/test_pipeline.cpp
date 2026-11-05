// T-SPSC-3's harness: the pipelined replay, a parser thread handing decoded ops to a book
// thread through the ring, leaves the same book as the single-threaded replay of the same
// stream, byte for byte and by the hash the benchmark compares. The decoded path makes the
// same changes as apply() on each payload, and the hash sees a single lost op. Under the
// tsan preset this is the two-thread pipeline's race check.
#include <gtest/gtest.h>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <print>
#include <span>
#include <vector>

#include "itch/book.hpp"
#include "itch/book_op.hpp"
#include "itch/reader.hpp"
#include "pipeline.hpp"
#include "support/churn.hpp"
#include "support/fixtures.hpp"

namespace {

using itch::ApplyStatus;
using itch::bench::book_hash;
using itch::bench::OpRing;
using itch::bench::ReplayCounts;

constexpr itch::BookCapacity kCapacity{
    .orders = 2048, .levels = 2048, .index_pages = 4, .index_bits = 20, .level_slots = 4096};

using Book = itch::FastBook<>;

void expect_same(const std::vector<std::byte>& stream, std::size_t ring_capacity) {
  const auto single = std::make_unique<Book>(kCapacity);
  const auto decoded = std::make_unique<Book>(kCapacity);
  const auto pipelined = std::make_unique<Book>(kCapacity);
  OpRing ring{ring_capacity};
  const ReplayCounts by_single = itch::bench::replay_single(stream, *single);
  const ReplayCounts by_decoded = itch::bench::replay_decoded(stream, *decoded);
  const ReplayCounts by_pipeline =
      itch::bench::replay_pipelined(stream, *pipelined, ring, -1, [] {});
  std::println(
      "{} messages, {} ops, {} applied; ring of {}: {} full waits, {} empty waits; hash {:016x}",
      by_single.messages, by_pipeline.ops, by_pipeline.of(ApplyStatus::applied), ring.capacity(),
      by_pipeline.full_waits, by_pipeline.empty_waits, book_hash(*pipelined));
  EXPECT_EQ(by_pipeline.messages, by_single.messages);
  EXPECT_EQ(by_decoded.messages, by_single.messages);
  EXPECT_EQ(by_pipeline.ops, by_decoded.ops);
  // Every status but no_book_effect: the decoded paths never hand the book a message it
  // does not act on.
  for (std::size_t status = 0; status < itch::bench::kStatuses; ++status) {
    if (status == static_cast<std::size_t>(ApplyStatus::no_book_effect)) {
      continue;
    }
    EXPECT_EQ(by_pipeline.by_status[status], by_single.by_status[status]) << "status " << status;
    EXPECT_EQ(by_decoded.by_status[status], by_single.by_status[status]) << "status " << status;
  }
  EXPECT_EQ(by_pipeline.of(ApplyStatus::no_book_effect), 0U);
  EXPECT_EQ(by_single.of(ApplyStatus::no_book_effect), by_single.messages - by_pipeline.ops);
  EXPECT_EQ(book_hash(*pipelined), book_hash(*single));
  EXPECT_EQ(book_hash(*decoded), book_hash(*single));
  EXPECT_TRUE(*pipelined == *single) << "the pipelined book differs in a byte the hash missed";
}

TEST(Pipeline, LeavesTheSingleThreadedBook) {
  const std::vector<std::byte> churn = itch::testing::build_stream();
  // A ring smaller than a batch, so the parser waits on a full ring again and again, and the
  // benchmark's own size.
  expect_same(churn, 16);
  expect_same(churn, itch::bench::kPipelineCapacity);
  expect_same(itch::fixtures::all_messages_stream(), 16);
}

TEST(Pipeline, DecodedOpTakesTheSamePathAsThePayload) {
  // Live orders for each message that needs one to act on.
  const std::vector<std::byte> setup = itch::testing::build_stream();
  for (const auto& message : itch::fixtures::all_messages()) {
    Book by_payload{kCapacity};
    Book by_op{kCapacity};
    static_cast<void>(itch::bench::replay_single(setup, by_payload));
    static_cast<void>(itch::bench::replay_single(setup, by_op));
    const ApplyStatus from_payload = by_payload.apply(std::span<const std::byte>{message});
    const std::optional<itch::BookOp> op = itch::decode_book_op(message);
    const ApplyStatus from_op = op ? by_op.apply(*op) : ApplyStatus::no_book_effect;
    EXPECT_EQ(from_op, from_payload) << "type " << static_cast<char>(message[0]);
    EXPECT_EQ(op.has_value(), from_payload != ApplyStatus::no_book_effect)
        << "type " << static_cast<char>(message[0]);
    EXPECT_TRUE(by_op == by_payload) << "type " << static_cast<char>(message[0]);
  }
}

TEST(Pipeline, HashSeesOneLostOp) {
  // The stream's last op, which changes a live order: a lost op earlier on can be
  // overtaken by the churn, which deletes most orders it touches.
  const std::vector<std::byte> churn = itch::testing::build_stream();
  Book whole{kCapacity};
  Book short_one{kCapacity};
  const ReplayCounts counts = itch::bench::replay_decoded(churn, whole);
  std::uint64_t seen = 0;
  itch::FrameReader reader{churn};
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    const std::optional<itch::BookOp> op = itch::decode_book_op(frame.payload);
    if (op && ++seen != counts.ops) {
      static_cast<void>(short_one.apply(*op));
    }
  }
  EXPECT_EQ(seen, counts.ops);
  EXPECT_NE(book_hash(short_one), book_hash(whole));
}

}  // namespace
