#!/usr/bin/env python3
"""Tests for types_report.py, on records invented here: blocks whose ticks are each type's
cost times its count, so the fit must give every cost back exactly; what it refuses; and that
--check catches a published file that says more than its passes and block sums."""

from __future__ import annotations

import copy
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import types_report  # noqa: E402

COLUMNS = types_report.COLUMNS
N = len(COLUMNS)
MAC = types_report.MAC
# Ticks a message of each column costs: A, F, D, U, E, C, X, P, I, L, everything else.
COST = [40, 44, 30, 60, 35, 37, 33, 12, 6, 7, 5]
LOOKUP = {"A": 8, "F": 8, "D": 9, "U": 17, "E": 9, "C": 9, "X": 9}
NS_PER_TICK = 41.666666667
BLOCK = 8
START = "measure.sh: start load1=1.10 host=box at=2026-11-10T09:{n:02d}:00Z"
END = "measure.sh: end load1=1.20 exit=0 at=2026-11-10T09:{n:02d}:30Z"


def quiet_sampler(foreign_at=None):
    lines = ["2026-11-10T08:59:58Z sample"]
    for second in range(0, 1200, 2):
        at = f"2026-11-10T09:{second // 60:02d}:{second % 60:02d}Z"
        lines.append(f"{at} sample")
        if foreign_at == second:
            lines.append(f"{at} FOREIGN 93.6 /somewhere/mds_stores")
    return "\n".join(lines) + "\n"


