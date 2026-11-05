// The quoting models: Avellaneda and Stoikov's finite-horizon quotes, the demo, and Guéant,
// Lehalle and Fernandez-Tapia's stationary quotes with an inventory bound, a labelled variant.
//
// Doubles live here and nowhere else. A model turns a mid, an inventory and a time into
// prices in ticks; the strategy (strategy.hpp) rounds them to the tick grid, and from there on
// every price and amount is an integer. Units are ticks and seconds, as GLFT state theirs: σ in
// ticks per √s, k per tick, A per second, γ per tick per lot, and inventory in lots of the
// strategy's order size.
//
// **Avellaneda-Stoikov** (Quantitative Finance 8(3), 2008, eqs. 29 and 30):
//
//     r(s, q, t) = s − qγσ²(T − t)
//     δᵃ + δᵇ    = γσ²(T − t) + (2/γ) ln(1 + γ/k)
//
// quoted symmetrically around r: bid r − spread/2, ask r + spread/2. A and k enter only
// through k; the spread does not depend on q.
//
// **GLFT, stationary** (arXiv 1105.3115v5, Proposition 2 and Theorem 2): with α = (k/2)γσ²
// and η = A(1 + γ/k)^−(1+k/γ), M is the (2Q+1)-square tridiagonal matrix with αq² on the
// diagonal for q from Q to −Q and −η off it, and f⁰ the eigenvector of its smallest
// eigenvalue. Then
//
//     δᵇ(q) = (1/γ) ln(1 + γ/k) + (1/k) ln(f⁰_q / f⁰_{q+1})    for q < Q
//     δᵃ(q) = (1/γ) ln(1 + γ/k) + (1/k) ln(f⁰_q / f⁰_{q−1})    for q > −Q
//
// from the reference price s. There is no bid at q = Q and no ask at q = −Q. The smallest
// eigenvalue comes from a Sturm-sequence bisection, which converges to the last bit; the
// ratios f⁰_{q−1}/f⁰_q from the three-term recurrence run inwards from q = Q, the direction in
// which the ground state grows, so that rounding errors die out. f⁰ is even in q, so δᵃ(q) =
// δᵇ(−q) holds by construction.
#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

#include "itch/mm/fills.hpp"

namespace itch::mm {

struct ModelParameters {
  double gamma = 0;  // Risk aversion, per tick per lot.
  double sigma = 0;  // Volatility of the mid, ticks per √s.
  double k = 0;      // λ(δ) = A e^(−kδ): decay, per tick.
  double a = 0;      // λ(δ) = A e^(−kδ): intensity at δ = 0, per second.
};

// Target prices in ticks, either side possibly absent.
struct Targets {
  std::optional<double> bid;
  std::optional<double> ask;
};

class AvellanedaStoikov {
 public:
  explicit AvellanedaStoikov(const ModelParameters& parameters) noexcept
      : gamma_{parameters.gamma},
        variance_{parameters.sigma * parameters.sigma},
        constant_{(2.0 / parameters.gamma) * std::log1p(parameters.gamma / parameters.k)} {}

  // r = s − qγσ²τ, with τ = T − t in seconds.
  [[nodiscard]] double reservation(double mid, double lots, double remaining) const noexcept {
    return mid - (lots * gamma_ * variance_ * remaining);
  }

  // γσ²τ + (2/γ) ln(1 + γ/k).
  [[nodiscard]] double spread(double remaining) const noexcept {
    return (gamma_ * variance_ * remaining) + constant_;
  }

  [[nodiscard]] Targets quote(double mid, double lots, double remaining) const noexcept {
    const double centre = reservation(mid, lots, remaining);
    const double half = spread(remaining) / 2.0;
    return {.bid = centre - half, .ask = centre + half};
  }

 private:
  double gamma_;
  double variance_;
  double constant_;
};

class GlftStationary {
 public:
  // max_lots is Q, at least 1.
  GlftStationary(const ModelParameters& parameters, std::int64_t max_lots)
      : max_lots_{max_lots < 1 ? 1 : max_lots},
        alpha_{0.5 * parameters.k * parameters.gamma * parameters.sigma * parameters.sigma},
        eta_{parameters.a * std::pow(1.0 + (parameters.gamma / parameters.k),
                                     -(1.0 + (parameters.k / parameters.gamma)))},
        constant_{std::log1p(parameters.gamma / parameters.k) / parameters.gamma},
        k_{parameters.k},
        eigenvalue_{smallest_eigenvalue()},
        ratio_(static_cast<std::size_t>(max_lots_) + 1, 0.0) {
    // ratio_[q] = f⁰_{q−1} / f⁰_q for q from 1 to Q, by the recurrence from row Q inwards.
    const auto last = static_cast<std::size_t>(max_lots_);
    ratio_[last] = (diagonal(max_lots_) - eigenvalue_) / eta_;
    for (std::size_t q = last - 1; q >= 1; --q) {
      const double row = diagonal(static_cast<std::int64_t>(q)) - eigenvalue_;
      ratio_[q] = (row - (eta_ / ratio_[q + 1])) / eta_;
    }
  }

