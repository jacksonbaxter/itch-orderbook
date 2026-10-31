// Hand-built scenarios, run against the fast book and the reference book alike.
//
// Each test states the book it expects by hand. Neither book is the other's expected
// value here, so a shared misreading of a scenario would have to be written into the
// test as well.
#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <numeric>
#include <span>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

#include "itch/book.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "oracle/canonical.hpp"
#include "oracle/diff.hpp"
#include "oracle/reference_book.hpp"
#include "oracle/wire_builder.hpp"
#include "support/fixtures.hpp"

namespace {

using itch::oracle::LevelId;
using itch::oracle::ReferenceBook;
namespace wire = itch::oracle::wire;

struct Resting {
  std::uint64_t reference;
  std::uint32_t shares;
  friend bool operator==(const Resting&, const Resting&) = default;
};
using Fifo = std::vector<Resting>;

struct Top {
  bool present;
  std::uint32_t price;
  std::uint64_t shares;
  std::uint32_t orders;
  friend bool operator==(const Top&, const Top&) = default;
};

constexpr Top kEmpty{.present = false, .price = 0, .shares = 0, .orders = 0};

class FastAdapter {
 public:
  std::string apply(const wire::Bytes& message) {
    return std::string{to_string(book_.apply(message))};
  }

  [[nodiscard]] Fifo level(std::uint16_t locate, char side, std::uint32_t price) const {
    Fifo fifo;
    const itch::BookLevel* level =
        book_.find_level(locate, side == 'B' ? itch::Side::bid : itch::Side::ask, price);
    if (level == nullptr) {
      return fifo;
    }
    std::uint64_t shares = 0;
    for (std::uint32_t at = level->front; at != itch::kNoIndex; at = book_.order_at(at).newer) {
      fifo.push_back(
          {.reference = book_.order_at(at).reference, .shares = book_.order_at(at).shares});
      shares += book_.order_at(at).shares;
    }
    EXPECT_EQ(level->order_count, fifo.size());
    EXPECT_EQ(level->aggregate_shares, shares);
    return fifo;
  }

  [[nodiscard]] Top best(std::uint16_t locate, char side) const {
    const itch::TopOfBook top = book_.best(locate, side == 'B' ? itch::Side::bid : itch::Side::ask);
    if (!top.present) {
      return kEmpty;
    }
    return {.present = true, .price = top.price, .shares = top.shares, .orders = top.orders};
  }

  [[nodiscard]] std::uint64_t live_orders() const { return book_.live_orders(); }

 private:
  itch::FastBook<> book_{{.orders = 256, .levels = 64}};
};

class ReferenceAdapter {
 public:
  std::string apply(const wire::Bytes& message) {
    return std::string{to_string(book_.apply(message).status)};
  }

  [[nodiscard]] Fifo level(std::uint16_t locate, char side, std::uint32_t price) const {
    Fifo fifo;
    const ReferenceBook::Fifo* level =
        book_.level(LevelId{.locate = locate, .side = side, .price = price});
    if (level != nullptr) {
      for (const ReferenceBook::Resting& order : *level) {
        fifo.push_back({.reference = order.reference, .shares = order.shares});
      }
    }
    return fifo;
  }

  [[nodiscard]] Top best(std::uint16_t locate, char side) const {
    const itch::oracle::RefTop top = book_.best(locate, side);
    if (!top.present) {
      return kEmpty;
    }
    return {.present = true, .price = top.price, .shares = top.shares, .orders = top.orders};
  }

  [[nodiscard]] std::uint64_t live_orders() const { return book_.live_orders(); }

 private:
  ReferenceBook book_;
};

template <class Adapter>
class BookScenario : public ::testing::Test {
 protected:
  Adapter book_;  // NOLINT(cppcoreguidelines-non-private-member-variables-in-classes)
};

using Books = ::testing::Types<FastAdapter, ReferenceAdapter>;
TYPED_TEST_SUITE(BookScenario, Books);

constexpr std::uint16_t kLocate = 7;

TYPED_TEST(BookScenario, AddRestsAtTheBackOfItsLevel) {
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000)), "applied");
  EXPECT_EQ(this->book_.apply(wire::add_attributed(kLocate, 2, 'B', 200, 5000)), "applied");
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 3, 'S', 50, 5100)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'B', 5000), (Fifo{{1, 100}, {2, 200}}));
  EXPECT_EQ(this->book_.best(kLocate, 'B'), (Top{true, 5000, 300, 2}));
  EXPECT_EQ(this->book_.best(kLocate, 'S'), (Top{true, 5100, 50, 1}));
}

