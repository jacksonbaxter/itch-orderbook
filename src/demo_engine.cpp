// The C interface of demo_engine.hpp. Nothing here times anything: the page holds the
// clock, and WebAssembly has none of its own.
#include "demo_engine.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <span>
#include <utility>
#include <vector>

#include "itch/book_types.hpp"
#include "itch/demo_replay.hpp"

namespace {

using itch::demo::DepthLevel;
using itch::demo::Replay;

struct Engine {
  std::vector<std::byte> input;
  // On the heap rather than in a std::optional: GCC 14 at -O3 warns that emplace()'s reset of
  // the static optional may read its payload uninitialized (-Wmaybe-uninitialized).
  std::unique_ptr<Replay> replay;
  std::array<DepthLevel, itch::demo::kDepthLevels> depth{};
  std::array<char, 9> symbol{};
};

Engine& engine() {
  static Engine instance;
  return instance;
}

// The replay, or nullptr before the first load.
Replay* live() { return engine().replay.get(); }

double count(std::uint64_t value) { return static_cast<double>(value); }

}  // namespace

extern "C" {

std::byte* demo_input(double bytes) {
  Engine& e = engine();
  e.replay.reset();
  if (!(bytes >= 0.0) || bytes > static_cast<double>(itch::demo::kMaxInputBytes)) {
    e.input.clear();
    return nullptr;
  }
  e.input.assign(static_cast<std::size_t>(bytes), std::byte{0});
  return e.input.data();
}

int demo_load() {
  Engine& e = engine();
  e.replay.reset();  // The old book goes before the new one is built: memory holds one.
  e.replay = std::make_unique<Replay>(std::span<const std::byte>{e.input});
  return static_cast<int>(e.replay->extract_status());
}

double demo_replay() {
  Replay* replay = live();
  return replay != nullptr ? count(replay->replay()) : 0.0;
}

double demo_step(double messages) {
  Replay* replay = live();
  if (replay == nullptr || !(messages > 0.0)) {
    return 0.0;
  }
  return count(replay->step(static_cast<std::uint64_t>(messages)));
}

double demo_messages() {
  const Replay* replay = live();
  return replay != nullptr ? count(replay->messages()) : 0.0;
}
double demo_applied() {
  const Replay* replay = live();
  return replay != nullptr ? count(replay->applied()) : 0.0;
}

double demo_status(int status) {
  const Replay* replay = live();
  if (replay == nullptr || status < 0 || std::cmp_greater_equal(status, itch::demo::kStatusCount)) {
    return 0.0;
  }
  return count(replay->status_count(static_cast<itch::ApplyStatus>(status)));
}

double demo_first_timestamp_ns() {
  const Replay* replay = live();
  return replay != nullptr ? count(replay->first_timestamp_ns()) : 0.0;
}
double demo_last_timestamp_ns() {
  const Replay* replay = live();
  return replay != nullptr ? count(replay->last_timestamp_ns()) : 0.0;
}

const char* demo_symbol() {
  Engine& e = engine();
  e.symbol.fill('\0');
  if (const Replay* replay = live(); replay != nullptr) {
    std::ranges::copy(replay->symbol(), e.symbol.begin());
  }
  return e.symbol.data();
}

int demo_depth(int side, int levels) {
  Engine& e = engine();
  const Replay* replay = live();
  if (replay == nullptr || (side != 0 && side != 1) || levels <= 0) {
    return 0;
  }
  const auto wanted = std::min(static_cast<std::size_t>(levels), e.depth.size());
  return static_cast<int>(replay->depth(side == 0 ? itch::Side::bid : itch::Side::ask,
                                        std::span<DepthLevel>{e.depth}.first(wanted)));
}

const DepthLevel* demo_depth_buffer() { return engine().depth.data(); }

double demo_prints() {
  const Replay* replay = live();
  return replay != nullptr ? count(replay->prints()) : 0.0;
}

const itch::demo::Print* demo_print(double n) {
  const Replay* replay = live();
  if (replay == nullptr || !(n >= 0.0)) {
    return nullptr;
  }
  const auto at = static_cast<std::uint64_t>(n);
  const std::uint64_t total = replay->prints();
  if (at >= total || total - at > itch::demo::kPrintRing) {
    return nullptr;
  }
  return &replay->print(at);
}

double demo_digest_high() {
  const Replay* replay = live();
  return replay != nullptr ? count(replay->digest() >> 32U) : 0.0;
}
double demo_digest_low() {
  const Replay* replay = live();
  return replay != nullptr ? count(replay->digest() & 0xFFFFFFFFU) : 0.0;
}

double demo_max_input_bytes() { return count(itch::demo::kMaxInputBytes); }
double demo_memory_bytes() { return count(itch::demo::kEngineMemoryBytes); }

}  // extern "C"
