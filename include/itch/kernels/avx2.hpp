// The AVX2 kernel, for x86-64. Built only into x86-64 builds, every function under
// target("avx2"), and run only where __builtin_cpu_supports("avx2") says so (kernels.hpp).
//
// VPSHUFB shuffles within each 128-bit lane, so each window is loaded into both lanes with
// VBROADCASTI128 and one VPSHUFB fills its bytes of both halves at once: an Add Order's BookOp
// is three broadcast loads, three VPSHUFBs and two ORs, all 32 bytes in one register. No load
// passes a message's end.
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

namespace itch::kernels::avx2 {

namespace detail {

inline constexpr std::uint8_t kNone = 0x80;

// Window w's index for both halves: the low lane fills output bytes 0 to 15, the high lane
// 16 to 31, each from its own copy of the window.
template <const Gather& G, std::size_t W>
inline constexpr std::array<std::uint8_t, kRecord> kIndex = [] {
  std::array<std::uint8_t, kRecord> index{};
  const auto low = window_index(G, 0, W, kNone);
  const auto high = window_index(G, 1, W, kNone);
  for (std::size_t i = 0; i < kWindow; ++i) {
    index[i] = low[i];
    index[kWindow + i] = high[i];
  }
  return index;
}();

inline constexpr std::array<std::uint8_t, kRecord> kReverse64{
    7, 6, 5, 4, 3, 2, 1, 0, 15, 14, 13, 12, 11, 10, 9, 8,
    7, 6, 5, 4, 3, 2, 1, 0, 15, 14, 13, 12, 11, 10, 9, 8};
inline constexpr std::array<std::uint8_t, kWindow> kReverse32{3,  2,  1, 0, 7,  6,  5,  4,
                                                              11, 10, 9, 8, 15, 14, 13, 12};
inline constexpr std::array<std::uint8_t, kWindow> kReverse16{1, 0, 3,  2,  5,  4,  7,  6,
                                                              9, 8, 11, 10, 13, 12, 15, 14};

[[gnu::target("avx2")]] [[nodiscard]] inline __m128i load16(const void* p) noexcept {
  __m128i v{};
  std::memcpy(&v, p, sizeof(v));
  return v;
}

[[gnu::target("avx2")]] [[nodiscard]] inline __m256i load32(const void* p) noexcept {
  __m256i v{};
  std::memcpy(&v, p, sizeof(v));
  return v;
}

template <const Gather& G, std::size_t W>
[[gnu::target("avx2")]] [[nodiscard]] inline __m256i part(const std::byte* m,
                                                         __m256i acc) noexcept {
  if constexpr (W < G.windows && (G.uses(0, W) || G.uses(1, W))) {
    const __m256i both = _mm256_broadcastsi128_si256(load16(m + G.anchor[W]));
    return _mm256_or_si256(acc, _mm256_shuffle_epi8(both, load32(kIndex<G, W>.data())));
  } else {
    return acc;
  }
}

template <const Gather& G, class Record>
[[gnu::target("avx2")]] [[nodiscard]] inline Record gather(const std::byte* m) noexcept {
  const __m256i all = part<G, 2>(m, part<G, 1>(m, part<G, 0>(m, _mm256_setzero_si256())));
  Record out{};
  std::memcpy(&out, &all, kRecord);
  return out;
}

template <const Gather& G>
[[gnu::target("avx2")]] [[nodiscard]] inline BookOp op(const std::byte* m) noexcept {
  BookOp out = gather<G, BookOp>(m);
  out.kind = static_cast<BookOpKind>(G.kind);
  return out;
}

}  // namespace detail

struct Kernel {
  static constexpr std::string_view kName = "avx2";
  static constexpr std::size_t kOverread = 0;

  template <std::size_t W>
  [[gnu::target("avx2")]] [[nodiscard]] static std::uint64_t field(const std::byte* p) noexcept {
    static_assert(W == 2 || W == 4 || W == 6 || W == 8);
    std::uint64_t raw = 0;
    std::memcpy(&raw, p, sizeof(raw));
    const __m128i v = _mm_cvtsi64_si128(static_cast<long long>(raw));
    const __m128i swapped = _mm_shuffle_epi8(v, detail::load16(detail::kReverse64.data()));
    return static_cast<std::uint64_t>(_mm_cvtsi128_si64(swapped)) >> (64 - (8 * W));
  }

  [[gnu::target("avx2")]] [[nodiscard]] static AddFields add_fields(const std::byte* m) noexcept {
    return detail::gather<kAddFields, AddFields>(m);
  }

  [[gnu::target("avx2")]] [[nodiscard]] static BookOp book_op(const std::byte* m,
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

  [[gnu::target("avx2")]] static void batch4(const std::byte* const* m, AddBatch& out) noexcept {
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
    const __m256i ref =
        _mm256_shuffle_epi8(detail::load32(reference.data()), detail::load32(detail::kReverse64.data()));
    const __m128i r32 = detail::load16(detail::kReverse32.data());
    const __m128i s = _mm_shuffle_epi8(detail::load16(shares.data()), r32);
    const __m128i p = _mm_shuffle_epi8(detail::load16(price.data()), r32);
    const __m128i l =
        _mm_shuffle_epi8(detail::load16(locate.data()), detail::load16(detail::kReverse16.data()));
    std::memcpy(out.reference.data(), &ref, sizeof(ref));
    std::memcpy(out.shares.data(), &s, sizeof(s));
    std::memcpy(out.price.data(), &p, sizeof(p));
    std::memcpy(out.locate.data(), &l, sizeof(out.locate));
  }

  [[gnu::target("avx2")]] [[nodiscard]] static bool any_equal(std::uint64_t key,
                                                             const std::uint64_t* list,
                                                             std::size_t n) noexcept {
    const __m256i k = _mm256_set1_epi64x(static_cast<long long>(key));
    __m256i hits = _mm256_setzero_si256();
    for (std::size_t i = 0; i < n; i += 8) {
      hits = _mm256_or_si256(hits, _mm256_cmpeq_epi64(detail::load32(list + i), k));
      hits = _mm256_or_si256(hits, _mm256_cmpeq_epi64(detail::load32(list + i + 4), k));
    }
    return _mm256_testz_si256(hits, hits) == 0;
  }
};

}  // namespace itch::kernels::avx2
