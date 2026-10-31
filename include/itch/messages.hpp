// Typed decode for the 23 Nasdaq TotalView-ITCH 5.0 message types.
//
// Offsets and lengths come from the field tables of the specification, revision of
// April 28, 2023. The spec prints no total lengths: each one here is the last
// field's offset plus its length. Two of its tables are mis-titled (MWCB Status on
// p. 11 and Cross Trade on p. 17), which changes no offset.
//
// Enumerated fields are plain chars and are never validated here. Real files carry
// values the spec does not list, such as Market Category 'M'. What a value means is
// the consumer's business, and nothing in this header can fail on wire content.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <span>
#include <string_view>
#include <variant>

#include "itch/wire.hpp"

namespace itch {

using Stock = std::array<char, 8>;
using Mpid = std::array<char, 4>;
using HaltReason = std::array<char, 4>;
using IssueSubType = std::array<char, 2>;

// Fixed point with 4 implied decimals. The spec's maximum is 200,000.0000.
using Price4 = std::uint32_t;
// Fixed point with 8 implied decimals. Used only by the MWCB Decline Level message.
using Price8 = std::uint64_t;

// The first 11 bytes of every message.
struct Header {
  char message_type;
  std::uint16_t stock_locate;
  std::uint16_t tracking_number;
  std::uint64_t timestamp_ns;  // Nanoseconds since midnight, 6 bytes on the wire.

  friend bool operator==(const Header&, const Header&) = default;
};

[[nodiscard]] inline Header read_header(const std::byte* m) noexcept {
  return {
      .message_type = read_char(m),
      .stock_locate = read_be<std::uint16_t>(m + 1),
      .tracking_number = read_be<std::uint16_t>(m + 3),
      .timestamp_ns = read_be48(m + 5),
  };
}

// Each from_wire() needs the message's full length to be readable at m. decode()
// and FrameReader establish that before calling it.

struct SystemEvent {
  static constexpr char kType = 'S';
  Header header;
  char event_code;

  [[nodiscard]] static SystemEvent from_wire(const std::byte* m) noexcept {
    return {.header = read_header(m), .event_code = read_char(m + 11)};
  }
  friend bool operator==(const SystemEvent&, const SystemEvent&) = default;
};

struct StockDirectory {
  static constexpr char kType = 'R';
  Header header;
  Stock stock;
  char market_category;
  char financial_status;
  std::uint32_t round_lot_size;
  char round_lots_only;
  char issue_classification;
  IssueSubType issue_sub_type;
  char authenticity;
  char short_sale_threshold;
  char ipo_flag;
  char luld_price_tier;
  char etp_flag;
  std::uint32_t etp_leverage_factor;
  char inverse_indicator;

  [[nodiscard]] static StockDirectory from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .stock = read_alpha<8>(m + 11),
        .market_category = read_char(m + 19),
        .financial_status = read_char(m + 20),
        .round_lot_size = read_be<std::uint32_t>(m + 21),
        .round_lots_only = read_char(m + 25),
        .issue_classification = read_char(m + 26),
        .issue_sub_type = read_alpha<2>(m + 27),
        .authenticity = read_char(m + 29),
        .short_sale_threshold = read_char(m + 30),
        .ipo_flag = read_char(m + 31),
        .luld_price_tier = read_char(m + 32),
        .etp_flag = read_char(m + 33),
        .etp_leverage_factor = read_be<std::uint32_t>(m + 34),
        .inverse_indicator = read_char(m + 38),
    };
  }
  friend bool operator==(const StockDirectory&, const StockDirectory&) = default;
};

struct StockTradingAction {
  static constexpr char kType = 'H';
  Header header;
  Stock stock;
  char trading_state;
  char reserved;
  HaltReason reason;

  [[nodiscard]] static StockTradingAction from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .stock = read_alpha<8>(m + 11),
        .trading_state = read_char(m + 19),
        .reserved = read_char(m + 20),
        .reason = read_alpha<4>(m + 21),
    };
  }
  friend bool operator==(const StockTradingAction&, const StockTradingAction&) = default;
};

struct RegShoRestriction {
  static constexpr char kType = 'Y';
  Header header;
  Stock stock;
  char reg_sho_action;

  [[nodiscard]] static RegShoRestriction from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .stock = read_alpha<8>(m + 11),
        .reg_sho_action = read_char(m + 19),
    };
  }
  friend bool operator==(const RegShoRestriction&, const RegShoRestriction&) = default;
};

