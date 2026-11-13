#!/usr/bin/env python3
"""backtest_driver.py and check_results.py's rules for a backtest results file.

The good files are rendered here by the driver from made-up records and stamped from the
committed measure.sh fixture, so the checks run on what the driver really writes. Each case
breaks one thing and requires that code. The end-to-end cases run the driver with a fake
bench and the fake probe, so they need macOS, where machine_check records pinning rather
than enforcing it.
"""

import argparse
import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest_driver  # noqa: E402
import check_results  # noqa: E402
import stamp_load  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
STDERR = (FIXTURES / "bench_results" / "measure.stderr").read_text(encoding="utf-8")
MACHINE = "machine_check: pass input-resident: 1 of 1 pages\nmachine_check: passed"
HEADER = {
    "host": "macOS (M4 Max)", "cpu": "Apple M4 Max", "kernel": "Darwin 27.2.0",
    "compiler": "Clang 23.1.4", "flags": "-O3 -DNDEBUG -std=c++23", "commit": "abc (clean)",
    "dataset": "12302019.NASDAQ_ITCH50", "dataset_sha256": "00",
    "timer": "mach_absolute_time, calibrated 24.000 MHz", "quantum": "1 ticks, 41.667 ns",
    "pair": "0.145 ticks, 6.031 ns, not subtracted: one span covers the run",
    "floor": "166.667 ns", "pinning": "not pinned: thread_policy_set returned code 46",
    "migrations": "0 seen in 3 samples", "core": "unpinned, last ran on core 6",
    "governor": "unavailable: macOS has no cpufreq governor",
    "frequency": "unavailable: macOS exposes no core frequency on Apple silicon",
    "turbo": "unavailable: macOS exposes no boost control", "smt": "none: 16 physical, 16 logical",
    "isolation": "unavailable: macOS has no isolated cores",
    "counters": "unavailable: bench_backtest reads no counters",
    "rdpmc": "unavailable: bench_backtest reads no counters",
    "page_size": "16384 bytes, base pages", "residency": "503626 of 503626 pages resident"}


def counts(messages):
    return {"ns": 0, "messages": messages, "deliveries": 5_425_845, "arrivals": 900_000,
            "decisions": 900_000, "reports": 1_200_000, "wakes": 0, "fills": 30_000,
            "filled_shares": 3_000_000, "maker_fills": 30_000, "taker_fills": 0,
            "crossing_fills": 900, "queue_anomalies": 0, "late_adds": 52_373,
            "late_adds_ahead": 46, "hidden_prints_at_sim_price": 6_331, "rejected": 0,
            "expired": 8, "after_end": 0, "no_order_slot": 0, "refused_messages": 0,
            "view_refused": 0, "out_of_order": 0, "overflows": 0, "fee_errors": 0,
            "strategy_events": 5_425_845, "sends": 90_000, "replaces": 350_000,
            "cancels": 70_000, "skipped": 0, "strategy_fills": 30_000,
            "event_hash": "c3fa26520a4e65ba"}


def record(workload="quoting", seconds=50.0):
    """A record as bench_backtest writes it."""
    shadow = workload == "shadow"
    messages = 5_425_845 if shadow else 268_744_780
    timed = counts(messages)
    timed["ns"] = seconds * 1e9
    warmup = dict(timed, ns=seconds * 1.1e9)
    return {"header": dict(HEADER), "workload": workload, "symbols": "AAPL MSFT SPY TSLA",
            "input": "extract: the messages of AAPL MSFT SPY TSLA" if shadow
            else "mapped: the whole day", "date": 20191230,
            "shadow_orders": 100_000 if shadow else 0,
            "shadow_span_ns": 0, "shadow_life_ns": 0,
            "latency_ns": {"market_data": 0 if shadow else 25_000,
                           "order_entry": 0 if shadow else 25_000,
                           "response": 0 if shadow else 25_000,
                           "compute": 0 if shadow else 5_000},
            "input_messages": 268_744_780, "residency": ["1 of 1 pages resident"] * 3,
            "migrations": 0,
            "extract": {"messages": messages, "bytes": 1, "ns": 3e10} if shadow else None,
            "peak_rss_bytes": 9_800_000_000, "warmup": warmup, "timed": timed, "refusals": []}


def args_for(workload, prediction="P-0101"):
    return argparse.Namespace(prediction=prediction, workload=workload, wrapped_by=None)


def rendered(workload="quoting", seconds=(50.0, 49.0, 51.0)):
    records = [record(workload, value) for value in seconds]
    text = backtest_driver.render(records, args_for(workload), "P-0101-x",
                                  "2026-10-31T10:00:01Z", "2026-10-31T10:00:09Z", MACHINE,
                                  "python3 -B scripts/backtest_driver.py")
    return stamp_load.stamp(text, STDERR)


