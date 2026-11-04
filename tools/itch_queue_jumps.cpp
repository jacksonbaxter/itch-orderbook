// The executions that did not hit the front of their level's queue, and what the feed says
// about them. T-TRADE-5 reports these as the remainder of the queue-front rate, and the
// backtester met the same events as queue anomalies. NOTES.md, "Queue jumps".
//
//     itch_queue_jumps FILE [--details FILE] [--strict]
//
// The population is T-TRADE-5's: every `E` while the market is open (System Event `Q` to
// `M`) on a symbol in state `T`, read before the book applies it. A jump is such an `E` on an
// order with an older order ahead of it in the book's FIFO, which is the order of the adds.
// Each `E` is also checked against the lowest order reference resting at its level: the
// spec assigns the reference "to the new order at the time of receipt" (p. 12).
//
// Each printable `C` in the population is compared with the price its order showed.
//
// A late add is an `A` or `F` whose reference is below the highest one its symbol has shown:
// an order the exchange received before an order the feed showed first. A late replace is
// the same for the new reference of a `U`.
//
// --details  one tab-separated line per jump, for the raw log.
// --strict   exit 1 unless every `E` in the population hit the lowest reference at its level,
//            every jump's order has a lower reference than every order ahead of it, no late
//            add came before the open, no replace came late, and every printable `C` in the
//            population traded at a price better for the aggressor than its order showed.
//
// Exit codes: 0 the day replayed (and, with --strict, held), 1 the book refused a message or
// --strict failed, 2 bad usage or an unreadable file.
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <functional>
#include <map>
#include <memory>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

#include "itch/book.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "mapped_file.hpp"

namespace {

using itch::Price4;
using itch::Side;

constexpr std::size_t kLocates = 65536;
constexpr std::uint32_t kRoundLot = 100;

// What became of the order at the front when an order behind it executed: the first later
// message that names it.
enum class Fate : std::uint8_t { pending, executed_same_ns, executed_later, removed_first };

struct Jump {
  std::uint64_t time;
  std::uint16_t locate;
  Side side;
  Price4 price;
  std::uint64_t reference;
  std::uint32_t shares;
  std::uint64_t added;  // When the feed showed the executed order.
  bool late;            // Its add was a late add.
  std::uint32_t position;
  std::uint64_t ahead_shares;
  std::uint32_t ahead_round_lots;
  std::uint64_t ahead_lowest_reference;
  std::uint64_t front_reference;
  std::uint64_t front_added;
  Fate fate;
};

struct Counts {
  std::uint64_t e_messages = 0;
  std::uint64_t population = 0;
  std::uint64_t at_fifo_front = 0;
  std::uint64_t at_lowest_reference = 0;
  std::uint64_t outside_population = 0;
  std::uint64_t outside_at_lowest_reference = 0;
  std::uint64_t adds = 0;
  std::uint64_t replaces = 0;
  std::uint64_t late_adds = 0;
  std::uint64_t late_adds_before_open = 0;
  std::uint64_t late_replaces = 0;
  std::uint64_t first_late_add = 0;
  std::uint64_t last_late_add = 0;
};

class QueueJumps {
 public:
  QueueJumps()
      : book_(std::make_unique<itch::FastBook<>>()),
        added_(book_->order_capacity(), 0),
        late_(book_->order_capacity(), 0) {
    states_.fill('T');
  }