TYPED_TEST(BookScenario, BestPriceIsTheHighestBidAndTheLowestAsk) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  this->book_.apply(wire::add(kLocate, 2, 'B', 100, 5200));
  this->book_.apply(wire::add(kLocate, 3, 'B', 100, 5100));
  this->book_.apply(wire::add(kLocate, 4, 'S', 100, 5400));
  this->book_.apply(wire::add(kLocate, 5, 'S', 100, 5300));
  this->book_.apply(wire::add(kLocate, 6, 'S', 100, 5350));
  EXPECT_EQ(this->book_.best(kLocate, 'B'), (Top{true, 5200, 100, 1}));
  EXPECT_EQ(this->book_.best(kLocate, 'S'), (Top{true, 5300, 100, 1}));
}

TYPED_TEST(BookScenario, PartialExecuteKeepsTheOrderInPlace) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  this->book_.apply(wire::add(kLocate, 2, 'B', 100, 5000));
  EXPECT_EQ(this->book_.apply(wire::execute(kLocate, 1, 30)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'B', 5000), (Fifo{{1, 70}, {2, 100}}));
  EXPECT_EQ(this->book_.best(kLocate, 'B'), (Top{true, 5000, 170, 2}));
}

TYPED_TEST(BookScenario, FullExecuteRemovesTheOrderWithNoDelete) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  this->book_.apply(wire::add(kLocate, 2, 'B', 100, 5000));
  EXPECT_EQ(this->book_.apply(wire::execute(kLocate, 1, 100)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'B', 5000), (Fifo{{2, 100}}));
  EXPECT_EQ(this->book_.apply(wire::erase(kLocate, 1)), "unknown_order");
}

TYPED_TEST(BookScenario, ExecuteWithPriceDeductsAtTheOrdersLevel) {
  this->book_.apply(wire::add(kLocate, 1, 'S', 100, 5000));
  EXPECT_EQ(this->book_.apply(wire::execute_with_price(kLocate, 1, 40, 4900)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'S', 5000), (Fifo{{1, 60}}));
  EXPECT_EQ(this->book_.level(kLocate, 'S', 4900), Fifo{});
}

TYPED_TEST(BookScenario, PartialCancelDeductsShares) {
  this->book_.apply(wire::add(kLocate, 1, 'S', 100, 5000));
  EXPECT_EQ(this->book_.apply(wire::cancel(kLocate, 1, 25)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'S', 5000), (Fifo{{1, 75}}));
}

TYPED_TEST(BookScenario, DeleteTakesAnOrderFromTheMiddle) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  this->book_.apply(wire::add(kLocate, 2, 'B', 200, 5000));
  this->book_.apply(wire::add(kLocate, 3, 'B', 300, 5000));
  EXPECT_EQ(this->book_.apply(wire::erase(kLocate, 2)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'B', 5000), (Fifo{{1, 100}, {3, 300}}));
  EXPECT_EQ(this->book_.best(kLocate, 'B'), (Top{true, 5000, 400, 2}));
}

TYPED_TEST(BookScenario, DeletingTheLastOrderRemovesTheLevel) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  this->book_.apply(wire::add(kLocate, 2, 'B', 100, 4900));
  EXPECT_EQ(this->book_.apply(wire::erase(kLocate, 1)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'B', 5000), Fifo{});
  EXPECT_EQ(this->book_.best(kLocate, 'B'), (Top{true, 4900, 100, 1}));
  EXPECT_EQ(this->book_.apply(wire::erase(kLocate, 2)), "applied");
  EXPECT_EQ(this->book_.best(kLocate, 'B'), kEmpty);
  EXPECT_EQ(this->book_.live_orders(), 0U);
}

TYPED_TEST(BookScenario, ReplaceWithAPriceChangeMovesLevels) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  this->book_.apply(wire::add(kLocate, 2, 'B', 100, 4900));
  EXPECT_EQ(this->book_.apply(wire::replace(kLocate, 1, 11, 150, 4900)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'B', 5000), Fifo{});
  EXPECT_EQ(this->book_.level(kLocate, 'B', 4900), (Fifo{{2, 100}, {11, 150}}));
  EXPECT_EQ(this->book_.best(kLocate, 'B'), (Top{true, 4900, 250, 2}));
  EXPECT_EQ(this->book_.apply(wire::erase(kLocate, 1)), "unknown_order");
}

