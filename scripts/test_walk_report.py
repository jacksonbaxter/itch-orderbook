#!/usr/bin/env python3
"""walk_report.py's counts and ladder files, walk_driver.py, and check_results.py on a ladder.

The good files are rendered here from made-up bench_walks and bench_backtest records, the
ladder through walk_driver.render() and stamped from the committed measure.sh fixture, so
the checks run on what the scripts really write. Each case breaks one thing and requires
that code. The end-to-end cases run the driver with a fake bench_backtest, a fake
bench_walks and the fake probe, so they need macOS, where machine_check records pinning
rather than enforcing it.
"""

import argparse
import contextlib
import copy
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402
import stamp_load  # noqa: E402
import walk_driver  # noqa: E402
import walk_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
STDERR = (FIXTURES / "bench_results" / "measure.stderr").read_text(encoding="utf-8")
MACHINE = "machine_check: pass input-resident: 1 of 1 pages\nmachine_check: passed"
MESSAGES = 5_425_845
LIVES = (60, 600, 3600)
HEADER = {
    "host": "debian", "cpu": "AMD Ryzen Embedded V1605B with Radeon Vega Gfx",
    "kernel": "Linux 6.12.48+deb13-amd64", "compiler": "Clang 23.1.4",
    "flags": "-O3 -DNDEBUG -std=c++23", "commit": "abcdef012345 (clean)",
    "dataset": "12302019.NASDAQ_ITCH50", "dataset_sha256": "00",
    "timer": "lfence;rdtsc and rdtscp;lfence, calibrated 1996.871 MHz",
    "quantum": "20 ticks, 10.016 ns", "pair": "0.128 ticks, 5.338 ns, not subtracted",
    "floor": "40.063 ns", "pinning": "pinned to core 4: pthread_setaffinity_np returned 0, read "
    "back [4]", "migrations": "0 seen in 3 samples", "core": "4", "governor": "performance",
    "frequency": "3500000 kHz", "turbo": "on", "smt": "on", "isolation": "none",
    "counters": "unavailable: bench_backtest reads no counters",
    "rdpmc": "unavailable: bench_backtest reads no counters",
    "page_size": "4096 bytes, base pages", "residency": "1325 of 1325 pages resident"}


def event_hash(life):
    return f"{life:016x}"


def count_record(life, orders=100_000):
    """A bench_walks record whose sides are deeper the longer the life."""
    depth = 1 + life // 600
    sites = {}
    for index, name in enumerate(walk_report.SITES):
        walks = 10_000 + index
        sites[name] = {"walks": walks, "nonempty": walks - 10, "level_steps": depth * (walks - 10),
                       "order_steps": 3 * index, "orders_on_side": 0,
                       "depth": [[0, 10], [depth + 1, walks - 10]],
                       "steps": [[0, 10], [depth, walks - 10]]}
    return {"header": {name: HEADER[name] for name in ("host", "cpu", "kernel", "compiler",
                                                       "flags", "commit", "dataset",
                                                       "dataset_sha256")},
            "symbols": "AAPL MSFT SPY TSLA", "date": 20191230, "shadow_orders": orders,
            "shadow_span_ns": 23_340_000_000_000, "shadow_life_ns": life * 1_000_000_000,
            "input_messages": 268_744_780,
            "engine": {"messages": MESSAGES, "deliveries": MESSAGES, "arrivals": 200_000,
                       "reports": 331_519, "wakes": 200_000, "fills": 131_519,
                       "crossing_fills": 61_990, "expired": 0, "placed": 100_000, "skipped": 0,
                       "cancels": 100_000, "event_hash": event_hash(life)},
            "resting_after_message": {"sum": 86 * MESSAGES * depth, "max": 191 * depth},
            "identical": True, "consistent": True, "empty_at_end": True, "sites": sites,
            "peak_rss_bytes": 9_300_000_000, "refusals": []}


def timed_record(life, seconds):
    """A bench_backtest record of the shadow load at this life."""
    timed = {"ns": seconds * 1e9, "messages": MESSAGES, "deliveries": MESSAGES,
             "arrivals": 200_000, "decisions": 200_000, "reports": 331_519, "wakes": 200_000,
             "fills": 131_519, "filled_shares": 7_531_024, "maker_fills": 131_519,
             "taker_fills": 0, "crossing_fills": 61_990, "queue_anomalies": 0,
             "late_adds": 52_373, "late_adds_ahead": 44, "hidden_prints_at_sim_price": 7_109,
             "rejected": 0, "expired": 0, "after_end": 0, "no_order_slot": 0,
             "refused_messages": 0, "view_refused": 0, "out_of_order": 0, "overflows": 0,
             "fee_errors": 0, "strategy_events": MESSAGES, "sends": 100_000, "replaces": 0,
             "cancels": 100_000, "skipped": 0, "strategy_fills": 131_519,
             "event_hash": event_hash(life)}
    return {"header": dict(HEADER), "workload": "shadow", "symbols": "AAPL MSFT SPY TSLA",
            "input": "extract: the messages of AAPL MSFT SPY TSLA", "date": 20191230,
            "shadow_orders": 100_000, "shadow_span_ns": 23_340_000_000_000,
            "shadow_life_ns": life * 1_000_000_000,
            "latency_ns": {"market_data": 0, "order_entry": 0, "response": 0, "compute": 0},
            "input_messages": 268_744_780, "residency": ["1 of 1 pages resident"] * 3,
            "migrations": 0, "extract": {"messages": MESSAGES, "bytes": 1, "ns": 3e9},
            "peak_rss_bytes": 9_300_000_000, "warmup": dict(timed, ns=seconds * 1.1e9),
            "timed": timed, "refusals": []}


