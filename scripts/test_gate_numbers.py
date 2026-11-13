#!/usr/bin/env python3
"""gate_numbers.py, breaking every figure of a fixture in turn (criterion 30)."""

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

import gate_numbers  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "gate_numbers"
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)


def run(root, *extra):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = gate_numbers.main(["--root", str(root), *extra])
    return code, output.getvalue()


def changed(figure):
    """The same figure with its last digit moved."""
    digits = re.search(r"(\d)(?=\D*$)", figure)
    moved = str((int(digits.group(1)) + 1) % 10)
    return figure[:digits.start()] + moved + figure[digits.end():]


class Gate(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "tree"
        shutil.copytree(FIXTURE, self.root)

    def test_the_fixture_passes(self):
        code, output = run(self.root)
        self.assertEqual(code, 0, output)
        self.assertIn("figures across 3 pages", output)

    def test_each_figure_changed_in_turn_fails_alone(self):
        for page in ("README.md", "RESULTS.md", "docs/index.html"):
            original = (self.root / page).read_text(encoding="utf-8")
            figures = list(gate_numbers.figures_in(original))
            self.assertTrue(figures)
            lines = original.splitlines()
            for number, figure, _ in figures:
                line = lines[number - 1]
                broken = list(lines)
                broken[number - 1] = line.replace(figure, changed(figure), 1)
                (self.root / page).write_text("\n".join(broken) + "\n", encoding="utf-8")
                code, output = run(self.root)
                self.assertEqual(code, 1, f"{figure} changed and the gate passed")
                self.assertEqual(len(re.findall(r"FAIL", output)), 1, output)
                self.assertIn(f"{page}:{number}:", output)
            (self.root / page).write_text(original, encoding="utf-8")

    def test_the_figures_found_are_the_right_ones(self):
        text = (self.root / "README.md").read_text(encoding="utf-8")
        figures = [figure for _, figure, _ in gate_numbers.figures_in(text)]
        self.assertEqual(figures, ["20,677,835", "291.7", "12.5", "3.06", "268,744,780"])

    def test_a_cited_file_is_the_only_source(self):
        (self.root / "results" / "bench_two.md").write_text("Rate 3.06.\n", encoding="utf-8")
        code, output = run(self.root)
        self.assertEqual(code, 1)
        self.assertIn("12.5 is not in results/bench_two.md", output)

    def test_a_cited_file_that_does_not_exist(self):
        (self.root / "results" / "bench_two.md").unlink()
        code, output = run(self.root)
        self.assertEqual(code, 1)
        self.assertIn("which does not exist", output)

    def test_a_figure_only_in_the_predictions_fails(self):
        with open(self.root / "README.md", "a", encoding="utf-8") as readme:
            readme.write("It reached 99,999.5 messages.\n")
        code, output = run(self.root)
        self.assertEqual(code, 1)
        self.assertIn("99,999.5 is in no results file", output)

    def write_fault_matrix(self):
        """A matrix file whose one row quotes the made-up figure its fault was caught on."""
        (self.root / "results" / "fault_matrix.md").write_text(
            "# Fault matrix\n\n- Controls: 196 commands, 0 failed\n"
            "- Result: 1061 of 1061 detected\n\n"
            "| Fault | Result | Signature seen |\n|---|---|---|\n"
            "| `page-figure` | detected | `gate_numbers: FAIL RESULTS.md:11: 88,888.5 is in no "
            "results file` |\n", encoding="utf-8")

    def test_a_figure_only_in_a_fault_matrix_row_fails(self):
        self.write_fault_matrix()
        with open(self.root / "README.md", "a", encoding="utf-8") as readme:
            readme.write("It reached 88,888.5 messages.\n")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("88,888.5 is in no results file", output)

    def test_a_fault_matrix_row_backs_no_figure_that_cites_the_file(self):
        self.write_fault_matrix()
        with open(self.root / "RESULTS.md", "a", encoding="utf-8") as page:
            page.write("| Fault matrix | 88,888.5 | [results/fault_matrix.md](x) |\n")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("88,888.5 is not in results/fault_matrix.md", output)

    def test_the_fault_matrix_header_is_a_source(self):
        self.write_fault_matrix()
        with open(self.root / "RESULTS.md", "a", encoding="utf-8") as page:
            page.write("| Fault matrix | 1061 of 1061 detected | controls: 196 commands "
                       "| [results/fault_matrix.md](x) |\n")
        code, output = run(self.root)
        self.assertEqual(code, 0, output)

    def test_a_figure_inside_a_longer_number_is_not_found(self):
        with open(self.root / "README.md", "a", encoding="utf-8") as readme:
            readme.write("It reached 77,835 messages.\n")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)

    def test_the_results_page_is_gated_without_being_named(self):
        page = self.root / "RESULTS.md"
        page.write_text(page.read_text(encoding="utf-8").replace("20,677,835", "20,677,836"),
                        encoding="utf-8")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("RESULTS.md:5: 20,677,836 is not in results/bench_one.md", output)

    def test_the_writeup_is_gated_without_being_named(self):
        page = self.root / "WRITEUP.md"
        page.write_text("It reached 20,677,835 messages/s ([results/bench_one.md](results/bench_one.md)).\n",
                        encoding="utf-8")
        code, output = run(self.root)
        self.assertEqual(code, 0, output)
        self.assertIn("figures across 4 pages", output)
        page.write_text("It reached 20,677,836 messages/s ([results/bench_one.md](results/bench_one.md)).\n",
                        encoding="utf-8")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("WRITEUP.md:1: 20,677,836 is not in results/bench_one.md", output)

    def test_a_wrong_figure_in_a_copy_of_the_writeup_is_caught(self):
        tree = Path(self.directory.name) / "repo"
        shutil.copytree(REPO_ROOT / "results", tree / "results")
        text = (REPO_ROOT / "WRITEUP.md").read_text(encoding="utf-8")
        (tree / "WRITEUP.md").write_text(text, encoding="utf-8")
        code, output = run(tree, "--page", "WRITEUP.md")
        self.assertEqual(code, 0, output)
        line = next(number for number, line in enumerate(text.splitlines(), 1)
                    if "4,570,542" in line)
        (tree / "WRITEUP.md").write_text(text.replace("4,570,542", "4,570,543"), encoding="utf-8")
        code, output = run(tree, "--page", "WRITEUP.md")
        self.assertEqual(code, 1, output)
        self.assertIn("4,570,543 is not in results/backtest_quoting_debian_12302019.md", output)
        self.assertIn(f"WRITEUP.md:{line}:", output)

    def test_no_pages_at_all_passes_and_says_so(self):
        (self.root / "README.md").unlink()
        (self.root / "RESULTS.md").unlink()
        shutil.rmtree(self.root / "docs")
        code, output = run(self.root)
        self.assertEqual(code, 0)
        self.assertIn("nothing to gate", output)

    def test_a_named_page_must_exist(self):
        code, _ = run(self.root, "--page", "missing.md")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