struct MarketParticipantPosition {
  static constexpr char kType = 'L';
  Header header;
  Mpid mpid;
  Stock stock;
  char primary_market_maker;
  char market_maker_mode;
  char market_participant_state;

  [[nodiscard]] static MarketParticipantPosition from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .mpid = read_alpha<4>(m + 11),
        .stock = read_alpha<8>(m + 15),
        .primary_market_maker = read_char(m + 23),
        .market_maker_mode = read_char(m + 24),
        .market_participant_state = read_char(m + 25),
    };
  }
  friend bool operator==(const MarketParticipantPosition&,
                         const MarketParticipantPosition&) = default;
};

struct MwcbDeclineLevel {
  static constexpr char kType = 'V';
  Header header;
  Price8 level_1;
  Price8 level_2;
  Price8 level_3;

  [[nodiscard]] static MwcbDeclineLevel from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .level_1 = read_be<std::uint64_t>(m + 11),
        .level_2 = read_be<std::uint64_t>(m + 19),
        .level_3 = read_be<std::uint64_t>(m + 27),
    };
  }
  friend bool operator==(const MwcbDeclineLevel&, const MwcbDeclineLevel&) = default;
};

struct MwcbStatus {
  static constexpr char kType = 'W';
  Header header;
  char breached_level;

  [[nodiscard]] static MwcbStatus from_wire(const std::byte* m) noexcept {
    return {.header = read_header(m), .breached_level = read_char(m + 11)};
  }
  friend bool operator==(const MwcbStatus&, const MwcbStatus&) = default;
};

struct IpoQuotingPeriodUpdate {
  static constexpr char kType = 'K';
  Header header;
  Stock stock;
  std::uint32_t release_time_seconds;  // Seconds since midnight, not nanoseconds.
  char release_qualifier;
  Price4 ipo_price;  // Binary. The spec's note describing ASCII is left over.

  [[nodiscard]] static IpoQuotingPeriodUpdate from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .stock = read_alpha<8>(m + 11),
        .release_time_seconds = read_be<std::uint32_t>(m + 19),
        .release_qualifier = read_char(m + 23),
        .ipo_price = read_be<std::uint32_t>(m + 24),
    };
  }
  friend bool operator==(const IpoQuotingPeriodUpdate&, const IpoQuotingPeriodUpdate&) = default;
};

struct LuldAuctionCollar {
  static constexpr char kType = 'J';
  Header header;
  Stock stock;
  Price4 reference_price;
  Price4 upper_collar_price;
  Price4 lower_collar_price;
  std::uint32_t collar_extension;

  [[nodiscard]] static LuldAuctionCollar from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .stock = read_alpha<8>(m + 11),
        .reference_price = read_be<std::uint32_t>(m + 19),
        .upper_collar_price = read_be<std::uint32_t>(m + 23),
        .lower_collar_price = read_be<std::uint32_t>(m + 27),
        .collar_extension = read_be<std::uint32_t>(m + 31),
    };
  }
  friend bool operator==(const LuldAuctionCollar&, const LuldAuctionCollar&) = default;
};

struct OperationalHalt {
  static constexpr char kType = 'h';
  Header header;
  Stock stock;
  char market_code;
  char halt_action;

  [[nodiscard]] static OperationalHalt from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .stock = read_alpha<8>(m + 11),
        .market_code = read_char(m + 19),
        .halt_action = read_char(m + 20),
    };
  }
  friend bool operator==(const OperationalHalt&, const OperationalHalt&) = default;
};

struct AddOrder {
  static constexpr char kType = 'A';
  Header header;
  std::uint64_t order_reference;
  char side;
  std::uint32_t shares;
  Stock stock;
  Price4 price;

  [[nodiscard]] static AddOrder from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .order_reference = read_be<std::uint64_t>(m + 11),
        .side = read_char(m + 19),
        .shares = read_be<std::uint32_t>(m + 20),
        .stock = read_alpha<8>(m + 24),
        .price = read_be<std::uint32_t>(m + 32),
    };
  }
  friend bool operator==(const AddOrder&, const AddOrder&) = default;
};

struct AddOrderAttributed {
  static constexpr char kType = 'F';
  Header header;
  std::uint64_t order_reference;
  char side;
  std::uint32_t shares;
  Stock stock;
  Price4 price;
  Mpid attribution;

