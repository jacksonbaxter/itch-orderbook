#!/usr/bin/env python3
"""spsc_driver.py and check_results.py's rules for an SPSC results file.

The good files are rendered here by the driver from made-up records and stamped from the
committed measure.sh fixture, so the checks run on what the driver really writes. Each case
breaks one thing and requires that code. The end-to-end cases run the driver with a fake
bench and the fake probe, so they need macOS, where machine_check records pinning rather
than enforcing it.
"""

import argparse
import copy
import json
import platform
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402
import spsc_driver  # noqa: E402
import stamp_load  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
STDERR = (FIXTURES / "bench_results" / "measure.stderr").read_text(encoding="utf-8")
MACHINE = "machine_check: pass input-resident: 1 of 1 pages\nmachine_check: passed"
MAC_PIN = "not pinned: thread_policy_set returned code 46"
HEADER = {
    "host": "macOS (M4 Max)", "cpu": "Apple M4 Max", "kernel": "Darwin 27.2.0",
    "compiler": "Clang 23.1.4", "flags": "-O3 -DNDEBUG -std=c++23", "commit": "abc (clean)",
    "dataset": "12302019.NASDAQ_ITCH50", "dataset_sha256": "00",
    "timer": "mach_absolute_time, calibrated 24.000 MHz", "quantum": "1 ticks, 41.667 ns",
    "pair": "0.133 ticks, 5.549 ns, not subtracted from a one-way latency",
    "floor": "166.667 ns", "pinning": MAC_PIN,
    "migrations": "0 seen in 2 samples", "core": "unpinned, last ran on core 6",
    "governor": "unavailable: macOS has no cpufreq governor",
    "frequency": "unavailable: macOS exposes no core frequency on Apple silicon",
    "turbo": "unavailable: macOS exposes no boost control", "smt": "none: 16 physical, 16 logical",
    "isolation": "unavailable: macOS has no isolated cores",
    "counters": "unavailable: bench_spsc reads no counters",
    "rdpmc": "unavailable: bench_spsc reads no counters",
    "page_size": "16384 bytes, base pages", "residency": "503626 of 503626 pages resident"}
QUEUES = ("project", "rigtorp", "boost", "moodycamel", "folly", "atomic_queue", "xenium_vyukov",
          "mutex")
VARIANTS = ("shipped", "uncached", "modulo", "uncached-modulo", "padding-8", "padding-64",
            "padding-256")
# rtt ns, saturated M items/s, by queue or variant.
FIGURES = {"project": (90.0, 200.0), "rigtorp": (100.0, 180.0), "boost": (160.0, 150.0),
           "moodycamel": (120.0, 800.0), "folly": (95.0, 190.0), "atomic_queue": (150.0, 160.0),
           "xenium_vyukov": (170.0, 60.0), "mutex": (600.0, 10.0),
           "shipped": (90.0, 400.0), "uncached": (160.0, 100.0), "modulo": (92.0, 95.0),
           "uncached-modulo": (165.0, 20.0), "padding-8": (150.0, 110.0),
           "padding-64": (91.0, 390.0), "padding-256": (95.0, 380.0)}


def latency(mean, low=None, high=2_000.0):
    values = (low, low, 250.0, 500.0, high, high)
    return {"samples": 200_000, "mean_ns": mean,
            "percentiles": [{"percentile": p, "value_ns": v} for p, v in
                            zip((50.0, 90.0, 99.0, 99.9, 99.99, 100.0), values)]}


def row(queue, protocol, scale=1.0, **extra):
    rtt, rate = FIGURES.get(queue, (100.0, 100.0))
    items = 1_000_000
    if protocol == "rtt":
        ns = rtt * scale * items
    elif protocol.startswith("saturated"):
        ns = items / (rate * 1e6) * 1e9 * scale
    else:
        ns = 1e9
    result = {"queue": queue, "protocol": protocol, "items": items, "ns": ns,
              "ns_per_item": ns / items, "items_per_s": items * 1e9 / ns, "errors": 0,
              "late": 0, "self_core": 4, "peer_core": 6, "peer_pinning": MAC_PIN}
    if protocol.startswith(("paced", "burst")):
        result["latency"] = latency(80.0 * scale)
    result.update(extra)
    return result


