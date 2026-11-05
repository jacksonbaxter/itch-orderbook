// The mark path: one symbol's mid in the replayed historical book, as the attribution reads it
// (fills.hpp, MarkPath).
//
// The recorder is given the exchange-side book after each real message. It excludes the
// simulated orders, which never enter that book, so every run of a switch matrix sees the same
// path: the zero-impact assumption of the backtester (backtest.hpp, "The limit"). It must never
// be given the strategy's view book, which runs L_md late and so moves with the latency switch.
//
// The mark is (best bid + best ask)/2, held as their sum. While the book is one-sided or
// crossed, or the symbol is not in state 'T', the mark holds its last valid value and the time
// spent so is counted. A locked book has a valid mid. Changes inside one nanosecond collapse
// into one point, the state after the last of them.
//
// Nothing here allocates after the constructor: the path's capacity is fixed, and a path that
// fills counts an overflow, after which the run is no longer valid.
#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

#include "itch/backtest.hpp"
#include "itch/book_types.hpp"
#include "itch/messages.hpp"
#include "itch/mm/fills.hpp"
#include "itch/wire.hpp"

namespace itch::mm {

class MarkRecorder {
 public:
  MarkRecorder(const Stock& symbol, std::size_t capacity) : symbol_{symbol}, points_(capacity) {}

  // After the book has applied `payload`.
  template <class Book>
  void observe(const Book& book, std::span<const std::byte> payload) noexcept {
    if (payload.size() < 11) {
      return;
    }
    const std::byte* m = payload.data();
    const char type = read_char(m);
    const auto locate = read_be<std::uint16_t>(m + 1);
    Nanos time = read_be48(m + 5);
    time = time < clock_ ? clock_ : time;
    clock_ = time;
    if (type == StockDirectory::kType && payload.size() == message_length(type) &&
        StockDirectory::from_wire(m).stock == symbol_) {
      locate_ = locate;
      known_ = true;
    }
    if (!known_ || locate != locate_) {
      return;
    }
    if (type == StockTradingAction::kType && payload.size() == message_length(type)) {
      trading_ = StockTradingAction::from_wire(m).trading_state;
    }
    const TopOfBook bid = book.best(locate_, Side::bid);
    const TopOfBook ask = book.best(locate_, Side::ask);
    const bool valid = trading_ == 'T' && bid.present && ask.present && bid.price <= ask.price;
    if (!valid) {
      if (!holding_) {
        holding_ = true;
        held_since_ = time;
      }
      return;
    }
    if (holding_) {
      held_nanos_ += time - held_since_;
      holding_ = false;
    }
    record(time, Mid2{bid.price} + Mid2{ask.price});
  }

  [[nodiscard]] std::span<const MarkPoint> points() const noexcept {
    return std::span<const MarkPoint>{points_}.first(count_);
  }
  [[nodiscard]] MarkPath path() const noexcept { return MarkPath{points()}; }
  [[nodiscard]] std::uint64_t overflows() const noexcept { return overflows_; }
  // Time with no valid mark, between the first message that lost it and the first that
  // brought it back. A spell still open at the end is not counted.
  [[nodiscard]] Nanos held_nanos() const noexcept { return held_nanos_; }

  // An order-sensitive hash of every point: equal across the runs of a switch matrix.
  [[nodiscard]] std::uint64_t hash() const noexcept {
    backtest::EventHash hash;
    for (const MarkPoint& point : points()) {
      hash(point.time);
      hash(static_cast<std::uint64_t>(point.mid2));
    }
    return hash.digest();
  }

 private:
  void record(Nanos time, Mid2 mid2) noexcept {
    MarkPoint* const last = count_ == 0 ? nullptr : &points_[count_ - 1];
    if (last != nullptr && last->mid2 == mid2) {
      return;
    }
    if (last != nullptr && last->time == time) {
      last->mid2 = mid2;
      return;
    }
    if (count_ == points_.size()) {
      ++overflows_;
      return;
    }
    points_[count_] = {.time = time, .mid2 = mid2};
    ++count_;
  }

  Stock symbol_;
  std::uint16_t locate_ = 0;
  bool known_ = false;
  char trading_ = ' ';
  bool holding_ = false;
  Nanos held_since_ = 0;
  Nanos held_nanos_ = 0;
  Nanos clock_ = 0;
  std::vector<MarkPoint> points_;  // Sized once; the first count_ are the path.
  std::size_t count_ = 0;
  std::uint64_t overflows_ = 0;
};

}  // namespace itch::mm