  [[nodiscard]] bool replay(std::span<const std::byte> bytes) {
    itch::FrameReader reader{bytes};
    for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status);
         frame = reader.next()) {
      if (frame.status == itch::FrameStatus::message && !step(frame.payload)) {
        return false;
      }
    }
    return true;
  }

  // Prints every count, and returns whether the --strict conditions hold.
  [[nodiscard]] bool print(std::FILE* details) const {
    std::uint64_t below_every_ahead = 0;
    std::uint64_t late = 0;
    std::uint64_t front_before_open = 0;
    std::uint64_t odd_lots_ahead = 0;
    std::array<std::uint64_t, 4> fates{};
    std::map<std::string, std::uint64_t> symbols;
    for (const Jump& jump : jumps_) {
      below_every_ahead += jump.reference < jump.ahead_lowest_reference ? 1U : 0U;
      late += jump.late ? 1U : 0U;
      front_before_open += jump.front_added < open_time_ ? 1U : 0U;
      odd_lots_ahead += jump.ahead_round_lots == 0 ? 1U : 0U;
      ++fates.at(static_cast<std::size_t>(jump.fate));
      ++symbols[symbol(jump.locate)];
    }
    const Counts& c = counts_;
    std::println("e_messages {}", c.e_messages);
    std::println("population {}", c.population);
    std::println("at_fifo_front {}", c.at_fifo_front);
    std::println("jumps {}", jumps_.size());
    std::println("at_lowest_reference {}", c.at_lowest_reference);
    std::println("population_not_at_lowest_reference {}", c.population - c.at_lowest_reference);
    std::println("outside_population {}", c.outside_population);
    std::println("outside_at_lowest_reference {}", c.outside_at_lowest_reference);
    std::println("jumps_reference_below_every_order_ahead {}", below_every_ahead);
    std::println("jumps_order_added_late {}", late);
    std::println("jumps_front_added_before_open {}", front_before_open);
    std::println("jumps_only_odd_lots_ahead {}", odd_lots_ahead);
    std::println("jumps_front_executed_same_ns {}",
                 fates[static_cast<std::size_t>(Fate::executed_same_ns)]);
    std::println("jumps_front_executed_later {}",
                 fates[static_cast<std::size_t>(Fate::executed_later)]);
    std::println("jumps_front_removed_first {}",
                 fates[static_cast<std::size_t>(Fate::removed_first)]);
    std::println("jumps_front_never_named {}", fates[static_cast<std::size_t>(Fate::pending)]);
    std::println("jump_symbols {}", symbols.size());
    std::vector<std::pair<std::uint64_t, std::string>> ranked;
    ranked.reserve(symbols.size());
    for (const auto& [name, count] : symbols) {
      ranked.emplace_back(count, name);
    }
    std::ranges::sort(ranked, std::greater{});
    for (std::size_t i = 0; i < std::min<std::size_t>(ranked.size(), 10); ++i) {
      std::println("jump_symbol {} {}", ranked[i].second, ranked[i].first);
    }
    std::println("adds {}", c.adds);
    std::println("replaces {}", c.replaces);
    std::println("late_adds {}", c.late_adds);
    std::println("late_adds_before_open {}", c.late_adds_before_open);
    std::println("late_add_symbols {}", std::ranges::count(late_by_locate_, true));
    std::println("first_late_add {}", c.first_late_add);
    std::println("last_late_add {}", c.last_late_add);
    std::println("late_replaces {}", c.late_replaces);
    std::println("c_printable {}", c_printable_[0] + c_printable_[1] + c_printable_[2] +
                                       c_printable_[3] + c_printable_[4] + c_printable_[5]);
    std::println("c_printable_at_lowest_reference {}", c_printable_lowest_);
    std::println("c_printable_aggressor_improved front {} behind {}", c_printable_[0],
                 c_printable_[1]);
    std::println("c_printable_at_shown_price front {} behind {}", c_printable_[2], c_printable_[3]);
    std::println("c_printable_resting_improved front {} behind {}", c_printable_[4],
                 c_printable_[5]);
    if (details != nullptr) {
      write_details(details);
    }
    const bool c_improved =
        c_printable_[2] + c_printable_[3] + c_printable_[4] + c_printable_[5] == 0;
    return c.population == c.at_lowest_reference && below_every_ahead == jumps_.size() &&
           c.late_adds_before_open == 0 && c.late_replaces == 0 && c_improved;
  }

 private:
  [[nodiscard]] std::string symbol(std::uint16_t locate) const {
    std::string text{stocks_[locate].begin(), stocks_[locate].end()};
    while (!text.empty() && text.back() == ' ') {
      text.pop_back();
    }
    return text;
  }

  void write_details(std::FILE* out) const {
    std::println(out,
                 "time\tsymbol\tside\tprice\treference\tshares\tadded\tlate\tposition\t"
                 "ahead_shares\tahead_round_lots\tahead_lowest_reference\tfront_reference\t"
                 "front_added\tfate");
    for (const Jump& jump : jumps_) {
      std::println(out, "{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}", jump.time,
                   symbol(jump.locate), jump.side == Side::bid ? 'B' : 'S', jump.price,
                   jump.reference, jump.shares, jump.added, jump.late ? 1 : 0, jump.position,
                   jump.ahead_shares, jump.ahead_round_lots, jump.ahead_lowest_reference,
                   jump.front_reference, jump.front_added, static_cast<int>(jump.fate));
    }
  }

  [[nodiscard]] bool step(std::span<const std::byte> payload) {
    const std::byte* m = payload.data();
    const char type = itch::read_char(m);
    const itch::Header header = itch::read_header(m);
    settle_fronts(type, m, header.timestamp_ns);
    switch (type) {
      case itch::SystemEvent::kType: {
        const char code = itch::SystemEvent::from_wire(m).event_code;
        if (code == 'Q') {
          open_ = true;
          open_time_ = header.timestamp_ns;
        } else if (code == 'M') {
          open_ = false;
        }
        break;
      }
      case itch::StockDirectory::kType:
        stocks_[header.stock_locate] = itch::StockDirectory::from_wire(m).stock;
        break;
      case itch::StockTradingAction::kType:
        states_[header.stock_locate] = itch::StockTradingAction::from_wire(m).trading_state;
        break;
      case itch::OrderExecuted::kType: execution(itch::OrderExecuted::from_wire(m)); break;
      case itch::OrderExecutedWithPrice::kType:
        execution_with_price(itch::OrderExecutedWithPrice::from_wire(m));
        break;
      default: break;
    }
    const itch::ApplyStatus status = book_->apply(payload);
    if (status != itch::ApplyStatus::applied && status != itch::ApplyStatus::no_book_effect) {
      std::println(stderr, "itch_queue_jumps: the book refused a {} message: {}", type,
                   itch::to_string(status));
      return false;
    }
    switch (type) {
      case itch::AddOrder::kType:
        added(itch::AddOrder::from_wire(m).order_reference, header, false);
        break;
      case itch::AddOrderAttributed::kType:
        added(itch::AddOrderAttributed::from_wire(m).order_reference, header, false);
        break;
      case itch::OrderReplace::kType:
        added(itch::OrderReplace::from_wire(m).new_order_reference, header, true);
        break;
      default: break;
    }
    return true;
  }

  // A new reference on the book: when it was shown, and whether it came late.
  void added(std::uint64_t reference, const itch::Header& header, bool replace) {
    const std::uint16_t locate = header.stock_locate;
    const bool late = reference < highest_[locate];
    highest_[locate] = std::max(highest_[locate], reference);
    const std::uint32_t slot = book_->order_slot(reference);
    added_[slot] = header.timestamp_ns;
    late_[slot] = late ? 1U : 0U;
    ++(replace ? counts_.replaces : counts_.adds);
    if (!late) {
      return;
    }
    if (replace) {
      ++counts_.late_replaces;
      return;
    }
    ++counts_.late_adds;
    counts_.late_adds_before_open += open_time_ == 0 ? 1U : 0U;
    late_by_locate_[locate] = true;
    if (counts_.first_late_add == 0) {
      counts_.first_late_add = header.timestamp_ns;
    }
    counts_.last_late_add = header.timestamp_ns;
  }

  // Before the book applies an E: where in its level's queue it fell.
  void execution(const itch::OrderExecuted& e) {
    const std::uint32_t slot = book_->order_slot(e.order_reference);
    if (slot == itch::kNoIndex) {
      return;
    }
    ++counts_.e_messages;
    const std::uint16_t locate = e.header.stock_locate;
    const itch::BookOrder& order = book_->order_at(slot);
    const auto& level = book_->level_at(order.level);
    std::uint64_t lowest = e.order_reference;
    for (std::uint32_t at = level.front; at != itch::kNoIndex; at = book_->order_at(at).newer) {
      lowest = std::min(lowest, book_->order_at(at).reference);
    }
    const bool at_lowest = lowest == e.order_reference;
    if (!open_ || states_[locate] != 'T') {
      ++counts_.outside_population;
      counts_.outside_at_lowest_reference += at_lowest ? 1U : 0U;
      return;
    }
    ++counts_.population;
    counts_.at_lowest_reference += at_lowest ? 1U : 0U;
    if (order.older == itch::kNoIndex) {
      ++counts_.at_fifo_front;
      return;
    }
    Jump jump{.time = e.header.timestamp_ns,
              .locate = locate,
              .side = level.side,
              .price = level.price,
              .reference = e.order_reference,
              .shares = e.executed_shares,
              .added = added_[slot],
              .late = late_[slot] != 0,
              .position = 0,
              .ahead_shares = 0,
              .ahead_round_lots = 0,
              .ahead_lowest_reference = ~std::uint64_t{0},
              .front_reference = book_->order_at(level.front).reference,
              .front_added = added_[level.front],
              .fate = Fate::pending};
    for (std::uint32_t at = level.front; at != slot; at = book_->order_at(at).newer) {
      const itch::BookOrder& ahead = book_->order_at(at);
      ++jump.position;
      jump.ahead_shares += ahead.shares;
      jump.ahead_round_lots += ahead.shares >= kRoundLot ? 1U : 0U;
      jump.ahead_lowest_reference = std::min(jump.ahead_lowest_reference, ahead.reference);
    }
    fronts_[jump.front_reference].push_back(jumps_.size());
    jumps_.push_back(jump);
  }

  // Before the book applies a printable C in the population: where its order stood, and the
  // price it traded at against the price it was shown at.
  void execution_with_price(const itch::OrderExecutedWithPrice& c) {
    const std::uint32_t slot = book_->order_slot(c.order_reference);
    const std::uint16_t locate = c.header.stock_locate;
    if (slot == itch::kNoIndex || c.printable != 'Y' || !open_ || states_[locate] != 'T') {
      return;
    }
    const itch::BookOrder& order = book_->order_at(slot);
    const auto& level = book_->level_at(order.level);
    std::uint64_t lowest = c.order_reference;
    for (std::uint32_t at = level.front; at != itch::kNoIndex; at = book_->order_at(at).newer) {
      lowest = std::min(lowest, book_->order_at(at).reference);
    }
    const bool front = order.older == itch::kNoIndex;
    const Price4 shown = level.price;
    const Price4 traded = c.execution_price;
    // The resting order pays more, or is paid less, than it showed: the aggressor improved.
    const bool aggressor_improved = level.side == Side::bid ? traded > shown : traded < shown;
    std::size_t price_class = 2U;  // The resting order improved.
    if (aggressor_improved) {
      price_class = 0U;
    } else if (traded == shown) {
      price_class = 1U;
    }
    ++c_printable_[(price_class * 2U) + (front ? 0U : 1U)];
    c_printable_lowest_ += lowest == c.order_reference ? 1U : 0U;
  }

  // The first message after a jump that names the order then at the front settles its fate.
  void settle_fronts(char type, const std::byte* m, std::uint64_t time) {
    if (fronts_.empty()) {
      return;
    }
    std::uint64_t reference = 0;
    bool executed = false;
    switch (type) {
      case itch::OrderExecuted::kType:
        reference = itch::OrderExecuted::from_wire(m).order_reference;
        executed = true;
        break;
      case itch::OrderExecutedWithPrice::kType:
        reference = itch::OrderExecutedWithPrice::from_wire(m).order_reference;
        executed = true;
        break;
      case itch::OrderCancel::kType:
        reference = itch::OrderCancel::from_wire(m).order_reference;
        break;
      case itch::OrderDelete::kType:
        reference = itch::OrderDelete::from_wire(m).order_reference;
        break;
      case itch::OrderReplace::kType:
        reference = itch::OrderReplace::from_wire(m).original_order_reference;
        break;
      default: return;
    }
    const auto found = fronts_.find(reference);
    if (found == fronts_.end()) {
      return;
    }
    for (const std::size_t index : found->second) {
      Jump& jump = jumps_[index];
      if (!executed) {
        jump.fate = Fate::removed_first;
      } else {
        jump.fate = time == jump.time ? Fate::executed_same_ns : Fate::executed_later;
      }
    }
    fronts_.erase(found);
  }

  std::unique_ptr<itch::FastBook<>> book_;
  std::vector<std::uint64_t> added_;  // Per book slot.
  std::vector<std::uint8_t> late_;    // Per book slot.
  std::array<std::uint64_t, kLocates> highest_{};
  std::array<bool, kLocates> late_by_locate_{};
  std::array<itch::Stock, kLocates> stocks_{};
  std::array<char, kLocates> states_{};
  std::unordered_map<std::uint64_t, std::vector<std::size_t>> fronts_;
  std::vector<Jump> jumps_;
  Counts counts_;
  // Printable C in the population, by price (aggressor improved, same, resting order
  // improved) and by place (FIFO front, behind).
  std::array<std::uint64_t, 6> c_printable_{};
  std::uint64_t c_printable_lowest_ = 0;
  bool open_ = false;
  std::uint64_t open_time_ = 0;
};

