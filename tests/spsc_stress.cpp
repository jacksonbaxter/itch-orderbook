// The ring under stress, without TSan: a producer and a consumer thread through a small
// ring, many rounds, every item checked for its place in the order and for its content.
//
//     spsc_stress [--rounds N] [--messages N] [--capacity N] [--noise N] [--noise-mib N]
//
// Each round builds a fresh ring of --capacity slots, 4 by default, so that it stays near
// empty and full and each index is read across cores as often as it can be. An item is
// 256 bytes, a sequence number and 31 words fixed by it, so it spans more than one line
// on either host. The consumer pops --messages items a round and checks that the item it
// pops in place i carries sequence i and all 31 words of it. --noise starts that many
// threads that write through --noise-mib MiB each, over and over, so that the slots'
// lines are pushed out of the caches between a write and its read.
//
// With the shipped orders a single wrong item is a bug. In a negative-control build
// (-DITCH_SPSC_NEGATIVE_CONTROL, support/spsc_ordering.hpp) the count is what this
// machine showed: components.md, LC-37, could find no published case of a weakened SPSC
// order caught by stress on Apple silicon, so either outcome is reported as it is.
//
// Exit codes: 0 no wrong item, 1 at least one, 2 bad usage.
#include <algorithm>
#include <array>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <memory>
#include <print>
#include <span>
#include <string_view>
#include <system_error>
#include <thread>
#include <vector>

#include "itch/spsc.hpp"
#include "support/spsc_ordering.hpp"

namespace {

using itch::testing::TestOrdering;

struct Item {
  std::uint64_t sequence = 0;
  std::array<std::uint64_t, 31> words{};

  void fill(std::uint64_t value) noexcept {
    sequence = value;
    for (std::size_t i = 0; i < words.size(); ++i) {
      words[i] = (value * 0x9E3779B97F4A7C15ULL) ^ i;
    }
  }
  [[nodiscard]] bool intact() const noexcept {
    for (std::size_t i = 0; i < words.size(); ++i) {
      if (words[i] != ((sequence * 0x9E3779B97F4A7C15ULL) ^ i)) {
        return false;
      }
    }
    return true;
  }
};
static_assert(sizeof(Item) == 256);

using Ring = itch::SpscRing<Item, itch::SpscConfig{}, TestOrdering>;

struct Options {
  std::uint64_t rounds = 100;
  std::uint64_t messages = 1'000'000;
  std::uint64_t capacity = 4;
  std::uint64_t noise = 0;
  std::uint64_t noise_mib = 64;
};

[[nodiscard]] bool parse(std::span<char*> args, Options& options) {
  if (args.size() % 2 != 0) {
    return false;
  }
  for (std::size_t i = 0; i < args.size(); i += 2) {
    const std::string_view flag = args[i];
    const std::string_view text = args[i + 1];
    std::uint64_t value = 0;
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size()) {
      return false;
    }
    if (flag == "--rounds") {
      options.rounds = value;
    } else if (flag == "--messages") {
      options.messages = value;
    } else if (flag == "--capacity") {
      options.capacity = value;
    } else if (flag == "--noise") {
      options.noise = value;
    } else if (flag == "--noise-mib") {
      options.noise_mib = value;
    } else {
      return false;
    }
  }
  return options.rounds != 0 && options.messages != 0 && options.capacity != 0;
}

struct Tally {
  std::uint64_t messages = 0;
  std::uint64_t wrong_sequence = 0;
  std::uint64_t torn = 0;
  std::uint64_t first_bad_round = 0;
  bool seen_bad = false;
};

// One round: the producer on a thread of its own, alternating single pushes with batches
// of three, and the consumer here.
void run_round(const Options& options, std::uint64_t index, Tally& tally) {
  const auto ring = std::make_unique<Ring>(options.capacity);
  std::thread producer{[&ring, &options] {
    std::array<Item, 3> batch{};
    std::uint64_t next = 0;
    while (next < options.messages) {
      if (next % 2 == 0) {
        Item item;
        item.fill(next);
        next += ring->try_push(item) ? 1 : 0;
        continue;
      }
      const auto want =
          static_cast<std::size_t>(std::min<std::uint64_t>(3, options.messages - next));
      for (std::size_t i = 0; i < want; ++i) {
        batch[i].fill(next + i);
      }
      next += ring->try_push_batch(std::span{batch}.first(want));
    }
  }};
  Item item;
  std::uint64_t wrong = 0;
  for (std::uint64_t place = 0; place < options.messages;) {
    if (!ring->try_pop(item)) {
      continue;
    }
    tally.wrong_sequence += item.sequence == place ? 0U : 1U;
    tally.torn += item.intact() ? 0U : 1U;
    wrong += item.sequence == place && item.intact() ? 0U : 1U;
    ++place;
  }
  producer.join();
  tally.messages += options.messages;
  if (wrong != 0 && !tally.seen_bad) {
    tally.seen_bad = true;
    tally.first_bad_round = index;
  }
}

int run(int argc, char** argv) {
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: spsc_stress [--rounds N] [--messages N] [--capacity N] [--noise N] "
                 "[--noise-mib N]");
    return 2;
  }
  std::atomic<bool> stop{false};
  std::vector<std::thread> noise;
  for (std::uint64_t n = 0; n < options.noise; ++n) {
    noise.emplace_back([&stop, &options] {
      std::vector<std::uint64_t> bytes(options.noise_mib * (std::size_t{1} << 20U) / 8);
      std::uint64_t pass = 0;
      while (!stop.load(std::memory_order_relaxed)) {
        for (std::size_t at = 0; at < bytes.size(); at += 8) {
          bytes[at] = pass;
        }
        ++pass;
      }
    });
  }
  std::println("spsc_stress: orders: {} ({})", itch::testing::ordering_label(),
               TestOrdering::kWeakened);
  const auto started = std::chrono::steady_clock::now();
  Tally tally;
  for (std::uint64_t index = 0; index < options.rounds; ++index) {
    run_round(options, index, tally);
  }
  const std::chrono::duration<double> took = std::chrono::steady_clock::now() - started;
  stop.store(true, std::memory_order_relaxed);
  for (std::thread& thread : noise) {
    thread.join();
  }
  std::println(
      "spsc_stress: {} rounds of {} items, capacity {}, {}-byte items, {} noise threads of {} "
      "MiB: {} items, {} in the wrong place, {} torn, {:.2f} s",
      options.rounds, options.messages, Ring{options.capacity}.capacity(), sizeof(Item),
      options.noise, options.noise_mib, tally.messages, tally.wrong_sequence, tally.torn,
      took.count());
  if (tally.seen_bad) {
    std::println("spsc_stress: FAIL, first in round {}", tally.first_bad_round);
    return 1;
  }
  std::println("spsc_stress: pass");
  return 0;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    return run(argc, argv);
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
