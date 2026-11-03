// The checks a fixture cannot reach, because the feed is right and the book is wrong.
//
// TradeRun is templated on the book, so a book that misbehaves on purpose drives it here,
// with no hook in the real one. Each fault below is one way a book could fail, and each
// test names the check that has to catch it. The last test runs the same stream through
// the same wrapper with no fault, as the control.
#include <gtest/gtest.h>

#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <optional>
#include <span>
#include <string_view>
#include <vector>

#include "itch/book.hpp"
#include "itch/messages.hpp"
#include "oracle/wire_builder.hpp"
#include "trades/trade_checks.hpp"

namespace {

namespace wire = itch::oracle::wire;
using itch::ApplyStatus;
using itch::BookCapacity;
using itch::Price4;
using itch::RestingOrder;
using itch::Side;
using itch::TopOfBook;
using itch::trades::Check;
using itch::trades::RunConfig;
using itch::trades::TradeRun;
using itch::trades::WriteCounter;

enum class Fault : std::uint8_t {
  none,
  extra_share,     // Reports one share more than it holds, once an order was touched.
  loses_order,     // Reports a partly filled order as gone.
  keeps_order,     // Reports a finished order as still resting.
  reprices_on_c,   // Reports a new price after a C.
  refuses_add,     // Says no to an add.
  refuses_cancel,  // Says no to an X.
  accepts_replay,  // Says yes to an add of a live reference.
  writes_on_trade,
  writes_on_cross,
  writes_on_break,
  writes_on_system,
  wrong_best,    // Reports a best price elsewhere.
  claims_empty,  // Reports no live orders.
  claims_order,  // Reports a live order on an empty book.
};

constexpr std::uint64_t kNoReference = 0;

template <Fault kFault>
class FaultyBook {
 public:
  explicit FaultyBook(BookCapacity capacity) : inner_{capacity} {}

  [[nodiscard]] ApplyStatus apply(std::span<const std::byte> payload) noexcept {
    const char type = itch::read_char(payload.data());
    last_type_ = type;
    if (kFault == Fault::refuses_add && type == 'A') {
      return ApplyStatus::bad_side;
    }
    if (kFault == Fault::refuses_cancel && type == 'X') {
      return ApplyStatus::unknown_order;
    }
    if ((kFault == Fault::writes_on_trade && type == 'P') ||
        (kFault == Fault::writes_on_cross && type == 'Q') ||
        (kFault == Fault::writes_on_break && type == 'B') ||
        (kFault == Fault::writes_on_system && type == 'H')) {
      inner_.observer().on_level_write(0, Side::bid, 0);
    }
    const ApplyStatus status = inner_.apply(payload);
    if (kFault == Fault::accepts_replay && status == ApplyStatus::duplicate_reference) {
      return ApplyStatus::applied;
    }
    if (kFault == Fault::keeps_order && (type == 'E' || type == 'D')) {
      kept_ = itch::read_be<std::uint64_t>(payload.data() + 11);
    }
    return status;
  }

  [[nodiscard]] std::optional<RestingOrder> resting_order(std::uint64_t reference) const noexcept {
    std::optional<RestingOrder> order = inner_.resting_order(reference);
    if (kFault == Fault::keeps_order && !order && reference == kept_) {
      return RestingOrder{.reference = reference,
                          .locate = 1,
                          .side = Side::bid,
                          .price = 100,
                          .shares = 1,
                          .at_front = true};
    }
    if (!order) {
      return order;
    }
    if (kFault == Fault::extra_share && last_type_ != 'A') {
      ++order->shares;
    }
    if (kFault == Fault::loses_order && last_type_ == 'E') {
      return std::nullopt;
    }
    if (kFault == Fault::reprices_on_c && last_type_ == 'C') {
      order->price += 100;
    }
    return order;
  }

  [[nodiscard]] TopOfBook best(std::uint16_t locate, Side side) const noexcept {
    TopOfBook top = inner_.best(locate, side);
    if (kFault == Fault::wrong_best) {
      top.price += 100;
    }
    return top;
  }

  [[nodiscard]] std::uint64_t live_orders() const noexcept {
    if (kFault == Fault::claims_empty) {
      return 0;
    }
    return inner_.live_orders() + (kFault == Fault::claims_order ? 1U : 0U);
  }

  [[nodiscard]] WriteCounter& observer() noexcept { return inner_.observer(); }

