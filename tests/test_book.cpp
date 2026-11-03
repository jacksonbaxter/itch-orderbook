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
#include <functional>
#include <limits>
#include <numeric>
#include <span>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

#include "itch/book.hpp"
#include "itch/heap_ager.hpp"
#include "itch/map_book.hpp"
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

// Small enough to fill on purpose, and with few pages, so that the paged index falls
// back as soon as references spread.
constexpr itch::BookCapacity kSmall{
    .orders = 256, .levels = 64, .index_pages = 4, .index_bits = 16, .level_slots = 1024};

template <class Index, class Levels>
using Arm = itch::FastBook<itch::NoWriteObserver, Index, Levels>;
using HashLinked = Arm<itch::HashIndex, itch::LinkedLevels>;
using HashFlat = Arm<itch::HashIndex, itch::FlatLevels>;
using PagedLinked = Arm<itch::PagedIndex<>, itch::LinkedLevels>;
using PagedFlat = Arm<itch::PagedIndex<>, itch::FlatLevels>;

// Runs a check once on each arm of the fast book, named in any failure.
template <class Check>
void for_each_arm(const Check& check) {
  const auto run = [&check]<class Index, class Levels>(std::string_view name) {
    SCOPED_TRACE(name);
    check.template operator()<Index, Levels>();
  };
  run.template operator()<itch::HashIndex, itch::LinkedLevels>("hash, linked");
  run.template operator()<itch::HashIndex, itch::FlatLevels>("hash, flat");
  run.template operator()<itch::PagedIndex<>, itch::LinkedLevels>("paged, linked");
  run.template operator()<itch::PagedIndex<>, itch::FlatLevels>("paged, flat");
}

template <class Book>
class FastAdapter {
 public:
  std::string apply(const wire::Bytes& message) {
    return std::string{to_string(book_.apply(message))};
  }

  [[nodiscard]] Fifo level(std::uint16_t locate, char side, std::uint32_t price) const {
    Fifo fifo;
    const auto* level =
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

  // The prices of a side's levels, best first, as the arm's own walk gives them.
  [[nodiscard]] std::vector<std::uint32_t> prices(std::uint16_t locate, char side) const {
    std::vector<std::uint32_t> prices;
    book_.walk_side(locate, side == 'B' ? itch::Side::bid : itch::Side::ask,
                    [&prices](const auto& level) {
                      prices.push_back(level.price);
                      return true;
                    });
    return prices;
  }

 private:
  Book book_{kSmall};
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

  [[nodiscard]] std::vector<std::uint32_t> prices(std::uint16_t locate, char side) const {
    std::vector<std::uint32_t> prices;
    const auto found = book_.sides().find({locate, side});
    if (found == book_.sides().end()) {
      return prices;
    }
    for (const auto& [price, fifo] : found->second) {
      prices.push_back(price);
    }
    if (side == 'B') {
      std::ranges::reverse(prices);
    }
    return prices;
  }

 private:
  ReferenceBook book_;
};

// The T-DESIGN-1 baseline, on a fresh heap or on one aged as heap_ager.hpp says, small.
template <bool Aged>
class MapAdapter {
 public:
  std::string apply(const wire::Bytes& message) {
    if (Aged) {
      ager_.before_message(message);
    }
    return std::string{to_string(book_.apply(message))};
  }

  [[nodiscard]] Fifo level(std::uint16_t locate, char side, std::uint32_t price) const {
    Fifo fifo;
    const auto& levels = book_.sides()[itch::side_key(locate, to_side(side))];
    const auto found = levels.find(price);
    if (found == levels.end()) {
      return fifo;
    }
    std::uint64_t shares = 0;
    for (const itch::MapBook::Resting& order : found->second.fifo) {
      fifo.push_back({.reference = order.reference, .shares = order.shares});
      shares += order.shares;
    }
    EXPECT_FALSE(fifo.empty()) << "an empty level was kept";
    EXPECT_EQ(found->second.aggregate_shares, shares);
    return fifo;
  }

  [[nodiscard]] Top best(std::uint16_t locate, char side) const {
    const itch::TopOfBook top = book_.best(locate, to_side(side));
    if (!top.present) {
      return kEmpty;
    }
    return {.present = true, .price = top.price, .shares = top.shares, .orders = top.orders};
  }

