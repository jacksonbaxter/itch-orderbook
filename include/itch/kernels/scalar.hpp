// The scalar kernel: memcpy and std::byteswap, field by field, as the shipped parser reads.
// Every vector kernel is held to this one, byte for byte (tests/test_kernels.cpp).
//
// Each kernel is a struct of static functions with the same names, so that a loop written
// once as a template runs on any of them:
//
//   kName          the --kernel name.
//   kOverread      the most bytes past a message's end that any function may read. The
//                  parser hands a message to the kernel only when that many bytes follow it
//                  in the mapped range, and decodes it here otherwise (parse.hpp).
//   field<W>(p)    one big-endian field of W = 2, 4, 6 or 8 bytes at p (E1). A vector kernel
//                  may read 8 bytes from p, so 8 must be readable there.
//   add_fields(m)  an Add Order's fields (E2).
//   book_op(m, t)  the BookOp of a message of op type t, its whole length readable (E3, E4).
//   batch4(m, out) four Add Orders' book fields into one array a field (E5).
//   any_equal(k, list, n)  whether any of n 8-byte keys equals k, n a multiple of 8 (E9).
//
// Here every field is read and swapped on its own, which is what "scalar" means in the
// experiments. The compiler may still merge some of it into vector code; E12 reads the
// disassembly to say where it did.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string_view>

#include "itch/book_op.hpp"
#include "itch/kernels/layout.hpp"
#include "itch/messages.hpp"
#include "itch/wire.hpp"

namespace itch::kernels::scalar {

struct Kernel {
  static constexpr std::string_view kName = "scalar";
  static constexpr std::size_t kOverread = 0;

  template <std::size_t W>
  [[nodiscard]] static std::uint64_t field(const std::byte* p) noexcept {
    static_assert(W == 2 || W == 4 || W == 6 || W == 8);
    if constexpr (W == 2) {
      return read_be<std::uint16_t>(p);
    } else if constexpr (W == 4) {
      return read_be<std::uint32_t>(p);
    } else if constexpr (W == 6) {
      return read_be48(p);
    } else {
      return read_be<std::uint64_t>(p);
    }
  }

  [[nodiscard]] static AddFields add_fields(const std::byte* m) noexcept {
    return {.reference = read_be<std::uint64_t>(m + 11),
            .timestamp_ns = read_be48(m + 5),
            .shares = read_be<std::uint32_t>(m + 20),
            .price = read_be<std::uint32_t>(m + 32),
            .locate = read_be<std::uint16_t>(m + 1),
            .tracking = read_be<std::uint16_t>(m + 3),
            .side = read_char(m + 19)};
  }

  // The fields decode_book_op() takes, read where messages.hpp reads them, without its
  // length check: the parser has made that check before it calls a kernel.
  [[nodiscard]] static BookOp book_op(const std::byte* m, char type) noexcept {
    const auto locate = read_be<std::uint16_t>(m + 1);
    const auto reference = read_be<std::uint64_t>(m + 11);
    switch (type) {
      case AddOrder::kType:
      case AddOrderAttributed::kType:
        return {.reference = reference,
                .shares = read_be<std::uint32_t>(m + 20),
                .price = read_be<std::uint32_t>(m + 32),
                .locate = locate,
                .side = read_char(m + 19),
                .kind = BookOpKind::add};
      case OrderExecuted::kType:
      case OrderExecutedWithPrice::kType:
      case OrderCancel::kType:
        return {.reference = reference,
                .shares = read_be<std::uint32_t>(m + 19),
                .locate = locate,
                .kind = BookOpKind::deduct};
      case OrderDelete::kType:
        return {.reference = reference, .locate = locate, .kind = BookOpKind::erase};
      default:
        return {.reference = reference,
                .new_reference = read_be<std::uint64_t>(m + 19),
                .shares = read_be<std::uint32_t>(m + 27),
                .price = read_be<std::uint32_t>(m + 31),
                .locate = locate,
                .kind = BookOpKind::replace};
    }
  }

  static void batch4(const std::byte* const* m, AddBatch& out) noexcept {
    for (std::size_t i = 0; i < 4; ++i) {
      out.reference[i] = read_be<std::uint64_t>(m[i] + 11);
      out.shares[i] = read_be<std::uint32_t>(m[i] + 20);
      out.price[i] = read_be<std::uint32_t>(m[i] + 32);
      out.locate[i] = read_be<std::uint16_t>(m[i] + 1);
    }
  }

  // One compare a key, stopping at the first match: the loop a scalar filter is.
  [[nodiscard]] static bool any_equal(std::uint64_t key, const std::uint64_t* list,
                                      std::size_t n) noexcept {
    for (std::size_t i = 0; i < n; ++i) {
      if (list[i] == key) {
        return true;
      }
    }
    return false;
  }
};

}  // namespace itch::kernels::scalar
