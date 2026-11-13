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

import bench_driver  # noqa: E402
import check_results  # noqa: E402
import spsc_driver  # noqa: E402
import run_record  # noqa: E402
import stamp_load  # noqa: E402
import test_driver_records  # noqa: E402

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


# Three quiet repetitions and one withdrawn, as the driver records them on macOS.
SAMPLED = {"every": 2.0,
           "published": [("2026-10-31T10:00:02Z", "2026-10-31T10:00:04Z",
                          "published: 2 samples, the longest gap 2 s, no other process above "
                          "20% CPU")] * 3,
           "withdrawn": [("2026-10-31T10:00:05Z", "2026-10-31T10:00:06Z",
                          "the CPU sampler saw launchd at 25.0% CPU at 2026-10-31T10:00:05Z")]}
CROSS_CORE = ("agree: core 6's clock minus core 4's within [-120, 140] ticks, [-60.1, 70.1] ns, "
              "over 1000000 started on each core; the fastest round trip 260 ticks, 130.2 ns; "
              "0 of 2000000 trips read a later stamp below an earlier one")


def rendered(records, sampled=None, cross_core=None):
    host = records[0]["header"]["host"]
    if sampled is None and host == check_results.MAC_HOST:
        sampled = SAMPLED
    if cross_core is None and host == "debian" and records[0]["suite"] != "pipeline":
        cross_core = CROSS_CORE
    text = spsc_driver.render(records, args_for(), "P-0301-x", "2026-10-31T10:00:01Z",
                              "2026-10-31T10:00:09Z", MACHINE,
                              "python3 -B scripts/spsc_driver.py", sampled=sampled,
                              cross_core=cross_core)
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

    def test_a_mac_file_without_the_sampler_rule(self):
        self.assertIn("- interference: sampled every 2 s by the driver", QUEUES_FILE)
        self.assertIn("| withdrawn 1 |", QUEUES_FILE)
        unsampled = spsc_driver.render([record("queues")] * 3, args_for(), "P-0301-x",
                                       "2026-10-31T10:00:01Z", "2026-10-31T10:00:09Z", MACHINE,
                                       "python3 -B scripts/spsc_driver.py")
        self.assertIn("sampler-rule-unsaid", codes(stamp_load.stamp(unsampled, STDERR)))
        self.assertIn("sampler-rule-unsaid", codes(replace(
            ABLATION_FILE, check_results.SPSC_SAMPLER_RULE, "The sampler ran.")))

    def test_a_mac_file_from_the_rule_day_keeps_its_own_sampler(self):
        """From 2026-11-05 stamp_load.py wants a sampler's log beside a macOS run. The SPSC
        driver samples each repetition itself, so its file is held to that and not to a
        second log, which it never has."""
        day = "2026-11-06"
        sampled = {"every": 2.0, "withdrawn": [], "published": [
            (f"{day}T10:00:02Z", f"{day}T10:00:04Z", SAMPLED["published"][0][2])] * 3}
        text = spsc_driver.render([record("queues", s) for s in (1.0, 0.98, 1.03)], args_for(),
                                  "P-0301-x", f"{day}T10:00:01Z", f"{day}T10:00:09Z", MACHINE,
                                  "python3 -B scripts/spsc_driver.py", sampled=sampled)
        try:
            stamped = stamp_load.stamp(text, STDERR.replace("2026-10-31", day))
        except ValueError as error:
            self.fail(f"stamp_load asked an SPSC file for a second sampler: {error}")
        self.assertEqual(codes(stamped), set())
        self.assertIn("sampler-rule-unsaid", codes(replace(
            stamped, check_results.SPSC_SAMPLER_RULE, "The sampler ran.")))

    def test_a_repetition_the_sampler_did_not_show_quiet(self):
        line = re.search(r"^\| repetition 2 \|.*$", PIPELINE_FILE, re.MULTILINE).group(0)
        self.assertIn("sampler-not-quiet", codes(replace(
            PIPELINE_FILE, line, line.replace("published: ", "withdrawn: "))))
        self.assertIn("sampler-not-quiet", codes(replace(PIPELINE_FILE, line + "\n", "")))

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


