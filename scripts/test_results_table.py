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
BOOK_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "bench_results"
BOOK_FIXTURE = "good_book_mac.md"
BOOK = "bench_book_hash-linked_ram_fixture_12302019.md"  # Beside the real files, not over one.


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
        for day in ("10302019", "12302019"):
            self.assertIn("| **FAIL** |", self.row("T-INV-2", day))
            self.assertIn("strict:", self.row("T-INV-2", day))
        self.assertIn("| **NOT AVAILABLE** |", self.row("T-TRADE-2", "10302019"))
        self.assertIn("| PASS |", self.row("T-TRADE-2", "12302019"))

    def test_t_inv_3_stands_beside_t_inv_2_on_both_days(self):
        row = self.row("T-INV-3", "12302019")
        self.assertIn("| PASS | 0 violations: 0 crossed and 0 locked with no leftover", row)
        self.assertIn("22 of 33 resumptions", row)
        self.assertIn("2 of 8 resumptions", self.row("T-INV-3", "10302019"))

    def test_a_failed_t_inv_3_is_shown_failing(self):
        self.edit(ORACLE, "Verdict: **PASS**, 0 violations. This check",
                  "Verdict: **FAIL**, 3 violations. This check")
        row = self.row("T-INV-3", "12302019")
        self.assertIn("| **FAIL** | 3 violations:", row)

    def test_a_failed_t_trade_3_names_its_symbols(self):
        self.edit(TRADES, "| T-TRADE-3 | **PASS** | 0 |", "| T-TRADE-3 | **FAIL** | 2 |")
        self.edit(TRADES, "## Per message type\n",
                  "- equal_odd_lots 5766 of 5767 symbols\n"
                  "- `t3_odd_lots message=1 locate=1 symbol=BRK.A feed 1, MIDAS 2`\n\n"
                  "## Per message type\n")
        row = self.row("T-TRADE-3", "10302019")
        self.assertIn("| **FAIL** | odd-lot columns wrong on BRK.A, equal on 5766 of 5767", row)
        self.assertRegex(row, r"shares equal on ([\d,]+) of \1\b")

    def test_a_late_day_says_its_odd_lots_were_not_compared(self):
        self.edit(TRADES, "## Results\n", "## Results\n\nThe odd-lot columns were not compared.\n")
        self.assertIn("odd-lot columns not compared", self.row("T-TRADE-3", "10302019"))

    def test_every_row_says_whether_every_arm_agreed(self):
        for check in ("T-ORACLE-1", "T-INV-2", "T-ALLOC-1"):
            self.assertIn("; the same on 4 arms |", self.row(check, "12302019"))
        self.assertIn("; the same on 4 arms |", self.row("T-TRADE-5", "10302019"))

    def test_an_arm_with_another_verdict_fails_its_row(self):
        self.edit(ORACLE, "| `paged_flat` | PASS | PASS |", "| `paged_flat` | FAIL | PASS |")
        row = self.row("T-ORACLE-1", "12302019")
        self.assertIn("| **FAIL** |", row)
        self.assertIn("the arms disagree: paged_flat FAIL", row)
        self.assertIn("| PASS |", self.row("T-INV-1", "12302019"))

    def test_an_arm_with_another_trade_verdict_fails_its_row(self):
        self.edit(TRADES, "| `hash_flat` | PASS | NOT_AVAILABLE | PASS | PASS |",
                  "| `hash_flat` | PASS | NOT_AVAILABLE | PASS | FAIL |")
        row = self.row("T-TRADE-5", "10302019")
        self.assertIn("| **FAIL** |", row)
        self.assertIn("the arms disagree: hash_flat FAIL", row)

    def test_a_file_with_no_table_of_arms_is_refused(self):
        self.edit(ORACLE, "## Every arm of the fast book\n", "## Arms\n")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn("Every arm of the fast book", output)

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
        self.edit("README.md", "| PASS | 0 divergences in 268", "| PASS | no divergences at all in 268")
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)

    def test_a_line_typed_in_fails(self):
        self.edit("README.md", results_table.END, "| one more row |\n" + results_table.END)
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)

    def test_write_mends_what_check_refused(self):
        self.edit("README.md", "| PASS | 0 divergences in 268", "| PASS | no divergences at all in 268")
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

    def test_a_profile_gets_a_summary_row_and_one_without_a_summary_is_refused(self):
        text = ("# Flamegraph: replay on debian\n\n## Header\n\n- host: debian\n"
                "- commit: 0123456789ab (clean)\n- summary: 10 samples, cycles:u\n")
        path = self.root / "results" / "flamegraph_fixture_debian_12302019.md"
        path.write_text(text, encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 0, output)
        self.assertIn("| Flamegraph: replay on debian | debian | 10 samples, cycles:u | "
                      "`0123456789ab` |", output)
        path.write_text(text.replace("- summary:", "- note:"), encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)

    def test_a_verdict_no_row_took_up_is_refused(self):
        self.edit(ORACLE, "## T-ALLOC-1:", "## T-NEW-1: new\n\nVerdict: **FAIL**\n\n## T-ALLOC-1:")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn("6 verdicts in the file, 5 rows", output)

    def test_a_trade_verdict_dropped_from_its_table_is_refused(self):
        self.edit(TRADES, "| T-TRADE-3 | **PASS** | 0 |\n", "")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)

    def test_a_benchmark_with_no_harness_label_is_held_to_the_book_rules(self):
        name = "results/bench_replay_stub_mac_12302019.md"
        text = (self.root / name).read_text(encoding="utf-8")
        (self.root / name).write_text(text.replace(results_table.HARNESS_LABEL, "A result."),
                                      encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn("- design:", output)

    def add_book(self, fixture=BOOK_FIXTURE):
        name = f"results/{BOOK}"
        shutil.copy(BOOK_FIXTURES / fixture, self.root / name)
        return name

    def test_a_book_file_gets_a_row_of_its_own(self):
        self.add_book()
        code, output = self.run_table()
        self.assertEqual(code, 0, output)
        rows = [line for line in output.splitlines()
                if line.startswith("| `hash-linked` |") and f"(results/{BOOK})" in line]
        self.assertEqual(len(rows), 1, output)
        self.assertIn("| ram | not tracked | 12302019 | macOS (M4 Max) | not pinned |", rows[0])
        self.assertIn(f"[results/{BOOK}](results/{BOOK})", rows[0])
        self.assertIn("macOS cannot pin a thread", output)

    def test_a_book_file_missing_a_field_is_refused(self):
        name = self.add_book()
        for line in ("- design: ", "- input: ", "- top_of_book: ",
                     "| Batch mean per book message, ns |", "| Book memory, MiB |",
                     "| Throughput, messages/s |"):
            with self.subTest(line=line):
                self.add_book()
                path = self.root / name
                text = path.read_text(encoding="utf-8")
                kept = [row for row in text.splitlines(keepends=True) if not row.startswith(line)]
                self.assertNotEqual(len(kept), len(text.splitlines()), line)
                path.write_text("".join(kept), encoding="utf-8")
                code, output = self.run_table()
                self.assertEqual(code, 1, output)
                self.assertIn(f"results_table: FAIL {name}", output)

    def test_a_book_figure_that_is_not_a_number_is_refused(self):
        name = self.add_book()
        path = self.root / name
        text = path.read_text(encoding="utf-8")
        path.write_text(re.sub(r"^\| Book memory, MiB \| [^|]+\|", "| Book memory, MiB | n/a |",
                               text, count=1, flags=re.MULTILINE), encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn("Book memory, MiB is 'n/a', not a figure", output)

    def test_t_oracle_2_gets_one_row_from_the_sum_of_the_sessions(self):
        import oracle_sessions  # noqa: PLC0415

        path = self.root / "results" / oracle_sessions.OUT
        path.write_text(oracle_sessions.generate(self.root), encoding="utf-8")
        row = self.row("T-ORACLE-2", "every session")
        self.assertIn("| **FAIL** | 0 divergences in ", row)
        self.assertIn("; the gap: ", row)
        self.edit(f"results/{oracle_sessions.OUT}", "Verdict: **FAIL**", "Verdict: **PASS**")
        self.assertIn("| PASS | 0 divergences in ", self.row("T-ORACLE-2", "every session"))

    def test_a_backtest_file_gets_a_row_of_its_own(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import test_backtest_results  # noqa: E402, PLC0415

        name = "backtest_shadow_fixture_12302019.md"
        (self.root / "results" / name).write_text(test_backtest_results.SHADOW, encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 0, output)
        rows = [line for line in output.splitlines() if f"(results/{name})" in line]
        self.assertEqual(len(rows), 1, output)
        self.assertIn("| shadow | T-BT-3 | 12302019 | macOS (M4 Max) | not pinned | 5,425,845 |",
                      rows[0])
        self.assertIn("| met | `abc` |", rows[0])
        path = self.root / "results" / name
        path.write_text(test_backtest_results.SHADOW.replace("| Engine events/s |", "| x |"),
                        encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn(f"results_table: FAIL results/{name}", output)

    def test_t_mm_2_shows_each_spread_and_its_verdict(self):
        row = self.row("T-MM-2", "none")
        self.assertIn("| **FAIL** | average spread 1.4918, 1.3490, 3.0317 against the paper's "
                      "1.49, 1.35, 3.02: 2 of 3 match at two decimals;", row)
        self.edit("results/mm_as_tables.md", "**Verdict: FAIL**", "**Verdict: PASS**")
        self.assertIn("| PASS |", self.row("T-MM-2", "none"))
        self.edit("results/mm_as_tables.md", "| 0.01 | 1.35 | 1.3490 |", "| 0.01 | 1.35 | x |")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn("results_table: FAIL results/mm_as_tables.md: 2 average spreads, not 3",
                      output)

    def test_the_market_maker_shows_every_run_of_its_report_day(self):
        code, output = self.run_table()
        self.assertEqual(code, 0, output)
        rows = [line for line in output.splitlines() if line.startswith(("| AMD | ", "| BYND | "))]
        self.assertEqual(len(rows), 14, output)
        self.assertIn("One symbol-day. No edge is claimed. 2 symbols on one day", output)

    def test_a_market_maker_run_dropped_from_a_table_is_refused(self):
        name = "results/mm_pnl_12302019.md"
        text = (self.root / name).read_text(encoding="utf-8")
        row = next(line for line in text.splitlines() if line.startswith("| GLFT Base | +"))
        self.edit(name, row + "\n", "")
        code, output = self.run_table()
        self.assertEqual(code, 1)
        self.assertIn("results_table: FAIL results/mm_pnl_12302019.md: AMD's tables do not have "
                      "one row a run", output)

    def test_no_stray_markup_in_generated_cells(self):
        _, output = self.run_table()
        for line in output.splitlines():
            if line.startswith("| T-"):
                self.assertEqual(len(re.findall(r" \| ", line)), 6, line)


if __name__ == "__main__":
    unittest.main()