def record(suite="queues", scale=1.0, host=None):
    """A record as bench_spsc writes it."""
    header = dict(HEADER) if host is None else host
    if suite == "queues":
        rows = [row(queue, protocol, scale) for queue in QUEUES
                for protocol in ("rtt", "saturated", "paced-1000ns", "paced-10000ns", "burst")]
    else:
        rows = [row(variant, protocol, scale) for variant in VARIANTS
                for protocol in ("rtt", "saturated", "burst")]
        rows += [row("batch-publish", "saturated-batch-32", scale),
                 row("publish-each", "saturated-batch-32", scale * 4)]
    return {"header": header, "suite": suite, "capacity": 131_072, "batch": 32,
            "element_bytes": 8, "burst_element_bytes": 64, "cache_line": 128, "peer_core": -1,
            "rotate": 0, "burst": {"start_ns": 34_200_000_000_000, "span_ns": 2_000_000_000,
                                   "speedup": 1, "messages": 1_500_000, "byte_sum": 12345},
            "migrations": 0, "peak_rss_bytes": 50_000_000, "comparison_queues": True,
            "rows": rows, "refusals": []}


def pass_of(mode, seconds):
    return {"mode": mode, "ns": seconds * 1e9, "messages": 268_744_780,
            "ops": 268_744_780 if mode == "single" else 250_000_000,
            "statuses": {"applied": 250_000_000, "no_book_effect": 18_744_780},
            "full_waits": 10 if mode == "pipelined" else 0,
            "empty_waits": 1_000 if mode == "pipelined" else 0, "book_hash": "6a0288a6a5995a4d",
            "live_orders": 1_061, "live_levels": 423, "checkpoint_hash": "5a3d6e3b95eeb051",
            "checkpoint_orders": 487, "checkpoint_levels": 211, "checkpoint_ns": 2.5e6,
            "parser_pinning": MAC_PIN if mode == "pipelined" else "no parser thread"}


def pipeline_record(single=10.0, pipelined=12.0, header=None):
    passes = [pass_of("single", single), pass_of("pipelined", pipelined),
              pass_of("decoded", single * 1.05)]
    return {"header": dict(HEADER) if header is None else header, "suite": "pipeline",
            "design": "paged-flat", "capacity": 65_536, "batch": 64, "op_bytes": 32,
            "cache_line": 128, "peer_core": -1, "rotate": 0, "checkpoint_ops": 131_000_000,
            "input": "mapped: the whole day, read-only",
            "residency": ["1 of 1 pages resident"] * 3, "migrations": 0,
            "peak_rss_bytes": 9_000_000_000, "warmup": copy.deepcopy(passes), "timed": passes,
            "refusals": []}


def args_for(prediction="P-0301"):
    return argparse.Namespace(prediction=prediction)


def rendered(records):
    text = spsc_driver.render(records, args_for(), "P-0301-x", "2026-10-31T10:00:01Z",
                              "2026-10-31T10:00:09Z", MACHINE,
                              "python3 -B scripts/spsc_driver.py")
    return stamp_load.stamp(text, STDERR)


QUEUES_FILE = rendered([record("queues", s) for s in (1.0, 0.98, 1.03)])
ABLATION_FILE = rendered([record("ablation", s) for s in (1.0, 0.98, 1.03)])
PIPELINE_FILE = rendered([pipeline_record(10.0, p) for p in (12.0, 11.5, 12.5)])
DEBIAN_HEADER = dict(HEADER, host="debian", cpu="AMD Ryzen 5 1600", kernel="Linux 6.12",
                     pinning="pinned to core 4: sched_setaffinity, read back [4]",
                     governor="performance", frequency="3.2 GHz", turbo="off",
                     isolation="cores 4-11", rdpmc="unavailable: not asked for",
                     core="4", smt="2 threads a core")


def codes(text):
    return {code for code, _ in check_results.check_text(text)}


def replace(text, old, new):
    assert old in text, old
    return text.replace(old, new)


def target_row(text, comparison):
    return re.search(rf"^\| T-SPSC-\d \| {re.escape(comparison)} \|.*\|$", text,
                     re.MULTILINE).group(0)


