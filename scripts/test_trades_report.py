#!/usr/bin/env python3
"""Tests of trades_report.py with no dependency on data/.

The log is what itch_trades printed for tests/fixtures/trades/day.itch, with the header and
the timings a real run adds. Every failure below is one a report that stopped comparing
something would miss.
"""

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import trades_report as report  # noqa: E402

SCRATCH = report.REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)

GOOD_DAY = {
    "name": "day.bin",
    "unpacked_sha256": "ab",
    "unpacked_bytes": 3041,
    "messages": 89,
    "per_type_counts": {"A": 15, "B": 1, "C": 12, "D": 7, "E": 12, "F": 1, "H": 8, "P": 5,
                        "Q": 13, "R": 6, "S": 6, "U": 1, "X": 2},
}

GOOD_LOG = """\
run_commit abc1234 (clean)
run_compiler clang version 23
run_flags -O3 -DNDEBUG
run_host Darwin arm64
run_command itch_trades data/day.bin
trades_index hash
trades_levels linked
trades_date 20200106
trades_max_orders 4096
trades_max_levels 4096
bytes 3041
broken_trade match=1060 class=E symbol=ZTST shares=100 printed_at=40200000002000
elapsed_seconds 0.0
frames 89
type A 15
type B 1
type C 12
type D 7
type E 12
type F 1
type H 8
type P 5
type Q 13
type R 6
type S 6
type U 1
type X 2
messages_checked 89
class E count=12 shares=1570 zero_share=0 test_count=1 test_shares=100
class C_Y count=3 shares=160 zero_share=0 test_count=0 test_shares=0
class C_N count=9 shares=1210 zero_share=0 test_count=0 test_shares=0
class P count=5 shares=1429 zero_share=0 test_count=0 test_shares=0
class Q_O count=6 shares=660 zero_share=2 test_count=1 test_shares=40
class Q_C count=6 shares=660 zero_share=3 test_count=1 test_shares=0
class Q_H count=1 shares=150 zero_share=0 test_count=0 test_shares=0
class Q_I count=0 shares=0 zero_share=0 test_count=0 test_shares=0
t1_orders_opened 17
t1_orders_ran_out_on_execute 8
t1_orders_ran_out_on_cancel 1
t1_orders_deleted 7
t1_orders_replaced 1
t1_orders_live_end 0
t1_shares_added 4700
t1_shares_executed 2940
t1_shares_cancelled 70
t1_shares_deleted 1540
t1_shares_replaced_out 150
t1_book_checked_empty 1
t1_cancels 2
t5_population 9
t5_at_best 9
t5_at_front 8
t5_outside_at_best 2
t5_outside market_open=0 state=T count=2
t5_outside market_open=1 state=H count=1
c_price printable=Y equal=2 different=1 equal_shares=110 different_shares=50
c_price printable=N equal=1 different=8 equal_shares=150 different_shares=1060
match_records 42
match_shared_numbers 0
cn_far_leg print=E count=1 shares=100
cn_far_leg print=C_Y count=1 shares=60
cn_far_leg print=P count=1 shares=100
cn_cross_leg cross=Q_O count=2 shares=300 longest_lag_ns=0
cn_cross_leg cross=Q_C count=2 shares=400 longest_lag_ns=0
cn_cross_leg cross=Q_H count=2 shares=250 longest_lag_ns=6000000
cn_cross_sides_filled 6
cn_cross_sides_exact 1
b_messages 1
b_matched 1
b_shares 100
b_net_printed_shares 3059
t2_file_rows 5
t2_rows_compared 5
t2_file_opening 620
t2_file_closing 660
t2_outside_file 150
t3_window 0935_1600
t3_odd_lot_columns compared
t3_other_round_lots 1
t3_round_lot symbol=BRVO lot=10 sec_lot=100 feed_below_sec=1/99 midas=1/99 feed_below_lot=0/0 feed_below_100=1/99 feed_below_10=0/0
t3_sec_lot symbol=BRK.A lot=1 listed=0 compared=0
t3_sec_lot symbol=SEB lot=1 listed=1 compared=1
t3_sec_lot symbol=BH lot=10 listed=1 compared=1
t3_rows 4
t3_rows_in_file 7
t3_second_rows 0
t3_symbols_compared 4
t3_midas_trade_shares 2489
t3_midas_hidden_shares 1429
t3_midas_trades 13
t3_midas_hidden_trades 5
t3_outside_symbols 1
t3_outside_shares 200
t3_outside_test_symbols 1
t3_edge_start last_before=34499999999999 first_inside=34500000000000
t3_edge_end last_inside=57599999999999 first_after=57600000000000
t3_fit window=whole_day equal_shares=3 equal_hidden=4 equal_prints=3 equal_hidden_prints=4 equal_odd_lots=3 equal_odd_shares=3 feed_below=0 feed_shares=2859 feed_hidden=1429 feed_prints=18
t3_fit window=0400_2000 equal_shares=3 equal_hidden=4 equal_prints=3 equal_hidden_prints=4 equal_odd_lots=3 equal_odd_shares=3 feed_below=0 feed_shares=2859 feed_hidden=1429 feed_prints=18
t3_fit window=0930_1600 equal_shares=3 equal_hidden=4 equal_prints=3 equal_hidden_prints=4 equal_odd_lots=3 equal_odd_shares=3 feed_below=0 feed_shares=2609 feed_hidden=1429 feed_prints=15
t3_fit window=0935_1600 equal_shares=4 equal_hidden=4 equal_prints=4 equal_hidden_prints=4 equal_odd_lots=4 equal_odd_shares=4 feed_below=0 feed_shares=2489 feed_hidden=1429 feed_prints=13
check add_refused group=T-TRADE-1 violations=0
check add_symbol_mismatch group=T-TRADE-1 violations=0
check order_refused group=T-TRADE-1 violations=0
check order_mismatch group=T-TRADE-1 violations=0
check order_alive_at_zero group=T-TRADE-1 violations=0
check order_gone_early group=T-TRADE-1 violations=0
check remainder_mismatch group=T-TRADE-1 violations=0
check book_not_empty group=T-TRADE-1 violations=0
check ledger_not_empty group=T-TRADE-1 violations=0
check orders_unbalanced group=T-TRADE-1 violations=0
check shares_unbalanced group=T-TRADE-1 violations=0
check stream_incomplete group=T-TRADE-1 violations=0
check e_not_live group=PRINTS violations=0
check e_wrong_locate group=PRINTS violations=0
check e_over_remaining group=PRINTS violations=0
check e_zero_shares group=PRINTS violations=0
check c_not_live group=PRINTS violations=0
check c_wrong_locate group=PRINTS violations=0
check c_over_remaining group=PRINTS violations=0
check c_zero_shares group=PRINTS violations=0
check c_bad_printable group=PRINTS violations=0
check c_price_changed group=PRINTS violations=0
check e_not_at_best group=T-TRADE-5 violations=0
check p_reference_nonzero group=PRINTS violations=0
check p_side_not_b group=PRINTS violations=0
check p_book_changed group=PRINTS violations=0
check p_symbol_mismatch group=PRINTS violations=0
check q_book_changed group=PRINTS violations=0
check q_symbol_mismatch group=PRINTS violations=0
check q_unknown_cross_type group=PRINTS violations=0
check b_unknown_match group=PRINTS violations=0
check b_wrong_locate group=PRINTS violations=0
check b_broken_twice group=PRINTS violations=0
check b_book_changed group=PRINTS violations=0
check match_shared group=PRINTS violations=0
check match_leg_differs group=PRINTS violations=0
check other_book_changed group=PRINTS violations=0
check cn_over_cross group=PRINTS violations=0
check cn_price group=PRINTS violations=0
check cn_no_cross group=PRINTS violations=0
check t2_date group=T-TRADE-2 violations=0
check t2_total_row group=T-TRADE-2 violations=0
check t2_row_not_in_directory group=T-TRADE-2 violations=0
check t2_opening group=T-TRADE-2 violations=0
check t2_closing group=T-TRADE-2 violations=0
check t2_intraday group=T-TRADE-2 violations=0
check t2_outside_file group=T-TRADE-2 violations=0
check t3_row_not_in_directory group=T-TRADE-3 violations=0
check t3_row_count group=T-TRADE-3 violations=0
check t3_trade_shares group=T-TRADE-3 violations=0
check t3_hidden_shares group=T-TRADE-3 violations=0
check t3_trades group=T-TRADE-3 violations=0
check t3_hidden_trades group=T-TRADE-3 violations=0
check t3_odd_lots group=T-TRADE-3 violations=0
check t3_odd_lot_shares group=T-TRADE-3 violations=0
verdict T-TRADE-1 PASS violations=0
verdict T-TRADE-2 PASS violations=0
verdict T-TRADE-3 PASS violations=0
verdict T-TRADE-5 PASS violations=0
verdict PRINTS PASS violations=0
        9.00 real         8.00 user         1.00 sys
          1073741824  maximum resident set size
          2147483648  peak memory footprint
"""


