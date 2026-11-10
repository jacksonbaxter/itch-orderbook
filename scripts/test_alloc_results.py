#!/usr/bin/env python3
"""alloc_driver.py and check_results.py's rules for an allocator results file (T-ARENA-1).

The good files are rendered here by the driver from made-up records and stamped from the
committed measure.sh fixture, so that the checks run on what the driver really writes. Each
case breaks one thing and requires that code. The end-to-end cases run the driver with a
fake bench_replay, itch_alloc and itch_map_compare and the fake probe, so they need macOS,
where machine_check records pinning rather than enforcing it.
"""

import argparse
import json
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import alloc_driver  # noqa: E402
import check_results  # noqa: E402
import stamp_load  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
STDERR = (FIXTURES / "bench_results" / "measure.stderr").read_text(encoding="utf-8")
MACHINE = "machine_check: pass input-resident: 1 of 1 pages\nmachine_check: passed"
ARMS = check_results.ALLOC_ARMS
POOLS = check_results.ALLOC_POOLS
MESSAGES = 1000
PAGES = ("huge-if-offered requested: 16384-byte base pages for all 98304 bytes; mmap with "
         "VM_FLAGS_SUPERPAGE_SIZE_2MB refused: errno 22 (Invalid argument); so base pages; "
         "prefaulted: yes")
HEADER = {
    "host": "macOS (M4 Max)", "cpu": "Apple M4 Max", "kernel": "Darwin 27.2.0",
    "compiler": "Clang 23.1.4", "flags": "-O3 -DNDEBUG -std=c++23", "commit": "abc (clean)",
    "dataset": "12302019.NASDAQ_ITCH50", "dataset_sha256": "00",
    "timer": "mach_absolute_time, calibrated 24.000 MHz", "quantum": "1 ticks, 41.667 ns",
    "pair": "0.133 ticks, 5.549 ns, subtracted only where a column says so",
    "floor": "166.667 ns", "pinning": "not pinned: thread_policy_set returned code 46",
    "migrations": "0 seen in 2 samples", "core": "unpinned, last ran on core 6",
    "governor": "unavailable: macOS has no cpufreq governor",
    "frequency": "unavailable: macOS exposes no core frequency on Apple silicon",
    "turbo": "unavailable: macOS exposes no boost control", "smt": "none: 16 physical, 16 logical",
    "isolation": "unavailable: macOS has no isolated cores",
    "counters": "unavailable: no counters on this host",
    "rdpmc": "unavailable: no user-mode rdpmc on this host",
    "page_size": "16384 bytes obtained per page", "residency": "503626 of 503626 pages resident",
    "input": "ram: read into anonymous memory before the warm-up", "top_of_book": "not tracked",
    "node_capacity": "2408448 orders and 983040 levels"}


def percentiles(base, resolved=True):
    return [{"percentile": p, "value_ns": (base * (1 + p / 100) if resolved else None)}
            for p in (50.0, 90.0, 99.0, 99.9, 99.99)]


def record(arm, scale=1.0, faults=(0, 0)):
    header = dict(HEADER, design=f"alloc-{arm}: NodeBook on the {arm} arm")
    if arm in POOLS:
        header["pool_pages"] = f"book 1: {PAGES} | book 2: {PAGES}"
    seconds = 10.0 * scale

    def a_pass(count):
        return {"messages": MESSAGES, "seconds": seconds, "messages_per_s": MESSAGES / seconds,
                "top_changes": 0, "pauses": 0, "faults": count}
    operations = [{"op": op, "samples": 250, "mean_ns": 30.0, "percentiles": percentiles(200.0)}
                  for op in ("add", "delete", "replace", "reduce")]
    return {
        "header": header, "warmup": {"messages": MESSAGES, "book": "hash-linked",
                                     "code_messages": 100},
        "mix": {"messages": MESSAGES, "book_messages": 990, "add": 250, "delete": 250,
                "replace": 250, "reduce": 250, "other": 0},
        "throughput": a_pass(faults[0]), "latency_pass": a_pass(faults[1]),
        "instrumentation_ratio": 1.1,
        "latency": {"percentiles": percentiles(180.0, arm != "pool-index"), "mean_ns": 30.0,
                    "max_ns": 9000.0, "samples": MESSAGES, "operations": operations},
        "memory": {"source": "phys_footprint from task_info(TASK_VM_INFO)", "complete": True,
                   "first_book": True, "baseline_bytes": 1 << 30, "built_bytes": (1 << 30) + (1 << 27),
                   "peak_bytes": (1 << 30) + (1 << 28), "book_bytes": 1 << 28, "samples": 10,
                   "ager_held_bytes": None},
        "counters": {"status": [], "agreement": "unavailable: no user-mode rdpmc on this host",
                     "agreement_events": [], "per_message_published": False, "per_message": []},
        "timer": {"quantum_ns": 41.667, "pair_ns": 5.5, "floor_ns": 166.667,
                  "calibrated_hz": 24e6},
        "pinned": False, "migrations": 0, "peak_rss_bytes": 9 << 30, "refusals": []}


