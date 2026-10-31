// Wire bytes for the order messages, for hand-built scenarios and the fuzz harness.
// Goes through tests/support/encode.hpp, so nothing here shares an offset with either
// book's decode.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "itch/messages.hpp"
#include "support/encode.hpp"

namespace itch::oracle::wire {

using Bytes = std::vector<std::byte>;

[[nodiscard]] inline Header header(char type, std::uint16_t locate, std::uint64_t timestamp) {
  return {.message_type = type,
          .stock_locate = locate,
          .tracking_number = 0,
          .timestamp_ns = timestamp};
}

[[nodiscard]] inline Stock stock(char tag) { return Stock{'S', 'Y', 'M', tag, ' ', ' ', ' ', ' '}; }

[[nodiscard]] inline Bytes add(std::uint16_t locate, std::uint64_t reference, char side,
                               std::uint32_t shares, Price4 price, std::uint64_t timestamp = 1) {
  return testing::encode(AddOrder{.header = header('A', locate, timestamp),
                                  .order_reference = reference,
                                  .side = side,
                                  .shares = shares,
                                  .stock = stock('A'),
                                  .price = price});
}

[[nodiscard]] inline Bytes add_attributed(std::uint16_t locate, std::uint64_t reference, char side,
                                          std::uint32_t shares, Price4 price,
                                          std::uint64_t timestamp = 1) {
  return testing::encode(AddOrderAttributed{.header = header('F', locate, timestamp),
                                            .order_reference = reference,
                                            .side = side,
                                            .shares = shares,
                                            .stock = stock('F'),
                                            .price = price,
                                            .attribution = Mpid{'M', 'P', 'I', 'D'}});
}

[[nodiscard]] inline Bytes execute(std::uint16_t locate, std::uint64_t reference,
                                   std::uint32_t shares, std::uint64_t timestamp = 1) {
  return testing::encode(OrderExecuted{.header = header('E', locate, timestamp),
                                       .order_reference = reference,
                                       .executed_shares = shares,
                                       .match_number = 7});
}

[[nodiscard]] inline Bytes execute_with_price(std::uint16_t locate, std::uint64_t reference,
                                              std::uint32_t shares, Price4 price,
                                              std::uint64_t timestamp = 1) {
  return testing::encode(OrderExecutedWithPrice{.header = header('C', locate, timestamp),
                                                .order_reference = reference,
                                                .executed_shares = shares,
                                                .match_number = 8,
                                                .printable = 'Y',
                                                .execution_price = price});
}

[[nodiscard]] inline Bytes cancel(std::uint16_t locate, std::uint64_t reference,
                                  std::uint32_t shares, std::uint64_t timestamp = 1) {
  return testing::encode(OrderCancel{.header = header('X', locate, timestamp),
                                     .order_reference = reference,
                                     .cancelled_shares = shares});
}

[[nodiscard]] inline Bytes erase(std::uint16_t locate, std::uint64_t reference,
                                 std::uint64_t timestamp = 1) {
  return testing::encode(
      OrderDelete{.header = header('D', locate, timestamp), .order_reference = reference});
}

[[nodiscard]] inline Bytes replace(std::uint16_t locate, std::uint64_t old_reference,
                                   std::uint64_t new_reference, std::uint32_t shares, Price4 price,
                                   std::uint64_t timestamp = 1) {
  return testing::encode(OrderReplace{.header = header('U', locate, timestamp),
                                      .original_order_reference = old_reference,
                                      .new_order_reference = new_reference,
                                      .shares = shares,
                                      .price = price});
}

[[nodiscard]] inline Bytes trade(std::uint16_t locate, std::uint32_t shares, Price4 price,
                                 std::uint64_t timestamp = 1) {
  return testing::encode(Trade{.header = header('P', locate, timestamp),
                               .order_reference = 0,
                               .side = 'B',
                               .shares = shares,
                               .stock = stock('P'),
                               .price = price,
                               .match_number = 9});
}

[[nodiscard]] inline Bytes cross(std::uint16_t locate, std::uint64_t shares, Price4 price,
                                 std::uint64_t timestamp = 1) {
  return testing::encode(CrossTrade{.header = header('Q', locate, timestamp),
                                    .shares = shares,
                                    .stock = stock('Q'),
                                    .cross_price = price,
                                    .match_number = 10,
                                    .cross_type = 'O'});
}

[[nodiscard]] inline Bytes broken(std::uint16_t locate, std::uint64_t timestamp = 1) {
  return testing::encode(BrokenTrade{.header = header('B', locate, timestamp), .match_number = 10});
}

[[nodiscard]] inline Bytes trading_action(std::uint16_t locate, char state,
                                          std::uint64_t timestamp = 1) {
  return testing::encode(StockTradingAction{.header = header('H', locate, timestamp),
                                            .stock = stock('H'),
                                            .trading_state = state,
                                            .reserved = ' ',
                                            .reason = HaltReason{' ', ' ', ' ', ' '}});
}

[[nodiscard]] inline Bytes system_event(char code, std::uint64_t timestamp = 1) {
  return testing::encode(SystemEvent{.header = header('S', 0, timestamp), .event_code = code});
}

}  // namespace itch::oracle::wire