int run(std::span<const char* const> args) {
  const char* path = nullptr;
  const char* details_path = nullptr;
  bool strict = false;
  bool valid = true;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string_view arg = args[i];
    if (arg == "--details" && i + 1 < args.size()) {
      details_path = args[++i];
    } else if (arg == "--strict") {
      strict = true;
    } else if (!arg.starts_with("--") && path == nullptr) {
      path = args[i];
    } else {
      valid = false;
    }
  }
  if (path == nullptr || !valid) {
    std::println(stderr, "usage: itch_queue_jumps FILE [--details FILE] [--strict]");
    return 2;
  }
  const auto file = itch::tools::MappedFile::open(path);
  if (!file) {
    std::println(stderr, "itch_queue_jumps: cannot read {}", path);
    return 2;
  }
  std::FILE* details = nullptr;
  if (details_path != nullptr) {
    // NOLINTNEXTLINE(cppcoreguidelines-owning-memory)
    details = std::fopen(details_path, "w");
    if (details == nullptr) {
      std::println(stderr, "itch_queue_jumps: cannot write {}", details_path);
      return 2;
    }
  }
  const auto jumps = std::make_unique<QueueJumps>();
  const bool replayed = jumps->replay(file->bytes());
  const bool held = replayed && jumps->print(details);
  if (details != nullptr) {
    static_cast<void>(std::fclose(details));  // NOLINT(cppcoreguidelines-owning-memory)
  }
  if (!replayed) {
    return 1;
  }
  std::println("strict {}", held ? "pass" : "FAIL");
  return strict && !held ? 1 : 0;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    return run(std::span<const char* const>{argv + 1, argv + argc});
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
