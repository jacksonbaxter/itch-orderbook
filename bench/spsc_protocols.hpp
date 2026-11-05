// The four protocols bench_spsc runs on each queue, from components.md's plan:
//
//   rtt         ping-pong: this thread pushes into one queue, the peer pops and pushes the
//               item back through a second, so one item is in flight. The mean of a long
//               span over the round trips, as LC-1 and LC-5 publish it. The coherence
//               floor: no cached-index gain is expected here.
//   saturated   one-way: the peer pushes sequence numbers as fast as the queue takes them
//               and this thread pops and checks each. The first lap of the ring is left
//               out of the span, so the rate is of a ring in steady state (LC-4's short
//               runs did not).
//   paced       one-way at a fixed gap: the peer waits for each send time, stamps the
//               item with the clock and pushes it; this thread pops it, reads the clock
//               and records the difference. The Disruptor's protocol (LC-12), the one
//               that gives tails.
//   burst       one-way, real messages at their real times: a stretch of the day's
//               messages, each in a 64-byte item with its own length of real bytes, sent
//               at the gaps between their ITCH timestamps, latency recorded as paced does.
//
// The peer runs on a thread of its own, pinned to the peer core where the platform can
// pin, started and waiting before any span opens. Every protocol checks what it moves: an
// item out of order, or a stamp that goes backwards, is counted, and the caller refuses a
// run with any.
#pragma once

#include <hdr/hdr_histogram.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <span>
#include <thread>
#include <utility>
#include <vector>

#include "platform/affinity.hpp"
#include "platform/timer.hpp"