QUOTING = rendered()
SHADOW = rendered("shadow", (1.0, 1.1, 0.9))
SLOW_SHADOW = rendered("shadow", (2.0, 2.1, 1.9))


def codes(text):
    return {code for code, _ in check_results.check_text(text)}


def replace(text, old, new):
    assert old in text, old
    return text.replace(old, new)


def summary_row(text, label):
    return re.search(rf"^\| {re.escape(label)} \|.*\|$", text, re.MULTILINE).group(0)


class Files(unittest.TestCase):
    def test_the_rendered_files_pass(self):
        for text in (QUOTING, SHADOW, SLOW_SHADOW):
            self.assertEqual(check_results.check_text(text), [])

    def test_the_book_rules_do_not_apply(self):
        self.assertNotIn("## Latency", QUOTING)
        self.assertNotIn("## Counters", QUOTING)

    def test_the_generic_rules_still_apply(self):
        self.assertIn("input-not-resident", codes(replace(
            QUOTING, "- residency: 503626 of 503626", "- residency: 503625 of 503626")))
        pending = backtest_driver.render(
            [record()] * 3, args_for("quoting"), "x", "2026-10-31T10:00:01Z",
            "2026-10-31T10:00:09Z", MACHINE, "x")
        self.assertIn("load-not-stamped", codes(pending))
        self.assertIn("machine-check-failed", codes(replace(QUOTING, "machine_check: passed",
                                                            "machine_check: refused")))

    def test_every_backtest_field_dropped_in_turn(self):
        for name in check_results.BACKTEST_FIELDS:
            broken = re.sub(rf"^- {name}: .*\n", "", QUOTING, flags=re.MULTILINE)
            self.assertNotEqual(broken, QUOTING, name)
            self.assertIn("header-field-missing", codes(broken), name)

    def test_an_unknown_workload(self):
        self.assertIn("workload-unknown", codes(replace(QUOTING, "- workload: quoting",
                                                        "- workload: replay")))

    def test_a_summary_figure_dropped(self):
        for label in check_results.BACKTEST_SUMMARY:
            broken = replace(QUOTING, summary_row(QUOTING, label) + "\n", "")
            self.assertIn("summary-figure-missing", codes(broken), label)

    def test_a_median_outside_its_min_and_max(self):
        row = summary_row(QUOTING, "Engine events/s")
        cells = row.split(" | ")
        broken = replace(QUOTING, row, row.replace(cells[1], "1", 1))
        self.assertIn("summary-inconsistent", codes(broken))

    def test_a_summary_rate_that_is_not_the_repetitions_median(self):
        row = summary_row(QUOTING, "Replay rate, messages/s")
        cells = row.split(" | ")
        broken = replace(QUOTING, row, row.replace(cells[1], cells[2], 1))
        self.assertIn("summary-inconsistent", codes(broken))

    def test_a_repetition_row_dropped(self):
        row = re.search(r"^\| 3 \|.*\|$", QUOTING, re.MULTILINE).group(0)
        self.assertIn("summary-inconsistent", codes(replace(QUOTING, row + "\n", "")))

    def test_no_statement_of_the_event(self):
        broken = re.sub(r"## What an event is\n.*?(?=## Targets)", "", QUOTING, flags=re.DOTALL)
        self.assertIn("event-definition-missing", codes(broken))
        self.assertIn("event-definition-missing",
                      codes(replace(QUOTING, "The denominator is", "The count is")))

    def test_no_queue_anomaly_count(self):
        self.assertIn("counts-missing", codes(replace(QUOTING, "| Queue anomalies |",
                                                      "| Anomalies |")))

    def test_a_mac_file_without_its_note(self):
        self.assertIn("host-note-missing", codes(replace(QUOTING, "is not a pinned host.",
                                                         "is a host.")))

    def test_t_bt_1_called_met_on_macos(self):
        broken = re.sub(r"\| host differs: [^|]* \|$", "| met |", QUOTING, flags=re.MULTILINE)
        self.assertNotEqual(broken, QUOTING)
        self.assertIn("target-host-blurred", codes(broken))

    def test_t_bt_3_met_and_missed_on_macos(self):
        self.assertIn("| met |", SHADOW)
        self.assertIn("| missed: ", SLOW_SHADOW)

    def test_a_verdict_against_its_figure(self):
        broken = re.sub(r"\| missed: [^|]* \|$", "| met |", SLOW_SHADOW, flags=re.MULTILINE)
        self.assertIn("target-verdict-wrong", codes(broken))
        broken = re.sub(r"\| met \|$", "| missed: by nothing |", SHADOW, flags=re.MULTILINE)
        self.assertIn("target-verdict-wrong", codes(broken))

    def test_a_measured_figure_that_is_not_the_median(self):
        broken = re.sub(r"\| ([0-9.]+) M messages/s \| met \|$", "| 9.99 M messages/s | met |",
                        SHADOW, flags=re.MULTILINE)
        self.assertNotEqual(broken, SHADOW)
        self.assertIn("target-verdict-wrong", codes(broken))

    def test_the_target_dropped(self):
        broken = re.sub(r"^\| T-BT-3 \|.*\n", "", SHADOW, flags=re.MULTILINE)
        self.assertIn("target-missing", codes(broken))

    def test_a_verdict_outside_the_fixed_words(self):
        broken = re.sub(r"\| met \|$", "| fine |", SHADOW, flags=re.MULTILINE)
        self.assertIn("target-verdict-invalid", codes(broken))


    def test_a_sibling_the_machine_check_read_is_in_the_command(self):
        text = replace(QUOTING, "machine_check: passed",
                       "machine_check: pass sibling-idle: cpu5 is 0.0% busy over 1 s\n"
                       "machine_check: passed")
        self.assertIn("command-differs", codes(text))
        asked = replace(text, "\npython3 -B scripts/backtest_driver.py\n",
                        "\npython3 -B scripts/backtest_driver.py --core 4 --sibling 5\n")
        self.assertNotIn("command-differs", codes(asked))


