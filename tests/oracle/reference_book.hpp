// The reference book: the slow, obvious statement of what a book is.
//
// It is deliberately independent of include/itch/book.hpp and of the parser. It reads
// the order messages one byte at a time at offsets written out here, so that a misread
// offset cannot hit both sides. It keeps no totals: shares and order counts are summed
// from the FIFO whenever someone asks. It holds plain standard containers, a map of
// (locate, side) to an ordered map of price to FIFO, plus a hash map of reference to
// order.
//
// apply() also says which levels the message can have changed. That is the touched set
// T(m) of the proof in NOTES.md, worked out from this book's own state.
#pragma once

#include <array>
#include <compare>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <list>
#include <map>
#include <span>
#include <string_view>
#include <unordered_map>
#include <utility>

namespace itch::oracle {

enum class RefStatus : std::uint8_t {
  applied,
  no_book_effect,
  unknown_order,
  locate_mismatch,
  over_deduction,
  duplicate_reference,
  bad_side,
};

[[nodiscard]] constexpr std::string_view to_string(RefStatus status) noexcept {
  switch (status) {
    case RefStatus::applied: return "applied";
    case RefStatus::no_book_effect: return "no_book_effect";
    case RefStatus::unknown_order: return "unknown_order";
    case RefStatus::locate_mismatch: return "locate_mismatch";
    case RefStatus::over_deduction: return "over_deduction";
    case RefStatus::duplicate_reference: return "duplicate_reference";
    case RefStatus::bad_side: return "bad_side";
  }
  return "unnamed";
}

// One price level, by identity. side is the wire byte, 'B' or 'S'.
struct LevelId {
  std::uint16_t locate = 0;
  char side = 'B';
  std::uint32_t price = 0;

  friend bool operator==(const LevelId&, const LevelId&) = default;
};

// The levels one message can change: at most two, for a replace that moves price.
struct TouchedSet {
  std::array<LevelId, 2> levels{};
  std::size_t count = 0;

  void add(const LevelId& id) noexcept {
    if (count == 0 || levels[0] != id) {
      levels[count] = id;
      ++count;
    }
  }
  [[nodiscard]] bool contains(const LevelId& id) const noexcept {
    for (std::size_t i = 0; i < count; ++i) {
      if (levels[i] == id) {
        return true;
      }
    }
    return false;
  }
};

struct RefOutcome {
  RefStatus status = RefStatus::no_book_effect;
  std::uint16_t locate = 0;
  std::uint64_t reference = 0;  // The order the message named, for a report.
  TouchedSet touched;
};

struct RefTop {
  bool present = false;
  std::uint32_t price = 0;
  std::uint64_t shares = 0;
  std::uint32_t orders = 0;
};

class ReferenceBook {
 public:
  struct Resting {
    std::uint64_t reference;
    std::uint32_t shares;
  };
  using Fifo = std::list<Resting>;
  using PriceLevels = std::map<std::uint32_t, Fifo>;
  using SideKey = std::pair<std::uint16_t, char>;

  // One whole payload. The frame reader has checked its length against the parser's
  // table, and this checks it again against its own.
  RefOutcome apply(std::span<const std::byte> payload) {
    RefOutcome outcome;
    if (payload.empty()) {
      return outcome;
    }
    outcome.locate = static_cast<std::uint16_t>(field(payload, 1, 2));
    const char type = static_cast<char>(field(payload, 0, 1));
    if (payload.size() != order_message_length(type)) {
      return outcome;
    }
    switch (type) {
      case 'A':
      case 'F': add(payload, outcome); break;
      case 'E':
      case 'C':
      case 'X': reduce(payload, outcome); break;
      case 'D': remove(payload, outcome); break;
      case 'U': replace(payload, outcome); break;
      default: break;
    }
    return outcome;
  }

  // The FIFO at a level, or nullptr if the level does not exist.
  [[nodiscard]] const Fifo* level(const LevelId& id) const {
    const auto side = sides_.find({id.locate, id.side});
    if (side == sides_.end()) {
      return nullptr;
    }
    const auto at = side->second.find(id.price);
    return at == side->second.end() ? nullptr : &at->second;
  }

  // Summed from the FIFO every time.
  [[nodiscard]] RefTop best(std::uint16_t locate, char side) const {
    const auto found = sides_.find({locate, side});
    if (found == sides_.end()) {
      return {};
    }
    const PriceLevels& levels = found->second;
    const auto& [price, fifo] = side == 'B' ? *levels.rbegin() : *levels.begin();
    RefTop top{.present = true, .price = price, .shares = 0, .orders = 0};
    for (const Resting& order : fifo) {
      top.shares += order.shares;
      ++top.orders;
    }
    return top;
  }

  // Every non-empty side, in (locate, side) order.
  [[nodiscard]] const std::map<SideKey, PriceLevels>& sides() const noexcept { return sides_; }
  [[nodiscard]] std::size_t live_orders() const noexcept { return orders_.size(); }