def checks(arm, calls=None, compare="NODE-BOOK PASS", last_hash="0123456789abcdef"):
    pool = arm in POOLS
    calls = (0 if pool else 500) if calls is None else calls
    verdict = "PASS" if pool else "REPORT"
    return {"alloc": {"node_allocations_counted_by": "the counter, the global operator new",
                      "node_book_allocations": str(calls),
                      "node_allocations_per_message": f"{calls / MESSAGES:.6f}",
                      "node_nodes_made": "500", "node_messages": str(MESSAGES),
                      "verdict": f"T-ARENA-1-ALLOC {verdict} arm={arm} book={calls} control=9"},
            "compare": {"verdict": compare, "messages_compared": str(MESSAGES),
                        "top_checks": str(2 * MESSAGES), "periodic_hash_checks": "0",
                        "divergences": "0" if compare.endswith("PASS") else "3",
                        "last_hash": last_hash}}


def args_for():
    return argparse.Namespace(prediction="P-0701", pages="huge-if-offered")


SAMPLED = {"every": 2.0, "withdrawn": [],
           "published": [(f"{arm}, repetition {i}", "2026-10-31T10:00:02Z", "2026-10-31T10:00:03Z",
                          "published: 31 samples, the longest gap 2 s, no other process above "
                          "20% CPU") for i in (1, 2, 3) for arm in ARMS]}


def rendered(runs=None, arm_checks=None, sampled=SAMPLED):
    runs = runs or {arm: [record(arm, s) for s in (1.0, 0.98, 1.03)] for arm in ARMS}
    arm_checks = arm_checks or {arm: checks(arm) for arm in runs}
    return alloc_driver.render(runs, arm_checks, args_for(), "P-0701-x",
                               "2026-10-31T10:00:01Z", "2026-10-31T10:00:09Z", MACHINE,
                               "python3 -B scripts/alloc_driver.py", sampled=sampled)


GOOD = rendered()


def codes(text):
    return {code for code, _ in check_results.check_text(text)}


def replace(text, old, new):
    assert old in text, old
    return text.replace(old, new, 1)


