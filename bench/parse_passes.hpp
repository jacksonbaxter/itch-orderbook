// The three parse passes of T-PARSE-1 to T-PARSE-3, each over a whole buffer of BinaryFILE
// frames, each as its target's source defines the pass:
//
//   frame_walk     T-PARSE-1, itch-book's framing tier: every frame counted by its 2-byte
//                  length prefix alone. No payload byte is read, the type byte included.
//   book_decode    T-PARSE-2, itch-book's decode tier: each frame's type checked against its
//                  length, and the ten types its Checksum handler takes (S, R, A, F, E, C, X,
//                  D, U and P) decoded, the fields that handler adds folded into a sum. As in
//                  its build, a field the sum does not use need not be read.
//   strict_decode  T-PARSE-3, Lunary's full decode: itch::decode() on every frame, which checks
//                  the length against the type before any field, and every field of each of the
//                  23 types, header included, folded into a sum.
//
// Every pass counts every frame: that is the denominator of each target's rate. A sum of
// fields changes whenever any one field does, so a field the pass did not read, or a type it
// skipped, shows in the fold (tests/test_parse_passes.cpp).
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <span>
#include <variant>

#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"

namespace itch::bench::parse {

struct Totals {
  std::uint64_t messages = 0;  // Frames walked: every pass's denominator.
  std::uint64_t decoded = 0;   // Messages the pass decoded.
  std::uint64_t rejected = 0;  // Frames whose type or length the pass refused to decode.
  std::uint64_t fold = 0;      // The sum of the fields decoded.
  std::size_t stopped_at = 0;  // Where the walk ended: the buffer's size on a whole file.

