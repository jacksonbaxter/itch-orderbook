// The SPSC ring: order and content across many laps in every configuration T-SPSC-2
// switches, full and empty at the edges, the capacity each wrap gives, the layout the
// padding promises, the padding constant against the line size the system reports, and
// two threads handing over every item in order. The two-thread tests are what the tsan
// preset checks, and what a negative-control build (support/spsc_ordering.hpp) must fail
// under TSan.
#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <print>
#include <span>
#include <string>
#include <thread>
#include <vector>

#ifdef __APPLE__
#include <sys/sysctl.h>
#elifdef __linux__
#include <unistd.h>

#include <fstream>
#endif

#include "itch/spsc.hpp"
#include "support/spsc_ordering.hpp"

// The padding constant against the compiler's own target, so that a build that set it
// wrong for the target does not compile: cmake/ItchCacheLine.cmake chooses it, and
// include/itch/spsc.hpp may not hold a platform #if.
#if defined(__APPLE__) && defined(__aarch64__)
static_assert(itch::kCacheLine == 128, "Apple silicon moves 128-byte lines; 64 would false-share");
#elifdef __x86_64__
static_assert(itch::kCacheLine == 64, "x86-64 moves 64-byte lines");
#elifdef __aarch64__
static_assert(itch::kCacheLine == 64, "Neoverse and Cortex-A cores move 64-byte lines");
#endif

namespace {

using itch::kCacheLine;
using itch::SpscConfig;
using itch::SpscRing;
using itch::SpscWrap;
using itch::testing::TestOrdering;

// Wider than a line, with every word fixed by the sequence number, so a slot read before
// its writes landed, or read twice, shows.
struct Stamped {
  std::uint64_t sequence = 0;
  std::array<std::uint64_t, 31> words{};

