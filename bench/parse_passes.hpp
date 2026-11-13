// The three parse passes of T-PARSE-1 to T-PARSE-3, each over a whole buffer of BinaryFILE
// frames, each as its target's source defines the pass:
//
//   frame_walk     T-PARSE-1, itch-book's framing tier, its scan(): the loop of its parse()
//                  (include/itch/parser.hpp in the source), with a handler that takes nothing.
//                  Per frame, the length prefix, a stop at a zero-length frame, the type byte,
//                  the type's length looked up and compared with the frame's, and one of three
//                  counters: a message, an unknown type, a wrong length.
//   book_decode    T-PARSE-2, itch-book's decode tier: the same loop, with the ten types its
//                  Checksum handler takes (S, R, A, F, E, C, X, D, U and P) decoded and the
//                  fields that handler adds folded into a sum. As in its build, a field the sum
//                  does not use need not be read. One more add a message than the source does:
//                  the count of messages decoded, which bench_parse holds to the frame reader's.
//   strict_decode  T-PARSE-3, Lunary's full decode: itch::decode() on every frame, which checks
//                  the length against the type before any field, and every field of each of the
//                  23 types, header included, folded into a sum.
//
// Every pass counts every frame: that is the denominator of each target's rate. A sum of
// fields changes whenever any one field does, so a field the pass did not read, or a type it
// skipped, shows in the fold (tests/test_parse_passes.cpp).
//
// Each pass is one function, never inlined into its caller, with everything it calls inlined
// into it: so a timed span holds one call, to the pass, and the pass's loop holds none, as
// itch-book's template loop inlines its handler. gnu::flatten does that alone in GCC, which
// inlines the calls that inlining brings in as well; Clang inlines only the calls written in
// the flattened function's own body, so each function a pass hands work to is always_inline
// and flatten too, its lambda included. scripts/parse_driver.py --inline-check reads the
// built binary's code of the three and refuses a call in any.
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

// The walk the strict decode makes: each frame's length prefix, then the frame's payload, or
// none for a zero-length frame, which decode() refuses. It stops at a prefix that asks for
// more bytes than remain.
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

// itch-book's parse() loop, transcribed: a zero-length frame ends the walk, past it, as the
// end of the session; a frame whose length is its type's goes to on_message and is counted a
// message, any other is counted an unknown type or a wrong length. Every frame before the
// stop is in `messages`, as the source's rate counts them.
template <class OnMessage>
[[gnu::always_inline]] inline Totals scan_frames(std::span<const std::byte> bytes,
                                                 OnMessage on_message) {
  constexpr std::size_t kPrefix = FrameReader::kLengthPrefix;
  const std::byte* const data = bytes.data();
  const std::size_t size = bytes.size();
  std::size_t at = 0;
  std::uint64_t messages = 0;
  std::uint64_t unknown = 0;
  std::uint64_t malformed = 0;
  while (size - at >= kPrefix) {
    const std::size_t length = read_be<std::uint16_t>(data + at);
    if (length == 0) {
      at += kPrefix;
      break;
    }
    if (length > size - at - kPrefix) {
      break;
    }
    const std::byte* const m = data + at + kPrefix;
    const char type = read_char(m);
    const std::size_t want = message_length(type);
    if (want == length) {
      on_message(type, m);
      ++messages;
    } else if (want == 0) {
      ++unknown;
    } else {
      ++malformed;
    }
    at += kPrefix + length;
  }
  Totals totals;
  totals.messages = messages + unknown + malformed;
  totals.rejected = unknown + malformed;
  totals.stopped_at = at;
  return totals;
}

// T-PARSE-1.
[[gnu::noinline, gnu::flatten, nodiscard]] inline Totals frame_walk(
    std::span<const std::byte> bytes) noexcept {
  return scan_frames(bytes, [](char, const std::byte*) __attribute__((always_inline)) {});
}

// T-PARSE-2: what itch-book's Checksum handler adds for each of its ten types.
[[gnu::noinline, gnu::flatten, nodiscard]] inline Totals book_decode(
    std::span<const std::byte> bytes) noexcept {
  std::uint64_t fold = 0;
  std::uint64_t decoded = 0;
  Totals totals = scan_frames(
      bytes, [&](char type, const std::byte* m) __attribute__((always_inline, flatten)) {
        switch (type) {
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

// The sum of the fields of whichever type a decoded message holds, by a switch on the
// variant's index: std::visit would reach FieldSum through its table of functions, an
// indirect call a message.
[[gnu::always_inline, gnu::flatten]] inline std::uint64_t field_sum(
    const Message& message) noexcept {
  static_assert(std::variant_size_v<Message> == 23);
  const FieldSum sum;
  switch (message.index()) {
    case 0: return sum(*std::get_if<0>(&message));
    case 1: return sum(*std::get_if<1>(&message));
    case 2: return sum(*std::get_if<2>(&message));
    case 3: return sum(*std::get_if<3>(&message));
    case 4: return sum(*std::get_if<4>(&message));
    case 5: return sum(*std::get_if<5>(&message));
    case 6: return sum(*std::get_if<6>(&message));
    case 7: return sum(*std::get_if<7>(&message));
    case 8: return sum(*std::get_if<8>(&message));
    case 9: return sum(*std::get_if<9>(&message));
    case 10: return sum(*std::get_if<10>(&message));
    case 11: return sum(*std::get_if<11>(&message));
    case 12: return sum(*std::get_if<12>(&message));
    case 13: return sum(*std::get_if<13>(&message));
    case 14: return sum(*std::get_if<14>(&message));
    case 15: return sum(*std::get_if<15>(&message));
    case 16: return sum(*std::get_if<16>(&message));
    case 17: return sum(*std::get_if<17>(&message));
    case 18: return sum(*std::get_if<18>(&message));
    case 19: return sum(*std::get_if<19>(&message));
    case 20: return sum(*std::get_if<20>(&message));
    case 21: return sum(*std::get_if<21>(&message));
    case 22: return sum(*std::get_if<22>(&message));
    default: return 0;
  }
}

// T-PARSE-3.
[[gnu::noinline, gnu::flatten, nodiscard]] inline Totals strict_decode(
    std::span<const std::byte> bytes) noexcept {
  Totals totals;
  std::uint64_t frames = 0;
  std::uint64_t fold = 0;
  std::uint64_t decoded = 0;
  std::uint64_t rejected = 0;
  totals.stopped_at = walk_frames(
      bytes, [&](const std::byte* m, std::size_t length) __attribute__((always_inline, flatten)) {
        ++frames;
        const auto message = decode(std::span<const std::byte>{m, length});
        if (!message) {
          ++rejected;
          return;
        }
        fold += field_sum(*message);
        ++decoded;
      });
  totals.messages = frames;
  totals.decoded = decoded;
  totals.rejected = rejected;
  totals.fold = fold;
  return totals;
}

}  // namespace itch::bench::parse
