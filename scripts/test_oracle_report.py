#!/usr/bin/env python3
"""Tests of oracle_report.py with no dependency on data/.

The day here is three messages. Each test states what the tool printed, so that a report
that stopped comparing something fails here rather than on a real day.
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

import oracle_report as report  # noqa: E402

SCRATCH = report.REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)

GOOD_DAY = {
    "name": "day.bin",
    "unpacked_sha256": "ab",
    "messages": 4,
    "per_type_counts": {"A": 2, "U": 1, "S": 1},
}

GOOD_LOG = """\
run_commit abc1234 (clean)
run_compiler clang version 23
run_flags -O3 -DNDEBUG
run_host Darwin arm64
run_command itch_oracle data/day.bin
oracle_hash_every 2
oracle_exhaustive 0
elapsed_seconds 1.5
frames 4
type A 2
type S 1
type U 1
messages_diffed 4
status_checks 4
touched_checks 3
empty_touched_checks 1
bbo_checks 8
write_set_checks 4
write_set_writes 8
write_set_violations 0
periodic_hash_checks 2
final_hash_checks 1
production_hash_checks 3
exhaustive_checks 0
last_hash 00ff
live_orders_end 0
live_levels_end 0
peak_live_orders 2
peak_live_levels 2
malformed_frames 0
end end_of_input
divergences 0
t_inv1_violations 0
t_inv2_crossed 0
t_inv2_locked 0
t_inv2_tally type=A scope=all checks=2 two_sided=1 crossed=0 locked=0
t_inv2_tally type=A scope=open_t checks=2 two_sided=1 crossed=0 locked=0
t_inv2_tally type=U scope=all checks=1 two_sided=1 crossed=0 locked=0
t_inv2_tally type=S scope=all checks=1 two_sided=0 crossed=0 locked=0
t_inv2_deepest_crossing 0
t_inv2_crossed_symbols 0
t_inv2_locked_symbols 0
t_inv2_not_in_t crossed=0 locked=0
t_inv2_no_action_yet crossed=0 locked=0
t_inv2_in_reopening_window crossed=0 locked=0
t_inv2_in_t_outside_window crossed=0 locked=0
t_inv2_market_open crossed=0 locked=0
t_inv2_market_open_and_t crossed=0 locked=0
t_inv2_clock_0930_1600 crossed=0 locked=0
t_inv2_in_t_within_50ms_of_resumption crossed=0 locked=0
t_inv2_in_t_later_than_50ms crossed=0 locked=0
t_inv2_in_t_never_resumed crossed=0 locked=0
t_inv2_reopening_windows 0
t_inv3_new crossed=0 locked=0
t_inv3_leftover crossed=0 locked=0
t_inv3_leftovers_opened 0
t_inv3_ended_by_book 0
t_inv3_ended_by_cross 0
t_inv3_longest_leftover_ns 0
t_inv3_still_in_force 0
verdict T-ORACLE-1 PASS
verdict T-INV-1 PASS violations=0
verdict T-INV-2 PASS crossed=0 locked=0
verdict T-INV-3 PASS violations=0
        9.00 real         8.00 user         1.00 sys
          1073741824  maximum resident set size
          2147483648  peak memory footprint
