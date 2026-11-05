// Emscripten only: the page's engine, through its C interface, on the hand-built extract of
// tests/support/demo_stream.hpp, under wasm32 in node. It must leave the book whose digest
// the native test holds, in a linear memory of kEngineMemoryBytes that did not grow.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>

#include "demo_engine.hpp"
#include "support/demo_stream.hpp"

namespace {

std::size_t memory_bytes() { return __builtin_wasm_memory_size(0) * std::size_t{65536}; }

}  // namespace

int main() {
  const std::size_t memory_at_start = memory_bytes();
  const auto stream = itch::testing::demo_stream();
  std::byte* input = demo_input(static_cast<double>(stream.size()));
  if (input == nullptr) {
    std::puts("demo smoke: no room for the input");
    return 1;
  }
  std::ranges::copy(stream, input);
  const int loaded = demo_load();
  const double applied = demo_replay();
  const std::uint64_t digest = (static_cast<std::uint64_t>(demo_digest_high()) << 32U) |
                               static_cast<std::uint64_t>(demo_digest_low());
  const bool fixed =
      memory_at_start == itch::demo::kEngineMemoryBytes && memory_bytes() == memory_at_start;
  std::printf("demo smoke: status %d, %.0f messages, digest 0x%016llx, memory %zu bytes%s\n",
              loaded, applied, static_cast<unsigned long long>(digest), memory_bytes(),
              fixed ? "" : ", not the engine's fixed size");
  return loaded == 0 && applied == 20.0 && digest == itch::testing::kDemoStreamDigest && fixed ? 0
                                                                                               : 1;
}
