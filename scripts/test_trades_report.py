#!/usr/bin/env python3
"""Tests of trades_report.py with no dependency on data/.

The log is what itch_trades printed for tests/fixtures/trades/day.itch, with the header and
the timings a real run adds. Every failure below is one a report that stopped comparing
something would miss.
"""

import contextlib
import copy
import io
import json
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
    "messages": 88,
    "per_type_counts": {"A": 15, "B": 1, "C": 12, "D": 7, "E": 12, "F": 1, "H": 8, "P": 4,
                        "Q": 13, "R": 6, "S": 6, "U": 1, "X": 2},
}

GOOD_LOG = """\
run_commit abc1234 (clean)
run_compiler clang version 23
run_flags -O3 -DNDEBUG
run_host Darwin arm64
run_command itch_trades data/day.bin
trades_date 20200106
trades_max_orders 4096
trades_max_levels 4096
bytes 2995
broken_trade match=1060 class=E symbol=ZTST shares=100 printed_at=40200000002000
elapsed_seconds 0.0
frames 88
type A 15
type B 1
type C 12
type D 7
type E 12
type F 1
type H 8
type P 4
type Q 13
type R 6
type S 6
type U 1
type X 2
messages_checked 88
class E count=12 shares=1570 zero_share=0 test_count=1 test_shares=100
class C_Y count=3 shares=160 zero_share=0 test_count=0 test_shares=0
class C_N count=9 shares=1210 zero_share=0 test_count=0 test_shares=0
class P count=4 shares=429 zero_share=0 test_count=0 test_shares=0
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
match_records 41
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
b_net_printed_shares 2059
t2_file_rows 5
t2_rows_compared 5
t2_file_opening 620
t2_file_closing 660
t2_outside_file 150
t3_window 0935_1600
t3_rows 4
t3_rows_in_file 7
t3_second_rows 0
t3_symbols_compared 4
t3_midas_trade_shares 1489
t3_midas_hidden_shares 429
t3_midas_trades 12
t3_midas_hidden_trades 4
t3_outside_symbols 1
t3_outside_shares 200
t3_outside_test_symbols 1
t3_edge_start last_before=34499999999999 first_inside=34500000000000
t3_edge_end last_inside=57599999999999 first_after=57600000000000
t3_fit window=whole_day equal_shares=3 equal_hidden=4 equal_prints=3 equal_hidden_prints=4 equal_odd_lots=3 equal_odd_shares=3 feed_below=0 feed_shares=1859 feed_hidden=429 feed_prints=17
t3_fit window=0400_2000 equal_shares=3 equal_hidden=4 equal_prints=3 equal_hidden_prints=4 equal_odd_lots=3 equal_odd_shares=3 feed_below=0 feed_shares=1859 feed_hidden=429 feed_prints=17
t3_fit window=0930_1600 equal_shares=3 equal_hidden=4 equal_prints=3 equal_hidden_prints=4 equal_odd_lots=3 equal_odd_shares=3 feed_below=0 feed_shares=1609 feed_hidden=429 feed_prints=14
t3_fit window=0935_1600 equal_shares=4 equal_hidden=4 equal_prints=4 equal_hidden_prints=4 equal_odd_lots=4 equal_odd_shares=4 feed_below=0 feed_shares=1489 feed_hidden=429 feed_prints=12
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

    def run_report(self):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({
            "days": {"01062020": self.day},
            "references": {"midas_2019q4": {"nasdaq_rows": {"20200106": self.rows}}},
        }), encoding="utf-8")
        log = self.root / "log"
        log.write_text(self.log, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = report.main(["--day", "01062020", "--log", str(log), "--manifest", str(manifest),
                                "--out", str(self.root / "out.md"), *self.flags])
        return code, out.getvalue()

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
        self.assertIn("| `P` | 4 | 429 |", written)
        self.assertIn("| `C`, not printable, far leg of a print | 3 | 260 |", written)
        self.assertIn("| `C`, not printable, cross leg | 6 | 950 |", written)
        self.assertIn("| `Q`, zero shares | 5 | 0 |", written)
        self.assertIn("| `0935_1600` | 4 | 4 | 4 | 4 | 4 | 4 | 0 | 1,489 |", written)
        self.assertIn("| Start | 09:34:59.999999999 | 09:35:00.000000000 |", written)
        self.assertIn("Match 1060: `E` on ZTST, 100 shares.", written)

    def test_a_shorter_replay_than_the_census_fails(self):
        self.day["messages"] = 89
        self.assert_fails("frames 88, manifest 89")

    def test_a_tool_that_checked_part_of_the_day_fails(self):
        self.replace("messages_checked 88", "messages_checked 87")
        self.assert_fails("87 messages checked, manifest 88")

    def test_per_type_counts_are_compared(self):
        self.day["per_type_counts"]["A"] = 16
        self.assert_fails("per-type counts differ")

    def test_each_print_class_is_tied_to_the_census(self):
        for old, text in (("class E count=12", "E count differs"),
                          ("class C_N count=9", "C_Y + C_N differs"),
                          ("class P count=4", "P count differs"),
                          ("class Q_H count=1", "Q count differs")):
            self.log = GOOD_LOG
            self.replace(old, old[:-1] + "99")
            self.assert_fails(text)

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
        self.flags = ["--no-cross-file"]
        code, output = self.run_report()
        self.assertEqual(code, 0, output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("T-TRADE-2 is not available on 01062020", written)

    def test_a_cross_file_on_a_day_said_to_have_none_fails(self):
        self.flags = ["--no-cross-file"]
        self.assert_fails("T-TRADE-2 ran with --no-cross-file")

    def test_t_trade_3_not_run_fails(self):
        self.replace("verdict T-TRADE-3 PASS violations=0", "verdict T-TRADE-3 NOT_RUN violations=0")
        self.assert_fails("T-TRADE-3 verdict is NOT_RUN")

    def test_the_midas_row_pin_is_compared(self):
        self.rows = 5
        self.assert_fails("MIDAS rows 4, manifest 5")

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


if __name__ == "__main__":
    unittest.main()
