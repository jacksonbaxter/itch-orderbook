// Replays a BinaryFILE ITCH 5.0 stream through a book and checks it against the
// exchange's own prints: T-TRADE-1, T-TRADE-2, T-TRADE-3 and T-TRADE-5, and the per-type
// table. tests/trades/trade_checks.hpp is the check, and this is its command line.
//
//     itch_trades FILE [--date YYYYMMDD] [--crossstats FILE] [--midas FILE]
//                 [--midas-rows N] [--symbols FILE] [--details N]
//                 [--max-orders N] [--max-levels N]
//
// --date        the trading day. Needed with --crossstats or --midas.
// --crossstats  Nasdaq's cross file for that day. Without it T-TRADE-2 is not run.
// --midas       the SEC MIDAS month holding that day. Without it T-TRADE-3 is not run.
// --midas-rows  the number of Nasdaq rows that day is pinned at, so that a missing row
//               cannot pass as a symbol outside MIDAS's universe.
// --symbols     writes one line per symbol, for the raw log.
// --details     how many violations of each check are printed in full. All are counted.
//
// Exit codes: 0 every check that ran passed. 2 bad usage or an unreadable file. 3 a
// reference file failed to parse, with its typed error printed. Otherwise 64 plus one
// bit for each failed result: 1 T-TRADE-1, 2 T-TRADE-2, 4 T-TRADE-3, 8 T-TRADE-5,
// 16 the per-type checks on E, C, P, Q and B.
#include <charconv>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <optional>
#include <print>
#include <span>
#include <string_view>
#include <system_error>

#include "itch/book.hpp"
#include "itch/reader.hpp"
#include "mapped_file.hpp"
#include "trades/cross_stats.hpp"
#include "trades/midas.hpp"
#include "trades/trade_checks.hpp"

namespace {

using itch::trades::Group;
using itch::trades::RunConfig;

// The book under test. Phase 3 changes this line.
using Run = itch::trades::TradeRun<itch::FastBook<itch::trades::WriteCounter>>;

constexpr int kExitUsage = 2;
constexpr int kExitReference = 3;
constexpr int kExitFailureBase = 64;

struct Options {
  const char* path = nullptr;
  const char* crossstats = nullptr;
  const char* midas = nullptr;
  const char* symbols = nullptr;
  std::uint32_t date = 0;
  std::optional<std::uint64_t> midas_rows;
  RunConfig config;
};

[[nodiscard]] std::optional<std::uint64_t> parse_number(std::string_view text) {
  std::uint64_t value = 0;
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
  if (error != std::errc{} || end != text.data() + text.size()) {
    return std::nullopt;
  }
  return value;
}

[[nodiscard]] bool take_number(Options& options, std::string_view flag, std::uint64_t value) {
  if (flag == "--date") {
    options.date = static_cast<std::uint32_t>(value);
  } else if (flag == "--midas-rows") {
    options.midas_rows = value;
  } else if (flag == "--details") {
    options.config.detail_limit = static_cast<std::size_t>(value);
  } else if (flag == "--max-orders") {
    options.config.capacity.orders = static_cast<std::uint32_t>(value);
  } else if (flag == "--max-levels") {
    options.config.capacity.levels = static_cast<std::uint32_t>(value);
  } else {
    return false;
  }
  return true;
}

[[nodiscard]] std::optional<Options> parse_options(std::span<const char* const> args) {
  Options options;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string_view arg = args[i];
    if (!arg.starts_with("--")) {
      if (options.path != nullptr) {
        return std::nullopt;
      }
      options.path = args[i];
      continue;
    }
    if (i + 1 == args.size()) {
      return std::nullopt;
    }
    ++i;
    if (arg == "--crossstats") {
      options.crossstats = args[i];
    } else if (arg == "--midas") {
      options.midas = args[i];
    } else if (arg == "--symbols") {
      options.symbols = args[i];
    } else {
      const auto number = parse_number(args[i]);
      if (!number || !take_number(options, arg, *number)) {
        return std::nullopt;
      }
    }
  }
  const bool has_reference = options.crossstats != nullptr || options.midas != nullptr;
  if (options.path == nullptr || (has_reference && options.date == 0)) {
    return std::nullopt;
  }
  return options;
}

[[nodiscard]] bool replay(Run& run, itch::FrameReader& reader) {
  itch::Frame frame = reader.next();
  for (; !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == itch::FrameStatus::zero_length) {
      continue;
    }
    if (frame.status != itch::FrameStatus::message) {
      run.note_malformed();
      continue;
    }
    run.on_message(frame.payload);
  }
  return frame.status == itch::FrameStatus::end_of_input;
}