  friend bool operator==(const Totals&, const Totals&) = default;
};

// An alpha field as the integer of its bytes, so that every byte of it reaches the sum.
template <std::size_t N>
[[nodiscard]] inline std::uint64_t alpha_value(const std::array<char, N>& field) noexcept {
  static_assert(N <= sizeof(std::uint64_t));
  std::uint64_t value = 0;
  std::memcpy(&value, field.data(), N);
  return value;
}

[[nodiscard]] inline std::uint64_t char_value(char c) noexcept {
  return static_cast<unsigned char>(c);
}

// The walk all three passes share: each frame's length prefix, then the frame's payload, or
// none for a zero-length frame. It stops at a prefix that asks for more bytes than remain.
template <class OnFrame>
[[gnu::always_inline]] inline std::size_t walk_frames(std::span<const std::byte> bytes,
                                                      OnFrame on_frame) {
  constexpr std::size_t kPrefix = FrameReader::kLengthPrefix;
  const std::byte* const data = bytes.data();
  const std::size_t size = bytes.size();
  std::size_t at = 0;
  while (size - at >= kPrefix) {
    const std::size_t length = read_be<std::uint16_t>(data + at);
    if (length > size - at - kPrefix) {
      break;
    }
    on_frame(data + at + kPrefix, length);
    at += kPrefix + length;
  }
  return at;
}

// T-PARSE-1.
[[nodiscard]] inline Totals frame_walk(std::span<const std::byte> bytes) noexcept {
  Totals totals;
  totals.stopped_at = walk_frames(bytes, [&](const std::byte*, std::size_t) { ++totals.messages; });
  return totals;
}

// T-PARSE-2: what itch-book's Checksum handler adds for each of its ten types.
[[nodiscard]] inline Totals book_decode(std::span<const std::byte> bytes) noexcept {
  Totals totals;
  std::uint64_t fold = 0;
  std::uint64_t decoded = 0;
  std::uint64_t rejected = 0;
  totals.stopped_at = walk_frames(bytes, [&](const std::byte* m, std::size_t length) {
    ++totals.messages;
    if (length == 0 || message_length(read_char(m)) != length) {
      ++rejected;
      return;
    }
    switch (read_char(m)) {
      case SystemEvent::kType: fold += char_value(SystemEvent::from_wire(m).event_code); break;
      case StockDirectory::kType: fold += StockDirectory::from_wire(m).round_lot_size; break;
      case AddOrder::kType: {
        const AddOrder add = AddOrder::from_wire(m);
        fold += add.order_reference + add.price + add.shares;
        break;
      }
      case AddOrderAttributed::kType: {
        const AddOrderAttributed add = AddOrderAttributed::from_wire(m);
        fold += add.order_reference + add.price + add.shares;
        break;
      }
      case OrderExecuted::kType: {
        const OrderExecuted executed = OrderExecuted::from_wire(m);
        fold += executed.order_reference + executed.executed_shares + executed.match_number;
        break;
      }
      case OrderExecutedWithPrice::kType: {
        const OrderExecutedWithPrice executed = OrderExecutedWithPrice::from_wire(m);
        fold += executed.order_reference + executed.execution_price;
        break;
      }
      case OrderCancel::kType: {
        const OrderCancel cancel = OrderCancel::from_wire(m);
        fold += cancel.order_reference + cancel.cancelled_shares;
        break;
      }
      case OrderDelete::kType: fold += OrderDelete::from_wire(m).order_reference; break;
      case OrderReplace::kType: {
        const OrderReplace replace = OrderReplace::from_wire(m);
        fold += replace.original_order_reference + replace.new_order_reference + replace.price;
        break;
      }
      case Trade::kType: {
        const Trade trade = Trade::from_wire(m);
        fold += trade.match_number + trade.shares;
        break;
      }
      default: return;
    }
    ++decoded;
  });
  totals.decoded = decoded;
  totals.rejected = rejected;
  totals.fold = fold;
  return totals;
}

// The sum of every field of a decoded message, header included.
struct FieldSum {
  [[nodiscard]] static std::uint64_t of(const Header& h) noexcept {
    return char_value(h.message_type) + h.stock_locate + h.tracking_number + h.timestamp_ns;
  }
  std::uint64_t operator()(const SystemEvent& m) const noexcept {
    return of(m.header) + char_value(m.event_code);
  }
  std::uint64_t operator()(const StockDirectory& m) const noexcept {
    return of(m.header) + alpha_value(m.stock) + char_value(m.market_category) +
           char_value(m.financial_status) + m.round_lot_size + char_value(m.round_lots_only) +
           char_value(m.issue_classification) + alpha_value(m.issue_sub_type) +
           char_value(m.authenticity) + char_value(m.short_sale_threshold) +
           char_value(m.ipo_flag) + char_value(m.luld_price_tier) + char_value(m.etp_flag) +
           m.etp_leverage_factor + char_value(m.inverse_indicator);
  }
  std::uint64_t operator()(const StockTradingAction& m) const noexcept {
    return of(m.header) + alpha_value(m.stock) + char_value(m.trading_state) +
           char_value(m.reserved) + alpha_value(m.reason);
  }
  std::uint64_t operator()(const RegShoRestriction& m) const noexcept {
    return of(m.header) + alpha_value(m.stock) + char_value(m.reg_sho_action);
  }
  std::uint64_t operator()(const MarketParticipantPosition& m) const noexcept {
    return of(m.header) + alpha_value(m.mpid) + alpha_value(m.stock) +
           char_value(m.primary_market_maker) + char_value(m.market_maker_mode) +
           char_value(m.market_participant_state);
  }
  std::uint64_t operator()(const MwcbDeclineLevel& m) const noexcept {
    return of(m.header) + m.level_1 + m.level_2 + m.level_3;
  }
  std::uint64_t operator()(const MwcbStatus& m) const noexcept {
    return of(m.header) + char_value(m.breached_level);
  }
  std::uint64_t operator()(const IpoQuotingPeriodUpdate& m) const noexcept {
    return of(m.header) + alpha_value(m.stock) + m.release_time_seconds +
           char_value(m.release_qualifier) + m.ipo_price;
  }
  std::uint64_t operator()(const LuldAuctionCollar& m) const noexcept {
    return of(m.header) + alpha_value(m.stock) + m.reference_price + m.upper_collar_price +
           m.lower_collar_price + m.collar_extension;
  }
  std::uint64_t operator()(const OperationalHalt& m) const noexcept {
    return of(m.header) + alpha_value(m.stock) + char_value(m.market_code) +
           char_value(m.halt_action);
  }
  std::uint64_t operator()(const AddOrder& m) const noexcept {
    return of(m.header) + m.order_reference + char_value(m.side) + m.shares + alpha_value(m.stock) +
           m.price;
  }
  std::uint64_t operator()(const AddOrderAttributed& m) const noexcept {
    return of(m.header) + m.order_reference + char_value(m.side) + m.shares + alpha_value(m.stock) +
           m.price + alpha_value(m.attribution);
  }
  std::uint64_t operator()(const OrderExecuted& m) const noexcept {
    return of(m.header) + m.order_reference + m.executed_shares + m.match_number;
  }
  std::uint64_t operator()(const OrderExecutedWithPrice& m) const noexcept {
    return of(m.header) + m.order_reference + m.executed_shares + m.match_number +
           char_value(m.printable) + m.execution_price;
  }
  std::uint64_t operator()(const OrderCancel& m) const noexcept {
    return of(m.header) + m.order_reference + m.cancelled_shares;
  }
  std::uint64_t operator()(const OrderDelete& m) const noexcept {
    return of(m.header) + m.order_reference;
  }
  std::uint64_t operator()(const OrderReplace& m) const noexcept {
    return of(m.header) + m.original_order_reference + m.new_order_reference + m.shares + m.price;
  }
  std::uint64_t operator()(const Trade& m) const noexcept {
    return of(m.header) + m.order_reference + char_value(m.side) + m.shares + alpha_value(m.stock) +
           m.price + m.match_number;
  }
  std::uint64_t operator()(const CrossTrade& m) const noexcept {
    return of(m.header) + m.shares + alpha_value(m.stock) + m.cross_price + m.match_number +
           char_value(m.cross_type);
  }
  std::uint64_t operator()(const BrokenTrade& m) const noexcept {
    return of(m.header) + m.match_number;
  }
  std::uint64_t operator()(const NetOrderImbalance& m) const noexcept {
    return of(m.header) + m.paired_shares + m.imbalance_shares + char_value(m.imbalance_direction) +
           alpha_value(m.stock) + m.far_price + m.near_price + m.current_reference_price +
           char_value(m.cross_type) + char_value(m.price_variation_indicator);
  }
  std::uint64_t operator()(const RetailPriceImprovement& m) const noexcept {
    return of(m.header) + alpha_value(m.stock) + char_value(m.interest_flag);
  }
  std::uint64_t operator()(const DirectListingPriceDiscovery& m) const noexcept {
    return of(m.header) + alpha_value(m.stock) + char_value(m.open_eligibility_status) +
           m.minimum_allowable_price + m.maximum_allowable_price + m.near_execution_price +
           m.near_execution_time + m.lower_price_range_collar + m.upper_price_range_collar;
  }
};

// T-PARSE-3. Not noexcept: std::visit may throw on a variant without a value, which decode()
// never returns.
[[nodiscard]] inline Totals strict_decode(std::span<const std::byte> bytes) {
  Totals totals;
  std::uint64_t fold = 0;
  std::uint64_t decoded = 0;
  std::uint64_t rejected = 0;
  totals.stopped_at = walk_frames(bytes, [&](const std::byte* m, std::size_t length) {
    ++totals.messages;
    const auto message = decode(std::span<const std::byte>{m, length});
    if (!message) {
      ++rejected;
      return;
    }
    fold += std::visit(FieldSum{}, *message);
    ++decoded;
  });
  totals.decoded = decoded;
  totals.rejected = rejected;
  totals.fold = fold;
  return totals;
}

}  // namespace itch::bench::parse