  [[nodiscard]] std::uint64_t live_orders() const { return book_.live_orders(); }

  [[nodiscard]] std::vector<std::uint32_t> prices(std::uint16_t locate, char side) const {
    std::vector<std::uint32_t> prices;
    book_.walk_side(locate, to_side(side), [&prices](itch::Price4 price, const auto& /*level*/) {
      prices.push_back(price);
      return true;
    });
    return prices;
  }

 private:
  static itch::Side to_side(char side) { return side == 'B' ? itch::Side::bid : itch::Side::ask; }

  itch::HeapAger ager_{
      {.seed = 7, .warm_blocks = Aged ? 4096U : 0U, .ring_blocks = Aged ? 64U : 0U}};
  itch::MapBook book_;
};

template <class Adapter>
class BookScenario : public ::testing::Test {
 protected:
  Adapter book_;  // NOLINT(cppcoreguidelines-non-private-member-variables-in-classes)
};

// The default arm stays first, so that a failure on it is BookScenario/0.
using Books = ::testing::Types<FastAdapter<itch::FastBook<>>, FastAdapter<HashLinked>,
                               FastAdapter<HashFlat>, FastAdapter<PagedLinked>, ReferenceAdapter,
                               MapAdapter<false>, MapAdapter<true>>;
static_assert(std::is_same_v<itch::FastBook<>, PagedFlat>);
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

// What the spec leaves out or says otherwise. Neither book reads the directory, so what
// these show is that the case reaches the book and is treated like any other.

// DS-64. Neither real day has 'M', so this is the only place it runs.
TYPED_TEST(BookScenario, MarketCategoryMListsAnOrdinarySymbol) {
  EXPECT_EQ(this->book_.apply(wire::directory(kLocate, 'M', 'T', 100)), "no_book_effect");
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000)), "applied");
  EXPECT_EQ(this->book_.best(kLocate, 'B'), (Top{true, 5000, 100, 1}));
}

// DS-65. Test symbols carry real order flow. Leaving them out of a statistic is the
// consumer's business, so the book has to hold them exactly as it holds a live one.
TYPED_TEST(BookScenario, ATestSymbolTradesLikeALiveOne) {
  constexpr std::uint16_t kLive = kLocate + 1;
  EXPECT_EQ(this->book_.apply(wire::directory(kLocate, 'Q', 'T', 100)), "no_book_effect");
  EXPECT_EQ(this->book_.apply(wire::directory(kLive, 'Q', 'P', 100)), "no_book_effect");
  std::uint64_t reference = 1;
  for (const std::uint16_t locate : {kLocate, kLive}) {
    const std::uint64_t first = reference++;
    const std::uint64_t second = reference++;
    const std::uint64_t replaced = reference++;
    EXPECT_EQ(this->book_.apply(wire::add(locate, first, 'B', 100, 5000)), "applied");
    EXPECT_EQ(this->book_.apply(wire::add_attributed(locate, second, 'B', 200, 5000)), "applied");
    EXPECT_EQ(this->book_.apply(wire::execute(locate, first, 30)), "applied");
    EXPECT_EQ(this->book_.apply(wire::cancel(locate, second, 50)), "applied");
    EXPECT_EQ(this->book_.apply(wire::replace(locate, second, replaced, 90, 4900)), "applied");
    EXPECT_EQ(this->book_.level(locate, 'B', 5000), (Fifo{{first, 70}}));
    EXPECT_EQ(this->book_.level(locate, 'B', 4900), (Fifo{{replaced, 90}}));
    EXPECT_EQ(this->book_.apply(wire::erase(locate, first)), "applied");
    EXPECT_EQ(this->book_.best(locate, 'B'), (Top{true, 4900, 90, 1}));
  }
  EXPECT_EQ(this->book_.live_orders(), 2U);
}

// DS-66. A book holds shares, whatever a round lot is on this symbol.
TYPED_TEST(BookScenario, ARoundLotDoesNotShapeTheBook) {
  std::uint16_t locate = kLocate;
  std::uint64_t reference = 1;
  for (const std::uint32_t lot : {1U, 10U, 40U, 100U}) {
    EXPECT_EQ(this->book_.apply(wire::directory(locate, 'N', 'P', lot)), "no_book_effect");
    const std::uint64_t odd = reference++;
    const std::uint64_t mixed = reference++;
    EXPECT_EQ(this->book_.apply(wire::add(locate, odd, 'S', 7, 5000)), "applied") << lot;
    EXPECT_EQ(this->book_.apply(wire::add(locate, mixed, 'S', 153, 5000)), "applied") << lot;
    EXPECT_EQ(this->book_.apply(wire::cancel(locate, mixed, 3)), "applied") << lot;
    EXPECT_EQ(this->book_.level(locate, 'S', 5000), (Fifo{{odd, 7}, {mixed, 150}})) << lot;
    ++locate;
  }
}