COUNTS = walk_report.render_counts([count_record(life) for life in LIVES], "python3 x")


def ladder(seconds=None):
    """A ladder file as walk_driver writes it, stamped."""
    seconds = seconds or {60: (1.60, 1.61, 1.62), 600: (1.65, 1.66, 1.64),
                          3600: (1.70, 1.69, 1.72)}
    counts = {life: count_record(life) for life in LIVES}
    timed = [(index + 1, life, timed_record(life, seconds[life][index]))
             for index in range(3) for life in LIVES]
    args = argparse.Namespace(orders=100_000, prediction="P-0999", repetitions=3)
    text = walk_driver.render(args, list(LIVES), counts, timed, "P-0999-x",
                              "2026-10-31T10:00:01Z", "2026-10-31T10:00:09Z", MACHINE,
                              "python3 -B scripts/walk_driver.py")
    return stamp_load.stamp(text, STDERR)


LADDER = ladder()


def codes(text):
    return {code for code, _ in walk_report.check_text(text)}


def replace(text, old, new, count=1):
    assert old in text, old
    return text.replace(old, new, count)


def row(text, pattern):
    return re.search(pattern, text, re.MULTILINE).group(0)


class Counts(unittest.TestCase):
    def test_the_rendered_file_passes(self):
        self.assertEqual(walk_report.check_text(COUNTS), [])

    def test_t_bt_3_is_named(self):
        self.assertIn("| T-BT-3 | 100,000 |", COUNTS)
        self.assertIn("| 100,000 orders, 600 s life | 100,000 |", COUNTS)

    def test_a_total_that_is_not_its_sites_sum(self):
        line = row(COUNTS, r"^\| T-BT-3 \| all sites \| [0-9,]+ ")
        self.assertIn("total-not-sum", codes(replace(COUNTS, line, line.replace(
            "| all sites | 60,015 ", "| all sites | 60,016 "))))

    def test_a_figure_a_message_off_its_total(self):
        line = row(COUNTS, r"^\| T-BT-3 \| 0\.011 \| .*$")
        self.assertIn("message-figure", codes(replace(COUNTS, line, line.replace(
            "| 0.011 |", "| 0.012 |", 1))))

    def test_a_depth_column_that_is_not_its_walks(self):
        self.assertIn("depth-not-walks", codes(replace(COUNTS, "| 0 | 60 |", "| 0 | 61 |")))

    def test_a_percentile_in_the_wrong_bucket(self):
        line = row(COUNTS, r"^\| Median \| .*$")
        self.assertIn("depth-percentile", codes(replace(COUNTS, line, line.replace(
            "| 2-3 |", "| 1 |", 1))))

    def test_a_deepest_outside_the_top_bucket(self):
        line = row(COUNTS, r"^\| Deepest \| .*$")
        self.assertIn("depth-deepest", codes(replace(COUNTS, line, "| Deepest | 2 | 3 | 99 |")))

    def test_a_stale_summary(self):
        self.assertIn("summary-stale", codes(replace(COUNTS, "- summary: T-BT-3: 0.011",
                                                     "- summary: T-BT-3: 0.012")))

    def test_a_schedule_missing_from_the_message_table(self):
        line = row(COUNTS, r"^\| 100,000 orders, 600 s life \| 0\.011 \| .*$") + "\n"
        self.assertIn("schedules-differ", codes(replace(COUNTS, line, "")))

    def test_a_site_dropped(self):
        line = row(COUNTS, r"^\| T-BT-3 \| late add \| .*$") + "\n"
        self.assertIn("sites-missing", codes(replace(COUNTS, line, "")))

    def test_a_missing_hash(self):
        self.assertIn("hash-missing", codes(replace(COUNTS, event_hash(60), "none")))

    def test_another_title(self):
        self.assertIn("title", codes(replace(COUNTS, "# The walks", "# Some walks")))

    def test_a_home_path(self):
        self.assertIn("private", codes(replace(COUNTS, "python3 x", "python3 /" + "Users" + "/someone/x")))

    def test_records_the_counted_engine_did_not_match_are_refused(self):
        for flag in ("identical", "consistent", "empty_at_end"):
            broken = count_record(60)
            broken[flag] = False
            with self.assertRaises(walk_report.Refused):
                walk_report.validate_record(broken)
        refused = count_record(60)
        refused["refusals"] = ["the counted engine and the engine disagree"]
        with self.assertRaises(walk_report.Refused):
            walk_report.validate_record(refused)

    def test_percentiles_are_nearest_rank(self):
        self.assertEqual(walk_report.percentile_bucket([1, 1], 50), 0)
        self.assertEqual(walk_report.percentile_bucket([1, 1], 51), 1)
        self.assertEqual(walk_report.percentile_bucket([9, 1], 90), 0)
        self.assertEqual(walk_report.percentile_bucket([9, 1], 91), 1)

    def test_buckets(self):
        self.assertEqual([walk_report.bucket_of(depth) for depth in (0, 1, 2, 3, 4, 7, 8)],
                         [0, 1, 2, 2, 3, 3, 4])
        self.assertEqual(walk_report.bucket_label(4), "8-15")


