// The C interface of demo_engine.hpp. Nothing here times anything: the page holds the
// clock, and WebAssembly has none of its own.
#include "demo_engine.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <vector>

#include "itch/book_types.hpp"
#include "itch/demo_replay.hpp"

namespace {

using itch::demo::DepthLevel;
using itch::demo::Replay;

struct Engine {
  std::vector<std::byte> input;
  std::optional<Replay> replay;
  std::array<DepthLevel, itch::demo::kDepthLevels> depth{};
  std::array<char, 9> symbol{};
};

Engine& engine() {
  static Engine instance;
  return instance;
}

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
  e.replay.emplace(std::span<const std::byte>{e.input});
  return static_cast<int>(e.replay->extract_status());
}

double demo_replay() { return engine().replay ? count(engine().replay->replay()) : 0.0; }

double demo_step(double messages) {
  Engine& e = engine();
  if (!e.replay || !(messages > 0.0)) {
    return 0.0;
  }
  return count(e.replay->step(static_cast<std::uint64_t>(messages)));
}

double demo_messages() { return engine().replay ? count(engine().replay->messages()) : 0.0; }
double demo_applied() { return engine().replay ? count(engine().replay->applied()) : 0.0; }

double demo_status(int status) {
  if (!engine().replay || status < 0 || status >= static_cast<int>(itch::demo::kStatusCount)) {
    return 0.0;
  }
  return count(engine().replay->status_count(static_cast<itch::ApplyStatus>(status)));
}

double demo_first_timestamp_ns() {
  return engine().replay ? count(engine().replay->first_timestamp_ns()) : 0.0;
}
double demo_last_timestamp_ns() {
  return engine().replay ? count(engine().replay->last_timestamp_ns()) : 0.0;
}

const char* demo_symbol() {
  Engine& e = engine();
  e.symbol.fill('\0');
  if (e.replay) {
    const itch::Stock& stock = e.replay->symbol();
    std::copy(stock.begin(), stock.end(), e.symbol.begin());
  }
  return e.symbol.data();
}

int demo_depth(int side, int levels) {
  Engine& e = engine();
  if (!e.replay || (side != 0 && side != 1) || levels <= 0) {
    return 0;
  }
  const auto wanted = std::min(static_cast<std::size_t>(levels), e.depth.size());
  return static_cast<int>(e.replay->depth(side == 0 ? itch::Side::bid : itch::Side::ask,
                                          std::span<DepthLevel>{e.depth}.first(wanted)));
}

const DepthLevel* demo_depth_buffer() { return engine().depth.data(); }

double demo_prints() { return engine().replay ? count(engine().replay->prints()) : 0.0; }

const itch::demo::Print* demo_print(double n) {
  const Engine& e = engine();
  if (!e.replay || !(n >= 0.0)) {
    return nullptr;
  }
  const auto at = static_cast<std::uint64_t>(n);
  const std::uint64_t total = e.replay->prints();
  if (at >= total || total - at > itch::demo::kPrintRing) {
    return nullptr;
  }
  return &e.replay->print(at);
}

double demo_digest_high() {
  return engine().replay ? count(engine().replay->digest() >> 32U) : 0.0;
}
double demo_digest_low() {
  return engine().replay ? count(engine().replay->digest() & 0xFFFFFFFFU) : 0.0;
}

double demo_max_input_bytes() { return count(itch::demo::kMaxInputBytes); }
double demo_memory_bytes() { return count(itch::demo::kEngineMemoryBytes); }

}  // extern "C"