  [[nodiscard]] static AddOrderAttributed from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .order_reference = read_be<std::uint64_t>(m + 11),
        .side = read_char(m + 19),
        .shares = read_be<std::uint32_t>(m + 20),
        .stock = read_alpha<8>(m + 24),
        .price = read_be<std::uint32_t>(m + 32),
        .attribution = read_alpha<4>(m + 36),
    };
  }
  friend bool operator==(const AddOrderAttributed&, const AddOrderAttributed&) = default;
};

struct OrderExecuted {
  static constexpr char kType = 'E';
  Header header;
  std::uint64_t order_reference;
  std::uint32_t executed_shares;
  std::uint64_t match_number;

  [[nodiscard]] static OrderExecuted from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .order_reference = read_be<std::uint64_t>(m + 11),
        .executed_shares = read_be<std::uint32_t>(m + 19),
        .match_number = read_be<std::uint64_t>(m + 23),
    };
  }
  friend bool operator==(const OrderExecuted&, const OrderExecuted&) = default;
};

struct OrderExecutedWithPrice {
  static constexpr char kType = 'C';
  Header header;
  std::uint64_t order_reference;
  std::uint32_t executed_shares;
  std::uint64_t match_number;
  char printable;
  Price4 execution_price;

  [[nodiscard]] static OrderExecutedWithPrice from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .order_reference = read_be<std::uint64_t>(m + 11),
        .executed_shares = read_be<std::uint32_t>(m + 19),
        .match_number = read_be<std::uint64_t>(m + 23),
        .printable = read_char(m + 31),
        .execution_price = read_be<std::uint32_t>(m + 32),
    };
  }
  friend bool operator==(const OrderExecutedWithPrice&, const OrderExecutedWithPrice&) = default;
};

struct OrderCancel {
  static constexpr char kType = 'X';
  Header header;
  std::uint64_t order_reference;
  std::uint32_t cancelled_shares;

  [[nodiscard]] static OrderCancel from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .order_reference = read_be<std::uint64_t>(m + 11),
        .cancelled_shares = read_be<std::uint32_t>(m + 19),
    };
  }
  friend bool operator==(const OrderCancel&, const OrderCancel&) = default;
};

struct OrderDelete {
  static constexpr char kType = 'D';
  Header header;
  std::uint64_t order_reference;

  [[nodiscard]] static OrderDelete from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .order_reference = read_be<std::uint64_t>(m + 11),
    };
  }
  friend bool operator==(const OrderDelete&, const OrderDelete&) = default;
};

struct OrderReplace {
  static constexpr char kType = 'U';
  Header header;
  std::uint64_t original_order_reference;
  std::uint64_t new_order_reference;
  std::uint32_t shares;  // The new total displayed quantity, not a delta.
  Price4 price;

  [[nodiscard]] static OrderReplace from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .original_order_reference = read_be<std::uint64_t>(m + 11),
        .new_order_reference = read_be<std::uint64_t>(m + 19),
        .shares = read_be<std::uint32_t>(m + 27),
        .price = read_be<std::uint32_t>(m + 31),
    };
  }
  friend bool operator==(const OrderReplace&, const OrderReplace&) = default;
};

struct Trade {
  static constexpr char kType = 'P';
  Header header;
  std::uint64_t order_reference;
  char side;
  std::uint32_t shares;
  Stock stock;
  Price4 price;
  std::uint64_t match_number;

  [[nodiscard]] static Trade from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .order_reference = read_be<std::uint64_t>(m + 11),
        .side = read_char(m + 19),
        .shares = read_be<std::uint32_t>(m + 20),
        .stock = read_alpha<8>(m + 24),
        .price = read_be<std::uint32_t>(m + 32),
        .match_number = read_be<std::uint64_t>(m + 36),
    };
  }
  friend bool operator==(const Trade&, const Trade&) = default;
};

struct CrossTrade {
  static constexpr char kType = 'Q';
  Header header;
  std::uint64_t shares;  // 8 bytes here, unlike every other share count but NOII's.
  Stock stock;
  Price4 cross_price;
  std::uint64_t match_number;
  char cross_type;

  [[nodiscard]] static CrossTrade from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .shares = read_be<std::uint64_t>(m + 11),
        .stock = read_alpha<8>(m + 19),
        .cross_price = read_be<std::uint32_t>(m + 27),
        .match_number = read_be<std::uint64_t>(m + 31),
        .cross_type = read_char(m + 39),
    };
  }
  friend bool operator==(const CrossTrade&, const CrossTrade&) = default;
};