TYPED_TEST(BookScenario, ReplaceToTheSamePriceLosesPriority) {
  this->book_.apply(wire::add(kLocate, 1, 'S', 100, 5000));
  this->book_.apply(wire::add(kLocate, 2, 'S', 100, 5000));
  this->book_.apply(wire::add(kLocate, 3, 'S', 100, 5000));
  EXPECT_EQ(this->book_.apply(wire::replace(kLocate, 1, 11, 100, 5000)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'S', 5000), (Fifo{{2, 100}, {3, 100}, {11, 100}}));
}

TYPED_TEST(BookScenario, ReplaceOfTheOnlyOrderKeepsItsLevel) {
  this->book_.apply(wire::add(kLocate, 1, 'S', 100, 5000));
  EXPECT_EQ(this->book_.apply(wire::replace(kLocate, 1, 11, 60, 5000)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'S', 5000), (Fifo{{11, 60}}));
  EXPECT_EQ(this->book_.best(kLocate, 'S'), (Top{true, 5000, 60, 1}));
}

TYPED_TEST(BookScenario, ReplaceKeepsSideAndLocate) {
  this->book_.apply(wire::add(kLocate, 1, 'S', 100, 5000));
  EXPECT_EQ(this->book_.apply(wire::replace(kLocate, 1, 11, 100, 4000)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'S', 4000), (Fifo{{11, 100}}));
  EXPECT_EQ(this->book_.level(kLocate, 'B', 4000), Fifo{});
}

TYPED_TEST(BookScenario, ExtremeLocatesAreSeparateBooks) {
  this->book_.apply(wire::add(0, 1, 'B', 100, 5000));
  this->book_.apply(wire::add(65535, 2, 'B', 200, 5000));
  EXPECT_EQ(this->book_.best(0, 'B'), (Top{true, 5000, 100, 1}));
  EXPECT_EQ(this->book_.best(65535, 'B'), (Top{true, 5000, 200, 1}));
  EXPECT_EQ(this->book_.apply(wire::erase(65535, 2)), "applied");
  EXPECT_EQ(this->book_.best(0, 'B'), (Top{true, 5000, 100, 1}));
  EXPECT_EQ(this->book_.best(65535, 'B'), kEmpty);
}

TYPED_TEST(BookScenario, ExtremeReferencesAndPrices) {
  constexpr std::uint64_t kMaxReference = std::numeric_limits<std::uint64_t>::max();
  constexpr std::uint32_t kMaxPrice = std::numeric_limits<std::uint32_t>::max();
  constexpr std::uint32_t kMaxShares = std::numeric_limits<std::uint32_t>::max();
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, kMaxReference, 'S', kMaxShares, kMaxPrice)),
            "applied");
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 0, 'S', kMaxShares, kMaxPrice)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'S', kMaxPrice),
            (Fifo{{kMaxReference, kMaxShares}, {0, kMaxShares}}));
  EXPECT_EQ(this->book_.best(kLocate, 'S'),
            (Top{true, kMaxPrice, std::uint64_t{2} * kMaxShares, 2}));
  EXPECT_EQ(this->book_.apply(wire::erase(kLocate, kMaxReference)), "applied");
  EXPECT_EQ(this->book_.apply(wire::erase(kLocate, 0)), "applied");
}

TYPED_TEST(BookScenario, ZeroPriceIsAPrice) {
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 1, 'B', 100, 0)), "applied");
  EXPECT_EQ(this->book_.best(kLocate, 'B'), (Top{true, 0, 100, 1}));
}

TYPED_TEST(BookScenario, MessagesThatDoNotChangeTheBook) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  EXPECT_EQ(this->book_.apply(wire::trade(kLocate, 100, 5000)), "no_book_effect");
  EXPECT_EQ(this->book_.apply(wire::cross(kLocate, 100, 5000)), "no_book_effect");
  EXPECT_EQ(this->book_.apply(wire::broken(kLocate)), "no_book_effect");
  EXPECT_EQ(this->book_.apply(wire::trading_action(kLocate, 'H')), "no_book_effect");
  EXPECT_EQ(this->book_.apply(wire::system_event('O')), "no_book_effect");
  EXPECT_EQ(this->book_.level(kLocate, 'B', 5000), (Fifo{{1, 100}}));
}