namespace itch::bench {

// A histogram of ticks, as bench_replay's.
class TickHistogram {
 public:
  TickHistogram() { hdr_init(1, 10'000'000'000, 3, &histogram_); }
  TickHistogram(const TickHistogram&) = delete;
  TickHistogram& operator=(const TickHistogram&) = delete;
  TickHistogram(TickHistogram&&) = delete;
  TickHistogram& operator=(TickHistogram&&) = delete;
  ~TickHistogram() {
    if (histogram_ != nullptr) {
      hdr_close(histogram_);
    }
  }
  void record(std::uint64_t value) noexcept {
    hdr_record_value(histogram_, static_cast<std::int64_t>(value));
  }
  [[nodiscard]] double at(double percentile) const noexcept {
    return static_cast<double>(hdr_value_at_percentile(histogram_, percentile));
  }
  [[nodiscard]] double highest() const noexcept { return static_cast<double>(hdr_max(histogram_)); }
  [[nodiscard]] double mean() const noexcept { return hdr_mean(histogram_); }
  [[nodiscard]] std::int64_t count() const noexcept { return histogram_->total_count; }

 private:
  hdr_histogram* histogram_ = nullptr;
};

// One protocol on one queue.
struct ProtocolRun {
  std::uint64_t items = 0;   // In the span.
  std::uint64_t ticks = 0;   // The span.
  std::uint64_t errors = 0;  // Out of order, a wrong item, or a stamp gone backwards.
  std::uint64_t late = 0;    // paced and burst: sends a whole gap or more behind time.
  std::unique_ptr<TickHistogram> latency;  // paced and burst: one-way, in ticks.
  PinOutcome peer_pin;
  // The core each thread was on when its part ended, -1 where unreadable. On macOS, which
  // cannot pin, the only record of where the two ran.
  int self_core = -1;
  int peer_core = -1;
};

// Starts `peer` on its own thread, pinned to peer_core, waits until it is spinning on
// the start flag, runs `self` here with the flag, and joins.
template <class Peer, class Self>
void run_pair(int peer_core, ProtocolRun& run, Peer peer, Self self) {
  std::atomic<bool> go{false};
  std::atomic<bool> waiting{false};
  std::thread thread{[&] {
    run.peer_pin = pin_current_thread(peer_core);
    waiting.store(true, std::memory_order_release);
    while (!go.load(std::memory_order_acquire)) {
      // Spinning, so that the start costs no wake-up.
    }
    peer();
    run.peer_core = current_core();
  }};
  while (!waiting.load(std::memory_order_acquire)) {
    // The peer is pinning itself.
  }
  self(go);
  run.self_core = current_core();
  thread.join();
}

template <class Queue, class T>
void push_spin(Queue& queue, const T& value) {
  while (!queue.try_push(value)) {
  }
}

template <class Queue, class T>
void pop_spin(Queue& queue, T& out) {
  while (!queue.try_pop(out)) {
  }
}

template <class Queue>
[[nodiscard]] ProtocolRun rtt(std::size_t capacity, std::uint64_t warmup, std::uint64_t trips,
                              int peer_core) {
  const auto ping = std::make_unique<Queue>(capacity);
  const auto pong = std::make_unique<Queue>(capacity);
  ProtocolRun run;
  run_pair(
      peer_core, run,
      [&] {
        for (std::uint64_t i = 0; i < warmup + trips; ++i) {
          std::uint64_t value = 0;
          pop_spin(*ping, value);
          push_spin(*pong, value);
        }
      },
      [&](std::atomic<bool>& go) {
        go.store(true, std::memory_order_release);
        std::uint64_t back = 0;
        for (std::uint64_t i = 0; i < warmup; ++i) {
          push_spin(*ping, i);
          pop_spin(*pong, back);
          run.errors += back == i ? 0U : 1U;
        }
        const std::uint64_t start = ticks_start();
        for (std::uint64_t i = warmup; i < warmup + trips; ++i) {
          push_spin(*ping, i);
          pop_spin(*pong, back);
          run.errors += back == i ? 0U : 1U;
        }
        run.ticks = ticks_stop() - start;
        run.items = trips;
      });
  return run;
}

// The peer pushes `items` sequence numbers. The span opens once this thread has popped the
// first `capacity` of them.
template <class Queue>
[[nodiscard]] ProtocolRun saturated(std::size_t capacity, std::uint64_t items, int peer_core) {
  const auto queue = std::make_unique<Queue>(capacity);
  ProtocolRun run;
  const std::uint64_t lap = std::min<std::uint64_t>(capacity, items / 2);
  run_pair(
      peer_core, run,
      [&] {
        for (std::uint64_t i = 0; i < items; ++i) {
          push_spin(*queue, i);
        }
      },
      [&](std::atomic<bool>& go) {
        go.store(true, std::memory_order_release);
        std::uint64_t value = 0;
        for (std::uint64_t i = 0; i < lap; ++i) {
          pop_spin(*queue, value);
          run.errors += value == i ? 0U : 1U;
        }
        const std::uint64_t start = ticks_start();
        for (std::uint64_t i = lap; i < items; ++i) {
          pop_spin(*queue, value);
          run.errors += value == i ? 0U : 1U;
        }
        run.ticks = ticks_stop() - start;
        run.items = items - lap;
      });
  return run;
}

// As saturated, through the ring's batch calls, `batch` items a call on each side: the
// protocol for batch publish against a store per item.
template <class Ring>
[[nodiscard]] ProtocolRun saturated_batch(std::size_t capacity, std::uint64_t items,
                                          std::size_t batch, int peer_core) {
  constexpr std::size_t kMaxBatch = 256;
  const auto ring = std::make_unique<Ring>(capacity);
  ProtocolRun run;
  const std::size_t size = std::min(batch, kMaxBatch);
  const std::uint64_t lap = std::min<std::uint64_t>(capacity, items / 2);
  run_pair(
      peer_core, run,
      [&] {
        std::array<std::uint64_t, kMaxBatch> values{};
        std::uint64_t next = 0;
        while (next < items) {
          const auto want = static_cast<std::size_t>(std::min<std::uint64_t>(size, items - next));
          for (std::size_t i = 0; i < want; ++i) {
            values[i] = next + i;
          }
          std::size_t at = 0;
          while (at < want) {
            at +=
                ring->try_push_batch(std::span<const std::uint64_t>{values}.subspan(at, want - at));
          }
          next += want;
        }
      },
      [&](std::atomic<bool>& go) {
        go.store(true, std::memory_order_release);
        std::array<std::uint64_t, kMaxBatch> out{};
        std::uint64_t expected = 0;
        std::uint64_t start = 0;
        bool started = false;
        while (expected < items) {
          const std::size_t got = ring->try_pop_batch(std::span{out}.first(size));
          for (std::size_t i = 0; i < got; ++i) {
            run.errors += out[i] == expected + i ? 0U : 1U;
          }
          expected += got;
          if (!started && expected >= lap) {
            start = ticks_start();
            run.items = items - expected;
            started = true;
          }
        }
        run.ticks = ticks_stop() - start;
      });
  return run;
}

// The peer sends one stamped item every gap_ticks, `warmup` of them first, outside the
// histogram. A send a whole gap or more behind its time is counted late and goes at once.
template <class Queue>
[[nodiscard]] ProtocolRun paced(std::size_t capacity, std::uint64_t warmup, std::uint64_t items,
                                std::uint64_t gap_ticks, int peer_core) {
  const auto queue = std::make_unique<Queue>(capacity);
  ProtocolRun run;
  run.latency = std::make_unique<TickHistogram>();
  std::uint64_t late = 0;
  run_pair(
      peer_core, run,
      [&] {
        const std::uint64_t origin = ticks_start();
        for (std::uint64_t i = 0; i < warmup + items; ++i) {
          const std::uint64_t due = origin + (i * gap_ticks);
          std::uint64_t now = ticks_start();
          while (now < due) {
            now = ticks_start();
          }
          late += now - due >= gap_ticks ? 1U : 0U;
          push_spin(*queue, now);
        }
      },
      [&](std::atomic<bool>& go) {
        go.store(true, std::memory_order_release);
        std::uint64_t previous = 0;
        std::uint64_t stamp = 0;
        const std::uint64_t start = ticks_start();
        for (std::uint64_t i = 0; i < warmup + items; ++i) {
          pop_spin(*queue, stamp);
          const std::uint64_t now = ticks_stop();
          // Sends that catch up after a late one can share a tick, so equal stamps are
          // in order; a stamp below the last, or above the clock that reads it, is not.
          run.errors += stamp >= previous && now >= stamp ? 0U : 1U;
          previous = stamp;
          if (i >= warmup) {
            run.latency->record(now - stamp);
          }
        }
        run.ticks = ticks_stop() - start;
        run.items = items;
      });
  run.late = late;
  return run;
}

// One real message in a 64-byte item: its send stamp, its place in the stretch, its
// length, and its bytes.
struct BurstItem {
  std::uint64_t stamp = 0;
  std::uint32_t sequence = 0;
  std::uint16_t length = 0;
  std::array<std::uint8_t, 50> bytes{};  // The longest ITCH 5.0 message, 'I', is 50.
};
static_assert(sizeof(BurstItem) == 64);

// The stretch: each message as it will be sent, and when, in ticks after the first.
struct BurstPlan {
  std::vector<BurstItem> items;
  std::vector<std::uint64_t> due_ticks;
  std::uint64_t byte_sum = 0;  // Of every message byte, for the consumer's check.
};

template <class Queue>
[[nodiscard]] ProtocolRun burst(std::size_t capacity, const BurstPlan& plan, int peer_core) {
  const auto queue = std::make_unique<Queue>(capacity);
  ProtocolRun run;
  run.latency = std::make_unique<TickHistogram>();
  std::uint64_t late = 0;
  const std::size_t count = plan.items.size();
  run_pair(
      peer_core, run,
      [&] {
        const std::uint64_t origin = ticks_start();
        for (std::size_t i = 0; i < count; ++i) {
          const std::uint64_t due = origin + plan.due_ticks[i];
          std::uint64_t now = ticks_start();
          while (now < due) {
            now = ticks_start();
          }
          // Messages of one burst share a timestamp, so a send is late only when it goes
          // after the next message was due.
          late += i + 1 < count && now > origin + plan.due_ticks[i + 1] ? 1U : 0U;
          BurstItem item = plan.items[i];
          item.stamp = now;
          push_spin(*queue, item);
        }
      },
      [&](std::atomic<bool>& go) {
        go.store(true, std::memory_order_release);
        BurstItem item;
        std::uint64_t byte_sum = 0;
        const std::uint64_t start = ticks_start();
        for (std::size_t i = 0; i < count; ++i) {
          pop_spin(*queue, item);
          const std::uint64_t now = ticks_stop();
          run.errors += item.sequence == i && now >= item.stamp ? 0U : 1U;
          for (std::size_t b = 0; b < item.length && b < item.bytes.size(); ++b) {
            byte_sum += item.bytes[b];
          }
          run.latency->record(now - item.stamp);
        }
        run.ticks = ticks_stop() - start;
        run.items = count;
        run.errors += byte_sum == plan.byte_sum ? 0U : 1U;
      });
  run.late = late;
  return run;
}

}  // namespace itch::bench