class TradesReport(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.day = copy.deepcopy(GOOD_DAY)
        self.rows = 4
        self.log = GOOD_LOG
        self.flags = []
        self.arms = []
        self.midas_key = "midas_2019q4"
        self.references = {}

    def run_report(self, day="01062020"):
        manifest = self.root / "manifest.json"
        references = {self.midas_key: {"nasdaq_rows": {"20200106": self.rows}}, **self.references}
        manifest.write_text(json.dumps({"days": {day: self.day}, "references": references}),
                            encoding="utf-8")
        log = self.root / "log"
        log.write_text(self.log, encoding="utf-8")
        arm_args = []
        for index, arm_log in enumerate(self.arms):
            path = self.root / f"arm_log_{index}"
            path.write_text(arm_log, encoding="utf-8")
            arm_args += ["--arm-log", str(path)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = report.main(["--day", day, "--log", str(log), "--manifest", str(manifest),
                                "--out", str(self.root / "out.md"), *self.flags, *arm_args])
        return code, out.getvalue()

    def other_arm(self, index="paged", levels="flat"):
        """The good log as another arm, which took longer."""
        log = self.log.replace("trades_index hash", f"trades_index {index}")
        log = log.replace("trades_levels linked", f"trades_levels {levels}")
        return log.replace("elapsed_seconds 0.0", "elapsed_seconds 0.5")

    def assert_fails(self, text):
        code, output = self.run_report()
        self.assertEqual(code, 1, output)
        self.assertIn(text, output)

    def replace(self, old, new):
        self.assertIn(old, self.log)
        self.log = self.log.replace(old, new)

    def test_a_clean_day_passes_and_writes_the_results(self):
        code, output = self.run_report()
        self.assertEqual(code, 0, output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("- Commit: abc1234 (clean)", written)
        self.assertIn("- Flags: `-O3 -DNDEBUG`", written)
        self.assertIn("- Maximum RSS: 1.00 GiB", written)
        self.assertIn("- Peak memory footprint: 2.00 GiB", written)
        self.assertIn("| T-TRADE-3 | **PASS** | 0 |", written)
        self.assertIn("| `P` | 5 | 1,429 |", written)
        self.assertIn("| `C`, not printable, far leg of a print | 3 | 260 |", written)
        self.assertIn("| `C`, not printable, cross leg | 6 | 950 |", written)
        self.assertIn("| `Q`, zero shares | 5 | 0 |", written)
        self.assertIn("| `0935_1600` | 4 | 4 | 4 | 4 | 4 | 4 | 0 | 2,489 |", written)
        self.assertIn("| Start | 09:34:59.999999999 | 09:35:00.000000000 |", written)
        self.assertIn("| BRVO | 10 | 100 | 1 / 99 | 1 / 99 | 0 / 0 | 1 / 99 | 0 / 0 | SEC, 100 |",
                      written)
        self.assertIn("| BRK.A | 1 | no | no |", written)
        self.assertIn("| SEB | 1 | yes | yes |", written)
        self.assertIn("Match 1060: `E` on ZTST, 100 shares.", written)

    def test_every_arm_is_shown_and_may_differ_only_in_its_own_lines(self):
        self.arms = [self.other_arm("hash", "flat"), self.other_arm("paged", "linked")]
        code, output = self.run_report()
        self.assertEqual(code, 0, output)
        self.assertIn("the same on 3 arms", output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("## Every arm of the fast book", written)
        self.assertIn("| `paged_linked` | PASS | PASS | PASS | PASS | PASS | 89 | 0.5 | 1.00 | "
                      "2.00 | `arm_log_1` |", written)

    def test_an_arm_that_counts_another_figure_fails(self):
        self.arms = [self.other_arm().replace("t1_shares_executed 2940",
                                              "t1_shares_executed 2941")]
        self.assert_fails("arm paged_flat disagrees with hash_linked on t1_shares_executed")

    def test_an_arm_is_held_to_the_hard_checks_on_its_own(self):
        self.arms = [self.other_arm().replace("verdict T-TRADE-5 PASS violations=0",
                                              "verdict T-TRADE-5 FAIL violations=1")]
        self.assert_fails("arm paged_flat: T-TRADE-5 verdict is FAIL")

    def test_an_arm_given_twice_fails(self):
        self.arms = [self.other_arm(), self.other_arm()]
        self.assert_fails("arm paged_flat is given twice")

    def test_a_log_that_does_not_name_its_arm_cannot_be_used(self):
        self.replace("trades_index hash\n", "")
        code, _ = self.run_report()
        self.assertEqual(code, 2)

    def test_a_late_day_says_its_odd_lots_were_not_compared(self):
        self.rows = None
        self.replace("t3_odd_lot_columns compared", "t3_odd_lot_columns not_compared_rule_ended")
        self.replace("sec_lot=100 feed_below_sec=1/99", "sec_lot=none feed_below_sec=none")
        self.replace("equal_hidden_prints=4 equal_odd_lots=4 equal_odd_shares=4",
                     "equal_hidden_prints=4 equal_odd_lots=0 equal_odd_shares=0")
        code, output = self.run_report("11032025")
        self.assertEqual(code, 0, output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("T-TRADE-3's odd-lot columns were not compared on 11032025", written)
        self.assertIn("**The odd-lot columns were not compared on this day.**", written)
        self.assertIn("| BRVO | 10 | none | 1 / 99 | not compared | 0 / 0 | 1 / 99 | 0 / 0 |",
                      written)
        self.assertIn("| `0935_1600` | 4 | 4 | 4 | 4 | not compared | not compared | 0 |", written)

    def test_a_late_day_compared_on_the_old_rule_fails(self):
        self.rows = None
        code, output = self.run_report("11032025")
        self.assertEqual(code, 1, output)
        self.assertIn("odd-lot columns compared on 20251103, wanted not_compared_rule_ended",
                      output)

    def test_an_early_day_with_its_odd_lots_left_out_fails(self):
        self.replace("t3_odd_lot_columns compared", "t3_odd_lot_columns not_compared_rule_ended")
        self.assert_fails("wanted compared")

    def test_a_log_that_does_not_say_what_became_of_the_odd_lots_fails(self):
        self.replace("t3_odd_lot_columns compared\n", "")
        self.assert_fails("odd-lot columns None on 20200106")

    def test_a_failing_run_writes_why_into_the_results(self):
        self.replace("window=0935_1600 equal_shares=4", "window=0935_1600 equal_shares=3")
        self.replace("check add_refused group=T-TRADE-1 violations=0\n",
                     "violation t3_trade_shares message=9 locate=1 symbol=ALFA feed 2, MIDAS 1\n"
                     "check add_refused group=T-TRADE-1 violations=0\n")
        self.assert_fails("equal_shares 3 of 4 symbols")
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("- equal_shares 3 of 4 symbols", written)
        self.assertIn("- `t3_trade_shares message=9 locate=1 symbol=ALFA feed 2, MIDAS 1`", written)

    def test_a_shorter_replay_than_the_census_fails(self):
        self.day["messages"] = 90
        self.assert_fails("frames 89, manifest 90")

    def test_a_tool_that_checked_part_of_the_day_fails(self):
        self.replace("messages_checked 89", "messages_checked 88")
        self.assert_fails("88 messages checked, manifest 89")

    def test_per_type_counts_are_compared(self):
        self.day["per_type_counts"]["A"] = 16
        self.assert_fails("per-type counts differ")

    def test_each_print_class_is_tied_to_the_census(self):
        for old, text in (("class E count=12", "E count differs"),
                          ("class C_N count=9", "C_Y + C_N differs"),
                          ("class P count=5", "P count differs"),
                          ("class Q_H count=1", "Q count differs")):
            self.log = GOOD_LOG
            self.replace(old, old[:-1] + "99")
            self.assert_fails(text)

    def test_a_broken_trade_prints_its_shares_with_the_separator_and_one_share_singular(self):
        # The break's shares no longer match b_shares, so the run fails; the file is written.
        for shares, line in (("1000", "Match 1060: `E` on ZTST, 1,000 shares."),
                             ("1", "Match 1060: `E` on ZTST, 1 share.")):
            self.log = GOOD_LOG.replace("shares=100 printed_at", f"shares={shares} printed_at")
            self.run_report()
            self.assertIn(line, (self.root / "out.md").read_text(encoding="utf-8"))

    def test_a_break_with_no_print_fails(self):
        self.replace("b_matched 1", "b_matched 0")
        self.assert_fails("a B matched no print")

    def test_the_order_lifecycle_is_recomputed_here(self):
        self.replace("t1_orders_deleted 7", "t1_orders_deleted 6")
        self.assert_fails("deletes differ from D")
        self.log = GOOD_LOG
        self.replace("t1_orders_replaced 1", "t1_orders_replaced 0")
        self.assert_fails("replaces differ from U")
        self.log = GOOD_LOG
        self.replace("t1_orders_opened 17", "t1_orders_opened 18")
        self.assert_fails("orders opened differ from A + F + U")
        self.log = GOOD_LOG
        self.replace("t1_orders_ran_out_on_cancel 1", "t1_orders_ran_out_on_cancel 2")
        self.assert_fails("17 orders opened, 18 accounted for")

    def test_the_shares_are_recomputed_here(self):
        self.replace("t1_shares_cancelled 70", "t1_shares_cancelled 71")
        self.assert_fails("4700 shares added, 4701 accounted for")

    def test_orders_left_at_the_end_fail(self):
        self.replace("t1_orders_live_end 0", "t1_orders_live_end 1")
        self.assert_fails("orders live at the end of the day")

    def test_a_book_never_checked_empty_fails(self):
        self.replace("t1_book_checked_empty 1", "t1_book_checked_empty 0")
        self.assert_fails("the book was never checked at S/C")

    def test_t_trade_5_has_to_account_for_every_e(self):
        self.replace("t5_outside market_open=1 state=H count=1",
                     "t5_outside market_open=1 state=H count=2")
        self.assert_fails("does not account for every E")

    def test_an_e_away_from_the_best_price_fails(self):
        self.replace("t5_at_best 9", "t5_at_best 8")
        self.assert_fails("an E in the population was not at the best price")

    def test_a_check_with_a_violation_fails(self):
        self.replace("check e_not_at_best group=T-TRADE-5 violations=0",
                     "check e_not_at_best group=T-TRADE-5 violations=1")
        self.assert_fails("check e_not_at_best (T-TRADE-5): 1 violations")

    def test_every_verdict_must_pass(self):
        for name in report.RESULTS:
            self.log = GOOD_LOG
            self.replace(f"verdict {name} PASS violations=0", f"verdict {name} FAIL violations=1")
            self.assert_fails(f"{name} verdict is FAIL")

    def test_t_trade_2_can_be_absent_only_when_said_so(self):
        self.replace("verdict T-TRADE-2 PASS violations=0",
                     "verdict T-TRADE-2 NOT_AVAILABLE violations=0")
        self.assert_fails("T-TRADE-2 verdict is NOT_AVAILABLE")
        # Said so on a day whose manifest pins no CrossStats file: not available, and a pass.
        self.flags = ["--no-cross-file"]
        code, output = self.run_report()
        self.assertEqual(code, 0, output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("T-TRADE-2 is not available on 01062020", written)
        # A Nasdaq day whose CrossStats file is pinned has one, so saying so is refused.
        self.references = {"crossstats_20200106": {"filename": "CrossStats20200106.txt"}}
        code, output = self.run_report()
        self.assertEqual(code, 2, output)
        self.assertIn("--no-cross-file on 01062020, whose CrossStats file the manifest pins as "
                      "crossstats_20200106", output)

    def test_gnu_time_lines_are_read_and_a_missing_figure_is_unavailable(self):
        mac = ("        9.00 real         8.00 user         1.00 sys\n"
               "          1073741824  maximum resident set size\n"
               "          2147483648  peak memory footprint\n")
        self.replace(mac, "\tCommand being timed: \"itch_trades data/day.bin\"\n"
                          "\tUser time (seconds): 340.56\n\tSystem time (seconds): 3.84\n"
                          "\tElapsed (wall clock) time (h:mm:ss or m:ss): 1:05:51.09\n"
                          "\tMaximum resident set size (kbytes): 2097152\n\tExit status: 0\n")
        code, output = self.run_report()
        self.assertEqual(code, 0, output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("- Elapsed: 0.0 s in the replay, 3951.1 s real including any wait for the "
                      "machine, 340.6 s user", written)
        self.assertIn("- Maximum RSS: 2.00 GiB, which counts resident pages of the mapped file",
                      written)
        self.assertIn("- Peak memory footprint: unavailable\n", written)
        self.assertIn("| 89 | 0.0 | 2.00 | unavailable | `log` |", written)
        # No timer at all: every figure unavailable, none 0.
        self.log = GOOD_LOG.replace(mac, "").replace("elapsed_seconds 0.0\n", "")
        code, output = self.run_report()
        self.assertEqual(code, 0, output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("- Elapsed: replay time unavailable, real time unavailable, user time "
                      "unavailable", written)
        self.assertIn("- Maximum RSS: unavailable\n", written)
        self.assertIn("| 89 | unavailable | unavailable | unavailable | `log` |", written)
        self.assertNotIn("0.00 GiB", written)

    def test_the_test_symbol_line_sums_what_it_says(self):
        self.replace("class C_Y count=3 shares=160 zero_share=0 test_count=0 test_shares=0",
                     "class C_Y count=3 shares=160 zero_share=0 test_count=1 test_shares=60")
        self.replace("class C_N count=9 shares=1210 zero_share=0 test_count=0 test_shares=0",
                     "class C_N count=9 shares=1210 zero_share=0 test_count=2 test_shares=200")
        self.replace("class P count=5 shares=1429 zero_share=0 test_count=0 test_shares=0",
                     "class P count=5 shares=1429 zero_share=0 test_count=1 test_shares=29")
        code, output = self.run_report()
        self.assertEqual(code, 0, output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        # E 1 of 100 shares, C_Y 1 of 60 and P 1 of 29; Q_O 1 of 40, and Q_C 1 of none.
        self.assertIn("Test symbols: 3 prints of `E + C_Y + P` with 189 shares, and 2 `Q` crosses "
                      "with 40 shares. They are checked per message, and left out of both "
                      "external comparisons. Their `C_N`, 2 with 200 shares, are in neither "
                      "count: a `C_N` is not a print, and its shares are those of the print or "
                      "cross beside it.", written)

    def test_the_log_must_have_replayed_the_manifest_s_bytes(self):
        self.replace("bytes 3041", "bytes 3040")
        self.assert_fails("bytes 3040, manifest unpacked_bytes 3041")
        self.log = GOOD_LOG
        del self.day["unpacked_bytes"]
        code, output = self.run_report()
        self.assertEqual(code, 2, output)
        self.assertIn("the manifest pins no unpacked_bytes for the day", output)

    def test_each_published_figure_names_its_source(self):
        for day, rows in report.PUBLISHED.items():
            for label, source, _, _ in rows:
                self.assertRegex(source, r"\(BENCHMARKS\.md, T-TRADE-\d\)", label)
                self.assertNotIn("research", source, label)
                if label.startswith(("CrossStats", "MIDAS")):
                    self.assertTrue(source.endswith(": a re-parse"), label)
        # The section says what a re-parse is.
        report.PUBLISHED["01062020"] = [("Deletes", report.KAREEM, 7, "t1_orders_deleted")]
        self.addCleanup(report.PUBLISHED.pop, "01062020")
        code, output = self.run_report()
        self.assertEqual(code, 0, output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("| Deletes | KareemJandali census (BENCHMARKS.md, T-TRADE-1) | 7 | 7 | +0 |",
                      written)
        self.assertIn("A re-parse sets the figure read from a file beside the tool's reading of "
                      "the same file, so it tests the parse, not the book.", written)

    def test_each_published_figure_is_in_the_target_it_names(self):
        benchmarks = report.REPO_ROOT / "BENCHMARKS.md"
        if not benchmarks.is_file():
            self.skipTest("no BENCHMARKS.md in this tree")
        text = benchmarks.read_text(encoding="utf-8")
        for rows in report.PUBLISHED.values():
            for label, source, claim, _ in rows:
                target = source.split("(BENCHMARKS.md, ", 1)[1].split(")", 1)[0]
                section = text.split(f"### {target}:", 1)[1].split("\n### ", 1)[0]
                self.assertIn(f"{claim:,}", section, label)

    def test_a_cross_file_on_a_day_said_to_have_none_fails(self):
        self.flags = ["--no-cross-file"]
        self.assert_fails("T-TRADE-2 ran with --no-cross-file")

    def test_t_trade_3_not_run_fails(self):
        self.replace("verdict T-TRADE-3 PASS violations=0", "verdict T-TRADE-3 NOT_RUN violations=0")
        self.assert_fails("T-TRADE-3 verdict is NOT_RUN")

    def test_the_midas_row_pin_is_compared(self):
        self.rows = 5
        self.assert_fails("MIDAS rows 4, manifest 5")

    def test_the_midas_row_pin_is_found_under_any_quarter(self):
        self.midas_key = "midas_2020q1"
        self.rows = 5
        self.assert_fails("MIDAS rows 4, manifest 5")

    def test_a_date_pinned_under_two_zips_is_refused(self):
        self.references = {"midas_2020q1": {"nasdaq_rows": {"20200106": 4}}}
        code, output = self.run_report()
        self.assertEqual(code, 2, output)
        self.assertIn("2 MIDAS zips pin Nasdaq rows for 20200106", output)

    def bx_day(self):
        """The good day as a BX feed: Boston's rows compared, and no cross file."""
        self.day = dict(self.day, venue="BX", midas_exchange="Boston")
        self.replace("trades_date 20200106", "trades_date 20200106\ntrades_midas_exchange Boston")
        self.replace("verdict T-TRADE-2 PASS violations=0",
                     "verdict T-TRADE-2 NOT_AVAILABLE violations=0")
        self.midas_key = "midas_2020q1"
        self.flags = ["--no-cross-file"]

    def run_bx(self, rows=None):
        manifest_rows = {"boston_rows": {"20200106": self.rows if rows is None else rows}}
        self.references = {"midas_2020q1": manifest_rows}
        self.midas_key = "midas_2019q4"
        return self.run_report(day="01062020_BX")

    def test_a_bx_day_is_held_to_boston_rows(self):
        self.bx_day()
        code, output = self.run_bx()
        self.assertEqual(code, 0, output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("| MIDAS, Boston rows | Count |", written)
        self.assertIn("| Ticker | SEC lot | In the BX directory | Has a Boston row in MIDAS |",
                      written)
        self.assertIn("CrossStats is Nasdaq's file of its own crosses, and this is a BX feed",
                      written)
        code, output = self.run_bx(rows=5)
        self.assertEqual(code, 1, output)
        self.assertIn("MIDAS rows 4, manifest 5", output)

    def test_a_bx_day_compared_on_nasdaq_rows_fails(self):
        self.bx_day()
        self.replace("trades_midas_exchange Boston", "trades_midas_exchange Nasdaq")
        code, output = self.run_bx()
        self.assertEqual(code, 1, output)
        self.assertIn("MIDAS compared on Nasdaq's rows, and the day's exchange is Boston", output)

    def test_a_nasdaq_day_compared_on_another_exchange_fails(self):
        self.replace("trades_date 20200106", "trades_date 20200106\ntrades_midas_exchange Boston")
        self.assert_fails("MIDAS compared on Boston's rows, and the day's exchange is Nasdaq")

    def test_a_venue_suffix_on_the_key_keeps_the_date(self):
        self.rows = 5
        code, output = self.run_report(day="01062020_BX")
        self.assertEqual(code, 1, output)
        self.assertIn("MIDAS rows 4, manifest 5", output)

    def test_another_window_is_not_a_pass(self):
        self.replace("t3_window 0935_1600", "t3_window 0930_1600")
        self.assert_fails("T-TRADE-3 compared on 0930_1600")

    def test_the_pinned_fit_must_cover_every_symbol(self):
        self.replace("window=0935_1600 equal_shares=4", "window=0935_1600 equal_shares=3")
        self.assert_fails("equal_shares 3 of 4 symbols")
        self.log = GOOD_LOG
        self.replace("window=0935_1600 equal_shares=4 equal_hidden=4",
                     "window=0935_1600 equal_shares=4 equal_hidden=3")
        self.assert_fails("equal_hidden 3 of 4 symbols")
        self.log = GOOD_LOG
        self.replace("equal_hidden_prints=4 equal_odd_lots=4 equal_odd_shares=4 feed_below=0 "
                     "feed_shares=2489",
                     "equal_hidden_prints=4 equal_odd_lots=3 equal_odd_shares=4 feed_below=0 "
                     "feed_shares=2489")
        self.assert_fails("equal_odd_lots 3 of 4 symbols")

    def test_every_row_has_to_be_compared(self):
        self.replace("t3_symbols_compared 4", "t3_symbols_compared 3")
        self.assert_fails("MIDAS rows are not all compared")

    def test_a_reference_that_did_not_parse_fails(self):
        self.log += "reference_error midas bad_value line=7\n"
        self.assert_fails("a reference file failed to parse")

    def test_a_log_with_no_summary_cannot_be_used(self):
        self.log = "frames 88\n"
        code, output = self.run_report()
        self.assertEqual(code, 2, output)

    def test_an_unknown_day_cannot_be_used(self):
        self.day = None
        log = self.root / "log"
        log.write_text(GOOD_LOG, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            code = report.main(["--day", "01012000", "--log", str(log)])
        self.assertEqual(code, 2)


class CheckRealDayCrossFile(unittest.TestCase):
    """check_real_day.sh's trade step on a pinned CrossStats file, in a tree of its own where
    cmake, itch_trades, extract_midas.py and trades_report.py are stand-ins, and fetch_data.py
    and the script are the real ones."""

    CROSS = b"Date      ,Symbol,ListingMarket,OpeningCross,ClosingCross,IntradayCross,\n"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.dir.cleanup)
        self.tree = Path(self.dir.name)
        scripts = self.tree / "scripts"
        scripts.mkdir()
        for name in ("check_real_day.sh", "fetch_data.py"):
            shutil.copy(report.REPO_ROOT / "scripts" / name, scripts / name)
        stub = "import json, sys\nopen(sys.argv[0] + '.args', 'w').write(json.dumps(sys.argv[1:]))\n"
        for name in ("extract_midas.py", "trades_report.py", "oracle_report.py"):
            (scripts / name).write_text(stub, encoding="utf-8")
        tools = self.tree / "build" / "release" / "tools"
        tools.mkdir(parents=True)
        (tools / "itch_trades").write_text('#!/bin/sh\necho "$@" > "$0.args"\n',
                                           encoding="utf-8")
        (tools / "itch_trades").chmod(0o755)
        bin_dir = self.tree / "bin"
        bin_dir.mkdir()
        (bin_dir / "cmake").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (bin_dir / "cmake").chmod(0o755)
        (self.tree / "data").mkdir()
        self.references = {"midas_2020q1": {"nasdaq_rows": {"20200106": 4}},
                           "crossstats_20200106": {
                               "filename": "CrossStats20200106.txt", "format": "text",
                               "bytes": len(self.CROSS),
                               "sha256": hashlib.sha256(self.CROSS).hexdigest()}}

    def run_day(self):
        (self.tree / "scripts" / "data_manifest.json").write_text(json.dumps(
            {"days": {"01062020": GOOD_DAY}, "references": self.references}), encoding="utf-8")
        env = dict(os.environ, PATH=f"{self.tree / 'bin'}{os.pathsep}{os.environ['PATH']}",
                   ITCH_REAL_DAY_LOGS=str(self.tree / "logs"))
        done = subprocess.run(["bash", str(self.tree / "scripts" / "check_real_day.sh"),
                               "--only", "trades", "01062020"], capture_output=True, text=True,
                              env=env, check=False, timeout=60)
        return done.returncode, done.stdout + done.stderr

    def tool_args(self):
        path = self.tree / "build" / "release" / "tools" / "itch_trades.args"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def report_args(self):
        path = self.tree / "scripts" / "trades_report.py.args"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def test_a_pinned_cross_file_is_verified_and_given_to_the_tool(self):
        (self.tree / "data" / "CrossStats20200106.txt").write_bytes(self.CROSS)
        code, output = self.run_day()
        self.assertEqual(code, 0, output)
        self.assertIn("--crossstats data/CrossStats20200106.txt", self.tool_args())
        self.assertNotIn("--no-cross-file", self.report_args())

    def test_a_pinned_cross_file_that_is_missing_fails_the_day(self):
        code, output = self.run_day()
        self.assertEqual(code, 1, output)
        self.assertIn("real day: 01062020 has no cross file data/CrossStats20200106.txt, and the "
                      "manifest pins crossstats_20200106", output)
        self.assertIsNone(self.tool_args())
        self.assertIsNone(self.report_args())

    def test_a_cross_file_that_is_not_the_pinned_one_fails_the_day(self):
        (self.tree / "data" / "CrossStats20200106.txt").write_bytes(self.CROSS.replace(b"D", b"d"))
        code, output = self.run_day()
        self.assertEqual(code, 1, output)
        self.assertIn("real day: 01062020's cross file data/CrossStats20200106.txt is not the one "
                      "the manifest pins", output)
        self.assertIsNone(self.tool_args())

    def test_a_day_with_no_pinned_cross_file_is_reported_not_available(self):
        del self.references["crossstats_20200106"]
        code, output = self.run_day()
        self.assertEqual(code, 0, output)
        self.assertIn("has no cross file in the manifest, so T-TRADE-2 is not available", output)
        self.assertNotIn("--crossstats", self.tool_args())
        self.assertIn("--no-cross-file", self.report_args())


if __name__ == "__main__":
    unittest.main()