TYPED_TEST(BookScenario, EveryFixtureMessageThatIsNotAnOrderChangesNothing) {
  for (const auto& message : itch::fixtures::all_messages()) {
    const char type = static_cast<char>(message[0]);
    if (!std::string_view{"AFECXDU"}.contains(type)) {
      EXPECT_EQ(this->book_.apply(message), "no_book_effect") << type;
    }
  }
  EXPECT_EQ(this->book_.live_orders(), 0U);
}

TYPED_TEST(BookScenario, ADeadReferenceIsRefusedAndChangesNothing) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  EXPECT_EQ(this->book_.apply(wire::execute(kLocate, 2, 10)), "unknown_order");
  EXPECT_EQ(this->book_.apply(wire::execute_with_price(kLocate, 2, 10, 1)), "unknown_order");
  EXPECT_EQ(this->book_.apply(wire::cancel(kLocate, 2, 10)), "unknown_order");
  EXPECT_EQ(this->book_.apply(wire::erase(kLocate, 2)), "unknown_order");
  EXPECT_EQ(this->book_.apply(wire::replace(kLocate, 2, 3, 10, 5000)), "unknown_order");
  EXPECT_EQ(this->book_.level(kLocate, 'B', 5000), (Fifo{{1, 100}}));
}

TYPED_TEST(BookScenario, AnotherLocatesOrderIsRefused) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  EXPECT_EQ(this->book_.apply(wire::execute(kLocate + 1, 1, 10)), "locate_mismatch");
  EXPECT_EQ(this->book_.apply(wire::erase(kLocate + 1, 1)), "locate_mismatch");
  EXPECT_EQ(this->book_.apply(wire::replace(kLocate + 1, 1, 2, 10, 5000)), "locate_mismatch");
  EXPECT_EQ(this->book_.level(kLocate, 'B', 5000), (Fifo{{1, 100}}));
}

TYPED_TEST(BookScenario, DeductingMoreThanRemainsIsRefused) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  EXPECT_EQ(this->book_.apply(wire::execute(kLocate, 1, 101)), "over_deduction");
  EXPECT_EQ(this->book_.apply(wire::cancel(kLocate, 1, 101)), "over_deduction");
  EXPECT_EQ(this->book_.level(kLocate, 'B', 5000), (Fifo{{1, 100}}));
}

TYPED_TEST(BookScenario, LiveReferencesCannotBeReused) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  this->book_.apply(wire::add(kLocate, 2, 'B', 100, 5000));
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000)), "duplicate_reference");
  EXPECT_EQ(this->book_.apply(wire::replace(kLocate, 1, 2, 100, 5000)), "duplicate_reference");
  EXPECT_EQ(this->book_.apply(wire::replace(kLocate, 1, 1, 100, 5000)), "duplicate_reference");
  EXPECT_EQ(this->book_.level(kLocate, 'B', 5000), (Fifo{{1, 100}, {2, 100}}));
}

TYPED_TEST(BookScenario, AReferenceComesBackAfterTheOrderDies) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  this->book_.apply(wire::erase(kLocate, 1));
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 1, 'S', 50, 6000)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'S', 6000), (Fifo{{1, 50}}));
}

TYPED_TEST(BookScenario, ASideThatIsNeitherBuyNorSellIsRefused) {
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 1, 'X', 100, 5000)), "bad_side");
  EXPECT_EQ(this->book_.live_orders(), 0U);
}

TEST(FastBook, ThePoolsRefuseRatherThanGrow) {
  itch::FastBook<> book{{.orders = 2, .levels = 2}};
  EXPECT_EQ(book.apply(wire::add(1, 1, 'B', 1, 10)), itch::ApplyStatus::applied);
  EXPECT_EQ(book.apply(wire::add(1, 2, 'B', 1, 20)), itch::ApplyStatus::applied);
  EXPECT_EQ(book.apply(wire::add(1, 3, 'B', 1, 30)), itch::ApplyStatus::capacity_exhausted);
  EXPECT_EQ(book.live_orders(), 2U);
  EXPECT_EQ(book.apply(wire::erase(1, 1)), itch::ApplyStatus::applied);
  EXPECT_EQ(book.apply(wire::add(1, 3, 'B', 1, 30)), itch::ApplyStatus::applied);
  EXPECT_EQ(book.peak_live_orders(), 2U);
  EXPECT_EQ(book.peak_live_levels(), 2U);
}

