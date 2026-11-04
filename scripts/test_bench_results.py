#!/usr/bin/env python3
"""check_results.py, stamp_load.py and bench_driver.py on fixture results files.

Each case breaks one thing in a results file that passes, and requires that code. The
strings the repo's gates ban are assembled from pieces, so this file does not trip them.
"""

import contextlib
import io
import json
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_driver  # noqa: E402
import check_results  # noqa: E402
import stamp_load  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "bench_results"
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
MAC = (FIXTURES / "good_mac.md").read_text(encoding="utf-8")
DEBIAN = (FIXTURES / "good_debian.md").read_text(encoding="utf-8")
PENDING = (FIXTURES / "pending_mac.md").read_text(encoding="utf-8")
STDERR = (FIXTURES / "measure.stderr").read_text(encoding="utf-8")
BOOK = (FIXTURES / "good_book_mac.md").read_text(encoding="utf-8")
BOOK_MAP = (FIXTURES / "good_book_map_mac.md").read_text(encoding="utf-8")
BOOK_TOP = (FIXTURES / "good_book_top_mac.md").read_text(encoding="utf-8")


def codes(text):
    return {code for code, _ in check_results.check_text(text)}


def replace(text, old, new):
    assert old in text, old
    return text.replace(old, new)


class Results(unittest.TestCase):
    def test_the_fixtures_pass(self):
        self.assertEqual(check_results.check_text(MAC), [])
        self.assertEqual(check_results.check_text(DEBIAN), [])

    def test_every_field_dropped_in_turn(self):
        for name in check_results.REQUIRED_FIELDS:
            broken = re.sub(rf"^- {name}: .*\n", "", MAC, flags=re.MULTILINE)
            self.assertNotEqual(broken, MAC, name)
            self.assertIn("header-field-missing", codes(broken), name)

    def test_an_empty_field(self):
        self.assertIn("header-field-empty", codes(replace(MAC, "- cpu: Apple M4 Max", "- cpu: ")))

    def test_unavailable_with_no_reason(self):
        broken = replace(DEBIAN, "- governor: performance", "- governor: unavailable")
        self.assertIn("unavailable-without-reason", codes(broken))

    def test_a_faked_field_on_macos(self):
        for name, value in (("governor", "performance"), ("frequency", "4500000 kHz"),
                            ("turbo", "on"), ("isolation", "none"), ("rdpmc", "agreed")):
            broken = re.sub(rf"^- {name}: .*$", f"- {name}: {value}", MAC, flags=re.MULTILINE)
            self.assertIn("unavailable-faked", codes(broken), name)

    def test_a_host_outside_the_fixed_set(self):
        broken = replace(MAC, "- host: macOS (M4 Max)", "- host: some-laptop")
        self.assertIn("host-label", codes(broken))

    def test_mac_numbers_labelled_debian(self):
        self.assertIn("host-label", codes(replace(MAC, "- host: macOS (M4 Max)", "- host: debian")))

    def test_a_pin_claimed_on_macos(self):
        broken = re.sub(r"^- pinning: .*$",
                        "- pinning: pinned to core 4: read back [4]", MAC, flags=re.MULTILINE)
        self.assertIn("pin-claimed", codes(broken))

    def test_a_pin_with_no_matching_read_back(self):
        broken = replace(DEBIAN, "read back [4]", "read back [0,1,2,3,4,5,6,7]")
        self.assertIn("pin-claimed", codes(broken))

    def test_a_failed_pin_on_debian_does_not_publish(self):
        broken = replace(DEBIAN, "- pinning: pinned to core 4: ", "- pinning: not pinned: ")
        self.assertIn("pin-failed", codes(broken))

    def test_input_not_resident(self):
        broken = replace(MAC, "- residency: 7 of 7", "- residency: 6 of 7")
        self.assertIn("input-not-resident", codes(broken))

    def test_warmup_counted_in(self):
        broken = replace(MAC, "- timed_messages: 3,330", "- timed_messages: 6,660")
        self.assertIn("warmup-included", codes(broken))

    def test_a_percentile_below_the_floor(self):
        broken = replace(MAC, "| p50 | unresolved | | | | resolved in 0 of 3 repetitions |",
                         "| p50 | 41.7 | 41.7 | 41.7 | 0.00% | resolved |")
        self.assertIn("percentile-below-floor", codes(broken))

    def test_a_multiplexed_counter_called_measured(self):
        broken = replace(DEBIAN, "| branch-misses | multiplexed | 0.8123 |",
                         "| branch-misses | measured | 0.8123 |")
        self.assertIn("multiplexed-as-measured", codes(broken))

    def test_best_of_alone(self):
        broken = replace(MAC, "| Figure | Median | Min | Max | CV |", "| Figure | Best |")
        self.assertIn("summary-missing", codes(broken))

    def test_load_lines_left_pending(self):
        self.assertIn("load-not-stamped", codes(PENDING))

    def test_a_hostname_in_a_load_line(self):
        broken = replace(MAC, "host=macOS (M4 Max) at", "host=jacksons-machine at")
        self.assertIn("load-line-hostname", codes(broken))

    def test_a_load_line_with_a_failed_exit(self):
        self.assertIn("load-line-invalid", codes(replace(MAC, "exit=0", "exit=1")))

    def test_load_lines_from_another_run(self):
        self.assertIn("load-line-invalid", codes(replace(MAC, "at=2026-10-31T10:00:10Z",
                                                         "at=2026-10-30T10:00:10Z")))

    def test_machine_check_dropped(self):
        broken = re.sub(r"## machine_check output\n.*?(?=## Commands)", "", MAC, flags=re.DOTALL)
        self.assertIn("machine-check-missing", codes(broken))

    def test_machine_check_failed(self):
        broken = replace(DEBIAN, "machine_check: pass governor: core 4 runs performance",
                         "machine_check: FAIL governor: core 4 runs schedutil")
        self.assertIn("machine-check-failed", codes(broken))

    def test_private_text(self):
        self.assertIn("private-text", codes(MAC + "/" + "Users" + "/someone/x\n"))
        self.assertIn("private-text", codes(MAC + "100." + "64.0.1\n"))


