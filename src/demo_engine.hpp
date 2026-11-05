// The page's engine: demo_replay.hpp's Replay behind a C interface for JavaScript.
//
// em++ builds src/demo_engine.cpp into docs/local/itch_demo.mjs and its .wasm, with a
// linear memory fixed at kEngineMemoryBytes that never grows (src/CMakeLists.txt). The
// native build compiles the same file into a library, so that clang-tidy and the native
// tests see it too.
//
// Every count crosses as a double, exact below 2^53, so that the page needs no BigInt. A
// pointer crosses as an offset into linear memory. One engine per process, holding one
// input and at most one replay over it.
#pragma once

#include <cstddef>
#include <cstdint>

#include "itch/demo_replay.hpp"

namespace itch::demo {

// The book at its default capacity holds about 663 MiB, so 1 GiB leaves room for an
// extract of up to kMaxInputBytes and the runtime. A day does not fit: 12302019 is 7.7 GiB.
inline constexpr std::size_t kEngineMemoryBytes = std::size_t{1} << 30U;
inline constexpr std::size_t kMaxInputBytes = std::size_t{256} << 20U;
inline constexpr std::size_t kDepthLevels = 64;

}  // namespace itch::demo

extern "C" {
// Room for an extract of `bytes`, which the page then fills, and its offset; or null if it is
// larger than kMaxInputBytes. Drops any replay over the previous input.
std::byte* demo_input(double bytes);
// A fresh book over the whole input, constructed here and not timed. Returns the extract's
// ExtractStatus, 0 when it is one symbol's.
int demo_load();
// Replay::replay(), the call the page times, and Replay::step(). Each returns messages applied.
double demo_replay();
double demo_step(double messages);

double demo_messages();
double demo_applied();
double demo_status(int status);
double demo_first_timestamp_ns();
double demo_last_timestamp_ns();
// The symbol, space-padded to 8 characters as on the wire, and a terminating zero.
const char* demo_symbol();
// The best `levels` of side 0 (bid) or 1 (ask), at most kDepthLevels, into the buffer whose
// offset demo_depth_buffer() gives. Returns how many it wrote.
int demo_depth(int side, int levels);
const itch::demo::DepthLevel* demo_depth_buffer();
double demo_prints();
// The n-th print, or null once it has left the ring.
const itch::demo::Print* demo_print(double n);
// Replay::digest() in two 32-bit halves.
double demo_digest_high();
double demo_digest_low();
double demo_max_input_bytes();
double demo_memory_bytes();
}