TEST(FastBook, TheHashIndexKeepsEveryOrderThroughDeletes) {
  // The index multiplies by this constant and keeps the top bits, here 7 of them for 128
  // slots. Multiplying by its inverse picks references that land in one chosen slot, so
  // every order below shares a cluster that wraps past the end of the table.
  constexpr std::uint64_t kInverse = 0xF1DE83E19937733DULL;
  static_assert(kInverse * 0x9E3779B97F4A7C15ULL == 1);
  constexpr std::uint64_t kLastSlot = 127ULL << 57U;
  const auto reference = [](std::uint64_t i) { return (kLastSlot + i) * kInverse; };

  itch::FastBook<> book{{.orders = 64, .levels = 64}};
  for (std::uint64_t i = 1; i <= 40; ++i) {
    ASSERT_EQ(book.apply(wire::add(1, reference(i), 'B', 1, 10)), itch::ApplyStatus::applied);
  }
  for (std::uint64_t i = 1; i <= 40; i += 2) {
    ASSERT_EQ(book.apply(wire::erase(1, reference(i))), itch::ApplyStatus::applied);
  }
  for (std::uint64_t i = 1; i <= 40; i += 2) {
    ASSERT_EQ(book.apply(wire::erase(1, reference(i))), itch::ApplyStatus::unknown_order);
  }
  for (std::uint64_t i = 2; i <= 40; i += 2) {
    ASSERT_EQ(book.apply(wire::erase(1, reference(i))), itch::ApplyStatus::applied);
  }
  EXPECT_EQ(book.live_orders(), 0U);
}

TEST(FastBook, TheDefaultObserverCostsNoStorage) {
  static_assert(std::is_empty_v<itch::NoWriteObserver>);
  EXPECT_LT(sizeof(itch::FastBook<>), sizeof(itch::FastBook<itch::oracle::WriteRecorder>));
}

TEST(FastBook, TheObserverSeesEveryLevelAMessageWrites) {
  itch::oracle::OracleFastBook book{{.orders = 16, .levels = 16}};
  const auto writes = [&book](const wire::Bytes& message) {
    book.observer().clear();
    static_cast<void>(book.apply(message));
    return std::vector<LevelId>(book.observer().kept().begin(), book.observer().kept().end());
  };
  const LevelId at_5000{.locate = 1, .side = 'B', .price = 5000};
  const LevelId at_4900{.locate = 1, .side = 'B', .price = 4900};
  EXPECT_EQ(writes(wire::add(1, 1, 'B', 100, 5000)), (std::vector{at_5000, at_5000}));
  EXPECT_EQ(writes(wire::execute(1, 1, 10)), (std::vector{at_5000}));
  EXPECT_EQ(writes(wire::replace(1, 1, 2, 10, 4900)),
            (std::vector{at_5000, at_5000, at_4900, at_4900}));
  EXPECT_TRUE(writes(wire::trade(1, 100, 5000)).empty());
  EXPECT_TRUE(writes(wire::execute(1, 99, 10)).empty());
}

// The live order with this reference. A missing one fails here, and comes back all zero.
itch::RestingOrder resting(const itch::FastBook<>& book, std::uint64_t reference) {
  const auto order = book.resting_order(reference);
  EXPECT_TRUE(order.has_value()) << reference;
  return order.value_or(itch::RestingOrder{});
}

