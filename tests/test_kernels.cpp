// Every kernel this build has, held to the scalar kernel byte for byte: on random bytes, on
// every fixture stream, and flush against a guard page. Each vector kernel runs through
// itch::kernels::run(), so it runs inside its own target region, as the benchmark runs it.
//
// The kernels' tests are value-parameterised by kernel and named after it, and
// tests/CMakeLists.txt runs each kernel as its own ctest entry. A kernel this CPU cannot run,
// avx512vbmi on most machines, is GTEST_SKIP()ped with the reason, and ctest reports that
// entry as skipped, never as passed. A kernel this build does not have is not instantiated:
// the non-parameterised tests check that asking for it is an error.
#include <gtest/gtest.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <format>
#include <fstream>
#include <iterator>
#include <optional>
#include <random>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "itch/book_op.hpp"
#include "itch/kernels/layout.hpp"
#include "itch/kernels/parse.hpp"
#include "itch/kernels/select.hpp"
#include "itch/reader.hpp"
#include "support/churn.hpp"
#include "support/fixtures.hpp"
#include "support/guard_page.hpp"

namespace {

using itch::BookOp;
using itch::kernels::AddBatch;
using itch::kernels::AddFields;
using itch::kernels::KernelId;
using Scalar = itch::kernels::scalar::Kernel;
using Bytes = std::vector<std::byte>;

constexpr std::size_t kCases = 20'000;
// Room for a kernel that reads a whole 64-byte vector from a message's first byte.
constexpr std::size_t kSlot = 64;

std::string hex(const void* data, std::size_t size) {
  std::string out;
  const auto* bytes = static_cast<const unsigned char*>(data);
  for (std::size_t i = 0; i < size; ++i) {
    out += std::format("{:02x}", bytes[i]);
  }
  return out;
}

// Random messages of one type, each in its own 64-byte slot.
Bytes random_messages(char type, std::uint64_t seed) {
  std::mt19937_64 rng{seed};
  Bytes slots(kCases * kSlot);
  for (std::byte& b : slots) {
    b = static_cast<std::byte>(rng() & 0xFFU);
  }
  for (std::size_t i = 0; i < kCases; ++i) {
    slots[i * kSlot] = static_cast<std::byte>(type);
  }
  return slots;
}

Bytes read_file(const std::filesystem::path& path) {
  std::ifstream in{path, std::ios::binary};
  const std::vector<char> raw{std::istreambuf_iterator<char>{in}, {}};
  Bytes out(raw.size());
  for (std::size_t i = 0; i < raw.size(); ++i) {
    out[i] = static_cast<std::byte>(raw[i]);
  }
  return out;
}

// Every stream the tests hold kernels to: the oracle fixtures, the stream of every type, and
// the churn stream that drives every order path many times.
std::vector<std::pair<std::string, Bytes>> fixture_streams() {
  std::vector<std::pair<std::string, Bytes>> streams;
  for (const auto& entry : std::filesystem::directory_iterator{ITCH_ORACLE_FIXTURES}) {
    if (entry.path().extension() == ".itch") {
      streams.emplace_back(entry.path().filename().string(), read_file(entry.path()));
    }
  }
  streams.emplace_back("all_messages_stream", itch::fixtures::all_messages_stream());
  streams.emplace_back("churn", itch::testing::build_stream());
  return streams;
}

struct Ops {
  std::vector<BookOp> ops;
  void operator()(const BookOp& op) { ops.push_back(op); }
};

// The first difference from the scalar kernel, or nothing.
struct BookOpsMatch {
  std::span<const std::byte> slots;
  char type;
  template <class K>
  [[gnu::always_inline]] std::string operator()() const {
    for (std::size_t i = 0; i < kCases; ++i) {
      const std::byte* m = slots.data() + (i * kSlot);
      const BookOp got = K::book_op(m, type);
      const BookOp want = Scalar::book_op(m, type);
      if (!(got == want)) {
        return std::format("case {}: message {} gave {}, scalar {}", i,
                           hex(m, itch::message_length(type)), hex(&got, sizeof(got)),
                           hex(&want, sizeof(want)));
      }
    }
    return {};
  }
};

struct AddFieldsMatch {
  std::span<const std::byte> slots;
  template <class K>
  [[gnu::always_inline]] std::string operator()() const {
    for (std::size_t i = 0; i < kCases; ++i) {
      const std::byte* m = slots.data() + (i * kSlot);
      const AddFields got = K::add_fields(m);
      const AddFields want = Scalar::add_fields(m);
      if (!(got == want)) {
        return std::format("case {}: gave {}, scalar {}", i, hex(&got, sizeof(got)),
                           hex(&want, sizeof(want)));
      }
    }
    return {};
  }
};

struct FieldsMatch {
  std::span<const std::byte> bytes;
  template <class K, std::size_t W>
  [[nodiscard, gnu::always_inline]] std::string width() const {
    for (std::size_t at = 0; at + 8 <= bytes.size(); ++at) {
      const std::uint64_t got = K::template field<W>(bytes.data() + at);
      const std::uint64_t want = Scalar::field<W>(bytes.data() + at);
      if (got != want) {
        return std::format("width {} at {}: gave {:#x}, scalar {:#x}", W, at, got, want);
      }
    }
    return {};
  }
  template <class K>
  [[gnu::always_inline]] std::string operator()() const {
    return width<K, 2>() + width<K, 4>() + width<K, 6>() + width<K, 8>();
  }
};

struct BatchMatch {
  std::span<const std::byte> slots;
  template <class K>
  [[gnu::always_inline]] std::string operator()() const {
    for (std::size_t i = 0; i + 4 <= kCases; i += 4) {
      const std::array<const std::byte*, 4> m{
          slots.data() + (i * kSlot), slots.data() + ((i + 1) * kSlot),
          slots.data() + ((i + 2) * kSlot), slots.data() + ((i + 3) * kSlot)};
      AddBatch got;
      AddBatch want;
      K::batch4(m.data(), got);
      Scalar::batch4(m.data(), want);
      if (!(got == want)) {
        return std::format("batch at case {}", i);
      }
    }
    return {};
  }
};

// Keys at every place in lists of 8, 16 and 64, and keys in none of them.
struct AnyEqualMatch {
  template <class K>
  [[gnu::always_inline]] std::string operator()() const {
    // The same keys every run.
    // NOLINTNEXTLINE(cert-msc32-c,cert-msc51-cpp,bugprone-random-generator-seed)
    std::mt19937_64 rng{42};
    for (const std::size_t n : {8U, 16U, 64U}) {
      std::vector<std::uint64_t> list(n);
      for (std::uint64_t& key : list) {
        key = rng() | 1U;
      }
      for (std::size_t at = 0; at < n; ++at) {
        if (!K::any_equal(list[at], list.data(), n)) {
          return std::format("missed the key at {} of {}", at, n);
        }
        // Differs from list[at] in one half only, which a half-width compare would miss.
        const std::uint64_t near = list[at] ^ (std::uint64_t{1} << 40U);
        const bool there = Scalar::any_equal(near, list.data(), n);
        if (K::any_equal(near, list.data(), n) != there) {
          return std::format("a key one bit off the one at {} of {} gave the wrong answer", at, n);
        }
      }
      if (K::any_equal(2, list.data(), n)) {
        return std::format("found an even key in a list of {} odd ones", n);
      }
    }
    return {};
  }
};

struct StreamOps {
  std::span<const std::byte> stream;
  template <class K>
  [[gnu::always_inline]] std::pair<itch::kernels::ParseTotals, std::vector<BookOp>> operator()()
      const {
    Ops sink;
    const itch::kernels::ParseTotals totals = itch::kernels::parse_ops<K>(stream, sink);
    return {totals, std::move(sink.ops)};
  }
};

// Each op type as the final frame, flush against a guard page, through the parser on the
// kernel. A kernel load past the mapped range would fault.
struct GuardedWalk {
  std::span<const std::byte> placed;
  template <class K>
  [[gnu::always_inline]] int operator()() const {
    Ops sink;
    const itch::kernels::ParseTotals totals = itch::kernels::parse_ops<K>(placed, sink);
    Ops want;
    static_cast<void>(itch::kernels::parse_ops<Scalar>(placed, want));
    return totals.end == itch::FrameStatus::end_of_input && sink.ops == want.ops ? 0 : 1;
  }
};

class KernelTest : public ::testing::TestWithParam<KernelId> {};

// The kernel under test, or a skip that says why it cannot run here.
#define SELECT_OR_SKIP(kernel)                                             \
  const auto kernel = itch::kernels::select_kernel(to_string(GetParam())); \
  if (!(kernel)) {                                                         \
    GTEST_SKIP() << (kernel).error();                                      \
  }

TEST_P(KernelTest, BookOpsMatchScalarOnRandomBytes) {
  SELECT_OR_SKIP(kernel);
  std::uint64_t seed = 1;
  for (const char type : itch::kernels::kOpTypes) {
    const Bytes slots = random_messages(type, seed++);
    BookOpsMatch work{.slots = slots, .type = type};
    EXPECT_EQ(itch::kernels::run(*kernel, work), "") << "type " << type;
  }
}

TEST_P(KernelTest, AddFieldsMatchScalarOnRandomBytes) {
  SELECT_OR_SKIP(kernel);
  const Bytes slots = random_messages(itch::AddOrder::kType, 11);
  AddFieldsMatch work{.slots = slots};
  EXPECT_EQ(itch::kernels::run(*kernel, work), "");
}

TEST_P(KernelTest, FieldsOfEveryWidthMatchScalarAtEveryOffset) {
  SELECT_OR_SKIP(kernel);
  const Bytes bytes = random_messages(itch::AddOrder::kType, 12);
  FieldsMatch work{.bytes = std::span{bytes}.first(4096)};
  EXPECT_EQ(itch::kernels::run(*kernel, work), "");
}

TEST_P(KernelTest, BatchOfFourMatchesScalar) {
  SELECT_OR_SKIP(kernel);
  const Bytes slots = random_messages(itch::AddOrder::kType, 13);
  BatchMatch work{.slots = slots};
  EXPECT_EQ(itch::kernels::run(*kernel, work), "");
}

TEST_P(KernelTest, AnyEqualFindsEveryKeyAndNoOther) {
  SELECT_OR_SKIP(kernel);
  AnyEqualMatch work;
  EXPECT_EQ(itch::kernels::run(*kernel, work), "");
}

// The parser on the kernel gives the scalar kernel's ops, in order, on every fixture stream.
TEST_P(KernelTest, ParserMatchesScalarOnEveryFixtureStream) {
  SELECT_OR_SKIP(kernel);
  for (const auto& [name, stream] : fixture_streams()) {
    StreamOps work{.stream = stream};
    const auto [totals, ops] = itch::kernels::run(*kernel, work);
    Ops want;
    const itch::kernels::ParseTotals scalar = itch::kernels::parse_ops<Scalar>(stream, want);
    EXPECT_EQ(totals.messages, scalar.messages) << name;
    EXPECT_EQ(totals.ops, scalar.ops) << name;
    EXPECT_EQ(totals.end, scalar.end) << name;
    EXPECT_GT(totals.ops, 0U) << name;
    EXPECT_TRUE(ops == want.ops) << name;
  }
}

TEST_P(KernelTest, FinalFrameOfEveryOpTypeStaysInsideTheMappedRange) {
  SELECT_OR_SKIP(kernel);
  itch::testing::GuardedRegion region{std::size_t{1} << 16U};
  for (const auto& message : itch::fixtures::all_messages()) {
    if (!itch::kernels::is_op_type(static_cast<char>(message[0]))) {
      continue;
    }
    Bytes stream;
    itch::fixtures::append_frame(stream, itch::fixtures::to_bytes(itch::fixtures::kAddOrder));
    itch::fixtures::append_frame(stream, message);
    const auto placed = region.flush_against_end(stream);
    const auto result = itch::testing::run_in_child([&kernel, placed] {
      GuardedWalk work{.placed = placed};
      return itch::kernels::run(*kernel, work);
    });
    EXPECT_TRUE(result.clean()) << "type " << static_cast<char>(message[0]) << ": "
                                << result.describe();
  }
}

INSTANTIATE_TEST_SUITE_P(Built, KernelTest, ::testing::ValuesIn(itch::kernels::kBuilt),
                         [](const ::testing::TestParamInfo<KernelId>& instance) {
                           return std::string{to_string(instance.param)};
                         });

// A kernel that reads 64 bytes from every message's first byte, as avx512vbmi does, written
// in plain C++ so that it runs on every machine. It shows the parser handing the messages
// near the end to the scalar kernel, whichever vector kernels this machine can run.
struct OverreadingKernel {
  static constexpr std::size_t kOverread = 64 - itch::kernels::kDeleteOp.length;
  static inline std::uint64_t calls = 0;
  [[nodiscard]] static BookOp book_op(const std::byte* m, char type) noexcept {
    std::array<std::byte, 64> whole{};
    std::memcpy(whole.data(), m, whole.size());
    ++calls;
    return Scalar::book_op(whole.data(), type);
  }
};

// The scalar kernel is the oracle for every other, so it is held to the shipped decoder.
TEST(Kernels, ScalarBookOpsAreTheShippedDecoders) {
  for (const auto& [name, stream] : fixture_streams()) {
    itch::FrameReader reader{stream};
    std::vector<BookOp> shipped;
    for (auto frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
      if (frame.status == itch::FrameStatus::message) {
        if (const auto op = itch::decode_book_op(frame.payload)) {
          shipped.push_back(*op);
        }
      }
    }
    Ops sink;
    static_cast<void>(itch::kernels::parse_ops<Scalar>(stream, sink));
    EXPECT_TRUE(sink.ops == shipped) << name;
  }
  std::uint64_t seed = 100;
  for (const char type : itch::kernels::kOpTypes) {
    const Bytes slots = random_messages(type, seed++);
    for (std::size_t i = 0; i < kCases; ++i) {
      const std::span<const std::byte> m{slots.data() + (i * kSlot), itch::message_length(type)};
      const std::optional<itch::BookOp> kernel{Scalar::book_op(m.data(), type)};
      ASSERT_EQ(kernel, itch::decode_book_op(m)) << type << " " << i;
    }
  }
}

TEST(Kernels, MessagesNearTheEndGoToTheScalarKernel) {
  itch::testing::GuardedRegion region{std::size_t{1} << 16U};
  // Every type eight times, then the op types, the shortest last, so that the last few ops
  // end within kOverread bytes of the guard.
  Bytes stream;
  for (int i = 0; i < 8; ++i) {
    for (const auto& message : itch::fixtures::all_messages()) {
      itch::fixtures::append_frame(stream, message);
    }
  }
  for (const auto& message : itch::fixtures::all_messages()) {
    if (itch::kernels::is_op_type(static_cast<char>(message[0]))) {
      itch::fixtures::append_frame(stream, message);
    }
  }
  for (int i = 0; i < 3; ++i) {
    itch::fixtures::append_frame(stream, itch::fixtures::to_bytes(itch::fixtures::kOrderDelete));
  }
  const auto placed = region.flush_against_end(stream);
  // How many ops end fewer than kOverread bytes before the end, counted by walking the frames.
  std::uint64_t near_end = 0;
  itch::FrameReader reader{placed};
  for (auto frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    const auto after = static_cast<std::size_t>(placed.data() + placed.size() -
                                                (frame.payload.data() + frame.payload.size()));
    if (frame.status == itch::FrameStatus::message &&
        itch::kernels::is_op_type(itch::read_char(frame.payload.data())) &&
        after < OverreadingKernel::kOverread) {
      ++near_end;
    }
  }
  ASSERT_GT(near_end, 0U);
  const auto result = itch::testing::run_in_child([placed, near_end] {
    Ops got;
    OverreadingKernel::calls = 0;
    const auto totals = itch::kernels::parse_ops<OverreadingKernel>(placed, got);
    Ops want;
    static_cast<void>(itch::kernels::parse_ops<Scalar>(placed, want));
    int failures = 0;
    failures += totals.tail == near_end ? 0 : 1;
    failures += OverreadingKernel::calls + totals.tail == totals.ops ? 0 : 1;
    failures += got.ops == want.ops ? 0 : 1;
    return failures;
  });
  EXPECT_TRUE(result.clean()) << result.describe();
}

TEST(Kernels, AKernelThatCannotRunIsAnErrorWithItsReason) {
  for (const KernelId id : itch::kernels::kAllKernels) {
    const auto selected = itch::kernels::select_kernel(to_string(id));
    if (!itch::kernels::built(id)) {
      ASSERT_FALSE(selected.has_value()) << to_string(id);
      EXPECT_NE(selected.error().find("is not built"), std::string::npos) << selected.error();
    } else if (const auto lacks = itch::kernels::cpu_lacks(id)) {
      ASSERT_FALSE(selected.has_value()) << to_string(id);
      EXPECT_NE(selected.error().find("this CPU lacks"), std::string::npos) << selected.error();
    } else {
      ASSERT_TRUE(selected.has_value()) << selected.error();
      EXPECT_EQ(selected->id(), id);
      EXPECT_FALSE(selected->from_auto());
    }
  }
  const auto unknown = itch::kernels::select_kernel("sse9");
  ASSERT_FALSE(unknown.has_value());
  EXPECT_NE(unknown.error().find("unknown kernel"), std::string::npos);
  const auto widest = itch::kernels::select_kernel("auto");
  ASSERT_TRUE(widest.has_value());
  EXPECT_EQ(widest->id(), itch::kernels::widest());
  EXPECT_TRUE(widest->from_auto());
}

// The kernels the header builds are the ones CMake runs a ctest entry for.
TEST(Kernels, BuiltKernelsAreTheOnesCMakeTests) {
  EXPECT_EQ(itch::kernels::built_names(), ITCH_EXPECTED_KERNELS);
}

}  // namespace
