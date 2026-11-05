// σ, A and k from the market tape, for each symbol in a list, by the estimators in the header
// comment of include/itch/mm/calibration.hpp (market-maker.md, "Parameters to calibrate").
//
//     itch_calibrate FILE --symbols SYM[,SYM...] [--interval-ms N] [--sample-ms N]
//                    [--tick N] [--max-depth-ticks N]
//
// --interval-ms   Δ, the interval for A and k (default 100, as MK-21).
// --sample-ms     S, the clock for σ (default 1000).
// --tick          τ in Price4 (default 100, one cent).
// --max-depth-ticks  the deepest depth fitted; deeper executions count at the cap (default 20).
//
// Prints, per symbol, every count the estimates rest on, both σ, each depth's exposure, hits
// and λ, and the fit of log λ on δ with its number of points and R². Units are ticks and
// seconds. It reads the market tape only, never a strategy's fills.
//
// Exit codes: 0 every symbol was found and calibrated, 1 the book refused a message or a
// symbol is not in the day's directory, 2 bad usage or an unreadable file.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <format>
#include <memory>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "itch/book.hpp"
#include "itch/messages.hpp"
#include "itch/mm/calibration.hpp"
#include "itch/reader.hpp"
#include "mapped_file.hpp"

namespace {

using itch::mm::CalibrationSettings;
using itch::mm::Calibrator;

constexpr std::uint64_t kNanosPerMilli = 1'000'000;

[[nodiscard]] std::string name_of(const itch::Stock& stock) {
  std::string name{stock.begin(), stock.end()};
  name.erase(name.find_last_not_of(' ') + 1);
  return name;
}

[[nodiscard]] itch::Stock stock_of(std::string_view name) {
  itch::Stock stock;
  stock.fill(' ');
  for (std::size_t i = 0; i < name.size() && i < stock.size(); ++i) {
    stock.at(i) = name[i];
  }
  return stock;
}

void print(const Calibrator& c, const CalibrationSettings& settings) {
  std::println("symbol {}", name_of(c.symbol()));
  std::println("session_open_ns {}", c.opened_at());
  std::println("session_close_ns {}", c.closed_at());
  std::println("valid_seconds {:.6f}", static_cast<double>(c.valid_nanos()) / 1e9);
  std::println("sigma_clock_changes {}", c.sample_changes());
  const auto clock = c.sigma_clock();
  std::println("sigma_clock {}", clock ? std::format("{:.6f}", *clock) : "none");
  std::println("sigma_jump_count {}", c.jumps());
  const auto jump = c.sigma_jump();
  std::println("sigma_jump {}", jump ? std::format("{:.6f}", *jump) : "none");
  std::println("intervals {}", c.intervals());
  std::println("intervals_off_grid {}", c.intervals_off_grid());
  std::println("executions {}", c.executions());
  const std::vector<itch::mm::DepthPoint> points = c.points();
  for (const itch::mm::DepthPoint& point : points) {
    std::println("depth {:.1f} exposure {} hits {} lambda {:.6f}",
                 static_cast<double>(point.half_ticks) / 2.0, point.exposure, point.hits,
                 itch::mm::intensity(point, settings.interval));
  }
  const auto fit = itch::mm::fit_intensity(points, settings.interval);
  if (fit) {
    std::println("fit points {} k {:.6f} A {:.6f} r_squared {:.6f}{}", fit->points, fit->k, fit->a,
                 fit->r_squared, fit->points == 2 ? " exact, two points test nothing" : "");
  } else {
    std::println("fit none, fewer than two depths with a hit");
  }
}

[[nodiscard]] bool positive(const std::string& text, std::uint64_t& out) {
  const long long value = std::stoll(text);
  if (value <= 0) {
    return false;
  }
  out = static_cast<std::uint64_t>(value);
  return true;
}

int run(std::span<const char* const> args) {
  const char* path = nullptr;
  std::vector<std::string> symbols;
  CalibrationSettings settings;
  bool valid = true;
  for (std::size_t i = 0; i < args.size() && valid; ++i) {
    const std::string_view arg = args[i];
    const bool has_value = i + 1 < args.size();
    std::uint64_t value = 0;
    if (arg == "--symbols" && has_value) {
      const std::string_view list = args[++i];
      for (std::size_t start = 0; start <= list.size();) {
        const std::size_t comma = std::min(list.find(',', start), list.size());
        const std::string_view name = list.substr(start, comma - start);
        valid = valid && !name.empty() && name.size() <= itch::Stock{}.size();
        symbols.emplace_back(name);
        start = comma + 1;
      }
    } else if (arg == "--interval-ms" && has_value && positive(args[++i], value)) {
      settings.interval = value * kNanosPerMilli;
    } else if (arg == "--sample-ms" && has_value && positive(args[++i], value)) {
      settings.sample = value * kNanosPerMilli;
    } else if (arg == "--tick" && has_value && positive(args[++i], value) && value <= 1'000'000) {
      settings.tick = static_cast<itch::Price4>(value);
    } else if (arg == "--max-depth-ticks" && has_value && positive(args[++i], value) &&
               value <= 10'000) {
      settings.max_depth_ticks = static_cast<std::int64_t>(value);
    } else if (!arg.starts_with("--") && path == nullptr) {
      path = args[i];
    } else {
      valid = false;
    }
  }
  if (path == nullptr || symbols.empty() || !valid) {
    std::println(stderr,
                 "usage: itch_calibrate FILE --symbols SYM[,SYM...] [--interval-ms N] "
                 "[--sample-ms N] [--tick N] [--max-depth-ticks N]");
    return 2;
  }
  const auto file = itch::tools::MappedFile::open(path);
  if (!file) {
    std::println(stderr, "itch_calibrate: cannot read {}", path);
    return 2;
  }
  std::vector<Calibrator> calibrators;
  calibrators.reserve(symbols.size());
  for (const std::string& symbol : symbols) {
    calibrators.emplace_back(stock_of(symbol), settings);
  }
  const auto book = std::make_unique<itch::FastBook<>>();
  itch::FrameReader reader{file->bytes()};
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != itch::FrameStatus::message) {
      continue;
    }
    for (Calibrator& calibrator : calibrators) {
      calibrator.before(*book, frame.payload);
    }
    const itch::ApplyStatus status = book->apply(frame.payload);
    if (status != itch::ApplyStatus::applied && status != itch::ApplyStatus::no_book_effect) {
      std::println(stderr, "itch_calibrate: the book refused a message: {}",
                   itch::to_string(status));
      return 1;
    }
    for (Calibrator& calibrator : calibrators) {
      calibrator.after(*book, frame.payload);
    }
  }
  std::println(
      "calibrate units ticks seconds interval_ms {} sample_ms {} tick {} max_depth_ticks {}",
      settings.interval / kNanosPerMilli, settings.sample / kNanosPerMilli, settings.tick,
      settings.max_depth_ticks);
  bool found = true;
  for (Calibrator& calibrator : calibrators) {
    calibrator.finish();
    if (!calibrator.known()) {
      std::println("symbol {} not in the directory", name_of(calibrator.symbol()));
      found = false;
      continue;
    }
    print(calibrator, settings);
  }
  return found ? 0 : 1;
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
