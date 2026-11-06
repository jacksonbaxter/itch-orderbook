// The WebAssembly SIMD128 kernel, written against wasm_simd128.h and no compatibility header.
// Built only into Emscripten builds, which compile it with -msimd128 (cmake/ItchKernels.cmake).
//
// Each output half is one i8x16.shuffle for each window it draws on, the window against a zero
// vector so that the lanes it does not fill come out zero, ORed together. The lanes are
// constants, which a runtime can lower to one TBL or PSHUFB; i8x16.swizzle takes its lanes at
// run time and costs more on x86 hosts. No load passes a message's end.
//
// Core WebAssembly has no byte-reverse instruction, so std::byteswap in the scalar kernel
// becomes shifts and masks here, unless the runtime spots the pattern. That is why E11 may
// come out the other way round from the native kernels.
#pragma once

#include <wasm_simd128.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string_view>

#include "itch/book_op.hpp"
#include "itch/kernels/layout.hpp"
#include "itch/messages.hpp"

namespace itch::kernels::wasm {

namespace detail {

template <const Gather& G, std::size_t H, std::size_t W>
inline constexpr std::array<std::uint8_t, kWindow> kIndex = window_index(G, H, W, kWindow);

template <const Gather& G, std::size_t H, std::size_t W>
[[nodiscard]] inline v128_t part(const std::array<v128_t, kMostWindows>& win,
                                 v128_t acc) noexcept {
  if constexpr (W < G.windows && G.uses(H, W)) {
    constexpr auto& i = kIndex<G, H, W>;
    const v128_t picked = wasm_i8x16_shuffle(win[W], wasm_i64x2_const(0, 0), i[0], i[1], i[2],
                                             i[3], i[4], i[5], i[6], i[7], i[8], i[9], i[10],
                                             i[11], i[12], i[13], i[14], i[15]);
    return wasm_v128_or(acc, picked);
  } else {
    return acc;
  }
}

template <const Gather& G, class Record>
[[nodiscard]] inline Record gather(const std::byte* m) noexcept {
  std::array<v128_t, kMostWindows> win{};
  for (std::size_t w = 0; w < G.windows; ++w) {
    win[w] = wasm_v128_load(m + G.anchor[w]);
  }
  const v128_t zero = wasm_i64x2_const(0, 0);
  const v128_t low = part<G, 0, 2>(win, part<G, 0, 1>(win, part<G, 0, 0>(win, zero)));
  const v128_t high = part<G, 1, 2>(win, part<G, 1, 1>(win, part<G, 1, 0>(win, zero)));
  std::array<std::byte, kRecord> bytes{};
  wasm_v128_store(bytes.data(), low);
  wasm_v128_store(bytes.data() + kWindow, high);
  Record out{};
  std::memcpy(&out, bytes.data(), kRecord);
  return out;
}

template <const Gather& G>
[[nodiscard]] inline BookOp op(const std::byte* m) noexcept {
  BookOp out = gather<G, BookOp>(m);
  out.kind = static_cast<BookOpKind>(G.kind);
  return out;
}

[[nodiscard]] inline v128_t reverse64(v128_t v) noexcept {
  return wasm_i8x16_shuffle(v, v, 7, 6, 5, 4, 3, 2, 1, 0, 15, 14, 13, 12, 11, 10, 9, 8);
}

[[nodiscard]] inline v128_t reverse32(v128_t v) noexcept {
  return wasm_i8x16_shuffle(v, v, 3, 2, 1, 0, 7, 6, 5, 4, 11, 10, 9, 8, 15, 14, 13, 12);
}

[[nodiscard]] inline v128_t reverse16(v128_t v) noexcept {
  return wasm_i8x16_shuffle(v, v, 1, 0, 3, 2, 5, 4, 7, 6, 9, 8, 11, 10, 13, 12, 15, 14);
}

}  // namespace detail

struct Kernel {
  static constexpr std::string_view kName = "wasm";
  static constexpr std::size_t kOverread = 0;

  template <std::size_t W>
  [[nodiscard]] static std::uint64_t field(const std::byte* p) noexcept {
    static_assert(W == 2 || W == 4 || W == 6 || W == 8);
    const v128_t swapped = detail::reverse64(wasm_v128_load64_zero(p));
    return static_cast<std::uint64_t>(wasm_i64x2_extract_lane(swapped, 0)) >> (64 - (8 * W));
  }

  [[nodiscard]] static AddFields add_fields(const std::byte* m) noexcept {
    return detail::gather<kAddFields, AddFields>(m);
  }

  [[nodiscard]] static BookOp book_op(const std::byte* m, char type) noexcept {
    switch (type) {
      case AddOrder::kType: return detail::op<kAddOp>(m);
      case AddOrderAttributed::kType: return detail::op<kAddAttributedOp>(m);
      case OrderExecuted::kType: return detail::op<kExecutedOp>(m);
      case OrderExecutedWithPrice::kType: return detail::op<kExecutedWithPriceOp>(m);
      case OrderCancel::kType: return detail::op<kCancelOp>(m);
      case OrderDelete::kType: return detail::op<kDeleteOp>(m);
      default: return detail::op<kReplaceOp>(m);
    }
  }

  static void batch4(const std::byte* const* m, AddBatch& out) noexcept {
    std::array<std::uint64_t, 4> reference{};
    std::array<std::uint32_t, 4> shares{};
    std::array<std::uint32_t, 4> price{};
    std::array<std::uint16_t, 8> locate{};
    for (std::size_t i = 0; i < 4; ++i) {
      std::memcpy(&reference[i], m[i] + 11, sizeof(std::uint64_t));
      std::memcpy(&shares[i], m[i] + 20, sizeof(std::uint32_t));
      std::memcpy(&price[i], m[i] + 32, sizeof(std::uint32_t));
      std::memcpy(&locate[i], m[i] + 1, sizeof(std::uint16_t));
    }
    wasm_v128_store(out.reference.data(), detail::reverse64(wasm_v128_load(reference.data())));
    wasm_v128_store(out.reference.data() + 2,
                    detail::reverse64(wasm_v128_load(reference.data() + 2)));
    wasm_v128_store(out.shares.data(), detail::reverse32(wasm_v128_load(shares.data())));
    wasm_v128_store(out.price.data(), detail::reverse32(wasm_v128_load(price.data())));
    wasm_v128_store64_lane(out.locate.data(), detail::reverse16(wasm_v128_load(locate.data())), 0);
  }

  [[nodiscard]] static bool any_equal(std::uint64_t key, const std::uint64_t* list,
                                      std::size_t n) noexcept {
    const v128_t k = wasm_i64x2_splat(static_cast<std::int64_t>(key));
    v128_t hits = wasm_i64x2_const(0, 0);
    for (std::size_t i = 0; i < n; i += 2) {
      hits = wasm_v128_or(hits, wasm_i64x2_eq(wasm_v128_load(list + i), k));
    }
    return wasm_v128_any_true(hits);
  }
};

}  // namespace itch::kernels::wasm