 private:
  itch::FastBook<WriteCounter> inner_;
  std::uint64_t kept_ = kNoReference;
  char last_type_ = ' ';
};

constexpr std::uint16_t kLocate = 1;
constexpr Price4 kPrice = 1000;
constexpr std::uint64_t kOpenNs = 34'200'000'000'000ULL;

const itch::Stock kSymbol = wire::stock('A');

std::vector<std::byte> listing() {
  return itch::testing::encode(itch::StockDirectory{
      .header = wire::header('R', kLocate, 1),
      .stock = kSymbol,
      .market_category = 'Q',
      .financial_status = 'N',
      .round_lot_size = 100,
      .round_lots_only = 'N',
      .issue_classification = 'C',
      .issue_sub_type = {'Z', ' '},
      .authenticity = 'P',
      .short_sale_threshold = 'N',
      .ipo_flag = ' ',
      .luld_price_tier = '1',
      .etp_flag = 'N',
      .etp_leverage_factor = 0,
      .inverse_indicator = 'N',
  });
}

// The opening of every stream here: a listed symbol, trading, with the market open.
std::vector<std::vector<std::byte>> opening() {
  return {wire::system_event('O'), listing(), wire::trading_action(kLocate, 'T'),
          wire::system_event('Q', kOpenNs)};
}

template <Fault kFault>
class Fed {
 public:
  Fed(std::initializer_list<std::vector<std::byte>> messages)
      : Fed(std::vector<std::vector<std::byte>>{messages}) {}

  explicit Fed(const std::vector<std::vector<std::byte>>& messages)
      : run_{RunConfig{.capacity = {.orders = 64, .levels = 64}, .detail_limit = 0}} {
    for (const auto& message : opening()) {
      run_.on_message(message);
    }
    for (const auto& message : messages) {
      run_.on_message(message);
    }
    run_.on_message(wire::system_event('C'));
    run_.finish(true);
  }

  [[nodiscard]] std::uint64_t violations(Check check) const { return run_.violations(check); }
  [[nodiscard]] std::uint64_t total() const {
    std::uint64_t total = 0;
    for (std::size_t i = 0; i < itch::trades::kCheckCount; ++i) {
      total += run_.violations(static_cast<Check>(i));
    }
    return total;
  }

