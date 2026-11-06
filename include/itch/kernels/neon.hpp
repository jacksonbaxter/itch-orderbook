// The NEON kernel, for arm64, where NEON is always present: no dispatch, no target attribute.
// Built only into arm64 builds (cmake/ItchKernels.cmake), so it includes arm_neon.h as is.
//
// A record is two TBL lookups, one for each 16-byte half, over the windows layout.hpp planned:
// TBL indexes up to three registers at once and gives zero for an index past them, so a half
// that draws on three windows is still one instruction, and the zero bytes cost nothing. An
// Add Order's BookOp is three 16-byte loads, one single-register TBL and one three-register
// TBL, and no load passes the message's end, so kOverread is 0.
#pragma once

#include <arm_neon.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string_view>

#include "itch/book_op.hpp"
#include "itch/kernels/layout.hpp"
#include "itch/messages.hpp"

namespace itch::kernels::neon {

namespace detail {

[[nodiscard]] inline uint8x16_t load16(const std::byte* p) noexcept {
  uint8x16_t v{};
  std::memcpy(&v, p, sizeof(v));
  return v;
}

[[nodiscard]] inline uint8x8_t load8(const std::byte* p) noexcept {
  uint8x8_t v{};
  std::memcpy(&v, p, sizeof(v));
  return v;
}

// clang-tidy 23 reports every use of a reference template parameter such as G as "redundant
// parentheses around expression", where the source has none; the check is off until the end
// of the plan code.
// NOLINTBEGIN(readability-redundant-parentheses)

// Output half H of plan G: which windows it reads, in the order TBL takes them, and the index
// of each of its bytes into those windows laid end to end.
template <const Gather& G, std::size_t H>
struct Half {
  struct Plan {
    std::size_t count = 0;
    std::array<std::size_t, kMostWindows> windows{};
    std::array<std::uint8_t, kWindow> index{};
  };
  static constexpr Plan kPlan = [] {
    Plan p{};
    for (std::size_t w = 0; w < G.windows; ++w) {
      if (G.uses(H, w)) {
        p.windows[p.count++] = w;
      }
    }
    for (std::size_t i = 0; i < kWindow; ++i) {
      const std::size_t out = (H * kWindow) + i;
      p.index[i] = kZero;
      for (std::size_t k = 0; k < p.count; ++k) {
        if (G.window[out] == p.windows[k]) {
          p.index[i] = static_cast<std::uint8_t>((k * kWindow) + G.lane[out]);
        }
      }
    }
    return p;
  }();
};

template <const Gather& G, std::size_t H>
[[nodiscard]] inline uint8x16_t half(const std::array<uint8x16_t, kMostWindows>& win) noexcept {
  constexpr auto& p = Half<G, H>::kPlan;
  const uint8x16_t index = vld1q_u8(p.index.data());
  if constexpr (p.count == 0) {
    return vdupq_n_u8(0);
  } else if constexpr (p.count == 1) {
    return vqtbl1q_u8(win[p.windows[0]], index);
  } else if constexpr (p.count == 2) {
    return vqtbl2q_u8(uint8x16x2_t{{win[p.windows[0]], win[p.windows[1]]}}, index);
  } else {
    return vqtbl3q_u8(uint8x16x3_t{{win[p.windows[0]], win[p.windows[1]], win[p.windows[2]]}},
                      index);
  }
}

template <const Gather& G, class Record>
[[nodiscard]] inline Record gather(const std::byte* m) noexcept {
  std::array<uint8x16_t, kMostWindows> win{};
  for (std::size_t w = 0; w < G.windows; ++w) {
    win[w] = load16(m + G.anchor[w]);
  }
  const uint8x16_t low = half<G, 0>(win);
  const uint8x16_t high = half<G, 1>(win);
  std::array<std::byte, kRecord> bytes{};
  std::memcpy(bytes.data(), &low, kWindow);
  std::memcpy(bytes.data() + kWindow, &high, kWindow);
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

// NOLINTEND(readability-redundant-parentheses)

}  // namespace detail

struct Kernel {
  static constexpr std::string_view kName = "neon";
  static constexpr std::size_t kOverread = 0;

  // One 8-byte load, REV64 and a shift: the field ends up in a general register either way.
  template <std::size_t W>
  [[nodiscard]] static std::uint64_t field(const std::byte* p) noexcept {
    static_assert(W == 2 || W == 4 || W == 6 || W == 8);
    const uint8x8_t swapped = vrev64_u8(detail::load8(p));
    return vget_lane_u64(vreinterpret_u64_u8(swapped), 0) >> (64 - (8 * W));
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

  // Each field's four wire values put side by side, then one REV for the lot.
  static void batch4(const std::byte* const* m, AddBatch& out) noexcept {
    const uint8x16_t ref01 = vcombine_u8(detail::load8(m[0] + 11), detail::load8(m[1] + 11));
    const uint8x16_t ref23 = vcombine_u8(detail::load8(m[2] + 11), detail::load8(m[3] + 11));
    std::array<std::uint32_t, 4> shares{};
    std::array<std::uint32_t, 4> price{};
    std::array<std::uint16_t, 4> locate{};
    for (std::size_t i = 0; i < 4; ++i) {
      std::memcpy(&shares[i], m[i] + 20, sizeof(std::uint32_t));
      std::memcpy(&price[i], m[i] + 32, sizeof(std::uint32_t));
      std::memcpy(&locate[i], m[i] + 1, sizeof(std::uint16_t));
    }
    vst1q_u64(out.reference.data(), vreinterpretq_u64_u8(vrev64q_u8(ref01)));
    vst1q_u64(out.reference.data() + 2, vreinterpretq_u64_u8(vrev64q_u8(ref23)));
    vst1q_u32(out.shares.data(),
              vreinterpretq_u32_u8(vrev32q_u8(vreinterpretq_u8_u32(vld1q_u32(shares.data())))));
    vst1q_u32(out.price.data(),
              vreinterpretq_u32_u8(vrev32q_u8(vreinterpretq_u8_u32(vld1q_u32(price.data())))));
    vst1_u16(out.locate.data(),
             vreinterpret_u16_u8(vrev16_u8(vreinterpret_u8_u16(vld1_u16(locate.data())))));
  }

  // Two keys a compare, eight a step, no branch until the end.
  [[nodiscard]] static bool any_equal(std::uint64_t key, const std::uint64_t* list,
                                      std::size_t n) noexcept {
    const uint64x2_t k = vdupq_n_u64(key);
    uint64x2_t hits = vdupq_n_u64(0);
    for (std::size_t i = 0; i < n; i += 8) {
      const uint64x2_t a =
          vorrq_u64(vceqq_u64(vld1q_u64(list + i), k), vceqq_u64(vld1q_u64(list + i + 2), k));
      const uint64x2_t b =
          vorrq_u64(vceqq_u64(vld1q_u64(list + i + 4), k), vceqq_u64(vld1q_u64(list + i + 6), k));
      hits = vorrq_u64(hits, vorrq_u64(a, b));
    }
    return vmaxvq_u32(vreinterpretq_u32_u64(hits)) != 0;
  }
};

}  // namespace itch::kernels::neon
