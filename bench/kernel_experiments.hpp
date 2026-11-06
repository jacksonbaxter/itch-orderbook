// The passes of the SIMD experiments (simd.md, "Candidate experiments"), each a whole walk of
// the day on one kernel. bench_kernels times them; nothing here is production code.
//
// Each experiment's work pass has a walk pass beside it: the same frame walk, the same type
// test, with the measured operation replaced by adding the type byte, which the walk has
// loaded already. Work less walk is the operation's own cost where it sits in a real loop.
//
// Every pass is always_inline, so that run on a vector kernel through itch::kernels::run() its
// loop is compiled inside that kernel's target region, kernel and all.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <memory>
#include <span>
#include <string_view>
#include <vector>

#include "itch/book_op.hpp"
#include "itch/book_types.hpp"
#include "itch/kernels/layout.hpp"
#include "itch/kernels/parse.hpp"
#include "itch/kernels/scalar.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"

namespace itch::bench::kernel {

struct Totals {
  std::uint64_t messages = 0;  // Whole messages walked.
  std::uint64_t items = 0;     // The messages the pass's operation applies to.
  std::uint64_t fold = 0;      // What the pass computed, so that none of it can be dropped.
  std::uint64_t matches = 0;   // E9: messages whose stock is on the watchlist.
  std::uint64_t refused = 0;   // The book refused an op. A clean day has none.
};

[[nodiscard]] inline unsigned type_of(const std::byte* m) noexcept {
  return static_cast<unsigned char>(*m);
}

// E1: where each width's field is read. All four lie inside the first 19 bytes, the length of
// the shortest op type, with 8 bytes readable from each, as a vector kernel may read.
template <std::size_t W>
inline constexpr std::size_t kFieldAt = W == 2 ? 1 : W == 6 ? 5 : 11;
static_assert(kFieldAt<2> + 8 <= 19 && kFieldAt<4> + 8 <= 19 && kFieldAt<6> + 8 <= 19 &&
              kFieldAt<8> + 8 <= 19);

// E1: one field of width W from every message of an op type.
template <class K, std::size_t W, bool Work>
[[gnu::always_inline]] inline Totals field_pass(std::span<const std::byte> bytes) noexcept {
  FrameReader reader{bytes};
  Totals t;
  for (Frame f = reader.next(); !is_terminal(f.status); f = reader.next()) {
    if (f.status != FrameStatus::message) {
      continue;
    }
    ++t.messages;
    const std::byte* m = f.payload.data();
    if (!kernels::kIsOpType[type_of(m)]) {
      continue;
    }
    ++t.items;
    if constexpr (Work) {
      t.fold += K::template field<W>(m + kFieldAt<W>);
    } else {
      t.fold += type_of(m);
    }
  }
  return t;
}

// E2: every Add Order's fields into a ring of records in memory, with no consumer.
inline constexpr std::size_t kRing = 64;

template <class K, bool Work>
[[gnu::always_inline]] inline Totals decode_pass(std::span<const std::byte> bytes,
                                                 std::array<kernels::AddFields, kRing>& ring) noexcept {
  FrameReader reader{bytes};
  Totals t;
  for (Frame f = reader.next(); !is_terminal(f.status); f = reader.next()) {
    if (f.status != FrameStatus::message) {
      continue;
    }
    ++t.messages;
    const std::byte* m = f.payload.data();
    if (type_of(m) != static_cast<unsigned char>(AddOrder::kType)) {
      continue;
    }
    if constexpr (Work) {
      ring[t.items % kRing] = K::add_fields(m);
    } else {
      t.fold += type_of(m);
    }
    ++t.items;
  }
  if constexpr (Work) {
    for (const kernels::AddFields& r : ring) {
      t.fold += r.reference ^ r.timestamp_ns ^ r.shares ^ r.price ^ r.locate ^ r.tracking ^
                static_cast<unsigned char>(r.side);
    }
  }
  return t;
}

// E3: every op decoded and consumed in integer registers: the reference hashed to one of 64K
// slots, its shares added there, the price compared with the highest so far.
inline constexpr std::size_t kSlots = std::size_t{1} << 16U;

template <class K, bool Work>
[[gnu::always_inline]] inline Totals consume_pass(std::span<const std::byte> bytes,
                                                  std::span<std::uint32_t, kSlots> slots) noexcept {
  FrameReader reader{bytes};
  Totals t;
  Price4 highest = 0;
  for (Frame f = reader.next(); !is_terminal(f.status); f = reader.next()) {
    if (f.status != FrameStatus::message) {
      continue;
    }
    ++t.messages;
    const std::byte* m = f.payload.data();
    const unsigned type = type_of(m);
    if (!kernels::kIsOpType[type]) {
      continue;
    }
    ++t.items;
    if constexpr (Work) {
      const BookOp op = K::book_op(m, static_cast<char>(type));
      const std::uint64_t slot = (op.reference * 0x9E3779B97F4A7C15ULL) >> 48U;
      t.fold += (slots[slot] += op.shares);
      highest = op.price > highest ? op.price : highest;
      t.fold += op.locate + op.new_reference + static_cast<unsigned char>(op.side) +
                static_cast<unsigned>(op.kind);
    } else {
      t.fold += type;
    }
  }
  return {.messages = t.messages, .items = t.items, .fold = t.fold + highest};
}

// E4: what a parse hands the book, folded.
struct FoldSink {
  std::uint64_t fold = 0;
  void operator()(const BookOp& op) noexcept {
    fold += op.reference ^ op.new_reference ^ (std::uint64_t{op.shares} << 32U) ^ op.price ^
            (std::uint64_t{op.locate} << 16U) ^ static_cast<unsigned char>(op.side) ^
            static_cast<unsigned>(op.kind);
  }
};

// E4: the whole parse on the kernel, every op type, near the end on the scalar kernel.
template <class K>
[[gnu::always_inline]] inline Totals parse_pass(std::span<const std::byte> bytes) noexcept {
  FoldSink sink;
  const kernels::ParseTotals p = kernels::parse_ops<K>(bytes, sink);
  return {.messages = p.messages, .items = p.ops, .fold = sink.fold};
}

// E4 by type: the same walk, with only messages of type `only` decoded; or, with `only` zero,
// none decoded, the walk.
template <class K>
[[gnu::always_inline]] inline Totals parse_one_type(std::span<const std::byte> bytes,
                                                    char only) noexcept {
  FrameReader reader{bytes};
  Totals t;
  FoldSink sink;
  for (Frame f = reader.next(); !is_terminal(f.status); f = reader.next()) {
    if (f.status != FrameStatus::message) {
      continue;
    }
    ++t.messages;
    const std::byte* m = f.payload.data();
    const unsigned type = type_of(m);
    if (!kernels::kIsOpType[type]) {
      continue;
    }
    if (static_cast<char>(type) == only) {
      ++t.items;
      sink(K::book_op(m, only));
    } else {
      t.fold += type;
    }
  }
  t.fold += sink.fold;
  return t;
}

// E4 end to end: the parse on the kernel feeding the book.
template <class Book>
struct BookSink {
  Book* book;
  std::uint64_t refused = 0;
  void operator()(const BookOp& op) noexcept {
    const ApplyStatus status = book->apply(op);
    refused += status != ApplyStatus::applied && status != ApplyStatus::no_book_effect ? 1U : 0U;
  }
};

template <class K, class Book>
[[gnu::always_inline]] inline Totals book_pass(std::span<const std::byte> bytes,
                                               Book& book) noexcept {
  BookSink<Book> sink{.book = &book};
  const kernels::ParseTotals p = kernels::parse_ops<K>(bytes, sink);
  return {.messages = p.messages,
          .items = p.ops,
          .fold = book.live_orders() + book.live_levels(),
          .refused = sink.refused};
}

// E5: Add Orders four at a time, into one array a field, where four come in a row; the rest
// one at a time on the scalar kernel, the same for every kernel.
[[nodiscard]] inline std::uint64_t fold_batch(const kernels::AddBatch& b) noexcept {
  std::uint64_t fold = 0;
  for (std::size_t i = 0; i < 4; ++i) {
    fold += b.reference[i] ^ (std::uint64_t{b.shares[i]} << 32U) ^ b.price[i] ^ b.locate[i];
  }
  return fold;
}

[[nodiscard]] inline std::uint64_t fold_single(const std::byte* m) noexcept {
  return read_be<std::uint64_t>(m + 11) ^ (std::uint64_t{read_be<std::uint32_t>(m + 20)} << 32U) ^
         read_be<std::uint32_t>(m + 32) ^ read_be<std::uint16_t>(m + 1);
}

template <class K, bool Work>
[[gnu::always_inline]] inline Totals batch_pass(std::span<const std::byte> bytes) noexcept {
  FrameReader reader{bytes};
  Totals t;
  std::array<const std::byte*, 4> pending{};
  std::size_t held = 0;
  kernels::AddBatch batch;
  for (Frame f = reader.next(); !is_terminal(f.status); f = reader.next()) {
    if (f.status != FrameStatus::message) {
      continue;
    }
    ++t.messages;
    const std::byte* m = f.payload.data();
    if (type_of(m) == static_cast<unsigned char>(AddOrder::kType)) {
      ++t.items;
      if constexpr (Work) {
        pending[held++] = m;
        if (held == 4) {
          K::batch4(pending.data(), batch);
          t.fold += fold_batch(batch);
          held = 0;
        }
      } else {
        t.fold += type_of(m);
      }
      continue;
    }
    if constexpr (Work) {
      for (std::size_t i = 0; i < held; ++i) {
        t.fold += fold_single(pending[i]);
      }
      held = 0;
    }
  }
  for (std::size_t i = 0; i < held; ++i) {
    t.fold += fold_single(pending[i]);
  }
  return t;
}

// E5's other half, untimed: how long runs of one type are. Buckets 1, 2, 3, 4 to 7, 8 to 15,
// 16 to 31, 32 and up.
inline constexpr std::array<std::string_view, 7> kRunBuckets{"1",    "2",     "3",    "4-7",
                                                             "8-15", "16-31", "32+"};

struct RunLengths {
  std::array<std::uint64_t, kRunBuckets.size()> any_type{};  // Messages, by the run they sit in.
  std::array<std::uint64_t, kRunBuckets.size()> add_order{};  // Add Orders, likewise.
};

[[nodiscard]] constexpr std::size_t run_bucket(std::uint64_t length) noexcept {
  if (length <= 3) {
    return static_cast<std::size_t>(length - 1);
  }
  std::size_t bucket = 3;
  for (std::uint64_t top = 7; length > top && bucket + 1 < kRunBuckets.size(); top = (top * 2) + 1) {
    ++bucket;
  }
  return bucket;
}

[[nodiscard]] inline RunLengths run_lengths(std::span<const std::byte> bytes) noexcept {
  RunLengths runs;
  FrameReader reader{bytes};
  unsigned current = 256;
  std::uint64_t length = 0;
  const auto close = [&runs, &current, &length] {
    if (length != 0) {
      runs.any_type[run_bucket(length)] += length;
      if (current == static_cast<unsigned char>(AddOrder::kType)) {
        runs.add_order[run_bucket(length)] += length;
      }
    }
  };
  for (Frame f = reader.next(); !is_terminal(f.status); f = reader.next()) {
    if (f.status != FrameStatus::message) {
      continue;
    }
    const unsigned type = type_of(f.payload.data());
    if (type != current) {
      close();
      current = type;
      length = 0;
    }
    ++length;
  }
  close();
  return runs;
}

// E6: the frame walk alone, four ways. Each adds every frame's type byte and counts frames,
// and stops at the first frame that would pass the end.
enum class Framing : std::uint8_t {
  length,    // p += 2 + be16(p): the data dependency the wire format sets.
  byte,      // p += 2 + p[1], refusing a frame whose high length byte is not zero.
  table,     // p += 2 + the type's length from the table: trusts a validated stream.
  constant,  // a switch on the type, each case advancing by its constant: a control dependency.
};

struct FramingTotals {
  Totals totals;
  std::size_t stopped_at = 0;  // Bytes walked. The whole input, on a clean day.
};

template <Framing Form>
[[gnu::always_inline]] inline FramingTotals framing_pass(std::span<const std::byte> bytes) noexcept {
  const std::byte* p = bytes.data();
  const std::byte* const end = bytes.data() + bytes.size();
  Totals t;
  while (end - p >= 3) {
    std::size_t length = 0;
    if constexpr (Form == Framing::length) {
      length = read_be<std::uint16_t>(p);
    } else if constexpr (Form == Framing::byte) {
      if (p[0] != std::byte{0}) {
        break;
      }
      length = static_cast<unsigned char>(p[1]);
    } else if constexpr (Form == Framing::table) {
      length = kLengthByType[type_of(p + 2)];
    } else {
      switch (static_cast<char>(p[2])) {
        case AddOrder::kType: length = 36; break;
        case AddOrderAttributed::kType: length = 40; break;
        case OrderExecuted::kType: length = 31; break;
        case OrderExecutedWithPrice::kType: length = 36; break;
        case OrderCancel::kType: length = 23; break;
        case OrderDelete::kType: length = 19; break;
        case OrderReplace::kType: length = 35; break;
        case Trade::kType: length = 44; break;
        default: length = read_be<std::uint16_t>(p); break;
      }
    }
    if (length == 0 || static_cast<std::size_t>(end - p) - 2 < length) {
      break;
    }
    t.fold += type_of(p + 2);
    ++t.messages;
    p += 2 + length;
  }
  t.items = t.messages;
  return {.totals = t, .stopped_at = static_cast<std::size_t>(p - bytes.data())};
}

// E9: does an Add Order's stock sit on the watchlist? Either compared with every listed stock,
// 8 bytes at a time, on the kernel; or looked up by the order's locate in a bitset, filled from
// the day's Stock Directory messages as they arrive.
enum class Filter : std::uint8_t { compare, bitset };

[[nodiscard]] inline std::uint64_t stock_key(const std::byte* stock) noexcept {
  std::uint64_t key = 0;
  std::memcpy(&key, stock, sizeof(key));
  return key;
}

struct Watchlist {
  std::vector<std::uint64_t> keys;  // Padded with zeros, which no stock is, to a multiple of 8.
  std::size_t listed = 0;
};

template <class K, Filter Form, bool Work>
[[gnu::always_inline]] inline Totals filter_pass(std::span<const std::byte> bytes,
                                                 const Watchlist& list,
                                                 std::span<std::uint8_t> bits) noexcept {
  FrameReader reader{bytes};
  Totals t;
  const std::uint64_t* keys = list.keys.data();
  const std::size_t n = list.keys.size();
  for (Frame f = reader.next(); !is_terminal(f.status); f = reader.next()) {
    if (f.status != FrameStatus::message) {
      continue;
    }
    ++t.messages;
    const std::byte* m = f.payload.data();
    const unsigned type = type_of(m);
    if constexpr (Work && Form == Filter::bitset) {
      if (type == static_cast<unsigned char>(StockDirectory::kType)) {
        bits[read_be<std::uint16_t>(m + 1)] =
            kernels::scalar::Kernel::any_equal(stock_key(m + 11), keys, n) ? 1 : 0;
        continue;
      }
    }
    if (type != static_cast<unsigned char>(AddOrder::kType) &&
        type != static_cast<unsigned char>(AddOrderAttributed::kType)) {
      continue;
    }
    ++t.items;
    if constexpr (!Work) {
      t.fold += type;
    } else if constexpr (Form == Filter::compare) {
      t.matches += K::any_equal(stock_key(m + 24), keys, n) ? 1U : 0U;
    } else {
      t.matches += bits[read_be<std::uint16_t>(m + 1)];
    }
  }
  t.fold += t.matches;
  return t;
}

// E13: every message's 6-byte timestamp, four ways.
enum class Stamp : std::uint8_t {
  exact,      // read_be48: 6 bytes into a zeroed 8, one swap. What the parser ships.
  split,      // a 4-byte and a 2-byte read, swapped and merged.
  overread,   // 8 bytes from the timestamp, swapped, shifted down 16: reads 2 bytes past it,
              // so near the end of the range, where those 2 are not there, it reads exactly.
  underread,  // 8 bytes from the tracking number, swapped, the top 16 bits masked off: reads
              // only bytes of the message.
};

template <Stamp Form, bool Work>
[[gnu::always_inline]] inline Totals timestamp_pass(std::span<const std::byte> bytes) noexcept {
  FrameReader reader{bytes};
  Totals t;
  const std::byte* const end = bytes.data() + bytes.size();
  for (Frame f = reader.next(); !is_terminal(f.status); f = reader.next()) {
    if (f.status != FrameStatus::message) {
      continue;
    }
    ++t.messages;
    ++t.items;
    const std::byte* m = f.payload.data();
    if constexpr (!Work) {
      t.fold += type_of(m);
    } else if constexpr (Form == Stamp::exact) {
      t.fold += read_be48(m + 5);
    } else if constexpr (Form == Stamp::split) {
      t.fold += (std::uint64_t{read_be<std::uint32_t>(m + 5)} << 16U) | read_be<std::uint16_t>(m + 9);
    } else if constexpr (Form == Stamp::overread) {
      t.fold += end - (m + 5) >= 8 ? read_be<std::uint64_t>(m + 5) >> 16U : read_be48(m + 5);
    } else {
      t.fold += read_be<std::uint64_t>(m + 3) & 0xFFFF'FFFF'FFFFULL;
    }
  }
  return t;
}

// The kernel against the scalar kernel on every message of the input: every op's BookOp,
// every Add Order's fields, every op's four E1 fields. Untimed.
struct VerifyTotals {
  std::uint64_t compared = 0;
  std::uint64_t mismatches = 0;
};

template <class K>
[[gnu::always_inline]] inline VerifyTotals verify_pass(std::span<const std::byte> bytes) noexcept {
  using Scalar = kernels::scalar::Kernel;
  FrameReader reader{bytes};
  VerifyTotals v;
  const std::byte* const end = bytes.data() + bytes.size();
  for (Frame f = reader.next(); !is_terminal(f.status); f = reader.next()) {
    if (f.status != FrameStatus::message) {
      continue;
    }
    const std::byte* m = f.payload.data();
    const auto type = static_cast<char>(type_of(m));
    if (!kernels::kIsOpType[type_of(m)]) {
      continue;
    }
    ++v.compared;
    if (static_cast<std::size_t>(end - (m + f.payload.size())) >= K::kOverread) {
      v.mismatches += K::book_op(m, type) == Scalar::book_op(m, type) ? 0U : 1U;
      if (type == AddOrder::kType) {
        v.mismatches += K::add_fields(m) == Scalar::add_fields(m) ? 0U : 1U;
      }
    }
    v.mismatches += K::template field<2>(m + kFieldAt<2>) == Scalar::field<2>(m + kFieldAt<2>) ? 0U : 1U;
    v.mismatches += K::template field<4>(m + kFieldAt<4>) == Scalar::field<4>(m + kFieldAt<4>) ? 0U : 1U;
    v.mismatches += K::template field<6>(m + kFieldAt<6>) == Scalar::field<6>(m + kFieldAt<6>) ? 0U : 1U;
    v.mismatches += K::template field<8>(m + kFieldAt<8>) == Scalar::field<8>(m + kFieldAt<8>) ? 0U : 1U;
  }
  return v;
}

}  // namespace itch::bench::kernel