TEST(FastBook, RestingOrderReportsWhereAnOrderIsAndWhatItHolds) {
  itch::FastBook<> book{{.orders = 16, .levels = 16}};
  EXPECT_FALSE(book.resting_order(1).has_value());
  ASSERT_EQ(book.apply(wire::add(7, 1, 'S', 100, 5000)), itch::ApplyStatus::applied);
  ASSERT_EQ(book.apply(wire::add(7, 2, 'S', 200, 5000)), itch::ApplyStatus::applied);

  const itch::RestingOrder first = resting(book, 1);
  EXPECT_EQ(first.reference, 1U);
  EXPECT_EQ(first.locate, 7U);
  EXPECT_EQ(first.side, itch::Side::ask);
  EXPECT_EQ(first.price, 5000U);
  EXPECT_EQ(first.shares, 100U);
  EXPECT_TRUE(first.at_front);
  EXPECT_FALSE(resting(book, 2).at_front);

  ASSERT_EQ(book.apply(wire::execute(7, 1, 40)), itch::ApplyStatus::applied);
  EXPECT_EQ(resting(book, 1).shares, 60U);
  ASSERT_EQ(book.apply(wire::execute(7, 1, 60)), itch::ApplyStatus::applied);
  EXPECT_FALSE(book.resting_order(1).has_value());
  EXPECT_TRUE(resting(book, 2).at_front);

  // A replace at the same price goes to the back, under a new reference.
  ASSERT_EQ(book.apply(wire::add(7, 3, 'S', 300, 5000)), itch::ApplyStatus::applied);
  ASSERT_EQ(book.apply(wire::replace(7, 2, 4, 250, 5000)), itch::ApplyStatus::applied);
  EXPECT_FALSE(book.resting_order(2).has_value());
  EXPECT_TRUE(resting(book, 3).at_front);
  EXPECT_FALSE(resting(book, 4).at_front);
  EXPECT_EQ(resting(book, 4).shares, 250U);
}

constexpr itch::BookCapacity kRestartCapacity{.orders = 64, .levels = 64};

// Every slot of both pools and every side's record, in use or not, and the counters. The
// hash index has no accessor. What it holds shows in the lookups the tests make of it.
template <class Book>
void expect_same_book(const Book& actual, const Book& expected) {
  EXPECT_EQ(actual.live_orders(), expected.live_orders());
  EXPECT_EQ(actual.live_levels(), expected.live_levels());
  EXPECT_EQ(actual.peak_live_orders(), expected.peak_live_orders());
  EXPECT_EQ(actual.peak_live_levels(), expected.peak_live_levels());
  for (std::uint32_t at = 0; at < kRestartCapacity.orders; ++at) {
    const itch::BookOrder& a = actual.order_at(at);
    const itch::BookOrder& e = expected.order_at(at);
    EXPECT_EQ(a.reference, e.reference) << at;
    EXPECT_EQ(a.shares, e.shares) << at;
    EXPECT_EQ(a.level, e.level) << at;
    EXPECT_EQ(a.older, e.older) << at;
    EXPECT_EQ(a.newer, e.newer) << at;
  }
  for (std::uint32_t at = 0; at < kRestartCapacity.levels; ++at) {
    const itch::BookLevel& a = actual.level_at(at);
    const itch::BookLevel& e = expected.level_at(at);
    EXPECT_EQ(a.aggregate_shares, e.aggregate_shares) << at;
    EXPECT_EQ(a.price, e.price) << at;
    EXPECT_EQ(a.order_count, e.order_count) << at;
    EXPECT_EQ(a.front, e.front) << at;
    EXPECT_EQ(a.back, e.back) << at;
    EXPECT_EQ(a.better, e.better) << at;
    EXPECT_EQ(a.worse, e.worse) << at;
    EXPECT_EQ(a.locate, e.locate) << at;
    EXPECT_EQ(a.side, e.side) << at;
  }
  const auto sides = actual.sides();
  const auto expected_sides = expected.sides();
  ASSERT_EQ(sides.size(), expected_sides.size());
  for (std::size_t key = 0; key < sides.size(); ++key) {
    EXPECT_EQ(sides[key].level, expected_sides[key].level) << key;
    EXPECT_EQ(sides[key].price, expected_sides[key].price) << key;
    EXPECT_EQ(sides[key].shares, expected_sides[key].shares) << key;
    EXPECT_EQ(sides[key].orders, expected_sides[key].orders) << key;
  }
}

std::string report_of(const itch::oracle::OracleRun& run) {
  testing::internal::CaptureStdout();
  run.invariants().print_report();
  return testing::internal::GetCapturedStdout();
}

void expect_same_run(const itch::oracle::OracleRun& actual,
                     const itch::oracle::OracleRun& expected) {
  EXPECT_TRUE(actual.divergences().empty());
  EXPECT_TRUE(expected.divergences().empty());
  EXPECT_TRUE(actual.counters() == expected.counters());
  EXPECT_EQ(actual.last_hash(), expected.last_hash());
  EXPECT_EQ(actual.reference().live_orders(), expected.reference().live_orders());
  EXPECT_EQ(actual.reference().sides().size(), expected.reference().sides().size());
  EXPECT_EQ(report_of(actual), report_of(expected));
  EXPECT_EQ(actual.invariants().market_open(), expected.invariants().market_open());
  for (std::uint32_t locate = 0; locate < 65536U; ++locate) {
    const auto id = static_cast<std::uint16_t>(locate);
    EXPECT_EQ(actual.invariants().trading_state(id), expected.invariants().trading_state(id));
  }
  expect_same_book(actual.fast(), expected.fast());
  expect_same_book(actual.production(), expected.production());
}