struct BrokenTrade {
  static constexpr char kType = 'B';
  Header header;
  std::uint64_t match_number;

  [[nodiscard]] static BrokenTrade from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .match_number = read_be<std::uint64_t>(m + 11),
    };
  }
  friend bool operator==(const BrokenTrade&, const BrokenTrade&) = default;
};

struct NetOrderImbalance {
  static constexpr char kType = 'I';
  Header header;
  std::uint64_t paired_shares;
  std::uint64_t imbalance_shares;
  char imbalance_direction;
  Stock stock;
  Price4 far_price;
  Price4 near_price;
  Price4 current_reference_price;
  char cross_type;
  char price_variation_indicator;

  [[nodiscard]] static NetOrderImbalance from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .paired_shares = read_be<std::uint64_t>(m + 11),
        .imbalance_shares = read_be<std::uint64_t>(m + 19),
        .imbalance_direction = read_char(m + 27),
        .stock = read_alpha<8>(m + 28),
        .far_price = read_be<std::uint32_t>(m + 36),
        .near_price = read_be<std::uint32_t>(m + 40),
        .current_reference_price = read_be<std::uint32_t>(m + 44),
        .cross_type = read_char(m + 48),
        .price_variation_indicator = read_char(m + 49),
    };
  }
  friend bool operator==(const NetOrderImbalance&, const NetOrderImbalance&) = default;
};

struct RetailPriceImprovement {
  static constexpr char kType = 'N';
  Header header;
  Stock stock;
  char interest_flag;

  [[nodiscard]] static RetailPriceImprovement from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .stock = read_alpha<8>(m + 11),
        .interest_flag = read_char(m + 19),
    };
  }
  friend bool operator==(const RetailPriceImprovement&, const RetailPriceImprovement&) = default;
};

struct DirectListingPriceDiscovery {
  static constexpr char kType = 'O';
  Header header;
  Stock stock;
  char open_eligibility_status;
  Price4 minimum_allowable_price;
  Price4 maximum_allowable_price;
  Price4 near_execution_price;
  std::uint64_t near_execution_time;
  Price4 lower_price_range_collar;
  Price4 upper_price_range_collar;

  [[nodiscard]] static DirectListingPriceDiscovery from_wire(const std::byte* m) noexcept {
    return {
        .header = read_header(m),
        .stock = read_alpha<8>(m + 11),
        .open_eligibility_status = read_char(m + 19),
        .minimum_allowable_price = read_be<std::uint32_t>(m + 20),
        .maximum_allowable_price = read_be<std::uint32_t>(m + 24),
        .near_execution_price = read_be<std::uint32_t>(m + 28),
        .near_execution_time = read_be<std::uint64_t>(m + 32),
        .lower_price_range_collar = read_be<std::uint32_t>(m + 40),
        .upper_price_range_collar = read_be<std::uint32_t>(m + 44),
    };
  }
  friend bool operator==(const DirectListingPriceDiscovery&,
                         const DirectListingPriceDiscovery&) = default;
};

using Message =
    std::variant<SystemEvent, StockDirectory, StockTradingAction, RegShoRestriction,
                 MarketParticipantPosition, MwcbDeclineLevel, MwcbStatus, IpoQuotingPeriodUpdate,
                 LuldAuctionCollar, OperationalHalt, AddOrder, AddOrderAttributed, OrderExecuted,
                 OrderExecutedWithPrice, OrderCancel, OrderDelete, OrderReplace, Trade, CrossTrade,
                 BrokenTrade, NetOrderImbalance, RetailPriceImprovement,
                 DirectListingPriceDiscovery>;

struct MessageSpec {
  char type;
  std::uint8_t length;
  std::string_view name;
};