class Rendered(unittest.TestCase):
    def test_the_rendered_file_passes_once_stamped(self):
        self.assertEqual(codes(GOOD), {"load-not-stamped"})
        self.assertEqual(codes(stamp_load.stamp(GOOD, STDERR)), set())

    def test_the_fault_check_passes_on_no_fault(self):
        self.assertIn("- fault_check: PASS: no page fault in any timed pass of pool-index, "
                      "pool-pointer, pool-fifo, 6 passes each", GOOD)
        self.assertIn("| pool-index | 0 / 0 / 0 | 0 / 0 / 0 | zero |", GOOD)

    def test_a_pool_arm_that_faults_fails_the_check_and_says_so(self):
        runs = {arm: [record(arm, s, faults=(2, 0) if arm == "pool-fifo" else (0, 0))
                      for s in (1.0, 0.98, 1.03)] for arm in ARMS}
        text = rendered(runs)
        self.assertIn("- fault_check: FAIL: page faults in the timed passes of pool-fifo 6", text)
        self.assertIn("| pool-fifo | 2 / 2 / 2 | 0 / 0 / 0 | faulted |", text)
        self.assertEqual(codes(text), {"load-not-stamped"})
        self.assertIn("fault-check-wrong", codes(replace(text, "| faulted |", "| zero |")))
        self.assertIn("fault-check-wrong", codes(text.replace(
            "FAIL: page faults in the timed passes of pool-fifo 6", "PASS: none")))

    def test_a_heap_arms_faults_are_shown_and_not_judged(self):
        runs = {arm: [record(arm, s, faults=(7, 3) if arm == "mimalloc" else (0, 0))
                      for s in (1.0, 0.98, 1.03)] for arm in ARMS}
        text = rendered(runs)
        self.assertIn("| mimalloc | 7 / 7 / 7 | 3 / 3 / 3 | not judged: a heap arm |", text)
        self.assertIn("- fault_check: PASS", text)
        self.assertIn("fault-check-wrong", codes(replace(
            text, "| mimalloc | 7 / 7 / 7 | 3 / 3 / 3 | not judged: a heap arm |",
            "| mimalloc | 7 / 7 / 7 | 3 / 3 / 3 | zero |")))

    def test_a_pool_arms_unread_faults(self):
        self.assertIn("faults-unread", codes(replace(GOOD, "| pool-index | 0 / 0 / 0 |",
                                                     "| pool-index | unread |")))

    def test_a_fault_row_dropped(self):
        self.assertIn("fault-row-missing", codes(replace(
            GOOD, "| pool-pointer | 0 / 0 / 0 | 0 / 0 / 0 | zero |\n", "")))

    def test_a_latency_cell_under_the_floor(self):
        self.assertIn("| pool-index | all | below floor |", GOOD)
        self.assertIn("percentile-below-floor", codes(replace(
            GOOD, "| pool-index | all | below floor |", "| pool-index | all | 90.0 |")))
        self.assertIn("latency-value-malformed", codes(replace(
            GOOD, "| pool-index | all | below floor |", "| pool-index | all | fast |")))

    def test_a_latency_row_dropped(self):
        row = next(line for line in GOOD.splitlines() if line.startswith("| mimalloc | replace |"))
        self.assertIn("latency-row-missing", codes(replace(GOOD, row + "\n", "")))

    def test_a_median_outside_its_min_and_max(self):
        row = next(line for line in GOOD.splitlines()
                   if line.startswith("| pmr-pool, throughput, messages/s |"))
        cells = row.split(" | ")
        broken = " | ".join([cells[0], "1", *cells[2:]])
        self.assertIn("summary-inconsistent", codes(replace(GOOD, row, broken)))

    def test_a_pool_arm_that_allocated(self):
        arm_checks = {arm: checks(arm, calls=3 if arm == "pool-pointer" else None) for arm in ARMS}
        self.assertIn("pool-arm-allocated", codes(rendered(arm_checks=arm_checks)))

    def test_a_heap_arm_called_a_pass(self):
        row = next(line for line in GOOD.splitlines()
                   if line.startswith("| malloc-aged | the counter"))
        self.assertIn("allocation-verdict-wrong",
                      codes(replace(GOOD, row, row.replace("| REPORT |", "| PASS |"))))

    def test_a_book_that_disagreed(self):
        arm_checks = {arm: checks(arm, compare="NODE-BOOK FAIL" if arm == "pmr-pool" else
                                  "NODE-BOOK PASS") for arm in ARMS}
        self.assertIn("book-disagrees", codes(rendered(arm_checks=arm_checks)))
        arm_checks = {arm: checks(arm, last_hash="fedcba9876543210" if arm == "mimalloc" else
                                  "0123456789abcdef") for arm in ARMS}
        self.assertIn("book-disagrees", codes(rendered(arm_checks=arm_checks)))

    def test_an_arms_agreement_or_allocations_dropped(self):
        for start, code in (("| pool-fifo | PASS | 1,000 |", "book-agreement-missing"),
                            ("| pool-fifo | the counter", "allocation-row-missing")):
            row = next(line for line in GOOD.splitlines() if line.startswith(start))
            self.assertIn(code, codes(replace(GOOD, row + "\n", "")), start)

    def test_the_target_row(self):
        row = next(line for line in GOOD.splitlines() if line.startswith("| T-ARENA-1 |"))
        self.assertIn("target-verdict-invalid", codes(replace(GOOD, row, row.replace(
            "no bar to meet; fault check PASS", "met: PASS"))))
        self.assertIn("target-missing", codes(replace(GOOD, row + "\n", "")))

    def test_every_alloc_field_dropped_in_turn(self):
        for name in check_results.ALLOC_FIELDS:
            line = next(line for line in GOOD.splitlines() if line.startswith(f"- {name}: "))
            found = codes(replace(GOOD, line + "\n", ""))
            if name == "allocators":
                # With no arms named, it is held to a book file's rules, and fails them.
                self.assertIn("design-unknown", found)
            else:
                self.assertIn("header-field-missing", found, name)

    def test_an_unknown_arm(self):
        self.assertIn("allocator-unknown", codes(replace(GOOD, "- allocators: malloc-fresh",
                                                         "- allocators: arena, malloc-fresh")))

    def test_a_mac_file_without_the_sampler_rule(self):
        self.assertIn("sampler-rule-unsaid", codes(rendered(sampled=None)))
        self.assertIn("sampler-rule-unsaid",
                      codes(replace(GOOD, check_results.ALLOC_SAMPLER_RULE, "Sampled.")))

    def test_a_timed_process_the_sampler_did_not_show_quiet(self):
        self.assertIn("sampler-not-quiet", codes(replace(
            GOOD, "| pmr-pool, repetition 2 | 2026-10-31T10:00:02Z | 2026-10-31T10:00:03Z | "
                  "published:", "| pmr-pool, repetition 2 | 2026-10-31T10:00:02Z | "
                                "2026-10-31T10:00:03Z | seen:")))
        row = next(line for line in GOOD.splitlines() if line.startswith("| pmr-pool, repetition 2"))
        self.assertIn("sampler-not-quiet", codes(replace(GOOD, row + "\n", "")))

    def test_a_mac_file_without_its_note(self):
        self.assertIn("host-note-missing", codes(replace(GOOD, "is not a pinned host.", "is.")))

    def test_the_generic_rules_still_apply(self):
        self.assertIn("pin-claimed", codes(replace(
            GOOD, "- pinning: not pinned: thread_policy_set returned code 46",
            "- pinning: pinned to core 4")))
        home = "/".join(("", "Users", "someone", "x"))
        self.assertIn("private-text", codes(GOOD + f"\n{home}\n"))


