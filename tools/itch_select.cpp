// The market maker's symbols, chosen from one day's tape by the rule of the research evidence
// (market-maker.md, MK-75), before any strategy runs on any day.
//
//     itch_select FILE [--from HH:MM:SS] [--to HH:MM:SS] [--top N]
//
// For every symbol in the day's Stock Directory it measures, from the Nasdaq book alone:
//
//   - the time-weighted average spread over [from, to], 09:45:00 to 15:45:00 by default, over
//     the time the symbol is in state 'T' with both sides present and not crossed; a locked book
//     counts at a spread of 0;
//   - the share of that time at a spread of exactly one cent;
//   - executed shares in regular hours, System Event `Q` to `M`: every `E` and every printable
//     `C`, the executions a displayed order can take part in;
//   - the close: the mid of the last valid book at System Event `M`;
//   - a halt: any state other than 'T' from `Q` to `M`.
//
// The rule, every threshold an integer comparison:
//
//   universe  Market Category Q, G or S (Nasdaq-listed), Issue Classification C (common stock),
//             ETP flag not Y, Authenticity P (not a test issue), not halted, a close from $5 to
//             $500, and some valid time in the window (MK-75, 1).
//   group A   tick-constrained: average spread at most $0.015 and at one tick at least 80% of
//             the valid time (MK-75, 3).
//   group B   not constrained: average spread at least $0.05 and at one tick at most 5% of the
//             valid time (MK-75, 4).
//
// Each group's pick is its symbol with the most executed shares, a tie going to the lower
// locate. It prints the counts behind the universe, each group's top N (default 10) with every
// figure, and one line `group A SYM` and one `group B SYM`.
//
// Exit codes: 0 both groups have a pick, 1 a group is empty, the book refused a message or the
// file's framing did not end on a frame boundary (tools/framing.hpp), 2 bad usage or an
// unreadable file.
#include <algorithm>
#include <array>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <memory>
#include <optional>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include "framing.hpp"
#include "itch/book.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"
#include "mapped_file.hpp"

namespace {

using Nanos = std::uint64_t;

constexpr std::size_t kLocates = 65536;
constexpr Nanos kNanosPerSecond = 1'000'000'000;
constexpr itch::Price4 kCent = 100;
constexpr itch::Price4 kAverageSpreadA = 150;       // $0.015
constexpr itch::Price4 kAverageSpreadB = 500;       // $0.05
constexpr std::uint64_t kOneTickShareA = 80;        // At least, in percent.
constexpr std::uint64_t kOneTickShareB = 5;         // At most, in percent.
constexpr std::uint64_t kLowestClose = 50'000;      // $5, in Price4.
constexpr std::uint64_t kHighestClose = 5'000'000;  // $500, in Price4.

struct Options {
  const char* path = nullptr;
  Nanos from = ((9 * 3600) + (45 * 60)) * kNanosPerSecond;
  Nanos to = ((15 * 3600) + (45 * 60)) * kNanosPerSecond;
  std::size_t top = 10;
};

// One symbol's day.
struct Symbol {
  unsigned __int128 spread_time = 0;  // Price4 × ns over the valid time in the window.
  Nanos valid = 0;                    // In the window.
  Nanos one_tick = 0;                 // Of the valid time, at a spread of one cent.
  Nanos since = 0;                    // The time the state below began.
  std::uint64_t executed = 0;
  std::uint64_t mid2 = 0;    // Best bid + best ask of the last valid book.
  std::uint64_t close2 = 0;  // mid2 at System Event M.
  itch::Price4 spread = 0;
  itch::Stock stock{};
  char category = ' ';
  char issue = ' ';
  char etp = ' ';
  char authenticity = ' ';
  char state = ' ';
  bool known = false;
  bool valid_now = false;
  bool halted = false;
  bool closed = false;
};

enum class Reason : std::uint8_t { in, category, issue, etp, test, halted, close, no_book };

[[nodiscard]] Reason exclusion(const Symbol& s) noexcept {
  if (s.category != 'Q' && s.category != 'G' && s.category != 'S') {
    return Reason::category;
  }
  if (s.issue != 'C') {
    return Reason::issue;
  }
  if (s.etp == 'Y') {
    return Reason::etp;
  }
  if (s.authenticity != 'P') {
    return Reason::test;
  }
  if (s.halted) {
    return Reason::halted;
  }
  if (!s.closed || s.close2 < 2 * kLowestClose || s.close2 > 2 * kHighestClose) {
    return Reason::close;
  }
  if (s.valid == 0) {
    return Reason::no_book;
  }
  return Reason::in;
}

[[nodiscard]] bool in_group_a(const Symbol& s) noexcept {
  return s.spread_time <= static_cast<unsigned __int128>(kAverageSpreadA) * s.valid &&
         static_cast<unsigned __int128>(s.one_tick) * 100 >=
             static_cast<unsigned __int128>(kOneTickShareA) * s.valid;
}

[[nodiscard]] bool in_group_b(const Symbol& s) noexcept {
  return s.spread_time >= static_cast<unsigned __int128>(kAverageSpreadB) * s.valid &&
         static_cast<unsigned __int128>(s.one_tick) * 100 <=
             static_cast<unsigned __int128>(kOneTickShareB) * s.valid;
}

class Selector {
 public:
  Selector(Nanos from, Nanos to) : from_{from}, to_{to}, symbols_(kLocates) {}

