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
import subprocess
import sys
import tempfile
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


if __name__ == "__main__":
    unittest.main()