class Driver(unittest.TestCase):
    def test_a_record_of_another_arm_is_refused(self):
        with self.assertRaisesRegex(alloc_driver.Refused, "not alloc-pool-index"):
            alloc_driver.validate_arm(record("pool-fifo"), "pool-index")

    def test_a_pool_record_with_no_fault_count_is_refused(self):
        broken = record("pool-pointer")
        broken["latency_pass"]["faults"] = None
        with self.assertRaisesRegex(alloc_driver.Refused, "no page faults"):
            alloc_driver.validate_arm(broken, "pool-pointer")
        heap = record("mimalloc")
        heap["latency_pass"]["faults"] = None
        alloc_driver.validate_arm(heap, "mimalloc")

    def test_a_pool_record_with_no_pages_is_refused(self):
        broken = record("pool-index")
        del broken["header"]["pool_pages"]
        with self.assertRaisesRegex(alloc_driver.Refused, "pool_pages"):
            alloc_driver.validate_arm(broken, "pool-index")

    def test_a_percentile_under_the_floor_is_refused(self):
        broken = record("malloc-fresh")
        broken["latency"]["percentiles"][0]["value_ns"] = 40.0
        with self.assertRaisesRegex(alloc_driver.Refused, "below the"):
            alloc_driver.validate_arm(broken, "malloc-fresh")

    def test_arms_on_other_capacities_are_refused(self):
        runs = {arm: [record(arm) for _ in range(3)] for arm in ARMS}
        runs["pmr-pool"][1]["header"]["node_capacity"] = "16 orders and 16 levels"
        with self.assertRaisesRegex(alloc_driver.Refused, "other capacities"):
            rendered(runs)

    def test_each_repetition_turns_the_order_by_one(self):
        arms = list(ARMS)
        self.assertEqual(alloc_driver.rotated(arms, 1), arms)
        self.assertEqual(alloc_driver.rotated(arms, 2), arms[1:] + arms[:1])
        self.assertEqual(alloc_driver.rotated(arms, 9), arms)

    def test_two_repetitions_are_not_enough(self):
        with self.assertRaises(SystemExit):
            alloc_driver.parse_args(["--bench", "b", "--probe", "p", "--alloc-tool", "a",
                                     "--compare-tool", "c", "--input", "i", "--prediction",
                                     "P-0701", "--raw-dir", "r", "--out", "o",
                                     "--repetitions", "2"])

    def test_the_pages_reach_every_bench_process(self):
        args = alloc_driver.parse_args(["--bench", "b", "--probe", "p", "--alloc-tool", "a",
                                        "--compare-tool", "c", "--input", "i", "--prediction",
                                        "P-0701", "--raw-dir", "r", "--out", "o", "--pages",
                                        "base", "--bench-arg=--core", "--bench-arg=3"])
        self.assertEqual(args.bench_arg, ["--pages", "base", "--core", "3"])
        self.assertEqual(args.arm, list(ARMS))
        self.assertEqual(args.input_mode, "ram")