  // After the book has applied `payload`.
  void after(const itch::FastBook<>& book, std::span<const std::byte> payload) {
    const std::byte* m = payload.data();
    const char type = itch::read_char(m);
    const auto locate = itch::read_be<std::uint16_t>(m + 1);
    const Nanos time = std::max(itch::read_be48(m + 5), clock_);
    clock_ = time;
    Symbol& s = symbols_[locate];
    switch (type) {
      case itch::SystemEvent::kType:
        system_event(itch::SystemEvent::from_wire(m).event_code);
        return;
      case itch::StockDirectory::kType: {
        const itch::StockDirectory d = itch::StockDirectory::from_wire(m);
        s.stock = d.stock;
        s.category = d.market_category;
        s.issue = d.issue_classification;
        s.etp = d.etp_flag;
        s.authenticity = d.authenticity;
        s.known = true;
        break;
      }
      case itch::StockTradingAction::kType:
        s.state = itch::StockTradingAction::from_wire(m).trading_state;
        s.halted = s.halted || (regular_ && s.state != 'T');
        break;
      case itch::OrderExecuted::kType:
        s.executed += regular_ ? itch::OrderExecuted::from_wire(m).executed_shares : 0U;
        break;
      case itch::OrderExecutedWithPrice::kType: {
        const itch::OrderExecutedWithPrice c = itch::OrderExecutedWithPrice::from_wire(m);
        s.executed += regular_ && c.printable == 'Y' ? c.executed_shares : 0U;
        break;
      }
      default: break;
    }
    if (!s.known) {
      return;
    }
    account(s, time);
    const itch::TopOfBook bid = book.best(locate, itch::Side::bid);
    const itch::TopOfBook ask = book.best(locate, itch::Side::ask);
    s.valid_now = s.state == 'T' && bid.present && ask.present && bid.price <= ask.price;
    if (s.valid_now) {
      s.spread = ask.price - bid.price;
      s.mid2 = std::uint64_t{bid.price} + ask.price;
    }
  }

  // Closes every symbol's window at its end.
  void finish() {
    for (Symbol& s : symbols_) {
      if (s.known) {
        account(s, std::max(clock_, to_));
      }
    }
  }

  [[nodiscard]] const std::vector<Symbol>& symbols() const noexcept { return symbols_; }

 private:
  // The state since `s.since` held until `time`: its share of the window counted.
  void account(Symbol& s, Nanos time) const noexcept {
    const Nanos start = std::max(s.since, from_);
    const Nanos end = std::min(time, to_);
    if (s.valid_now && end > start) {
      const Nanos length = end - start;
      s.valid += length;
      s.spread_time += static_cast<unsigned __int128>(s.spread) * length;
      s.one_tick += s.spread == kCent ? length : 0;
    }
    s.since = time;
  }

  void system_event(char code) {
    if (code == 'Q') {
      regular_ = true;
      for (Symbol& s : symbols_) {
        s.halted = s.halted || (s.known && s.state != 'T');
      }
    } else if (code == 'M') {
      regular_ = false;
      for (Symbol& s : symbols_) {
        s.close2 = s.mid2;
        s.closed = s.known && s.mid2 != 0;
      }
    }
  }

