// Whether two cores' clocks agree, before a one-way latency read on two cores means anything.
//
// A one-way latency subtracts a stamp read on the sender's core from one read on the
// receiver's, so any offset between the two cores' counters lands in every sample. A
// constant, invariant TSC is meant to be synchronised across a socket, but that is a
// promise of the platform, not a measurement. This measures it.
//
// One thread on each core, handing a flag back and forth. On each trip the starting side
// reads t1, then publishes; the other side observes, reads t2, publishes; the starting side
// observes and reads t3. Real time orders them t1 < t2 < t3, so if the other core's clock
// reads `offset` ticks ahead of the starting core's, t1 <= t2 - offset <= t3, that is
// t2 - t3 <= offset <= t2 - t1. Over many trips the bound is the tightest of them. Then
// the roles swap, so that each core starts half the trips, which bounds the offset from
// the other side too. The clocks agree when the bound holds zero, and the bound is no
// wider than the fastest round trip; no trip can put the offset outside it, so a bound
// with its low end above its high end is a clock that went backwards between cores.
#pragma once

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <limits>
#include <span>
#include <thread>

#include "platform/affinity.hpp"
#include "platform/timer.hpp"

namespace itch::bench {

// One trip, each stamp in its own core's ticks.
struct CrossCoreTrip {
  std::uint64_t start = 0;  // t1, on the starting core
  std::uint64_t reply = 0;  // t2, on the other core
  std::uint64_t back = 0;   // t3, on the starting core
};

// The other core's clock minus the starting core's, in ticks, as far as the trips bound it.
struct OffsetBound {
  std::int64_t low = std::numeric_limits<std::int64_t>::min();
  std::int64_t high = std::numeric_limits<std::int64_t>::max();
  std::uint64_t backwards = 0;  // Trips with t2 < t1 or t3 < t2, read as if the offset were 0.
  std::uint64_t fastest_round_trip = std::numeric_limits<std::uint64_t>::max();

  [[nodiscard]] bool consistent() const noexcept { return low <= high; }
  [[nodiscard]] bool holds_zero() const noexcept { return low <= 0 && 0 <= high; }
};

[[nodiscard]] inline std::int64_t signed_difference(std::uint64_t a, std::uint64_t b) noexcept {
  return static_cast<std::int64_t>(a - b);
}

[[nodiscard]] inline OffsetBound bound_offset(std::span<const CrossCoreTrip> trips) noexcept {
  OffsetBound bound;
  for (const CrossCoreTrip& trip : trips) {
    bound.low = std::max(bound.low, signed_difference(trip.reply, trip.back));
    bound.high = std::min(bound.high, signed_difference(trip.reply, trip.start));
    bound.fastest_round_trip = std::min(bound.fastest_round_trip, trip.back - trip.start);
    if (trip.reply < trip.start || trip.back < trip.reply) {
      ++bound.backwards;
    }
  }
  return bound;
}

// The bound on core B's clock minus core A's, from trips A started and trips B started.
[[nodiscard]] inline OffsetBound combine(const OffsetBound& a_started,
                                         const OffsetBound& b_started) noexcept {
  // B's trips bound A minus B, so their ends change sides and sign.
  return {
      .low = std::max(a_started.low, -b_started.high),
      .high = std::min(a_started.high, -b_started.low),
      .backwards = a_started.backwards + b_started.backwards,
      .fastest_round_trip = std::min(a_started.fastest_round_trip, b_started.fastest_round_trip)};
}

struct CrossCoreRun {
  PinOutcome self_pin;
  PinOutcome peer_pin;
  OffsetBound self_started;  // Trips this thread started: the peer's clock minus this one's.
  OffsetBound peer_started;  // Trips the peer started: this clock minus the peer's.
};

namespace detail {

// One side's half of the trips. `starts` says whether this side reads t1 and t3; the
// other reads t2. Flag values count up, so neither side can mistake an old publish for a
// new one.
inline void cross_core_side(std::atomic<std::uint64_t>& flag, std::atomic<std::uint64_t>& reply,
                            bool starts, std::span<CrossCoreTrip> trips) {
  for (std::size_t i = 0; i < trips.size(); ++i) {
    const std::uint64_t asked = (2 * i) + 1;
    if (starts) {
      // rdtscp;lfence: the read is done before the publish can be.
      trips[i].start = ticks_stop();
      flag.store(asked, std::memory_order_release);
      while (flag.load(std::memory_order_acquire) != asked + 1) {
      }
      // lfence;rdtsc: the observe is done before the read starts.
      trips[i].back = ticks_start();
      trips[i].reply = reply.load(std::memory_order_relaxed);
    } else {
      while (flag.load(std::memory_order_acquire) != asked) {
      }
      reply.store(ticks_start(), std::memory_order_relaxed);
      flag.store(asked + 1, std::memory_order_release);
    }
  }
}

}  // namespace detail

// `trips` round trips started on each core, `self` and `peer` the two cores. `buffer`
// holds at least `trips` entries, so nothing is allocated while the clocks are read.
[[nodiscard]] inline CrossCoreRun check_cross_core(int self, int peer,
                                                   std::span<CrossCoreTrip> buffer) {
  CrossCoreRun run;
  run.self_pin = pin_current_thread(self);
  for (const bool self_starts : {true, false}) {
    std::atomic<std::uint64_t> flag{0};
    std::atomic<std::uint64_t> reply{0};
    std::atomic<bool> peer_ready{false};
    std::thread other([&] {
      // A thread for each half, each pinned; the record keeps a failure from either.
      const PinOutcome pin = pin_current_thread(peer);
      if (self_starts || !pin.pinned) {
        run.peer_pin = pin;
      }
      peer_ready.store(true, std::memory_order_release);
      detail::cross_core_side(flag, reply, !self_starts, buffer);
    });
    while (!peer_ready.load(std::memory_order_acquire)) {
    }
    detail::cross_core_side(flag, reply, self_starts, buffer);
    other.join();
    (self_starts ? run.self_started : run.peer_started) = bound_offset(buffer);
  }
  return run;
}

}  // namespace itch::bench