// The one table of message lengths. Each is the payload length, without the 2-byte
// BinaryFILE prefix.
inline constexpr std::array<MessageSpec, 23> kMessageSpecs{{
    {.type = 'S', .length = 12, .name = "System Event"},
    {.type = 'R', .length = 39, .name = "Stock Directory"},
    {.type = 'H', .length = 25, .name = "Stock Trading Action"},
    {.type = 'Y', .length = 20, .name = "Reg SHO Restriction"},
    {.type = 'L', .length = 26, .name = "Market Participant Position"},
    {.type = 'V', .length = 35, .name = "MWCB Decline Level"},
    {.type = 'W', .length = 12, .name = "MWCB Status"},
    {.type = 'K', .length = 28, .name = "IPO Quoting Period Update"},
    {.type = 'J', .length = 35, .name = "LULD Auction Collar"},
    {.type = 'h', .length = 21, .name = "Operational Halt"},
    {.type = 'A', .length = 36, .name = "Add Order"},
    {.type = 'F', .length = 40, .name = "Add Order with MPID"},
    {.type = 'E', .length = 31, .name = "Order Executed"},
    {.type = 'C', .length = 36, .name = "Order Executed With Price"},
    {.type = 'X', .length = 23, .name = "Order Cancel"},
    {.type = 'D', .length = 19, .name = "Order Delete"},
    {.type = 'U', .length = 35, .name = "Order Replace"},
    {.type = 'P', .length = 44, .name = "Trade"},
    {.type = 'Q', .length = 40, .name = "Cross Trade"},
    {.type = 'B', .length = 19, .name = "Broken Trade"},
    {.type = 'I', .length = 50, .name = "Net Order Imbalance Indicator"},
    {.type = 'N', .length = 20, .name = "Retail Price Improvement Indicator"},
    {.type = 'O', .length = 48, .name = "Direct Listing Price Discovery"},
}};

inline constexpr std::array<std::uint8_t, 256> kLengthByType = [] {
  std::array<std::uint8_t, 256> lengths{};
  for (const MessageSpec& spec : kMessageSpecs) {
    lengths[static_cast<unsigned char>(spec.type)] = spec.length;
  }
  return lengths;
}();

// The payload length of a type, or 0 for a type the spec does not define. A byte
// indexes this table, so no wire value can leave its bounds.
[[nodiscard]] constexpr std::size_t message_length(char type) noexcept {
  return kLengthByType[static_cast<unsigned char>(type)];
}

enum class DecodeError : std::uint8_t {
  empty_payload,
  unknown_type,
  bad_length,
};

// A stable name for logs and tools.
[[nodiscard]] std::string_view to_string(DecodeError error) noexcept;

// Decodes one payload, without its length prefix. The length is checked against the
// type before any field is read.
[[nodiscard]] inline std::expected<Message, DecodeError> decode(
    std::span<const std::byte> payload) noexcept {
  if (payload.empty()) {
    return std::unexpected(DecodeError::empty_payload);
  }
  const std::byte* m = payload.data();
  const char type = read_char(m);
  const std::size_t length = message_length(type);
  if (length == 0) {
    return std::unexpected(DecodeError::unknown_type);
  }
  if (payload.size() != length) {
    return std::unexpected(DecodeError::bad_length);
  }
  switch (type) {
    case SystemEvent::kType: return SystemEvent::from_wire(m);
    case StockDirectory::kType: return StockDirectory::from_wire(m);
    case StockTradingAction::kType: return StockTradingAction::from_wire(m);
    case RegShoRestriction::kType: return RegShoRestriction::from_wire(m);
    case MarketParticipantPosition::kType: return MarketParticipantPosition::from_wire(m);
    case MwcbDeclineLevel::kType: return MwcbDeclineLevel::from_wire(m);
    case MwcbStatus::kType: return MwcbStatus::from_wire(m);
    case IpoQuotingPeriodUpdate::kType: return IpoQuotingPeriodUpdate::from_wire(m);
    case LuldAuctionCollar::kType: return LuldAuctionCollar::from_wire(m);
    case OperationalHalt::kType: return OperationalHalt::from_wire(m);
    case AddOrder::kType: return AddOrder::from_wire(m);
    case AddOrderAttributed::kType: return AddOrderAttributed::from_wire(m);
    case OrderExecuted::kType: return OrderExecuted::from_wire(m);
    case OrderExecutedWithPrice::kType: return OrderExecutedWithPrice::from_wire(m);
    case OrderCancel::kType: return OrderCancel::from_wire(m);
    case OrderDelete::kType: return OrderDelete::from_wire(m);
    case OrderReplace::kType: return OrderReplace::from_wire(m);
    case Trade::kType: return Trade::from_wire(m);
    case CrossTrade::kType: return CrossTrade::from_wire(m);
    case BrokenTrade::kType: return BrokenTrade::from_wire(m);
    case NetOrderImbalance::kType: return NetOrderImbalance::from_wire(m);
    case RetailPriceImprovement::kType: return RetailPriceImprovement::from_wire(m);
    case DirectListingPriceDiscovery::kType: return DirectListingPriceDiscovery::from_wire(m);
    default: return std::unexpected(DecodeError::unknown_type);
  }
}

}  // namespace itch