  Nanos from_;
  Nanos to_;
  Nanos clock_ = 0;
  bool regular_ = false;
  std::vector<Symbol> symbols_;
};

[[nodiscard]] std::string name_of(const itch::Stock& stock) {
  std::string name{stock.begin(), stock.end()};
  name.erase(name.find_last_not_of(' ') + 1);
  return name;
}

[[nodiscard]] double percent(Nanos part, Nanos whole) {
  return whole == 0 ? 0.0 : 100.0 * static_cast<double>(part) / static_cast<double>(whole);
}

void print_row(const Symbol& s, std::size_t locate) {
  const double average =
      static_cast<double>(s.spread_time) / static_cast<double>(s.valid) / 10'000.0;
  std::println(
      "  {} locate {} executed {} average_spread {:.6f} one_tick_pct {:.3f} valid_seconds {:.3f} "
      "close {:.4f}",
      name_of(s.stock), locate, s.executed, average, percent(s.one_tick, s.valid),
      static_cast<double>(s.valid) / 1e9, static_cast<double>(s.close2) / 20'000.0);
}

// The group's members, most executed shares first, a tie to the lower locate.
[[nodiscard]] std::vector<std::size_t> ranked(const std::vector<Symbol>& symbols,
                                              bool (*member)(const Symbol&) noexcept) {
  std::vector<std::size_t> out;
  for (std::size_t locate = 0; locate < symbols.size(); ++locate) {
    if (symbols[locate].known && exclusion(symbols[locate]) == Reason::in &&
        member(symbols[locate])) {
      out.push_back(locate);
    }
  }
  std::ranges::stable_sort(out, [&symbols](std::size_t a, std::size_t b) {
    return symbols[a].executed > symbols[b].executed;
  });
  return out;
}

[[nodiscard]] std::optional<Nanos> parse_clock(std::string_view text) {
  if (text.size() != 8 || text[2] != ':' || text[5] != ':') {
    return std::nullopt;
  }
  std::array<unsigned, 3> parts{};
  for (std::size_t i = 0; i < parts.size(); ++i) {
    const char* begin = text.data() + (3 * i);
    const auto [end, error] = std::from_chars(begin, begin + 2, parts.at(i));
    if (error != std::errc{} || end != begin + 2) {
      return std::nullopt;
    }
  }
  if (parts[0] > 23 || parts[1] > 59 || parts[2] > 59) {
    return std::nullopt;
  }
  return Nanos{(parts[0] * 3600U) + (parts[1] * 60U) + parts[2]} * kNanosPerSecond;
}

[[nodiscard]] std::optional<Options> parse(std::span<const char* const> args) {
  Options options;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string_view arg = args[i];
    const bool has_value = i + 1 < args.size();
    if ((arg == "--from" || arg == "--to") && has_value) {
      const auto clock = parse_clock(args[++i]);
      if (!clock) {
        return std::nullopt;
      }
      (arg == "--from" ? options.from : options.to) = *clock;
    } else if (arg == "--top" && has_value) {
      const std::string_view value = args[++i];
      const auto [end, error] =
          std::from_chars(value.data(), value.data() + value.size(), options.top);
      if (error != std::errc{} || end != value.data() + value.size() || options.top == 0) {
        return std::nullopt;
      }
    } else if (!arg.starts_with("--") && options.path == nullptr) {
      options.path = args[i];
    } else {
      return std::nullopt;
    }
  }
  if (options.path == nullptr || options.from >= options.to) {
    return std::nullopt;
  }
  return options;
}

[[nodiscard]] bool replay(std::span<const std::byte> bytes, Selector& selector) {
  const auto book = std::make_unique<itch::FastBook<>>();
  itch::FrameReader reader{bytes};
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != itch::FrameStatus::message) {
      continue;
    }
    const itch::ApplyStatus status = book->apply(frame.payload);
    if (status != itch::ApplyStatus::applied && status != itch::ApplyStatus::no_book_effect) {
      std::println(stderr, "itch_select: the book refused a message: {}", itch::to_string(status));
      return false;
    }
    selector.after(*book, frame.payload);
  }
  selector.finish();
  return true;
}

void print_counts(const std::vector<Symbol>& symbols) {
  std::array<std::uint64_t, 8> counts{};
  std::uint64_t directory = 0;
  for (const Symbol& s : symbols) {
    if (s.known) {
      ++directory;
      ++counts.at(static_cast<std::size_t>(exclusion(s)));
    }
  }
  std::println("directory_symbols {}", directory);
  std::println("excluded category {} issue {} etp {} test {} halted {} close {} no_valid_book {}",
               counts[1], counts[2], counts[3], counts[4], counts[5], counts[6], counts[7]);
  std::println("universe {}", counts[0]);
}

// One group's ranking and pick. False if it is empty.
bool print_group(const std::vector<Symbol>& symbols, char name,
                 bool (*member)(const Symbol&) noexcept, std::size_t top) {
  const std::vector<std::size_t> members = ranked(symbols, member);
  std::println("group {} members {}", name, members.size());
  for (std::size_t i = 0; i < members.size() && i < top; ++i) {
    print_row(symbols[members[i]], members[i]);
  }
  if (members.empty()) {
    std::println("group {} none", name);
    return false;
  }
  std::println("group {} {}", name, name_of(symbols[members.front()].stock));
  return true;
}

int run(std::span<const char* const> args) {
  const std::optional<Options> options = parse(args);
  if (!options) {
    std::println(stderr, "usage: itch_select FILE [--from HH:MM:SS] [--to HH:MM:SS] [--top N]");
    return 2;
  }
  const auto file = itch::tools::MappedFile::open(options->path);
  if (!file) {
    std::println(stderr, "itch_select: cannot read {}", options->path);
    return 2;
  }
  if (!itch::tools::framing_ends_cleanly("itch_select", file->bytes())) {
    return 1;
  }
  auto selector = std::make_unique<Selector>(options->from, options->to);
  if (!replay(file->bytes(), *selector)) {
    return 1;
  }
  std::println("select window_from_ns {} window_to_ns {} rule MK-75", options->from, options->to);
  const std::vector<Symbol>& symbols = selector->symbols();
  print_counts(symbols);
  const bool a = print_group(symbols, 'A', in_group_a, options->top);
  const bool b = print_group(symbols, 'B', in_group_b, options->top);
  return a && b ? 0 : 1;
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