class Files(unittest.TestCase):
    def test_the_rendered_files_pass(self):
        for text in (QUEUES_FILE, ABLATION_FILE, PIPELINE_FILE):
            self.assertEqual(check_results.check_text(text), [])

    def test_the_book_rules_do_not_apply(self):
        self.assertNotIn("## Counters", QUEUES_FILE)
        self.assertNotIn("warmup_messages", QUEUES_FILE)

    def test_each_file_states_its_element_and_capacity(self):
        for text in (QUEUES_FILE, ABLATION_FILE, PIPELINE_FILE):
            self.assertRegex(text, r"(?m)^- element: \S")
            self.assertRegex(text, r"(?m)^- capacity: [0-9,]+ ")
        self.assertIn("Every queue is asked for the same capacity, 131,072 items", QUEUES_FILE)

    def test_the_generic_rules_still_apply(self):
        self.assertIn("input-not-resident", codes(replace(
            QUEUES_FILE, "- residency: 503626 of 503626", "- residency: 503625 of 503626")))
        self.assertIn("machine-check-failed", codes(replace(
            QUEUES_FILE, "machine_check: passed", "machine_check: refused")))
        self.assertIn("pin-claimed", codes(replace(
            QUEUES_FILE, f"- pinning: {MAC_PIN}",
            "- pinning: pinned to core 4: read back [4]")))

    def test_every_spsc_field_dropped_in_turn(self):
        for name in check_results.SPSC_FIELDS + ("burst",):
            broken = re.sub(rf"^- {name}: .*\n", "", QUEUES_FILE, flags=re.MULTILINE)
            self.assertNotEqual(broken, QUEUES_FILE, name)
            self.assertIn("header-field-missing", codes(broken), name)
        for name in check_results.SPSC_PIPELINE_FIELDS:
            broken = re.sub(rf"^- {name}: .*\n", "", PIPELINE_FILE, flags=re.MULTILINE)
            self.assertIn("header-field-missing", codes(broken), name)

    def test_an_unknown_suite(self):
        self.assertIn("suite-unknown", codes(replace(QUEUES_FILE, "- suite: queues",
                                                     "- suite: other")))

    def test_a_hurried_burst(self):
        self.assertIn("burst-hurried", codes(replace(QUEUES_FILE, ", speedup 1\n",
                                                     ", speedup 1000\n")))

    def test_a_mac_file_without_its_note(self):
        self.assertIn("host-note-missing", codes(replace(
            QUEUES_FILE, "Unpinned cross-core latency on the Mac is weak evidence", "x")))
        self.assertIn("host-note-missing", codes(replace(
            PIPELINE_FILE, "is not a pinned host.", "x")))

    def test_a_latency_cell_under_the_floor(self):
        line = re.search(r"^\| project \| burst \|.*$", QUEUES_FILE, re.MULTILINE).group(0)
        self.assertIn("percentile-below-floor",
                      codes(replace(QUEUES_FILE, line, line.replace("250.0", "125.0", 1))))
        self.assertIn("latency-value-malformed",
                      codes(replace(QUEUES_FILE, line, line.replace("250.0", "fast", 1))))

    def test_the_latency_table_dropped(self):
        broken = re.sub(r"(?ms)^## Latency\n.*?(?=^## )", "", ABLATION_FILE)
        self.assertIn("latency-table-missing", codes(broken))

    def test_a_median_outside_its_min_and_max(self):
        line = re.search(r"^\| project, rtt, ns a round trip \|.*$", QUEUES_FILE,
                         re.MULTILINE).group(0)
        cells = line.split(" | ")
        broken = replace(QUEUES_FILE, line, " | ".join([cells[0], "1.000", *cells[2:]]))
        self.assertIn("summary-inconsistent", codes(broken))

    def test_a_target_row_dropped(self):
        line = target_row(QUEUES_FILE, "project rtt / rigtorp rtt")
        self.assertIn("target-missing", codes(replace(QUEUES_FILE, line + "\n", "")))
        line = target_row(PIPELINE_FILE, "pipelined time / single-threaded time")
        self.assertIn("target-missing", codes(replace(PIPELINE_FILE, line + "\n", "")))

    def test_a_measured_ratio_that_is_not_the_summarys(self):
        line = target_row(ABLATION_FILE, "mask over %, cached: shipped / modulo")
        measured = line.split(" | ")[3]
        self.assertIn("target-verdict-wrong",
                      codes(replace(ABLATION_FILE, line, line.replace(measured, "9.99×"))))

    def test_met_or_missed_on_macos(self):
        line = target_row(QUEUES_FILE, "boost rtt / project rtt")
        verdict = line.split(" | ")[5].rstrip(" |")
        self.assertIn("target-host-blurred",
                      codes(replace(QUEUES_FILE, line, line.replace(verdict, "met"))))

    def test_a_mac_verdict_that_says_the_wrong_side(self):
        line = target_row(ABLATION_FILE, "mask over %, cached: shipped / modulo")
        self.assertIn("which it would meet", line)
        self.assertIn("target-verdict-wrong", codes(replace(
            ABLATION_FILE, line, line.replace("which it would meet", "which it would miss"))))

    def test_a_verdict_outside_the_fixed_words(self):
        line = target_row(PIPELINE_FILE, "pipelined time / single-threaded time")
        verdict = line.split(" | ")[5].rstrip(" |")
        self.assertIn("target-verdict-invalid",
                      codes(replace(PIPELINE_FILE, line, line.replace(verdict, "fine"))))

    def test_the_book_agreement_dropped(self):
        broken = replace(PIPELINE_FILE, "left the book", "ended")
        self.assertIn("book-agreement-missing", codes(broken))

    def test_the_checkpoint_dropped_or_empty(self):
        self.assertIn("At the checkpoint, just after op 131,000,000", PIPELINE_FILE)
        self.assertIn("checkpoint-missing", codes(replace(
            PIPELINE_FILE, "every pass held the book", "the passes held books")))
        self.assertIn("checkpoint-empty", codes(replace(PIPELINE_FILE, "487 orders", "0 orders")))