def without_line(text, prefix):
    """The text with the first line that starts with prefix taken out."""
    lines = text.splitlines(keepends=True)
    for position, line in enumerate(lines):
        if line.startswith(prefix):
            return "".join(lines[:position] + lines[position + 1:])
    raise AssertionError(prefix)


def target_verdict(text, target, verdict):
    """The text with one target's verdict cell, the last in its row, replaced."""
    lines = text.splitlines(keepends=True)
    for position, line in enumerate(lines):
        if line.startswith(f"| {target} |"):
            cells = line.rstrip("\n").split(" | ")
            cells[-1] = f"{verdict} |"
            lines[position] = " | ".join(cells) + "\n"
            return "".join(lines)
    raise AssertionError(target)


class BookResults(unittest.TestCase):
    def test_the_book_fixtures_pass(self):
        for text in (BOOK, BOOK_MAP, BOOK_TOP):
            self.assertEqual(check_results.check_text(text), [])

    def test_the_stub_label_spares_a_file_the_book_rules(self):
        self.assertTrue(check_results.is_book_file(BOOK))
        self.assertFalse(check_results.is_book_file(MAC))

    def test_every_book_field_dropped_in_turn(self):
        for name in check_results.BOOK_FIELDS:
            self.assertIn("header-field-missing", codes(without_line(BOOK, f"- {name}: ")), name)

    def test_an_unknown_design(self):
        self.assertIn("design-unknown", codes(replace(BOOK, "- design: hash-linked:",
                                                      "- design: tree-linked:")))

    def test_an_unknown_input_mode(self):
        self.assertIn("input-mode-unknown", codes(replace(BOOK, "- input: ram:", "- input: disk:")))

    def test_a_malformed_top_of_book(self):
        self.assertIn("top-of-book-malformed", codes(replace(BOOK, "- top_of_book: not tracked",
                                                             "- top_of_book: maybe")))

    def test_a_malformed_book_message_count(self):
        self.assertIn("book-messages-malformed", codes(replace(BOOK, "- book_messages: 3,312",
                                                               "- book_messages: about 3k")))

    def test_a_latency_row_with_no_subtracted_column(self):
        row = "| p50 | below floor | | | | resolved in 0 of 3 repetitions | below floor |"
        broken = replace(BOOK, row, "| p50 | below floor | | | | resolved in 0 of 3 repetitions |")
        self.assertIn("latency-subtracted-missing", codes(broken))

    def test_raw_and_subtracted_disagree_on_the_floor(self):
        row = "| p99 | below floor | | | | resolved in 0 of 3 repetitions | below floor |"
        broken = replace(BOOK, row, "| p99 | below floor | | | | resolved in 0 of 3 repetitions "
                                    "| 300.0 |")
        self.assertIn("latency-subtracted-missing", codes(broken))

    def test_a_subtracted_figure_under_the_floor(self):
        row = "| p99 | below floor | | | | resolved in 0 of 3 repetitions | below floor |"
        broken = replace(BOOK, row, "| p99 | 291.7 | 291.7 | 291.7 | 0.00% | resolved in 3 of 3 "
                                    "repetitions | 120.0 |")
        self.assertEqual(codes(broken), {"percentile-below-floor"})

    def test_a_malformed_subtracted_figure(self):
        row = "| p99 | below floor | | | | resolved in 0 of 3 repetitions | below floor |"
        broken = replace(BOOK, row, "| p99 | 291.7 | 291.7 | 291.7 | 0.00% | resolved in 3 of 3 "
                                    "repetitions | fast |")
        self.assertIn("latency-value-malformed", codes(broken))

    def test_an_operation_row_dropped(self):
        for operation in check_results.OPERATIONS:
            for percentile in check_results.OPERATION_PERCENTILES:
                broken = without_line(BOOK, f"| {operation} | {percentile} |")
                self.assertIn("operation-table-missing", codes(broken), (operation, percentile))

    def test_an_operation_figure_under_the_floor(self):
        row = "| delete | p99 | below floor | | | | resolved in 0 of 3 repetitions | below floor |"
        broken = replace(BOOK, row, "| delete | p99 | 100.0 | 100.0 | 100.0 | 0.00% | resolved "
                                    "in 3 of 3 repetitions | below floor |")
        self.assertIn("percentile-below-floor", codes(broken))

    def test_a_summary_figure_dropped(self):
        for label in check_results.BOOK_SUMMARY:
            self.assertIn("summary-figure-missing", codes(without_line(BOOK, f"| {label} |")),
                          label)

    def test_no_book_memory_section(self):
        self.assertIn("memory-missing", codes(replace(BOOK, "## Book memory\n", "## Memory\n")))

    def test_no_statement_of_how_the_day_was_read(self):
        broken = replace(BOOK, "## How this run reads the day\n", "## Input\n")
        self.assertIn("method-missing", codes(broken))

    def test_tracked_with_no_count_of_the_moves(self):
        broken = replace(BOOK_TOP, "The top of book moved after 207 of the 3,330 messages",
                         "The top of book moved")
        self.assertIn("top-of-book-missing", codes(broken))

    def test_no_design_comparison(self):
        broken = replace(BOOK, "## Design comparison\n", "## Designs\n")
        self.assertIn("design-comparison-missing", codes(broken))

    def test_a_fast_design_with_no_ratio_and_no_reason(self):
        broken = without_line(without_line(BOOK, "| map-fresh | 1.27 |"), "| map-aged | 0.72 |")
        self.assertIn("design-comparison-missing", codes(broken))

    def test_a_ratio_that_is_not_a_figure(self):
        broken = replace(BOOK, "| map-fresh | 1.27 |", "| map-fresh | n/a |")
        self.assertIn("design-comparison-missing", codes(broken))

    def test_a_target_dropped(self):
        for target in check_results.TARGETS:
            self.assertIn("target-missing", codes(without_line(BOOK, f"| {target} |")), target)

    def test_a_verdict_outside_the_fixed_words(self):
        broken = target_verdict(BOOK, "T-BOOK-3", "fine")
        self.assertIn("target-verdict-invalid", codes(broken))

    def test_an_x86_bar_called_met_on_macos(self):
        for target in check_results.X86_TARGETS:
            for verdict in ("met", "missed: by 1 ns", "below floor: p50"):
                broken = target_verdict(BOOK, target, verdict)
                self.assertIn("target-host-blurred", codes(broken), (target, verdict))

    def test_a_host_independent_bar_may_be_met_or_missed_on_macos(self):
        for verdict in ("met", "missed: by 0.1 GB"):
            self.assertEqual(codes(target_verdict(BOOK, "T-BOOK-3", verdict)), set())


