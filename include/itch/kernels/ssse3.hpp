// The SSSE3 kernel, for x86-64. Built only into x86-64 builds (cmake/ItchKernels.cmake), and
// every function carries target("ssse3"), so the build needs no -mssse3 and a CPU without it
// never runs a byte of this unless asked to by name, which kernels.hpp refuses.
//
// PSHUFB reads one 16-byte register, so each output half is one PSHUFB for each window it
// draws on, ORed together: an Add Order's BookOp is three loads and four PSHUFBs. SSSE3 has
// no 64-bit compare, so any_equal pairs two 32-bit compares. No load passes a message's end.
#pragma once

#include <immintrin.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string_view>

#include "itch/book_op.hpp"
#include "itch/kernels/layout.hpp"
#include "itch/messages.hpp"

namespace itch::kernels::ssse3 {

namespace detail {

inline constexpr std::uint8_t kNone = 0x80;

template <const Gather& G, std::size_t H, std::size_t W>
inline constexpr std::array<std::uint8_t, kWindow> kIndex = window_index(G, H, W, kNone);

inline constexpr std::array<std::uint8_t, kWindow> kReverse64{7,  6,  5,  4,  3,  2,  1, 0,
                                                              15, 14, 13, 12, 11, 10, 9, 8};
inline constexpr std::array<std::uint8_t, kWindow> kReverse32{3,  2,  1, 0, 7,  6,  5,  4,
                                                              11, 10, 9, 8, 15, 14, 13, 12};
inline constexpr std::array<std::uint8_t, kWindow> kReverse16{1, 0, 3,  2,  5,  4,  7,  6,
                                                              9, 8, 11, 10, 13, 12, 15, 14};

[[gnu::target("ssse3")]] [[nodiscard]] inline __m128i load16(const void* p) noexcept {
  __m128i v{};
  std::memcpy(&v, p, sizeof(v));
  return v;
}

// Each half loads its own windows, as avx2's part() does: the compiler merges the two loads
// of one window, and GCC drops __m128i's attributes from a std::array of it.
template <const Gather& G, std::size_t H, std::size_t W>
[[gnu::target("ssse3")]] [[nodiscard]] inline __m128i part(const std::byte* m,
                                                           __m128i acc) noexcept {
  if constexpr (W < G.windows && G.uses(H, W)) {
    return _mm_or_si128(acc,
                        _mm_shuffle_epi8(load16(m + G.anchor[W]), load16(kIndex<G, H, W>.data())));
  } else {
    return acc;
  }
}

template <const Gather& G, class Record>
[[gnu::target("ssse3")]] [[nodiscard]] inline Record gather(const std::byte* m) noexcept {
  const __m128i zero = _mm_setzero_si128();
  const __m128i low = part<G, 0, 2>(m, part<G, 0, 1>(m, part<G, 0, 0>(m, zero)));
  const __m128i high = part<G, 1, 2>(m, part<G, 1, 1>(m, part<G, 1, 0>(m, zero)));
  std::array<std::byte, kRecord> bytes{};
  std::memcpy(bytes.data(), &low, kWindow);
  std::memcpy(bytes.data() + kWindow, &high, kWindow);
  Record out{};
  std::memcpy(&out, bytes.data(), kRecord);
  return out;
}

template <const Gather& G>
[[gnu::target("ssse3")]] [[nodiscard]] inline BookOp op(const std::byte* m) noexcept {
  BookOp out = gather<G, BookOp>(m);
  out.kind = static_cast<BookOpKind>(G.kind);
  return out;
}

}  // namespace detail

struct Kernel {
  static constexpr std::string_view kName = "ssse3";
  static constexpr std::size_t kOverread = 0;

  template <std::size_t W>
  [[gnu::target("ssse3")]] [[nodiscard]] static std::uint64_t field(const std::byte* p) noexcept {
    static_assert(W == 2 || W == 4 || W == 6 || W == 8);
    std::uint64_t raw = 0;
    std::memcpy(&raw, p, sizeof(raw));
    const __m128i v = _mm_cvtsi64_si128(static_cast<long long>(raw));
    const __m128i swapped = _mm_shuffle_epi8(v, detail::load16(detail::kReverse64.data()));
    return static_cast<std::uint64_t>(_mm_cvtsi128_si64(swapped)) >> (64 - (8 * W));
  }

  [[gnu::target("ssse3")]] [[nodiscard]] static AddFields add_fields(const std::byte* m) noexcept {
    return detail::gather<kAddFields, AddFields>(m);
  }

  [[gnu::target("ssse3")]] [[nodiscard]] static BookOp book_op(const std::byte* m,
                                                               char type) noexcept {
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

  [[gnu::target("ssse3")]] static void batch4(const std::byte* const* m, AddBatch& out) noexcept {
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
    const __m128i r64 = detail::load16(detail::kReverse64.data());
    const __m128i r32 = detail::load16(detail::kReverse32.data());
    const __m128i ref01 = _mm_shuffle_epi8(detail::load16(reference.data()), r64);
    const __m128i ref23 = _mm_shuffle_epi8(detail::load16(reference.data() + 2), r64);
    const __m128i s = _mm_shuffle_epi8(detail::load16(shares.data()), r32);
    const __m128i p = _mm_shuffle_epi8(detail::load16(price.data()), r32);
    const __m128i l =
        _mm_shuffle_epi8(detail::load16(locate.data()), detail::load16(detail::kReverse16.data()));
    std::memcpy(out.reference.data(), &ref01, sizeof(ref01));
    std::memcpy(out.reference.data() + 2, &ref23, sizeof(ref23));
    std::memcpy(out.shares.data(), &s, sizeof(s));
    std::memcpy(out.price.data(), &p, sizeof(p));
    std::memcpy(out.locate.data(), &l, sizeof(out.locate));
  }

  // PCMPEQD, then each 32-bit result ANDed with its neighbour, so a lane is all ones only when
  // both halves of the key matched.
  [[gnu::target("ssse3")]] [[nodiscard]] static bool any_equal(std::uint64_t key,
                                                               const std::uint64_t* list,
                                                               std::size_t n) noexcept {
    const __m128i k = _mm_set1_epi64x(static_cast<long long>(key));
    __m128i hits = _mm_setzero_si128();
    for (std::size_t i = 0; i < n; i += 2) {
      const __m128i eq = _mm_cmpeq_epi32(detail::load16(list + i), k);
      hits = _mm_or_si128(hits, _mm_and_si128(eq, _mm_shuffle_epi32(eq, 0xB1)));
    }
    return _mm_movemask_epi8(hits) != 0;
  }
};

}  // namespace itch::kernels::ssse3
