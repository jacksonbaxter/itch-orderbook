// E11 (simd.md): every Add Order of the day decoded into a 32-byte record, on the scalar kernel
// (std::byteswap, which core WebAssembly has no instruction for) or on the SIMD128 kernel
// (constant i8x16.shuffle), in node. scripts/simd_wasm_e11.mjs copies the day into the window
// a piece at a time and times each e11_pass() with performance.now(); nothing here reads a
// clock.
//
// A pass walks the window's whole frames and returns how many bytes they took, so that the
// driver carries a frame cut by the window's end over to the next piece. Work 0 is the walk
// alone, 1 the decode; kernel 0 is scalar, 1 SIMD128. The counts and the fold add up over
// every pass since e11_reset(), so the driver can check that both kernels decoded the same.
#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

#include "itch/kernels/layout.hpp"
#include "itch/kernels/scalar.hpp"
#include "itch/kernels/wasm.hpp"
#include "kernel_experiments.hpp"

namespace {

constexpr std::size_t kWindowBytes = std::size_t{64} << 20U;

struct State {
  std::array<std::byte, kWindowBytes> window{};
  std::array<itch::kernels::AddFields, itch::bench::kernel::kRing> ring{};
  std::uint64_t messages = 0;
  std::uint64_t items = 0;
  std::uint64_t fold = 0;
};

State& state() {
  static State s;
  return s;
}

// Whole frames only: the decode pass of bench_kernels, over the bytes up to the last frame that
// ends inside the window.
std::size_t whole_frames(std::span<const std::byte> bytes) {
  std::size_t at = 0;
  while (bytes.size() - at >= 2) {
    const std::size_t length = itch::read_be<std::uint16_t>(bytes.data() + at);
    if (bytes.size() - at - 2 < length) {
      break;
    }
    at += 2 + length;
  }
  return at;
}

template <class K, bool Work>
void pass(std::span<const std::byte> bytes) {
  State& s = state();
  const itch::bench::kernel::Totals t = itch::bench::kernel::decode_pass<K, Work>(bytes, s.ring);
  s.messages += t.messages;
  s.items += t.items;
  s.fold += t.fold;
}

}  // namespace

extern "C" {

std::byte* e11_window() { return state().window.data(); }
std::uint32_t e11_window_bytes() { return static_cast<std::uint32_t>(kWindowBytes); }

void e11_reset() {
  State& s = state();
  s.messages = 0;
  s.items = 0;
  s.fold = 0;
  s.ring.fill({});
}

std::uint32_t e11_pass(std::uint32_t kernel, std::uint32_t work, std::uint32_t length) {
  const std::span<const std::byte> bytes{state().window.data(),
                                         length < kWindowBytes ? length : kWindowBytes};
  const std::span<const std::byte> whole = bytes.first(whole_frames(bytes));
  using Scalar = itch::kernels::scalar::Kernel;
  using Simd = itch::kernels::wasm::Kernel;
  if (kernel == 0) {
    work != 0 ? pass<Scalar, true>(whole) : pass<Scalar, false>(whole);
  } else {
    work != 0 ? pass<Simd, true>(whole) : pass<Simd, false>(whole);
  }
  return static_cast<std::uint32_t>(whole.size());
}

// Doubles, exact to 2^53, so that node reads them as numbers whatever the BigInt setting.
double e11_messages() { return static_cast<double>(state().messages); }
double e11_items() { return static_cast<double>(state().items); }
std::uint32_t e11_fold_high() { return static_cast<std::uint32_t>(state().fold >> 32U); }
std::uint32_t e11_fold_low() { return static_cast<std::uint32_t>(state().fold); }
}
