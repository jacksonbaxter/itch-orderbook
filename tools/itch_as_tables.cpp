// T-MM-2: Avellaneda and Stoikov's three tables (Quantitative Finance 8(3), 2008, pp. 222-223)
// by Monte Carlo, with the quoting model the strategy uses (include/itch/mm/quoting.hpp).
//
//     itch_as_tables [--paths N] [--seed S] [--gamma G]...
//
// The defaults are the paper's: s = 100, T = 1, σ = 2, dt = 0.005, q = 0, k = 1.5, A = 140, 1000
// paths, and γ = 0.1, 0.01 and 1, one table each. Each table starts its own mt19937_64 from the
// same seed, so the tables see the same paths, and one set of draws drives both strategies of a
// table. The output is the raw input of scripts/as_tables_report.py, which writes the results
// file. Nothing here reads market data.
//
// One step, at t = n·dt for n = 0 to T/dt − 1, fixed by prediction P-0201 before any path ran:
//   1. The inventory strategy quotes r ± ψ/2, r = s − qγσ²(T − t), ψ = γσ²(T − t) +
//      (2/γ) ln(1 + γ/k). The symmetric strategy quotes ±ψ̄/2 around s, where ψ̄ is the
//      inventory strategy's average spread over the steps, which no path changes since ψ does
//      not depend on q.
//   2. Each side fills at most once, with probability min(1, A e^(−kδ) dt) at its distance δ
//      from s, on its own uniform draw: the bid buys one share at its price, the ask sells one.
//   3. s moves +σ√dt or −σ√dt with probability one half each.
// The profit is X_T + q_T S_T, and the tables give its mean and standard deviation over the
// paths, and those of q_T. Standard deviations divide by N − 1.
//
// Exit codes: 0 done, 2 bad usage.
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <print>
#include <random>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "itch/mm/quoting.hpp"

namespace {

struct Settings {
  double s0 = 100.0;
  double horizon = 1.0;
  double sigma = 2.0;
  double dt = 0.005;
  double k = 1.5;
  double a = 140.0;
  int paths = 1000;
  std::uint64_t seed = 20'080'401;
  std::vector<double> gammas;
};

// The top 53 bits of a 64-bit draw, as a double in [0, 1): the same on every platform, which
// std::uniform_real_distribution does not promise.
[[nodiscard]] double uniform(std::mt19937_64& random) {
  return static_cast<double>(random() >> 11U) * 0x1.0p-53;
}

// Running mean and the sum of squared deviations (Welford).
class Moments {
 public:
  void add(double x) noexcept {
    ++count_;
    const double delta = x - mean_;
    mean_ += delta / static_cast<double>(count_);
    squares_ += delta * (x - mean_);
  }
  [[nodiscard]] double mean() const noexcept { return mean_; }
  [[nodiscard]] double deviation() const noexcept {
    return count_ < 2 ? 0.0 : std::sqrt(squares_ / static_cast<double>(count_ - 1));
  }

