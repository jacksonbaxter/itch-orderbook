#!/usr/bin/env python3
"""results_table.py on a copy of the real results: what it must show, and what it must refuse."""

import contextlib
import io
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import results_table  # noqa: E402

REPO_ROOT = results_table.REPO_ROOT
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
ORACLE = "results/oracle_12302019.md"
TRADES = "results/trades_10302019.md"


class ResultsTable(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        shutil.copytree(REPO_ROOT / "results", self.root / "results")
        shutil.copy(REPO_ROOT / "README.md", self.root / "README.md")

    def run_table(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = results_table.main(["--root", str(self.root), *extra])
        return code, out.getvalue()

    def edit(self, name, old, new):
        path = self.root / name
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, old)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def row(self, check, day):
        code, output = self.run_table()
        self.assertEqual(code, 0, output)
        rows = [line for line in output.splitlines()
                if line.startswith(f"| {check} | {day} |")]
        self.assertEqual(len(rows), 1, output)
        return rows[0]

    def test_the_repository_passes(self):
        code, output = self.run_table("--check")
        self.assertEqual(code, 0, output)

    def test_every_failing_verdict_is_shown(self):
        self.assertIn("| **FAIL** |", self.row("T-INV-2", "12302019"))
        for day in ("10302019", "12302019"):
            row = self.row("T-TRADE-3", day)
            self.assertIn("| **FAIL** |", row)
            self.assertIn("MKL and NVR", row)
            self.assertRegex(row, r"shares equal on ([\d,]+) of \1\b")
        self.assertIn("| **NOT AVAILABLE** |", self.row("T-TRADE-2", "10302019"))
        self.assertIn("| PASS |", self.row("T-TRADE-2", "12302019"))

    def test_harness_rows_carry_their_label_and_host(self):
        _, output = self.run_table()
        rows = [line for line in output.splitlines() if line.startswith("| Harness proof")]
        self.assertEqual(len(rows), 2, output)
        self.assertIn("| debian |", rows[0])
        self.assertIn("| macOS (M4 Max) |", rows[1])

    def test_a_failed_run_stays_in_the_run_table(self):
        _, output = self.run_table()
        self.assertIn("`chase verdict FAIL`", output)
        self.assertIn("`refused at repetition 1`", output)

    def test_a_changed_verdict_changes_the_table_and_fails_the_check(self):
        self.edit(ORACLE, "Verdict, strict: **FAIL**", "Verdict, strict: **PASS**")
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)
        self.assertIn("results_table: FAIL README.md:", output)

    def test_a_row_typed_over_fails(self):
        self.edit("README.md", "| PASS | 0 divergences", "| PASS | no divergences at all")
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)

    def test_a_line_typed_in_fails(self):
        self.edit("README.md", results_table.END, "| one more row |\n" + results_table.END)
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)

    def test_write_mends_what_check_refused(self):
        self.edit("README.md", "| PASS | 0 divergences", "| PASS | no divergences at all")
        self.assertEqual(self.run_table("--write")[0], 0)
        self.assertEqual(self.run_table("--check")[0], 0)

    def test_a_missing_marker_fails(self):
        self.edit("README.md", results_table.END + "\n", "")
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)
        self.assertIn("marker", output)

    def test_a_results_file_with_no_reader_is_refused(self):
        (self.root / "results" / "new_kind.md").write_text("# New\n", encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn("results/new_kind.md", output)

    def test_a_verdict_no_row_took_up_is_refused(self):
        self.edit(ORACLE, "## T-ALLOC-1:", "## T-NEW-1: new\n\nVerdict: **FAIL**\n\n## T-ALLOC-1:")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn("5 verdicts in the file, 4 rows", output)

    def test_a_trade_verdict_dropped_from_its_table_is_refused(self):
        self.edit(TRADES, "| T-TRADE-3 | **FAIL** | 4 |\n", "")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)

    def test_a_benchmark_with_no_harness_label_is_refused(self):
        name = "results/bench_replay_stub_mac_12302019.md"
        text = (self.root / name).read_text(encoding="utf-8")
        (self.root / name).write_text(text.replace(results_table.HARNESS_LABEL, "A result."),
                                      encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn("harness proof", output)

    def test_no_stray_markup_in_generated_cells(self):
        _, output = self.run_table()
        for line in output.splitlines():
            if line.startswith("| T-"):
                self.assertEqual(len(re.findall(r" \| ", line)), 6, line)


if __name__ == "__main__":
    unittest.main()