FAKE_BENCH = """#!{python}
import json, sys
argv = sys.argv[1:]
options = dict(zip(argv[::2], argv[1::2]))
record = json.loads(open({template!r}).read())
arm = options["--design"][len("alloc-"):]
record["header"]["design"] = options["--design"] + ": NodeBook on the " + arm + " arm"
if arm not in {pools!r}:
    record["header"].pop("pool_pages", None)
open(options["--out"], "w").write(json.dumps(record))
sys.exit({code})
"""
FAKE_ALLOC = """#!{python}
import sys
arm = sys.argv[sys.argv.index("--allocator") + 1]
pool = arm.startswith("pool-")
calls = 0 if pool else 500
print("node_allocator", arm)
print("node_messages {messages}")
print("node_allocations_counted_by the counter, the global operator new")
print("node_book_allocations", calls)
print("node_allocations_per_message", calls / {messages})
print("node_nodes_made 500")
print("verdict T-ARENA-1-ALLOC", "PASS" if pool else "REPORT", "arm=" + arm)
"""
FAKE_COMPARE = """#!{python}
print("messages_compared {messages}")
print("top_checks {top}")
print("periodic_hash_checks 0")
print("last_hash 0123456789abcdef")
print("divergences 0")
print("verdict NODE-BOOK PASS")
"""


