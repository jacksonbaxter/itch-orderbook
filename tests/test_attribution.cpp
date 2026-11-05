// The PnL attribution: T-MM-1, property tests 1 to 14 of the research evidence
// (market-maker.md, "Property tests"), and its worked example. Tests 15 and 16 run the
// strategy through the backtester and are in test_market_maker.cpp.
//
// Every check is `==` on integers. Each compares the attribution with a ledger computed from
// cash and position only (ledger.hpp), or the attribution's two forms with each other. The
// random cases put fills and mark changes on a coarse clock, so that a fill often lands on a
// mark change and a window often closes exactly on one: the boundaries are where a form goes
// wrong.
#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <random>
#include <utility>
#include <vector>

#include "itch/book_types.hpp"
#include "itch/fees.hpp"
#include "itch/mm/attribution.hpp"
#include "itch/mm/fills.hpp"
#include "itch/mm/ledger.hpp"

namespace {

using itch::Picodollars;
using itch::Price4;
using itch::mm::Components;
using itch::mm::Fill;
using itch::mm::FillTerms;
using itch::mm::Ledger;
using itch::mm::MarkPath;
using itch::mm::MarkPoint;
using itch::mm::Mid2;
using itch::mm::Nanos;
using itch::mm::Window;

constexpr Nanos kMillisecond = 1'000'000;
constexpr Nanos kSecond = 1'000 * kMillisecond;
constexpr Price4 kTick = 100;
constexpr Picodollars kDollar = 1'000'000'000'000;
constexpr Picodollars kCent = kDollar / 100;
constexpr Picodollars kUnit = itch::mm::kPicodollarsPerMid2;

// The amended Rule 605 grid, plus h = 0 and h past the close.
constexpr std::array<Nanos, 7> kHorizons{
    0, 50 * kMillisecond, kSecond, 15 * kSecond, 60 * kSecond, 300 * kSecond, itch::mm::kPastClose};

struct Case {
  std::vector<Fill> fills;
  std::vector<MarkPoint> marks;
  Window window;
};

// A seeded random case. Times are on a 50 ms clock over ten minutes, so ties with the horizon
// grid are common. The first mark is one tick of that clock before t₀, and sometimes there is
// a mark at t₀ itself and marks after T. Mid2 moves by half a tick or a tick.
class Cases {
 public:
  explicit Cases(std::uint64_t seed) : random_{seed} {}

