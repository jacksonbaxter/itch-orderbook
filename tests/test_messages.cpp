// Every field of all 23 message types, asserted against hand-written bytes.
//
// B, W, h, N and O do not occur on the reference day, so these fixtures are their
// only coverage. Expected values are written out here as literals, and none of them
// goes through the code under test.
#include <gtest/gtest.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>
#include <variant>

#include "itch/messages.hpp"
#include "support/encode.hpp"
#include "support/fixtures.hpp"

namespace {

using itch::fixtures::to_bytes;
using itch::testing::round_trips;

template <std::size_t N>
std::array<char, N> alpha(std::string_view text) {
  std::array<char, N> field{};
  text.copy(field.data(), N);
  return field;
}

template <class M>
M decoded(std::span<const std::uint8_t> wire) {
  const itch::fixtures::Bytes bytes = to_bytes(wire);
  const auto result = itch::decode(bytes);
  if (!result) {
    ADD_FAILURE() << "decode failed: " << itch::to_string(result.error());
    return M{};
  }
  const M* message = std::get_if<M>(&*result);
  if (message == nullptr) {
    ADD_FAILURE() << "decoded to the wrong type, index " << result->index();
    return M{};
  }
  EXPECT_TRUE(round_trips(bytes)) << "re-encoding does not reproduce the input bytes";
  return *message;
}

void expect_header(const itch::Header& header, char type, std::uint16_t locate,
                   std::uint16_t tracking, std::uint64_t timestamp) {
  EXPECT_EQ(header.message_type, type);
  EXPECT_EQ(header.stock_locate, locate);
  EXPECT_EQ(header.tracking_number, tracking);
  EXPECT_EQ(header.timestamp_ns, timestamp);
}

TEST(Messages, SystemEvent) {
  const auto m = decoded<itch::SystemEvent>(itch::fixtures::kSystemEvent);
  expect_header(m.header, 'S', 0, 0x1234, 0x000102030405ULL);
  EXPECT_EQ(m.event_code, 'O');
}

TEST(Messages, StockDirectory) {
  const auto m = decoded<itch::StockDirectory>(itch::fixtures::kStockDirectory);
  expect_header(m.header, 'R', 0x1A2B, 0x3C4D, 0x0A0B0C0D0E0FULL);
  EXPECT_EQ(m.stock, alpha<8>("TESTR   "));
  EXPECT_EQ(m.market_category, 'Q');
  EXPECT_EQ(m.financial_status, 'D');
  EXPECT_EQ(m.round_lot_size, 100001U);
  EXPECT_EQ(m.round_lots_only, 'N');
  EXPECT_EQ(m.issue_classification, 'C');
  EXPECT_EQ(m.issue_sub_type, alpha<2>("AI"));
  EXPECT_EQ(m.authenticity, 'T');
  EXPECT_EQ(m.short_sale_threshold, 'Y');
  EXPECT_EQ(m.ipo_flag, ' ');
  EXPECT_EQ(m.luld_price_tier, '2');
  EXPECT_EQ(m.etp_flag, 'N');
  EXPECT_EQ(m.etp_leverage_factor, 259U);
  EXPECT_EQ(m.inverse_indicator, 'Y');
}

TEST(Messages, StockTradingAction) {
  const auto m = decoded<itch::StockTradingAction>(itch::fixtures::kStockTradingAction);
  expect_header(m.header, 'H', 0x002C, 0x0102, 0x101112131415ULL);
  EXPECT_EQ(m.stock, alpha<8>("TESTH   "));
  EXPECT_EQ(m.trading_state, 'P');
  EXPECT_EQ(m.reserved, 'Z');
  EXPECT_EQ(m.reason, alpha<4>("LUDP"));
}

TEST(Messages, RegShoRestriction) {
  const auto m = decoded<itch::RegShoRestriction>(itch::fixtures::kRegShoRestriction);
  expect_header(m.header, 'Y', 0x002D, 0x0304, 0x202122232425ULL);
  EXPECT_EQ(m.stock, alpha<8>("TESTY   "));
  EXPECT_EQ(m.reg_sho_action, '1');
}

TEST(Messages, MarketParticipantPosition) {
  const auto m =
      decoded<itch::MarketParticipantPosition>(itch::fixtures::kMarketParticipantPosition);
  expect_header(m.header, 'L', 0x002E, 0x0506, 0x303132333435ULL);
  EXPECT_EQ(m.mpid, alpha<4>("MPID"));
  EXPECT_EQ(m.stock, alpha<8>("TESTL   "));
  EXPECT_EQ(m.primary_market_maker, 'Y');
  EXPECT_EQ(m.market_maker_mode, 'P');
  EXPECT_EQ(m.market_participant_state, 'A');
}

TEST(Messages, MwcbDeclineLevelUsesPrice8) {
  const auto m = decoded<itch::MwcbDeclineLevel>(itch::fixtures::kMwcbDeclineLevel);
  expect_header(m.header, 'V', 0, 0x0708, 0x404142434445ULL);
  EXPECT_EQ(m.level_1, 0x000000441F2E3D4CULL);
  EXPECT_EQ(m.level_2, 0x000000335B6A7988ULL);
  EXPECT_EQ(m.level_3, 0x0000002297A6B5C4ULL);
}

TEST(Messages, MwcbStatus) {
  const auto m = decoded<itch::MwcbStatus>(itch::fixtures::kMwcbStatus);
  expect_header(m.header, 'W', 0, 0x090A, 0x505152535455ULL);
  EXPECT_EQ(m.breached_level, '2');
}

TEST(Messages, IpoQuotingPeriodUpdateCarriesSeconds) {
  const auto m = decoded<itch::IpoQuotingPeriodUpdate>(itch::fixtures::kIpoQuotingPeriodUpdate);
  expect_header(m.header, 'K', 0, 0x0B0C, 0x606162636465ULL);
  EXPECT_EQ(m.stock, alpha<8>("TESTK   "));
  EXPECT_EQ(m.release_time_seconds, 34200U);
  EXPECT_EQ(m.release_qualifier, 'A');
  EXPECT_EQ(m.ipo_price, 1000001U);
}

TEST(Messages, LuldAuctionCollar) {
  const auto m = decoded<itch::LuldAuctionCollar>(itch::fixtures::kLuldAuctionCollar);
  expect_header(m.header, 'J', 0x002F, 0x0D0E, 0x707172737475ULL);
  EXPECT_EQ(m.stock, alpha<8>("TESTJ   "));
  EXPECT_EQ(m.reference_price, 0x00102030U);
  EXPECT_EQ(m.upper_collar_price, 0x00112131U);
  EXPECT_EQ(m.lower_collar_price, 0x000F1F2FU);
  EXPECT_EQ(m.collar_extension, 2U);
}

TEST(Messages, OperationalHalt) {
  const auto m = decoded<itch::OperationalHalt>(itch::fixtures::kOperationalHalt);
  expect_header(m.header, 'h', 0x0030, 0x0F10, 0x808182838485ULL);
  EXPECT_EQ(m.stock, alpha<8>("TESTh   "));
  EXPECT_EQ(m.market_code, 'B');
  EXPECT_EQ(m.halt_action, 'H');
}

TEST(Messages, AddOrder) {
  const auto m = decoded<itch::AddOrder>(itch::fixtures::kAddOrder);
  expect_header(m.header, 'A', 0x0031, 0x1112, 0x0190A1B2C3D4ULL);
  EXPECT_EQ(m.order_reference, 0x0000010203040506ULL);
  EXPECT_EQ(m.side, 'B');
  EXPECT_EQ(m.shares, 300U);
  EXPECT_EQ(m.stock, alpha<8>("TESTA   "));
  EXPECT_EQ(m.price, 1800000U);
}

TEST(Messages, AddOrderAttributed) {
  const auto m = decoded<itch::AddOrderAttributed>(itch::fixtures::kAddOrderAttributed);
  expect_header(m.header, 'F', 0x0032, 0x1314, 0x0191A2B3C4D5ULL);
  EXPECT_EQ(m.order_reference, 0x0000020304050607ULL);
  EXPECT_EQ(m.side, 'S');
  EXPECT_EQ(m.shares, 600U);
  EXPECT_EQ(m.stock, alpha<8>("TESTF   "));
  EXPECT_EQ(m.price, 0x001C8851U);
  EXPECT_EQ(m.attribution, alpha<4>("ATTR"));
}

TEST(Messages, OrderExecuted) {
  const auto m = decoded<itch::OrderExecuted>(itch::fixtures::kOrderExecuted);
  expect_header(m.header, 'E', 0x0033, 0x1516, 0x0192A3B4C5D6ULL);
  EXPECT_EQ(m.order_reference, 0x0000030405060708ULL);
  EXPECT_EQ(m.executed_shares, 100U);
  EXPECT_EQ(m.match_number, 0x0000090A0B0C0D0EULL);
}

TEST(Messages, OrderExecutedWithPrice) {
  const auto m = decoded<itch::OrderExecutedWithPrice>(itch::fixtures::kOrderExecutedWithPrice);
  expect_header(m.header, 'C', 0x0034, 0x1718, 0x0193A4B5C6D7ULL);
  EXPECT_EQ(m.order_reference, 0x0000040506070809ULL);
  EXPECT_EQ(m.executed_shares, 150U);
  EXPECT_EQ(m.match_number, 0x00000A0B0C0D0E0FULL);
  EXPECT_EQ(m.printable, 'N');
  EXPECT_EQ(m.execution_price, 0x001D9962U);
}

TEST(Messages, OrderCancel) {
  const auto m = decoded<itch::OrderCancel>(itch::fixtures::kOrderCancel);
  expect_header(m.header, 'X', 0x0035, 0x191A, 0x0194A5B6C7D8ULL);
  EXPECT_EQ(m.order_reference, 0x000005060708090AULL);
  EXPECT_EQ(m.cancelled_shares, 50U);
}

TEST(Messages, OrderDelete) {
  const auto m = decoded<itch::OrderDelete>(itch::fixtures::kOrderDelete);
  expect_header(m.header, 'D', 0x0036, 0x1B1C, 0x0195A6B7C8D9ULL);
  EXPECT_EQ(m.order_reference, 0x0000060708090A0BULL);
}

TEST(Messages, OrderReplace) {
  const auto m = decoded<itch::OrderReplace>(itch::fixtures::kOrderReplace);
  expect_header(m.header, 'U', 0x0037, 0x1D1E, 0x0196A7B8C9DAULL);
  EXPECT_EQ(m.original_order_reference, 0x00000708090A0B0CULL);
  EXPECT_EQ(m.new_order_reference, 0x000008090A0B0C0DULL);
  EXPECT_EQ(m.shares, 800U);
  EXPECT_EQ(m.price, 0x001EAA73U);
}

TEST(Messages, Trade) {
  const auto m = decoded<itch::Trade>(itch::fixtures::kTrade);
  expect_header(m.header, 'P', 0x0038, 0x1F20, 0x0197A8B9CADBULL);
  EXPECT_EQ(m.order_reference, 0U);
  EXPECT_EQ(m.side, 'B');
  EXPECT_EQ(m.shares, 900U);
  EXPECT_EQ(m.stock, alpha<8>("TESTP   "));
  EXPECT_EQ(m.price, 0x001FBB84U);
  EXPECT_EQ(m.match_number, 0x00000B0C0D0E0F10ULL);
}

TEST(Messages, CrossTrade) {
  const auto m = decoded<itch::CrossTrade>(itch::fixtures::kCrossTrade);
  expect_header(m.header, 'Q', 0x0039, 0x2122, 0x0198A9BACBDCULL);
  EXPECT_EQ(m.shares, 0x0000000102030405ULL);
  EXPECT_EQ(m.stock, alpha<8>("TESTQ   "));
  EXPECT_EQ(m.cross_price, 0x0020CC95U);
  EXPECT_EQ(m.match_number, 0x00000C0D0E0F1011ULL);
  EXPECT_EQ(m.cross_type, 'O');
}

TEST(Messages, BrokenTrade) {
  const auto m = decoded<itch::BrokenTrade>(itch::fixtures::kBrokenTrade);
  expect_header(m.header, 'B', 0x003A, 0x2324, 0x0199AABBCCDDULL);
  EXPECT_EQ(m.match_number, 0x00000D0E0F101112ULL);
}

TEST(Messages, NetOrderImbalance) {
  const auto m = decoded<itch::NetOrderImbalance>(itch::fixtures::kNetOrderImbalance);
  expect_header(m.header, 'I', 0x003B, 0x2526, 0x019AABBCCDDEULL);
  EXPECT_EQ(m.paired_shares, 0x0000000203040506ULL);
  EXPECT_EQ(m.imbalance_shares, 0x0000000304050607ULL);
  EXPECT_EQ(m.imbalance_direction, 'S');
  EXPECT_EQ(m.stock, alpha<8>("TESTI   "));
  EXPECT_EQ(m.far_price, 0x0021DDA6U);
  EXPECT_EQ(m.near_price, 0x0022EEB7U);
  EXPECT_EQ(m.current_reference_price, 0x0023FFC8U);
  EXPECT_EQ(m.cross_type, 'H');
  EXPECT_EQ(m.price_variation_indicator, 'A');
}

TEST(Messages, RetailPriceImprovement) {
  const auto m = decoded<itch::RetailPriceImprovement>(itch::fixtures::kRetailPriceImprovement);
  expect_header(m.header, 'N', 0x003C, 0x2728, 0x019BACBDCEDFULL);
  EXPECT_EQ(m.stock, alpha<8>("TESTN   "));
  EXPECT_EQ(m.interest_flag, 'A');
}

TEST(Messages, DirectListingPriceDiscovery) {
  const auto m =
      decoded<itch::DirectListingPriceDiscovery>(itch::fixtures::kDirectListingPriceDiscovery);
  expect_header(m.header, 'O', 0x003D, 0x292A, 0x019CADBECFE0ULL);
  EXPECT_EQ(m.stock, alpha<8>("TESTO   "));
  EXPECT_EQ(m.open_eligibility_status, 'Y');
  EXPECT_EQ(m.minimum_allowable_price, 0x00051627U);
  EXPECT_EQ(m.maximum_allowable_price, 0x00061728U);
  EXPECT_EQ(m.near_execution_price, 0x00071829U);
  EXPECT_EQ(m.near_execution_time, 0x00001E2F30415263ULL);
  EXPECT_EQ(m.lower_price_range_collar, 0x0008192AU);
  EXPECT_EQ(m.upper_price_range_collar, 0x00091A2BU);
}

// The lengths as derived from the spec's field tables, typed out separately from
// kMessageSpecs.
TEST(Messages, LengthOfEveryType) {
  const std::array<std::pair<char, std::size_t>, 23> lengths{{
      {'S', 12}, {'R', 39}, {'H', 25}, {'Y', 20}, {'L', 26}, {'V', 35}, {'W', 12}, {'K', 28},
      {'J', 35}, {'h', 21}, {'A', 36}, {'F', 40}, {'E', 31}, {'C', 36}, {'X', 23}, {'D', 19},
      {'U', 35}, {'P', 44}, {'Q', 40}, {'B', 19}, {'I', 50}, {'N', 20}, {'O', 48},
  }};
  for (const auto& [type, length] : lengths) {
    EXPECT_EQ(itch::message_length(type), length) << "type " << type;
  }
}

TEST(Messages, EveryOtherTypeByteHasNoLength) {
  int defined = 0;
  for (unsigned value = 0; value < 256; ++value) {
    if (itch::message_length(static_cast<char>(value)) != 0) {
      ++defined;
    }
  }
  EXPECT_EQ(defined, 23);
}

TEST(Messages, FixturesCoverEveryTypeInTheTable) {
  const auto messages = itch::fixtures::all_messages();
  ASSERT_EQ(messages.size(), itch::kMessageSpecs.size());
  for (std::size_t i = 0; i < messages.size(); ++i) {
    EXPECT_EQ(itch::read_char(messages[i].data()), itch::kMessageSpecs[i].type);
    EXPECT_EQ(messages[i].size(), itch::kMessageSpecs[i].length);
  }
}

TEST(Decode, RejectsAnEmptyPayload) {
  const auto result = itch::decode({});
  ASSERT_FALSE(result);
  EXPECT_EQ(result.error(), itch::DecodeError::empty_payload);
}

TEST(Decode, RejectsAnUnknownType) {
  const auto bytes = to_bytes(std::array<std::uint8_t, 3>{0x7A, 0x00, 0x00});
  const auto result = itch::decode(bytes);
  ASSERT_FALSE(result);
  EXPECT_EQ(result.error(), itch::DecodeError::unknown_type);
}

TEST(Decode, RejectsEveryLengthButTheExactOne) {
  for (const auto& message : itch::fixtures::all_messages()) {
    auto padded = message;
    padded.push_back(std::byte{0});
    for (std::size_t size = 1; size <= padded.size(); ++size) {
      const auto result = itch::decode(std::span{padded}.first(size));
      if (size == message.size()) {
        EXPECT_TRUE(result);
      } else {
        ASSERT_FALSE(result);
        EXPECT_EQ(result.error(), itch::DecodeError::bad_length);
      }
    }
  }
}

// Real files carry values the spec does not list (DS-64) and values it does not
// expect (DS-66, DS-69). The decoder has to pass them through.
TEST(Decode, EnumeratedFieldsAreOpenSets) {
  auto directory = itch::fixtures::kStockDirectory;
  directory[19] = 0x4D;  // Market Category 'M', absent from the spec.
  EXPECT_EQ(decoded<itch::StockDirectory>(directory).market_category, 'M');

  auto trade = itch::fixtures::kCrossTrade;
  for (const std::size_t offset : {11U, 12U, 13U, 14U, 15U, 16U, 17U, 18U, 27U, 28U, 29U, 30U}) {
    trade[offset] = 0;  // Zero shares and zero price, which real days carry.
  }
  const auto cross = decoded<itch::CrossTrade>(trade);
  EXPECT_EQ(cross.shares, 0U);
  EXPECT_EQ(cross.cross_price, 0U);
}

}  // namespace