class Ladder(unittest.TestCase):
    def test_the_rendered_file_passes_both_checks(self):
        self.assertEqual(walk_report.check_text(LADDER), [])
        self.assertEqual(check_results.check_text(LADDER), [])

    def test_the_line_is_least_squares(self):
        slope, intercept = walk_report.least_squares([(1.0, 3.0), (2.0, 5.0), (3.0, 7.0)])
        self.assertAlmostEqual(slope, 2.0)
        self.assertAlmostEqual(intercept, 1.0)

    def test_check_results_hands_a_ladder_to_this_check(self):
        broken = replace(LADDER, "- slope: ", "- slope: 9")
        self.assertIn("line-stale", {code for code, _ in check_results.check_text(broken)})

    def test_the_command_shows_the_core_and_the_sibling(self):
        args = walk_driver.parse_args(
            ["--bench", "b", "--walks", "w", "--probe", "p", "--input", "i", "--date", "20191230",
             "--prediction", "P-0999", "--raw-dir", "r", "--out", "results/x.md", "--core", "4",
             "--sibling", "5", "--wrapped-by", "measure.sh taskset -c 4"])
        command = walk_driver.relative_command(args)
        self.assertTrue(command.startswith("measure.sh taskset -c 4 python3 -B"), command)
        self.assertTrue(command.endswith(" --core 4 --sibling 5"), command)

    def test_a_sibling_read_but_not_asked_for(self):
        broken = replace(LADDER, "machine_check: passed",
                         "machine_check: pass sibling-idle: cpu5 is 0.0% busy over 1 s\n"
                         "machine_check: passed")
        self.assertIn("command-differs",
                      {code for code, _ in check_results.check_text(broken)})

    def test_the_generic_rules_still_apply(self):
        broken = replace(LADDER, "machine_check: passed", "machine_check: refused")
        self.assertIn("machine-check-failed", {code for code, _ in check_results.check_text(broken)})

    def test_a_repetition_with_another_hash(self):
        line = row(LADDER, r"^\| 1 \| T-BT-3 \| .*$")
        self.assertIn("repetition-hash", codes(replace(LADDER, line, line.replace(
            event_hash(60), event_hash(61)))))

    def test_a_summary_off_its_repetitions(self):
        line = row(LADDER, r"^\| T-BT-3 \| [0-9.]+ \| [0-9.]+ \| [0-9.]+ \| [0-9.]+% \|$")
        median = line.split(" | ")[1]
        self.assertIn("summary-figure", codes(replace(LADDER, line, line.replace(
            f"| {median} |", "| 1.00 |", 1))))

    def test_a_repetition_dropped(self):
        line = row(LADDER, r"^\| 3 \| T-BT-3 \| .*$") + "\n"
        broken = codes(replace(LADDER, line, ""))
        self.assertTrue({"repetitions-few", "summary-figure"} & broken, broken)

    def test_a_share_off_the_line(self):
        line = row(LADDER, r"^\| T-BT-3 \| [0-9.]+ \| [0-9.]+ \| [0-9.]+ \| [0-9.]+ \| .*%.*$")
        share = line.split(" | ")[-1].rstrip(" |")
        self.assertIn("share-figure", codes(replace(LADDER, line, line.replace(share, "50.00%"))))

    def test_a_stale_slope(self):
        self.assertIn("line-stale", codes(replace(LADDER, "- slope: ", "- slope: 1")))

    def test_a_stale_summary(self):
        self.assertIn("summary-stale", codes(replace(LADDER, "- summary: ", "- summary: x")))

    def test_a_round_line_off_its_repetitions(self):
        line = row(LADDER, r"^\| 2 \| -?[0-9.]+ \| -?[0-9.]+ \|$")
        self.assertIn("round-line", codes(replace(LADDER, line, "| 2 | 1.000 | 1.00 |")))

    def test_a_stale_round_range(self):
        self.assertIn("line-stale", codes(replace(LADDER, "- round_slopes: ", "- round_slopes: 9")))

    def test_the_slope_follows_the_times(self):
        flat = ladder({60: (1.6, 1.6, 1.6), 600: (1.6, 1.6, 1.6), 3600: (1.6, 1.6, 1.6)})
        self.assertIn("- slope: 0.000 ns a walk step", flat)
        self.assertIn("- slope: ", LADDER)
        self.assertNotIn("- slope: 0.000 ", LADDER)

    def test_lives_must_rise_and_be_three(self):
        base = ["--bench", "x", "--walks", "x", "--probe", "x", "--input", "x", "--date",
                "20191230", "--prediction", "P-0999", "--raw-dir", "x", "--out", "x"]
        for lives in ("60,600", "600,60,3600", "60,60,600", "0,60,600", "a,b,c"):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                walk_driver.parse_args([*base, "--lives-s", lives])
        self.assertEqual(walk_driver.parse_args(base).lives, [60, 600, 3600, 14400])