// Touches each table: three locates, both ends of the range among them, a crossed book,
// freed orders and levels, an emptied side, a refused message and a reopening window.
std::vector<wire::Bytes> restart_traffic(std::uint16_t locate) {
  return {wire::system_event('Q'),
          wire::trading_action(locate, 'H'),
          wire::trading_action(locate, 'T'),
          wire::add(locate, 1, 'B', 100, 5000),
          wire::add(locate, 2, 'S', 100, 4900),
          wire::add(65535, 3, 'B', 50, 6000),
          wire::add(0, 4, 'S', 70, 6100),
          wire::add(locate, 5, 'B', 10, 4800),
          wire::execute(locate, 1, 40),
          wire::cancel(locate, 2, 10),
          wire::erase(0, 4),
          wire::replace(locate, 5, 6, 20, 4700),
          wire::erase(locate, 99),
          wire::erase(65535, 3),
          wire::add(locate, 7, 'S', 30, 5000)};
}

TEST(Canonical, TheWholeBookWalkSeesWhatAWalkOfEveryLocateSees) {
  itch::FastBook<> book{kRestartCapacity};
  std::uint64_t reference = 0;
  for (const std::uint16_t locate : std::array<std::uint16_t, 5>{0, 1, 2, 65534, 65535}) {
    ASSERT_EQ(book.apply(wire::add(locate, ++reference, 'B', 100, 5000)),
              itch::ApplyStatus::applied);
    ASSERT_EQ(book.apply(wire::add(locate, ++reference, 'S', 100, 5100)),
              itch::ApplyStatus::applied);
  }
  std::vector<std::uint16_t> every(65536);
  std::ranges::iota(every, std::uint16_t{0});

  itch::oracle::WordList whole;
  itch::oracle::WordList named;
  itch::oracle::emit_fast(book, whole);
  itch::oracle::emit_fast(book, std::span<const std::uint16_t>{every}, named);
  EXPECT_EQ(whole.words(), named.words());
  // Six words to a side, three to its level and two to its order.
  EXPECT_EQ(whole.words().size(), std::size_t{10} * 11);
}

TEST(OracleRun, RestartLeavesNothingOfTheLastStream) {
  const itch::oracle::OracleConfig config{
      .capacity = kRestartCapacity, .hash_every = 5, .exhaustive = true, .exhaustive_locates = {}};
  const itch::oracle::OracleRun fresh{config};
  itch::oracle::OracleRun control{config};
  itch::oracle::OracleRun used{config};

  // The directory goes to the used run alone, so that its symbol has to be gone: the
  // report names a crossed symbol.
  const wire::Bytes directory = itch::fixtures::all_messages()[1];
  const std::uint16_t locate = itch::read_header(directory.data()).stock_locate;
  const std::vector<wire::Bytes> traffic = restart_traffic(locate);
  ASSERT_TRUE(used.on_message(directory));
  for (const wire::Bytes& message : traffic) {
    ASSERT_TRUE(used.on_message(message));
  }
  used.finish();
  ASSERT_GT(used.fast().live_orders(), 0U);
  ASSERT_GT(used.reference().live_orders(), 0U);
  ASSERT_GT(used.invariants().inv1_violations(), 0U);
  ASSERT_GT(used.invariants().crossed(), 0U);
  ASSERT_NE(used.last_hash(), 0U);
  ASSERT_NE(report_of(used), report_of(control));

  used.restart(fresh);
  expect_same_run(used, control);
  EXPECT_EQ(report_of(used), report_of(fresh));
  for (std::uint64_t reference = 1; reference <= 7; ++reference) {
    EXPECT_FALSE(used.fast().resting_order(reference).has_value()) << reference;
  }

  // The same traffic from here takes the same slots, so the free lists were restored
  // too, and the report shows no symbol that only the first stream named.
  for (const wire::Bytes& message : traffic) {
    ASSERT_TRUE(used.on_message(message));
    ASSERT_TRUE(control.on_message(message));
  }
  used.finish();
  control.finish();
  expect_same_run(used, control);
}

}  // namespace