class FromThePush(unittest.TestCase):
    """r4-2: each latency is from the push, not from when the message was due, and the file
    says so, with each row's late share beside its late sends."""

    def test_the_columns_say_they_are_from_the_push_and_what_that_leaves_out(self):
        for text in (QUEUES_FILE, ABLATION_FILE):
            self.assertIn("| p50 from the push | p90 from the push |", text)
            self.assertIn(check_results.SPSC_LEFT_OUT, text)
            self.assertIn("latency-push-unsaid", codes(replace(
                text, check_results.SPSC_LEFT_OUT, "")))
            self.assertIn("latency-push-unsaid", codes(replace(
                text, "| p99 from the push |", "| p99 |")))
        self.assertNotIn("## Latency", PIPELINE_FILE)

    def test_the_late_share_is_of_the_sends_the_count_covers(self):
        records = [record("queues", s) for s in (1.0, 0.98, 1.03)]
        for index, one in enumerate(records):
            for item in one["rows"]:
                # Paced counts its untimed tenth as well: 2,200 of 220,000 sends.
                if item["protocol"].startswith("paced"):
                    item["late"] = 2_200 + index
                elif item["protocol"] == "burst":
                    item["late"] = 50_000 - index
        text = rendered(records)
        self.assertEqual(codes(text), set())
        paced = re.search(r"^\| project \| paced-1000ns \|.*$", text, re.MULTILINE).group(0)
        self.assertTrue(paced.endswith("| 2,201 | 1.00% | 200,000 |"), paced)
        burst = re.search(r"^\| project \| burst \|.*$", text, re.MULTILINE).group(0)
        self.assertTrue(burst.endswith("| 49,999 | 25.0% | 200,000 |"), burst)
        for wrong in ("0.910%", "1.10%", "fast"):
            self.assertIn("late-share-wrong", codes(replace(
                text, paced, paced.replace("| 1.00% |", f"| {wrong} |"))))
        self.assertIn("latency-value-malformed", codes(replace(
            text, paced, paced.replace("| 1.00% ", ""))))

    def test_a_share_is_three_figures_and_never_in_e_notation(self):
        for share, cell in ((0, "0%"), (0.25226, "25.2%"), (0.000759, "0.0759%"),
                            (1 / 3_000_000, "0.0000333%"), (1.0, "100%")):
            self.assertEqual(check_results.share_cell(share), cell)