// DS-69. A cross that did not happen prints zero shares at price zero. On both real days
// every zero-share Q is also zero-price and no other Q is. The two mixed forms are here
// because nothing but those days rules them out.
TYPED_TEST(BookScenario, ACrossOfNoSharesOrNoPriceChangesNothing) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  this->book_.apply(wire::add(kLocate, 2, 'S', 100, 5100));
  EXPECT_EQ(this->book_.apply(wire::cross(kLocate, 0, 0)), "no_book_effect");
  EXPECT_EQ(this->book_.apply(wire::cross(kLocate, 0, 5000)), "no_book_effect");
  EXPECT_EQ(this->book_.apply(wire::cross(kLocate, 100, 0)), "no_book_effect");
  EXPECT_EQ(this->book_.best(kLocate, 'B'), (Top{true, 5000, 100, 1}));
  EXPECT_EQ(this->book_.best(kLocate, 'S'), (Top{true, 5100, 100, 1}));
  EXPECT_EQ(this->book_.level(kLocate, 'B', 0), Fifo{});
  EXPECT_EQ(this->book_.level(kLocate, 'S', 0), Fifo{});
  EXPECT_EQ(this->book_.live_orders(), 2U);
}

// DS-71. The current spec names only Broken Trade and Order Delete after 'E', and 2026
// files carry partial cancels there as well. Both real days here have only deletes, so
// the cancel runs nowhere else.
TYPED_TEST(BookScenario, OrderMessagesAfterSystemEventEStillApply) {
  this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000));
  this->book_.apply(wire::add(kLocate, 2, 'B', 100, 5000));
  EXPECT_EQ(this->book_.apply(wire::system_event('E')), "no_book_effect");
  EXPECT_EQ(this->book_.apply(wire::cancel(kLocate, 1, 40)), "applied");
  EXPECT_EQ(this->book_.level(kLocate, 'B', 5000), (Fifo{{1, 60}, {2, 100}}));
  EXPECT_EQ(this->book_.apply(wire::erase(kLocate, 1)), "applied");
  EXPECT_EQ(this->book_.apply(wire::cancel(kLocate, 2, 100)), "applied");
  EXPECT_EQ(this->book_.apply(wire::system_event('C')), "no_book_effect");
  EXPECT_EQ(this->book_.live_orders(), 0U);
}

// DS-73. Stub quotes sit almost the whole price field away from the market, and real ones
// reach $199,999.99. No arm indexes a level by its price, so a far quote is just the
// farthest level on its side, and it becomes the best once the market in front goes.
TYPED_TEST(BookScenario, AFarStubQuoteIsTheFarthestLevel) {
  constexpr std::uint32_t kStubBid = 1;
  constexpr std::uint32_t kStubAsk = 1'999'999'900;
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 1, 'B', 100, 5000)), "applied");
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 2, 'B', 300, kStubBid)), "applied");
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 3, 'S', 100, 5100)), "applied");
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 4, 'S', 200, kStubAsk)), "applied");
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 5, 'B', 100, 4999)), "applied");
  EXPECT_EQ(this->book_.apply(wire::add(kLocate, 6, 'S', 100, 5101)), "applied");
  EXPECT_EQ(this->book_.prices(kLocate, 'B'), (std::vector<std::uint32_t>{5000, 4999, kStubBid}));
  EXPECT_EQ(this->book_.prices(kLocate, 'S'), (std::vector<std::uint32_t>{5100, 5101, kStubAsk}));
  EXPECT_EQ(this->book_.best(kLocate, 'B'), (Top{true, 5000, 100, 1}));
  EXPECT_EQ(this->book_.best(kLocate, 'S'), (Top{true, 5100, 100, 1}));

  for (const std::uint64_t reference : {1U, 5U, 3U, 6U}) {
    EXPECT_EQ(this->book_.apply(wire::erase(kLocate, reference)), "applied") << reference;
  }
  EXPECT_EQ(this->book_.best(kLocate, 'B'), (Top{true, kStubBid, 300, 1}));
  EXPECT_EQ(this->book_.best(kLocate, 'S'), (Top{true, kStubAsk, 200, 1}));
  EXPECT_EQ(this->book_.level(kLocate, 'S', kStubAsk), (Fifo{{4, 200}}));

  // The quote comes back to the market, and its old level goes.
  EXPECT_EQ(this->book_.apply(wire::replace(kLocate, 4, 7, 200, 5200)), "applied");
  EXPECT_EQ(this->book_.prices(kLocate, 'S'), (std::vector<std::uint32_t>{5200}));
  EXPECT_EQ(this->book_.level(kLocate, 'S', kStubAsk), Fifo{});
}