@unittest.skipUnless(platform.system() == "Darwin", "machine_check enforces pinning on Linux")
class EndToEnd(unittest.TestCase):
    def run_driver(self, root, code=0, busy_first=False, busy_always=False):
        """The driver on fakes. The sampler reads a fake ps, which shows another process at
        50% CPU while `busy` exists; with busy_first, the first timed process makes it, and
        with busy_always every timed process does."""
        (root / "results").mkdir()
        (root / "results" / "predictions.md").write_text("## P-0701: x\n\nIt runs.\n",
                                                         encoding="utf-8")
        template = root / "record.json"
        fake = record("pool-index")
        fake["header"]["residency"] = "1 of 1 pages resident"
        fake["latency"]["percentiles"] = percentiles(180.0)
        template.write_text(json.dumps(fake), encoding="utf-8")
        tools = {}
        for name, body in (("bench", FAKE_BENCH), ("alloc", FAKE_ALLOC),
                           ("compare", FAKE_COMPARE)):
            path = root / f"{name}.py"
            path.write_text(body.format(python=sys.executable, template=str(template),
                                        pools=POOLS, code=code, messages=MESSAGES,
                                        top=2 * MESSAGES), encoding="utf-8")
            path.chmod(0o755)
            tools[name] = path
        if busy_first or busy_always:
            once = "" if busy_always else f" and not os.path.exists('{root}/once')"
            bench = tools["bench"].read_text(encoding="utf-8").replace(
                "open(options[\"--out\"]", (
                    f"import os, time\nif options['--passes'] == 'timed'{once}:\n"
                    f"    open('{root}/once', 'w').close(); "
                    f"open('{root}/busy', 'w').close(); time.sleep(1); os.remove('{root}/busy')\n"
                    "open(options[\"--out\"]"), 1)
            tools["bench"].write_text(bench, encoding="utf-8")
        ps = root / "ps.sh"
        ps.write_text(f"#!/bin/sh\n[ -f '{root}/busy' ] && echo '99999 1 50.0 /usr/bin/other_job'\n"
                      "exit 0\n", encoding="utf-8")
        ps.chmod(0o755)
        out = root / "results" / "alloc_arms_mac_12302019.md"
        machine = FIXTURES / "machine_check"
        result = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "alloc_driver.py"),
             "--bench", str(tools["bench"]), "--probe", str(machine / "fake_probe.sh"),
             "--alloc-tool", str(tools["alloc"]), "--compare-tool", str(tools["compare"]),
             "--input", str(machine / "input.bin"), "--prediction", "P-0701",
             "--raw-dir", str(root / "raw"), "--out", str(out), "--allow-dirty",
             "--predictions-root", str(root), "--run-log", str(root / "results" / "run_log.md"),
             "--ps", str(ps), "--sample-every", "0.2"],
            capture_output=True, text=True, timeout=300, check=False)
        return result, out, (root / "results" / "run_log.md").read_text(encoding="utf-8")

    def test_a_run_writes_a_file_that_passes_once_stamped(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, out, log = self.run_driver(Path(directory))
            self.assertEqual(result.returncode, 0, result.stderr)
            text = out.read_text(encoding="utf-8")
            self.assertEqual(codes(text), {"load-not-stamped"})
            self.assertIn("| published |", log)
            raw = Path(directory) / "raw"
            self.assertTrue((raw / "alloc-pool-fifo-repetition-3-untimed.json").is_file())
            self.assertTrue((raw / "itch_map_compare-mimalloc.log").is_file())

    def test_a_timed_process_beside_another_busy_process_is_withdrawn_and_rerun(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, out, log = self.run_driver(Path(directory), busy_first=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("withdrawn: alloc arms, malloc-fresh, repetition 1 on the Mac, exit 0; "
                          "the CPU sampler saw other_job at 50.0% CPU", log)
            text = out.read_text(encoding="utf-8")
            self.assertEqual(codes(text), {"load-not-stamped"})
            self.assertIn("; 1 withdrawn under the rule", text)
            self.assertRegex(text, r"\| withdrawn 1: malloc-fresh, repetition 1 \| .* \| "
                                   r"withdrawn: the CPU sampler saw other_job at 50.0% CPU")
            raw = Path(directory) / "raw"
            self.assertTrue(
                (raw / "alloc-malloc-fresh-repetition-1-timed-withdrawn-1.json").is_file())

    def test_a_process_withdrawn_three_times_running_ends_the_run(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, out, log = self.run_driver(Path(directory), busy_always=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(out.exists())
            self.assertEqual(log.count("withdrawn: alloc arms, malloc-fresh, repetition 1 "), 3)
            self.assertIn("refused at malloc-fresh, repetition 1: withdrawn 3 times in a row "
                          "under the sampler rule", log)
            raw = Path(directory) / "raw"
            self.assertFalse((raw / "alloc-malloc-aged-repetition-1-timed.json").exists())

    def test_a_bench_that_refuses_refuses_the_run(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, out, log = self.run_driver(Path(directory), code=3)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(out.exists())
            self.assertIn("refused at repetition 1, alloc-malloc-fresh: exited 3", log)


if __name__ == "__main__":
    unittest.main()
