#!/usr/bin/env python3
"""Tests of check_census.py with no dependency on data/.

The census itself is faked by a small script, so that each test states exactly what
the census printed. tests/test_census_cli.py covers the real binary, and the census
area of scripts/fault_matrix.py runs the two together.
"""

import contextlib
import copy
import io
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_census as cc  # noqa: E402

GOOD_OUTPUT = """\
bytes 100
consumed 100
messages 3
type A 2
type S 1
unknown_type 0
bad_length 0
zero_length 0
decode_failures 0
end end_of_input
last_message S C locate=0 tracking=7 timestamp_ns=99 offset=86
"""

GOOD_DAY = {
    "name": "day.bin",
    "unpacked_bytes": 100,
    "messages": 3,
    "per_type_counts": {"A": 2, "S": 1},
    "last_message": {"type": "S", "event_code": "C", "stock_locate": 0, "tracking_number": 7,
                     "timestamp_ns": 99, "offset": 86},
}


class CheckCensus(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=Path(cc.REPO_ROOT, "build"))
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.day = copy.deepcopy(GOOD_DAY)
        self.output = GOOD_OUTPUT
        self.census_exit = 0

    def run_check(self):
        census = self.root / "fake_census"
        printed = self.root / "printed.txt"
        printed.write_text(self.output, encoding="utf-8")
        census.write_text(f'#!/bin/sh\ncat "{printed}"\nexit {self.census_exit}\n', encoding="utf-8")
        census.chmod(census.stat().st_mode | stat.S_IXUSR)
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"days": {"d": self.day}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cc.main(["--day", "d", "--file", "x", "--manifest", str(manifest),
                            "--census", str(census)])

    def test_exact_agreement_passes(self):
        self.assertEqual(self.run_check(), 0)

    def test_every_expected_field_is_compared(self):
        mutations = [
            ("unpacked_bytes", 101),
            ("messages", 4),
        ]
        for field, value in mutations:
            with self.subTest(field=field):
                self.day = copy.deepcopy(GOOD_DAY)
                self.day[field] = value
                if field == "messages":
                    self.day["per_type_counts"]["A"] = 3  # Keeps the manifest self-consistent.
                self.assertEqual(self.run_check(), 1)
        for field in GOOD_DAY["last_message"]:
            with self.subTest(last=field):
                self.day = copy.deepcopy(GOOD_DAY)
                self.day["last_message"][field] = "changed"
                self.assertEqual(self.run_check(), 1)

    def test_a_count_moved_between_types_fails_though_the_total_holds(self):
        self.day["per_type_counts"] = {"A": 1, "S": 2}
        self.assertEqual(self.run_check(), 1)

    def test_a_type_missing_from_the_manifest_fails(self):
        self.output = GOOD_OUTPUT.replace("type A 2\n", "type A 1\ntype B 1\n")
        self.assertEqual(self.run_check(), 1)

    def test_a_type_missing_from_the_census_fails(self):
        self.day["per_type_counts"] = {"A": 2, "E": 0, "S": 1}
        self.assertEqual(self.run_check(), 1)

    def test_nonzero_skip_counters_fail(self):
        for field in ("unknown_type", "bad_length", "zero_length", "decode_failures"):
            with self.subTest(field=field):
                self.output = GOOD_OUTPUT.replace(f"{field} 0", f"{field} 1")
                self.assertEqual(self.run_check(), 1)

    def test_no_last_message_fails(self):
        self.output = "\n".join(GOOD_OUTPUT.splitlines()[:-1]) + "\n"
        self.assertEqual(self.run_check(), 1)

    def test_census_failure_fails_even_when_the_counts_agree(self):
        self.census_exit = 1
        self.assertEqual(self.run_check(), 1)

    def test_census_crash_fails(self):
        self.census_exit = 134
        self.assertEqual(self.run_check(), 1)

    def test_unknown_census_line_is_an_error(self):
        self.output = GOOD_OUTPUT + "surprise 1\n"
        self.assertEqual(self.run_check(), 2)

    def test_truncated_census_output_is_an_error(self):
        self.output = "bytes 100\n"
        self.assertEqual(self.run_check(), 2)

    def test_unpinned_day_is_an_error(self):
        self.day["per_type_counts"] = None
        self.assertEqual(self.run_check(), 2)

    def test_manifest_that_does_not_sum_is_an_error(self):
        self.day["messages"] = 4
        self.assertEqual(self.run_check(), 2)


if __name__ == "__main__":
    unittest.main()
