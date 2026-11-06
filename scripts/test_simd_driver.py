#!/usr/bin/env python3
"""Tests for simd_driver.py's own decisions, with no benchmark run: which variants each
experiment has and what each is named, where a process's span comes from, and which
processes it withdraws."""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
        self.assertIn("start or end line", self.reason(err=f"{START}\n",
                                                       sampler=Sampler(samples())))

    def test_a_failed_or_recordless_process_is_withdrawn(self):
        self.assertEqual(self.judged(code=3, err=f"{START}\nbench_kernels: refused: two work "
                                                 f"passes disagree\n{END}\n"),
                         "exited 3: bench_kernels: refused: two work passes disagree")
        self.assertEqual(self.judged(record=False), "exited 0 with no record")


if __name__ == "__main__":
    unittest.main()
