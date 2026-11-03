// The hot path allocates nothing, and the counter that says so can see an allocation.
//
// Each arm of the fast book takes a stream through the frame reader, every order path
// included, in a window that starts straight after construction: it has no warm-up to
// hide behind. The stream makes each arm take its rarer paths too: pages taken, given
// back and run out, references past the paged index's reach, and flat arrays moving to
// bigger blocks.
// The reference book takes the same stream and must allocate, and each form of new is
// shown reaching its own counter, so a broken counter fails here instead of passing the
// fast book.
#include <gtest/gtest.h>

#include <bit>
#include <cstddef>
#include <cstdint>
#include <new>
#include <print>
#include <random>
#include <span>
#include <string_view>
#include <type_traits>
#include <vector>

#include "itch/book.hpp"
#include "itch/reader.hpp"
#include "oracle/alloc_counter.hpp"
#include "oracle/reference_book.hpp"
#include "oracle/wire_builder.hpp"
#include "support/fixtures.hpp"

namespace {

using itch::oracle::AllocCounts;
using itch::oracle::AllocForm;
using itch::oracle::read_alloc_counts;
using itch::oracle::reset_alloc_counts;
namespace wire = itch::oracle::wire;

// Churns a fixed pool of orders through every order path many times over, so that each
// pool and index recycle, on a stream framed once before any window opens. One new
// reference in five has bit 40 set.
std::vector<std::byte> build_stream() {
  std::vector<std::byte> stream;
  const auto put = [&stream](const wire::Bytes& message) {
    itch::fixtures::append_frame(stream, message);
  };
  for (const auto& message : itch::fixtures::all_messages()) {
    const char type = static_cast<char>(message[0]);
    if (type != 'E' && type != 'C' && type != 'X' && type != 'D' && type != 'U') {
      put(message);
    }
  }
  // The same stream every run.
  // NOLINTNEXTLINE(cert-msc32-c,cert-msc51-cpp,bugprone-random-generator-seed)
  std::mt19937 random{20191230};
  constexpr std::uint32_t kPool = 512;
  std::vector<std::uint64_t> live;
  std::uint64_t next_reference = 1;
  const auto fresh = [&next_reference] {
    const std::uint64_t reference = next_reference++;
    return reference % 5 == 0 ? reference | (std::uint64_t{1} << 40U) : reference;
  };
  for (int step = 0; step < 200000; ++step) {
    const auto price = static_cast<std::uint32_t>(1000 + ((random() % 64) * 100));
    if (live.size() < kPool / 2) {
      const std::uint64_t reference = fresh();
      put((step % 3 == 0 ? wire::add_attributed : wire::add)(
          1, reference, step % 2 == 0 ? 'B' : 'S', 300, price, 1));
      live.push_back(reference);
      continue;
    }
    const std::size_t pick = random() % live.size();
    const std::uint64_t reference = live[pick];
    switch (random() % 5) {
      case 0: put(wire::execute(1, reference, 1)); break;
      case 1: put(wire::execute_with_price(1, reference, 1, 77)); break;
      case 2: put(wire::cancel(1, reference, 1)); break;
      case 3: {
        const std::uint64_t replaced = fresh();
        put(wire::replace(1, reference, replaced, 300, price));
        live[pick] = replaced;
        break;
      }
      default:
        put(wire::erase(1, reference));
        live[pick] = live.back();
        live.pop_back();
        break;
    }
  }
  return stream;
}

template <class Apply>
std::uint64_t replay(std::span<const std::byte> stream, Apply apply) {
  std::uint64_t messages = 0;
  itch::FrameReader reader{stream};
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    apply(frame.payload);
    ++messages;
  }
  return messages;
}

// Few pages and a short reach, so that the paged index runs out and falls back, and
// first blocks of 8, so that the flat arrays move as they fill.
constexpr itch::BookCapacity kCapacity{
    .orders = 2048, .levels = 2048, .index_pages = 4, .index_bits = 20, .level_slots = 4096};

