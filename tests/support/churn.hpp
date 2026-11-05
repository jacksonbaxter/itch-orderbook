// A stream that churns a fixed pool of orders through every order path, for the tests
// that need the book's every path many times over: test_alloc and test_pipeline.
#pragma once

#include <cstddef>
#include <cstdint>
#include <random>
#include <vector>

#include "oracle/wire_builder.hpp"
#include "support/fixtures.hpp"

namespace itch::testing {

// Churns a fixed pool of orders through every order path many times over, so that each
// pool and index recycle, on a stream framed once before any window opens. One new
// reference in five has bit 40 set.
[[nodiscard]] inline std::vector<std::byte> build_stream() {
  std::vector<std::byte> stream;
  namespace wire = oracle::wire;
  const auto put = [&stream](const wire::Bytes& message) {
    fixtures::append_frame(stream, message);
  };
  for (const auto& message : fixtures::all_messages()) {
    const char type = static_cast<char>(message[0]);
    if (type != 'E' && type != 'C' && type != 'X' && type != 'D' && type != 'U') {
      put(message);
    }
  }
  // The same stream every run.
  // NOLINTNEXTLINE(cert-msc32-c,cert-msc51-cpp,bugprone-random-generator-seed)
  std::mt19937 random{20191230};
  constexpr std::uint32_t kPool = 512;
  std::vector<std::uint64_t> live;
  std::uint64_t next_reference = 1;
  const auto fresh = [&next_reference] {
    const std::uint64_t reference = next_reference++;
    return reference % 5 == 0 ? reference | (std::uint64_t{1} << 40U) : reference;
  };
  for (int step = 0; step < 200000; ++step) {
    const auto price = static_cast<std::uint32_t>(1000 + ((random() % 64) * 100));
    if (live.size() < kPool / 2) {
      const std::uint64_t reference = fresh();
      put((step % 3 == 0 ? wire::add_attributed : wire::add)(
          1, reference, step % 2 == 0 ? 'B' : 'S', 300, price, 1));
      live.push_back(reference);
      continue;
    }
    const std::size_t pick = random() % live.size();
    const std::uint64_t reference = live[pick];
    switch (random() % 5) {
      case 0: put(wire::execute(1, reference, 1)); break;
      case 1: put(wire::execute_with_price(1, reference, 1, 77)); break;
      case 2: put(wire::cancel(1, reference, 1)); break;
      case 3: {
        const std::uint64_t replaced = fresh();
        put(wire::replace(1, reference, replaced, 300, price));
        live[pick] = replaced;
        break;
      }
      default:
        put(wire::erase(1, reference));
        live[pick] = live.back();
        live.pop_back();
        break;
    }
  }
  return stream;
}

}  // namespace itch::testing
