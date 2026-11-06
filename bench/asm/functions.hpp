// E12 (simd.md): each kernel's decode as the compiler emits it, alone, one extern "C" function
// an operation, for scripts/simd_asm_audit.py to compile with -S under each compiler and flag
// set and count. Not built by CMake. ATTRIBUTE is the kernel's target region, so that the
// kernel inlines into the function as it inlines into a pass.
#pragma once

#include <cstddef>
#include <cstdint>

#include "itch/book_op.hpp"
#include "itch/kernels/layout.hpp"

// NOLINTBEGIN(cppcoreguidelines-macro-usage)
#define ITCH_E12_FUNCTIONS(PREFIX, KERNEL, ATTRIBUTE)                                              \
  extern "C" ATTRIBUTE void PREFIX##_add_fields(const std::byte* m,                                \
                                                itch::kernels::AddFields* out) noexcept {          \
    *out = KERNEL::add_fields(m);                                                                  \
  }                                                                                                \
  extern "C" ATTRIBUTE void PREFIX##_book_op_add(const std::byte* m, itch::BookOp* out) noexcept { \
    *out = KERNEL::book_op(m, 'A');                                                                \
  }                                                                                                \
  extern "C" ATTRIBUTE std::uint64_t PREFIX##_field6(const std::byte* p) noexcept {                \
    return KERNEL::template field<6>(p);                                                           \
  }                                                                                                \
  extern "C" ATTRIBUTE void PREFIX##_batch4(const std::byte* const* m,                             \
                                            itch::kernels::AddBatch* out) noexcept {               \
    KERNEL::batch4(m, *out);                                                                       \
  }
// NOLINTEND(cppcoreguidelines-macro-usage)
