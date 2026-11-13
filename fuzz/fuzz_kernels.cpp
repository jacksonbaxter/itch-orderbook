// Every parsing kernel this host runs, held to the scalar kernel and to decode_book_op on
// arbitrary bytes.
//
// The input is copied into a heap block of exactly its size, so that ASan sees any read
// past its last byte, and used twice.
//
// As a stream, through parse_ops on each kernel, the way the library's callers run it:
// inside itch::kernels::run(), so that a vector kernel's loop runs in its own target region.
// The ops it hands over, in order, and its totals must equal the scalar kernel's and those of
// a walk written here from FrameReader and decode_book_op alone. Its tail must be exactly the
// op messages that end fewer than the kernel's kOverread bytes before the input's end, which
// parse_ops gives to the scalar kernel instead.
//
// Message by message, on each kernel: the input's first bytes as one message of each op type,
// in a heap block of the message's length plus the kernel's kOverread and not a byte more, so
// that a read past what the contract allows is an ASan report. The Add Order fields of E2 the
// same way, every field width at every offset of the input block, four Add Orders as one
// batch, and a search of the input read as keys. Any difference from the scalar kernel, or
// from the shipped decoder where there is one, is a failure.
//
// A kernel this CPU cannot run, avx512vbmi on most machines, is skipped and named on stderr
// once. A kernel this build lacks is not compiled in.
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <optional>
#include <print>
#include <span>
#include <string_view>
#include <vector>

#include "fuzz_check.hpp"
#include "itch/book_op.hpp"
#include "itch/kernels/layout.hpp"
#include "itch/kernels/parse.hpp"
#include "itch/kernels/select.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"