 private:
  struct Located {
    std::uint16_t locate;
    char side;
    std::uint32_t price;
    Fifo::iterator position;
  };

  [[nodiscard]] static std::uint64_t field(std::span<const std::byte> bytes, std::size_t offset,
                                           std::size_t width) {
    std::uint64_t value = 0;
    for (std::size_t i = 0; i < width; ++i) {
      value = (value << 8U) | std::to_integer<std::uint64_t>(bytes[offset + i]);
    }
    return value;
  }

  [[nodiscard]] static std::size_t order_message_length(char type) noexcept {
    switch (type) {
      case 'A': return 36;
      case 'F': return 40;
      case 'E': return 31;
      case 'C': return 36;
      case 'X': return 23;
      case 'D': return 19;
      case 'U': return 35;
      default: return 0;
    }
  }

  void add(std::span<const std::byte> m, RefOutcome& outcome) {
    const std::uint64_t reference = field(m, 11, 8);
    const auto side = static_cast<char>(field(m, 19, 1));
    const auto shares = static_cast<std::uint32_t>(field(m, 20, 4));
    const auto price = static_cast<std::uint32_t>(field(m, 32, 4));
    outcome.reference = reference;
    if (side != 'B' && side != 'S') {
      outcome.status = RefStatus::bad_side;
      return;
    }
    if (orders_.contains(reference)) {
      outcome.status = RefStatus::duplicate_reference;
      return;
    }
    rest(outcome.locate, side, price, reference, shares);
    outcome.touched.add({.locate = outcome.locate, .side = side, .price = price});
    outcome.status = RefStatus::applied;
  }

  void reduce(std::span<const std::byte> m, RefOutcome& outcome) {
    outcome.reference = field(m, 11, 8);
    const auto shares = static_cast<std::uint32_t>(field(m, 19, 4));
    const auto found = find_live(outcome);
    if (found == orders_.end()) {
      return;
    }
    if (shares > found->second.position->shares) {
      outcome.status = RefStatus::over_deduction;
      return;
    }
    outcome.touched.add(id_of(found->second));
    found->second.position->shares -= shares;
    if (found->second.position->shares == 0) {
      unlink(found);
    }
    outcome.status = RefStatus::applied;
  }

  void remove(std::span<const std::byte> m, RefOutcome& outcome) {
    outcome.reference = field(m, 11, 8);
    const auto found = find_live(outcome);
    if (found == orders_.end()) {
      return;
    }
    outcome.touched.add(id_of(found->second));
    unlink(found);
    outcome.status = RefStatus::applied;
  }

  void replace(std::span<const std::byte> m, RefOutcome& outcome) {
    outcome.reference = field(m, 11, 8);
    const std::uint64_t new_reference = field(m, 19, 8);
    const auto shares = static_cast<std::uint32_t>(field(m, 27, 4));
    const auto price = static_cast<std::uint32_t>(field(m, 31, 4));
    const auto found = find_live(outcome);
    if (found == orders_.end()) {
      return;
    }
    if (orders_.contains(new_reference)) {
      outcome.status = RefStatus::duplicate_reference;
      return;
    }
    const Located old = found->second;
    outcome.touched.add(id_of(old));
    outcome.touched.add({.locate = old.locate, .side = old.side, .price = price});
    unlink(found);
    rest(old.locate, old.side, price, new_reference, shares);
    outcome.status = RefStatus::applied;
  }

  // The order the message names, or end() with the reason in the outcome.
  [[nodiscard]] std::unordered_map<std::uint64_t, Located>::iterator find_live(
      RefOutcome& outcome) {
    const auto found = orders_.find(outcome.reference);
    if (found == orders_.end()) {
      outcome.status = RefStatus::unknown_order;
      return found;
    }
    if (found->second.locate != outcome.locate) {
      outcome.status = RefStatus::locate_mismatch;
      return orders_.end();
    }
    return found;
  }

  void rest(std::uint16_t locate, char side, std::uint32_t price, std::uint64_t reference,
            std::uint32_t shares) {
    Fifo& fifo = sides_[{locate, side}][price];
    fifo.push_back({.reference = reference, .shares = shares});
    orders_.emplace(
        reference,
        Located{.locate = locate, .side = side, .price = price, .position = std::prev(fifo.end())});
  }

  void unlink(std::unordered_map<std::uint64_t, Located>::iterator order) {
    const Located& where = order->second;
    const auto side = sides_.find({where.locate, where.side});
    const auto level = side->second.find(where.price);
    level->second.erase(where.position);
    if (level->second.empty()) {
      side->second.erase(level);
      if (side->second.empty()) {
        sides_.erase(side);
      }
    }
    orders_.erase(order);
  }

  [[nodiscard]] static LevelId id_of(const Located& order) noexcept {
    return {.locate = order.locate, .side = order.side, .price = order.price};
  }

  std::map<SideKey, PriceLevels> sides_;
  std::unordered_map<std::uint64_t, Located> orders_;
};

}  // namespace itch::oracle