FAKE = """#!/bin/sh
life=0
while [ $# -gt 0 ]; do
  [ "$1" = --out ] && out=$2
  [ "$1" = --shadow-life-ns ] && life=$2
  shift
done
life=$((life / 1000000000))
case $life in
{cases}
esac
exit {code}
"""


def fake(path, records, code=0):
    """A script that writes the record of the life it is asked for."""
    cases = "\n".join(f"  {life}) cat > \"$out\" <<'JSON'\n{json.dumps(record)}\nJSON\n  ;;"
                      for life, record in records.items())
    path.write_text(FAKE.format(cases=cases, code=code), encoding="utf-8")
    path.chmod(0o755)


@unittest.skipUnless(sys.platform == "darwin",
                     "machine_check.sh passes the fake probe only on macOS, where pinning and "
                     "the governor are recorded, not enforced")
class EndToEnd(unittest.TestCase):
    def run_driver(self, root, timed, counts=None):
        (root / "results").mkdir()
        (root / "results" / "predictions.md").write_text("## P-0999: x\n\nIt runs.\n",
                                                         encoding="utf-8")
        fake(root / "bench.sh", timed)
        fake(root / "walks.sh", counts or {life: count_record(life) for life in LIVES})
        out = root / "results" / "walks_ladder_x.md"
        machine = FIXTURES / "machine_check"
        result = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "walk_driver.py"),
             "--bench", str(root / "bench.sh"), "--walks", str(root / "walks.sh"),
             "--probe", str(machine / "fake_probe.sh"), "--input", str(machine / "input.bin"),
             "--date", "20191230", "--prediction", "P-0999", "--raw-dir", str(root / "raw"),
             "--out", str(out), "--lives-s", ",".join(str(life) for life in LIVES),
             "--repetitions", "3", "--allow-dirty", "--predictions-root", str(root),
             "--run-log", str(root / "results" / "run_log.md")],
            capture_output=True, text=True, timeout=120, check=False)
        return result, out, (root / "results" / "run_log.md").read_text(encoding="utf-8")

    def test_a_run_writes_a_file_that_passes_once_stamped(self):
        timed = {life: timed_record(life, 1.6 + life / 36_000) for life in LIVES}
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, out, log = self.run_driver(Path(directory), timed)
            self.assertEqual(result.returncode, 0, result.stderr)
            text = out.read_text(encoding="utf-8")
            self.assertEqual({code for code, _ in check_results.check_text(text)},
                             {"load-not-stamped"})
            self.assertEqual(len(re.findall(r"^\| 3 \| (?:T-BT-3|100,000 orders)", text,
                                            re.MULTILINE)), len(LIVES))
            self.assertIn("| published |", log)

    def test_a_timed_process_off_its_count_refuses_the_run(self):
        timed = {life: timed_record(life, 1.6) for life in LIVES}
        timed[600] = copy.deepcopy(timed[600])
        timed[600]["timed"]["event_hash"] = timed[600]["warmup"]["event_hash"] = event_hash(601)
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, out, log = self.run_driver(Path(directory), timed)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(out.exists())
            self.assertIn("did not give its count's event hash", log)

    def test_a_count_the_engine_did_not_match_refuses_the_run(self):
        counts = {life: count_record(life) for life in LIVES}
        counts[3600]["identical"] = False
        timed = {life: timed_record(life, 1.6) for life in LIVES}
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, out, log = self.run_driver(Path(directory), timed, counts)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(out.exists())
            self.assertIn("refused at counting the walks at a 3600 s life", log)


if __name__ == "__main__":
    unittest.main()