namespace {

using itch::BookOp;
using itch::FrameStatus;
using itch::kernels::AddBatch;
using itch::kernels::AddFields;
using itch::kernels::ParseTotals;
using itch::kernels::Selected;
using Scalar = itch::kernels::scalar::Kernel;
using Bytes = std::vector<std::byte>;

[[noreturn]] void kernel_failed(std::string_view kernel, const char* condition, const char* file,
                                int line) {
  std::println(stderr, "fuzz_kernels: kernel {} differs", kernel);
  itch::fuzzing::check_failed(condition, file, line);
}

// FUZZ_CHECK, naming the kernel first.
#define KERNEL_CHECK(K, condition) \
  ((condition) ? void() : kernel_failed(K::kName, #condition, __FILE__, __LINE__))

// The kernels this build has and this CPU runs, found once.
const std::vector<Selected>& runnable() {
  static const std::vector<Selected> kernels = [] {
    std::vector<Selected> found;
    for (const itch::kernels::KernelId id : itch::kernels::kBuilt) {
      const auto selected = itch::kernels::select_kernel(to_string(id));
      if (selected) {
        found.push_back(*selected);
      } else {
        std::println(stderr, "fuzz_kernels: skipping {}: {}", to_string(id), selected.error());
      }
    }
    std::println(stderr, "fuzz_kernels: {} of {} built kernels run here", found.size(),
                 itch::kernels::kBuilt.size());
    return found;
  }();
  return kernels;
}

struct Ops {
  std::vector<BookOp> ops;
  void operator()(const BookOp& op) { ops.push_back(op); }
};

// The stream walked without parse_ops: every frame FrameReader calls a message, and an op
// for each one decode_book_op takes.
struct Walk {
  std::vector<BookOp> ops;
  std::vector<std::size_t> after;  // Bytes of input after each op's message.
  std::uint64_t messages = 0;
  FrameStatus end = FrameStatus::end_of_input;

  [[nodiscard]] std::uint64_t tail(std::size_t overread) const {
    return static_cast<std::uint64_t>(
        std::ranges::count_if(after, [overread](std::size_t a) { return a < overread; }));
  }
};

Walk walk(std::span<const std::byte> bytes) {
  Walk w;
  itch::FrameReader reader{bytes};
  itch::Frame frame = reader.next();
  for (; !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != FrameStatus::message) {
      continue;
    }
    ++w.messages;
    if (const std::optional<BookOp> op = itch::decode_book_op(frame.payload)) {
      w.ops.push_back(*op);
      w.after.push_back(bytes.size() - (frame.offset + 2 + frame.payload.size()));
    }
  }
  w.end = frame.status;
  return w;
}

// parse_ops on one kernel, against the scalar kernel's run and the walk.
struct StreamMatches {
  std::span<const std::byte> bytes;
  const ParseTotals* scalar_totals;
  const Ops* scalar;
  const Walk* want;

  template <class K>
  [[gnu::always_inline]] void operator()() const {
    Ops got;
    const ParseTotals totals = itch::kernels::parse_ops<K>(bytes, got);
    KERNEL_CHECK(K, totals.messages == scalar_totals->messages);
    KERNEL_CHECK(K, totals.ops == scalar_totals->ops);
    KERNEL_CHECK(K, totals.end == scalar_totals->end);
    KERNEL_CHECK(K, got.ops == scalar->ops);
    KERNEL_CHECK(K, totals.messages == want->messages);
    KERNEL_CHECK(K, totals.ops == want->ops.size());
    KERNEL_CHECK(K, totals.end == want->end);
    KERNEL_CHECK(K, totals.tail == want->tail(K::kOverread));
    KERNEL_CHECK(K, got.ops == want->ops);
  }
};

// The input's first bytes, then zeros, as `size` bytes in a block of their own, the type
// byte set. The block holds `size` and no more, so ASan sees a read past it.
Bytes block_from(std::span<const std::byte> input, std::size_t size, std::size_t skip, char type) {
  Bytes block(size);
  if (skip < input.size()) {
    const std::span<const std::byte> from = input.subspan(skip);
    std::ranges::copy(from.first(std::min(from.size(), size)), block.begin());
  }
  block[0] = static_cast<std::byte>(type);
  return block;
}

struct MessagesMatch {
  std::span<const std::byte> input;

  // One message of each op type, with only the overread the kernel declares after it.
  template <class K>
  [[gnu::always_inline]] void book_ops() const {
    for (const char type : itch::kernels::kOpTypes) {
      const std::size_t length = itch::message_length(type);
      const Bytes block = block_from(input, length + K::kOverread, 0, type);
      const BookOp got = K::book_op(block.data(), type);
      const BookOp scalar = Scalar::book_op(block.data(), type);
      const std::optional<BookOp> shipped = itch::decode_book_op(std::span{block}.first(length));
      KERNEL_CHECK(K, shipped.has_value());
      KERNEL_CHECK(K, got == scalar);
      KERNEL_CHECK(K, got == *shipped);
    }
  }

  // E2's Add Order fields, against the scalar kernel and AddOrder::from_wire.
  template <class K>
  [[gnu::always_inline]] void add_fields() const {
    const std::size_t length = itch::message_length(itch::AddOrder::kType);
    const Bytes block = block_from(input, length + K::kOverread, 0, itch::AddOrder::kType);
    const AddFields got = K::add_fields(block.data());
    KERNEL_CHECK(K, got == Scalar::add_fields(block.data()));
    const itch::AddOrder order = itch::AddOrder::from_wire(block.data());
    const AddFields shipped{.reference = order.order_reference,
                            .timestamp_ns = order.header.timestamp_ns,
                            .shares = order.shares,
                            .price = order.price,
                            .locate = order.header.stock_locate,
                            .tracking = order.header.tracking_number,
                            .side = order.side};
    KERNEL_CHECK(K, got == shipped);
  }

  // Every width at every offset with the 8 bytes a vector kernel may read still in the
  // block: the last offset reads the input's final byte.
  template <class K, std::size_t W>
  [[gnu::always_inline]] void width(std::span<const std::byte> bytes) const {
    for (std::size_t at = 0; at + 8 <= bytes.size(); ++at) {
      KERNEL_CHECK(K, K::template field<W>(bytes.data() + at) ==
                          Scalar::template field<W>(bytes.data() + at));
    }
  }

  // Four Add Orders from the input, 36 bytes apart, each in its own block.
  template <class K>
  [[gnu::always_inline]] void batch() const {
    const std::size_t length = itch::message_length(itch::AddOrder::kType);
    std::array<Bytes, 4> blocks;
    std::array<const std::byte*, 4> m{};
    for (std::size_t i = 0; i < 4; ++i) {
      blocks[i] = block_from(input, length + K::kOverread, i * length, itch::AddOrder::kType);
      m[i] = blocks[i].data();
    }
    AddBatch got;
    AddBatch want;
    K::batch4(m.data(), got);
    Scalar::batch4(m.data(), want);
    KERNEL_CHECK(K, got == want);
  }

  // The input as keys, as many as fill whole groups of eight, in a block of exactly those
  // keys: each of them, each with one bit flipped, and the input's first 8 bytes.
  template <class K>
  [[gnu::always_inline]] void keys() const {
    const std::size_t n = (input.size() / 8) & ~std::size_t{7};
    std::vector<std::uint64_t> list(n);
    if (n != 0) {
      std::memcpy(list.data(), input.data(), n * 8);
    }
    std::vector<std::uint64_t> probes(list);
    for (std::size_t i = 0; i < n; ++i) {
      probes.push_back(list[i] ^ (std::uint64_t{1} << (i % 64)));
    }
    std::uint64_t first = 0;
    if (!input.empty()) {
      std::memcpy(&first, input.data(), std::min(input.size(), sizeof(first)));
    }
    probes.push_back(first);
    for (const std::uint64_t key : probes) {
      const bool found = std::ranges::find(list, key) != list.end();
      KERNEL_CHECK(K, Scalar::any_equal(key, list.data(), n) == found);
      KERNEL_CHECK(K, K::any_equal(key, list.data(), n) == found);
    }
  }

  template <class K>
  [[gnu::always_inline]] void operator()() const {
    book_ops<K>();
    add_fields<K>();
    width<K, 2>(input);
    width<K, 4>(input);
    width<K, 6>(input);
    width<K, 8>(input);
    batch<K>();
    keys<K>();
  }
};

}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  // Exactly the input's bytes, so that its end is the end of a heap block.
  Bytes input(size);
  if (size != 0) {
    std::memcpy(input.data(), data, size);
  }
  const std::span<const std::byte> bytes{input};

  const Walk want = walk(bytes);
  Ops scalar;
  const ParseTotals scalar_totals = itch::kernels::parse_ops<Scalar>(bytes, scalar);
  for (const Selected kernel : runnable()) {
    StreamMatches stream{
        .bytes = bytes, .scalar_totals = &scalar_totals, .scalar = &scalar, .want = &want};
    itch::kernels::run(kernel, stream);
    MessagesMatch messages{.input = bytes};
    itch::kernels::run(kernel, messages);
  }
  return 0;
}