  Case next() {
    constexpr Nanos kStep = 50 * kMillisecond;
    constexpr Nanos kOpen = 34'200 * kSecond;
    constexpr Nanos kSteps = 12'000;
    Case out;
    out.window = {.open = kOpen,
                  .close = kOpen + (kSteps * kStep),
                  .opening_position = static_cast<std::int64_t>(draw(2'001)) - 1'000};
    Mid2 mid2 = Mid2{200'000};
    out.marks.push_back({.time = kOpen - kStep, .mid2 = mid2});
    Nanos time = draw(2) == 0 ? kOpen : kOpen + kStep;
    const std::uint64_t changes = 1 + draw(60);
    for (std::uint64_t i = 0; i < changes; ++i) {
      mid2 += static_cast<Mid2>(draw(2) == 0 ? -1 : 1) * static_cast<Mid2>(1 + draw(2)) * kTick;
      out.marks.push_back({.time = time, .mid2 = mid2});
      time += kStep * (1 + draw(400));
    }
    const std::uint64_t fills = draw(40);
    std::vector<Nanos> times;
    times.reserve(fills);
    for (std::uint64_t i = 0; i < fills; ++i) {
      times.push_back(kOpen + (kStep * draw(kSteps + 1)));
    }
    std::ranges::sort(times);
    const MarkPath path{out.marks};
    for (const Nanos at : times) {
      const Mid2 before = path.before(at).value_or(mid2);
      const auto near = static_cast<std::int64_t>((before / 2 / kTick) * kTick);
      const std::int64_t offset = (static_cast<std::int64_t>(draw(7)) - 3) * kTick;
      const auto shares = static_cast<std::int64_t>(1 + draw(500));
      out.fills.push_back(
          {.time = at,
           .quantity = draw(2) == 0 ? shares : -shares,
           .price = static_cast<Price4>(near + offset),
           .fee = (static_cast<Picodollars>(draw(7)) - 3) * shares * 1'000'000'000});
    }
    return out;
  }

  std::uint64_t draw(std::uint64_t bound) { return random_() % bound; }

 private:
  std::mt19937_64 random_;
};

// m(t) and m(t−) where the case defines them, as every generated case does.
Mid2 mark_at(const MarkPath& path, Nanos time) {
  const auto mark = path.at(time);
  EXPECT_TRUE(mark.has_value()) << time;
  return mark.value_or(0);
}

Mid2 mark_before(const MarkPath& path, Nanos time) {
  const auto mark = path.before(time);
  EXPECT_TRUE(mark.has_value()) << time;
  return mark.value_or(0);
}

Components attribute(const Case& c, Nanos horizon) {
  const auto result = itch::mm::attribute(c.fills, MarkPath{c.marks}, c.window, horizon);
  EXPECT_TRUE(result.has_value()) << itch::mm::to_string(result.error());
  return result.value_or(Components{});
}

itch::mm::PathSplit by_path(const Case& c, Nanos horizon) {
  const auto result = itch::mm::attribute_by_path(c.fills, MarkPath{c.marks}, c.window, horizon);
  EXPECT_TRUE(result.has_value()) << itch::mm::to_string(result.error());
  return result.value_or(itch::mm::PathSplit{});
}

std::vector<FillTerms> rows(const Case& c, Nanos horizon) {
  const auto result = itch::mm::fill_terms(c.fills, MarkPath{c.marks}, c.window, horizon);
  EXPECT_TRUE(result.has_value());
  return result.value_or(std::vector<FillTerms>{});
}

// Π from cash and position, marked at the path's m₀ and m_T.
Picodollars ledger_total(const std::vector<Fill>& fills, const std::vector<MarkPoint>& marks,
                         const Window& window) {
  const MarkPath path{marks};
  const auto total =
      Ledger::total_of(fills, window.opening_position, path.at(window.open).value_or(0),
                       path.at(window.close).value_or(0));
  EXPECT_TRUE(total.has_value());
  return total.value_or(0);
}

// Test 1's check: the ledger reads one fill log and mark path, the attribution another. In a
// passing run the two are the same; test 14 makes them differ in one item.
bool identity_holds(const Case& for_ledger, const Case& for_attribution, Nanos horizon) {
  const auto components = itch::mm::attribute(
      for_attribution.fills, MarkPath{for_attribution.marks}, for_attribution.window, horizon);
  return components.has_value() &&
         components->total() == ledger_total(for_ledger.fills, for_ledger.marks, for_ledger.window);
}

constexpr int kCases = 2'000;

// ---- 1 to 4: the identity and the two forms ------------------------------------------------

TEST(Attribution, P01SumEqualsLedgerTotalForEveryHorizon) {
  Cases cases{1};
  int checked = 0;
  for (int n = 0; n < kCases; ++n) {
    const Case c = cases.next();
    for (const Nanos horizon : kHorizons) {
      ASSERT_TRUE(identity_holds(c, c, horizon)) << "case " << n << " h " << horizon;
      const Components per_fill = attribute(c, horizon);
      const auto path = by_path(c, horizon);
      ASSERT_EQ(per_fill.spread_capture() + path.adverse_selection + path.inventory,
                ledger_total(c.fills, c.marks, c.window))
          << "case " << n << " h " << horizon;
      ++checked;
    }
  }
  EXPECT_EQ(checked, kCases * static_cast<int>(kHorizons.size()));
}

TEST(Attribution, P02PerFillAndPathFormsAgree) {
  Cases cases{2};
  for (int n = 0; n < kCases; ++n) {
    const Case c = cases.next();
    for (const Nanos horizon : kHorizons) {
      const Components per_fill = attribute(c, horizon);
      const auto path = by_path(c, horizon);
      ASSERT_EQ(per_fill.adverse_selection, path.adverse_selection) << n << " h " << horizon;
      ASSERT_EQ(per_fill.inventory, path.inventory) << n << " h " << horizon;
    }
  }
}

TEST(Attribution, P03TotalSpreadAndPositioningDoNotDependOnTheHorizon) {
  Cases cases{3};
  for (int n = 0; n < kCases; ++n) {
    const Case c = cases.next();
    const Components first = attribute(c, kHorizons.front());
    for (const Nanos horizon : kHorizons) {
      const Components other = attribute(c, horizon);
      ASSERT_EQ(other.total(), first.total()) << n;
      ASSERT_EQ(other.spread_capture(), first.spread_capture()) << n;
      ASSERT_EQ(other.gross_spread, first.gross_spread) << n;
      ASSERT_EQ(other.adverse_selection + other.inventory,
                first.adverse_selection + first.inventory)
          << n;
    }
  }
}

TEST(Attribution, P04PastTheCloseInventoryIsTheOpeningPositionAlone) {
  Cases cases{4};
  for (int n = 0; n < kCases; ++n) {
    const Case c = cases.next();
    const MarkPath path{c.marks};
    const Components past = attribute(c, itch::mm::kPastClose);
    const Picodollars expected = c.window.opening_position *
                                 (mark_at(path, c.window.close) - mark_at(path, c.window.open)) *
                                 kUnit;
    ASSERT_EQ(past.inventory, expected) << n;
  }
}

// ---- 5 to 8: invariances and the published spread identity --------------------------------

TEST(Attribution, P05SplittingAFillChangesNothing) {
  Cases cases{5};
  int split = 0;
  for (int n = 0; n < kCases; ++n) {
    const Case c = cases.next();
    for (std::size_t i = 0; i < c.fills.size(); ++i) {
      const Fill whole = c.fills[i];
      if (whole.quantity > -2 && whole.quantity < 2) {
        continue;
      }
      Case halves = c;
      Fill first = whole;
      Fill second = whole;
      first.quantity = whole.quantity / 2;
      second.quantity = whole.quantity - first.quantity;
      first.fee = whole.fee / 3;
      second.fee = whole.fee - first.fee;
      halves.fills[i] = first;
      halves.fills.insert(halves.fills.begin() + static_cast<std::ptrdiff_t>(i) + 1, second);
      for (const Nanos horizon : kHorizons) {
        ASSERT_EQ(attribute(halves, horizon), attribute(c, horizon)) << n << " fill " << i;
      }
      ++split;
    }
  }
  EXPECT_GT(split, kCases);
}

TEST(Attribution, P06ShiftingEveryPriceAndMarkChangesNothing) {
  Cases cases{6};
  for (int n = 0; n < kCases; ++n) {
    const Case c = cases.next();
    Case shifted = c;
    const auto shift = static_cast<Price4>(1 + cases.draw(10'000));
    for (Fill& fill : shifted.fills) {
      fill.price += shift;
    }
    for (MarkPoint& mark : shifted.marks) {
      mark.mid2 += Mid2{2} * shift;
    }
    for (const Nanos horizon : kHorizons) {
      ASSERT_EQ(attribute(shifted, horizon), attribute(c, horizon)) << n;
    }
  }
}

TEST(Attribution, P07MirroringSidesAndPricesChangesNothing) {
  constexpr Price4 kCentre = 400'000;
  Cases cases{7};
  for (int n = 0; n < kCases; ++n) {
    const Case c = cases.next();
    Case mirror = c;
    mirror.window.opening_position = -c.window.opening_position;
    for (Fill& fill : mirror.fills) {
      fill.quantity = -fill.quantity;
      fill.price = kCentre - fill.price;
    }
    for (MarkPoint& mark : mirror.marks) {
      mark.mid2 = (Mid2{2} * kCentre) - mark.mid2;
    }
    for (const Nanos horizon : kHorizons) {
      ASSERT_EQ(attribute(mirror, horizon), attribute(c, horizon)) << n;
    }
  }
}

// Per share, with D = −sign(q) the taker's direction: effective half-spread D(P − m⁻), realized
// half-spread D(P − mʰ), price impact D(mʰ − m⁻), and effective = realized + impact (Hendershott,
// Jones and Menkveld, eq. 4). Each is worked here from the fill and the marks, and the module's
// rows must be the effective spread and minus the impact, times the shares.
TEST(Attribution, P08EffectiveIsRealizedPlusImpactPerShare) {
  Cases cases{8};
  for (int n = 0; n < kCases; ++n) {
    const Case c = cases.next();
    const MarkPath path{c.marks};
    for (const Nanos horizon : kHorizons) {
      const std::vector<FillTerms> terms = rows(c, horizon);
      ASSERT_EQ(terms.size(), c.fills.size());
      for (std::size_t i = 0; i < c.fills.size(); ++i) {
        const Fill& fill = c.fills[i];
        const Nanos end =
            horizon >= c.window.close - fill.time ? c.window.close : fill.time + horizon;
        const Mid2 before = mark_before(path, fill.time);
        const Mid2 later = mark_at(path, end);
        const Mid2 price2 = Mid2{2} * fill.price;
        const std::int64_t direction = fill.quantity > 0 ? -1 : 1;
        const std::int64_t shares = fill.quantity > 0 ? fill.quantity : -fill.quantity;
        const Picodollars effective = direction * (price2 - before) * kUnit;
        const Picodollars realized = direction * (price2 - later) * kUnit;
        const Picodollars impact = direction * (later - before) * kUnit;
        ASSERT_EQ(effective, realized + impact);
        ASSERT_EQ(terms[i].gross_spread, effective * shares) << n << " fill " << i;
        ASSERT_EQ(terms[i].adverse_selection, -impact * shares) << n << " fill " << i;
        ASSERT_EQ(terms[i].fee, fill.fee);
      }
    }
  }
}

// ---- 9 to 13: cases with a known answer ----------------------------------------------------

TEST(Attribution, P09FlatMarkHasNoAdverseSelectionOrInventory) {
  Cases cases{9};
  for (int n = 0; n < kCases; ++n) {
    Case c = cases.next();
    for (MarkPoint& mark : c.marks) {
      mark.mid2 = Mid2{200'000};
    }
    for (const Nanos horizon : kHorizons) {
      const Components flat = attribute(c, horizon);
      ASSERT_EQ(flat.adverse_selection, 0) << n;
      ASSERT_EQ(flat.inventory, 0) << n;
      ASSERT_EQ(flat.spread_capture(), ledger_total(c.fills, c.marks, c.window)) << n;
    }
  }
}

TEST(Attribution, P10RoundTripEarnsTheFullSpreadAndNothingElse) {
  // Bid $10.00, ask $10.01: buy 100 at the bid, sell 100 at the ask, no fees.
  const std::vector<MarkPoint> marks{{.time = 0, .mid2 = 100'000 + 100'100}};
  const Case c{.fills = {{.time = kSecond, .quantity = 100, .price = 100'000, .fee = 0},
                         {.time = 2 * kSecond, .quantity = -100, .price = 100'100, .fee = 0}},
               .marks = marks,
               .window = {.open = 0, .close = 10 * kSecond, .opening_position = 0}};
  for (const Nanos horizon : kHorizons) {
    const Components trip = attribute(c, horizon);
    EXPECT_EQ(trip.spread_capture(), 100 * kCent);
    EXPECT_EQ(trip.gross_spread, 100 * kCent);
    EXPECT_EQ(trip.adverse_selection, 0);
    EXPECT_EQ(trip.inventory, 0);
    EXPECT_EQ(ledger_total(c.fills, c.marks, c.window), 100 * kCent);
  }
}

// A flat position at T with every window closed: what the mark does after the last window
// closes reaches no component.
TEST(Attribution, P11FlatEndIgnoresTheFinalMark) {
  constexpr Nanos kHorizon = 15 * kSecond;
  Cases cases{11};
  int checked = 0;
  for (int n = 0; n < kCases; ++n) {
    Case c = cases.next();
    std::erase_if(c.fills, [&](const Fill& fill) {
      return fill.time + kHorizon >= c.window.close - kSecond;
    });
    std::int64_t position = c.window.opening_position;
    for (const Fill& fill : c.fills) {
      position += fill.quantity;
    }
    if (position != 0) {
      const Nanos last = c.fills.empty() ? c.window.open : c.fills.back().time;
      c.fills.push_back({.time = last, .quantity = -position, .price = 100'000, .fee = 0});
    }
    Nanos closed = c.window.open;
    for (const Fill& fill : c.fills) {
      closed = std::max(closed, fill.time + kHorizon);
    }
    std::erase_if(c.marks, [&](const MarkPoint& mark) { return mark.time > closed; });
    const Components before = attribute(c, kHorizon);
    const Picodollars total = ledger_total(c.fills, c.marks, c.window);
    Case moved = c;
    moved.marks.push_back({.time = closed + 1, .mid2 = c.marks.back().mid2 + 700});
    moved.marks.push_back({.time = c.window.close, .mid2 = c.marks.back().mid2 - 300});
    ASSERT_EQ(attribute(moved, kHorizon), before) << n;
    ASSERT_EQ(ledger_total(moved.fills, moved.marks, moved.window), total) << n;
    ++checked;
  }
  EXPECT_EQ(checked, kCases);
}

TEST(Attribution, P12AFillWithinTheHorizonOfTheCloseHasNoInventoryTerm) {
  constexpr Nanos kHorizon = 60 * kSecond;
  Cases cases{12};
  int truncated = 0;
  for (int n = 0; n < kCases; ++n) {
    const Case c = cases.next();
    const std::vector<FillTerms> terms = rows(c, kHorizon);
    for (std::size_t i = 0; i < c.fills.size(); ++i) {
      if (c.fills[i].time + kHorizon >= c.window.close) {
        ASSERT_EQ(terms[i].inventory, 0) << n << " fill " << i;
        ++truncated;
      }
    }
  }
  EXPECT_GT(truncated, 100);
}

// The fees of real fills under the 20191230 schedule, with rebates on and off.
TEST(Attribution, P13RebatesOffChangeOnlyTheFeeSubLine) {
  const itch::FeeSchedule* on = itch::find_fee_schedule(20191230);
  ASSERT_NE(on, nullptr);
  itch::FeeSchedule off = *on;
  off.add_credit_tape_ab = 0;
  off.add_credit_tape_c = 0;
  Cases cases{13};
  for (int n = 0; n < kCases; ++n) {
    Case with = cases.next();
    Case without = with;
    Picodollars rebates = 0;
    for (std::size_t i = 0; i < with.fills.size(); ++i) {
      const Fill& fill = with.fills[i];
      const itch::FillTerms terms{
          .tape = itch::Tape::c,
          .liquidity = cases.draw(3) == 0 ? itch::Liquidity::taker : itch::Liquidity::maker,
          .side = fill.quantity > 0 ? itch::Side::bid : itch::Side::ask,
          .price = fill.price,
          .shares = static_cast<std::uint32_t>(fill.quantity > 0 ? fill.quantity : -fill.quantity),
          .regular_hours = true};
      const auto fee_on = itch::fill_fees(*on, terms);
      const auto fee_off = itch::fill_fees(off, terms);
      ASSERT_TRUE(fee_on && fee_off);
      with.fills[i].fee = fee_on->total();
      without.fills[i].fee = fee_off->total();
      rebates += fee_off->total() - fee_on->total();
    }
    for (const Nanos horizon : kHorizons) {
      const Components a = attribute(with, horizon);
      const Components b = attribute(without, horizon);
      ASSERT_EQ(a.gross_spread, b.gross_spread);
      ASSERT_EQ(a.adverse_selection, b.adverse_selection);
      ASSERT_EQ(a.inventory, b.inventory);
      ASSERT_EQ(b.fees - a.fees, rebates);
      ASSERT_EQ(a.total() - b.total(), rebates);
      ASSERT_EQ(ledger_total(with.fills, with.marks, with.window) -
                    ledger_total(without.fills, without.marks, without.window),
                rebates);
    }
  }
}

// ---- 14: the negative test ------------------------------------------------------------------

// Each change below is made to only one of the two computations, and test 1's check must then
// fail. A fill whose own contribution to Π, q(m_T − P) − f, is zero changes neither side when
// deleted, so the deleted fill is one with a non-zero contribution; the same holds for a fee
// of zero, and for the closing mark when nothing is held at T.
TEST(Attribution, P14TheIdentityFailsWhenOneSideLosesAFeeAFillOrAMark) {
  Cases cases{14};
  int fees = 0;
  int fills = 0;
  int marks = 0;
  for (int n = 0; n < kCases / 4; ++n) {
    const Case c = cases.next();
    const MarkPath path{c.marks};
    const Mid2 close = mark_at(path, c.window.close);
    std::int64_t held = c.window.opening_position;
    for (const Fill& fill : c.fills) {
      held += fill.quantity;
    }
    for (const Nanos horizon : kHorizons) {
      ASSERT_TRUE(identity_holds(c, c, horizon));
      for (std::size_t i = 0; i < c.fills.size(); ++i) {
        const Fill& fill = c.fills[i];
        if (fill.fee != 0) {
          Case no_fee = c;
          no_fee.fills[i].fee = 0;
          ASSERT_FALSE(identity_holds(c, no_fee, horizon)) << n;
          ASSERT_FALSE(identity_holds(no_fee, c, horizon)) << n;
          ++fees;
        }
        const Picodollars contribution =
            (fill.quantity * (close - (Mid2{2} * fill.price)) * kUnit) - fill.fee;
        if (contribution != 0) {
          Case no_fill = c;
          no_fill.fills.erase(no_fill.fills.begin() + static_cast<std::ptrdiff_t>(i));
          ASSERT_FALSE(identity_holds(c, no_fill, horizon)) << n;
          ASSERT_FALSE(identity_holds(no_fill, c, horizon)) << n;
          ++fills;
        }
      }
      if (held != 0) {
        // The closing mark a cent higher, in a point of its own at T.
        Case last = c;
        std::erase_if(last.marks,
                      [&](const MarkPoint& mark) { return mark.time == c.window.close; });
        last.marks.push_back({.time = c.window.close, .mid2 = close + 200});
        std::ranges::sort(last.marks, {}, &MarkPoint::time);
        ASSERT_FALSE(identity_holds(c, last, horizon)) << n;
        ASSERT_FALSE(identity_holds(last, c, horizon)) << n;
        ++marks;
      }
    }
  }
  EXPECT_GT(fees, 10'000);
  EXPECT_GT(fills, 10'000);
  EXPECT_GT(marks, 1'000);
}

// ---- The worked example of market-maker.md, "Attribution identity" --------------------------

// $100 stock, h = 5 s, Q₀ = 0, T = 20 s. Rebate $0.002 a share to add, $0.003 to remove.
Case worked_example() {
  const auto price = [](std::int64_t cents) { return static_cast<Price4>(cents * 100); };
  const auto mid2 = [](std::int64_t half_cents) { return Mid2{half_cents} * 100; };
  return {
      .fills = {{.time = 1 * kSecond, .quantity = 100, .price = price(10'000), .fee = -20 * kCent},
                {.time = 3 * kSecond, .quantity = -40, .price = price(10'001), .fee = -8 * kCent},
                {.time = 5 * kSecond, .quantity = -40, .price = price(10'001), .fee = -8 * kCent},
                {.time = 9 * kSecond, .quantity = 100, .price = price(9'998), .fee = -20 * kCent},
                {.time = 10 * kSecond, .quantity = -60, .price = price(9'998), .fee = 18 * kCent}},
      .marks = {{.time = 0, .mid2 = mid2(20'001)},
                {.time = 1 * kSecond, .mid2 = mid2(20'000)},
                {.time = 4 * kSecond, .mid2 = mid2(20'001)},
                {.time = 6 * kSecond, .mid2 = mid2(19'999)},
                {.time = 9 * kSecond, .mid2 = mid2(19'997)},
                {.time = 12 * kSecond, .mid2 = mid2(19'998)},
                {.time = 20 * kSecond, .mid2 = mid2(19'999)}},
      .window = {.open = 0, .close = 20 * kSecond, .opening_position = 0}};
}

TEST(Attribution, WorkedExampleMatchesTheEvidenceTable) {
  const Case c = worked_example();
  const Components five = attribute(c, 5 * kSecond);
  EXPECT_EQ(five.gross_spread, 230 * kCent);
  EXPECT_EQ(five.fees, -38 * kCent);
  EXPECT_EQ(five.spread_capture(), 268 * kCent);
  EXPECT_EQ(five.adverse_selection, -80 * kCent);
  EXPECT_EQ(five.inventory, -20 * kCent);
  EXPECT_EQ(five.total(), 168 * kCent);
  EXPECT_EQ(ledger_total(c.fills, c.marks, c.window), 168 * kCent);
  struct Row {
    Nanos horizon;
    Picodollars adverse;
    Picodollars inventory;
  };
  for (const Row row :
       {Row{.horizon = 0, .adverse = -150 * kCent, .inventory = 50 * kCent},
        Row{.horizon = kSecond, .adverse = -130 * kCent, .inventory = 30 * kCent},
        Row{.horizon = 5 * kSecond, .adverse = -80 * kCent, .inventory = -20 * kCent},
        Row{.horizon = 15 * kSecond, .adverse = -130 * kCent, .inventory = 30 * kCent},
        Row{.horizon = itch::mm::kPastClose, .adverse = -100 * kCent, .inventory = 0}}) {
    const Components at = attribute(c, row.horizon);
    EXPECT_EQ(at.spread_capture(), 268 * kCent) << row.horizon;
    EXPECT_EQ(at.adverse_selection, row.adverse) << row.horizon;
    EXPECT_EQ(at.inventory, row.inventory) << row.horizon;
    const auto path = by_path(c, row.horizon);
    EXPECT_EQ(path.adverse_selection, row.adverse) << row.horizon;
    EXPECT_EQ(path.inventory, row.inventory) << row.horizon;
  }
  const std::vector<FillTerms> terms = rows(c, 5 * kSecond);
  ASSERT_EQ(terms.size(), 5U);
  EXPECT_EQ(terms[0], (FillTerms{.gross_spread = 50 * kCent,
                                 .fee = -20 * kCent,
                                 .adverse_selection = -100 * kCent,
                                 .inventory = 0}));
  EXPECT_EQ(terms[4], (FillTerms{.gross_spread = -30 * kCent,
                                 .fee = 18 * kCent,
                                 .adverse_selection = -30 * kCent,
                                 .inventory = -30 * kCent}));
}

// The identity telescopes: each fill's three terms sum to q(m_T − P) − f whatever the marks
// between, so a mark changed inside the session, in only one computation, is invisible to
// test 1 by construction. The split is what such a change moves, and test 2 catches it when
// the change is made to only one of the two forms. Here the mark at 6 s, inside the 5 s window
// of the fill at 5 s, is a cent higher for the path form only.
TEST(Attribution, P14AMarkInsideTheSessionIsCaughtByTheTwoFormsNotBySum) {
  const Case c = worked_example();
  Case moved = c;
  moved.marks[3].mid2 += 200;  // The mark at 6 s, a cent higher.
  constexpr Nanos kHorizon = 5 * kSecond;
  EXPECT_TRUE(identity_holds(c, moved, kHorizon));
  const Components per_fill = attribute(c, kHorizon);
  const auto path = by_path(moved, kHorizon);
  EXPECT_NE(per_fill.adverse_selection, path.adverse_selection);
  EXPECT_NE(per_fill.inventory, path.inventory);
}

// ---- What the module refuses ---------------------------------------------------------------

TEST(Attribution, RefusesInputItCannotMark) {
  const Case c = worked_example();
  const MarkPath path{c.marks};
  Case early = c;
  early.fills.front().time = 0;  // No mark strictly before t = 0.
  EXPECT_EQ(itch::mm::attribute(early.fills, path, early.window, kSecond).error(),
            itch::mm::AttributionError::mark_undefined);
  Case late = c;
  late.fills.back().time = 21 * kSecond;
  EXPECT_EQ(itch::mm::attribute(late.fills, path, late.window, kSecond).error(),
            itch::mm::AttributionError::fill_outside_window);
  Case unordered = c;
  std::swap(unordered.fills[0], unordered.fills[1]);
  EXPECT_EQ(itch::mm::attribute_by_path(unordered.fills, path, unordered.window, kSecond).error(),
            itch::mm::AttributionError::fills_out_of_order);
  Case huge = c;
  huge.fills[0].quantity = itch::mm::kMaxQuantity + 1;
  EXPECT_EQ(itch::mm::attribute(huge.fills, path, huge.window, kSecond).error(),
            itch::mm::AttributionError::quantity_too_large);
  std::vector<MarkPoint> repeated = c.marks;
  repeated[2].time = repeated[1].time;
  EXPECT_EQ(itch::mm::attribute(c.fills, MarkPath{repeated}, c.window, kSecond).error(),
            itch::mm::AttributionError::marks_out_of_order);
}

}  // namespace
