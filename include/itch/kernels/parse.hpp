// The parser on a kernel: the frames walked as FrameReader walks them, and every message the
// book acts on turned into a BookOp by the kernel and handed to a sink.
//
// A kernel may read up to Kernel::kOverread bytes past a message's end. Those bytes are only
// there to read if the mapped range goes on that far, so a message with fewer bytes after it
// is decoded by the scalar kernel instead, which reads exactly the message. For a kernel whose
// kOverread is 0 the test folds away.
//
// always_inline, so that called from a kernel's target region (kernels_built.hpp) the walk and
// the sink are compiled into that region with the kernel.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

#include "itch/book_op.hpp"
#include "itch/kernels/layout.hpp"
#include "itch/kernels/scalar.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"

namespace itch::kernels {

struct ParseTotals {
  std::uint64_t messages = 0;  // Frames that were whole messages of a known type.
  std::uint64_t ops = 0;       // Of those, messages the book acts on.
  std::uint64_t tail = 0;      // Of those, ops the scalar kernel took near the end.
  FrameStatus end = FrameStatus::end_of_input;
};

inline constexpr std::array<bool, 256> kIsOpType = [] {
  std::array<bool, 256> table{};
  for (const char type : kOpTypes) {
    table[static_cast<unsigned char>(type)] = true;
  }
  return table;
}();

template <class Kernel, class Sink>
[[gnu::always_inline]] inline ParseTotals parse_ops(std::span<const std::byte> bytes,
                                                    Sink& sink) noexcept {
  FrameReader reader{bytes};
  ParseTotals totals;
  const std::byte* const end = bytes.data() + bytes.size();
  Frame frame = reader.next();
  for (; !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != FrameStatus::message) {
      continue;
    }
    ++totals.messages;
    const std::byte* const m = frame.payload.data();
    const char type = read_char(m);
    if (!kIsOpType[static_cast<unsigned char>(type)]) {
      continue;
    }
    ++totals.ops;
    const auto after = static_cast<std::size_t>(end - (m + frame.payload.size()));
    if (after >= Kernel::kOverread) {
      sink(Kernel::book_op(m, type));
    } else {
      ++totals.tail;
      sink(scalar::Kernel::book_op(m, type));
    }
  }
  totals.end = frame.status;
  return totals;
}

}  // namespace itch::kernels