"""

GOOD_ALLOC = """\
fast_messages 4
fast_allocations 0
control_messages 4
control_allocations 9
verdict T-ALLOC-1 PASS fast=0 control=9
"""


class OracleReport(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.day = copy.deepcopy(GOOD_DAY)
        self.log = GOOD_LOG
        self.alloc = GOOD_ALLOC

    def run_report(self):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"days": {"day": self.day}}), encoding="utf-8")
        log = self.root / "log"
        log.write_text(self.log, encoding="utf-8")
        alloc = self.root / "alloc"
        alloc.write_text(self.alloc, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = report.main(["--day", "day", "--log", str(log), "--alloc-log", str(alloc),
                                "--manifest", str(manifest), "--out",
                                str(self.root / "out.md")])
        return code, out.getvalue()

    def assert_fails(self, text):
        code, output = self.run_report()
        self.assertEqual(code, 1, output)
        self.assertIn(text, output)

    def test_a_clean_day_passes_and_writes_the_results(self):
        code, output = self.run_report()
        self.assertEqual(code, 0, output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("- Commit: abc1234 (clean)", written)
        self.assertIn("- Flags: `-O3 -DNDEBUG`", written)
        self.assertIn("- Maximum RSS: 1.00 GiB", written)
        self.assertIn("- Peak memory footprint: 2.00 GiB", written)
        self.assertIn("| Messages diffed | 4 |", written)
        self.assertIn("| Fast book | 4 | 0 |", written)
        self.assertIn("Verdict, strict: **PASS**", written)

    def test_a_shorter_replay_than_the_census_fails(self):
        self.day["messages"] = 5
        self.assert_fails("frames 4, manifest 5")

    def test_a_tool_that_diffed_part_of_the_day_fails(self):
        self.log = self.log.replace("messages_diffed 4", "messages_diffed 2")
        self.assert_fails("messages_diffed 2, manifest 4")

    def test_per_type_counts_are_compared(self):
        self.day["per_type_counts"] = {"A": 3, "S": 1}
        self.assert_fails("per-type counts differ")

    def test_a_missing_touched_check_fails(self):
        self.log = self.log.replace("touched_checks 3", "touched_checks 1")
        self.assert_fails("touched levels")

    def test_bbo_checks_must_cover_both_sides(self):
        self.log = self.log.replace("bbo_checks 8", "bbo_checks 4")
        self.assert_fails("bbo_checks is not two per message")

    def test_periodic_hashes_must_match_the_interval(self):
        self.log = self.log.replace("periodic_hash_checks 2", "periodic_hash_checks 1")
        self.assert_fails("periodic hash count")

    def test_a_missing_final_hash_fails(self):
        self.log = self.log.replace("final_hash_checks 1", "final_hash_checks 0")
        self.assert_fails("no final hash")

    def test_a_divergence_fails(self):
        self.log = self.log.replace("divergences 0", "divergences 1\nDIVERGENCE kind=fifo")
        self.assert_fails("divergences")

    def test_a_write_set_violation_fails(self):
        self.log = self.log.replace("write_set_violations 0", "write_set_violations 1")
        self.assert_fails("write-set violations")

    def test_a_t_inv_1_violation_fails(self):
        self.log = self.log.replace("t_inv1_violations 0", "t_inv1_violations 1")
        self.assert_fails("T-INV-1 violations")

    def test_the_tools_own_verdict_must_pass(self):
        self.log = self.log.replace("verdict T-ORACLE-1 PASS", "verdict T-ORACLE-1 FAIL")
        self.assert_fails("T-ORACLE-1 verdict is not PASS")

    def test_an_unclean_end_fails(self):
        self.log = self.log.replace("end end_of_input", "end truncated_payload")
        self.assert_fails("did not end cleanly")

    def test_t_alloc_1_must_pass_over_the_whole_day(self):
        self.alloc = self.alloc.replace("verdict T-ALLOC-1 PASS", "verdict T-ALLOC-1 FAIL")
        self.assert_fails("T-ALLOC-1 verdict is not PASS")
        self.alloc = GOOD_ALLOC.replace("fast_messages 4", "fast_messages 3")
        self.assert_fails("did not cover the whole day")

    def test_a_crossed_day_is_reported_failing_with_its_own_code(self):
        self.log = self.log.replace("verdict T-INV-2 PASS crossed=0 locked=0",
                                    "verdict T-INV-2 FAIL crossed=5 locked=0")
        code, output = self.run_report()
        self.assertEqual(code, 3, output)
        self.assertIn("T-INV-2 FAIL", output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("Verdict, strict: **FAIL**", written)

    def test_a_leftover_only_day_keeps_the_t_inv_2_code_and_passes_t_inv_3(self):
        self.log = self.log.replace("verdict T-INV-2 PASS crossed=0 locked=0",
                                    "verdict T-INV-2 FAIL crossed=5 locked=1")
        self.log = self.log.replace("t_inv3_leftover crossed=0 locked=0",
                                    "t_inv3_leftover crossed=5 locked=1")
        self.log = self.log.replace("t_inv2_market_open_and_t crossed=0 locked=0",
                                    "t_inv2_market_open_and_t crossed=5 locked=1")
        code, output = self.run_report()
        self.assertEqual(code, 3, output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("## T-INV-3: no new cross in normal trading", written)
        self.assertIn("Verdict: **PASS**, 0 violations.", written)
        self.assertIn("| With a leftover in force, allowed | 5 | 1 |", written)

    def test_a_new_cross_is_a_hard_failure_shown_in_full(self):
        self.log = self.log.replace("verdict T-INV-2 PASS crossed=0 locked=0",
                                    "verdict T-INV-2 FAIL crossed=1 locked=0")
        self.log = self.log.replace("verdict T-INV-3 PASS violations=0",
                                    "verdict T-INV-3 FAIL violations=1")
        self.log = self.log.replace("t_inv3_new crossed=0 locked=0",
                                    "t_inv3_violation kind=crossed symbol=ABC locate=7 "
                                    "time=10:00:00.000000000 message=9 type=A depth=300\n"
                                    "t_inv3_new crossed=1 locked=0")
        self.log = self.log.replace("t_inv2_market_open_and_t crossed=0 locked=0",
                                    "t_inv2_market_open_and_t crossed=1 locked=0")
        self.assert_fails("T-INV-3 verdict is not PASS")
        code, output = self.run_report()
        self.assertEqual(code, 1, output)
        self.assertIn("T-INV-3 new crosses: 1 crossed and 0 locked", output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("Verdict: **FAIL**, 1 violations.", written)
        self.assertIn("| crossed | ABC | 10:00:00.000000000 | 9 | `A` | 300 |", written)

    def test_a_count_of_new_crosses_with_none_printed_fails(self):
        self.log = self.log.replace("t_inv3_new crossed=0 locked=0",
                                    "t_inv3_new crossed=2 locked=0")
        self.assert_fails("0 T-INV-3 violations printed, 2 counted")

    def test_t_inv_3_has_to_account_for_its_whole_population(self):
        self.log = self.log.replace("t_inv2_market_open_and_t crossed=0 locked=0",
                                    "t_inv2_market_open_and_t crossed=1 locked=0")
        self.assert_fails("T-INV-3 accounts for 0 crossed and 0 locked")

    def test_more_leftovers_than_resumptions_fails(self):
        self.log = self.log.replace("t_inv3_leftovers_opened 0", "t_inv3_leftovers_opened 1")
        self.assert_fails("more leftovers than resumptions")

    def test_a_log_with_no_t_inv_3_cannot_be_used(self):
        self.log = self.log.replace("t_inv3_new crossed=0 locked=0\n", "")
        code, _ = self.run_report()
        self.assertEqual(code, 2)

    def test_populations_are_summed_by_type_and_scope(self):
        log = report.parse_log(self.log + "t_inv2_tally type=U scope=open_t checks=5 two_sided=4 "
                                          "crossed=3 locked=2\n")
        self.assertEqual(report.sum_tallies(log, "AFU", "open_t"), (7, 5, 3, 2))
        self.assertEqual(report.sum_tallies(log, "AFECXDU", "all"), (3, 2, 0, 0))

    def test_the_comparison_counts_a_replace_twice_as_carteret_does(self):
        log = report.parse_log(self.log + "t_inv2_cause type=A cause=window crossed=5 locked=1\n"
                                          "t_inv2_cause type=U cause=window crossed=2 locked=0\n"
                                          "t_inv2_cause type=I cause=window crossed=9 locked=9\n"
                                          "t_inv2_cause type=U cause=clock crossed=2 locked=0\n"
                                          "t_inv2_cause type=D cause=not_t crossed=3 locked=4\n")
        rows = {row[0]: row for row in report.comparison_rows(log)}
        self.assertEqual(rows["reopening window, crossed"][2:], (635, 9))
        self.assertEqual(rows["reopening window, locked"][2:], (8, 1))
        self.assertEqual(rows["inside 09:30 to 16:00, crossed"][2:], (8372, 4))
        self.assertEqual(rows["not in state T, locked"][2:], (62, 4))
        self.assertEqual(rows["book messages, crossed"][2:], (8580, 12))

    def test_a_log_with_a_missing_counter_cannot_be_used(self):
        self.log = self.log.replace("bbo_checks 8\n", "")
        code, _ = self.run_report()
        self.assertEqual(code, 2)

    def test_flags_keep_no_paths(self):
        commands = self.root / "compile_commands.json"
        commands.write_text(json.dumps([{
            "file": "/some/where/tools/itch_oracle.cpp",
            "command": "/x/clang++ -I/some/where/include -isystem /x/inc -O3 -DNDEBUG "
                       "-std=c++23 -MD -MT a -MF b -o c -c /some/where/tools/itch_oracle.cpp",
        }]), encoding="utf-8")
        self.assertEqual(report.tool_flags(commands), "-O3 -DNDEBUG -std=c++23")


if __name__ == "__main__":
    unittest.main()