[[nodiscard]] int compare_cross_stats(Run& run, const char* path, std::uint32_t date) {
  const auto file = itch::tools::MappedFile::open(path);
  if (!file) {
    std::println(stderr, "itch_trades: cannot read {}", path);
    return kExitUsage;
  }
  const auto table = itch::trades::parse_cross_stats(file->bytes());
  if (!table) {
    std::println("reference_error crossstats {} line={}", to_string(table.error().failure),
                 table.error().line);
    return kExitReference;
  }
  run.compare_cross_stats(*table, date);
  return 0;
}

[[nodiscard]] int compare_midas(Run& run, const Options& options) {
  const auto file = itch::tools::MappedFile::open(options.midas);
  if (!file) {
    std::println(stderr, "itch_trades: cannot read {}", options.midas);
    return kExitUsage;
  }
  const auto table = itch::trades::parse_midas(
      file->bytes(), {.date = options.date, .exchange = itch::trades::kMidasExchange});
  if (!table) {
    std::println("reference_error midas {} line={}", to_string(table.error().failure),
                 table.error().line);
    return kExitReference;
  }
  run.compare_midas(*table, options.midas_rows);
  return 0;
}

void write_symbols(const Run& run, const char* path) {
  // NOLINTNEXTLINE(cppcoreguidelines-owning-memory)
  std::FILE* out = std::fopen(path, "w");
  if (out == nullptr) {
    std::println(stderr, "itch_trades: cannot write {}", path);
    return;
  }
  run.write_symbols(out);
  static_cast<void>(std::fclose(out));  // NOLINT(cppcoreguidelines-owning-memory)
}

[[nodiscard]] int verdicts(const Run& run) {
  struct Line {
    Group group;
    int bit;
    bool ran;
    std::string_view not_run;
  };
  const Line lines[] = {
      {.group = Group::t1, .bit = 1, .ran = true, .not_run = ""},
      {.group = Group::t2, .bit = 2, .ran = run.cross_compared(), .not_run = "NOT_AVAILABLE"},
      {.group = Group::t3, .bit = 4, .ran = run.midas_compared(), .not_run = "NOT_RUN"},
      {.group = Group::t5, .bit = 8, .ran = true, .not_run = ""},
      {.group = Group::prints, .bit = 16, .ran = true, .not_run = ""},
  };
  int mask = 0;
  for (const Line& line : lines) {
    const std::uint64_t count = run.violations(line.group);
    if (!line.ran) {
      std::println("verdict {} {} violations=0", to_string(line.group), line.not_run);
      continue;
    }
    std::println("verdict {} {} violations={}", to_string(line.group), count == 0 ? "PASS" : "FAIL",
                 count);
    mask |= count == 0 ? 0 : line.bit;
  }
  return mask == 0 ? 0 : kExitFailureBase + mask;
}

int run_tool(const Options& options) {
  const auto file = itch::tools::MappedFile::open(options.path);
  if (!file) {
    std::println(stderr, "itch_trades: cannot read {}", options.path);
    return kExitUsage;
  }
  std::println("trades_date {}", options.date);
  std::println("trades_max_orders {}", options.config.capacity.orders);
  std::println("trades_max_levels {}", options.config.capacity.levels);
  std::println("bytes {}", file->bytes().size());

  Run run{options.config};
  itch::FrameReader reader{file->bytes()};
  const auto started = std::chrono::steady_clock::now();
  const bool clean = replay(run, reader);
  run.finish(clean);
  const std::chrono::duration<double> replayed = std::chrono::steady_clock::now() - started;

  int reference_status = 0;
  if (options.crossstats != nullptr) {
    reference_status = compare_cross_stats(run, options.crossstats, options.date);
  }
  if (reference_status == 0 && options.midas != nullptr) {
    reference_status = compare_midas(run, options);
  }
  if (options.symbols != nullptr) {
    write_symbols(run, options.symbols);
  }

  const auto& counters = reader.counters();
  std::println("elapsed_seconds {:.1f}", replayed.count());
  std::println("frames {}", counters.frames);
  for (unsigned type = 0; type < counters.frames_by_type.size(); ++type) {
    if (counters.frames_by_type[type] != 0) {
      std::println("type {:c} {}", static_cast<char>(type), counters.frames_by_type[type]);
    }
  }
  std::println("messages_checked {}", run.messages());
  run.print_report();
  const int verdict = verdicts(run);
  return reference_status != 0 ? reference_status : verdict;
}

}  // namespace

int main(int argc, const char* const* argv) {
  try {
    const auto options = parse_options(std::span{argv + 1, static_cast<std::size_t>(argc - 1)});
    if (!options) {
      static_cast<void>(
          std::fputs("usage: itch_trades FILE [--date YYYYMMDD] [--crossstats FILE] "
                     "[--midas FILE] [--midas-rows N] [--symbols FILE] [--details N] "
                     "[--max-orders N] [--max-levels N]\n",
                     stderr));
      return kExitUsage;
    }
    return run_tool(*options);
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return kExitUsage;
  }
}