// Levels added in no particular order come out in price order, on a side deep enough to
// move a flat array to a bigger block twice, and stay in order as levels go from the
// middle and come back.
TYPED_TEST(BookScenario, ManyLevelsStayInPriceOrder) {
  constexpr std::uint32_t kLevels = 30;
  std::vector<std::uint32_t> bids;
  std::vector<std::uint32_t> asks;
  for (std::uint32_t i = 0; i < kLevels; ++i) {
    const std::uint32_t step = (i * 17U) % kLevels;  // Each of 0 to 29 once, out of order.
    ASSERT_EQ(this->book_.apply(wire::add(kLocate, 1 + i, 'B', 10 + i, 5000 - (step * 10))),
              "applied");
    ASSERT_EQ(this->book_.apply(wire::add(kLocate, 101 + i, 'S', 10 + i, 6000 + (step * 10))),
              "applied");
    bids.push_back(5000 - (i * 10));
    asks.push_back(6000 + (i * 10));
  }
  EXPECT_EQ(this->book_.prices(kLocate, 'B'), bids);
  EXPECT_EQ(this->book_.prices(kLocate, 'S'), asks);

  // Every third level goes, the best among them.
  for (std::uint32_t i = 0; i < kLevels; i += 3) {
    ASSERT_EQ(this->book_.apply(wire::erase(kLocate, 1 + i)), "applied");
    ASSERT_EQ(this->book_.apply(wire::erase(kLocate, 101 + i)), "applied");
    const std::uint32_t step = (i * 17U) % kLevels;
    std::erase(bids, 5000 - (step * 10));
    std::erase(asks, 6000 + (step * 10));
  }
  EXPECT_EQ(this->book_.prices(kLocate, 'B'), bids);
  EXPECT_EQ(this->book_.prices(kLocate, 'S'), asks);
  EXPECT_EQ(this->book_.best(kLocate, 'B').price, bids.front());
  EXPECT_EQ(this->book_.best(kLocate, 'S').price, asks.front());

  // And come back, through a replace from the far end.
  ASSERT_EQ(this->book_.apply(wire::add(kLocate, 201, 'B', 5, 5000)), "applied");
  ASSERT_EQ(this->book_.apply(wire::replace(kLocate, 2, 202, 5, 4995)), "applied");
  std::erase(bids, 5000 - (((1U * 17U) % kLevels) * 10));
  bids.push_back(5000);
  bids.push_back(4995);
  std::ranges::sort(bids, std::greater{});
  EXPECT_EQ(this->book_.prices(kLocate, 'B'), bids);
  EXPECT_EQ(this->book_.level(kLocate, 'B', 4995), (Fifo{{202, 5}}));
}

