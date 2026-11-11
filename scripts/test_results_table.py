#!/usr/bin/env python3
"""results_table.py on a copy of the real results: what RESULTS.md and the README's headline
must show, and what each must refuse."""

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
HEADLINE_BOOK = "results/bench_book_paged-flat_mapped_top_counters_debian_12302019.md"
HEADLINE_BACKTEST = "results/backtest_quoting_debian_12302019.md"


class ResultsTable(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        shutil.copytree(REPO_ROOT / "results", self.root / "results")
        shutil.copy(REPO_ROOT / "README.md", self.root / "README.md")
        shutil.copy(REPO_ROOT / "RESULTS.md", self.root / "RESULTS.md")

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
        self.assertIn("| **NOT AVAILABLE** |", self.row("T-TRADE-2", "01302019_BX"))
        for day in ("10302019", "12302019"):
            self.assertIn("| PASS |", self.row("T-TRADE-2", day))

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

    def test_a_t_trade_3_failure_outside_the_odd_lots_names_its_column_and_symbol(self):
        self.edit(TRADES, "| T-TRADE-3 | **PASS** | 0 |", "| T-TRADE-3 | **FAIL** | 2 |")
        self.edit(TRADES, "## Per message type\n",
                  "- equal_shares 5766 of 5767 symbols\n"
                  "- equal_prints 5766 of 5767 symbols\n"
                  "- `t3_trade_shares message=1 locate=1 symbol=PLAB feed 200, MIDAS 100`\n"
                  "- `t3_trades message=1 locate=1 symbol=PLAB feed 2, MIDAS 1`\n\n"
                  "## Per message type\n")
        row = self.row("T-TRADE-3", "10302019")
        self.assertIn("| **FAIL** | trade_shares wrong on PLAB, trades wrong on PLAB; "
                      "equal_shares 5766 of 5767, equal_prints 5766 of 5767;", row)
        self.assertNotIn("odd-lot", row)

    def test_a_day_of_another_venue_keeps_its_suffix_and_says_why_t_trade_2_did_not_run(self):
        row = self.row("T-TRADE-2", "01302019_BX")
        self.assertIn("| **NOT AVAILABLE** | CrossStats is Nasdaq's file of its own crosses, and "
                      "this is a BX feed", row)
        self.assertIn("(results/trades_01302019_BX.md)", row)
        self.assertIn("| PASS |", self.row("T-TRADE-2", "01302019"))

    def test_a_nasdaq_day_with_no_cross_file_says_so(self):
        # Every Nasdaq day now has its file, so the BX day's text stands in for one without.
        text = (self.root / "results" / "trades_01302019_BX.md").read_text(encoding="utf-8")
        old = ("T-TRADE-2 is not available on 01302019_BX: CrossStats is Nasdaq's file of its own "
               "crosses, and this is a BX feed, so ")
        self.assertEqual(text.count(old), 1)
        text = text.replace(old, "T-TRADE-2 is not available on 10302019: no CrossStats file for "
                                 "that day is in hand, so ")
        (self.root / TRADES).write_text(text, encoding="utf-8")
        row = self.row("T-TRADE-2", "10302019")
        self.assertIn("| **NOT AVAILABLE** | no CrossStats file for that day is in hand", row)

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
        self.edit(TRADES, "| `hash_flat` | PASS | PASS | PASS | PASS |",
                  "| `hash_flat` | PASS | PASS | PASS | FAIL |")
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

    def test_every_run_row_reaches_the_table(self):
        _, output = self.run_table()
        log = (self.root / "results" / "run_log.md").read_text(encoding="utf-8")
        runs = [line for line in log.splitlines() if line.startswith("| P-")]
        shown = [line for line in output.splitlines() if line.startswith("| `P-")]
        self.assertEqual(len(shown), len(runs))
        self.assertIn("| `P-0601-20261106T031311Z_scalar-w2-1` |", output)
        self.assertIn("| `P-0024-window-12302019` |", output)

    def test_a_run_row_the_table_cannot_place_is_refused(self):
        path = self.root / "results" / "run_log.md"
        path.write_text(path.read_text(encoding="utf-8")
                        + "| P-0999 typed by hand | P-0999 | abc | Darwin | published |\n",
                        encoding="utf-8")
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)
        self.assertRegex(output, r"results_table: FAIL results/run_log\.md:\d+: a line of the "
                                 r"table that is not a run with five cells")

    def test_a_changed_verdict_changes_the_table_and_fails_the_check(self):
        self.edit(ORACLE, "Verdict, strict: **FAIL**", "Verdict, strict: **PASS**")
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)
        self.assertIn("results_table: FAIL RESULTS.md:", output)

    def test_a_row_typed_over_fails(self):
        self.edit("RESULTS.md", "| PASS | 0 divergences in 268", "| PASS | no divergences at all in 268")
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)

    def test_a_line_typed_in_fails(self):
        self.edit("RESULTS.md", results_table.END, "| one more row |\n" + results_table.END)
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)

    def test_write_mends_what_check_refused(self):
        self.edit("RESULTS.md", "| PASS | 0 divergences in 268", "| PASS | no divergences at all in 268")
        self.edit("README.md", "| 5.04 M msg/s |", "| 12.6 M msg/s |")
        self.assertEqual(self.run_table("--write")[0], 0)
        self.assertEqual(self.run_table("--check")[0], 0)

    def test_a_missing_marker_fails(self):
        self.edit("RESULTS.md", results_table.END + "\n", "")
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
        if "Verdict: **PASS**" in path.read_text(encoding="utf-8"):  # Shown failing, either way.
            self.edit(f"results/{oracle_sessions.OUT}", "Verdict: **PASS**", "Verdict: **FAIL**")
        self.assertIn("| **FAIL** | 0 divergences in ", self.row("T-ORACLE-2", "every session"))
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

    def test_an_alloc_file_gets_a_row_for_each_arm_and_its_fault_check(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import test_alloc_results  # noqa: E402, PLC0415

        name = "alloc_arms_fixture_12302019.md"
        path = self.root / "results" / name
        path.write_text(test_alloc_results.GOOD, encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 0, output)
        rows = [line for line in output.splitlines()
                if line.startswith("| ") and f"(results/{name})" in line]
        self.assertEqual([row.split(" | ")[0][2:] for row in rows],
                         list(test_alloc_results.ARMS), output)
        self.assertIn("| malloc-fresh | 12302019 | macOS (M4 Max) | not pinned |", rows[0])
        self.assertIn("| – |", rows[0])
        for row in rows:
            arm = row.split(" | ")[0][2:]
            judged = "zero" if arm in test_alloc_results.POOLS else "not judged: a heap arm"
            self.assertIn(f"| {judged} |", row)
        self.assertIn("it read: PASS: no page fault", output)
        self.assertIn("the pools' pages requested as `huge-if-offered`.", output)
        path.write_text(re.sub(r"(?m)^\| pool-fifo \| all \|.*\n", "", test_alloc_results.GOOD),
                        encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn(f"results_table: FAIL results/{name}: pool-fifo has no latency", output)

    def test_an_spsc_file_gets_a_row_for_each_comparison(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import test_spsc_results  # noqa: E402, PLC0415

        name = "spsc_queues_fixture_12302019.md"
        (self.root / "results" / name).write_text(test_spsc_results.QUEUES_FILE,
                                                  encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 0, output)
        rows = [line for line in output.splitlines() if f"(results/{name})" in line]
        self.assertEqual(len(rows), 3, output)
        self.assertIn("| T-SPSC-1 | boost rtt / project rtt | 12302019 | macOS (M4 Max) | "
                      "not pinned | 1.78× | ≥ 1.16× | host differs | `abc` |", rows[0])
        path = self.root / "results" / name
        path.write_text(re.sub(r"(?m)^\| T-SPSC-\d \|.*\n", "", test_spsc_results.QUEUES_FILE),
                        encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn(f"results_table: FAIL results/{name}: no SPSC target row", output)

    def test_a_wasm_file_gets_a_row_of_its_own(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import test_demo  # noqa: E402, PLC0415
        import wasm_report  # noqa: E402, PLC0415

        native, browser, stderr = test_demo.records()
        name = "wasm_replay_mac_12302019.md"
        path = self.root / "results" / name
        path.write_text(wasm_report.render(native, browser, stderr, stderr), encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 0, output)
        rows = [line for line in output.splitlines() if f"(results/{name})" in line]
        self.assertEqual(len(rows), 1, output)
        self.assertIn("| T-WASM-1 | TEST | 12302019 | macOS (M4 Max) | Google Chrome for Testing "
                      "153.0.8010.12 | 24,875,622 | 19,083,969 | 1.30× | browser differs: Google "
                      "Chrome for Testing 153.0.8010.12, not Google Chrome | `0123456789ab` |",
                      rows[0])
        self.edit(f"results/{name}", "| Native, messages/s |", "| x |")
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
        # Every target row has its table's cells, no more: a stray | in a cell adds one.
        _, output = self.run_table()
        header = None
        for line in output.splitlines():
            if not line.startswith("|"):
                header = None
                continue
            header = header or line
            if line.startswith("| T-"):
                self.assertEqual(len(re.findall(r" \| ", line)),
                                 len(re.findall(r" \| ", header)), line)


    # The README's headline

    def headline(self):
        code, output = self.run_table()
        self.assertEqual(code, 0, output)
        lines = output[output.index("README.md:\n"):].splitlines()[1:]
        return [line for line in lines if line.startswith("| T-")]

    def test_the_headline_has_its_targets_in_order_each_from_its_file(self):
        rows = self.headline()
        self.assertEqual([row.split(" | ")[0][2:] for row in rows],
                         [target for target, *_ in results_table.HEADLINE])
        oracle, alloc, book, latency, backtest, spsc = rows
        self.assertIn("| 0 divergences in 3,081,392,329 messages over 10 sessions | "
                      "Darwin arm64 Apple M4 Max | met | [results/oracle_sessions.md]", oracle)
        self.assertIn("| 0 allocations after warm-up in 268,744,780 messages; the control book "
                      "made 324,511,069; the same on 4 arms | Darwin arm64 Apple M4 Max | met |",
                      alloc)
        self.assertIn("| ≥ 12.5 M msg/s, set on Ryzen 7 9700X, x86 Linux | 5.04 M msg/s | debian, "
                      "AMD Ryzen Embedded V1605B, pinned to core 4 | **missed**: by 7.46 M msg/s |",
                      book)
        self.assertIn("| p50 130.13 ns; p99 780.80 ns; p99.9 1,331.86 ns | debian,", latency)
        self.assertIn("| **missed**: p50 by 9.13 ns,", latency)
        self.assertIn("| ≥ 2.00 M messages/s | 4.57 M messages/s | debian, AMD Ryzen Embedded "
                      "V1605B, pinned to core 4 | met |", backtest)
        self.assertIn("| ≤ 1.31×, set on Ryzen 9 5950X, Linux, pinned | 0.95×, pipelined time / "
                      "single-threaded time |", spsc)

    def test_a_headline_row_typed_over_fails(self):
        self.edit("README.md", "| 5.04 M msg/s |", "| 12.6 M msg/s |")
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)
        self.assertRegex(output, r"results_table: FAIL README\.md:\d+: differs from a fresh run")
        self.assertIn("results_table: ok, RESULTS.md", output)

    def test_a_headline_value_follows_its_file(self):
        self.edit(HEADLINE_BOOK, "| rate 5.04 M msg/s | missed: rate by 7.46 M msg/s |",
                  "| rate 5.05 M msg/s | missed: rate by 7.45 M msg/s |")
        self.assertIn("| 5.05 M msg/s | debian,", self.headline()[2])
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)
        self.assertRegex(output, r"results_table: FAIL README\.md:\d+: differs from a fresh run")

    def test_a_stale_results_page_fails_and_both_pages_are_checked(self):
        self.edit("results/backtest_shadow_debian_12302019.md",
                  "| Replay rate, messages/s | 3,354,119 |", "| Replay rate, messages/s | 3,354,120 |")
        self.edit("README.md", "| 5.04 M msg/s |", "| 12.6 M msg/s |")
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)
        self.assertRegex(output, r"results_table: FAIL RESULTS\.md:\d+: differs from a fresh run")
        self.assertRegex(output, r"results_table: FAIL README\.md:\d+: differs from a fresh run")

    def test_a_missing_results_page_fails(self):
        (self.root / "RESULTS.md").unlink()
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)
        self.assertIn("results_table: FAIL RESULTS.md: the page is not there", output)

    def test_a_missing_headline_marker_fails(self):
        self.edit("README.md", results_table.HEAD_END + "\n", "")
        code, output = self.run_table("--check")
        self.assertEqual(code, 1, output)
        self.assertIn("results_table: FAIL README.md: the two marker lines", output)

    def test_a_headline_with_no_missed_target_is_refused(self):
        text = (self.root / HEADLINE_BOOK).read_text(encoding="utf-8")
        text = text.replace("| missed: rate by 7.46 M msg/s |", "| met |")
        text = text.replace("| missed: p50 by 9.13 ns, p99 by 228.80 ns, p99.9 by 457.86 ns |",
                            "| met |")
        (self.root / HEADLINE_BOOK).write_text(text, encoding="utf-8")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn("results_table: FAIL the headline shows no missed target", output)

    def test_a_headline_row_with_no_verdict_is_refused(self):
        self.edit(HEADLINE_BACKTEST, "| 4.57 M messages/s | met |",
                  "| 4.57 M messages/s | host differs: a macOS figure |")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn(f"results_table: FAIL {HEADLINE_BACKTEST}: T-BT-1's verdict is 'host "
                      "differs', neither met nor missed", output)

    def test_a_headline_file_that_is_gone_is_refused(self):
        (self.root / HEADLINE_BACKTEST).unlink()
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn(f"results_table: FAIL {HEADLINE_BACKTEST}: the headline's T-BT-1 row names "
                      "it, and it is not there", output)

    def test_a_headline_target_missing_from_its_targets_table_is_refused(self):
        self.edit(HEADLINE_BACKTEST, "| T-BT-1 | ≥ 2.00 M", "| T-BT-9 | ≥ 2.00 M")
        code, output = self.run_table()
        self.assertEqual(code, 1, output)
        self.assertIn("0 T-BT-1 rows in its targets table, not 1", output)

    def test_an_arm_that_allocated_misses_the_alloc_headline(self):
        self.edit(ORACLE, "| `paged_flat` | PASS | PASS | FAIL, 18,958 crossed and 1,018 locked | "
                          "PASS | PASS |",
                  "| `paged_flat` | PASS | PASS | FAIL, 18,958 crossed and 1,018 locked | "
                  "PASS | FAIL |")
        alloc = self.headline()[1]
        self.assertIn("the arms disagree: paged_flat FAIL", alloc)
        self.assertIn("| **missed** |", alloc)

    def test_the_headline_is_five_or_six_rows(self):
        for chosen in (results_table.HEADLINE[:4],
                       results_table.HEADLINE + results_table.HEADLINE[:1]):
            with self.assertRaisesRegex(results_table.TableError, "rows, not 5 to 6"):
                results_table.headline(self.root, chosen)
        self.assertEqual(len(results_table.headline(self.root, results_table.HEADLINE[:5])), 7)

if __name__ == "__main__":
    unittest.main()