class EdgeSamples(unittest.TestCase):
    def test_the_edge_samples_are_kept_and_not_judged(self):
        """r5-10, as the docstrings now say: span_lines keeps the sample either side of the
        span, and a FOREIGN line there withdraws nothing; one inside the span does. The same
        in stamp_load's sampler line."""
        lines = [f"2026-11-05T09:00:{s:02d}Z sample" for s in (0, 2, 4, 6, 8)]
        edge = lines[:4] + ["2026-11-05T09:00:06Z FOREIGN 31.0 /usr/bin/a-backup"] + lines[4:]
        begin, end = "2026-11-05T09:00:01Z", "2026-11-05T09:00:05Z"
        kept = spsc_driver.span_lines(edge, begin, end)
        self.assertEqual(kept[0], lines[0])
        self.assertIn("2026-11-05T09:00:06Z FOREIGN 31.0 /usr/bin/a-backup", kept)
        self.assertIsNone(spsc_driver.span_unquiet(edge, begin, end))
        stamp_load.sampler_line("\n".join(edge), begin, end)
        inside = lines[:2] + ["2026-11-05T09:00:02Z FOREIGN 31.0 /usr/bin/a-backup"] + lines[2:]
        self.assertIn("a-backup", spsc_driver.span_unquiet(inside, begin, end))
        with self.assertRaises(ValueError):
            stamp_load.sampler_line("\n".join(inside), begin, end)


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

    def test_one_way_latencies_need_the_two_clocks_checked(self):
        records = [record("queues", s, DEBIAN_HEADER) for s in (1.0, 0.98, 1.03)]
        text = rendered(records)
        self.assertIn("- cross_core_timer: agree: ", text)
        self.assertNotIn("Sampler", text)
        self.assertEqual(check_results.check_text(text), [])
        self.assertIn("cross-core-timer-unchecked", codes(replace(
            text, "- cross_core_timer: agree: ", "- cross_core_timer: disagree: ")))
        unchecked = re.sub(r"(?m)^- cross_core_timer: .*\n", "", text)
        self.assertIn("cross-core-timer-unchecked", codes(unchecked))

    def test_a_sibling_the_machine_check_read_is_in_the_command(self):
        records = [record("queues", s, DEBIAN_HEADER) for s in (1.0, 0.98, 1.03)]
        text = replace(rendered(records), "machine_check: passed",
                       "machine_check: pass sibling-idle: cpu5 is 0.0% busy over 1 s\n"
                       "machine_check: passed")
        self.assertIn("command-differs", codes(text))
        asked = replace(text, "\npython3 -B scripts/spsc_driver.py\n",
                        "\npython3 -B scripts/spsc_driver.py --core 4 --sibling 5\n")
        self.assertNotIn("command-differs", codes(asked))

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

    def test_the_command_shown_carries_every_flag_that_ran(self):
        base = ["--suite", "queues", "--bench", "b", "--probe", "p", "--input", "i",
                "--prediction", "P-0306", "--raw-dir", "r", "--out", "results/x.md"]
        plain = spsc_driver.relative_command(spsc_driver.parse_args(base))
        self.assertNotIn("--sibling", plain)
        self.assertNotIn("--quiet", plain)
        command = spsc_driver.relative_command(spsc_driver.parse_args(
            [*base, "--core", "4", "--peer-core", "6", "--sibling", "5", "--peer-sibling", "7",
             "--quiet-before", "60"]))
        self.assertIn(" --core 4 --peer-core 6 --sibling 5 --peer-sibling 7 --quiet-before 60",
                      command)

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

    def test_a_span_is_judged_on_the_samples_around_it(self):
        lines = ["2026-10-31T10:00:00Z sample", "2026-10-31T10:00:02Z sample",
                 "2026-10-31T10:00:02Z FOREIGN 30.0 /usr/bin/other_job",
                 "2026-10-31T10:00:04Z sample", "2026-10-31T10:00:10Z sample",
                 "2026-10-31T10:00:12Z sample"]
        self.assertIsNone(spsc_driver.span_unquiet(lines, "2026-10-31T10:00:03Z",
                                                   "2026-10-31T10:00:04Z"))
        self.assertIn("saw other_job at 30.0% CPU", spsc_driver.span_unquiet(
            lines, "2026-10-31T10:00:01Z", "2026-10-31T10:00:03Z") or "")
        self.assertIn("went 6 s without a sample", spsc_driver.span_unquiet(
            lines, "2026-10-31T10:00:05Z", "2026-10-31T10:00:11Z") or "")
        self.assertIn("did not cover", spsc_driver.span_unquiet(
            lines, "2026-10-31T10:00:11Z", "2026-10-31T10:00:13Z") or "")

    def test_clocks_that_disagree_refuse_the_run(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            probe = Path(directory) / "probe.sh"
            for verdict, code in (("disagree: the offset is bounded away from 0", 1),
                                  ("agree: the bound holds 0", 1)):
                probe.write_text(
                    "#!/bin/sh\necho 'cross_core_offset: [100, 140] ticks'\n"
                    "echo 'cross_core_trips: 10 started on each core'\n"
                    "echo 'cross_core_fastest_round_trip: 40 ticks'\n"
                    "echo 'cross_core_backwards: 20 of 20'\n"
                    f"echo 'cross_core_verdict: {verdict}'\nexit {code}\n", encoding="utf-8")
                probe.chmod(0o755)
                args = argparse.Namespace(peer_core=6, core=4, probe=probe)
                with self.assertRaisesRegex(spsc_driver.Refused, verdict.split(":")[0]):
                    spsc_driver.cross_core_check(args)
        self.assertIsNone(spsc_driver.cross_core_check(argparse.Namespace(peer_core=None)))

    def test_two_repetitions_are_not_enough(self):
        with self.assertRaises(SystemExit):
            spsc_driver.parse_args(["--suite", "queues", "--bench", "b", "--probe", "p",
                                    "--input", "i", "--prediction", "P-0301", "--raw-dir", "r",
                                    "--out", "o", "--repetitions", "2"])


@unittest.skipUnless(platform.system() == "Darwin", "machine_check enforces pinning on Linux")
class EndToEnd(unittest.TestCase):
    def run_driver(self, root, fake_record, code=0, busy_first=False, every=0.2):
        """The driver on a fake bench. The sampler reads a fake ps, which shows another
        process at 50% CPU while `busy` exists; with busy_first, the first repetition makes
        it for a second, or for three samples when the sampler runs every second or more."""
        (root / "results").mkdir()
        (root / "results" / "predictions.md").write_text("## P-0301: x\n\nIt runs.\n",
                                                         encoding="utf-8")
        busy = ""
        if busy_first:
            busy = (f"if [ ! -f '{root}/once' ]; then touch '{root}/once' '{root}/busy'; "
                    f"sleep {max(1, 3 * every):g}; rm '{root}/busy'; fi\n")
        bench = root / "bench.sh"
        bench.write_text(
            "#!/bin/sh\nwhile [ $# -gt 0 ]; do [ \"$1\" = --out ] && out=$2; shift; done\n"
            f"{busy}cat > \"$out\" <<'JSON'\n{json.dumps(fake_record)}\nJSON\nexit {code}\n",
            encoding="utf-8")
        bench.chmod(0o755)
        ps = root / "ps.sh"
        ps.write_text(f"#!/bin/sh\n[ -f '{root}/busy' ] && echo '99999 1 50.0 /usr/bin/other_job'\n"
                      "exit 0\n", encoding="utf-8")
        ps.chmod(0o755)
        out = root / "results" / "spsc_x_mac_12302019.md"
        machine = FIXTURES / "machine_check"
        result = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "spsc_driver.py"),
             "--suite", fake_record["suite"], "--bench", str(bench),
             "--probe", str(machine / "fake_probe.sh"), "--input", str(machine / "input.bin"),
             "--prediction", "P-0301", "--raw-dir", str(root / "raw"), "--out", str(out),
             "--allow-dirty", "--predictions-root", str(root),
             "--run-log", str(root / "results" / "run_log.md"), "--ps", str(ps),
             "--sample-every", f"{every:g}"],
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
                # The driver leaves the outcome to stamp_load.py (r5-3).
                self.assertIn("| written, pending the load stamp: ", log)
                self.assertNotIn("| published", log)

    def test_a_repetition_beside_another_busy_process_is_withdrawn_and_rerun(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            fake = record("queues")
            fake["header"]["residency"] = "1 of 1 pages resident"
            result, out, log = self.run_driver(Path(directory), fake, busy_first=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("withdrawn: queues, repetition 1 on the Mac, exit 0; the CPU sampler "
                          "saw other_job at 50.0% CPU", log)
            # The driver leaves the outcome to stamp_load.py (r5-3).
            self.assertIn("| written, pending the load stamp: ", log)
            self.assertNotIn("| published", log)
            text = out.read_text(encoding="utf-8")
            self.assertEqual(codes(text), {"load-not-stamped"})
            self.assertIn("; 1 withdrawn under the rule", text)
            self.assertRegex(text, r"\| withdrawn 1 \| .* \| withdrawn: the CPU sampler saw "
                                   r"other_job at 50.0% CPU")
            raw = test_driver_records.run_folder_of(Path(directory) / "raw")
            self.assertTrue((raw / "queues-repetition-1-withdrawn-1.json").is_file())
            self.assertIn("FOREIGN 50.0 /usr/bin/other_job",
                          (raw / "sampler.txt").read_text(encoding="utf-8"))

    def test_a_published_file_renders_again_from_its_raw_records(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            fake = record("queues")
            fake["header"]["residency"] = "1 of 1 pages resident"
            # At most one sample in any second of the stamps, as on a real run, which samples
            # every 2 s, so that a span's samples are the same read at any time after it.
            result, out, _ = self.run_driver(Path(directory), fake, busy_first=True, every=1.0)
            self.assertEqual(result.returncode, 0, result.stderr)
            published = stamp_load.stamp(out.read_text(encoding="utf-8"), STDERR)
            root, machine = Path(directory), FIXTURES / "machine_check"
            raw, _ = test_driver_records.assert_run_record(
                self, root / "raw", published, (root / "bench.sh", machine / "fake_probe.sh"),
                (bench_driver.MACHINE_CHECK, "sampler.txt"), machine / "input.bin")
            test_driver_records.state_the_tree(
                self, raw, lambda: spsc_driver.render_again(raw, published, STDERR))
            name, again = spsc_driver.render_again(raw, published, STDERR)
            self.assertEqual((name, again), (out.name, published))
            name, again = spsc_driver.render_again(raw, published, STDERR, "a reason")
            self.assertEqual(again, published.replace(
                "Do not edit by hand.\n\n",
                f"Do not edit by hand.\n\n{bench_driver.AGAIN}a reason\n\n", 1))
            # A sampler verdict the raw sampler.txt does not give back is refused.
            line = re.search(r"^\| withdrawn 1 \|.*$", published, re.MULTILINE).group(0)
            with self.assertRaisesRegex(spsc_driver.Refused, "sampler.txt gives"):
                spsc_driver.render_again(raw, replace(published, line, line.replace(
                    "withdrawn: the CPU sampler saw", "withdrawn: it saw")), STDERR)
            line = re.search(r"^\| repetition 1 \|.*$", published, re.MULTILINE).group(0)
            with self.assertRaisesRegex(spsc_driver.Refused, "sampler.txt gives"):
                spsc_driver.render_again(raw, replace(published, line, line.replace(
                    "published: ", "published: 1 more, ")), STDERR)
            # So is a command this driver would not print for the run.
            with self.assertRaisesRegex(spsc_driver.Refused, "command"):
                spsc_driver.render_again(raw, replace(
                    published, "--repetitions 3", "--repetitions 3 --core 4"), STDERR)
            # A record of the new kind is refused with any of its parts gone (gate item 8).
            (raw / "sampler.txt").unlink()
            with self.assertRaisesRegex(run_record.Incomplete, "sampler.txt as it was kept"):
                spsc_driver.render_again(raw, published, STDERR)
            # One of the kind before, with no run.json, renders as before, from what it holds.
            (raw / "run.json").unlink()
            with self.assertRaisesRegex(spsc_driver.Refused, "no sampler.txt"):
                spsc_driver.render_again(raw, published, STDERR)

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