// All five cases as one framed stream, through the whole path a real day takes: nothing
// skipped by the reader, nothing refused by decode, and every status the expected one.
TEST(SpecOmissions, AStreamOfThemWalksDecodesAndApplies) {
  constexpr std::uint16_t kTest = 7;
  constexpr std::uint16_t kLotOfTen = 8;
  const std::vector<std::pair<wire::Bytes, itch::ApplyStatus>> script{
      {wire::system_event('O'), itch::ApplyStatus::no_book_effect},
      {wire::directory(kTest, 'M', 'T', 100), itch::ApplyStatus::no_book_effect},
      {wire::directory(kLotOfTen, 'N', 'P', 10), itch::ApplyStatus::no_book_effect},
      {wire::add(kTest, 1, 'B', 100, 5000), itch::ApplyStatus::applied},
      {wire::add(kLotOfTen, 2, 'S', 7, 5100), itch::ApplyStatus::applied},
      {wire::cross(kTest, 0, 0), itch::ApplyStatus::no_book_effect},
      {wire::system_event('E'), itch::ApplyStatus::no_book_effect},
      {wire::cancel(kTest, 1, 40), itch::ApplyStatus::applied},
      {wire::erase(kTest, 1), itch::ApplyStatus::applied},
      {wire::erase(kLotOfTen, 2), itch::ApplyStatus::applied},
      {wire::system_event('C'), itch::ApplyStatus::no_book_effect},
  };
  std::vector<std::byte> stream;
  for (const auto& [message, status] : script) {
    itch::fixtures::append_frame(stream, message);
  }

  itch::FrameReader reader{stream};
  itch::FastBook<> book{
      {.orders = 16, .levels = 16, .index_pages = 4, .index_bits = 16, .level_slots = 64}};
  for (const auto& [message, status] : script) {
    const itch::Frame frame = reader.next();
    ASSERT_EQ(frame.status, itch::FrameStatus::message);
    ASSERT_TRUE(itch::decode(frame.payload).has_value());
    EXPECT_EQ(book.apply(frame.payload), status) << static_cast<char>(message[0]);
  }
  EXPECT_EQ(reader.next().status, itch::FrameStatus::end_of_input);
  EXPECT_EQ(reader.counters().messages, script.size());
  EXPECT_EQ(reader.counters().unknown_type, 0U);
  EXPECT_EQ(reader.counters().bad_length, 0U);
  EXPECT_EQ(book.live_orders(), 0U);
}

TEST(FastBook, ThePoolsRefuseRatherThanGrow) {
  for_each_arm([]<class Index, class Levels> {
    Arm<Index, Levels> book{
        {.orders = 2, .levels = 2, .index_pages = 1, .index_bits = 16, .level_slots = 64}};
    EXPECT_EQ(book.apply(wire::add(1, 1, 'B', 1, 10)), itch::ApplyStatus::applied);
    EXPECT_EQ(book.apply(wire::add(1, 2, 'B', 1, 20)), itch::ApplyStatus::applied);
    EXPECT_EQ(book.apply(wire::add(1, 3, 'B', 1, 30)), itch::ApplyStatus::capacity_exhausted);
    EXPECT_EQ(book.live_orders(), 2U);
    EXPECT_EQ(book.apply(wire::erase(1, 1)), itch::ApplyStatus::applied);
    EXPECT_EQ(book.apply(wire::add(1, 3, 'B', 1, 30)), itch::ApplyStatus::applied);
    EXPECT_EQ(book.peak_live_orders(), 2U);
    EXPECT_EQ(book.peak_live_levels(), 2U);
  });
}