class Debian(unittest.TestCase):
    """On debian, a verdict is met or missed by the ratio against the bar."""

    def test_met_and_missed(self):
        fast = rendered([pipeline_record(10.0, p, DEBIAN_HEADER) for p in (12.0, 11.9, 12.1)])
        slow = rendered([pipeline_record(10.0, p, DEBIAN_HEADER) for p in (14.0, 13.9, 14.1)])
        comparison = "pipelined time / single-threaded time"
        self.assertTrue(target_row(fast, comparison).endswith("| met |"))
        self.assertIn("| missed: 1.40×", target_row(slow, comparison))
        for text in (fast, slow):
            self.assertEqual(check_results.check_text(text), [], text)
        line = target_row(slow, comparison)
        self.assertIn("target-verdict-wrong",
                      codes(replace(slow, line, re.sub(r"\| missed: .*\|$", "| met |", line))))
        line = target_row(fast, comparison)
        self.assertIn("target-verdict-wrong", codes(replace(
            fast, line, line.replace("| met |", "| missed: 1.20× against ≤ 1.31× |"))))

    def test_a_target_whose_queue_did_not_run(self):
        records = [record("queues", s, DEBIAN_HEADER) for s in (1.0, 0.98, 1.03)]
        for one in records:
            one["rows"] = [r for r in one["rows"] if r["queue"] != "rigtorp"]
        text = rendered(records)
        self.assertIn("not measured in this run: ",
                      target_row(text, "project rtt / rigtorp rtt"))
        self.assertEqual(check_results.check_text(text), [])


