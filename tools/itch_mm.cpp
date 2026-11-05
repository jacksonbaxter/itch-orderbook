// The market maker on a whole day: one symbol, one model, one configuration of the switches,
// and its PnL attribution. These are PnL results, not timings: it prints counts, amounts and
// checks, never a time.
//
//     itch_mm FILE --date YYYYMMDD --symbol SYM --model as|glft --gamma G --sigma S --k K --a A
//             [--lot N] [--max-lots N] [--start HH:MM:SS] [--stop HH:MM:SS] [--close HH:MM:SS]
//             [--queue exact|conservative|proportional] [--latency-off] [--small]
//
// The strategy is Quoter (include/itch/mm/strategy.hpp) with the named model, quoting from
// --start to --stop (default 09:45:00 to 15:45:00); AS's horizon T is the stop. The PnL window
// runs from the start, flat, to --close (default 16:00:00), opening instead at the mark path's
// first point if that is later, and the mark path is the replayed historical book's
// (marks.hpp). Latencies are the engine's base ones, 25 µs each way and 5 µs of compute, unless
// --latency-off zeroes all four; --queue names the queue rule (backtest.hpp, "Q-off"). --small
// sizes every book for a fixture instead of a day.
//
// It prints a line naming every parameter, the attribution report of session.hpp in integer
// picodollars (S, its gross and fee lines, A, I, the total, the ledger's total computed apart,
// the identity, and A and I at each markout horizon), the shares bought and sold, the
// notional, the largest position, the engine's and the strategy's counts, and these checks:
//
//   - allocations: none between the end of construction and the end of the day;
//   - the engine refused no real message and its view book no delivered one, no ring and no
//     log overflowed, and no fee failed;
//   - every fill lies in the PnL window, and S + A + I equals the ledger's total, with ==.
//
// Exit codes: 0 every check passed, 1 a check failed, 2 bad usage or an unreadable file.
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

#include "itch/backtest.hpp"
#include "itch/book.hpp"
#include "itch/mm/attribution.hpp"
#include "itch/mm/fills.hpp"
#include "itch/mm/marks.hpp"
#include "itch/mm/quoting.hpp"
#include "itch/mm/session.hpp"
#include "itch/mm/strategy.hpp"
#include "mapped_file.hpp"
#include "oracle/alloc_counter.hpp"

namespace {

using itch::backtest::Nanos;
using itch::backtest::QueueModel;

constexpr Nanos kNanosPerSecond = 1'000'000'000;
constexpr itch::BookCapacity kSmall{
    .orders = 4096, .levels = 4096, .index_pages = 8, .index_bits = 16, .level_slots = 8192};
constexpr std::size_t kMarkCapacity = std::size_t{1} << 24U;
constexpr std::uint32_t kMaxFills = 1U << 20U;

struct Options {
  const char* path = nullptr;
  std::uint32_t date = 0;
  itch::Stock symbol{};
  bool has_symbol = false;
  bool glft = false;
  bool has_model = false;
  itch::mm::ModelParameters parameters{};
  std::uint32_t lot = 100;
  std::int64_t max_lots = 5;
  Nanos start = ((9 * 3600) + (45 * 60)) * kNanosPerSecond;
  Nanos stop = ((15 * 3600) + (45 * 60)) * kNanosPerSecond;
  Nanos close = kNanosPerSecond * 16 * 3600;
  QueueModel queue = QueueModel::exact;
  bool latency_off = false;
  bool small = false;
};

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

template <class T>
[[nodiscard]] bool parse_number(std::string_view text, T& out) {
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), out);
  return error == std::errc{} && end == text.data() + text.size();
}

[[nodiscard]] bool parse_positive(std::string_view text, double& out) {
  return parse_number(text, out) && out > 0.0 && out < 1e12;
}

[[nodiscard]] std::optional<QueueModel> parse_queue(std::string_view text) {
  for (const QueueModel model :
       {QueueModel::exact, QueueModel::conservative, QueueModel::proportional}) {
    if (text == itch::backtest::to_string(model)) {
      return model;
    }
  }
  return std::nullopt;
}

