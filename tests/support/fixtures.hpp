// Hand-written wire bytes for all 23 ITCH 5.0 message types, one field per line.
//
// Nothing here comes from a Nasdaq file. Values are invented, and each field gets
// bytes that differ from its neighbours and read differently in the opposite byte
// order, so that a wrong offset or a swapped field shows up as a wrong value.
//
// scripts/gen_fuzz_corpus.py parses the arrays below to build the seed corpus, so
// keep the `inline constexpr ... kName{` form with plain hex bytes.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <utility>
#include <vector>

namespace itch::fixtures {

inline constexpr std::array<std::uint8_t, 12> kSystemEvent{
    0x53,                                // Message Type 'S'
    0x00, 0x00,                          // Stock Locate: always 0
    0x12, 0x34,                          // Tracking Number
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05,  // Timestamp
    0x4F,                                // Event Code 'O'
};

inline constexpr std::array<std::uint8_t, 39> kStockDirectory{
    0x52,                                            // Message Type 'R'
    0x1A, 0x2B,                                      // Stock Locate
    0x3C, 0x4D,                                      // Tracking Number
    0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,              // Timestamp
    0x54, 0x45, 0x53, 0x54, 0x52, 0x20, 0x20, 0x20,  // Stock "TESTR   "
    0x51,                                            // Market Category 'Q'
    0x44,                                            // Financial Status 'D'
    0x00, 0x01, 0x86, 0xA1,                          // Round Lot Size 100001
    0x4E,                                            // Round Lots Only 'N'
    0x43,                                            // Issue Classification 'C'
    0x41, 0x49,                                      // Issue Sub-Type "AI"
    0x54,                                            // Authenticity 'T'
    0x59,                                            // Short Sale Threshold 'Y'
    0x20,                                            // IPO Flag ' '
    0x32,                                            // LULD Tier '2'
    0x4E,                                            // ETP Flag 'N'
    0x00, 0x00, 0x01, 0x03,                          // ETP Leverage Factor 259
    0x59,                                            // Inverse Indicator 'Y'
};

inline constexpr std::array<std::uint8_t, 25> kStockTradingAction{
    0x48,                                            // Message Type 'H'
    0x00, 0x2C,                                      // Stock Locate
    0x01, 0x02,                                      // Tracking Number
    0x10, 0x11, 0x12, 0x13, 0x14, 0x15,              // Timestamp
    0x54, 0x45, 0x53, 0x54, 0x48, 0x20, 0x20, 0x20,  // Stock "TESTH   "
    0x50,                                            // Trading State 'P'
    0x5A,                                            // Reserved 'Z'
    0x4C, 0x55, 0x44, 0x50,                          // Reason "LUDP"
};

inline constexpr std::array<std::uint8_t, 20> kRegShoRestriction{
    0x59,                                            // Message Type 'Y'
    0x00, 0x2D,                                      // Stock Locate
    0x03, 0x04,                                      // Tracking Number
    0x20, 0x21, 0x22, 0x23, 0x24, 0x25,              // Timestamp
    0x54, 0x45, 0x53, 0x54, 0x59, 0x20, 0x20, 0x20,  // Stock "TESTY   "
    0x31,                                            // Reg SHO Action '1'
};

inline constexpr std::array<std::uint8_t, 26> kMarketParticipantPosition{
    0x4C,                                            // Message Type 'L'
    0x00, 0x2E,                                      // Stock Locate
    0x05, 0x06,                                      // Tracking Number
    0x30, 0x31, 0x32, 0x33, 0x34, 0x35,              // Timestamp
    0x4D, 0x50, 0x49, 0x44,                          // MPID "MPID"
    0x54, 0x45, 0x53, 0x54, 0x4C, 0x20, 0x20, 0x20,  // Stock "TESTL   "
    0x59,                                            // Primary Market Maker 'Y'
    0x50,                                            // Market Maker Mode 'P'
    0x41,                                            // Market Participant State 'A'
};

inline constexpr std::array<std::uint8_t, 35> kMwcbDeclineLevel{
    0x56,                                            // Message Type 'V'
    0x00, 0x00,                                      // Stock Locate: always 0
    0x07, 0x08,                                      // Tracking Number
    0x40, 0x41, 0x42, 0x43, 0x44, 0x45,              // Timestamp
    0x00, 0x00, 0x00, 0x44, 0x1F, 0x2E, 0x3D, 0x4C,  // Level 1, Price(8)
    0x00, 0x00, 0x00, 0x33, 0x5B, 0x6A, 0x79, 0x88,  // Level 2, Price(8)
    0x00, 0x00, 0x00, 0x22, 0x97, 0xA6, 0xB5, 0xC4,  // Level 3, Price(8)
};

inline constexpr std::array<std::uint8_t, 12> kMwcbStatus{
    0x57,                                // Message Type 'W'
    0x00, 0x00,                          // Stock Locate: always 0
    0x09, 0x0A,                          // Tracking Number
    0x50, 0x51, 0x52, 0x53, 0x54, 0x55,  // Timestamp
    0x32,                                // Breached Level '2'
};

inline constexpr std::array<std::uint8_t, 28> kIpoQuotingPeriodUpdate{
    0x4B,                                            // Message Type 'K'
    0x00, 0x00,                                      // Stock Locate: 0 per the spec
    0x0B, 0x0C,                                      // Tracking Number
    0x60, 0x61, 0x62, 0x63, 0x64, 0x65,              // Timestamp
    0x54, 0x45, 0x53, 0x54, 0x4B, 0x20, 0x20, 0x20,  // Stock "TESTK   "
    0x00, 0x00, 0x85, 0x98,                          // Release Time 34200, seconds
    0x41,                                            // Release Qualifier 'A'
    0x00, 0x0F, 0x42, 0x41,                          // IPO Price 1000001
};

inline constexpr std::array<std::uint8_t, 35> kLuldAuctionCollar{
    0x4A,                                            // Message Type 'J'
    0x00, 0x2F,                                      // Stock Locate
    0x0D, 0x0E,                                      // Tracking Number
    0x70, 0x71, 0x72, 0x73, 0x74, 0x75,              // Timestamp
    0x54, 0x45, 0x53, 0x54, 0x4A, 0x20, 0x20, 0x20,  // Stock "TESTJ   "
    0x00, 0x10, 0x20, 0x30,                          // Reference Price
    0x00, 0x11, 0x21, 0x31,                          // Upper Collar Price
    0x00, 0x0F, 0x1F, 0x2F,                          // Lower Collar Price
    0x00, 0x00, 0x00, 0x02,                          // Collar Extension 2
};

inline constexpr std::array<std::uint8_t, 21> kOperationalHalt{
    0x68,                                            // Message Type 'h'
    0x00, 0x30,                                      // Stock Locate
    0x0F, 0x10,                                      // Tracking Number
    0x80, 0x81, 0x82, 0x83, 0x84, 0x85,              // Timestamp
    0x54, 0x45, 0x53, 0x54, 0x68, 0x20, 0x20, 0x20,  // Stock "TESTh   "
    0x42,                                            // Market Code 'B'
    0x48,                                            // Halt Action 'H'
};

inline constexpr std::array<std::uint8_t, 36> kAddOrder{
    0x41,                                            // Message Type 'A'
    0x00, 0x31,                                      // Stock Locate
    0x11, 0x12,                                      // Tracking Number
    0x01, 0x90, 0xA1, 0xB2, 0xC3, 0xD4,              // Timestamp
    0x00, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06,  // Order Reference
    0x42,                                            // Side 'B'
    0x00, 0x00, 0x01, 0x2C,                          // Shares 300
    0x54, 0x45, 0x53, 0x54, 0x41, 0x20, 0x20, 0x20,  // Stock "TESTA   "
    0x00, 0x1B, 0x77, 0x40,                          // Price 1800000
};

inline constexpr std::array<std::uint8_t, 40> kAddOrderAttributed{
    0x46,                                            // Message Type 'F'
    0x00, 0x32,                                      // Stock Locate
    0x13, 0x14,                                      // Tracking Number
    0x01, 0x91, 0xA2, 0xB3, 0xC4, 0xD5,              // Timestamp
    0x00, 0x00, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,  // Order Reference
    0x53,                                            // Side 'S'
    0x00, 0x00, 0x02, 0x58,                          // Shares 600
    0x54, 0x45, 0x53, 0x54, 0x46, 0x20, 0x20, 0x20,  // Stock "TESTF   "
    0x00, 0x1C, 0x88, 0x51,                          // Price 1869905
    0x41, 0x54, 0x54, 0x52,                          // Attribution "ATTR"
};

inline constexpr std::array<std::uint8_t, 31> kOrderExecuted{
    0x45,                                            // Message Type 'E'
    0x00, 0x33,                                      // Stock Locate
    0x15, 0x16,                                      // Tracking Number
    0x01, 0x92, 0xA3, 0xB4, 0xC5, 0xD6,              // Timestamp
    0x00, 0x00, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,  // Order Reference
    0x00, 0x00, 0x00, 0x64,                          // Executed Shares 100
    0x00, 0x00, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E,  // Match Number
};

inline constexpr std::array<std::uint8_t, 36> kOrderExecutedWithPrice{
    0x43,                                            // Message Type 'C'
    0x00, 0x34,                                      // Stock Locate
    0x17, 0x18,                                      // Tracking Number
    0x01, 0x93, 0xA4, 0xB5, 0xC6, 0xD7,              // Timestamp
    0x00, 0x00, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09,  // Order Reference
    0x00, 0x00, 0x00, 0x96,                          // Executed Shares 150
    0x00, 0x00, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,  // Match Number
    0x4E,                                            // Printable 'N'
    0x00, 0x1D, 0x99, 0x62,                          // Execution Price 1939810
};

inline constexpr std::array<std::uint8_t, 23> kOrderCancel{
    0x58,                                            // Message Type 'X'
    0x00, 0x35,                                      // Stock Locate
    0x19, 0x1A,                                      // Tracking Number
    0x01, 0x94, 0xA5, 0xB6, 0xC7, 0xD8,              // Timestamp
    0x00, 0x00, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A,  // Order Reference
    0x00, 0x00, 0x00, 0x32,                          // Cancelled Shares 50
};

inline constexpr std::array<std::uint8_t, 19> kOrderDelete{
    0x44,                                            // Message Type 'D'
    0x00, 0x36,                                      // Stock Locate
    0x1B, 0x1C,                                      // Tracking Number
    0x01, 0x95, 0xA6, 0xB7, 0xC8, 0xD9,              // Timestamp
    0x00, 0x00, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B,  // Order Reference
};

inline constexpr std::array<std::uint8_t, 35> kOrderReplace{
    0x55,                                            // Message Type 'U'
    0x00, 0x37,                                      // Stock Locate
    0x1D, 0x1E,                                      // Tracking Number
    0x01, 0x96, 0xA7, 0xB8, 0xC9, 0xDA,              // Timestamp
    0x00, 0x00, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C,  // Original Order Reference
    0x00, 0x00, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D,  // New Order Reference
    0x00, 0x00, 0x03, 0x20,                          // Shares 800
    0x00, 0x1E, 0xAA, 0x73,                          // Price 2009715
};

inline constexpr std::array<std::uint8_t, 44> kTrade{
    0x50,                                            // Message Type 'P'
    0x00, 0x38,                                      // Stock Locate
    0x1F, 0x20,                                      // Tracking Number
    0x01, 0x97, 0xA8, 0xB9, 0xCA, 0xDB,              // Timestamp
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,  // Order Reference: zero since 2010
    0x42,                                            // Side: always 'B' since 2014
    0x00, 0x00, 0x03, 0x84,                          // Shares 900
    0x54, 0x45, 0x53, 0x54, 0x50, 0x20, 0x20, 0x20,  // Stock "TESTP   "
    0x00, 0x1F, 0xBB, 0x84,                          // Price 2079620
    0x00, 0x00, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10,  // Match Number
};

inline constexpr std::array<std::uint8_t, 40> kCrossTrade{
    0x51,                                            // Message Type 'Q'
    0x00, 0x39,                                      // Stock Locate
    0x21, 0x22,                                      // Tracking Number
    0x01, 0x98, 0xA9, 0xBA, 0xCB, 0xDC,              // Timestamp
    0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05,  // Shares, 8 bytes
    0x54, 0x45, 0x53, 0x54, 0x51, 0x20, 0x20, 0x20,  // Stock "TESTQ   "
    0x00, 0x20, 0xCC, 0x95,                          // Cross Price 2149525
    0x00, 0x00, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11,  // Match Number
    0x4F,                                            // Cross Type 'O'
};

inline constexpr std::array<std::uint8_t, 19> kBrokenTrade{
    0x42,                                            // Message Type 'B'
    0x00, 0x3A,                                      // Stock Locate
    0x23, 0x24,                                      // Tracking Number
    0x01, 0x99, 0xAA, 0xBB, 0xCC, 0xDD,              // Timestamp
    0x00, 0x00, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x12,  // Match Number
};

inline constexpr std::array<std::uint8_t, 50> kNetOrderImbalance{
    0x49,                                            // Message Type 'I'
    0x00, 0x3B,                                      // Stock Locate
    0x25, 0x26,                                      // Tracking Number
    0x01, 0x9A, 0xAB, 0xBC, 0xCD, 0xDE,              // Timestamp
    0x00, 0x00, 0x00, 0x02, 0x03, 0x04, 0x05, 0x06,  // Paired Shares
    0x00, 0x00, 0x00, 0x03, 0x04, 0x05, 0x06, 0x07,  // Imbalance Shares
    0x53,                                            // Imbalance Direction 'S'
    0x54, 0x45, 0x53, 0x54, 0x49, 0x20, 0x20, 0x20,  // Stock "TESTI   "
    0x00, 0x21, 0xDD, 0xA6,                          // Far Price
    0x00, 0x22, 0xEE, 0xB7,                          // Near Price
    0x00, 0x23, 0xFF, 0xC8,                          // Current Reference Price
    0x48,                                            // Cross Type 'H'
    0x41,                                            // Price Variation Indicator 'A'
};

inline constexpr std::array<std::uint8_t, 20> kRetailPriceImprovement{
    0x4E,                                            // Message Type 'N'
    0x00, 0x3C,                                      // Stock Locate
    0x27, 0x28,                                      // Tracking Number
    0x01, 0x9B, 0xAC, 0xBD, 0xCE, 0xDF,              // Timestamp
    0x54, 0x45, 0x53, 0x54, 0x4E, 0x20, 0x20, 0x20,  // Stock "TESTN   "
    0x41,                                            // Interest Flag 'A'
};

inline constexpr std::array<std::uint8_t, 48> kDirectListingPriceDiscovery{
    0x4F,                                            // Message Type 'O'
    0x00, 0x3D,                                      // Stock Locate
    0x29, 0x2A,                                      // Tracking Number
    0x01, 0x9C, 0xAD, 0xBE, 0xCF, 0xE0,              // Timestamp
    0x54, 0x45, 0x53, 0x54, 0x4F, 0x20, 0x20, 0x20,  // Stock "TESTO   "
    0x59,                                            // Open Eligibility Status 'Y'
    0x00, 0x05, 0x16, 0x27,                          // Minimum Allowable Price
    0x00, 0x06, 0x17, 0x28,                          // Maximum Allowable Price
    0x00, 0x07, 0x18, 0x29,                          // Near Execution Price
    0x00, 0x00, 0x1E, 0x2F, 0x30, 0x41, 0x52, 0x63,  // Near Execution Time, 8 bytes
    0x00, 0x08, 0x19, 0x2A,                          // Lower Price Range Collar
    0x00, 0x09, 0x1A, 0x2B,                          // Upper Price Range Collar
};

// A valid System Event 'C', the required end of a complete file.
inline constexpr std::array<std::uint8_t, 12> kEndOfMessages{
    0x53,                                // Message Type 'S'
    0x00, 0x00,                          // Stock Locate: always 0
    0x2B, 0x2C,                          // Tracking Number
    0x41, 0xC1, 0x23, 0x45, 0x67, 0x89,  // Timestamp
    0x43,                                // Event Code 'C'
};

using Bytes = std::vector<std::byte>;

// Wire bytes as the std::byte the parser reads.
[[nodiscard]] inline Bytes to_bytes(std::span<const std::uint8_t> raw) {
  Bytes out(raw.size());
  for (std::size_t i = 0; i < raw.size(); ++i) {
    out[i] = static_cast<std::byte>(raw[i]);
  }
  return out;
}

// Prefixes a payload with its 2-byte big-endian length.
inline void append_frame(Bytes& stream, std::span<const std::byte> payload) {
  stream.push_back(static_cast<std::byte>(payload.size() >> 8U));
  stream.push_back(static_cast<std::byte>(payload.size() & 0xFFU));
  stream.insert(stream.end(), payload.begin(), payload.end());
}

// All 23 fixtures, in the order of the spec.
[[nodiscard]] inline std::array<Bytes, 23> all_messages() {
  return {
      to_bytes(kSystemEvent),
      to_bytes(kStockDirectory),
      to_bytes(kStockTradingAction),
      to_bytes(kRegShoRestriction),
      to_bytes(kMarketParticipantPosition),
      to_bytes(kMwcbDeclineLevel),
      to_bytes(kMwcbStatus),
      to_bytes(kIpoQuotingPeriodUpdate),
      to_bytes(kLuldAuctionCollar),
      to_bytes(kOperationalHalt),
      to_bytes(kAddOrder),
      to_bytes(kAddOrderAttributed),
      to_bytes(kOrderExecuted),
      to_bytes(kOrderExecutedWithPrice),
      to_bytes(kOrderCancel),
      to_bytes(kOrderDelete),
      to_bytes(kOrderReplace),
      to_bytes(kTrade),
      to_bytes(kCrossTrade),
      to_bytes(kBrokenTrade),
      to_bytes(kNetOrderImbalance),
      to_bytes(kRetailPriceImprovement),
      to_bytes(kDirectListingPriceDiscovery),
  };
}

// A stream of every message type, framed, ending on System Event 'C'.
[[nodiscard]] inline Bytes all_messages_stream() {
  Bytes stream;
  for (const Bytes& message : all_messages()) {
    append_frame(stream, message);
  }
  append_frame(stream, to_bytes(kEndOfMessages));
  return stream;
}

}  // namespace itch::fixtures
