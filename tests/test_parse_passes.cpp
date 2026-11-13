// bench_parse's three passes (bench/parse_passes.hpp): the framing walk counts every frame,
// the decode folds what itch-book's Checksum handler folds for its ten types and nothing
// else, and the strict decode reads every byte of every one of the 23 types.
//
// The decode is held to a plain reference written here from the spec's offsets, with no
// decoder: the bytes at each field's offset, read big-endian. A pass that skips a type,
// reads a field at the wrong place, or folds nothing gives another sum.
#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <span>
#include <string>
#include <vector>

#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "parse_passes.hpp"
#include "support/fixtures.hpp"

namespace {

using itch::fixtures::append_frame;
using itch::fixtures::Bytes;
namespace parse = itch::bench::parse;

// A big-endian unsigned field of `width` bytes at `offset`, read a byte at a time.
std::uint64_t field(std::span<const std::byte> m, std::size_t offset, std::size_t width) {
  std::uint64_t value = 0;
  for (std::size_t i = 0; i < width; ++i) {
    value = (value << 8U) | std::to_integer<std::uint64_t>(m[offset + i]);
  }
  return value;
}

struct Field {
  std::size_t offset;
  std::size_t width;
};

Field at(std::size_t offset, std::size_t width) { return {.offset = offset, .width = width}; }

// The fields itch-book's Checksum handler adds, by type, at the spec's offsets: S its event
// code, R its round lot size, A and F reference, shares and price, E reference, shares and
// match, C reference and execution price, X reference and cancelled shares, D reference, U
// both references and price, P shares and match.
std::vector<Field> folded_fields(char type) {
  switch (type) {
    case 'S': return {at(11, 1)};
    case 'R': return {at(21, 4)};
    case 'A':
    case 'F': return {at(11, 8), at(20, 4), at(32, 4)};
    case 'E': return {at(11, 8), at(19, 4), at(23, 8)};
    case 'C': return {at(11, 8), at(32, 4)};
    case 'X': return {at(11, 8), at(19, 4)};
    case 'D': return {at(11, 8)};
    case 'U': return {at(11, 8), at(19, 8), at(31, 4)};
    case 'P': return {at(20, 4), at(36, 8)};
    default: return {};
  }
}

constexpr std::array<char, 10> kTenTypes{'S', 'R', 'A', 'F', 'E', 'C', 'X', 'D', 'U', 'P'};

// The plain reference for the decode: what it must fold and how many it must decode.
struct Reference {
  std::uint64_t fold = 0;
  std::uint64_t decoded = 0;
  std::uint64_t frames = 0;
};

Reference reference_of(std::span<const std::byte> stream) {
  Reference ref;
  itch::FrameReader reader{stream};
  for (auto frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    ++ref.frames;
    if (frame.status != itch::FrameStatus::message) {
      continue;
    }
    const char type = static_cast<char>(frame.payload[0]);
    if (std::ranges::find(kTenTypes, type) == kTenTypes.end()) {
      continue;
    }
    ++ref.decoded;
    for (const Field& f : folded_fields(type)) {
      ref.fold += field(frame.payload, f.offset, f.width);
    }
  }
  return ref;
}

Bytes framed(const Bytes& payload) {
  Bytes stream;
  append_frame(stream, payload);
  return stream;
}

Bytes read_fixture(const std::string& name) {
  std::ifstream in{std::string{ITCH_ORACLE_FIXTURES} + "/" + name, std::ios::binary};
  const std::vector<char> raw{std::istreambuf_iterator<char>{in}, {}};
  Bytes bytes(raw.size());
  for (std::size_t i = 0; i < raw.size(); ++i) {
    bytes[i] = static_cast<std::byte>(raw[i]);
  }
  return bytes;
}

TEST(ParsePasses, TheWalkCountsEveryFrameToTheEnd) {
  Bytes stream = itch::fixtures::all_messages_stream();
  append_frame(stream, Bytes{});                              // zero length
  append_frame(stream, Bytes{std::byte{'Z'}, std::byte{0}});  // unknown type
  append_frame(stream, Bytes(5, std::byte{'A'}));             // Add Order, wrong length
  const parse::Totals walk = parse::frame_walk(stream);
  EXPECT_EQ(walk.messages, 23U + 1U + 3U);
  EXPECT_EQ(walk.stopped_at, stream.size());
  EXPECT_EQ(walk.decoded, 0U);
  EXPECT_EQ(walk.fold, 0U);
}

TEST(ParsePasses, TheWalkStopsAtATruncatedFrame) {
  const Bytes whole = itch::fixtures::all_messages_stream();
  const parse::Totals full = parse::frame_walk(whole);
  const std::span<const std::byte> cut{whole.data(), whole.size() - 3};
  const parse::Totals walk = parse::frame_walk(cut);
  EXPECT_EQ(walk.messages, full.messages - 1);
  EXPECT_EQ(walk.stopped_at, whole.size() - 2 - itch::fixtures::kEndOfMessages.size());
}

TEST(ParsePasses, EveryPassWalksASessionAsTheFrameReaderDoes) {
  const Bytes session = read_fixture("session.itch");
  ASSERT_FALSE(session.empty());
  const Reference ref = reference_of(session);
  for (const parse::Totals& totals :
       {parse::frame_walk(session), parse::book_decode(session), parse::strict_decode(session)}) {
    EXPECT_EQ(totals.messages, ref.frames);
    EXPECT_EQ(totals.stopped_at, session.size());
  }
}

TEST(ParsePasses, TheDecodeFoldsItchBooksFieldsOfEachOfItsTenTypes) {
  for (const Bytes& message : itch::fixtures::all_messages()) {
    const char type = static_cast<char>(message[0]);
    SCOPED_TRACE(std::string{"type "} + type);
    const Bytes stream = framed(message);
    const Reference ref = reference_of(stream);
    const parse::Totals decode = parse::book_decode(stream);
    EXPECT_EQ(decode.messages, 1U);
    EXPECT_EQ(decode.decoded, ref.decoded);
    EXPECT_EQ(decode.fold, ref.fold);
    const bool ten = std::ranges::find(kTenTypes, type) != kTenTypes.end();
    EXPECT_EQ(decode.decoded, ten ? 1U : 0U);
    if (ten) {
      EXPECT_NE(decode.fold, 0U);
    }
  }
}

TEST(ParsePasses, TheDecodeMatchesThePlainReferenceOnASession) {
  for (const Bytes& stream : {itch::fixtures::all_messages_stream(), read_fixture("session.itch"),
                              read_fixture("lifecycle.itch")}) {
    const Reference ref = reference_of(stream);
    const parse::Totals decode = parse::book_decode(stream);
    EXPECT_EQ(decode.decoded, ref.decoded);
    EXPECT_EQ(decode.fold, ref.fold);
    EXPECT_GT(ref.decoded, 0U);
  }
}

TEST(ParsePasses, TheDecodeReadsEveryByteOfEachFieldItFolds) {
  for (const Bytes& message : itch::fixtures::all_messages()) {
    const char type = static_cast<char>(message[0]);
    const parse::Totals clean = parse::book_decode(framed(message));
    for (const Field& f : folded_fields(type)) {
      for (std::size_t at = f.offset; at < f.offset + f.width; ++at) {
        SCOPED_TRACE(std::string{"type "} + type + ", byte " + std::to_string(at));
        Bytes changed = message;
        changed[at] ^= std::byte{0x5A};
        EXPECT_NE(parse::book_decode(framed(changed)).fold, clean.fold);
      }
    }
  }
}

TEST(ParsePasses, TheDecodeRejectsWhatItsLengthCheckRefuses) {
  Bytes stream;
  append_frame(stream, Bytes{});
  append_frame(stream, Bytes{std::byte{'Z'}, std::byte{0}});
  append_frame(stream, Bytes(5, std::byte{'A'}));
  const parse::Totals decode = parse::book_decode(stream);
  EXPECT_EQ(decode.messages, 3U);
  EXPECT_EQ(decode.rejected, 3U);
  EXPECT_EQ(decode.decoded, 0U);
  EXPECT_EQ(decode.fold, 0U);
}

TEST(ParsePasses, TheStrictDecodeReadsEveryByteOfEveryType) {
  for (const Bytes& message : itch::fixtures::all_messages()) {
    const parse::Totals clean = parse::strict_decode(framed(message));
    ASSERT_EQ(clean.decoded, 1U);
    ASSERT_EQ(clean.rejected, 0U);
    for (std::size_t at = 1; at < message.size(); ++at) {
      SCOPED_TRACE(std::string{"type "} + static_cast<char>(message[0]) + ", byte " +
                   std::to_string(at));
      Bytes changed = message;
      changed[at] ^= std::byte{0x5A};
      const parse::Totals strict = parse::strict_decode(framed(changed));
      EXPECT_EQ(strict.decoded, 1U);
      EXPECT_NE(strict.fold, clean.fold);
    }
  }
}

TEST(ParsePasses, TheStrictDecodeSumsTheSystemEventsFieldsByHand) {
  // kSystemEvent: type 'S' 0x53, locate 0, tracking 0x1234, timestamp 0x000102030405,
  // event code 'O' 0x4F.
  const parse::Totals strict =
      parse::strict_decode(framed(itch::fixtures::to_bytes(itch::fixtures::kSystemEvent)));
  EXPECT_EQ(strict.fold, 0x53U + 0x1234U + 0x000102030405ULL + 0x4FU);
}

TEST(ParsePasses, TheStrictDecodeRejectsWhatDecodeRefuses) {
  Bytes stream = itch::fixtures::all_messages_stream();
  append_frame(stream, Bytes{});
  append_frame(stream, Bytes{std::byte{'Z'}, std::byte{0}});
  append_frame(stream, Bytes(5, std::byte{'A'}));
  const parse::Totals strict = parse::strict_decode(stream);
  EXPECT_EQ(strict.messages, 27U);
  EXPECT_EQ(strict.decoded, 24U);
  EXPECT_EQ(strict.rejected, 3U);
}

}  // namespace
