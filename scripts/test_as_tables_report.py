#!/usr/bin/env python3
"""as_tables_report.py on tool output written here, with every expected verdict worked by hand."""

import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import as_tables_report as report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)

# The paper's own figures as the measurement, so every stochastic column is at difference 0.
PAPER_ROWS = {
    "0.1": ("65.0 6.6 0.08 2.9", "68.4 12.7 0.26 8.4"),
    "0.01": ("68.6 8.7 0.12 5.1", "68.8 12.8 0.09 8.7"),
    "1.0": ("31.4 5.0 0.02 1.7", "44.0 11.0 0.00 5.1"),
}


def raw(spreads=(1.4917704228, 1.3490085437, 3.0216512475), rows=None):
    rows = rows or PAPER_ROWS
    lines = ["as_tables paths 1000 seed 1 s 100 T 1 sigma 2 dt 0.005 k 1.5 A 140 steps 200"]
    for (gamma, (inventory, symmetric)), spread in zip(rows.items(), spreads, strict=True):
        lines += [f"table gamma {gamma}", f"spread_average {spread:.10f}",
                  f"spread_grid_simulated {spread:.10f}",
                  f"spread_grid_with_t_equal_T {spread:.10f}",
                  f"spread_grid_from_dt {spread:.10f}"]
        for name, values in (("inventory", inventory), ("symmetric", symmetric)):
            p, ps, q, qs = values.split()
            lines.append(f"strategy {name} profit_mean {p} profit_std {ps} final_q_mean {q} "
                         f"final_q_std {qs} fills_per_path 50.000")
    return "\n".join(lines) + "\n"


HEADER = ["# T-MM-2", "", "- prediction: P-0201", "- run_id: P-0201-20261104T000000Z",
          "- Raw log: `raw.log`"]


def body(text):
    return "\n".join(report.render_body(report.parse_raw(text)))


class Spread(unittest.TestCase):
    def test_all_three_at_two_decimals_pass(self):
        text = body(raw())
        self.assertIn("**Verdict: PASS**", text)
        self.assertIn("3 of 3 average spreads match", text)

    def test_the_left_point_grid_at_gamma_one_fails_by_a_hundredth(self):
        text = body(raw(spreads=(1.4917704228, 1.3490085437, 3.0316512475)))
        self.assertIn("**Verdict: FAIL**", text)
        self.assertIn("| 1 | 3.02 | 3.0317 | 3.03 | does not match |", text)
        self.assertIn("| 0.1 | 1.49 | 1.4918 | 1.49 | matches |", text)

    def test_a_half_rounds_up_as_printed_tables_round(self):
        # 1.345 rounds to 1.35, where Python's round() would give 1.34.
        self.assertEqual(str(report.round_half_up(1.345, 2)), "1.35")
        text = body(raw(spreads=(1.4917704228, 1.345, 3.0216512475)))
        self.assertIn("| 0.01 | 1.35 | 1.3450 | 1.35 | matches |", text)

    def test_a_spread_off_by_a_thousandth_past_the_rounding_fails(self):
        text = body(raw(spreads=(1.4950000001, 1.3490085437, 3.0216512475)))
        self.assertIn("| 0.1 | 1.49 | 1.4950 | 1.50 | does not match |", text)


