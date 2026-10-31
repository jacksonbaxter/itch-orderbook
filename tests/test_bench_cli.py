#!/usr/bin/env python3
"""bench_probe, bench_replay and the driver together, on a fixture.

    test_bench_cli.py BENCH_REPLAY BENCH_PROBE SCRATCH_DIR FIXTURE

The driver's run needs a machine that passes machine_check.sh, which a Linux box other
than debian on its measuring core does not, so on Linux that case is skipped and the
fault matrix's fake machine covers the script.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_results  # noqa: E402

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
             "--run-log", str(run_log)],
            capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = out.read_text(encoding="utf-8")
        self.assertEqual({code for code, _ in check_results.check_text(text)},
                         {"load-not-stamped"})
        self.assertIn("published", run_log.read_text(encoding="utf-8"))
        fields = check_results.parse_header(text)
        stderr = SCRATCH / "measure.stderr"
        stderr.write_text(
            "measure.sh: start load1=1.20 host=some-machine at=2000-01-01T00:00:00Z\n"
            "measure.sh: end load1=1.30 exit=0 at=2999-01-01T00:00:00Z\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "stamp_load.py"), "--results",
             str(out), "--stderr", str(stderr)], capture_output=True, text=True, timeout=60,
            check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        stamped = out.read_text(encoding="utf-8")
        self.assertNotIn("some-machine", stamped)
        self.assertIn(f"host={fields['host']}", stamped)
        self.assertEqual(check_results.check_text(stamped), [])

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
             "--predictions-root", str(predictions), "--run-log", str(run_log)],
            capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((SCRATCH / "never.md").exists())
        self.assertIn("refused at prediction", run_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