class Driver(unittest.TestCase):
    def test_a_good_record_is_accepted(self):
        for workload in ("quoting", "shadow"):
            backtest_driver.validate_run(record(workload), workload)

    def test_a_warmup_that_disagrees_is_refused(self):
        broken = record()
        broken["warmup"]["event_hash"] = "0"
        with self.assertRaisesRegex(backtest_driver.Refused, "differ on event_hash"):
            backtest_driver.validate_run(broken, "quoting")

    def test_another_workload_is_refused(self):
        with self.assertRaisesRegex(backtest_driver.Refused, "workload"):
            backtest_driver.validate_run(record("shadow"), "quoting")

    def test_a_shadow_run_off_its_extract_is_refused(self):
        broken = record("shadow")
        broken["extract"] = None
        with self.assertRaisesRegex(backtest_driver.Refused, "extract"):
            backtest_driver.validate_run(broken, "shadow")

    def test_repetitions_that_disagree_are_refused(self):
        other = record()
        other["timed"]["fills"] += 1
        with self.assertRaisesRegex(backtest_driver.Refused, "repetition 2 differs"):
            backtest_driver.validate_together([record(), other, record()])

    def test_verdicts(self):
        mac = check_results.MAC_HOST
        self.assertTrue(backtest_driver.verdict("quoting", mac, 9e6).startswith("host differs: "))
        self.assertEqual(backtest_driver.verdict("quoting", "debian", 2.0e6), "met")
        self.assertTrue(backtest_driver.verdict("quoting", "debian", 1.9e6).startswith("missed: "))
        self.assertEqual(backtest_driver.verdict("shadow", mac, 4.61e6), "met")
        self.assertTrue(backtest_driver.verdict("shadow", mac, 4.6e6).startswith("missed: "))

    def test_t_bt_1_is_held_in_engine_events(self):
        # 1.99 M messages/s is 2.05 M engine events/s: under the bar in messages, over it in
        # engine events, the unit BENCHMARKS.md gives T-BT-1's bar.
        records = [record(seconds=135.0) for _ in range(3)]
        for one in records:
            one["header"]["host"] = "debian"
        self.assertEqual(backtest_driver.target_rows(records), [
            "| T-BT-1 | ≥ 2.00 M engine events/s | 2.05 M engine events/s (1.99 M messages/s) "
            "| met |"])
        self.assertIn("| T-BT-1 | ≥ 2.00 M engine events/s | ", QUOTING)
        self.assertIn("M engine events/s (5.37 M messages/s) | host differs: ", QUOTING)
        self.assertIn("against the bar: +3.53 M engine events/s |", QUOTING)
        self.assertIn("| T-BT-3 | ≥ 4.61 M messages/s | 5.43 M messages/s | met |", SHADOW)

    def test_a_target_in_another_unit(self):
        row = re.search(r"^\| T-BT-1 \|.*$", QUOTING, re.MULTILINE).group(0)
        old = row.replace("engine events/s (5.37 M messages/s)", "messages/s").replace(
            "M engine events/s", "M messages/s")
        self.assertIn("target-unit-wrong", codes(replace(QUOTING, row, old)))
        self.assertIn("target-verdict-wrong", codes(replace(QUOTING, " (5.37 M messages/s)", "")))
        self.assertIn("target-verdict-wrong", codes(replace(QUOTING, "(5.37 M messages/s)",
                                                            "(5.38 M messages/s)")))
        self.assertIn("target-unit-wrong", codes(replace(
            SHADOW, "| ≥ 4.61 M messages/s | 5.43 M messages/s |",
            "| ≥ 4.61 M engine events/s | 5.43 M engine events/s |")))

    def test_the_rates(self):
        one = record(seconds=2.0)
        self.assertEqual(backtest_driver.message_rate(one), 268_744_780 / 2.0)
        events = 268_744_780 + 5_425_845 + 900_000 + 1_200_000
        self.assertEqual(backtest_driver.engine_rate(one), events / 2.0)

    def test_the_shadow_denominator_is_named_beside_queuetruths(self):
        self.assertIn("The denominator is the 5,425,845 extract messages", SHADOW)
        self.assertIn("differ by 4", SHADOW)
        self.assertIn("5,425,849", SHADOW)

    def test_the_command_shown_carries_the_core_and_its_sibling(self):
        base = ["--bench", "x", "--probe", "x", "--input", "x", "--workload", "quoting",
                "--date", "20191230", "--prediction", "P-0103", "--raw-dir", "x", "--out", "x"]
        plain = backtest_driver.relative_command(backtest_driver.parse_args(base))
        self.assertNotIn("--sibling", plain)
        command = backtest_driver.relative_command(backtest_driver.parse_args(
            [*base, "--core", "4", "--sibling", "5"]))
        self.assertIn(" --core 4 --sibling 5", command)

    def test_two_repetitions_are_not_enough(self):
        with self.assertRaises(SystemExit), \
                open("/dev/null", "w", encoding="utf-8") as null, \
                unittest.mock.patch("sys.stderr", null):
            backtest_driver.parse_args(["--bench", "x", "--probe", "x", "--input", "x",
                                        "--workload", "quoting", "--date", "20191230",
                                        "--prediction", "P-0101", "--raw-dir", "x", "--out",
                                        "x", "--repetitions", "2"])