  [[nodiscard]] std::int64_t max_lots() const noexcept { return max_lots_; }
  [[nodiscard]] double alpha() const noexcept { return alpha_; }
  [[nodiscard]] double eta() const noexcept { return eta_; }
  [[nodiscard]] double eigenvalue() const noexcept { return eigenvalue_; }

  // δᵇ(q), none at q ≥ Q.
  [[nodiscard]] std::optional<double> bid_depth(std::int64_t lots) const noexcept {
    if (lots >= max_lots_ || lots < -max_lots_) {
      return std::nullopt;
    }
    // f_q / f_{q+1}: ratio_[q + 1] for q ≥ 0, and by symmetry 1 / ratio_[−q] below.
    const double ratio = lots >= 0 ? ratio_[static_cast<std::size_t>(lots + 1)]
                                   : 1.0 / ratio_[static_cast<std::size_t>(-lots)];
    return constant_ + (std::log(ratio) / k_);
  }

  // δᵃ(q) = δᵇ(−q), none at q ≤ −Q.
  [[nodiscard]] std::optional<double> ask_depth(std::int64_t lots) const noexcept {
    return bid_depth(-lots);
  }

  [[nodiscard]] Targets quote(double mid, std::int64_t lots) const noexcept {
    const std::optional<double> bid = bid_depth(lots);
    const std::optional<double> ask = ask_depth(lots);
    return {.bid = bid ? std::optional<double>{mid - *bid} : std::nullopt,
            .ask = ask ? std::optional<double>{mid + *ask} : std::nullopt};
  }

 private:
  [[nodiscard]] double diagonal(std::int64_t lots) const noexcept {
    const auto q = static_cast<double>(lots);
    return alpha_ * q * q;
  }

  // How many eigenvalues of M lie below x: the negative pivots of M − xI (Sturm).
  [[nodiscard]] std::int64_t below(double x) const noexcept {
    std::int64_t count = 0;
    double pivot = 1.0;
    for (std::int64_t q = -max_lots_; q <= max_lots_; ++q) {
      const double coupling = q == -max_lots_ ? 0.0 : (eta_ * eta_) / pivot;
      pivot = diagonal(q) - x - coupling;
      if (pivot == 0.0) {
        pivot = -1e-300;
      }
      count += pivot < 0.0 ? 1 : 0;
    }
    return count;
  }

  // In [−2η, 0]: Gershgorin's discs bound it below, and the Rayleigh quotient of the unit
  // vector at q = 0 bounds it above.
  [[nodiscard]] double smallest_eigenvalue() const noexcept {
    double low = -2.0 * eta_;
    double high = 0.0;
    for (int step = 0; step < 2'000; ++step) {
      const double middle = 0.5 * (low + high);
      if (middle <= low || middle >= high) {
        break;
      }
      (below(middle) >= 1 ? high : low) = middle;
    }
    return 0.5 * (low + high);
  }

  std::int64_t max_lots_;
  double alpha_;
  double eta_;
  double constant_;
  double k_;
  double eigenvalue_;
  std::vector<double> ratio_;
};

// GLFT's closed-form approximation of the stationary quotes (p. 11), for comparison only:
// δᵇ(q) ≈ (1/γ) ln(1 + γ/k) + ((2q + 1)/2) ω and δᵃ(q) ≈ (1/γ) ln(1 + γ/k) − ((2q − 1)/2) ω,
// with ω = √(σ²γ/(2kA) · (1 + γ/k)^(1 + k/γ)). GLFT warn it fits poorly at large q.
struct GlftApproximation {
  double bid;
  double ask;
};

[[nodiscard]] inline GlftApproximation glft_approximation(const ModelParameters& parameters,
                                                          std::int64_t lots) noexcept {
  const double g = parameters.gamma;
  const double k = parameters.k;
  const double constant = std::log1p(g / k) / g;
  const double omega =
      std::sqrt((parameters.sigma * parameters.sigma * g / (2.0 * k * parameters.a)) *
                std::pow(1.0 + (g / k), 1.0 + (k / g)));
  const auto q = static_cast<double>(lots);
  return {.bid = constant + (((2.0 * q) + 1.0) / 2.0 * omega),
          .ask = constant - (((2.0 * q) - 1.0) / 2.0 * omega)};
}

}  // namespace itch::mm
