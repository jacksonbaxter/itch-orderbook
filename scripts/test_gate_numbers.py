#!/usr/bin/env python3
"""gate_numbers.py, breaking every figure of a fixture in turn, and each rule that holds a figure
to its place (criterion 30)."""

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
            for found in figures:
                number, figure, _ = found
                line = lines[number - 1]
                broken = list(lines)
                digits = figure.lstrip("-−+")
                broken[number - 1] = (line[:found.position]
                                      + line[found.position:].replace(digits, changed(digits), 1))
                (self.root / page).write_text("\n".join(broken) + "\n", encoding="utf-8")
                code, output = run(self.root)
                self.assertEqual(code, 1, f"{figure} changed and the gate passed")
                self.assertEqual(len(re.findall(r"FAIL", output)), 1, output)
                self.assertIn(f"{page}:{number}:", output)
            (self.root / page).write_text(original, encoding="utf-8")

    def test_the_figures_found_are_the_right_ones(self):
        text = (self.root / "README.md").read_text(encoding="utf-8")
        figures = [figure for _, figure, _ in gate_numbers.figures_in(text)]
        self.assertEqual(figures, ["20,677,835", "291.7", "12.5", "3.06", "268,744,780", "10",
                                   "2,830,848", "2,735,896", "9", "136", "200", "146", "-27.50"])

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
            readme.write("\n| Reached | 99,999.5 messages |\n")
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
        # The row shares the matrix row's words, so only the file's kind keeps it out.
        with open(self.root / "README.md", "a", encoding="utf-8") as readme:
            readme.write("\n| Results file | 88,888.5 messages |\n")
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
            readme.write("It reached 77,835 messages ([results/bench_one.md](x)).\n")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("77,835 is not in results/bench_one.md", output)

    def edit(self, page, old, new):
        path = self.root / page
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, old)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def test_a_figure_moved_to_another_row_fails(self):
        self.edit("README.md", "S4 carries 2,830,848 messages/s; S1 and S3 tie at 2,735,896",
                  "S4 carries 2,735,896 messages/s; S1 and S3 tie at 2,830,848")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("README.md:12: 2,735,896 is not in results/modes.md", output)
        self.assertIn("README.md:12: 2,830,848 is not in results/modes.md", output)

    def test_a_figure_from_another_column_of_its_row_fails(self):
        self.edit("README.md", "in 9 trials", "in 5 trials")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("README.md:12: 5 is not in results/modes.md", output)

    def test_a_small_count_on_a_cited_line_is_read(self):
        self.edit("README.md", "Over 10 sessions", "Over 12 sessions")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("README.md:12: 12 is not in results/modes.md", output)

    def test_names_days_and_titles_are_not_figures(self):
        text = (self.root / "README.md").read_text(encoding="utf-8")
        lines = {number: line for number, line in enumerate(text.splitlines(), start=1)}
        for found in gate_numbers.figures_in(text):
            line = lines[found.line]
            for name in ("Zen 1", "4 cores", "12302019", "4 GB", "3 hosts"):
                start = line.find(name)
                if start >= 0:
                    self.assertFalse(start <= found.position < start + len(name),
                                     f"{name} read as the figure {found.figure}")

    def test_listed_figures_out_of_order_fail(self):
        self.edit("README.md", "take 136, 200 and 146", "take 146, 200 and 136")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("146 is not in results/modes.md as pool-index's", output)
        self.assertIn("136 is not in results/modes.md as pool-fifo's", output)

    def test_a_listed_figure_its_key_line_gives_otherwise_fails(self):
        # pool-index's row holds a 0 under Page faults; the fault_check line gives it 136.
        self.edit("README.md", "take 136, 200 and 146", "take 0, 200 and 146")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("README.md:14: 0 is not in results/modes.md as pool-index's", output)

    def test_a_dropped_minus_sign_fails(self):
        self.edit("README.md", "-$27.50", "$27.50")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("README.md:14: 27.50 is not in results/modes.md", output)

    def test_a_minus_sign_is_read_however_it_is_written(self):
        self.edit("README.md", "-$27.50", "−27.50")
        code, output = run(self.root)
        self.assertEqual(code, 0, output)
        self.edit("results/modes.md", "-$27.50", "-27.50")
        code, output = run(self.root)
        self.assertEqual(code, 0, output)
        self.edit("results/modes.md", "-27.50", "27.50")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("−27.50 is not in results/modes.md", output)

    def test_a_line_of_prose_with_a_figure_must_cite(self):
        self.edit("README.md", " ([results/bench_one.md](results/bench_one.md))", "")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("README.md:10: 268,744,780 is in no results file: its line of prose cites "
                      "none", output)

    def test_a_figure_is_held_to_the_file_its_clause_cites(self):
        self.edit("README.md", "dated 2019-12-30 ([results/bench_one.md](results/bench_one.md))",
                  "dated 2019-12-30 ([results/bench_one.md](x)); the rate is 12.5 "
                  "([results/bench_two.md](x))")
        code, output = run(self.root)
        self.assertEqual(code, 0, output)
        self.edit("README.md", "the rate is 12.5 ([results/bench_two.md](x))",
                  "the rate is 12.5 ([results/modes.md](x))")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("README.md:10: 12.5 is not in results/modes.md", output)

    def test_a_label_in_brackets_after_a_figure_is_its_label(self):
        self.edit("README.md", "S1 and S3 tie at 2,735,896 messages/s in 9 trials",
                  "the modes run at 2,735,896 (S3) to 2,830,848 (S4) messages/s")
        code, output = run(self.root)
        self.assertEqual(code, 0, output)
        self.edit("README.md", "2,735,896 (S3) to 2,830,848 (S4)",
                  "2,735,896 (S4) to 2,830,848 (S3)")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("2,735,896 is not in results/modes.md", output)

    def test_the_results_page_is_gated_without_being_named(self):
        page = self.root / "RESULTS.md"
        page.write_text(page.read_text(encoding="utf-8").replace("20,677,835", "20,677,836"),
                        encoding="utf-8")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("RESULTS.md:5: 20,677,836 is not in results/bench_one.md", output)

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
