// The browser demo's replay of one symbol's day, built twice from this one header: by
// em++ into the page's engine (src/demo_wasm.cpp) and natively into bench_demo, so that
// T-WASM-1 times the same work on both sides. The book is FastBook<>, the arm that ships,
// at its default capacity.
//
// The input is an extract that tools/itch_extract cuts from a day: the frames of one
// symbol and every System Event, framed as in the day's file. The constructor walks it
// once, untimed, to count its frames and to find the symbol's locate, and refuses an
// extract that holds more than one symbol.
//
// replay() is the call both sides time: every remaining frame through FrameReader and
// FastBook::apply(), counting each status, and nothing else. A page cannot time one
// message, since performance.now() is clamped and WebAssembly has no clock, so the page
// times whole calls and divides. step() is the animation's: the same frames, a batch at
// a time, also reading each print as it passes. It is never timed against native.
#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>

#include "itch/book.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"

namespace itch::demo {

// One print, as the page draws it. side is the side of the order that traded, 'B' or
// 'S', from the book for an execution and from the message for a P; a cross has none.
struct Print {
  std::uint64_t timestamp_ns;
  std::uint64_t shares;
  Price4 price;
  char kind;  // 'E', 'C', 'P' or 'Q'.
  char side;  // 'B', 'S' or ' '.
};

static_assert(sizeof(Print) == 24);

// One level of a side, best first, as the page reads it out of linear memory.
struct DepthLevel {
  Price4 price;
  std::uint32_t orders;
  std::uint64_t shares;
};

static_assert(sizeof(DepthLevel) == 16);

// What the constructor's walk found.
enum class ExtractStatus : std::uint8_t {
  ok,
  no_symbol,     // No Stock Directory message.
  many_symbols,  // A second Stock Directory, or a frame on another locate.
  bad_framing,   // The walk ended on a truncated frame.
};

[[nodiscard]] constexpr std::string_view to_string(ExtractStatus status) noexcept {
  switch (status) {
    case ExtractStatus::ok: return "ok";
    case ExtractStatus::no_symbol: return "no_symbol";
    case ExtractStatus::many_symbols: return "many_symbols";
    case ExtractStatus::bad_framing: return "bad_framing";
  }
  return "unnamed";
}

inline constexpr std::size_t kStatusCount = 8;  // ApplyStatus's values.
static_assert(static_cast<std::size_t>(ApplyStatus::capacity_exhausted) + 1 == kStatusCount);
inline constexpr std::size_t kPrintRing = 4096;

class Replay {
 public:
  using Book = FastBook<>;

  explicit Replay(std::span<const std::byte> extract, const BookCapacity& capacity = {})
      : extract_{extract}, reader_{extract}, book_{capacity} {
    survey();
  }

  // Every frame not yet applied, statuses counted. The span both sides time.
  std::uint64_t replay() noexcept {
    std::uint64_t applied = 0;
    for (Frame frame = reader_.next(); !is_terminal(frame.status); frame = reader_.next()) {
      if (frame.status == FrameStatus::message) {
        ++statuses_[static_cast<std::size_t>(book_.apply(frame.payload))];
        ++applied;
      }
    }
    applied_ += applied;
    return applied;
  }

  // Up to `messages` more frames, each print kept in the ring. Returns how many.
  std::uint64_t step(std::uint64_t messages) noexcept {
    std::uint64_t applied = 0;
    while (applied < messages) {
      const Frame frame = reader_.next();
      if (is_terminal(frame.status)) {
        break;
      }
      if (frame.status != FrameStatus::message) {
        continue;
      }
      record_print(frame.payload);
      ++statuses_[static_cast<std::size_t>(book_.apply(frame.payload))];
      last_timestamp_ns_ = read_be48(frame.payload.data() + 5);
      ++applied;
    }
    applied_ += applied;
    return applied;
  }

  // The best `out.size()` levels of a side, best first. Returns how many were written.
  [[nodiscard]] std::size_t depth(Side side, std::span<DepthLevel> out) const noexcept {
    std::size_t written = 0;
    book_.walk_side(locate_, side, [&](const Book::Level& level) {
      if (written == out.size()) {
        return false;
      }
      out[written++] = {
          .price = level.price, .orders = level.order_count, .shares = level.aggregate_shares};
      return true;
    });
    return written;
  }

  // An FNV-1a hash of every status count and every level of both sides, so that two
  // builds that agree on it left the same book behind.
  [[nodiscard]] std::uint64_t digest() const noexcept {
    std::uint64_t hash = 0xcbf29ce484222325ULL;
    const auto mix = [&hash](std::uint64_t value) {
      for (unsigned byte = 0; byte < 8U; ++byte) {
        hash = (hash ^ ((value >> (8U * byte)) & 0xFFU)) * 0x100000001b3ULL;
      }
    };
    for (const std::uint64_t count : statuses_) {
      mix(count);
    }
    mix(book_.live_orders());
    for (const Side side : {Side::bid, Side::ask}) {
      book_.walk_side(locate_, side, [&](const Book::Level& level) {
        mix(level.price);
        mix(level.order_count);
        mix(level.aggregate_shares);
        return true;
      });
    }
    return hash;
  }