@unittest.skipUnless(sys.platform == "darwin",
                     "machine_check.sh passes the fake probe only on macOS, where pinning and "
                     "the governor are recorded, not enforced")
class EndToEnd(unittest.TestCase):
    def run_driver(self, root, fake_record, code=0, busy=None):
        """The driver on fakes, under the Mac's sampler rule (r5-2), whose sampler reads a fake
        ps that shows another process at 50% CPU while `busy` exists: made by the first
        process with busy="first", by every one with busy="always"."""
        (root / "results").mkdir()
        (root / "results" / "predictions.md").write_text("## P-0101: x\n\nIt runs.\n",
                                                         encoding="utf-8")
        bench = root / "bench.sh"
        making = {None: "", "always": f"touch '{root}/busy'; sleep 1; rm -f '{root}/busy'\n",
                  "first": (f"[ -f '{root}/once' ] || {{ touch '{root}/once' '{root}/busy'; "
                            f"sleep 1; rm -f '{root}/busy'; }}\n")}[busy]
        bench.write_text(
            "#!/bin/sh\nwhile [ $# -gt 0 ]; do [ \"$1\" = --out ] && out=$2; shift; done\n"
            f"{making}cat > \"$out\" <<'JSON'\n{json.dumps(fake_record)}\nJSON\nexit {code}\n",
            encoding="utf-8")
        bench.chmod(0o755)
        ps = root / "ps.sh"
        ps.write_text(f"#!/bin/sh\n[ -f '{root}/busy' ] && echo '99999 1 50.0 /usr/bin/other_job'"
                      "\nexit 0\n", encoding="utf-8")
        ps.chmod(0o755)
        out = root / "results" / "backtest_x.md"
        machine = FIXTURES / "machine_check"
        result = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "backtest_driver.py"),
             "--bench", str(bench), "--probe", str(machine / "fake_probe.sh"),
             "--input", str(machine / "input.bin"), "--workload", fake_record["workload"],
             "--date", "20191230", "--prediction", "P-0101", "--raw-dir", str(root / "raw"),
             "--out", str(out), "--allow-dirty", "--predictions-root", str(root),
             "--run-log", str(root / "results" / "run_log.md"), "--ps", str(ps),
             "--sample-every", "0.2", "--quiet-before", "0"],
            capture_output=True, text=True, timeout=120, check=False)
        return result, out, (root / "results" / "run_log.md").read_text(encoding="utf-8")

    def test_a_run_writes_a_file_that_passes_once_stamped(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, out, log = self.run_driver(Path(directory), record("shadow", 1.0))
            self.assertEqual(result.returncode, 0, result.stderr)
            text = out.read_text(encoding="utf-8")
            self.assertEqual({code for code, _ in check_results.check_text(text)},
                             {"load-not-stamped"})
            # The driver leaves the outcome to stamp_load.py (r5-3).
            self.assertIn("| written, pending the load stamp: ", log)
            self.assertNotIn("| published", log)
            # The driver applied the Mac's sampler rule itself (r5-2).
            self.assertIn(f"- interference: {check_results.driver_interference(0.2)}; 0 "
                          "withdrawn under the rule\n", text)
            self.assertIn(check_results.DRIVER_SAMPLER_RULE, text)
            self.assertEqual(text.count("| T-BT-3, repetition "), 3)

    def test_a_process_beside_another_busy_process_is_withdrawn_and_rerun(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, out, log = self.run_driver(Path(directory), record("shadow", 1.0),
                                               busy="first")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("withdrawn: T-BT-3, repetition 1 on the Mac, exit 0; the CPU sampler "
                          "saw other_job at 50.0% CPU", log)
            text = out.read_text(encoding="utf-8")
            self.assertEqual({code for code, _ in check_results.check_text(text)},
                             {"load-not-stamped"})
            self.assertIn("; 1 withdrawn under the rule", text)
            self.assertTrue((Path(directory) / "raw" /
                             "shadow-repetition-1-withdrawn-1.json").is_file())

    def test_a_process_withdrawn_three_times_running_ends_the_run(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, out, log = self.run_driver(Path(directory), record("shadow", 1.0),
                                               busy="always")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(out.exists())
            self.assertEqual(log.count("withdrawn: T-BT-3, repetition 1 on the Mac"), 3)
            self.assertIn("refused at T-BT-3, repetition 1: withdrawn 3 times in a row under the "
                          "sampler rule", log)

    def test_a_bench_that_refuses_refuses_the_run(self):
        refused = copy.deepcopy(record())
        refused["refusals"] = ["no simulated order traded"]
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, out, log = self.run_driver(Path(directory), refused, code=3)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(out.exists())
            self.assertIn("refused at repetition 1: exited 3: no simulated order traded", log)


class RenderAgain(unittest.TestCase):
    """backtest_driver.py render: a published file rendered again from its raw records."""

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.raw = Path(self.scratch.name)
        self.records = [record(seconds=value) for value in (50.0, 49.0, 51.0)]
        for index, one in enumerate(self.records, start=1):
            (self.raw / f"quoting-repetition-{index}.json").write_text(json.dumps(one),
                                                                      encoding="utf-8")
        args = backtest_driver.parse_args(
            ["--bench", "b", "--probe", "p", "--input", "i", "--workload", "quoting", "--date",
             "20191230", "--prediction", "P-0101", "--raw-dir", "<raw>", "--out",
             "results/backtest_quoting_mac_12302019.md"])
        self.published = stamp_load.stamp(backtest_driver.render(
            self.records, args, "P-0101-x", "2026-10-31T10:00:01Z", "2026-10-31T10:00:09Z",
            MACHINE, backtest_driver.relative_command(args)), STDERR)

    def tearDown(self):
        self.scratch.cleanup()

    def test_the_same_records_give_the_same_file(self):
        self.assertEqual(check_results.check_text(self.published), [])
        self.assertEqual(backtest_driver.render_again(self.raw, self.published, STDERR),
                         self.published)
        again = backtest_driver.render_again(self.raw, self.published, STDERR, "the reason")
        self.assertIn("\n\nRendered again from the run's own raw records, unchanged: the reason"
                      "\n\nHost: ", again)

    def test_the_figures_come_from_the_records(self):
        self.records[1]["timed"]["ns"] = 45e9
        self.records[1]["warmup"]["ns"] = 45e9
        (self.raw / "quoting-repetition-2.json").write_text(json.dumps(self.records[1]),
                                                           encoding="utf-8")
        again = backtest_driver.render_again(self.raw, self.published, STDERR)
        self.assertIn("| 2 | 5,972,106 |", again)
        self.assertNotEqual(again, self.published)

    def test_a_file_with_a_sampler_section(self):
        with self.assertRaisesRegex(backtest_driver.Refused, "Sampler"):
            backtest_driver.render_again(self.raw, self.published.replace(
                "## Commands", "## Sampler\n\nx\n\n## Commands"), STDERR)


if __name__ == "__main__":
    import unittest.mock  # noqa: F401

    unittest.main()
