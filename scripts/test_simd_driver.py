#!/usr/bin/env python3
"""Tests for simd_driver.py's own decisions, with no benchmark run: which variants each
experiment has and what each is named, where a process's span comes from, and which
processes it withdraws."""

from __future__ import annotations

import argparse
import sys
import json
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_record  # noqa: E402
import simd_driver  # noqa: E402

START = "measure.sh: start load1=1.10 host=box at=2026-11-05T09:00:00Z cpus=16"
END = "measure.sh: end load1=1.20 exit=0 at=2026-11-05T09:00:10Z"


class Sampler:
    def __init__(self, lines):
        self.lines = lines


def samples(foreign_at=None, gap=False):
    lines = []
    for second in range(-2, 14, 2):
        if gap and 2 <= second <= 6:
            continue
        at = f"2026-11-05T09:00:{second:02d}Z" if second >= 0 else "2026-11-05T08:59:58Z"
        lines.append(f"{at} sample")
        if second == foreign_at:
            lines.append(f"{at} FOREIGN 31.0 /System/mds_stores")
    return lines


class SimdDriverTest(unittest.TestCase):
    def test_every_variant_is_named_after_its_kernel(self):
        field = simd_driver.variants("field", ["neon"])
        self.assertEqual([name for name, _ in field][:2], ["scalar-w2", "neon-w2"])
        self.assertEqual(len(field), 8)
        self.assertIn(("neon-w8", ["--kernel", "neon", "--width", "8"]), field)
        names = [name for name, _ in simd_driver.variants("filter", ["neon"])]
        self.assertEqual(names, ["compare-scalar-n8", "compare-neon-n8", "bitset-n8",
                                 "compare-scalar-n64", "compare-neon-n64", "bitset-n64"])
        self.assertEqual([name for name, _ in simd_driver.variants("framing", [])],
                         ["length", "byte", "table", "constant"])
        self.assertEqual([name for name, _ in simd_driver.variants("decode", ["ssse3", "avx2"])],
                         ["scalar", "ssse3", "avx2"])

    def test_the_span_is_measure_sh_s_start_and_end(self):
        self.assertEqual(simd_driver.span_of(f"{START}\nnoise\n{END}\n"),
                         ("2026-11-05T09:00:00Z", "2026-11-05T09:00:10Z"))
        self.assertIsNone(simd_driver.span_of(f"{START}\nnoise\n"))

    def setUp(self):
        simd_driver.SAMPLE_WAIT_S = 0

    def test_it_waits_for_the_sample_after_the_end(self):
        lines = samples()[:-2]
        self.assertIn("did not cover", self.reason(sampler=Sampler(list(lines))))
        simd_driver.SAMPLE_WAIT_S = 5
        timer = threading.Timer(1.0, lambda: lines.append("2026-11-05T09:00:12Z sample"))
        timer.start()
        self.assertIsNone(self.judged(sampler=Sampler(lines)))
        timer.join()

    def reason(self, **kwargs):
        """judged()'s reason as text, so that a process wrongly kept fails an assertIn."""
        return str(self.judged(**kwargs))

    def judged(self, code=0, err=None, sampler=None, record=True):
        with tempfile.TemporaryDirectory() as scratch:
            stem = Path(scratch) / "neon-1"
            Path(f"{stem}.err").write_text(err if err is not None else f"{START}\n{END}\n",
                                           encoding="utf-8")
            if record:
                Path(f"{stem}.json").write_text("{}", encoding="utf-8")
            return simd_driver.judge(argparse.Namespace(), stem, code, sampler)

    def test_a_process_is_withdrawn_unless_sampled_quiet(self):
        self.assertIsNone(self.judged(sampler=Sampler(samples())))
        self.assertIn("mds_stores at 31.0% CPU",
                      self.reason(sampler=Sampler(samples(foreign_at=4))))
        self.assertIn("without a sample", self.reason(sampler=Sampler(samples(gap=True))))
        self.assertIn("did not cover", self.reason(sampler=Sampler(samples()[3:])))

    def test_on_linux_every_process_and_check_runs_pinned_under_measure_sh(self):
        args = argparse.Namespace(measure=Path("/q/bin/measure.sh"), input=Path("/q/day"),
                                  probe=Path("/q/bench_probe"))
        self.assertEqual(simd_driver.process_words(args, ["bench_kernels", "--core", "4"],
                                                   "Linux"),
                         ["/q/bin/measure.sh", "taskset", "-c", "4", "bench_kernels", "--core",
                          "4"])
        self.assertEqual(simd_driver.process_words(args, ["bench_kernels"], "Darwin"),
                         ["/q/bin/measure.sh", "/usr/bin/time", "-l", "bench_kernels"])
        words = simd_driver.check_words(args)
        self.assertEqual(words[:5], ["/q/bin/measure.sh", "taskset", "-c", "4", "bash"])
        self.assertTrue(words[5].endswith("scripts/machine_check.sh"))
        self.assertEqual(words[6:], ["--input", "/q/day", "--probe", "/q/bench_probe", "--core",
                                     "4"])

    def test_a_failed_or_recordless_process_refuses_the_run(self):
        """Only the sampler rule withdraws and reruns a process (r5-1): bench_kernels' exit 3,
        its refusal of its own record, a missing record or missing measure.sh lines refuse the
        run, sampler or not, so that no rerun can hide what the first process found."""
        for sampler in (None, Sampler(samples())):
            with self.assertRaisesRegex(simd_driver.Refused, "exited 3: bench_kernels: refused: "
                                                             "two work passes disagree"):
                self.judged(code=3, err=f"{START}\nbench_kernels: refused: two work passes "
                                        f"disagree\n{END}\n", sampler=sampler)
            with self.assertRaisesRegex(simd_driver.Refused, "exited 1$"):
                self.judged(code=1, sampler=sampler)
            with self.assertRaisesRegex(simd_driver.Refused, "exited 0 with no record"):
                self.judged(record=False, sampler=sampler)
            with self.assertRaisesRegex(simd_driver.Refused, "start or end line"):
                self.judged(err=f"{START}\n", sampler=sampler)

    def test_a_refused_process_ends_the_run_with_its_row(self):
        """The whole driver on a fake bench_kernels whose first process exits 3: the run stops
        there, exit 1, one run-log row that names the process and why, and no rerun."""
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            fake = root / "bench_kernels"
            fake.write_text('#!/bin/sh\nout=""; prev=""; for a in "$@"; do [ "$prev" = "--out" ] '
                            '&& out=$a; prev=$a; done\necho \'{}\' > "$out"\n'
                            'case "$out" in *-1.json) echo "bench_kernels: refused: the kernel '
                            'differed from the scalar kernel 12 times" >&2; exit 3 ;; esac\n',
                            encoding="utf-8")
            measure = root / "measure.sh"
            measure.write_text('#!/bin/sh\necho "measure.sh: start load1=0.10 host=box '
                               'at=$(date -u +%Y-%m-%dT%H:%M:%SZ) cpus=16" >&2\nshift 3\n"$@"; '
                               'code=$?\necho "measure.sh: end load1=0.10 exit=$code '
                               'at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >&2\nexit $code\n',
                               encoding="utf-8")
            fake.chmod(0o755)
            measure.chmod(0o755)
            log = root / "run_log.md"
            system, check = simd_driver.platform.system, simd_driver.machine_check
            simd_driver.platform.system = lambda: "Linux"
            simd_driver.machine_check = lambda args: "machine_check: passed"
            try:
                code = simd_driver.main(
                    ["--experiment", "framing", "--prediction", "P-0601", "--raw-dir",
                     str(root / "raw"), "--input", str(fake), "--dataset", "d",
                     "--dataset-sha256", run_record.sha256_of(fake), "--bench", str(fake),
                     "--probe", str(fake),
                     "--measure", str(measure), "--repetitions", "3", "--run-log", str(log),
                     "--allow-dirty"])
            finally:
                simd_driver.platform.system, simd_driver.machine_check = system, check
            rows = [line for line in log.read_text(encoding="utf-8").splitlines()
                    if line.startswith("| P-0601-")]
            processes = [path.name for path in (root / "raw").glob("*/*.json")
                         if path.name not in ("manifest.json", "run.json")]
            # The run's own record (gate item 8), though the run was refused.
            folder = next((root / "raw").iterdir())
            kept = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("refused: length: framing, length, repetition 1: exited 3: bench_kernels: "
                      "refused: the kernel differed from the scalar kernel 12 times; the run is "
                      "refused, not rerun", rows[0])
        self.assertEqual(processes, ["length-1.json"])
        self.assertEqual(folder.name, rows[0].split(" | ")[0].lstrip("| "))
        self.assertEqual(kept["binaries"], {str(fake): kept["dataset"]["sha256"],
                                            str(measure): kept["binaries"][str(measure)]})
        self.assertEqual(kept["outputs"], {"machine_check_before.txt": kept["outputs"][
            "machine_check_before.txt"], "machine_check_after.txt": kept["outputs"][
            "machine_check_after.txt"]})

    def test_a_dataset_that_is_not_the_hash_given_is_refused(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            fake = root / "bench_kernels"
            fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            log = root / "run_log.md"
            code = simd_driver.main(
                ["--experiment", "framing", "--prediction", "P-0601", "--raw-dir",
                 str(root / "raw"), "--input", str(fake), "--dataset", "d",
                 "--dataset-sha256", "0" * 64, "--bench", str(fake), "--probe", str(fake),
                 "--measure", str(fake), "--run-log", str(log), "--allow-dirty"])
            self.assertEqual(code, 1)
            self.assertIn(f"hashes to {run_record.sha256_of(fake)}, not the --dataset-sha256 "
                          + "0" * 64, log.read_text(encoding="utf-8"))

    def test_avx512vbmi_is_refused_where_a_pass_has_no_overread_guard(self):
        """r1-1, r4-6: decode, consume and parse refuse the kernel, with the reason in the run
        log's row, before any process runs; the guarded experiments take it."""
        for experiment in ("decode", "consume", "parse"):
            with tempfile.TemporaryDirectory() as scratch:
                root = Path(scratch)
                fake = root / "bench_kernels"
                fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                log = root / "run_log.md"
                code = simd_driver.main(
                    ["--experiment", experiment, "--prediction", "P-0601", "--raw-dir",
                     str(root / "raw"), "--input", str(fake), "--dataset", "d",
                     "--dataset-sha256", run_record.sha256_of(fake), "--bench", str(fake),
                     "--probe", str(fake), "--measure", str(fake), "--kernels",
                     "avx2,avx512vbmi", "--run-log", str(log), "--allow-dirty"])
                self.assertEqual(code, 1, experiment)
                self.assertIn(f"refused: kernel: avx512vbmi for {experiment}: "
                              f"{simd_driver.UNGUARDED[experiment]} has no guard for kOverread",
                              log.read_text(encoding="utf-8"))
                self.assertFalse((root / "raw").exists(), "no process ran")
        for experiment in ("field", "filter", "batch", "book", "verify"):
            simd_driver.refuse_unguarded(experiment, ["avx512vbmi"])
        simd_driver.refuse_unguarded("decode", ["avx2", "ssse3", "neon"])


if __name__ == "__main__":
    unittest.main()
