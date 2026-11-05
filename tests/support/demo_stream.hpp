// A hand-built one-symbol extract for the demo's replay, used by the native test and by
// the wasm smoke test, which both hold Replay::digest() over it to the one constant below:
// a wasm build that left a different book behind fails there.
//
// Every value is invented. The symbol is on locate 3, and each print kind the page draws
// occurs, with the ones it must leave out: a non-printable C and a cross of no shares.
#pragma once

#include <cstdint>
#include <vector>

#include "oracle/wire_builder.hpp"
#include "support/fixtures.hpp"

namespace itch::testing {

inline constexpr std::uint16_t kDemoLocate = 3;

// Replay::digest() of demo_stream() replayed whole. A change to the stream, the book or the
// digest changes it, and then it is set again from the native test's failure message.
inline constexpr std::uint64_t kDemoStreamDigest = 0xde5249b7a137dc0eULL;

[[nodiscard]] inline std::vector<std::byte> demo_stream() {
  namespace wire = itch::oracle::wire;
  constexpr std::uint16_t s = kDemoLocate;
  std::vector<std::byte> stream;
  const auto put = [&stream](const std::vector<std::byte>& payload) {
    itch::fixtures::append_frame(stream, payload);
  };
  put(wire::system_event('O', 1'000));
  put(wire::directory(s, 'Q', 'P', 100, 2'000));
  put(wire::system_event('Q', 3'000));
  put(wire::add(s, 11, 'B', 300, 1'000'000, 4'000));
  put(wire::add(s, 12, 'B', 200, 999'900, 5'000));
  put(wire::add(s, 13, 'B', 100, 1'000'000, 6'000));
  put(wire::add_attributed(s, 21, 'S', 400, 1'000'200, 7'000));
  put(wire::add(s, 22, 'S', 500, 1'000'300, 8'000));
  put(wire::execute(s, 11, 100, 9'000));                        // E at 100.0000, a bid.
  put(wire::execute_with_price(s, 21, 50, 1'000'100, 10'000));  // C, printable.
  // C, not printable: deducted from the order and never drawn.
  put(itch::testing::encode(OrderExecutedWithPrice{.header = wire::header('C', s, 11'000),
                                                   .order_reference = 22,
                                                   .executed_shares = 25,
                                                   .match_number = 12,
                                                   .printable = 'N',
                                                   .execution_price = 1'000'300}));
  put(wire::trade(s, 70, 1'000'150, 12'000));  // P, against a hidden order.
  put(wire::cancel(s, 12, 50, 13'000));
  put(wire::replace(s, 13, 14, 150, 999'800, 14'000));
  put(wire::erase(s, 22, 15'000));
  put(wire::execute(s, 11, 200, 16'000));     // Fills 11, which leaves the book.
  put(wire::cross(s, 0, 1'000'000, 17'000));  // No shares: not drawn.
  put(wire::cross(s, 900, 1'000'050, 18'000));
  put(wire::execute(s, 99, 10, 19'000));  // Unknown order: refused, and no print.
  put(wire::system_event('C', 20'000));
  return stream;
}

}  // namespace itch::testing