template <class Index, class Levels>
void expect_no_allocation(std::string_view name) {
  SCOPED_TRACE(name);
  const std::vector<std::byte> stream = build_stream();
  itch::FastBook<itch::NoWriteObserver, Index, Levels> book{kCapacity};
  std::uint64_t refused = 0;
  reset_alloc_counts();
  const std::uint64_t messages =
      replay(stream, [&book, &refused](std::span<const std::byte> payload) {
        const itch::ApplyStatus status = book.apply(payload);
        refused +=
            status == itch::ApplyStatus::applied || status == itch::ApplyStatus::no_book_effect
                ? 0U
                : 1U;
      });
  const AllocCounts counts = read_alloc_counts();
  std::println("fast book, {}: {} messages, {} refused, {} allocations", name, messages, refused,
               counts.total());
  EXPECT_EQ(counts.total(), 0U) << "allocation in the hot path";
  EXPECT_EQ(refused, 0U) << "the stream did not exercise the book";
  EXPECT_GT(messages, 200000U);
  if constexpr (std::is_same_v<Index, itch::PagedIndex<>>) {
    EXPECT_GT(book.index().fallback_inserts(), 0U) << "the paged index never fell back";
    EXPECT_EQ(book.index().peak_live_pages(), 4U) << "the paged index never ran out of pages";
  }
  if constexpr (std::is_same_v<Levels, itch::FlatLevels>) {
    EXPECT_GT(book.levels().relocations(), 0U) << "no flat array moved to a bigger block";
  }
}

TEST(Allocation, TheFastBookAllocatesNothingAfterConstruction) {
  expect_no_allocation<itch::HashIndex, itch::LinkedLevels>("hash, linked");
  expect_no_allocation<itch::HashIndex, itch::FlatLevels>("hash, flat");
  expect_no_allocation<itch::PagedIndex<>, itch::LinkedLevels>("paged, linked");
  expect_no_allocation<itch::PagedIndex<>, itch::FlatLevels>("paged, flat");
}

TEST(Allocation, TheControlBookIsSeenAllocating) {
  const std::vector<std::byte> stream = build_stream();
  itch::oracle::ReferenceBook book;
  reset_alloc_counts();
  const std::uint64_t messages = replay(stream, [&book](std::span<const std::byte> payload) {
    static_cast<void>(book.apply(payload));
  });
  const AllocCounts counts = read_alloc_counts();
  std::println("control book: {} messages, {} allocations", messages, counts.total());
  EXPECT_GT(counts.total(), messages / 4) << "control book made almost no allocation";
}

// A plain call to the allocation function, which no compiler may remove as it may a
// new-expression.
TEST(Allocation, EveryFormOfNewReachesItsOwnCounter) {
  const auto expect_one = [](AllocForm form) {
    const AllocCounts counts = read_alloc_counts();
    EXPECT_EQ(counts.of(form), 1U) << itch::oracle::kAllocFormNames[static_cast<std::size_t>(form)];
    EXPECT_EQ(counts.total(), 1U) << itch::oracle::kAllocFormNames[static_cast<std::size_t>(form)];
    reset_alloc_counts();
  };
  constexpr std::align_val_t kAlign{128};

  reset_alloc_counts();
  ::operator delete(::operator new(16));
  expect_one(AllocForm::plain);
  ::operator delete[](::operator new[](16));
  expect_one(AllocForm::array);
  ::operator delete(::operator new(16, std::nothrow));
  expect_one(AllocForm::nothrow);
  ::operator delete[](::operator new[](16, std::nothrow));
  expect_one(AllocForm::array_nothrow);
  ::operator delete(::operator new(16, kAlign), kAlign);
  expect_one(AllocForm::aligned);
  ::operator delete[](::operator new[](16, kAlign), kAlign);
  expect_one(AllocForm::array_aligned);
  ::operator delete(::operator new(16, kAlign, std::nothrow), kAlign);
  expect_one(AllocForm::aligned_nothrow);
  ::operator delete[](::operator new[](16, kAlign, std::nothrow), kAlign);
  expect_one(AllocForm::array_aligned_nothrow);
}

TEST(Allocation, AnAlignedRequestIsAligned) {
  constexpr std::align_val_t kAlign{4096};
  void* memory = ::operator new(16, kAlign);
  EXPECT_EQ(std::bit_cast<std::uintptr_t>(memory) % 4096, 0U);
  ::operator delete(memory, kAlign);
}

}  // namespace