 private:
  std::uint64_t count_ = 0;
  double mean_ = 0;
  double squares_ = 0;
};

struct Book {
  double cash = 0;
  double inventory = 0;
  std::uint64_t fills = 0;
};

// One side's fill: probability min(1, A e^(−kδ) dt).
[[nodiscard]] bool fills(double depth, double draw, const Settings& settings) noexcept {
  const double probability = settings.a * std::exp(-settings.k * depth) * settings.dt;
  return draw < (probability < 1.0 ? probability : 1.0);
}

void trade(Book& book, double bid, double ask, double mid, double bid_draw, double ask_draw,
           const Settings& settings) noexcept {
  if (fills(mid - bid, bid_draw, settings)) {
    book.cash -= bid;
    book.inventory += 1.0;
    ++book.fills;
  }
  if (fills(ask - mid, ask_draw, settings)) {
    book.cash += ask;
    book.inventory -= 1.0;
    ++book.fills;
  }
}

struct Strategy {
  Moments profit;
  Moments final_inventory;
  std::uint64_t fills = 0;
};

void print(std::string_view name, const Strategy& strategy, int paths) {
  std::println(
      "strategy {} profit_mean {:.6f} profit_std {:.6f} final_q_mean {:.6f} "
      "final_q_std {:.6f} fills_per_path {:.3f}",
      name, strategy.profit.mean(), strategy.profit.deviation(), strategy.final_inventory.mean(),
      strategy.final_inventory.deviation(),
      static_cast<double>(strategy.fills) / static_cast<double>(paths));
}

void table(const Settings& settings, double gamma) {
  const itch::mm::AvellanedaStoikov model{
      {.gamma = gamma, .sigma = settings.sigma, .k = settings.k, .a = settings.a}};
  const auto steps = static_cast<int>(std::lround(settings.horizon / settings.dt));
  const double move = settings.sigma * std::sqrt(settings.dt);

  // ψ̄: the inventory strategy's spread averaged over its steps, the same on every path.
  double total = 0;
  for (int n = 0; n < steps; ++n) {
    total += model.spread(settings.horizon - (n * settings.dt));
  }
  const double average = total / steps;

  std::mt19937_64 random{settings.seed};
  Strategy inventory;
  Strategy symmetric;
  Moments quoted;
  for (int path = 0; path < settings.paths; ++path) {
    double mid = settings.s0;
    Book held;
    Book plain;
    for (int n = 0; n < steps; ++n) {
      const double remaining = settings.horizon - (n * settings.dt);
      const double bid_draw = uniform(random);
      const double ask_draw = uniform(random);
      const double step_draw = uniform(random);
      const itch::mm::Targets quote = model.quote(mid, held.inventory, remaining);
      const double bid = quote.bid.value_or(mid);
      const double ask = quote.ask.value_or(mid);
      quoted.add(ask - bid);
      trade(held, bid, ask, mid, bid_draw, ask_draw, settings);
      trade(plain, mid - (average / 2.0), mid + (average / 2.0), mid, bid_draw, ask_draw, settings);
      mid += step_draw < 0.5 ? move : -move;
    }
    inventory.profit.add(held.cash + (held.inventory * mid));
    inventory.final_inventory.add(held.inventory);
    inventory.fills += held.fills;
    symmetric.profit.add(plain.cash + (plain.inventory * mid));
    symmetric.final_inventory.add(plain.inventory);
    symmetric.fills += plain.fills;
  }

  // The same eq. 30 averaged on three grids: the one simulated, n = 0 to N − 1; one that also
  // quotes at t = T, whose mean T − t is exactly T/2; and n = 1 to N.
  const double rate = gamma * settings.sigma * settings.sigma;
  const double constant = model.spread(0.0);
  std::println("table gamma {}", gamma);
  std::println("spread_average {:.10f}", quoted.mean());
  std::println("spread_grid_simulated {:.10f}", average);
  std::println("spread_grid_with_t_equal_T {:.10f}", (rate * settings.horizon / 2.0) + constant);
  std::println("spread_grid_from_dt {:.10f}",
               (rate * (settings.horizon - (settings.dt * (steps + 1) / 2.0))) + constant);
  print("inventory", inventory, settings.paths);
  print("symmetric", symmetric, settings.paths);
}

int run(std::span<const char* const> args) {
  Settings settings;
  bool valid = true;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string_view arg = args[i];
    if (arg == "--paths" && i + 1 < args.size()) {
      settings.paths = std::stoi(args[++i]);
    } else if (arg == "--seed" && i + 1 < args.size()) {
      settings.seed = std::stoull(args[++i]);
    } else if (arg == "--gamma" && i + 1 < args.size()) {
      settings.gammas.push_back(std::stod(args[++i]));
    } else {
      valid = false;
    }
  }
  if (!valid || settings.paths < 2) {
    std::println(stderr, "usage: itch_as_tables [--paths N] [--seed S] [--gamma G]...");
    return 2;
  }
  if (settings.gammas.empty()) {
    settings.gammas = {0.1, 0.01, 1.0};
  }
  std::println("as_tables paths {} seed {} s {} T {} sigma {} dt {} k {} A {} steps {}",
               settings.paths, settings.seed, settings.s0, settings.horizon, settings.sigma,
               settings.dt, settings.k, settings.a, std::lround(settings.horizon / settings.dt));
  for (const double gamma : settings.gammas) {
    table(settings, gamma);
  }
  return 0;
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
