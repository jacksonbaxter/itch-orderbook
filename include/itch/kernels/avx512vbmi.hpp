// The AVX-512 VBMI kernel, for x86-64. Built only into x86-64 builds, every function under
// target("avx512f,avx512bw,avx512vl,avx512vbmi"), and run only where __builtin_cpu_supports
// finds all four (kernels.hpp). Neither this Mac nor debian (Zen 1) has it, and GitHub's
// x86-64 runners may not, so its tests skip, and say so, wherever it cannot run.
//
// VPERMB picks any of 64 bytes for each output byte, so a whole record is one 64-byte load and
// one VPERMB with a zeroing mask, whatever the type: no windows and no lane problem. The load
// is always 64 bytes from the message's first byte, so it reads past the end of every message
// the kernel takes, by 64 - 19 = 45 bytes at most, after an Order Delete. kOverread says so,
// and the parser decodes with the scalar kernel any message with fewer bytes after it.
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

namespace itch::kernels::avx512vbmi {

namespace detail {

inline constexpr std::size_t kLoad = 64;

template <const Gather& G>
inline constexpr std::array<std::uint8_t, kLoad> kIndex = [] {
  std::array<std::uint8_t, kLoad> index{};
  for (std::size_t i = 0; i < kRecord; ++i) {
    index[i] = G.from[i] == kZero ? 0 : G.from[i];
  }
  return index;
}();

// One bit an output byte, set where the byte comes from the wire.
template <const Gather& G>
inline constexpr std::uint64_t kKeep = [] {
  std::uint64_t keep = 0;
  for (std::size_t i = 0; i < kRecord; ++i) {
    keep |= G.from[i] == kZero ? 0 : (std::uint64_t{1} << i);
  }
  return keep;
}();

inline constexpr std::array<std::uint8_t, kRecord> kReverse64{
    7, 6, 5, 4, 3, 2, 1, 0, 15, 14, 13, 12, 11, 10, 9, 8,
    7, 6, 5, 4, 3, 2, 1, 0, 15, 14, 13, 12, 11, 10, 9, 8};
inline constexpr std::array<std::uint8_t, kWindow> kReverse32{3,  2,  1, 0, 7,  6,  5,  4,
                                                              11, 10, 9, 8, 15, 14, 13, 12};
inline constexpr std::array<std::uint8_t, kWindow> kReverse16{1, 0, 3,  2,  5,  4,  7,  6,
                                                              9, 8, 11, 10, 13, 12, 15, 14};

[[gnu::target("avx512f,avx512bw,avx512vl,avx512vbmi")]] [[nodiscard]] inline __m128i load16(
    const void* p) noexcept {
  __m128i v{};
  std::memcpy(&v, p, sizeof(v));
  return v;
}

[[gnu::target("avx512f,avx512bw,avx512vl,avx512vbmi")]] [[nodiscard]] inline __m256i load32(
    const void* p) noexcept {
  __m256i v{};
  std::memcpy(&v, p, sizeof(v));
  return v;
}

[[gnu::target("avx512f,avx512bw,avx512vl,avx512vbmi")]] [[nodiscard]] inline __m512i load64(
    const void* p) noexcept {
  __m512i v{};
  std::memcpy(&v, p, sizeof(v));
  return v;
}

template <const Gather& G, class Record>
[[gnu::target("avx512f,avx512bw,avx512vl,avx512vbmi")]] [[nodiscard]] inline Record gather(
    const std::byte* m) noexcept {
  const __m512i all = _mm512_maskz_permutexvar_epi8(kKeep<G>, load64(kIndex<G>.data()), load64(m));
  const __m256i low = _mm512_castsi512_si256(all);
  Record out{};
  std::memcpy(&out, &low, kRecord);
  return out;
}

template <const Gather& G>
[[gnu::target("avx512f,avx512bw,avx512vl,avx512vbmi")]] [[nodiscard]] inline BookOp op(
    const std::byte* m) noexcept {
  BookOp out = gather<G, BookOp>(m);
  out.kind = static_cast<BookOpKind>(G.kind);
  return out;
}

}  // namespace detail

struct Kernel {
  static constexpr std::string_view kName = "avx512vbmi";
  static constexpr std::size_t kOverread = detail::kLoad - kDeleteOp.length;

  template <std::size_t W>
  [[gnu::target("avx512f,avx512bw,avx512vl,avx512vbmi")]] [[nodiscard]] static std::uint64_t field(
      const std::byte* p) noexcept {
    static_assert(W == 2 || W == 4 || W == 6 || W == 8);
    std::uint64_t raw = 0;
    std::memcpy(&raw, p, sizeof(raw));
    const __m128i v = _mm_cvtsi64_si128(static_cast<long long>(raw));
    const __m128i swapped = _mm_shuffle_epi8(v, detail::load16(detail::kReverse64.data()));
    return static_cast<std::uint64_t>(_mm_cvtsi128_si64(swapped)) >> (64 - (8 * W));
  }

  [[gnu::target("avx512f,avx512bw,avx512vl,avx512vbmi")]] [[nodiscard]] static AddFields add_fields(
      const std::byte* m) noexcept {
    return detail::gather<kAddFields, AddFields>(m);
  }

  [[gnu::target("avx512f,avx512bw,avx512vl,avx512vbmi")]] [[nodiscard]] static BookOp book_op(
      const std::byte* m, char type) noexcept {
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

  [[gnu::target("avx512f,avx512bw,avx512vl,avx512vbmi")]] static void batch4(
      const std::byte* const* m, AddBatch& out) noexcept {
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
    const __m256i ref = _mm256_shuffle_epi8(detail::load32(reference.data()),
                                            detail::load32(detail::kReverse64.data()));
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

  // Eight keys a compare, into a mask register.
  [[gnu::target("avx512f,avx512bw,avx512vl,avx512vbmi")]] [[nodiscard]] static bool any_equal(
      std::uint64_t key, const std::uint64_t* list, std::size_t n) noexcept {
    const __m512i k = _mm512_set1_epi64(static_cast<long long>(key));
    __mmask8 hits = 0;
    for (std::size_t i = 0; i < n; i += 8) {
      hits = static_cast<__mmask8>(hits | _mm512_cmpeq_epi64_mask(detail::load64(list + i), k));
    }
    return hits != 0;
  }
};

}  // namespace itch::kernels::avx512vbmi