def blocks(seed=7, count=400, quarter_cost=None):
    """Blocks of BLOCK messages, each with a random mix, as (quarter, counts)."""
    rng = random.Random(seed)
    out = []
    for number in range(count):
        counts = [0] * N
        for _ in range(BLOCK):
            counts[rng.choice(range(N))] += 1
        out.append((number * 4 // count, counts))
    return out


def sums_of(mix, cost, extra=None, quarter_cost=None):
    """A typed pass's quarters, every block's ticks its counts times `cost`."""
    quarters = []
    for _ in range(4):
        quarters.append({"blocks": 0, "ticks": 0, "ticks_squared": 0, "under_floor": 0,
                         "most_ticks": 0, "counts": [0] * N, "cross": [0] * N,
                         "gram": [0] * (N * N)})
    for quarter, counts in mix:
        per = list(cost)
        if extra:
            for name, ticks in extra.items():
                per[COLUMNS.index(name)] += ticks
        if quarter_cost and quarter in quarter_cost:
            for name, ticks in quarter_cost[quarter].items():
                per[COLUMNS.index(name)] = ticks
        ticks = sum(c * t for c, t in zip(counts, per))
        q = quarters[quarter]
        q["blocks"] += 1
        q["ticks"] += ticks
        q["ticks_squared"] += ticks * ticks
        q["most_ticks"] = max(q["most_ticks"], ticks)
        for i in range(N):
            q["counts"][i] += counts[i]
            q["cross"][i] += counts[i] * ticks
            for j in range(N):
                q["gram"][i * N + j] += counts[i] * counts[j]
    whole = sum(q["ticks"] for q in quarters)
    return {"block": BLOCK, "columns": COLUMNS, "first": 1000, "last": 1000 + whole,
            "frames": sum(sum(c) for _, c in mix), "quarters": quarters}


def header():
    return {"host": MAC, "cpu": "Apple M4 Max", "kernel": "Darwin 27.2.0",
            "compiler": "Clang 23", "flags": "-O3 -DNDEBUG -std=c++23",
            "commit": "0123456789ab (clean)", "dataset": "12302019.NASDAQ_ITCH50",
            "pinning": "not pinned: macOS has no thread affinity",
            "governor": "unavailable: macOS", "design": "paged-flat: FastBook",
            "input": "mapped"}


def record(kind, mix, quarter_cost=None, refusals=None):
    messages = sum(sum(c) for _, c in mix)
    types = {}
    for _, counts in mix:
        for i, name in enumerate(COLUMNS):
            key = "S" if name == "*" else name
            types[key] = types.get(key, 0) + counts[i]
    modes = ("replay", "typed", "typed", "replay") if kind == "types" else (
        "check", "answer", "answer", "check")
    passes = []
    for mode in modes:
        typed = None
        if mode == "typed" or mode == "answer":
            typed = sums_of(mix, COST, quarter_cost=quarter_cost)
        elif mode == "check":
            typed = sums_of(mix, COST, extra=LOOKUP, quarter_cost=quarter_cost)
        ticks = typed["last"] - typed["first"] if typed else sum(
            sum(c * t for c, t in zip(counts, COST)) for _, counts in mix) - messages
        passes.append({"mode": mode, "messages": messages, "seconds": ticks * NS_PER_TICK / 1e9,
                       "rejected": 0, "fold": 0, "lookups": 0 if kind == "types" else 2 * messages,
                       "mismatches": 0, "script_exact": True, "live_orders": 5, "live_levels": 3,
                       "residency_before": "", "residency_after": "", "counters": [],
                       **({"typed": typed} if typed else {})})
    return {"header": header(),
            "variant": {"pass": kind, "design": "paged-flat", "dispatch": "switch", "prefetch": 0,
                        "threaded_built": True, "block": BLOCK, "first_messages": 0},
            "mix": {"messages": messages, "book_messages": messages}, "warmup_messages": messages,
            "input": {"frames": messages, "bytes": 10, "file_bytes": 10, "types": types},
            "passes": passes, "pinned": False, "migrations": 0,
            "peak_rss_bytes": 9 * 1024 ** 3, "ns_per_tick": NS_PER_TICK,
            "refusals": refusals or []}


def stderr(n):
    return START.format(n=n) + "\n" + END.format(n=n) + "\n"


def render(records, sampler=None):
    return types_report.render(records, [stderr(i) for i in range(len(records))], "P-0801",
                               [], [], sampler or quiet_sampler())


def standard(quarter_cost=None):
    mix = blocks()
    return [record("types", mix, quarter_cost), record("lookup", mix, quarter_cost),
            record("types", mix, quarter_cost)]


class TypesReportTest(unittest.TestCase):
    def test_the_fit_gives_each_types_cost_back_exactly(self):
        one = sums_of(blocks(), COST)
        got = types_report.fit(types_report.summed(
            [{**q, "gram": types_report.upper_of(q["gram"])} for q in one["quarters"]]), 1.0)
        self.assertEqual(got["ns"], [float(c) for c in COST])
        self.assertAlmostEqual(got["r2"], 1.0)
        self.assertAlmostEqual(got["fitted"], 1.0)

    def test_lookups_are_the_check_fit_less_the_answer_fit_by_type(self):
        text, _, _ = render(standard())
        figures = types_report.figures_of(types_report.parse_passes(text),
                                          types_report.parse_sums(text))
        for name, ticks in LOOKUP.items():
            self.assertAlmostEqual(figures["columns"][name]["lookup"], ticks * NS_PER_TICK, 6)
            self.assertAlmostEqual(figures["columns"][name]["ns"],
                                   COST[COLUMNS.index(name)] * NS_PER_TICK, 6)
        self.assertIsNone(figures["columns"]["P"]["lookup"])

    def test_a_quarter_whose_cost_moves_shows_it(self):
        text, _, _ = render(standard(quarter_cost={1: {"U": 90}}))
        figures = types_report.figures_of(types_report.parse_passes(text),
                                          types_report.parse_sums(text))
        quarters = figures["columns"]["U"]["quarters"]
        self.assertAlmostEqual(quarters[0], 60 * NS_PER_TICK, 6)
        self.assertAlmostEqual(quarters[1], 90 * NS_PER_TICK, 6)

    def test_a_published_file_passes_its_own_check(self):
        text, run_id, summary = render(standard())
        self.assertEqual(types_report.check_published(text, "types_mac_12302019.md"), [])
        self.assertTrue(run_id.startswith("P-0801-20261110T0900"))
        self.assertTrue(summary.startswith("U 2500.00 ns, D 1250.00 ns, A 1666.67 ns"))

    def test_check_catches_a_figure_the_sums_do_not_give(self):
        text, _, _ = render(standard())
        row = next(line for line in text.splitlines() if line.startswith("| `U` | replace"))
        cells = row.split(" | ")
        cells[4] = "2,400.00"
        tampered = text.replace(row, " | ".join(cells))
        codes = [code for code, _ in types_report.check_published(tampered,
                                                                    "types_mac_12302019.md")]
        self.assertIn("results-differ", codes)

    def test_check_catches_block_sums_that_were_changed(self):
        text, _, _ = render(standard())
        lines = text.splitlines()
        at = next(i for i, line in enumerate(lines) if line.startswith('{"variant":"types"'))
        one = json.loads(lines[at])
        one["quarters"][0]["cross"][COLUMNS.index("U")] += 10 ** 6
        lines[at] = json.dumps(one, separators=(",", ":"))
        codes = [code for code, _ in types_report.check_published("\n".join(lines),
                                                                    "types_mac_12302019.md")]
        self.assertIn("results-differ", codes)

    def test_a_file_named_for_another_host_fails(self):
        text, _, _ = render(standard())
        codes = [code for code, _ in types_report.check_published(text, "types_debian_1.md")]
        self.assertIn("host-label", codes)

    def test_a_busy_mac_withdraws_the_process(self):
        with self.assertRaises(types_report.variants_report.ReportError):
            render(standard(), quiet_sampler(foreign_at=10))

    def test_a_refused_record_is_not_published(self):
        records = standard()
        records[0]["refusals"] = ["input not resident"]
        with self.assertRaises(types_report.ReportError):
            render(records)

    def test_blocks_that_do_not_tile_their_pass_are_refused(self):
        records = standard()
        records[0]["passes"][1]["typed"]["last"] += 1
        with self.assertRaisesRegex(types_report.ReportError, "tile"):
            render(records)

    def test_processes_on_different_inputs_are_refused(self):
        records = standard()
        records[2]["variant"]["first_messages"] = 100
        with self.assertRaises(types_report.ReportError):
            render(records)

    def test_a_slice_shows_the_whole_days_pinned_counts(self):
        records = standard()
        for one in records:
            one["variant"]["first_messages"] = one["mix"]["messages"]
        text, _, _ = render(records)
        self.assertIn("| The whole day | Share |", text)
        self.assertIn("| `A` |", text)
        self.assertIn("117,145,568", text)
        self.assertEqual(types_report.check_published(text, "types_mac_12302019.md"), [])
        changed = text.replace("117,145,568", "117,145,569")
        codes = [code for code, _ in types_report.check_published(changed,
                                                                    "types_mac_12302019.md")]
        self.assertIn("day-mix-differs", codes)

    def test_main_writes_the_file_and_its_run_log_row(self):
        records = standard()
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            args = ["--prediction", "P-0801", "--out", str(root / "types_mac_12302019.md"),
                    "--run-log", str(root / "run_log.md"), "--sampler", str(root / "s.txt"),
                    "--record"]
            (root / "s.txt").write_text(quiet_sampler())
            for i, one in enumerate(records):
                (root / f"r{i}.json").write_text(json.dumps(one))
                (root / f"r{i}.err").write_text(stderr(i))
            args += [str(root / f"r{i}.json") for i in range(len(records))]
            args += ["--load"] + [str(root / f"r{i}.err") for i in range(len(records))]
            self.assertEqual(types_report.main(args), 0)
            self.assertIn("| P-0801 | 0123456789ab (clean) | Darwin | published: U ",
                          (root / "run_log.md").read_text())
            self.assertEqual(types_report.main(["--check", str(root / "types_mac_12302019.md")]),
                             0)


if __name__ == "__main__":
    unittest.main()
