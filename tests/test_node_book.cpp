// The node pool, the pages under it, and the node book on every allocator arm.
//
// The pool hands out nodes in the order its free list says, LIFO or FIFO, by index or by
// pointer, refuses past its capacity, and was faulted in before the first node was taken.
// A page report never claims a page size the kernel did not say it gave.
//
// The node book on each arm is held to the reference book, the oracle's own independent
// book, over the churn stream that takes every order path many times over and over every
// oracle fixture: the same status for every message, the canonical words of the
// message's locate after it, and the whole book every 1,000 messages and at the end.
#include <gtest/gtest.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <unistd.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "itch/book.hpp"
#include "itch/heap_ager.hpp"
#include "itch/node_arms.hpp"
#include "itch/node_book.hpp"
#include "itch/node_pool.hpp"
#include "itch/page_map.hpp"
#include "itch/reader.hpp"
#include "oracle/canonical.hpp"
#include "oracle/reference_book.hpp"
#include "oracle/wire_builder.hpp"
#include "support/churn.hpp"

#ifdef ITCH_HAVE_MIMALLOC
#include "itch/mimalloc_arm.hpp"
#endif

namespace {

namespace wire = itch::oracle::wire;

struct Probe {
  std::uint64_t value;
  std::uint64_t pad;
};

// ThreadSanitizer maps the shadow of a page on the first instrumented write to it, so in the
// tsan build the process's fault count takes in the shadow's pages as well as the pool's.
#ifdef __SANITIZE_THREAD__
constexpr bool kThreadSanitizer = true;
#elifdef __has_feature
#if __has_feature(thread_sanitizer)
constexpr bool kThreadSanitizer = true;
#else
constexpr bool kThreadSanitizer = false;
#endif
#else
constexpr bool kThreadSanitizer = false;
#endif

std::uint64_t faults_now() {
  rusage usage{};
  ::getrusage(RUSAGE_SELF, &usage);
  return static_cast<std::uint64_t>(usage.ru_minflt) + static_cast<std::uint64_t>(usage.ru_majflt);
}

TEST(NodePool, LifoGivesTheNodeFreedLastFirst) {
  itch::NodePool<Probe, itch::HandleKind::index, itch::FreeOrder::lifo> pool{
      8, itch::PageRequest::base};
  ASSERT_TRUE(pool.has_room()) << pool.pages().describe();
  const std::uint32_t a = pool.take();
  const std::uint32_t b = pool.take();
  const std::uint32_t c = pool.take();
  EXPECT_EQ(a, 0U);
  EXPECT_EQ(b, 1U);
  EXPECT_EQ(c, 2U);
  pool.give_back(b);
  pool.give_back(a);
  EXPECT_EQ(pool.take(), a);
  EXPECT_EQ(pool.take(), b);
  EXPECT_EQ(pool.take(), 3U);
  EXPECT_EQ(pool.live(), 4U);
  EXPECT_EQ(pool.peak(), 4U);
}

TEST(NodePool, FifoGivesTheNodeFreedFirstFirst) {
  itch::NodePool<Probe, itch::HandleKind::index, itch::FreeOrder::fifo> pool{
      8, itch::PageRequest::base};
  ASSERT_TRUE(pool.has_room()) << pool.pages().describe();
  const std::uint32_t a = pool.take();
  const std::uint32_t b = pool.take();
  const std::uint32_t c = pool.take();
  pool.give_back(b);
  pool.give_back(a);
  pool.give_back(c);
  EXPECT_EQ(pool.take(), b);
  pool.give_back(b);
  EXPECT_EQ(pool.take(), a);
  EXPECT_EQ(pool.take(), c);
  EXPECT_EQ(pool.take(), b);
  EXPECT_EQ(pool.take(), 3U);
}

TEST(NodePool, PointerHandlesAreAddressesInThePool) {
  itch::NodePool<Probe, itch::HandleKind::pointer, itch::FreeOrder::lifo> pool{
      4, itch::PageRequest::base};
  static_assert(sizeof(decltype(pool)::Handle) == 8);
  static_assert(
      sizeof(itch::NodePool<Probe, itch::HandleKind::index, itch::FreeOrder::lifo>::Handle) == 4);
  ASSERT_TRUE(pool.has_room()) << pool.pages().describe();
  Probe* a = pool.take();
  const Probe* b = pool.take();
  EXPECT_EQ(b - a, 1);
  a->value = 11;
  EXPECT_EQ(&pool.at(a), a);
  pool.give_back(a);
  EXPECT_EQ(pool.take(), a);
}

TEST(NodePool, ItRefusesPastItsCapacityAndTakesBackAfterAGiveBack) {
  itch::NodePool<Probe, itch::HandleKind::index, itch::FreeOrder::lifo> pool{
      2, itch::PageRequest::base};
  EXPECT_TRUE(pool.has_room());
  const std::uint32_t a = pool.take();
  static_cast<void>(pool.take());
  EXPECT_FALSE(pool.has_room());
  pool.give_back(a);
  EXPECT_TRUE(pool.has_room());
  EXPECT_EQ(pool.take(), a);
  EXPECT_FALSE(pool.has_room());
}

// Every page is touched in the constructor, so writing every node faults nothing in. Under
// ThreadSanitizer the first write to each page faults in its shadow, about one fault for each
// of the pool's 16 KiB pages on this Mac where an uninstrumented write faults none (docs/NOTES.md,
// "The node pool's fault test under TSan"), so there the count is printed and not judged.
TEST(NodePool, EveryNodeIsFaultedInBeforeTheFirstIsTaken) {
  constexpr std::uint32_t kNodes = 1U << 18U;  // 4 MiB of nodes.
  itch::NodePool<Probe, itch::HandleKind::index, itch::FreeOrder::lifo> pool{
      kNodes, itch::PageRequest::huge_if_offered};
  ASSERT_TRUE(pool.pages().mapped) << pool.pages().describe();
  EXPECT_TRUE(pool.pages().prefaulted);
  const std::uint64_t before = faults_now();
  for (std::uint32_t i = 0; i < kNodes; ++i) {
    pool.at(pool.take()).value = i;
  }
  const std::uint64_t faulted = faults_now() - before;
  EXPECT_FALSE(pool.has_room());
  if (kThreadSanitizer) {
    GTEST_SKIP() << "under ThreadSanitizer, whose shadow faults in on first write: " << faulted
                 << " faults over " << kNodes << " nodes";
  }
  EXPECT_EQ(faulted, 0U);
}

// The control for the test above: the same writes to fresh pages do fault.
TEST(NodePool, TheFaultCountSeesFreshPagesFaultIn) {
  const auto page = static_cast<std::size_t>(::getpagesize());
  constexpr std::size_t kPages = 64;
  void* at = ::mmap(nullptr, kPages * page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
  ASSERT_NE(at, MAP_FAILED);
  auto* bytes = static_cast<std::byte*>(at);
  const std::uint64_t before = faults_now();
  for (std::size_t i = 0; i < kPages; ++i) {
    bytes[i * page] = std::byte{1};
  }
  EXPECT_GE(faults_now() - before, kPages);
  ::munmap(at, kPages * page);
}

TEST(PageMapping, BasePagesAreTheBasePageSize) {
  const itch::PageMapping pages{100'000, itch::PageRequest::base};
  const itch::PageReport& report = pages.report();
  ASSERT_TRUE(report.mapped) << report.describe();
  EXPECT_NE(pages.data(), nullptr);
  EXPECT_EQ(report.base_page, static_cast<std::size_t>(::getpagesize()));
  EXPECT_EQ(report.bytes % report.base_page, 0U);
  EXPECT_GE(report.bytes, 100'000U);
  EXPECT_TRUE(report.prefaulted);
  EXPECT_EQ(report.huge_bytes, 0U);
#if defined(__APPLE__) || defined(__linux__)
  EXPECT_TRUE(report.verified) << report.describe();
  EXPECT_EQ(report.page_bytes(), report.base_page) << report.describe();
#endif
}

// What a hugepage request got is what the kernel said, whatever it was.
TEST(PageMapping, AHugeRequestClaimsOnlyWhatTheKernelSays) {
  for (const itch::PageRequest request :
       {itch::PageRequest::huge_if_offered, itch::PageRequest::transparent,
        itch::PageRequest::explicit_huge}) {
    const itch::PageMapping pages{std::size_t{8} << 20U, request};
    const itch::PageReport& report = pages.report();
    SCOPED_TRACE(report.describe());
    EXPECT_EQ(report.requested, request);
    EXPECT_EQ(pages.data() != nullptr, report.mapped);
    if (!report.mapped) {
      EXPECT_EQ(pages.size(), 0U);
      EXPECT_NE(report.how.find("nothing was mapped"), std::string::npos);
      EXPECT_EQ(report.page_bytes(), 0U);
      continue;
    }
    EXPECT_LE(report.huge_bytes, report.bytes);
    if (report.page_bytes() == itch::kHugePage) {
      EXPECT_TRUE(report.verified);
      EXPECT_EQ(report.huge_bytes, report.bytes);
    }
    if (!report.verified) {
      EXPECT_EQ(report.page_bytes(), 0U);
    }
  }
}

#ifdef __APPLE__
// LC-57: the SDK calls superpages unsupported on current hardware. Whether this Mac
// refuses or not, a refusal must say so and fall back only where the request allows it.
TEST(PageMapping, OnMacOSASuperpageRefusalIsRecorded) {
  const itch::PageMapping offered{std::size_t{4} << 20U, itch::PageRequest::huge_if_offered};
  const itch::PageMapping exact{std::size_t{4} << 20U, itch::PageRequest::explicit_huge};
  const itch::PageMapping transparent{std::size_t{4} << 20U, itch::PageRequest::transparent};
  EXPECT_FALSE(transparent.report().mapped);
  if (offered.report().how.starts_with("mmap with VM_FLAGS_SUPERPAGE_SIZE_2MB refused")) {
    EXPECT_TRUE(offered.report().mapped);
    EXPECT_EQ(offered.report().page_bytes(), offered.report().base_page);
    EXPECT_FALSE(exact.report().mapped) << exact.report().describe();
  } else {
    EXPECT_FALSE(offered.report().verified) << offered.report().describe();
  }
  std::println("superpage on this Mac: {}", offered.report().describe());
}

// A pool whose pages could not be had has no room at all.
TEST(NodePool, APoolWithNoPagesHasNoRoom) {
  const itch::NodePool<Probe, itch::HandleKind::index, itch::FreeOrder::lifo> pool{
      16, itch::PageRequest::transparent};
  EXPECT_FALSE(pool.pages().mapped);
  EXPECT_FALSE(pool.has_room());
  EXPECT_EQ(pool.capacity(), 0U);
}

// A pool book whose pages were refused refuses every add, and touches no node.
TEST(NodeBook, APoolBookWithNoPagesRefusesEveryAdd) {
  itch::NodeBook<itch::PoolIndexArm> book{
      {.orders = 64, .levels = 64, .pages = itch::PageRequest::transparent}};
  EXPECT_FALSE(book.heap().ready());
  EXPECT_EQ(book.capacity().orders, 0U);
  EXPECT_EQ(book.apply(wire::add(1, 1, 'B', 100, 10)), itch::ApplyStatus::capacity_exhausted);
  EXPECT_EQ(book.live_orders(), 0U);
  EXPECT_EQ(book.nodes_made(), 0U);
}
#endif

// ---------------------------------------------------------------------------------------
// The node book on every arm against the reference book.

constexpr itch::NodeCapacity kCapacity{
    .orders = 2048, .levels = 2048, .pages = itch::PageRequest::huge_if_offered};

template <class Arm>
struct Plain {
  using Book = itch::NodeBook<Arm>;
  static constexpr bool kAged = false;
};
template <class Arm>
struct Aged {
  using Book = itch::NodeBook<Arm>;
  static constexpr bool kAged = true;
};

template <class Case>
class NodeBookArm : public ::testing::Test {};

using Arms = ::testing::Types<Plain<itch::MallocArm>, Aged<itch::MallocArm>,
                              Plain<itch::PmrPoolArm>, Plain<itch::PmrMonotonicArm>,
                              Plain<itch::PoolIndexArm>, Plain<itch::PoolPointerArm>,
#ifdef ITCH_HAVE_MIMALLOC
                              Plain<itch::MimallocArm>,
#endif
                              Plain<itch::PoolFifoArm>>;
TYPED_TEST_SUITE(NodeBookArm, Arms);

template <class Book>
std::vector<std::uint64_t> node_words(const Book& book, std::uint16_t locate) {
  itch::oracle::WordList words;
  itch::oracle::emit_node_side(book, locate, itch::Side::bid, words);
  itch::oracle::emit_node_side(book, locate, itch::Side::ask, words);
  return words.words();
}

std::vector<std::uint64_t> reference_words(const itch::oracle::ReferenceBook& book,
                                           std::uint16_t locate) {
  itch::oracle::WordList words;
  const std::array<std::uint16_t, 1> locates{locate};
  itch::oracle::emit_reference(book, std::span<const std::uint16_t>{locates}, words);
  return words.words();
}

template <class Book>
std::uint64_t whole_node(const Book& book) {
  itch::oracle::WordHash hash;
  itch::oracle::emit_node(book, hash);
  return hash.value();
}

std::uint64_t whole_reference(const itch::oracle::ReferenceBook& book) {
  itch::oracle::WordHash hash;
  itch::oracle::emit_reference(book, hash);
  return hash.value();
}

// Every message through both books, compared as the header says. Returns the messages.
template <class Case>
std::uint64_t replay_against_reference(std::span<const std::byte> stream) {
  using Book = Case::Book;
  itch::HeapAger ager{
      {.seed = 7, .warm_blocks = Case::kAged ? 4096U : 0U, .ring_blocks = Case::kAged ? 64U : 0U}};
  Book book{kCapacity};
  itch::oracle::ReferenceBook reference;
  itch::FrameReader reader{stream};
  std::uint64_t messages = 0;
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != itch::FrameStatus::message) {
      continue;
    }
    ++messages;
    if (Case::kAged) {
      ager.before_message(frame.payload);
    }
    const std::string_view node = to_string(book.apply(frame.payload));
    const std::string_view expected = to_string(reference.apply(frame.payload).status);
    EXPECT_EQ(node, expected) << "message " << messages;
    const std::uint16_t locate =
        frame.payload.size() >= 3 ? itch::read_be<std::uint16_t>(frame.payload.data() + 1) : 0;
    EXPECT_EQ(node_words(book, locate), reference_words(reference, locate))
        << "message " << messages;
    if (messages % 1000 == 0) {
      EXPECT_EQ(whole_node(book), whole_reference(reference)) << "message " << messages;
    }
    if (::testing::Test::HasFailure()) {
      return messages;
    }
  }
  EXPECT_EQ(whole_node(book), whole_reference(reference));
  EXPECT_EQ(book.live_orders(), reference.live_orders());
  return messages;
}

TYPED_TEST(NodeBookArm, TheChurnStreamGivesTheReferenceBook) {
  const std::vector<std::byte> stream = itch::testing::build_stream();
  EXPECT_GT(replay_against_reference<TypeParam>(stream), 200000U);
}

TYPED_TEST(NodeBookArm, EveryOracleFixtureGivesTheReferenceBook) {
  std::size_t fixtures = 0;
  for (const auto& entry : std::filesystem::directory_iterator{ITCH_ORACLE_FIXTURES}) {
    if (entry.path().extension() != ".itch") {
      continue;
    }
    SCOPED_TRACE(entry.path().filename().string());
    std::ifstream in{entry.path(), std::ios::binary};
    const std::vector<char> text{std::istreambuf_iterator<char>{in}, {}};
    std::vector<std::byte> stream(text.size());
    std::memcpy(stream.data(), text.data(), text.size());
    EXPECT_GT(replay_against_reference<TypeParam>(stream), 0U);
    ++fixtures;
  }
  EXPECT_GE(fixtures, 6U);
}

TYPED_TEST(NodeBookArm, ItRefusesPastItsCapacityAsTheFastBookDoes) {
  typename TypeParam::Book book{{.orders = 2, .levels = 2, .pages = itch::PageRequest::base}};
  itch::FastBook<itch::NoWriteObserver, itch::HashIndex, itch::LinkedLevels> fast{
      {.orders = 2, .levels = 2}};
  const std::vector<wire::Bytes> script{
      wire::add(1, 1, 'B', 1, 10),
      wire::add(1, 2, 'B', 1, 20),
      wire::add(1, 3, 'B', 1, 30),
      wire::erase(1, 1),
      wire::add(1, 3, 'B', 1, 30),
      wire::replace(1, 3, 4, 5, 20),
      wire::replace(1, 2, 5, 5, 40),
      wire::add(1, 6, 'S', 1, 50),
      wire::erase(1, 4),
      wire::add(1, 7, 'S', 1, 50),
  };
  for (const wire::Bytes& message : script) {
    EXPECT_EQ(book.apply(message), fast.apply(message));
    EXPECT_EQ(book.live_orders(), fast.live_orders());
    EXPECT_EQ(book.live_levels(), fast.live_levels());
  }
  EXPECT_EQ(book.peak_live_orders(), 2U);
  EXPECT_LE(book.peak_live_levels(), 2U);
}

TEST(NodeBook, TheIndexArmsLinkBy32BitsAndThePointerArmsBy64) {
  static_assert(sizeof(itch::NodeBook<itch::PoolIndexArm>::Order) == 24);
  static_assert(sizeof(itch::NodeBook<itch::PoolIndexArm>::Level) == 40);
  static_assert(sizeof(itch::NodeBook<itch::PoolFifoArm>::Order) == 24);
  static_assert(sizeof(itch::NodeBook<itch::PoolPointerArm>::Order) == 40);
  static_assert(sizeof(itch::NodeBook<itch::PoolPointerArm>::Level) == 56);
  static_assert(sizeof(itch::NodeBook<itch::MallocArm>::Order) == 40);
  static_assert(sizeof(itch::NodeBook<itch::PmrPoolArm>::Level) == 56);
  SUCCEED();
}

// The pools are sized from the counted peak, and every node they hold is faulted in.
TEST(NodeBook, ThePoolArmsTakeTheirPagesUpFront) {
  const itch::NodeBook<itch::PoolIndexArm> book{
      {.orders = 1U << 16U, .levels = 1U << 14U, .pages = itch::PageRequest::huge_if_offered}};
  ASSERT_NE(book.heap().pages(), nullptr);
  EXPECT_TRUE(book.heap().ready());
  EXPECT_TRUE(book.heap().pages()->prefaulted);
  EXPECT_GE(book.heap().pages()->bytes, std::size_t{1} << 16U << 4U);
  const itch::NodeCapacity defaults;
  EXPECT_GE(defaults.orders, 1'924'078U + (1'924'078U / 4));
  EXPECT_GE(defaults.levels, 784'060U + (784'060U / 4));
  EXPECT_EQ(defaults.orders % 4096, 0U);
  EXPECT_EQ(defaults.levels % 4096, 0U);
}

}  // namespace
