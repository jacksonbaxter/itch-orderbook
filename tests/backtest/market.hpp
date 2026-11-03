// A synthetic market for the backtester's tests: a seeded random walk of one symbol's
// book, written as ITCH messages, with every execution at the front of the best level as
// a FIFO market's would be. Nothing here comes from a Nasdaq file.
//
// The book is never crossed: bids rest at or below mid less a tick, asks at or above mid
// plus a tick, and a move of mid deletes the orders it would cross first.
#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <map>
#include <random>
#include <span>
#include <vector>

#include "itch/backtest.hpp"
#include "itch/messages.hpp"
#include "oracle/wire_builder.hpp"
#include "support/fixtures.hpp"

namespace itch::backtest::synthetic {

namespace wire = itch::oracle::wire;

inline constexpr std::uint16_t kLocate = 1;
inline constexpr Price4 kTick = 100;
// The symbol the directory message names, which the configs trade.
inline const Stock kSymbol = wire::stock('R');

// One message and its timestamp, kept apart so that a test can cut or change the feed.
struct Message {
  Nanos time;
  wire::Bytes payload;
};

[[nodiscard]] inline std::vector<std::byte> frame(std::span<const Message> messages) {
  std::vector<std::byte> stream;
  for (const Message& message : messages) {
    itch::fixtures::append_frame(stream, message.payload);
  }
  return stream;
}

[[nodiscard]] inline Nanos time_of(const wire::Bytes& payload) {
  return read_be48(payload.data() + 5);
}

class RandomMarket {
 public:
  explicit RandomMarket(std::uint32_t seed, Price4 mid = 100'000) : random_{seed}, mid_{mid} {}

  // The directory, trading state and start of market hours, then `steps` random events.
  [[nodiscard]] std::vector<Message> generate(int steps) {
    std::vector<Message> out;
    put(out, wire::directory(kLocate, 'Q', 'P', 100, time_));
    put(out, wire::trading_action(kLocate, 'T', time_));
    put(out, wire::system_event('Q', time_));
    for (int step = 0; step < steps; ++step) {
      time_ += 1 + (random_() % 2'000);
      this->step(out);
    }
    return out;
  }

 private:
  struct Live {
    char side;
    Price4 price;
    std::uint32_t shares;
  };
  using Fifo = std::deque<std::uint64_t>;

  static void put(std::vector<Message>& out, wire::Bytes payload) {
    out.push_back({.time = time_of(payload), .payload = std::move(payload)});
  }

  void step(std::vector<Message>& out) {
    const std::uint32_t pick = random_() % 100;
    if (pick < 40 || live_.size() < 8) {
      const char side = random_() % 2 == 0 ? 'B' : 'S';
      const auto ticks = static_cast<Price4>(1 + (random_() % 4));
      const Price4 price = side == 'B' ? mid_ - (ticks * kTick) : mid_ + (ticks * kTick);
      add(out, side, price, 100 * (1 + (random_() % 5)));
    } else if (pick < 60) {
      execute_front(out, random_() % 2 == 0 ? 'B' : 'S');
    } else if (pick < 72) {
      const std::uint64_t reference = any_live();
      Live& order = live_.at(reference);
      if (order.shares > 100) {
        put(out, wire::cancel(kLocate, reference, 100, time_));
        order.shares -= 100;
      }
    } else if (pick < 84) {
      remove(out, any_live());
    } else if (pick < 92) {
      const std::uint64_t reference = any_live();
      const Live order = live_.at(reference);
      const auto ticks = static_cast<Price4>(1 + (random_() % 4));
      const Price4 price = order.side == 'B' ? mid_ - (ticks * kTick) : mid_ + (ticks * kTick);
      const std::uint64_t fresh = next_reference_++;
      put(out, wire::replace(kLocate, reference, fresh, order.shares, price, time_));
      forget(reference);
      remember(fresh, order.side, price, order.shares);
    } else {
      move_mid(out, random_() % 2 == 0 ? 1 : -1);
    }
  }

  void add(std::vector<Message>& out, char side, Price4 price, std::uint32_t shares) {
    const std::uint64_t reference = next_reference_++;
    put(out, wire::add(kLocate, reference, side, shares, price, time_));
    remember(reference, side, price, shares);
  }

  void execute_front(std::vector<Message>& out, char side) {
    auto& levels = side == 'B' ? bids_ : asks_;
    if (levels.empty()) {
      return;
    }
    const auto best = side == 'B' ? std::prev(levels.end()) : levels.begin();
    const std::uint64_t reference = best->second.front();
    Live& order = live_.at(reference);
    const std::uint32_t shares = 1 + (random_() % order.shares);
    put(out, wire::execute(kLocate, reference, shares, time_));
    order.shares -= shares;
    if (order.shares == 0) {
      forget(reference);
    }
  }

  void remove(std::vector<Message>& out, std::uint64_t reference) {
    put(out, wire::erase(kLocate, reference, time_));
    forget(reference);
  }

  void move_mid(std::vector<Message>& out, int direction) {
    mid_ = direction > 0 ? mid_ + kTick : mid_ - kTick;
    // Whatever now sits within a tick of mid on the wrong side goes.
    std::vector<std::uint64_t> crossing;
    for (const auto& [reference, order] : live_) {
      if ((order.side == 'B' && order.price > mid_ - kTick) ||
          (order.side == 'S' && order.price < mid_ + kTick)) {
        crossing.push_back(reference);
      }
    }
    for (const std::uint64_t reference : crossing) {
      remove(out, reference);
    }
  }

  [[nodiscard]] std::uint64_t any_live() {
    auto at = live_.begin();
    std::advance(at, static_cast<std::ptrdiff_t>(random_() % live_.size()));
    return at->first;
  }

  void remember(std::uint64_t reference, char side, Price4 price, std::uint32_t shares) {
    live_[reference] = {.side = side, .price = price, .shares = shares};
    (side == 'B' ? bids_ : asks_)[price].push_back(reference);
  }

  void forget(std::uint64_t reference) {
    const Live order = live_.at(reference);
    auto& levels = order.side == 'B' ? bids_ : asks_;
    Fifo& fifo = levels.at(order.price);
    std::erase(fifo, reference);
    if (fifo.empty()) {
      levels.erase(order.price);
    }
    live_.erase(reference);
  }

  std::mt19937 random_;
  Price4 mid_;
  Nanos time_ = 1'000'000;
  std::uint64_t next_reference_ = 1;
  std::map<std::uint64_t, Live> live_;
  std::map<Price4, Fifo> bids_;
  std::map<Price4, Fifo> asks_;
};

}  // namespace itch::backtest::synthetic