 private:
  TradeRun<FaultyBook<kFault>> run_;
};

// wire::trade and wire::cross carry their own symbols, and these need the listed one.
std::vector<std::byte> hidden_print() {
  return itch::testing::encode(itch::Trade{.header = wire::header('P', kLocate, 2),
                                           .order_reference = 0,
                                           .side = 'B',
                                           .shares = 5,
                                           .stock = kSymbol,
                                           .price = kPrice,
                                           .match_number = 9});
}

std::vector<std::byte> cross_print() {
  return itch::testing::encode(itch::CrossTrade{.header = wire::header('Q', kLocate, 3),
                                                .shares = 5,
                                                .stock = kSymbol,
                                                .cross_price = kPrice,
                                                .match_number = 10,
                                                .cross_type = 'O'});
}

// A full life: added, partly executed, cancelled in part, partly filled by C, deleted,
// with a hidden print, a cross, a break and a trading action along the way.
std::vector<std::vector<std::byte>> one_order_life() {
  return {wire::add(kLocate, 1, 'B', 100, kPrice),
          wire::execute(kLocate, 1, 10, kOpenNs + 1),
          wire::cancel(kLocate, 1, 10),
          wire::execute_with_price(kLocate, 1, 10, kPrice + 5),
          hidden_print(),
          cross_print(),
          wire::broken(kLocate),
          wire::trading_action(kLocate, 'T'),
          wire::erase(kLocate, 1)};
}

TEST(FaultyBook, TheStreamIsCleanOnTheWrapperWithNoFault) {
  const Fed<Fault::none> fed(one_order_life());
  EXPECT_EQ(fed.total(), 0U);
}

TEST(FaultyBook, AnOffByOneRemainderIsCaughtPerOrderAndForTheDay) {
  const Fed<Fault::extra_share> fed(one_order_life());
  EXPECT_GT(fed.violations(Check::order_mismatch), 0U);
  EXPECT_EQ(fed.violations(Check::remainder_mismatch), 1U);
  EXPECT_EQ(fed.violations(Check::shares_unbalanced), 1U);
}

TEST(FaultyBook, AnOrderLostBeforeItRanOutIsCaught) {
  const Fed<Fault::loses_order> fed{wire::add(kLocate, 1, 'B', 100, kPrice),
                                    wire::execute(kLocate, 1, 10)};
  EXPECT_EQ(fed.violations(Check::order_gone_early), 1U);
}

TEST(FaultyBook, AnOrderThatOutlivesItsLastShareIsCaught) {
  const Fed<Fault::keeps_order> fed{wire::add(kLocate, 1, 'B', 100, kPrice),
                                    wire::execute(kLocate, 1, 100)};
  EXPECT_EQ(fed.violations(Check::order_alive_at_zero), 1U);
  const Fed<Fault::keeps_order> deleted{wire::add(kLocate, 1, 'B', 100, kPrice),
                                        wire::erase(kLocate, 1)};
  EXPECT_EQ(deleted.violations(Check::order_alive_at_zero), 1U);
}

TEST(FaultyBook, APriceChangedByACIsCaught) {
  const Fed<Fault::reprices_on_c> fed{wire::add(kLocate, 1, 'B', 100, kPrice),
                                      wire::execute_with_price(kLocate, 1, 10, kPrice + 5)};
  EXPECT_EQ(fed.violations(Check::c_price_changed), 1U);
  EXPECT_EQ(fed.violations(Check::order_mismatch), 1U);
}

TEST(FaultyBook, ARefusedAddIsCaught) {
  const Fed<Fault::refuses_add> fed{wire::add(kLocate, 1, 'B', 100, kPrice)};
  EXPECT_EQ(fed.violations(Check::add_refused), 1U);
}

TEST(FaultyBook, ARefusedCancelIsCaught) {
  const Fed<Fault::refuses_cancel> fed{wire::add(kLocate, 1, 'B', 100, kPrice),
                                       wire::cancel(kLocate, 1, 10), wire::erase(kLocate, 1)};
  EXPECT_EQ(fed.violations(Check::order_refused), 1U);
}

TEST(FaultyBook, AnOrderOpenedTwiceBreaksTheCountOfOrders) {
  const Fed<Fault::accepts_replay> fed{wire::add(kLocate, 1, 'B', 100, kPrice),
                                       wire::add(kLocate, 1, 'B', 100, kPrice),
                                       wire::erase(kLocate, 1)};
  EXPECT_EQ(fed.violations(Check::orders_unbalanced), 1U);
  EXPECT_EQ(fed.violations(Check::shares_unbalanced), 1U);
}

TEST(FaultyBook, AWriteOnAMessageThatMustNotWriteIsCaught) {
  EXPECT_EQ(Fed<Fault::writes_on_trade>(one_order_life()).violations(Check::p_book_changed), 1U);
  EXPECT_EQ(Fed<Fault::writes_on_cross>(one_order_life()).violations(Check::q_book_changed), 1U);
  EXPECT_EQ(Fed<Fault::writes_on_break>(one_order_life()).violations(Check::b_book_changed), 1U);
  EXPECT_EQ(Fed<Fault::writes_on_system>(one_order_life()).violations(Check::other_book_changed),
            2U);
}

TEST(FaultyBook, AWrongBestPriceIsCaught) {
  const Fed<Fault::wrong_best> fed{wire::add(kLocate, 1, 'B', 100, kPrice),
                                   wire::execute(kLocate, 1, 10), wire::erase(kLocate, 1)};
  EXPECT_EQ(fed.violations(Check::e_not_at_best), 1U);
}

TEST(FaultyBook, ABookThatDisagreesWithTheLedgerAtTheCloseIsCaught) {
  const Fed<Fault::claims_empty> claims_empty{wire::add(kLocate, 1, 'B', 100, kPrice)};
  EXPECT_EQ(claims_empty.violations(Check::ledger_not_empty), 1U);
  EXPECT_EQ(claims_empty.violations(Check::book_not_empty), 0U);
  const Fed<Fault::claims_order> claims_order{wire::add(kLocate, 1, 'B', 100, kPrice),
                                              wire::erase(kLocate, 1)};
  EXPECT_EQ(claims_order.violations(Check::book_not_empty), 1U);
  EXPECT_EQ(claims_order.violations(Check::ledger_not_empty), 0U);
}

TEST(WindowTable, TheWindowsAreHalfOpenAndNamedForTheirBounds) {
  const auto& pinned = itch::trades::kWindows[itch::trades::kMidasWindow];
  EXPECT_EQ(pinned.name, "0935_1600");
  EXPECT_EQ(pinned.start_ns, 34'500'000'000'000ULL);
  EXPECT_EQ(pinned.end_ns, 57'600'000'000'000ULL);
}

TEST(SecRoundLot, ThreeTickersDifferFrom100) {
  using itch::trades::sec_round_lot;
  EXPECT_EQ(sec_round_lot("BRK.A"), 1U);
  EXPECT_EQ(sec_round_lot("SEB"), 1U);
  EXPECT_EQ(sec_round_lot("BH"), 10U);
  for (const std::string_view symbol : {"BRK.B", "BH.A", "BHB", "MKL", "NVR", "DIT", "AAPL"}) {
    EXPECT_EQ(sec_round_lot(symbol), 100U) << symbol;
  }
}

TEST(SecRoundLot, TheOldRuleStopsAtTheDateTheSecGives) {
  using itch::trades::odd_lot_columns_on;
  using itch::trades::OddLotColumns;
  EXPECT_EQ(odd_lot_columns_on(20191030), OddLotColumns::compared);
  EXPECT_EQ(odd_lot_columns_on(20251102), OddLotColumns::compared);
  EXPECT_EQ(odd_lot_columns_on(20251103), OddLotColumns::not_compared_rule_ended);
  EXPECT_EQ(odd_lot_columns_on(20260101), OddLotColumns::not_compared_rule_ended);
}

}  // namespace