class Columns(unittest.TestCase):
    def test_standard_errors_are_the_difference_of_two_estimates(self):
        # A mean: sqrt(6² + 8²)/sqrt(1000) = 10/31.6228 = 0.3162.
        difference, error, z, allowance, ok = report.compare(66.0, 6.0, "65.0", 8.0, 1000, True)
        self.assertAlmostEqual(error, 10 / 1000 ** 0.5, places=12)
        self.assertAlmostEqual(difference, 1.0, places=12)
        self.assertAlmostEqual(z, 1.0 / (10 / 1000 ** 0.5), places=9)
        self.assertAlmostEqual(allowance, 2 * error + 0.05, places=12)
        self.assertFalse(ok)  # 1.0 > 0.6325 + 0.05
        # A standard deviation: sqrt(6² + 8²)/sqrt(2 × 999).
        _, error, _, _, _ = report.compare(6.0, 6.0, "8.0", 8.0, 1000, False)
        self.assertAlmostEqual(error, 10 / (2 * 999) ** 0.5, places=12)

    def test_within_two_errors_and_half_a_unit_is_consistent(self):
        error = 10 / 1000 ** 0.5
        inside = 65.0 + 2 * error + 0.049
        outside = 65.0 + 2 * error + 0.051
        self.assertTrue(report.compare(inside, 6.0, "65.0", 8.0, 1000, True)[4])
        self.assertFalse(report.compare(outside, 6.0, "65.0", 8.0, 1000, True)[4])

    def test_a_column_three_errors_out_is_named_in_the_summary(self):
        rows = dict(PAPER_ROWS)
        rows["0.1"] = ("67.0 6.6 0.08 2.9", rows["0.1"][1])  # 2.0 from 65.0, z about 6.
        text = body(raw(rows=rows))
        self.assertIn("| inventory | Profit | 65.0 | 67.000 | +2.000 |", text)
        self.assertIn("Other columns: 23 of 24 within 2 standard errors; outside: Table 1 "
                      "inventory Profit.", text)

    def test_the_paper_as_measured_is_consistent_everywhere(self):
        self.assertIn("Other columns: 24 of 24 within 2 standard errors.", body(raw()))


def fake_tool(directory, text):
    tool = Path(directory) / "fake_as_tables"
    output = Path(directory) / "output.txt"
    output.write_text(text, encoding="utf-8")
    tool.write_text(f"#!/bin/sh\ncat '{output}'\n", encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    return tool


def check(path, tool):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = report.main(["check", "--tool", str(tool), str(path)])
    return code, output.getvalue()


class Check(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.directory.cleanup)
        self.tool = fake_tool(self.directory.name, raw())
        self.file = Path(self.directory.name) / "mm_as_tables.md"
        self.file.write_text(report.render(report.parse_raw(raw()), HEADER), encoding="utf-8")

    def test_a_file_the_rerun_renders_holds(self):
        code, output = check(self.file, self.tool)
        self.assertEqual(code, 0, output)

    def test_an_edited_figure_is_caught(self):
        text = self.file.read_text(encoding="utf-8")
        self.file.write_text(text.replace("| 0.1 | 1.49 | 1.4918 |", "| 0.1 | 1.49 | 1.4900 |"),
                             encoding="utf-8")
        code, output = check(self.file, self.tool)
        self.assertEqual(code, 1)
        self.assertIn("as_tables_report: FAIL differs-from-rerun", output)

    def test_a_flipped_verdict_is_caught(self):
        text = self.file.read_text(encoding="utf-8")
        self.file.write_text(text.replace("**Verdict: PASS**", "**Verdict: FAIL**"),
                             encoding="utf-8")
        code, output = check(self.file, self.tool)
        self.assertEqual(code, 1)
        self.assertIn("differs-from-rerun", output)

    def test_a_model_that_changed_is_caught(self):
        other = fake_tool(self.directory.name,
                          raw(spreads=(1.4917704228, 1.3490085437, 3.0316512475)))
        code, output = check(self.file, other)
        self.assertEqual(code, 1)
        self.assertIn("differs-from-rerun", output)

    def test_a_header_with_no_prediction_is_caught(self):
        text = self.file.read_text(encoding="utf-8")
        self.file.write_text(text.replace("- prediction: P-0201\n", ""), encoding="utf-8")
        code, output = check(self.file, self.tool)
        self.assertEqual(code, 1)
        self.assertIn("as_tables_report: FAIL header-field-missing", output)

    def test_output_with_a_table_missing_is_unusable(self):
        cut = raw().split("table gamma 1.0")[0]
        code, output = check(self.file, fake_tool(self.directory.name, cut))
        self.assertEqual(code, 2)
        self.assertIn("no complete table for gamma 1", output)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