  [[nodiscard]] ExtractStatus extract_status() const noexcept { return extract_status_; }
  [[nodiscard]] std::uint64_t messages() const noexcept { return messages_; }
  [[nodiscard]] std::uint64_t applied() const noexcept { return applied_; }
  [[nodiscard]] bool done() const noexcept { return applied_ == messages_; }
  [[nodiscard]] std::uint16_t locate() const noexcept { return locate_; }
  [[nodiscard]] const Stock& symbol() const noexcept { return symbol_; }
  [[nodiscard]] std::uint64_t first_timestamp_ns() const noexcept { return first_timestamp_ns_; }
  [[nodiscard]] std::uint64_t last_timestamp_ns() const noexcept { return last_timestamp_ns_; }
  [[nodiscard]] std::uint64_t status_count(ApplyStatus status) const noexcept {
    return statuses_[static_cast<std::size_t>(status)];
  }
  [[nodiscard]] std::size_t bytes() const noexcept { return extract_.size(); }
  [[nodiscard]] const Book& book() const noexcept { return book_; }

  // Prints kept so far, and the n-th of them while it is still among the last kPrintRing.
  [[nodiscard]] std::uint64_t prints() const noexcept { return prints_; }
  [[nodiscard]] const Print& print(std::uint64_t n) const noexcept {
    return ring_[static_cast<std::size_t>(n % kPrintRing)];
  }

 private:
  void survey() noexcept {
    bool directory_seen = false;
    FrameReader walk{extract_};
    Frame frame = walk.next();
    for (; !is_terminal(frame.status); frame = walk.next()) {
      if (frame.status != FrameStatus::message) {
        continue;
      }
      ++messages_;
      const std::byte* m = frame.payload.data();
      const char type = read_char(m);
      const auto locate = read_be<std::uint16_t>(m + 1);
      if (messages_ == 1) {
        first_timestamp_ns_ = read_be48(m + 5);
      }
      if (type == StockDirectory::kType) {
        if (directory_seen) {
          extract_status_ = ExtractStatus::many_symbols;
        }
        directory_seen = true;
        locate_ = locate;
        symbol_ = StockDirectory::from_wire(m).stock;
      } else if (type != SystemEvent::kType && (!directory_seen || locate != locate_)) {
        extract_status_ = ExtractStatus::many_symbols;
      }
    }
    if (frame.status != FrameStatus::end_of_input) {
      extract_status_ = ExtractStatus::bad_framing;
    } else if (!directory_seen && extract_status_ == ExtractStatus::ok) {
      extract_status_ = ExtractStatus::no_symbol;
    }
    last_timestamp_ns_ = first_timestamp_ns_;
  }

  // Read before apply(), since an execution that fills an order takes it off the book.
  void record_print(std::span<const std::byte> payload) noexcept {
    const std::byte* m = payload.data();
    switch (read_char(m)) {
      case OrderExecuted::kType: {
        const OrderExecuted e = OrderExecuted::from_wire(m);
        const auto order = book_.resting_order(e.order_reference);
        if (order) {
          keep({.timestamp_ns = e.header.timestamp_ns,
                .shares = e.executed_shares,
                .price = order->price,
                .kind = 'E',
                .side = side_char(order->side)});
        }
        break;
      }
      case OrderExecutedWithPrice::kType: {
        const OrderExecutedWithPrice c = OrderExecutedWithPrice::from_wire(m);
        const auto order = book_.resting_order(c.order_reference);
        if (order && c.printable == 'Y') {
          keep({.timestamp_ns = c.header.timestamp_ns,
                .shares = c.executed_shares,
                .price = c.execution_price,
                .kind = 'C',
                .side = side_char(order->side)});
        }
        break;
      }
      case Trade::kType: {
        const Trade p = Trade::from_wire(m);
        keep({.timestamp_ns = p.header.timestamp_ns,
              .shares = p.shares,
              .price = p.price,
              .kind = 'P',
              .side = p.side == 'S' ? 'S' : 'B'});
        break;
      }
      case CrossTrade::kType: {
        const CrossTrade q = CrossTrade::from_wire(m);
        if (q.shares != 0) {
          keep({.timestamp_ns = q.header.timestamp_ns,
                .shares = q.shares,
                .price = q.cross_price,
                .kind = 'Q',
                .side = ' '});
        }
        break;
      }
      default: break;
    }
  }

  [[nodiscard]] static char side_char(Side side) noexcept { return side == Side::bid ? 'B' : 'S'; }

  void keep(const Print& print) noexcept {
    ring_[static_cast<std::size_t>(prints_++ % kPrintRing)] = print;
  }

  std::span<const std::byte> extract_;
  FrameReader reader_;
  Book book_;
  std::array<std::uint64_t, kStatusCount> statuses_{};
  std::array<Print, kPrintRing> ring_{};
  std::uint64_t messages_ = 0;
  std::uint64_t applied_ = 0;
  std::uint64_t prints_ = 0;
  std::uint64_t first_timestamp_ns_ = 0;
  std::uint64_t last_timestamp_ns_ = 0;
  std::uint16_t locate_ = 0;
  Stock symbol_{};
  ExtractStatus extract_status_ = ExtractStatus::ok;
};

}  // namespace itch::demo