class Driver(unittest.TestCase):
    def test_a_good_record_is_accepted(self):
        for one in (record("queues"), record("ablation"), pipeline_record()):
            spsc_driver.validate_run(one, one["suite"])

    def test_a_wrong_item_is_refused(self):
        bad = record("queues")
        bad["rows"][3]["errors"] = 1
        with self.assertRaisesRegex(spsc_driver.Refused, "1 wrong"):
            spsc_driver.validate_run(bad, "queues")

    def test_a_hurried_burst_is_refused(self):
        bad = record("queues")
        bad["burst"]["speedup"] = 10
        with self.assertRaisesRegex(spsc_driver.Refused, "faster than the day"):
            spsc_driver.validate_run(bad, "queues")

    def test_another_suite_is_refused(self):
        with self.assertRaisesRegex(spsc_driver.Refused, "suite"):
            spsc_driver.validate_run(record("ablation"), "queues")

    def test_a_pass_that_left_another_book_is_refused(self):
        bad = pipeline_record()
        bad["timed"][1]["book_hash"] = "0000000000000000"
        with self.assertRaisesRegex(spsc_driver.Refused, "different books"):
            spsc_driver.validate_run(bad, "pipeline")

    def test_a_checkpoint_missed_or_disagreeing_is_refused(self):
        for change, reason in ((None, "no checkpoint"), ("0000000000000000", "at the checkpoint")):
            bad = pipeline_record()
            bad["warmup"][2]["checkpoint_hash"] = change
            with self.assertRaisesRegex(spsc_driver.Refused, reason):
                spsc_driver.validate_run(bad, "pipeline")
        bad = pipeline_record()
        for one in bad["warmup"] + bad["timed"]:
            one["checkpoint_orders"] = 0
        with self.assertRaisesRegex(spsc_driver.Refused, "empty at the checkpoint"):
            spsc_driver.validate_run(bad, "pipeline")
        other = pipeline_record()
        for one in other["warmup"] + other["timed"]:
            one["checkpoint_hash"] = "1111111111111111"
        with self.assertRaisesRegex(spsc_driver.Refused, "another book at the checkpoint"):
            spsc_driver.validate_together([pipeline_record(), other])

    def test_repetitions_that_disagree_are_refused(self):
        other = record("queues")
        other["rows"] = other["rows"][:-1]
        with self.assertRaisesRegex(spsc_driver.Refused, "other rows"):
            spsc_driver.validate_together([record("queues"), other])

    def test_the_ratios_are_of_the_shown_medians(self):
        line = target_row(QUEUES_FILE, "boost rtt / project rtt")
        self.assertIn("| 1.78× |", line)
        line = target_row(PIPELINE_FILE, "pipelined time / single-threaded time")
        self.assertIn("| 1.20× |", line)

    def test_two_repetitions_are_not_enough(self):
        with self.assertRaises(SystemExit):
            spsc_driver.parse_args(["--suite", "queues", "--bench", "b", "--probe", "p",
                                    "--input", "i", "--prediction", "P-0301", "--raw-dir", "r",
                                    "--out", "o", "--repetitions", "2"])


@unittest.skipUnless(platform.system() == "Darwin", "machine_check enforces pinning on Linux")
class EndToEnd(unittest.TestCase):
    def run_driver(self, root, fake_record, code=0):
        (root / "results").mkdir()
        (root / "results" / "predictions.md").write_text("## P-0301: x\n\nIt runs.\n",
                                                         encoding="utf-8")
        bench = root / "bench.sh"
        bench.write_text(
            "#!/bin/sh\nwhile [ $# -gt 0 ]; do [ \"$1\" = --out ] && out=$2; shift; done\n"
            f"cat > \"$out\" <<'JSON'\n{json.dumps(fake_record)}\nJSON\nexit {code}\n",
            encoding="utf-8")
        bench.chmod(0o755)
        out = root / "results" / "spsc_x_mac_12302019.md"
        machine = FIXTURES / "machine_check"
        result = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "spsc_driver.py"),
             "--suite", fake_record["suite"], "--bench", str(bench),
             "--probe", str(machine / "fake_probe.sh"), "--input", str(machine / "input.bin"),
             "--prediction", "P-0301", "--raw-dir", str(root / "raw"), "--out", str(out),
             "--allow-dirty", "--predictions-root", str(root),
             "--run-log", str(root / "results" / "run_log.md")],
            capture_output=True, text=True, timeout=120, check=False)
        return result, out, (root / "results" / "run_log.md").read_text(encoding="utf-8")

    def test_a_run_writes_a_file_that_passes_once_stamped(self):
        for fake in (record("queues"), pipeline_record()):
            with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
                fake["header"]["residency"] = "1 of 1 pages resident"
                result, out, log = self.run_driver(Path(directory), fake)
                self.assertEqual(result.returncode, 0, result.stderr)
                text = out.read_text(encoding="utf-8")
                self.assertEqual({code for code, _ in check_results.check_text(text)},
                                 {"load-not-stamped"})
                self.assertIn("| published |", log)

    def test_a_bench_that_refuses_refuses_the_run(self):
        refused = record("queues")
        refused["refusals"] = ["project rtt: 3 items wrong"]
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, out, log = self.run_driver(Path(directory), refused, code=3)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(out.exists())
            self.assertIn("refused at repetition 1: exited 3: project rtt: 3 items wrong", log)


if __name__ == "__main__":
    unittest.main()
