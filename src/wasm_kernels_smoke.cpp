// Emscripten only: the SIMD128 kernel held to the scalar kernel in node, on random messages of
// every op type and on the stream of every type, so that em++ has compiled and run wasm.hpp
// under wasm32 and not only included it. tests/test_kernels.cpp does the same for the native
// kernels, which this build does not have.
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <vector>

#include "itch/kernels/layout.hpp"
#include "itch/kernels/parse.hpp"
#include "itch/kernels/scalar.hpp"
#include "itch/kernels/wasm.hpp"
#include "support/churn.hpp"
#include "support/fixtures.hpp"

namespace {

using Scalar = itch::kernels::scalar::Kernel;
using Simd = itch::kernels::wasm::Kernel;

constexpr std::size_t kCases = 20'000;
constexpr std::size_t kSlot = 64;

struct Ops {
  std::vector<itch::BookOp> ops;
  void operator()(const itch::BookOp& op) { ops.push_back(op); }
};

// xorshift64, so that the bytes are the same on every run without <random>'s engines.
std::uint64_t next(std::uint64_t& state) {
  state ^= state << 13U;
  state ^= state >> 7U;
  state ^= state << 17U;
  return state;
}

int random_messages() {
  int failures = 0;
  std::uint64_t state = 0x9E3779B97F4A7C15ULL;
  std::vector<std::byte> slots(kCases * kSlot);
  for (const char type : itch::kernels::kOpTypes) {
    for (std::byte& b : slots) {
      b = static_cast<std::byte>(next(state) & 0xFFU);
    }
    for (std::size_t i = 0; i < kCases; ++i) {
      std::byte* m = slots.data() + (i * kSlot);
      *m = static_cast<std::byte>(type);
      failures += Simd::book_op(m, type) == Scalar::book_op(m, type) ? 0 : 1;
      if (type == itch::AddOrder::kType) {
        failures += Simd::add_fields(m) == Scalar::add_fields(m) ? 0 : 1;
      }
      failures += Simd::field<2>(m) == Scalar::field<2>(m) ? 0 : 1;
      failures += Simd::field<4>(m + 3) == Scalar::field<4>(m + 3) ? 0 : 1;
      failures += Simd::field<6>(m + 5) == Scalar::field<6>(m + 5) ? 0 : 1;
      failures += Simd::field<8>(m + 11) == Scalar::field<8>(m + 11) ? 0 : 1;
    }
    for (std::size_t i = 0; i + 4 <= kCases; i += 4) {
      const std::array<const std::byte*, 4> m{
          slots.data() + (i * kSlot), slots.data() + ((i + 1) * kSlot),
          slots.data() + ((i + 2) * kSlot), slots.data() + ((i + 3) * kSlot)};
      itch::kernels::AddBatch got;
      itch::kernels::AddBatch want;
      Simd::batch4(m.data(), got);
      Scalar::batch4(m.data(), want);
      failures += got == want ? 0 : 1;
    }
  }
  std::vector<std::uint64_t> list(64);
  for (std::uint64_t& key : list) {
    key = next(state) | 1U;
  }
  for (std::size_t at = 0; at < list.size(); ++at) {
    failures += Simd::any_equal(list[at], list.data(), list.size()) ? 0 : 1;
  }
  failures += Simd::any_equal(2, list.data(), list.size()) ? 1 : 0;
  return failures;
}

int streams() {
  int failures = 0;
  for (const auto& stream : {itch::fixtures::all_messages_stream(), itch::testing::build_stream()}) {
    Ops got;
    Ops want;
    const auto simd = itch::kernels::parse_ops<Simd>(stream, got);
    const auto scalar = itch::kernels::parse_ops<Scalar>(stream, want);
    failures += simd.ops == scalar.ops && simd.ops != 0 && got.ops == want.ops ? 0 : 1;
  }
  return failures;
}

}  // namespace

int main() {
  const int random = random_messages();
  const int framed = streams();
  std::printf("wasm kernels smoke: %d mismatches on random messages, %d on streams\n", random,
              framed);
  return random == 0 && framed == 0 ? 0 : 1;
}
