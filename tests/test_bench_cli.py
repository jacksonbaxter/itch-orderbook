#!/usr/bin/env python3
"""bench_probe, bench_replay and the driver together, on a fixture.

    test_bench_cli.py BENCH_REPLAY BENCH_PROBE SCRATCH_DIR FIXTURE

The driver's run needs a machine that passes machine_check.sh, which a Linux box other
than debian on its measuring core does not, so on Linux that case is skipped and the
fault matrix's fake machine covers the script.
"""

import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_results  # noqa: E402
import stamp_load  # noqa: E402

BENCH, PROBE, SCRATCH, FIXTURE = (Path(argument) for argument in sys.argv[1:5])
del sys.argv[1:5]

PROBE_KEYS = ("host", "timer", "timer_quantum", "timer_empty_pair", "timer_floor", "pin",
              "counters", "rdpmc", "arena", "governor")


def core_arguments():
    if platform.system() == "Linux":
        return ["--core", str(min(os.sched_getaffinity(0)))]
    return []


def fault_in(path):
    path.read_bytes()


# What the driver's sampler reads instead of the Mac's process table, so that a run's outcome
# does not hang on what else the Mac is doing: quiet, loud while a file is there, or always
# loud. The file says so (check_results.STAND_IN_SAMPLER), and stamp_load.py refuses it.
def stand_in_ps(loud=None):
    ps = SCRATCH / "ps.sh"
    busy = "echo '99999 1 50.0 /usr/bin/a_busy_job'"
    body = {None: "", "always": busy, "while busy": f"[ -f '{SCRATCH / 'busy'}' ] && {busy}"}
    ps.write_text(f"#!/bin/sh\n{body[loud]}\nexit 0\n", encoding="utf-8")
    ps.chmod(0o755)
    return ["--ps", str(ps), "--sample-every", "0.2"]


def busy_first_bench():
    """bench_replay behind a script that makes the stand-in loud during its first process
    only, so that one process is withdrawn and run again."""
    bench = SCRATCH / "bench.sh"
    once, busy = SCRATCH / "once", SCRATCH / "busy"
    # The first process is held half a second, so that the sampler, every 0.2 s, sees the
    # stand-in loud inside its span; the file is gone before the next one starts.
    bench.write_text(f"#!/bin/sh\n[ -f '{once}' ] && exec '{BENCH}' \"$@\"\n: > '{once}'\n"
                     f": > '{busy}'\nsleep 0.5\n'{BENCH}' \"$@\"\ncode=$?\nrm -f '{busy}'\n"
                     "exit $code\n", encoding="utf-8")
    bench.chmod(0o755)
    return bench