  [[nodiscard]] static Stamped of(std::uint64_t sequence) noexcept {
    Stamped stamped;
    stamped.sequence = sequence;
    for (std::size_t i = 0; i < stamped.words.size(); ++i) {
      stamped.words[i] = (sequence * 0x9E3779B97F4A7C15ULL) ^ i;
    }
    return stamped;
  }
  [[nodiscard]] bool intact() const noexcept {
    for (std::size_t i = 0; i < words.size(); ++i) {
      if (words[i] != ((sequence * 0x9E3779B97F4A7C15ULL) ^ i)) {
        return false;
      }
    }
    return true;
  }
};

constexpr SpscConfig kShipped{};
constexpr SpscConfig kUncached{.cached_indices = false};
constexpr SpscConfig kModulo{.wrap = SpscWrap::modulo};
constexpr SpscConfig kUncachedModulo{.cached_indices = false, .wrap = SpscWrap::modulo};
constexpr SpscConfig kSingle{.batch_publish = false};
constexpr SpscConfig kUnpadded{.padding = alignof(std::uint64_t)};
constexpr SpscConfig kWide{.padding = 256};

// One thread, pushing and popping in uneven runs of single and batch calls, so that each
// index passes the other's copy at every offset of the ring, many laps over.
template <SpscConfig Config>
void expect_fifo(std::size_t capacity) {
  SpscRing<std::uint64_t, Config> ring{capacity};
  std::uint64_t pushed = 0;
  std::uint64_t popped = 0;
  std::array<std::uint64_t, 13> batch{};
  for (std::uint64_t round = 0; round < 20'000; ++round) {
    const std::size_t want_in = 1 + (round * 7 % 13);
    for (std::size_t i = 0; i < want_in; ++i) {
      batch[i] = pushed + i;
    }
    std::size_t in = 0;
    if (round % 2 == 0) {
      in = ring.try_push_batch(std::span{batch}.first(want_in));
    } else if (ring.try_push(pushed)) {
      in = 1;
    }
    pushed += in;
    ASSERT_LE(pushed - popped, ring.capacity());
    const std::size_t want_out = 1 + (round * 5 % 11);
    std::array<std::uint64_t, 13> out{};
    std::size_t got = 0;
    if (round % 3 == 0) {
      got = ring.try_pop_batch(std::span{out}.first(want_out));
    } else if (ring.try_pop(out[0])) {
      got = 1;
    }
    for (std::size_t i = 0; i < got; ++i) {
      ASSERT_EQ(out[i], popped + i) << "round " << round;
    }
    popped += got;
  }
  EXPECT_GT(popped, 10 * ring.capacity()) << "the ring did not lap";
}

TEST(SpscRing, KeepsOrderInEveryConfiguration) {
  expect_fifo<kShipped>(8);
  expect_fifo<kUncached>(8);
  expect_fifo<kModulo>(6);
  expect_fifo<kUncachedModulo>(6);
  expect_fifo<kSingle>(8);
  expect_fifo<kUnpadded>(8);
  expect_fifo<kWide>(8);
}

template <SpscConfig Config>
void expect_edges() {
  SpscRing<std::uint64_t, Config> ring{4};
  std::uint64_t value = 99;
  EXPECT_FALSE(ring.try_pop(value)) << "an empty ring gave an item";
  EXPECT_EQ(value, 99U) << "a failed pop wrote its output";
  for (std::uint64_t i = 0; i < 4; ++i) {
    EXPECT_TRUE(ring.try_push(i));
  }
  EXPECT_FALSE(ring.try_push(4)) << "a full ring took an item";
  const std::array<std::uint64_t, 2> more{5, 6};
  EXPECT_EQ(ring.try_push_batch(more), 0U) << "a full ring took a batch";
  std::array<std::uint64_t, 8> out{};
  EXPECT_EQ(ring.try_pop_batch(out), 4U);
  EXPECT_EQ(out[0], 0U);
  EXPECT_EQ(out[3], 3U);
  EXPECT_EQ(ring.try_pop_batch(out), 0U) << "an empty ring gave a batch";
  const std::array<std::uint64_t, 6> six{10, 11, 12, 13, 14, 15};
  EXPECT_EQ(ring.try_push_batch(six), 4U) << "a batch is cut to the room there is";
  EXPECT_TRUE(ring.try_pop(value));
  EXPECT_EQ(value, 10U);
}

TEST(SpscRing, StopsAtFullAndEmpty) {
  expect_edges<kShipped>();
  expect_edges<kUncached>();
  expect_edges<kModulo>();
  expect_edges<kSingle>();
}

TEST(SpscRing, MaskRoundsCapacityUpAndModuloKeepsIt) {
  EXPECT_EQ((SpscRing<std::uint64_t, kShipped>{6}.capacity()), 8U);
  EXPECT_EQ((SpscRing<std::uint64_t, kShipped>{8}.capacity()), 8U);
  EXPECT_EQ((SpscRing<std::uint64_t, kShipped>{0}.capacity()), 1U);
  EXPECT_EQ((SpscRing<std::uint64_t, kModulo>{6}.capacity()), 6U);
  EXPECT_EQ((SpscRing<std::uint64_t, kShipped>{std::size_t{1} << 17U}.capacity()),
            std::size_t{1} << 17U);
}

[[nodiscard]] std::uintptr_t address(const void* block) noexcept {
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  return reinterpret_cast<std::uintptr_t>(block);
}

template <SpscConfig Config>
void expect_layout(std::size_t apart) {
  const SpscRing<std::uint64_t, Config> ring{16};
  const std::uintptr_t producer = address(ring.producer_block());
  const std::uintptr_t consumer = address(ring.consumer_block());
  const std::uintptr_t shared = address(ring.shared_block());
  EXPECT_EQ(producer % apart, 0U);
  EXPECT_EQ(consumer % apart, 0U);
  EXPECT_EQ(shared % apart, 0U);
  EXPECT_GE(consumer - producer, apart) << "the two indices share a line";
  EXPECT_GE(shared - consumer, apart) << "the consumer's index shares a line with the slots";
}

TEST(SpscRing, PadsEachSideToItsOwnLine) {
  expect_layout<kShipped>(kCacheLine);
  expect_layout<kWide>(256);
  EXPECT_EQ(alignof(SpscRing<std::uint64_t>), kCacheLine);
}

// The line size the system reports, or 0 when it reports none.
[[nodiscard]] std::size_t reported_line() {
#ifdef __APPLE__
  std::uint64_t line = 0;
  std::size_t size = sizeof(line);
  return ::sysctlbyname("hw.cachelinesize", &line, &size, nullptr, 0) == 0 ? line : 0;
#elifdef __linux__
  const long line = ::sysconf(_SC_LEVEL1_DCACHE_LINESIZE);
  if (line > 0) {
    return static_cast<std::size_t>(line);
  }
  std::ifstream file{"/sys/devices/system/cpu/cpu0/cache/index0/coherency_line_size"};
  std::size_t from_sysfs = 0;
  return file >> from_sysfs ? from_sysfs : 0;
#else
  return 0;
#endif
}

TEST(SpscRing, PaddingConstantIsTheHardwareLine) {
  const std::size_t line = reported_line();
  if (line == 0) {
    GTEST_SKIP() << "this system reports no cache line size";
  }
  std::println("cache line: {} bytes reported, kCacheLine {}", line, kCacheLine);
  EXPECT_EQ(kCacheLine, line);
}

// How long a consumer waits on an empty ring before it calls the handover stalled, so
// that a ring that loses items, or never sees them, fails the test instead of hanging it.
constexpr std::chrono::seconds kStallLimit{10};

// Pops until `items` have arrived, `check` returns false, or the ring has given nothing for
// kStallLimit, and then tells the producer to stop. Returns how many arrived in order.
template <class Ring, class Pop, class Check>
std::uint64_t consume(Ring& ring, std::uint64_t items, std::atomic<bool>& abandon, Pop pop,
                      Check check) {
  std::uint64_t expected = 0;
  auto progress = std::chrono::steady_clock::now();
  std::uint64_t polls = 0;
  while (expected < items) {
    const std::size_t got = pop(ring, expected);
    if (got == 0) {
      if (++polls % 4096 == 0 && std::chrono::steady_clock::now() - progress > kStallLimit) {
        break;
      }
      continue;
    }
    if (!check(expected, got)) {
      break;
    }
    expected += got;
    progress = std::chrono::steady_clock::now();
  }
  abandon.store(true, std::memory_order_relaxed);
  return expected;
}

// A producer and a consumer on two threads, the producer alternating single pushes with
// batches and the consumer single pops with batches, through a small ring so that it is
// full and empty again and again. Every item must arrive once, in order, whole. A 256-byte
// item checks that no item arrives torn; its copy is a memcpy, which TSan on this Mac did
// not check for the race a weakened order leaves (docs/NOTES.md, "The SPSC ring"), so an 8-byte
// item, copied by a single load and store, runs the same handover for TSan to see.
template <SpscConfig Config>
void expect_handover(std::uint64_t items) {
  SpscRing<Stamped, Config, TestOrdering> ring{16};
  std::atomic<bool> abandon{false};
  std::thread producer{[&ring, &abandon, items] {
    std::array<Stamped, 5> batch{};
    std::uint64_t next = 0;
    while (next < items && !abandon.load(std::memory_order_relaxed)) {
      if (next % 2 == 0) {
        next += ring.try_push(Stamped::of(next)) ? 1U : 0U;
        continue;
      }
      const std::size_t want = static_cast<std::size_t>(std::min<std::uint64_t>(5, items - next));
      for (std::size_t i = 0; i < want; ++i) {
        batch[i] = Stamped::of(next + i);
      }
      next += ring.try_push_batch(std::span{batch}.first(want));
    }
  }};
  std::array<Stamped, 7> out{};
  const std::uint64_t arrived = consume(
      ring, items, abandon,
      [&out](auto& r, std::uint64_t expected) {
        return expected % 3 == 0 ? r.try_pop_batch(out)
                                 : static_cast<std::size_t>(r.try_pop(out[0]));
      },
      [&out](std::uint64_t expected, std::size_t got) {
        for (std::size_t i = 0; i < got; ++i) {
          if (out[i].sequence != expected + i || !out[i].intact()) {
            return false;
          }
        }
        return true;
      });
  producer.join();
  EXPECT_EQ(arrived, items) << "an item arrived out of order or torn, or none came for "
                            << kStallLimit.count() << " s, after " << arrived;
}

template <SpscConfig Config>
void expect_word_handover(std::uint64_t items) {
  SpscRing<std::uint64_t, Config, TestOrdering> ring{16};
  std::atomic<bool> abandon{false};
  std::thread producer{[&ring, &abandon, items] {
    std::array<std::uint64_t, 5> batch{};
    std::uint64_t next = 0;
    while (next < items && !abandon.load(std::memory_order_relaxed)) {
      if (next % 2 == 0) {
        next += ring.try_push(next) ? 1U : 0U;
        continue;
      }
      const std::size_t want = static_cast<std::size_t>(std::min<std::uint64_t>(5, items - next));
      for (std::size_t i = 0; i < want; ++i) {
        batch[i] = next + i;
      }
      next += ring.try_push_batch(std::span{batch}.first(want));
    }
  }};
  std::array<std::uint64_t, 7> out{};
  const std::uint64_t arrived = consume(
      ring, items, abandon,
      [&out](auto& r, std::uint64_t expected) {
        return expected % 3 == 0 ? r.try_pop_batch(out)
                                 : static_cast<std::size_t>(r.try_pop(out[0]));
      },
      [&out](std::uint64_t expected, std::size_t got) {
        for (std::size_t i = 0; i < got; ++i) {
          if (out[i] != expected + i) {
            return false;
          }
        }
        return true;
      });
  producer.join();
  EXPECT_EQ(arrived, items) << "a word arrived out of order, or none came for "
                            << kStallLimit.count() << " s, after " << arrived;
}

// TSan runs each access through its runtime, so fewer items there.
constexpr std::uint64_t kHandover = itch::testing::kUnderTsan ? 20'000 : 1'000'000;

TEST(SpscRing, TwoThreadsHandOverEveryItemInOrder) {
  std::println("orders: {} ({})", itch::testing::ordering_label(), TestOrdering::kWeakened);
  expect_handover<kShipped>(kHandover);
  expect_handover<kUncached>(kHandover);
  expect_handover<kModulo>(kHandover);
  expect_handover<kSingle>(kHandover);
}

TEST(SpscRing, TwoThreadsHandOverEveryWordInOrder) {
  std::println("orders: {} ({})", itch::testing::ordering_label(), TestOrdering::kWeakened);
  expect_word_handover<kShipped>(kHandover);
  expect_word_handover<kUncached>(kHandover);
  expect_word_handover<kModulo>(kHandover);
  expect_word_handover<kSingle>(kHandover);
}

}  // namespace