// One flag that takes a value. False if the value does not parse.
[[nodiscard]] bool take_value(Options& options, std::string_view flag, std::string_view value) {
  if (flag == "--date") {
    return value.size() == 8 && parse_number(value, options.date);
  }
  if (flag == "--symbol") {
    options.symbol.fill(' ');
    if (value.empty() || value.size() > options.symbol.size()) {
      return false;
    }
    for (std::size_t i = 0; i < value.size(); ++i) {
      options.symbol.at(i) = value[i];
    }
    options.has_symbol = true;
    return true;
  }
  if (flag == "--model") {
    options.glft = value == "glft";
    options.has_model = value == "glft" || value == "as";
    return options.has_model;
  }
  if (flag == "--gamma") {
    return parse_positive(value, options.parameters.gamma);
  }
  if (flag == "--sigma") {
    return parse_positive(value, options.parameters.sigma);
  }
  if (flag == "--k") {
    return parse_positive(value, options.parameters.k);
  }
  if (flag == "--a") {
    return parse_positive(value, options.parameters.a);
  }
  if (flag == "--lot") {
    return parse_number(value, options.lot) && options.lot != 0 && options.lot <= 1'000'000;
  }
  if (flag == "--max-lots") {
    return parse_number(value, options.max_lots) && options.max_lots > 0 &&
           options.max_lots <= 1000;
  }
  if (flag == "--queue") {
    const auto model = parse_queue(value);
    options.queue = model.value_or(QueueModel::exact);
    return model.has_value();
  }
  const auto clock = parse_clock(value);
  if (!clock) {
    return false;
  }
  if (flag == "--start") {
    options.start = *clock;
  } else if (flag == "--stop") {
    options.stop = *clock;
  } else {
    options.close = *clock;
  }
  return true;
}

[[nodiscard]] std::optional<Options> parse_options(std::span<const char* const> args) {
  constexpr std::array<std::string_view, 13> kValued{
      "--date", "--symbol",   "--model", "--gamma", "--sigma", "--k",    "--a",
      "--lot",  "--max-lots", "--queue", "--start", "--stop",  "--close"};
  Options options;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string_view arg = args[i];
    if (arg == "--latency-off") {
      options.latency_off = true;
    } else if (arg == "--small") {
      options.small = true;
    } else if (std::ranges::find(kValued, arg) != kValued.end()) {
      if (i + 1 == args.size() || !take_value(options, arg, args[i + 1])) {
        return std::nullopt;
      }
      ++i;
    } else if (arg.starts_with("--") || options.path != nullptr) {
      return std::nullopt;
    } else {
      options.path = args[i];
    }
  }
  const itch::mm::ModelParameters& p = options.parameters;
  if (options.path == nullptr || options.date == 0 || !options.has_symbol || !options.has_model ||
      p.gamma == 0.0 || p.sigma == 0.0 || p.k == 0.0 || p.a == 0.0 ||
      options.start >= options.stop || options.stop > options.close) {
    return std::nullopt;
  }
  return options;
}

[[nodiscard]] std::string name_of(const itch::Stock& stock) {
  std::string name{stock.begin(), stock.end()};
  name.erase(name.find_last_not_of(' ') + 1);
  return name;
}

void check(bool passed, std::string_view name, int& failed) {
  std::println("check {}: {}", name, passed ? "pass" : "FAIL");
  failed += passed ? 0 : 1;
}

