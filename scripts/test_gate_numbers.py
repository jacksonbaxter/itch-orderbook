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

    def test_a_day_written_out_is_not_a_figure(self):
        figures = [found.figure for found in gate_numbers.figures_in(
            "On 30 May 2019 and 1 October 2019, 38 violations "
            "([results/trades.md](results/trades.md)); 30 May runs and 2019 May.\n")]
        self.assertEqual(figures, ["38", "30", "2019"])

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
        shutil.copy(REPO_ROOT / "BENCHMARKS.md", tree / "BENCHMARKS.md")
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

    def write(self, name, text):
        (self.root / name).write_text(text, encoding="utf-8")

    def test_a_figure_beside_a_one_word_row_label_is_held_to_its_row(self):
        self.write("results/queues.md", "# Queues\n\n| Queue | Median round trip, ns |\n"
                   "|---|---|\n| rigtorp, rtt | 198.5 |\n| moodycamel, rtt | 169.3 |\n\n"
                   "| Form | Branch misses a message |\n|---|---|\n| `switch` | 2.0303 |\n"
                   "| `table` | 2.1005 |\n")
        self.write("WRITEUP.md", "| Queue | Median round trip, ns | File |\n|---|---|---|\n"
                   "| rigtorp | 198.5 | results/queues.md |\n"
                   "| moodycamel | 169.3 | results/queues.md |\n\n"
                   "The forms ran `switch` 2.0303 a message and `table` 2.1005 "
                   "([results/queues.md](results/queues.md)).\n")
        code, output = run(self.root)
        self.assertEqual(code, 0, output)
        self.edit("WRITEUP.md", "| rigtorp | 198.5 |", "| rigtorp | 169.3 |")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("WRITEUP.md:3: 169.3 is not in results/queues.md", output)
        self.edit("WRITEUP.md", "| rigtorp | 169.3 |", "| rigtorp | 198.5 |")
        self.edit("WRITEUP.md", "`switch` 2.0303 a message and `table` 2.1005",
                  "`switch` 2.1005 a message and `table` 2.0303")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("WRITEUP.md:6: 2.1005 is not in results/queues.md", output)
        self.assertIn("WRITEUP.md:6: 2.0303 is not in results/queues.md", output)

    def test_the_two_integers_of_n_of_m_stand_together(self):
        self.write("results/resumptions.md", "# Resumptions\n\n- resumptions late: 23\n\n"
                   "| Resumptions and leftovers | Count |\n|---|---|\n"
                   "| Resumptions into trading | 33 |\n"
                   "| Of which found the book crossed | 22 |\n")
        self.write("WRITEUP.md", "On the day 22 of 33 resumptions found the book crossed "
                   "([results/resumptions.md](results/resumptions.md)).\n")
        code, output = run(self.root)
        self.assertEqual(code, 0, output)
        for wrong in ("23 of 33", "33 of 33"):
            self.write("WRITEUP.md", f"On the day {wrong} resumptions found the book crossed "
                       "([results/resumptions.md](results/resumptions.md)).\n")
            code, output = run(self.root)
            self.assertEqual(code, 1, output)
            self.assertIn(f"WRITEUP.md:1: {wrong} is not in results/resumptions.md together",
                          output)

    def test_a_figure_in_billions_or_millions_is_held_as_its_rounding(self):
        self.write("results/sessions.md", "# Sessions\n\n| Sessions | Messages | Rate |\n"
                   "|---|---|---|\n| all ten | 3,081,392,329 | 5,150,000 |\n")
        cite = " ([results/sessions.md](results/sessions.md)).\n"
        for held in ("3.08 billion messages", "3.1 billion messages", "3,081.4 million messages",
                     "5.15 million a second, the rate", "3,081,392,329 messages"):
            self.write("WRITEUP.md", f"The sessions ran {held}{cite}")
            code, output = run(self.root)
            self.assertEqual(code, 0, f"{held}: {output}")
        for wrong in ("The sessions ran 3.09 billion messages",
                      "The sessions ran 3.08 million messages",
                      "The sessions ran 3.082 billion messages",
                      "The sessions ran 5.15 billion a second, the rate",
                      "The day took 3.08 billion faults"):
            self.write("WRITEUP.md", f"{wrong}{cite}")
            code, output = run(self.root)
            self.assertEqual(code, 1, f"{wrong}: {output}")
            figure = " ".join(wrong.split()[3:5])
            self.assertIn(f"WRITEUP.md:1: {figure} is not in results/sessions.md, nor a figure "
                          "that rounds to it,", output)

    def real_tree(self, page):
        """A tree of the repository's own results and BENCHMARKS.md, with one page."""
        tree = Path(self.directory.name) / "real"
        if not tree.exists():
            shutil.copytree(REPO_ROOT / "results", tree / "results")
            shutil.copy(REPO_ROOT / "BENCHMARKS.md", tree / "BENCHMARKS.md")
        (tree / "README.md").write_text(page, encoding="utf-8")
        return run(tree, "--page", "README.md")

    def test_a_verdict_word_is_held_to_its_targets_result(self):
        code, output = self.real_tree("On the Zen 1 host T-BT-1 is met and T-BT-3 is missed.\n")
        self.assertEqual(code, 0, output)
        code, output = self.real_tree("On the Zen 1 host T-BT-1 is met and T-BT-3 is met.\n")
        self.assertEqual(code, 1, output)
        self.assertIn("README.md:1: 'met' for T-BT-3: its result is missed", output)
        self.assertEqual(len(re.findall(r"FAIL", output)), 1, output)
        code, output = self.real_tree("T-BT-3 is met.\n")
        self.assertEqual(code, 1, output)
        # Its bar was set on a Mac, and there it is met: a sentence that names the host is held
        # to that host's rows.
        code, output = self.real_tree("T-BT-3 is met on the Mac.\n")
        self.assertEqual(code, 0, output)
        code, output = self.real_tree("T-SPSC-1 and T-SPSC-2 are missed and T-SPSC-3 is met.\n")
        self.assertEqual(code, 0, output)
        code, output = self.real_tree("T-SPSC-1 and T-SPSC-2 are met and T-SPSC-3 is met.\n")
        self.assertEqual(code, 1, output)
        self.assertEqual(len(re.findall(r"FAIL", output)), 2, output)
        code, output = self.real_tree("T-INV-2, the strict check, fails and stays failing.\n")
        self.assertEqual(code, 0, output)
        code, output = self.real_tree("T-INV-2, the strict check, passes.\n")
        self.assertEqual(code, 1, output)
        code, output = self.real_tree("T-NONE-1 is met.\n")
        self.assertEqual(code, 1, output)
        self.assertIn("no target of BENCHMARKS.md's", output)

    def test_no_verdict_is_held_too(self):
        code, output = self.real_tree("On the Mac T-BOOK-1 has no verdict.\n")
        self.assertEqual(code, 0, output)
        code, output = self.real_tree("T-BOOK-1 has no verdict.\n")
        self.assertEqual(code, 1, output)
        self.assertIn("its result is missed", output)

    def test_a_count_after_a_verdict_is_held_to_the_rows(self):
        code, output = self.real_tree("T-TRADE-3 fails on four of the ten sessions, and "
                                      "T-TRADE-5 fails on two.\n")
        self.assertEqual(code, 0, output)
        for wrong in ("three of the ten sessions", "4 of 9 sessions", "four of the eleven days"):
            code, output = self.real_tree(f"T-TRADE-3 fails on {wrong}.\n")
            self.assertEqual(code, 1, wrong)
            self.assertIn("for T-TRADE-3: its rows give 4 of 10", output)
        code, output = self.real_tree("T-TRADE-5 fails on three.\n")
        self.assertEqual(code, 1, output)
        self.assertIn("its rows give 2", output)
        # After "in" or a comma too (r18-19).
        for said in ("fails in three of the ten sessions", "fails, on three of the ten sessions",
                     "fails in eight of the ten sessions"):
            code, output = self.real_tree(f"T-TRADE-3 {said}.\n")
            self.assertEqual(code, 1, said)
            self.assertIn("for T-TRADE-3: its rows give 4 of 10", output)
        code, output = self.real_tree("T-TRADE-5 fails, on three.\n")
        self.assertEqual(code, 1, output)
        self.assertIn("its rows give 2", output)
        code, output = self.real_tree("T-TRADE-3 fails in four of the ten sessions, and "
                                      "T-TRADE-5 fails, on two.\n")
        self.assertEqual(code, 0, output)

    def test_a_verdict_word_that_names_no_target_is_refused_unless_marked(self):
        code, output = self.real_tree("The check failed.\n")
        self.assertEqual(code, 1, output)
        self.assertIn("'failed' names no target", output)
        code, output = self.real_tree(f"The check failed. {gate_numbers.VERDICT_MARK}\n")
        self.assertEqual(code, 0, output)
        code, output = self.real_tree("## Every target missed\n\nEach missed target is below; "
                                      "the met targets are in RESULTS.md.\n")
        self.assertEqual(code, 0, output)
        # Only a word straight before "target" is excused (r18-14): after it, the word is a
        # verdict on targets the sentence does not name.
        for said in ("Targets met are in RESULTS.md.", "All targets met.", "No target failed."):
            code, output = self.real_tree(said + "\n")
            self.assertEqual(code, 1, said)

    def test_a_verdict_turned_round_by_a_negation_is_refused(self):
        for said in ("T-SPSC-3 is not met.", "T-BT-3 is not missed.", "T-INV-3 never passes.",
                     "On the Mac, where its bar was set, T-BT-3 is not met.",
                     "No target failed.", "Targets missed: none."):
            with self.subTest(said=said):
                code, output = self.real_tree(said + "\n")
                self.assertEqual(code, 1, output)
                self.assertIn("turned round by a negation", output)
        for said in ("T-SPSC-3 is met.", "With no sampler beside it, T-SPSC-3 is met.",
                     "T-TRADE-5 fails on two, none allowed.", "T-BOOK-4 is not measured."):
            with self.subTest(said=said):
                code, output = self.real_tree(said + "\n")
                self.assertEqual(code, 0, output)

    def test_a_verdict_word_split_by_markup_is_read_whole(self):
        for said in ("T-SPSC-1 is **m**et.", "T-SPSC-1 is m<b></b>et.", "T-SPSC-1 is _met_.",
                     "[T-SPSC-1](RESULTS.md) is met."):
            with self.subTest(said=said):
                code, output = self.real_tree(said + "\n")
                self.assertEqual(code, 1, output)
                self.assertIn("'met' for T-SPSC-1: its result is missed", output)
        # A count a verdict holds is found where it stands on the line, markup and all, so it is
        # not read again as a figure of the file the line cites.
        code, output = self.real_tree("**T-TRADE-3** `fails` on four of the ten sessions "
                                      "([results/oracle_sessions.md](results/oracle_sessions.md))"
                                      ".\n")
        self.assertEqual(code, 0, output)

    def test_a_noun_form_is_read_only_straight_after_a_target(self):
        code, output = self.real_tree("T-PERF-2 counts branch misses over timed passes.\n")
        self.assertEqual(code, 0, output)
        code, output = self.real_tree("T-PERF-2 passes.\n")
        self.assertEqual(code, 1, output)
        code, output = self.real_tree("T-PERF-2, the branch-miss rate, misses.\n")
        self.assertEqual(code, 0, output)

    def test_a_generated_block_is_left_to_results_table(self):
        begin = "<!-- headline: begin, generated by scripts/results_table.py, do not edit -->"
        code, output = self.real_tree(f"{begin}\n| T-BT-3 | met |\n<!-- headline: end -->\n")
        self.assertEqual(code, 0, output)

    def test_the_figures_marker_is_refused_on_a_line_that_cites_a_file(self):
        mark = gate_numbers.SKIP_MARK
        cited = "([results/oracle_sessions.md](results/oracle_sessions.md))"
        for said in (f"Every symbol's book at 6.15 M msg/s {cited}. {mark}",
                     f"It ran 9 runs of 10 {cited}. {mark}",
                     f"Clean {cited}. {mark} {gate_numbers.VERDICT_MARK}"):
            with self.subTest(said=said):
                code, output = self.real_tree(said + "\n")
                self.assertEqual(code, 1, output)
                self.assertIn("on a line that cites results/oracle_sessions.md", output)
        begin = "<!-- missed targets: begin, generated by scripts/results_table.py, do not edit -->"
        for said in (f"About 698 cycles a message, an estimate. {mark}",
                     f"{begin}\n- 14.4 M msg/s [file](results/oracle_sessions.md) {mark}\n"
                     "<!-- missed targets: end -->",
                     f"```\n6.15 {cited} {mark}\n```"):
            with self.subTest(said=said):
                code, output = self.real_tree(said + "\n")
                self.assertEqual(code, 0, output)

    def test_a_count_in_words_is_a_figure_where_its_line_cites_a_file(self):
        self.write("results/runs.md", "# Runs\n\n- repetitions: 3\n")
        self.write("WRITEUP.md", "It ran three repetitions ([results/runs.md](results/runs.md)).\n"
                   "It ran four repetitions in all.\n")
        code, output = run(self.root)
        self.assertEqual(code, 0, output)
        self.write("WRITEUP.md", "It ran four repetitions ([results/runs.md](results/runs.md)).\n")
        code, output = run(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("WRITEUP.md:1: four is not in results/runs.md", output)
        # Four cores name hardware, and `one` is read only in "one of N".
        self.write("WRITEUP.md", "On four cores one run of three repetitions "
                   "([results/runs.md](results/runs.md)).\n")
        code, output = run(self.root)
        self.assertEqual(code, 0, output)
        figures = [figure for _, figure, _ in gate_numbers.figures_in(
            "One of three ran ([results/runs.md](results/runs.md)).\n")]
        self.assertEqual(figures, ["One", "three"])

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