class Cli(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        SCRATCH.mkdir(parents=True)
        fault_in(FIXTURE)

    def replay(self, *extra):
        out = SCRATCH / "run.json"
        result = subprocess.run([str(BENCH), "--input", str(FIXTURE), "--out", str(out),
                                 *core_arguments(), *extra],
                                capture_output=True, text=True, timeout=120, check=False)
        return result, out

    def test_probe_answers_every_question(self):
        result = subprocess.run([str(PROBE)], capture_output=True, text=True, timeout=60,
                                check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        keys = {line.split(":")[0] for line in result.stdout.splitlines()}
        for key in PROBE_KEYS:
            self.assertIn(key, keys)

    def test_probe_check_reads_residency(self):
        result = subprocess.run([str(PROBE), "--check", str(FIXTURE)], capture_output=True,
                                text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("timer_quantum_ns: ", result.stdout)
        self.assertRegex(result.stdout, r"residency: \d+ of \d+ pages resident")

    def test_a_replay_writes_a_whole_record(self):
        result, out = self.replay()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(record["refusals"], [])
        one_pass = record["warmup"]["messages"]
        self.assertGreater(one_pass, 0)
        self.assertEqual(record["throughput"]["messages"], one_pass)
        self.assertEqual(record["latency_pass"]["messages"], one_pass)
        self.assertEqual(record["latency"]["samples"], one_pass)
        self.assertGreater(record["instrumentation_ratio"], 0.0)
        for field in check_results.REQUIRED_FIELDS[:24]:
            self.assertIn(field, record["header"])
        for entry in record["latency"]["percentiles"]:
            if entry["value_ns"] is not None:
                self.assertGreaterEqual(entry["value_ns"], record["timer"]["floor_ns"])

    def test_a_tiny_book_is_refused_not_timed(self):
        result, out = self.replay("--orders", "1", "--levels", "1")
        self.assertEqual(result.returncode, 3, result.stdout)
        record = json.loads(out.read_text(encoding="utf-8"))
        self.assertTrue(record["refusals"])
        self.assertIn("rejected", record["refusals"][0])

    def test_a_missing_input_is_a_usage_error(self):
        result = subprocess.run([str(BENCH), "--input", str(SCRATCH / "none"), "--out",
                                 str(SCRATCH / "x.json")], capture_output=True, text=True,
                                timeout=60, check=False)
        self.assertEqual(result.returncode, 2)

    def test_every_design_replays_the_same_mix_and_top_of_book(self):
        seen = {}
        for design in ("hash-linked", "hash-flat", "paged-linked", "paged-flat", "map-fresh",
                       "map-aged"):
            for mode in ("ram", "mapped"):
                result, out = self.replay("--design", design, "--input-mode", mode,
                                          "--track-top", "on", "--passes", "timed")
                self.assertEqual(result.returncode, 0, result.stderr)
                record = json.loads(out.read_text(encoding="utf-8"))
                self.assertTrue(record["header"]["design"].startswith(f"{design}: "))
                self.assertEqual(record["warmup"]["book"],
                                 "hash-linked" if design.startswith("map-") else "this design")
                self.assertTrue(record["header"]["input"].startswith(f"{mode}: "))
                self.assertTrue(record["header"]["top_of_book"].startswith("tracked: "))
                seen[(design, mode)] = (json.dumps(record["mix"], sort_keys=True),
                                        record["throughput"]["top_changes"],
                                        record["latency_pass"]["top_changes"])
                spans = sum(op["samples"] for op in record["latency"]["operations"])
                mix = record["mix"]
                self.assertEqual(spans, sum(mix[op] for op in ("add", "delete", "replace",
                                                               "reduce")))
        self.assertEqual(len(set(seen.values())), 1, seen)
        self.assertGreater(next(iter(seen.values()))[1], 0)

    def test_the_untimed_process_measures_the_first_book(self):
        result, out = self.replay("--design", "paged-flat", "--passes", "untimed")
        self.assertEqual(result.returncode, 0, result.stderr)
        memory = json.loads(out.read_text(encoding="utf-8"))["memory"]
        self.assertTrue(memory["complete"])
        self.assertTrue(memory["first_book"])
        # The pages' slots alone are 418 MiB, so a baseline read after the book was built
        # would leave next to nothing.
        self.assertGreater(memory["book_bytes"], 400 * 1024 ** 2)
        self.assertEqual(memory["book_bytes"], memory["peak_bytes"] - memory["baseline_bytes"])

    def test_a_throughput_only_process_runs_no_latency_pass(self):
        result, out = self.replay("--design", "map-aged", "--input-mode", "ram", "--passes",
                                  "throughput")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(record["refusals"], [])
        self.assertEqual(record["throughput"]["messages"], record["warmup"]["messages"])
        self.assertIsNone(record["latency_pass"])
        self.assertIsNone(record["instrumentation_ratio"])
        self.assertEqual(record["latency"]["samples"], 0)
        self.assertEqual(record["header"]["page_size"],
                         "unavailable: no sample arena in a process with no latency pass")

    def test_the_aged_map_book_reports_its_ballast(self):
        result, out = self.replay("--design", "map-aged", "--passes", "untimed")
        self.assertEqual(result.returncode, 0, result.stderr)
        memory = json.loads(out.read_text(encoding="utf-8"))["memory"]
        self.assertGreater(memory["ager_held_bytes"], 0)

    def test_an_unknown_design_is_a_usage_error(self):
        result, _ = self.replay("--design", "tree-flat")
        self.assertEqual(result.returncode, 2)
        result, _ = self.replay("--input-mode", "disk")
        self.assertEqual(result.returncode, 2)

    def test_a_chase_writes_a_whole_record(self):
        out = SCRATCH / "chase.json"
        result = subprocess.run([str(BENCH.parent / "bench_chase"), "--out", str(out), "--loads",
                                 "2000", "--sizes", "64K,128K", *core_arguments()],
                                capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(record["raw_event"], "0x0843")
        self.assertEqual([row["bytes"] for row in record["rows"]], [65536, 131072])
        self.assertEqual(record["refusals"], [])

    def test_a_chase_records_the_event_it_was_given(self):
        out = SCRATCH / "chase.json"
        result = subprocess.run([str(BENCH.parent / "bench_chase"), "--out", str(out), "--loads",
                                 "2000", "--sizes", "64K", "--raw-event", "0x0143",
                                 *core_arguments()],
                                capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["raw_event"], "0x0143")

    def test_a_chase_refuses_a_bad_size(self):
        result = subprocess.run([str(BENCH.parent / "bench_chase"), "--out",
                                 str(SCRATCH / "chase.json"), "--sizes", "64"],
                                capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 2)

    @unittest.skipUnless(platform.system() == "Darwin", "machine_check needs a measuring machine")
    def test_the_driver_writes_a_file_that_stamps_and_passes(self):
        predictions = SCRATCH / "records"
        (predictions / "results").mkdir(parents=True)
        (predictions / "results" / "predictions.md").write_text(
            "## P-0999: fixture\n\n- Prediction: it runs.\n", encoding="utf-8")
        out = SCRATCH / "results" / "bench_fixture.md"
        run_log = predictions / "results" / "run_log.md"
        result = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "bench_driver.py"),
             "--bench", str(BENCH), "--probe", str(PROBE), "--input", str(FIXTURE),
             "--prediction", "P-0999", "--repetitions", "3", "--raw-dir", str(SCRATCH / "raw"),
             "--out", str(out), "--allow-dirty", "--predictions-root", str(predictions),
             "--run-log", str(run_log), *stand_in_ps()],
            capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = out.read_text(encoding="utf-8")
        self.assertEqual({code for code, _ in check_results.check_text(text)},
                         {"load-not-stamped", "sampler-stand-in"})
        self.assertIn(check_results.STAND_IN_SAMPLER, text)
        self.assertIn("published", run_log.read_text(encoding="utf-8"))
        fields = check_results.parse_header(text)
        # measure.sh's lines bracket the run, and a quiet sampler's lines every 2 s bracket
        # those, as a macOS run from 2026-11-05 on needs.
        stamp = "%Y-%m-%dT%H:%M:%SZ"
        second = datetime.timedelta(seconds=1)
        opened = datetime.datetime.strptime(fields["run_started"], stamp) - second
        closed = datetime.datetime.strptime(fields["run_finished"], stamp) + second
        stderr = SCRATCH / "measure.stderr"
        stderr.write_text(
            f"measure.sh: start load1=1.20 host=some-machine at={opened.strftime(stamp)}\n"
            f"measure.sh: end load1=1.30 exit=0 at={closed.strftime(stamp)}\n", encoding="utf-8")
        sampler = SCRATCH / "sampler.log"
        tick, lines = opened - 2 * second, []
        while tick <= closed + 2 * second:
            lines.append(f"{tick.strftime(stamp)} sample\n")
            tick += 2 * second
        sampler.write_text("".join(lines), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "stamp_load.py"), "--results",
             str(out), "--stderr", str(stderr), "--sampler", str(sampler), "--run-log",
             str(run_log)], capture_output=True, text=True, timeout=60, check=False)
        # A stand-in sampler's file is a test's: stamp_load refuses it, for that alone, and
        # says so in the test's own run log.
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("stamp_load.py refused it: sampler-stand-in",
                      run_log.read_text(encoding="utf-8"))
        self.assertEqual(re.findall(r"FAIL (\S+):", result.stderr), ["sampler-stand-in"])
        stamped = stamp_load.stamp(text, stderr.read_text(encoding="utf-8"),
                                   sampler.read_text(encoding="utf-8"))
        self.assertNotIn("some-machine", stamped)
        self.assertIn(f"host={fields['host']}", stamped)
        self.assertEqual({code for code, _ in check_results.check_text(stamped)},
                         {"sampler-stand-in"})

    @unittest.skipUnless(platform.system() == "Darwin", "machine_check needs a measuring machine")
    def test_the_driver_writes_a_file_for_each_design(self):
        predictions = SCRATCH / "records"
        (predictions / "results").mkdir(parents=True)
        (predictions / "results" / "predictions.md").write_text(
            "## P-0999: fixture\n\n- Prediction: it runs.\n", encoding="utf-8")
        out = SCRATCH / "results" / "bench_book_{design}_ram.md"
        result = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "bench_driver.py"),
             "--bench", str(BENCH), "--probe", str(PROBE), "--input", str(FIXTURE),
             "--prediction", "P-0999", "--repetitions", "3", "--raw-dir", str(SCRATCH / "raw"),
             "--out", str(out), "--design", "paged-linked", "--design", "map-fresh",
             "--design", "map-aged", "--input-mode", "ram", "--allow-dirty",
             "--predictions-root", str(predictions),
             "--run-log", str(predictions / "results" / "run_log.md"), *stand_in_ps()],
            capture_output=True, text=True, timeout=300, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        for design in ("paged-linked", "map-fresh", "map-aged"):
            text = Path(str(out).replace("{design}", design)).read_text(encoding="utf-8")
            self.assertEqual({code for code, _ in check_results.check_text(text)},
                             {"load-not-stamped", "sampler-stand-in"}, design)
            self.assertIn(f"- design: {design}: ", text)
        fast = Path(str(out).replace("{design}", "paged-linked")).read_text(encoding="utf-8")
        self.assertRegex(fast, r"\| map-fresh \| [0-9.]+ \|")
        self.assertRegex(fast, r"\| map-aged \| [0-9.]+ \|")

    @unittest.skipUnless(platform.system() == "Darwin", "machine_check needs a measuring machine")
    def test_the_driver_runs_a_design_throughput_only(self):
        predictions = SCRATCH / "records"
        (predictions / "results").mkdir(parents=True)
        (predictions / "results" / "predictions.md").write_text(
            "## P-0999: fixture\n\n- Prediction: it runs.\n", encoding="utf-8")
        out = SCRATCH / "results" / "bench_book_{design}_ram.md"
        result = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "bench_driver.py"),
             "--bench", str(BENCH), "--probe", str(PROBE), "--input", str(FIXTURE),
             "--prediction", "P-0999", "--repetitions", "3", "--raw-dir", str(SCRATCH / "raw"),
             "--out", str(out), "--design", "paged-linked", "--design", "map-fresh",
             "--throughput-only", "map-fresh", "--throughput-only-why", "a test of the option",
             "--input-mode", "ram", "--allow-dirty", "--predictions-root", str(predictions),
             "--run-log", str(predictions / "results" / "run_log.md"), *stand_in_ps()],
            capture_output=True, text=True, timeout=300, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        texts = {design: Path(str(out).replace("{design}", design)).read_text(encoding="utf-8")
                 for design in ("paged-linked", "map-fresh")}
        for design, text in texts.items():
            self.assertEqual({code for code, _ in check_results.check_text(text)},
                             {"load-not-stamped", "sampler-stand-in"}, design)
        self.assertIn("- passes: all: ", texts["paged-linked"])
        self.assertRegex(texts["paged-linked"], r"\| p50 \| ")
        self.assertRegex(texts["paged-linked"], r"\| map-fresh \| [0-9.]+ \|")
        self.assertIn("- passes: throughput only: ", texts["map-fresh"])
        self.assertIn("Why: a test of the option", texts["map-fresh"])
        self.assertNotRegex(texts["map-fresh"], r"\| (p50|max|add) \| ")
        for index in (1, 2, 3):
            # The run's raw files are in its own folder, named by its run id.
            path, = (SCRATCH / "raw").glob(f"*/map-fresh-repetition-{index}-throughput.json")
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNone(record["latency_pass"])

    @unittest.skipUnless(platform.system() == "Darwin", "machine_check needs a measuring machine")
    def test_a_refused_run_stays_in_the_log(self):
        predictions = SCRATCH / "records"
        (predictions / "results").mkdir(parents=True)
        run_log = predictions / "results" / "run_log.md"
        result = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "bench_driver.py"),
             "--bench", str(BENCH), "--probe", str(PROBE), "--input", str(FIXTURE),
             "--prediction", "P-0998", "--raw-dir", str(SCRATCH / "raw"),
             "--out", str(SCRATCH / "never.md"), "--allow-dirty",
             "--predictions-root", str(predictions), "--run-log", str(run_log), *stand_in_ps()],
            capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((SCRATCH / "never.md").exists())
        self.assertIn("refused at prediction", run_log.read_text(encoding="utf-8"))

    def sampled_run(self, bench, loud):
        predictions = SCRATCH / "records"
        (predictions / "results").mkdir(parents=True)
        (predictions / "results" / "predictions.md").write_text(
            "## P-0999: fixture\n\n- Prediction: it runs.\n", encoding="utf-8")
        out = SCRATCH / "results" / "bench_fixture.md"
        run_log = predictions / "results" / "run_log.md"
        result = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "bench_driver.py"),
             "--bench", str(bench), "--probe", str(PROBE), "--input", str(FIXTURE),
             "--prediction", "P-0999", "--repetitions", "3", "--raw-dir", str(SCRATCH / "raw"),
             "--out", str(out), "--allow-dirty", "--predictions-root", str(predictions),
             "--run-log", str(run_log), *stand_in_ps(loud)],
            capture_output=True, text=True, timeout=300, check=False)
        return result, out, run_log.read_text(encoding="utf-8") if run_log.is_file() else ""

    @unittest.skipUnless(platform.system() == "Darwin", "the Mac's sampler rule runs on macOS")
    def test_a_process_the_stand_in_shows_beside_a_busy_one_is_withdrawn_then_run_again(self):
        result, out, log = self.sampled_run(busy_first_bench(), "while busy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.count("| withdrawn: "), 1, log)
        self.assertIn("the CPU sampler saw another process at 50.0% CPU", log)
        text = out.read_text(encoding="utf-8")
        self.assertIn("; 1 withdrawn under the rule", text)
        self.assertEqual({code for code, _ in check_results.check_text(text)},
                         {"load-not-stamped", "sampler-stand-in"})

    @unittest.skipUnless(platform.system() == "Darwin", "the Mac's sampler rule runs on macOS")
    def test_three_withdrawn_in_a_row_refuse_the_run(self):
        result, out, log = self.sampled_run(BENCH, "always")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(out.exists())
        self.assertEqual(log.count("| withdrawn: "), 3, log)
        self.assertIn("withdrawn 3 times in a row under the sampler rule", result.stderr)


if __name__ == "__main__":
    unittest.main()