template <class Model>
int session(const Options& options, std::span<const std::byte> bytes, const Model& model) {
  itch::backtest::Config config;
  config.date = options.date;
  config.latency_off = options.latency_off;
  config.queue = options.queue;
  config.symbols = {options.symbol};
  // The view book holds one symbol, so it needs far less than a whole day.
  config.view_capacity = {.orders = 1U << 20U,
                          .levels = 1U << 16U,
                          .index_pages = 1U << 15U,
                          .index_bits = 30,
                          .level_slots = 1U << 18U};
  if (options.small) {
    config.exchange_capacity = kSmall;
    config.view_capacity = kSmall;
  }
  const auto engine = std::make_unique<itch::backtest::Engine<>>(config);
  if (!engine->ready()) {
    std::println(stderr, "itch_mm: no fee schedule for {}", options.date);
    return 2;
  }
  const itch::mm::QuoterConfig quoting{.symbol = options.symbol,
                                       .lot = options.lot,
                                       .tick = 100,
                                       .max_lots = options.max_lots,
                                       .start = options.start,
                                       .stop = options.stop,
                                       .max_fills = kMaxFills};
  const auto maker = std::make_unique<itch::mm::Quoter<Model>>(quoting, model);
  itch::mm::MarkRecorder marks{options.symbol, kMarkCapacity};

  itch::oracle::reset_alloc_counts();
  const std::uint64_t messages = itch::mm::replay(*engine, bytes, *maker, marks);
  const std::uint64_t allocations = itch::oracle::read_alloc_counts().total();

  // The strategy cannot quote before it has seen a mid, so a path that starts after --start
  // opens the window at its first point.
  const Nanos first_mark = marks.points().empty() ? options.start : marks.points().front().time;
  const itch::mm::Window window{
      .open = std::max(options.start, first_mark), .close = options.close, .opening_position = 0};
  std::int64_t bought = 0;
  std::int64_t sold = 0;
  std::int64_t position = 0;
  std::int64_t largest = 0;
  unsigned __int128 notional = 0;
  bool in_window = true;
  for (const itch::mm::Fill& fill : maker->fills()) {
    (fill.quantity > 0 ? bought : sold) += fill.quantity > 0 ? fill.quantity : -fill.quantity;
    position += fill.quantity;
    largest = std::max(largest, position < 0 ? -position : position);
    notional += static_cast<unsigned __int128>(fill.quantity > 0 ? fill.quantity : -fill.quantity) *
                fill.price * itch::mm::kPicodollarsPerPrice4;
    in_window = in_window && fill.time >= window.open && fill.time <= window.close;
  }
  const auto report = itch::mm::format_report({.fills = maker->fills(),
                                               .marks = &marks,
                                               .window = window,
                                               .horizon = 15 * kNanosPerSecond});

  const auto& stats = engine->stats();
  const auto& made = maker->counts();
  std::println(
      "mm symbol {} model {} queue {} latency_off {} gamma {} sigma {} k {} a {} lot {} max_lots "
      "{} start_ns {} stop_ns {} close_ns {} date {}",
      name_of(options.symbol), options.glft ? "glft" : "as",
      itch::backtest::to_string(options.queue), options.latency_off ? 1 : 0,
      options.parameters.gamma, options.parameters.sigma, options.parameters.k,
      options.parameters.a, options.lot, options.max_lots, options.start, options.stop,
      options.close, options.date);
  if (report) {
    std::print("{}", *report);
  } else {
    std::println("attribution refused: {}", itch::mm::to_string(report.error()));
  }
  std::println("bought {}\nsold {}\nlargest_position {}", bought, sold, largest);
  std::println("notional {}", static_cast<std::uint64_t>(notional));
  std::println("messages {} deliveries {} out_of_order {}", messages, stats.deliveries,
               stats.out_of_order);
  std::println("engine fills {} shares {} maker {} taker {} crossing {}", stats.fills,
               stats.filled_shares, stats.maker_fills, stats.taker_fills, stats.crossing_fills);
  std::println("queue_anomalies {} late_adds_ahead {} hidden_prints_at_sim_price {}",
               stats.queue_anomalies, stats.late_adds_ahead, stats.hidden_prints_at_sim_price);
  std::println("strategy sends {} replaces {} cancels {} rejects {} unpriced {}", made.sends,
               made.replaces, made.cancels, made.rejects, made.unpriced);
  std::println("allocations_after_construction {}", allocations);

  int failed = 0;
  check(allocations == 0, "allocations", failed);
  check(stats.refused_messages == 0, "no refused message", failed);
  check(stats.view_refused == 0, "no refused delivery", failed);
  check(stats.overflows == 0 && marks.overflows() == 0 && made.fill_overflows == 0, "no overflow",
        failed);
  check(stats.fee_errors == 0, "no fee error", failed);
  check(in_window, "every fill in the window", failed);
  check(report.has_value() && report->find("\nidentity holds\n") != std::string::npos, "identity",
        failed);
  return failed == 0 ? 0 : 1;
}

int run(const std::optional<Options>& options) {
  if (!options) {
    std::println(stderr,
                 "usage: itch_mm FILE --date YYYYMMDD --symbol SYM --model as|glft --gamma G "
                 "--sigma S --k K --a A [--lot N] [--max-lots N] [--start HH:MM:SS] "
                 "[--stop HH:MM:SS] [--close HH:MM:SS] [--queue exact|conservative|proportional] "
                 "[--latency-off] [--small]");
    return 2;
  }
  const auto file = itch::tools::MappedFile::open(options->path);
  if (!file) {
    std::println(stderr, "itch_mm: cannot read {}", options->path);
    return 2;
  }
  if (options->glft) {
    return session(*options, file->bytes(),
                   itch::mm::GlftModel{options->parameters, options->max_lots});
  }
  return session(*options, file->bytes(), itch::mm::AsModel{options->parameters, options->stop});
}

}  // namespace

int main(int argc, char** argv) {
  try {
    return run(parse_options(std::span<const char* const>{argv + 1, argv + argc}));
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