class Stamp(unittest.TestCase):
    def test_debian_lines_keep_their_detail_and_lose_the_hostname(self):
        stderr = ("measure.sh: start load1=1.42 host=some-machine at=2000-01-01T00:00:00Z cpus=4 "
                  "governor=performance freq_khz=cpu4:2000000 perf_event_paranoid=2\n"
                  "measure.sh: end load1=1.42 exit=0 at=2999-01-01T00:00:00Z cpus=4 "
                  "governor=performance freq_khz=cpu4:2727044 perf_event_paranoid=2\n"
                  "measure.sh: restored governor=schedutil\n")
        text = stamp_load.stamp(PENDING, stderr)
        self.assertNotIn("some-machine", text)
        self.assertIn("host=macOS (M4 Max) at=2000-01-01T00:00:00Z cpus=4 governor=performance",
                      text)
        self.assertIn("perf_event_paranoid=2", text)

    def test_copies_the_lines_and_drops_the_hostname(self):
        stamped = stamp_load.stamp(PENDING, STDERR)
        self.assertNotIn("some-machine", stamped)
        self.assertIn("- measure_load_start: measure.sh: start load1=1.20 host=macOS (M4 Max) at=",
                      stamped)
        self.assertIn("- measure_load_end: measure.sh: end load1=1.30 exit=0 at=", stamped)
        self.assertIn("- peak_rss: 0.22 GiB, the largest process in the run", stamped)
        self.assertIn("- peak_footprint: 0.11 GiB, of the driver alone", stamped)
        self.assertEqual(check_results.check_text(stamped), [])

    def test_refuses_a_missing_line(self):
        for prefix in ("measure.sh: start", "measure.sh: end"):
            kept = "\n".join(line for line in STDERR.splitlines() if not line.startswith(prefix))
            with self.assertRaises(ValueError):
                stamp_load.stamp(PENDING, kept)

    def test_refuses_lines_from_two_runs(self):
        with self.assertRaises(ValueError):
            stamp_load.stamp(PENDING, STDERR + STDERR)

    def test_refuses_to_stamp_twice(self):
        with self.assertRaises(ValueError):
            stamp_load.stamp(stamp_load.stamp(PENDING, STDERR), STDERR)

    def test_a_failed_bracket_leaves_the_file_alone(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            results = Path(directory) / "results.md"
            stderr = Path(directory) / "stderr"
            results.write_text(PENDING, encoding="utf-8")
            stderr.write_text(STDERR.replace("at=2026-10-31T10:00:10Z", "at=2026-10-31T10:00:06Z"),
                              encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                code = stamp_load.main(["--results", str(results), "--stderr", str(stderr)])
            self.assertEqual(code, 1)
            self.assertEqual(results.read_text(encoding="utf-8"), PENDING)


def record(rate=100.0, ratio=1.5):
    return {
        "header": {},
        "warmup": {"messages": 10},
        "throughput": {"messages": 10, "seconds": 0.1, "messages_per_s": rate},
        "latency_pass": {"messages": 10, "seconds": 0.15, "messages_per_s": rate / ratio},
        "instrumentation_ratio": ratio,
        "latency": {"percentiles": [{"percentile": 50, "value_ns": None},
                                    {"percentile": 99, "value_ns": 500.0}],
                    "mean_ns": 40.0, "max_ns": 900.0, "samples": 10},
        "counters": {"status": [{"name": "cycles", "state": "measured", "value": 5, "ratio": 1.0,
                                 "reason": ""}],
                     "agreement": "x", "per_message_published": False, "per_message": []},
        "timer": {"quantum_ns": 41.667, "pair_ns": 5.0, "floor_ns": 166.0, "calibrated_hz": 24e6},
        "pinned": False,
        "migrations": 0,
        "peak_rss_bytes": 1024,
        "refusals": [],
    }


class Driver(unittest.TestCase):
    def test_rdpmc_against_read_is_shown_as_numbers(self):
        records = [record(), record()]
        for position, gap in enumerate((3, 40)):
            records[position]["counters"]["agreement_events"] = [
                {"name": "cycles", "read": 4000, "rdpmc": 4000 - gap}]
            records[position]["counters"]["per_message"] = [
                {"name": "cycles", "mean": 1.0, "p50": 1, "p99": 2, "sum": 3000}]
        self.assertEqual(bench_driver.agreement_rows(records),
                         ["| cycles | 4,000 | 3,978 | 40 | 1.0000% | 3,000 |"])

    def test_no_agreement_figures_means_no_rows(self):
        self.assertEqual(bench_driver.agreement_rows([record()]), [])

    def test_a_linux_run_gives_peak_memory_from_the_harness(self):
        linux = record()
        linux["peak_rss_bytes"] = 2 * 1024 ** 3
        linux["header"] = {"kernel": "Linux 6.12"}
        mac = record()
        mac["header"] = {"kernel": "Darwin 27.2.0"}
        lines = bench_driver.peak_lines([mac])
        self.assertEqual(lines[0], "- peak_rss: unavailable: /usr/bin/time did not wrap the run")
        lines = bench_driver.peak_lines([linux, mac])
        self.assertTrue(lines[0].startswith("- peak_rss: 2.00 GiB, getrusage"))
        self.assertTrue(lines[1].startswith("- peak_footprint: unavailable: "))

    def test_a_good_record_is_accepted(self):
        bench_driver.validate_run(record())

    def test_warmup_leaking_into_a_pass_is_refused(self):
        leaked = record()
        leaked["throughput"]["messages"] = 20
        with self.assertRaises(bench_driver.Refused):
            bench_driver.validate_run(leaked)

    def test_a_percentile_under_the_floor_is_refused(self):
        low = record()
        low["latency"]["percentiles"][1]["value_ns"] = 41.0
        with self.assertRaises(bench_driver.Refused):
            bench_driver.validate_run(low)

    def test_a_partial_counter_called_measured_is_refused(self):
        partial = record()
        partial["counters"]["status"][0]["ratio"] = 0.9
        with self.assertRaises(bench_driver.Refused):
            bench_driver.validate_run(partial)

    def test_spread_is_median_min_max_and_cv(self):
        median, low, high, variation = bench_driver.spread([10.0, 12.0, 20.0])
        self.assertEqual((median, low, high), (12.0, 10.0, 20.0))
        self.assertAlmostEqual(variation, 0.37796, places=4)

    def test_the_summary_is_never_the_best_alone(self):
        rows = bench_driver.summary_rows([record(100.0), record(300.0), record(200.0)])
        self.assertIn("| Throughput, messages/s | 200 | 100 | 300 | 50.00% |", rows)

    def test_a_percentile_unresolved_in_any_repetition_shows_no_value(self):
        row = bench_driver.percentile_row("p50", [200.0, None, 210.0])
        self.assertIn("| p50 | unresolved |", row)
        self.assertIn("resolved in 2 of 3 repetitions", row)

    def test_a_percentile_resolved_everywhere_shows_its_spread(self):
        row = bench_driver.percentile_row("p99", [200.0, 220.0, 210.0])
        self.assertIn("| p99 | 210.0 | 200.0 | 220.0 |", row)

    def test_a_multiplexed_repetition_marks_the_row(self):
        records = [record(), record()]
        records[1]["counters"]["status"][0].update(state="multiplexed", ratio=0.5)
        self.assertEqual(bench_driver.counter_rows(records), ["| cycles | multiplexed | 0.5000 | 5 |  |"])

    def test_two_repetitions_are_not_enough(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bench_driver.parse_args(["--bench", "x", "--probe", "x", "--input", "x",
                                     "--prediction", "P-0001", "--raw-dir", "x", "--out", "x",
                                     "--repetitions", "2"])

    def test_a_machine_check_failure_refuses_and_stays_in_the_log(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            root = Path(directory)
            (root / "results").mkdir()
            (root / "results" / "predictions.md").write_text("## P-0001: x\n\nIt runs.\n",
                                                             encoding="utf-8")
            data = root / "input.bin"
            data.write_bytes(b"x" * 100)
            log = root / "results" / "run_log.md"
            out = root / "out.md"
            result = subprocess.run(
                [sys.executable, "-B", str(REPO_ROOT / "scripts" / "bench_driver.py"),
                 "--bench", str(root / "no-bench"), "--probe", str(root / "no-probe"),
                 "--input", str(data), "--prediction", "P-0001", "--raw-dir", str(root / "raw"),
                 "--out", str(out), "--allow-dirty", "--predictions-root", str(root),
                 "--run-log", str(log)],
                capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("refused at machine_check", result.stderr)
            self.assertFalse(out.exists())
            self.assertIn("refused at machine_check", log.read_text(encoding="utf-8"))

    def test_a_bench_that_refuses_refuses_the_run(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            root = Path(directory)
            fake = root / "bench.py"
            refused = record()
            refused["refusals"] = ["input not resident: 3 of 4 pages resident"]
            fake.write_text(
                "#!/bin/sh\nwhile [ $# -gt 0 ]; do [ \"$1\" = --out ] && out=$2; shift; done\n"
                f"cat > \"$out\" <<'JSON'\n{json.dumps(refused)}\nJSON\nexit 3\n",
                encoding="utf-8")
            fake.chmod(0o755)
            args = bench_driver.parse_args(
                ["--bench", str(fake), "--probe", "x", "--input", "x", "--prediction", "P-0001",
                 "--raw-dir", str(root), "--out", "x"])
            with self.assertRaises(bench_driver.Refused) as caught:
                bench_driver.run_repetition(args, 1, "abc", "00")
            self.assertIn("input not resident", str(caught.exception))


def book_record(rate=100.0, design="hash-linked", pair=5.0, top=0):
    """A record from a book run: the mix, the per-operation spans and the book's memory."""
    book = record(rate)
    book["header"] = {"design": f"{design}: x", "host": check_results.MAC_HOST,
                      "input": "ram: x", "top_of_book": "not tracked"}
    book["mix"] = {"book_messages": 8, "add": 4, "delete": 2, "replace": 1, "reduce": 1}
    book["throughput"]["top_changes"] = top
    book["latency_pass"]["top_changes"] = top
    book["timer"]["pair_ns"] = pair
    book["latency"]["operations"] = [
        {"op": op, "samples": count, "mean_ns": 40.0,
         "percentiles": [{"percentile": 50, "value_ns": None},
                         {"percentile": 99, "value_ns": 500.0}]}
        for op, count in (("add", 4), ("delete", 2), ("replace", 1), ("reduce", 1))]
    book["memory"] = {"complete": True, "first_book": True, "book_bytes": 3 * 1024 ** 2}
    return book


class BookDriver(unittest.TestCase):
    def test_a_good_book_record_is_accepted(self):
        bench_driver.validate_run(book_record())

    def test_operation_spans_that_do_not_match_the_mix_are_refused(self):
        short = book_record()
        short["latency"]["operations"][1]["samples"] = 1
        with self.assertRaisesRegex(bench_driver.Refused, "1 delete spans for 2 delete"):
            bench_driver.validate_run(short)

    def test_an_operation_percentile_under_the_floor_is_refused(self):
        low = book_record()
        low["latency"]["operations"][2]["percentiles"][1]["value_ns"] = 80.0
        with self.assertRaisesRegex(bench_driver.Refused, "replace p99"):
            bench_driver.validate_run(low)

    def test_designs_that_saw_another_mix_are_refused(self):
        other = book_record(design="map-fresh")
        other["mix"]["add"] = 5
        with self.assertRaisesRegex(bench_driver.Refused, "another mix"):
            bench_driver.validate_together({"hash-linked": [book_record()],
                                            "map-fresh": [other]})

    def test_designs_that_saw_another_top_of_book_are_refused(self):
        with self.assertRaisesRegex(bench_driver.Refused, "moves of the top"):
            bench_driver.validate_together({"hash-linked": [book_record(top=7)],
                                            "paged-flat": [book_record(top=8)]})

    def test_designs_that_agree_are_accepted(self):
        bench_driver.validate_together({"hash-linked": [book_record(top=7)] * 3,
                                        "map-aged": [book_record(design="map-aged", top=7)] * 3})

    def test_a_below_floor_percentile_shows_no_figure_in_either_column(self):
        row = bench_driver.percentile_row("p50", [None, 200.0, 210.0], [5.0, 5.0, 5.0])
        self.assertEqual(row, "| p50 | below floor | | | | resolved in 2 of 3 repetitions "
                              "| below floor |")

    def test_each_repetition_loses_its_own_pair(self):
        row = bench_driver.percentile_row("p99", [200.0, 220.0, 210.0], [10.0, 40.0, 5.0])
        self.assertEqual(row, "| p99 | 210.0 | 200.0 | 220.0 | 4.76% | resolved | 190.0 |")

    def test_ratios_pair_repetition_with_repetition(self):
        fast = [book_record(300.0), book_record(100.0), book_record(200.0)]
        slow = [book_record(100.0), book_record(50.0), book_record(200.0)]
        self.assertEqual(bench_driver.paired_ratios(fast, slow), [3.0, 2.0, 1.0])

    def test_the_book_summary_adds_service_batch_and_memory(self):
        rows = bench_driver.summary_rows([book_record()] * 3)
        self.assertIn("| Mean service time per message, ns | 10000000.00 | 10000000.00 "
                      "| 10000000.00 | 0.00% |", rows)
        self.assertIn("| Batch mean per book message, ns | 12500000.00 | 12500000.00 "
                      "| 12500000.00 | 0.00% |", rows)
        self.assertIn("| Book memory, MiB | 3.0 | 3.0 | 3.0 | 0.00% |", rows)

    def test_an_x86_bar_on_macos_is_neither_met_nor_missed(self):
        for value in (20e6, 10e6):
            _, verdict = bench_driver.judge("T-BOOK-1", check_results.MAC_HOST,
                                            [("rate", value, 16.4e6, "min", "")])
            self.assertTrue(verdict.startswith("host differs: "), verdict)

    def test_an_x86_bar_on_debian_is_met_or_missed(self):
        _, verdict = bench_driver.judge("T-BOOK-1", "debian", [("rate", 20.0, 16.4, "min", "")])
        self.assertEqual(verdict, "met")
        _, verdict = bench_driver.judge("T-BOOK-1", "debian", [("rate", 10.0, 16.4, "min", "")])
        self.assertEqual(verdict, "missed: rate by 6.40")

    def test_a_host_independent_bar_on_macos_is_met_or_missed(self):
        _, verdict = bench_driver.judge("T-BOOK-3", check_results.MAC_HOST,
                                        [("book", 1.5, 1.4, "max", " GB")])
        self.assertEqual(verdict, "missed: book by 0.10 GB")

    def test_a_bar_with_a_figure_below_the_floor_says_so(self):
        measured, verdict = bench_driver.judge("T-LAT-3", "debian",
                                               [("mean", 40.0, 393, "max", " ns"),
                                                ("p99.9", None, 750, "max", " ns")])
        self.assertEqual(measured, "mean 40.00 ns; p99.9 below floor")
        self.assertTrue(verdict.startswith("below floor: p99.9"), verdict)

    def test_each_pairing_names_the_method_it_matches(self):
        def method(source, top):
            return bench_driver.method_lines({"input": f"{source}: x", "top_of_book": top})[-1]
        self.assertIn("T-BOOK-1 method", method("ram", "not tracked"))
        self.assertIn("T-BOOK-2 method", method("mapped", "tracked: x"))
        for source, top in (("ram", "tracked: x"), ("mapped", "not tracked")):
            self.assertIn("neither of itch-book's methods", method(source, top))

    def test_two_designs_need_a_design_in_the_out_path(self):
        base = ["--bench", "x", "--probe", "x", "--input", "x", "--prediction", "P-0001",
                "--raw-dir", "x", "--design", "hash-linked", "--design", "map-fresh"]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bench_driver.parse_args([*base, "--out", "x.md"])
        args = bench_driver.parse_args([*base, "--out", "x_{design}.md"])
        self.assertEqual(args.design, ["hash-linked", "map-fresh"])

    def test_a_design_named_twice_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bench_driver.parse_args(["--bench", "x", "--probe", "x", "--input", "x",
                                     "--prediction", "P-0001", "--raw-dir", "x",
                                     "--out", "{design}", "--design", "hash-flat",
                                     "--design", "hash-flat"])

    def fake_bench(self, root, untimed):
        """A bench_replay stand-in that writes the timed record or the untimed one."""
        fake = root / "bench.sh"
        timed = book_record()
        timed["header"].update(counters="none", rdpmc="none")
        untimed_record = book_record()
        untimed_record["header"].update(counters="from the untimed pass", rdpmc="unavailable")
        untimed_record["memory"].update(untimed)
        fake.write_text(
            "#!/bin/sh\nwhile [ $# -gt 0 ]; do case $1 in --out) out=$2;; --passes) passes=$2;; "
            "esac; shift; done\n"
            f"if [ \"$passes\" = timed ]; then cat > \"$out\" <<'JSON'\n{json.dumps(timed)}\nJSON\n"
            f"else cat > \"$out\" <<'JSON'\n{json.dumps(untimed_record)}\nJSON\nfi\n",
            encoding="utf-8")
        fake.chmod(0o755)
        return bench_driver.parse_args(
            ["--bench", str(fake), "--probe", "x", "--input", "x", "--prediction", "P-0001",
             "--raw-dir", str(root), "--out", "x", "--design", "hash-linked"])

    def test_the_untimed_process_supplies_counters_and_memory(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            args = self.fake_bench(Path(directory), {"book_bytes": 7})
            merged = bench_driver.run_repetition(args, 1, "abc", "00", "hash-linked")
            self.assertEqual(merged["memory"]["book_bytes"], 7)
            self.assertEqual(merged["header"]["counters"], "from the untimed pass")
            self.assertEqual(merged["header"]["rdpmc"], "unavailable")

    def test_memory_that_is_not_the_first_book_is_refused(self):
        for broken in ({"first_book": False}, {"complete": False}):
            with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
                args = self.fake_bench(Path(directory), broken)
                with self.assertRaisesRegex(bench_driver.Refused, "first book"):
                    bench_driver.run_repetition(args, 1, "abc", "00", "hash-linked")



class Swap(unittest.TestCase):
    """The driver refuses a run during which the kernel swapped any page out."""

    def run_one(self, root, swaps):
        vmstat = root / "vmstat"
        vmstat.write_text("pgpgin 5\npswpin 3\npswpout 40\n", encoding="utf-8")
        fake = root / "bench.sh"
        bump = f"printf 'pswpin 3\\npswpout 41\\n' > '{vmstat}'\n" if swaps else ""
        fake.write_text(
            "#!/bin/sh\nwhile [ $# -gt 0 ]; do [ \"$1\" = --out ] && out=$2; shift; done\n"
            f"{bump}cat > \"$out\" <<'JSON'\n{json.dumps(record())}\nJSON\n", encoding="utf-8")
        fake.chmod(0o755)
        args = bench_driver.parse_args(
            ["--bench", str(fake), "--probe", "x", "--input", "x", "--prediction", "P-0001",
             "--raw-dir", str(root), "--out", "x", "--vmstat", str(vmstat)])
        bench_driver.swap_watch = bench_driver.SwapWatch(args.vmstat)
        self.addCleanup(setattr, bench_driver, "swap_watch", None)
        return bench_driver.run_process(args, 1, "abc", "00", None, "timed")

    def test_a_page_swapped_out_during_a_process_refuses_the_run(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            with self.assertRaisesRegex(bench_driver.Refused, "pswpout moved by 1 pages"):
                self.run_one(Path(directory), swaps=True)

    def test_a_still_count_passes_and_is_stated(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            self.run_one(Path(directory), swaps=False)
            self.assertIn("was 40 at the start of the run and 40 after its last process",
                          bench_driver.swap_watch.line())

    def test_a_host_with_no_vmstat_says_it_is_not_checked(self):
        watch = bench_driver.SwapWatch(SCRATCH / "no-such-vmstat")
        watch.check("repetition 1")
        self.assertIn("not read on this host", watch.line())


class Signals(unittest.TestCase):
    """A run stopped by a signal writes its own row in the run log."""

    def start(self, root, probe, bench):
        (root / "results").mkdir()
        (root / "results" / "predictions.md").write_text("## P-0001: x\n\nIt runs.\n",
                                                         encoding="utf-8")
        data = root / "input.bin"
        data.write_bytes(b"x" * 100)
        return subprocess.Popen(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "bench_driver.py"),
             "--bench", str(bench), "--probe", str(probe), "--input", str(data),
             "--prediction", "P-0001", "--raw-dir", str(root / "raw"), "--out",
             str(root / "out.md"), "--allow-dirty", "--predictions-root", str(root),
             "--run-log", str(root / "results" / "run_log.md")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def stop_when(self, driver, marker):
        for _ in range(600):
            if marker.exists() or driver.poll() is not None:
                break
            time.sleep(0.05)
        if not marker.exists():
            self.fail(f"the driver never got that far: {driver.communicate(timeout=30)}")
        driver.send_signal(signal.SIGTERM)
        _, stderr = driver.communicate(timeout=30)
        return driver.returncode, stderr

    def script(self, path, body):
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_a_signal_during_machine_check_writes_the_row(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            root = Path(directory)
            marker = root / "probe-started"
            probe = self.script(root / "probe.sh", f"touch '{marker}'\nsleep 2\n")
            driver = self.start(root, probe, root / "no-bench")
            code, stderr = self.stop_when(driver, marker)
            self.assertEqual(code, 128 + signal.SIGTERM, stderr)
            log_path = root / "results" / "run_log.md"
            self.assertTrue(log_path.is_file(), f"no run log written: {stderr}")
            log = log_path.read_text(encoding="utf-8")
            self.assertRegex(log, r"\| P-0001-\d{8}T\d{6}Z \| P-0001 \| .* \| stopped by "
                                  r"SIGTERM at machine_check; nothing from it is published")
            self.assertEqual(log.count("| P-0001-"), 1)

    @unittest.skipUnless(sys.platform == "darwin",
                         "machine_check.sh passes the fake probe only on macOS, where pinning "
                         "and the governor are recorded, not enforced")
    def test_a_signal_during_a_repetition_reaches_the_bench_and_writes_the_row(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            root = Path(directory)
            marker = root / "bench-started"
            heard = root / "bench-heard"
            bench = self.script(root / "bench.sh",
                                f"trap 'touch \"{heard}\"; exit 143' TERM\ntouch '{marker}'\n"
                                "i=0\nwhile [ $i -lt 200 ]; do sleep 0.1; i=$((i + 1)); done\n")
            probe = REPO_ROOT / "tests" / "fixtures" / "machine_check" / "fake_probe.sh"
            driver = self.start(root, probe, bench)
            code, stderr = self.stop_when(driver, marker)
            self.assertEqual(code, 128 + signal.SIGTERM, stderr)
            self.assertTrue(heard.exists(), "the bench never heard the signal")
            log_path = root / "results" / "run_log.md"
            self.assertTrue(log_path.is_file(), f"no run log written: {stderr}")
            log = log_path.read_text(encoding="utf-8")
            self.assertIn("stopped by SIGTERM at repetition 1, timed process; nothing from it "
                          "is published", log)
            self.assertFalse((root / "out.md").exists())


if __name__ == "__main__":
    unittest.main()
