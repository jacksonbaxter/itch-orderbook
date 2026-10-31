// Emscripten only: runs the reader and every decoder under wasm32 in node, where
// size_t is 32 bits, so that em++ has compiled and run the parser headers and not
// only included them.
#include <cstdint>
#include <cstdio>

#include "itch/reader.hpp"
#include "support/encode.hpp"
#include "support/fixtures.hpp"

int main() {
  const auto stream = itch::fixtures::all_messages_stream();
  itch::FrameReader reader{stream};
  std::uint64_t round_tripped = 0;
  auto frame = reader.next();
  for (; !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == itch::FrameStatus::message && itch::testing::round_trips(frame.payload)) {
      ++round_tripped;
    }
  }
  const bool clean = frame.status == itch::FrameStatus::end_of_input;
  std::printf("wasm smoke: %llu of 24 messages round-tripped\n",
              static_cast<unsigned long long>(round_tripped));
  return clean && round_tripped == 24 ? 0 : 1;
}
