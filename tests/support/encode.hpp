// Writes a decoded Message back to wire bytes.
//
// This is a second, separately written statement of the field layout, used by the
// round-trip checks: production code never calls it, and it shares no offset with
// include/itch/messages.hpp. A decoded message that re-encodes to its input shows
// that every byte of the message landed in exactly one field.
#pragma once

#include <algorithm>
#include <array>
#include <concepts>
#include <cstddef>
#include <cstdint>
#include <span>
#include <utility>
#include <variant>
#include <vector>

#include "itch/messages.hpp"

namespace itch::testing {

class Writer {
 public:
  template <std::unsigned_integral T>
  Writer& integer(T value, std::size_t width = sizeof(T)) {
    for (std::size_t i = width; i > 0; --i) {
      bytes_.push_back(static_cast<std::byte>((std::uint64_t{value} >> ((i - 1) * 8U)) & 0xFFU));
    }
    return *this;
  }
  Writer& ch(char value) {
    bytes_.push_back(static_cast<std::byte>(value));
    return *this;
  }
  template <std::size_t N>
  Writer& alpha(const std::array<char, N>& field) {
    for (const char c : field) {
      ch(c);
    }
    return *this;
  }
  Writer& header(const Header& h) {
    return ch(h.message_type)
        .integer(h.stock_locate)
        .integer(h.tracking_number)
        .integer(h.timestamp_ns, 6);
  }
  [[nodiscard]] std::vector<std::byte> take() { return std::move(bytes_); }

 private:
  std::vector<std::byte> bytes_;
};

inline void write(Writer& w, const SystemEvent& m) { w.header(m.header).ch(m.event_code); }

inline void write(Writer& w, const StockDirectory& m) {
  w.header(m.header)
      .alpha(m.stock)
      .ch(m.market_category)
      .ch(m.financial_status)
      .integer(m.round_lot_size)
      .ch(m.round_lots_only)
      .ch(m.issue_classification)
      .alpha(m.issue_sub_type)
      .ch(m.authenticity)
      .ch(m.short_sale_threshold)
      .ch(m.ipo_flag)
      .ch(m.luld_price_tier)
      .ch(m.etp_flag)
      .integer(m.etp_leverage_factor)
      .ch(m.inverse_indicator);
}

inline void write(Writer& w, const StockTradingAction& m) {
  w.header(m.header).alpha(m.stock).ch(m.trading_state).ch(m.reserved).alpha(m.reason);
}

inline void write(Writer& w, const RegShoRestriction& m) {
  w.header(m.header).alpha(m.stock).ch(m.reg_sho_action);
}

inline void write(Writer& w, const MarketParticipantPosition& m) {
  w.header(m.header)
      .alpha(m.mpid)
      .alpha(m.stock)
      .ch(m.primary_market_maker)
      .ch(m.market_maker_mode)
      .ch(m.market_participant_state);
}

inline void write(Writer& w, const MwcbDeclineLevel& m) {
  w.header(m.header).integer(m.level_1).integer(m.level_2).integer(m.level_3);
}

inline void write(Writer& w, const MwcbStatus& m) { w.header(m.header).ch(m.breached_level); }

inline void write(Writer& w, const IpoQuotingPeriodUpdate& m) {
  w.header(m.header)
      .alpha(m.stock)
      .integer(m.release_time_seconds)
      .ch(m.release_qualifier)
      .integer(m.ipo_price);
}

inline void write(Writer& w, const LuldAuctionCollar& m) {
  w.header(m.header)
      .alpha(m.stock)
      .integer(m.reference_price)
      .integer(m.upper_collar_price)
      .integer(m.lower_collar_price)
      .integer(m.collar_extension);
}

inline void write(Writer& w, const OperationalHalt& m) {
  w.header(m.header).alpha(m.stock).ch(m.market_code).ch(m.halt_action);
}

inline void write(Writer& w, const AddOrder& m) {
  w.header(m.header)
      .integer(m.order_reference)
      .ch(m.side)
      .integer(m.shares)
      .alpha(m.stock)
      .integer(m.price);
}

inline void write(Writer& w, const AddOrderAttributed& m) {
  w.header(m.header)
      .integer(m.order_reference)
      .ch(m.side)
      .integer(m.shares)
      .alpha(m.stock)
      .integer(m.price)
      .alpha(m.attribution);
}

inline void write(Writer& w, const OrderExecuted& m) {
  w.header(m.header).integer(m.order_reference).integer(m.executed_shares).integer(m.match_number);
}

inline void write(Writer& w, const OrderExecutedWithPrice& m) {
  w.header(m.header)
      .integer(m.order_reference)
      .integer(m.executed_shares)
      .integer(m.match_number)
      .ch(m.printable)
      .integer(m.execution_price);
}

inline void write(Writer& w, const OrderCancel& m) {
  w.header(m.header).integer(m.order_reference).integer(m.cancelled_shares);
}

inline void write(Writer& w, const OrderDelete& m) {
  w.header(m.header).integer(m.order_reference);
}

inline void write(Writer& w, const OrderReplace& m) {
  w.header(m.header)
      .integer(m.original_order_reference)
      .integer(m.new_order_reference)
      .integer(m.shares)
      .integer(m.price);
}

inline void write(Writer& w, const Trade& m) {
  w.header(m.header)
      .integer(m.order_reference)
      .ch(m.side)
      .integer(m.shares)
      .alpha(m.stock)
      .integer(m.price)
      .integer(m.match_number);
}

inline void write(Writer& w, const CrossTrade& m) {
  w.header(m.header)
      .integer(m.shares)
      .alpha(m.stock)
      .integer(m.cross_price)
      .integer(m.match_number)
      .ch(m.cross_type);
}

inline void write(Writer& w, const BrokenTrade& m) { w.header(m.header).integer(m.match_number); }

inline void write(Writer& w, const NetOrderImbalance& m) {
  w.header(m.header)
      .integer(m.paired_shares)
      .integer(m.imbalance_shares)
      .ch(m.imbalance_direction)
      .alpha(m.stock)
      .integer(m.far_price)
      .integer(m.near_price)
      .integer(m.current_reference_price)
      .ch(m.cross_type)
      .ch(m.price_variation_indicator);
}

inline void write(Writer& w, const RetailPriceImprovement& m) {
  w.header(m.header).alpha(m.stock).ch(m.interest_flag);
}

inline void write(Writer& w, const DirectListingPriceDiscovery& m) {
  w.header(m.header)
      .alpha(m.stock)
      .ch(m.open_eligibility_status)
      .integer(m.minimum_allowable_price)
      .integer(m.maximum_allowable_price)
      .integer(m.near_execution_price)
      .integer(m.near_execution_time)
      .integer(m.lower_price_range_collar)
      .integer(m.upper_price_range_collar);
}

[[nodiscard]] inline std::vector<std::byte> encode(const Message& message) {
  Writer writer;
  std::visit([&writer](const auto& m) { write(writer, m); }, message);
  return writer.take();
}

// True when the payload decodes and re-encodes to the same bytes.
[[nodiscard]] inline bool round_trips(std::span<const std::byte> payload) {
  const auto decoded = decode(payload);
  if (!decoded) {
    return false;
  }
  const std::vector<std::byte> again = encode(*decoded);
  return std::ranges::equal(again, payload);
}

}  // namespace itch::testing