TEST(FastBook, TheHashIndexKeepsEveryOrderThroughDeletes) {
  // The index multiplies by this constant and keeps the top bits, here 7 of them for 128
  // slots. Multiplying by its inverse picks references that land in one chosen slot, so
  // every order below shares a cluster that wraps past the end of the table.
  constexpr std::uint64_t kInverse = 0xF1DE83E19937733DULL;
  static_assert(kInverse * 0x9E3779B97F4A7C15ULL == 1);
  constexpr std::uint64_t kLastSlot = 127ULL << 57U;
  const auto reference = [](std::uint64_t i) { return (kLastSlot + i) * kInverse; };

  HashLinked book{{.orders = 64, .levels = 64}};
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

// DS-72: a reference fits 32 bits on the days seen, and nothing promises it. Past the
// directory's reach, or with every page taken, a reference goes to the fallback hash and
// is counted there, and every path still finds it.
TEST(FastBook, ThePagedIndexFallsBackRatherThanDropAReference) {
  PagedLinked book{{.orders = 64, .levels = 16, .index_pages = 2, .index_bits = 16}};
  const auto& index = book.index();
  constexpr std::uint64_t kPage = std::uint64_t{1} << 8U;
  const std::vector<std::uint64_t> references{
      1,                        // Page 0.
      kPage + 1,                // Page 1, the last free one.
      (2 * kPage) + 1,          // A third page: none left.
      std::uint64_t{1} << 16U,  // The first reference past the reach.
      std::uint64_t{1} << 32U,  // Past what a default directory reaches.
      std::numeric_limits<std::uint64_t>::max(),
  };
  std::uint32_t shares = 10;
  for (const std::uint64_t reference : references) {
    ASSERT_EQ(book.apply(wire::add(1, reference, 'B', shares++, 5000)), itch::ApplyStatus::applied)
        << reference;
  }
  EXPECT_EQ(index.live_pages(), 2U);
  EXPECT_EQ(index.fallback_live(), 4U);
  EXPECT_EQ(index.fallback_inserts(), 4U);
  for (const std::uint64_t reference : references) {
    EXPECT_EQ(book.apply(wire::add(1, reference, 'S', 1, 6000)),
              itch::ApplyStatus::duplicate_reference)
        << reference;
  }

  // Every path reaches a fallen-back order: execute, cancel, replace and delete.
  EXPECT_EQ(book.apply(wire::execute(1, (2 * kPage) + 1, 2)), itch::ApplyStatus::applied);
  EXPECT_EQ(book.apply(wire::cancel(1, std::uint64_t{1} << 32U, 3)), itch::ApplyStatus::applied);
  EXPECT_EQ(
      book.apply(wire::replace(1, std::numeric_limits<std::uint64_t>::max(), 2 * kPage, 7, 5000)),
      itch::ApplyStatus::applied);
  const auto* level = book.find_level(1, itch::Side::bid, 5000);
  ASSERT_NE(level, nullptr);
  std::vector<Resting> fifo;
  for (std::uint32_t at = level->front; at != itch::kNoIndex; at = book.order_at(at).newer) {
    fifo.push_back({.reference = book.order_at(at).reference, .shares = book.order_at(at).shares});
  }
  EXPECT_EQ(fifo, (Fifo{{1, 10},
                        {kPage + 1, 11},
                        {(2 * kPage) + 1, 10},
                        {std::uint64_t{1} << 16U, 13},
                        {std::uint64_t{1} << 32U, 11},
                        {2 * kPage, 7}}));
  EXPECT_EQ(index.fallback_live(), 4U);  // The new reference still finds no page.

  // A page goes back once its last reference goes, and the next reference gets it.
  EXPECT_EQ(book.apply(wire::erase(1, kPage + 1)), itch::ApplyStatus::applied);
  EXPECT_EQ(index.live_pages(), 1U);
  EXPECT_EQ(book.apply(wire::add(1, (3 * kPage) + 5, 'B', 1, 5000)), itch::ApplyStatus::applied);
  EXPECT_EQ(index.live_pages(), 2U);
  EXPECT_EQ(index.fallback_inserts(), 5U);

  for (const std::uint64_t reference : {std::uint64_t{1}, (2 * kPage) + 1, std::uint64_t{1} << 16U,
                                        std::uint64_t{1} << 32U, 2 * kPage, (3 * kPage) + 5}) {
    EXPECT_EQ(book.apply(wire::erase(1, reference)), itch::ApplyStatus::applied) << reference;
    EXPECT_EQ(book.apply(wire::erase(1, reference)), itch::ApplyStatus::unknown_order) << reference;
  }
  EXPECT_EQ(book.live_orders(), 0U);
  EXPECT_EQ(index.fallback_live(), 0U);
  EXPECT_EQ(index.live_pages(), 0U);
  EXPECT_EQ(index.peak_live_pages(), 2U);
}

// A side's flat array moves to a block twice its size when full. When the arena has no
// such block, the add is refused and nothing changes, and a replace that might need one
// is refused before its old order goes.
TEST(FastBook, AFlatSideThatCannotGrowRefusesAndChangesNothing) {
  // One side takes a block of 8, then 16, and gives the 8 back: 24 entries handed out.
  // A second side reuses that 8, and its 16 does not fit in the 15 left.
  HashFlat book{{.orders = 64, .levels = 64, .level_slots = 39}};
  for (std::uint32_t i = 0; i < 9; ++i) {
    ASSERT_EQ(book.apply(wire::add(1, 1 + i, 'B', 1, 1000 + i)), itch::ApplyStatus::applied) << i;
  }
  EXPECT_EQ(book.levels().relocations(), 1U);
  EXPECT_EQ(book.levels().slots_used(), 24U);
  for (std::uint32_t i = 0; i < 8; ++i) {
    ASSERT_EQ(book.apply(wire::add(2, 101 + i, 'S', 1, 2000 + i)), itch::ApplyStatus::applied) << i;
  }
  const HashFlat before = book;
  EXPECT_EQ(book.apply(wire::add(2, 200, 'S', 1, 3000)), itch::ApplyStatus::capacity_exhausted);
  EXPECT_EQ(book.apply(wire::replace(2, 101, 201, 1, 3000)), itch::ApplyStatus::capacity_exhausted);
  EXPECT_TRUE(book == before);
  // An existing level needs no room.
  EXPECT_EQ(book.apply(wire::add(2, 202, 'S', 1, 2003)), itch::ApplyStatus::applied);
  EXPECT_EQ(book.best(2, itch::Side::ask), (itch::TopOfBook{true, 2000, 1, 1}));
}

TEST(FastBook, TheDefaultObserverCostsNoStorage) {
  static_assert(std::is_empty_v<itch::NoWriteObserver>);
  EXPECT_LT(sizeof(itch::FastBook<>), sizeof(itch::FastBook<itch::oracle::WriteRecorder>));
}

// What the write policy saw for one message.
struct Seen {
  std::vector<LevelId> writes;
  std::vector<LevelId> moves;
};

template <class Book>
Seen seen(Book& book, const wire::Bytes& message) {
  book.observer().clear();
  static_cast<void>(book.apply(message));
  const auto& observer = book.observer();
  EXPECT_EQ(observer.move_count(), observer.moves().size()) << "the recorder ran out of room";
  return {.writes = {observer.kept().begin(), observer.kept().end()},
          .moves = {observer.moves().begin(), observer.moves().end()}};
}

template <class Index, class Levels>
using Recorded = itch::FastBook<itch::oracle::WriteRecorder, Index, Levels>;

// A recording book with room for every move, as the oracle makes it.
template <class Index, class Levels>
struct RecordedBook {
  RecordedBook() { book.observer().reserve_moves(kSmall.levels); }
  Recorded<Index, Levels> book{kSmall};
};

TEST(FastBook, TheObserverSeesEveryLevelAMessageWrites) {
  for_each_arm([]<class Index, class Levels> {
    RecordedBook<Index, Levels> recorded;
    auto& book = recorded.book;
    const auto writes = [&book](const wire::Bytes& message) { return seen(book, message).writes; };
    const LevelId at_5000{.locate = 1, .side = 'B', .price = 5000};
    const LevelId at_4900{.locate = 1, .side = 'B', .price = 4900};
    EXPECT_EQ(writes(wire::add(1, 1, 'B', 100, 5000)), (std::vector{at_5000, at_5000}));
    EXPECT_EQ(writes(wire::execute(1, 1, 10)), (std::vector{at_5000}));
    EXPECT_EQ(writes(wire::replace(1, 1, 2, 10, 4900)),
              (std::vector{at_5000, at_5000, at_4900, at_4900}));
    EXPECT_TRUE(writes(wire::trade(1, 100, 5000)).empty());
    EXPECT_TRUE(writes(wire::execute(1, 99, 10)).empty());
  });
}

// A linked level's neighbours are rewritten when it comes or goes, and each is reported
// as moved. Nothing else is.
TEST(FastBook, TheObserverSeesTheLinkedNeighboursAMessageMoves) {
  RecordedBook<itch::HashIndex, itch::LinkedLevels> recorded;
  auto& book = recorded.book;
  const auto moves = [&book](const wire::Bytes& message) { return seen(book, message).moves; };
  const auto bid = [](std::uint32_t price) {
    return LevelId{.locate = 1, .side = 'B', .price = price};
  };
  EXPECT_TRUE(moves(wire::add(1, 1, 'B', 100, 5000)).empty());
  EXPECT_EQ(moves(wire::add(1, 2, 'B', 100, 4900)), (std::vector{bid(5000)}));
  EXPECT_EQ(moves(wire::add(1, 3, 'B', 100, 4950)), (std::vector{bid(5000), bid(4900)}));
  EXPECT_TRUE(moves(wire::add(1, 4, 'B', 100, 4950)).empty());
  EXPECT_TRUE(moves(wire::execute(1, 4, 10)).empty());
  EXPECT_TRUE(moves(wire::erase(1, 4)).empty());
  EXPECT_EQ(moves(wire::erase(1, 3)), (std::vector{bid(5000), bid(4900)}));
  EXPECT_EQ(moves(wire::add(1, 5, 'B', 100, 5100)), (std::vector{bid(5000)}));
}

// A flat side's entries shift when a level comes or goes, and every one shifted is
// reported as moved, as is every one a full block moves to a bigger one.
TEST(FastBook, TheObserverSeesTheFlatEntriesAMessageMoves) {
  RecordedBook<itch::HashIndex, itch::FlatLevels> recorded;
  auto& book = recorded.book;
  const auto moves = [&book](const wire::Bytes& message) { return seen(book, message).moves; };
  const auto ask = [](std::uint32_t price) {
    return LevelId{.locate = 1, .side = 'S', .price = price};
  };
  EXPECT_TRUE(moves(wire::add(1, 1, 'S', 100, 5000)).empty());
  // The best stays at the end, so a worse ask goes in front of it and shifts it.
  EXPECT_EQ(moves(wire::add(1, 2, 'S', 100, 5100)), (std::vector{ask(5000)}));
  EXPECT_EQ(moves(wire::add(1, 3, 'S', 100, 5050)), (std::vector{ask(5000)}));
  // A new best goes at the end and shifts nothing.
  EXPECT_TRUE(moves(wire::add(1, 4, 'S', 100, 4900)).empty());
  EXPECT_EQ(moves(wire::erase(1, 2)), (std::vector{ask(5050), ask(5000), ask(4900)}));
  EXPECT_TRUE(moves(wire::erase(1, 4)).empty());
  for (std::uint32_t i = 0; i < 6; ++i) {
    static_cast<void>(moves(wire::add(1, 10 + i, 'S', 100, 4000 - i)));
  }
  // Eight levels fill the first block, so a ninth moves all of them to the next.
  std::vector<LevelId> all;
  for (const std::uint32_t price : {5050U, 5000U, 4000U, 3999U, 3998U, 3997U, 3996U, 3995U}) {
    all.push_back(ask(price));
  }
  EXPECT_EQ(moves(wire::add(1, 20, 'S', 100, 3000)), all);
}

// The live order with this reference. A missing one fails here, and comes back all zero.
template <class Book>
itch::RestingOrder resting(const Book& book, std::uint64_t reference) {
  const auto order = book.resting_order(reference);
  EXPECT_TRUE(order.has_value()) << reference;
  return order.value_or(itch::RestingOrder{});
}

TEST(FastBook, RestingOrderReportsWhereAnOrderIsAndWhatItHolds) {
  for_each_arm([]<class Index, class Levels> {
    Arm<Index, Levels> book{kSmall};
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
  });
}

constexpr itch::BookCapacity kRestartCapacity{
    .orders = 64, .levels = 64, .index_pages = 4, .index_bits = 16, .level_slots = 1024};

// Every slot of every table, in use or not, and the counters. A book compares equal
// only if each byte it holds does, its free lists and its index among them.
template <class Book>
void expect_same_book(const Book& actual, const Book& expected) {
  EXPECT_EQ(actual.live_orders(), expected.live_orders());
  EXPECT_EQ(actual.live_levels(), expected.live_levels());
  EXPECT_EQ(actual.peak_live_orders(), expected.peak_live_orders());
  EXPECT_EQ(actual.peak_live_levels(), expected.peak_live_levels());
  EXPECT_TRUE(actual == expected);
}

template <class Run>
std::string report_of(const Run& run) {
  testing::internal::CaptureStdout();
  run.invariants().print_report();
  return testing::internal::GetCapturedStdout();
}

template <class Run>
void expect_same_run(const Run& actual, const Run& expected) {
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
  for_each_arm([]<class Index, class Levels> {
    Arm<Index, Levels> book{kRestartCapacity};
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
  });
}

TEST(OracleRun, RestartLeavesNothingOfTheLastStream) {
  for_each_arm([]<class Index, class Levels> {
    using Run = itch::oracle::BasicOracleRun<Index, Levels>;
    const itch::oracle::OracleConfig config{.capacity = kRestartCapacity,
                                            .hash_every = 5,
                                            .exhaustive = true,
                                            .exhaustive_locates = {}};
    const Run fresh{config};
    Run control{config};
    Run used{config};

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
  });
}

}  // namespace
