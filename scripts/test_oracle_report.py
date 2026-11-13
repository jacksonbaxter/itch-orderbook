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
    "unpacked_bytes": 120,
    "messages": 4,
    "per_type_counts": {"A": 2, "U": 1, "S": 1},
}

GOOD_LOG = """\
run_commit abc1234 (clean)
run_compiler clang version 23
run_flags -O3 -DNDEBUG
run_host Darwin arm64
run_command itch_oracle data/day.bin
oracle_index hash
oracle_levels linked
oracle_hash_every 2
oracle_exhaustive 0
bytes 120
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
move_reports 5
moved_checks 1
empty_moved_checks 0
order_slot_checks 7
level_slot_checks 12
slot_violations 0
index_checks 4
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
book_index hash
book_levels linked
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

        self.arms = []
        self.day_name = "day"

    def run_report(self):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"days": {self.day_name: self.day}}), encoding="utf-8")
        log = self.root / "log"
        log.write_text(self.log, encoding="utf-8")
        alloc = self.root / "alloc"
        alloc.write_text(self.alloc, encoding="utf-8")
        arm_args = []
        for number, (arm_log, arm_alloc) in enumerate(self.arms):
            paths = (self.root / f"arm_log_{number}", self.root / f"arm_alloc_{number}")
            paths[0].write_text(arm_log, encoding="utf-8")
            paths[1].write_text(arm_alloc, encoding="utf-8")
            arm_args += ["--arm-log", str(paths[0]), str(paths[1])]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = report.main(["--day", self.day_name, "--log", str(log), "--alloc-log", str(alloc),
                                *arm_args, "--manifest", str(manifest), "--out",
                                str(self.root / "out.md")])
        return code, out.getvalue()

    def other_arm(self, index="paged", levels="flat"):
        """The good logs as another arm, which moved other levels and took longer."""
        log = self.log.replace("oracle_index hash", f"oracle_index {index}")
        log = log.replace("oracle_levels linked", f"oracle_levels {levels}")
        log = log.replace("move_reports 5", "move_reports 9").replace("moved_checks 1",
                                                                      "moved_checks 4")
        log = log.replace("elapsed_seconds 1.5", "elapsed_seconds 2.5")
        log = log.replace("run_command itch_oracle data/day.bin",
                          f"run_command itch_oracle data/day.bin --index {index} --levels {levels}")
        alloc = self.alloc.replace("book_index hash", f"book_index {index}")
        alloc = alloc.replace("book_levels linked", f"book_levels {levels}")
        return log, alloc

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

    def test_real_time_is_shown_beside_the_replay(self):
        self.run_report()
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("- Elapsed: 1.5 s in the replay, 9.0 s real, 8.0 s user", written)
        self.assertIn("- Peak memory footprint: 2.00 GiB\n", written)

    def test_a_real_time_shorter_than_the_replay_is_not_shown_as_the_runs(self):
        # macOS's time read its clock and the footprint at the first SIGSTOP.
        self.log = self.log.replace("elapsed_seconds 1.5", "elapsed_seconds 38.8")
        self.run_report()
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("- Elapsed: 38.8 s in the replay, wall time with the pause included, 8.0 s "
                      "user. /usr/bin/time's real, 9.0 s, is shorter than the replay", written)
        self.assertNotIn("9.0 s real", written)
        self.assertIn("- Peak memory footprint: 2.00 GiB to the first stop", written)
        self.assertIn("| 2.00 to the first stop |", written)

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
        self.log = self.log.replace("t_inv2_crossed 0", "t_inv2_crossed 5")
        code, output = self.run_report()
        self.assertEqual(code, 3, output)
        self.assertIn("T-INV-2 FAIL", output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("Verdict, strict: **FAIL**", written)

    def test_a_leftover_only_day_keeps_the_t_inv_2_code_and_passes_t_inv_3(self):
        self.log = self.log.replace("verdict T-INV-2 PASS crossed=0 locked=0",
                                    "verdict T-INV-2 FAIL crossed=5 locked=1")
        self.log = self.log.replace("t_inv2_crossed 0\nt_inv2_locked 0",
                                    "t_inv2_crossed 5\nt_inv2_locked 1")
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
        self.log = self.log.replace("t_inv2_crossed 0", "t_inv2_crossed 1")
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

    def test_published_figures_are_shown_on_their_own_day_only(self):
        code, _ = self.run_report()
        text = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("None. The figures this file could set beside ours were published for "
                      "12302019 only.", text)
        self.assertNotIn("| book messages, crossed | [carteret]", text)
        self.day_name = report.COMPARISON_DAY
        code, _ = self.run_report()
        text = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("| book messages, crossed | [carteret](https://github.com/EkanshSingh401/"
                      "carteret/blob/3df0e8757c979731b07a991de4ec7e81e81c8c08/docs/design.md), "
                      "design.md record 036, Evidence, row Total | 8,580 |", text)
        self.assertNotIn("None. The figures", text)

    def test_a_log_with_a_missing_counter_cannot_be_used(self):
        self.log = self.log.replace("bbo_checks 8\n", "")
        code, _ = self.run_report()
        self.assertEqual(code, 2)

    def test_every_arm_is_shown_and_may_differ_only_in_its_own_lines(self):
        self.arms = [self.other_arm("hash", "flat"), self.other_arm("paged", "flat")]
        code, output = self.run_report()
        self.assertEqual(code, 0, output)
        self.assertIn("the same on 3 arms", output)
        written = (self.root / "out.md").read_text(encoding="utf-8")
        self.assertIn("## Every arm of the fast book", written)
        self.assertIn("| `hash_linked` | PASS | PASS | PASS, 0 crossed and 0 locked | PASS | PASS | "
                      "4 | `00ff` | 5 | 1 | 1.5 | 1.00 | 2.00 | `log` |", written)
        self.assertIn("| `paged_flat` | PASS | PASS | PASS, 0 crossed and 0 locked | PASS | PASS | "
                      "4 | `00ff` | 9 | 4 | 2.5 | 1.00 | 2.00 | `arm_log_1` |", written)

    def test_an_arm_that_ends_on_another_hash_fails(self):
        log, alloc = self.other_arm()
        self.arms = [(log.replace("last_hash 00ff", "last_hash 00fe"), alloc)]
        self.assert_fails("arm paged_flat disagrees with hash_linked on last_hash")

    def test_an_arm_with_another_crossed_count_fails(self):
        log, alloc = self.other_arm()
        self.arms = [(log.replace("t_inv2_tally type=U scope=all checks=1 two_sided=1 crossed=0",
                                  "t_inv2_tally type=U scope=all checks=1 two_sided=1 crossed=1"),
                      alloc)]
        self.assert_fails("arm paged_flat disagrees with hash_linked on tallies")

    def test_an_arm_is_held_to_the_hard_checks_on_its_own(self):
        log, alloc = self.other_arm()
        self.arms = [(log.replace("bbo_checks 8", "bbo_checks 4"), alloc)]
        self.assert_fails("arm paged_flat: bbo_checks is not two per message")
        log, alloc = self.other_arm()
        self.arms = [(log, alloc.replace("verdict T-ALLOC-1 PASS", "verdict T-ALLOC-1 FAIL"))]
        self.assert_fails("arm paged_flat: T-ALLOC-1 verdict is not PASS")

    def test_an_arms_allocation_count_must_agree(self):
        log, alloc = self.other_arm()
        self.arms = [(log, alloc.replace("control_allocations 9", "control_allocations 8"))]
        self.assert_fails("arm paged_flat disagrees with hash_linked on allocation "
                          "control_allocations")

    def test_an_allocation_log_from_another_arm_fails(self):
        log, _ = self.other_arm()
        self.arms = [(log, self.alloc)]
        self.assert_fails("arm paged_flat: its allocation log ran another arm")

    def test_an_arm_given_twice_fails(self):
        self.arms = [self.other_arm(), self.other_arm()]
        self.assert_fails("arm paged_flat is given twice")

    def test_a_log_that_does_not_name_its_arm_cannot_be_used(self):
        self.log = self.log.replace("oracle_index hash\n", "")
        code, _ = self.run_report()
        self.assertEqual(code, 2)

    def written(self):
        return (self.root / "out.md").read_text(encoding="utf-8")

    def test_a_hard_failure_is_written_as_one(self):
        # review r10-1: the file printed the tool's PASS beside the report's own FAIL.
        self.log = self.log.replace("write_set_violations 0", "write_set_violations 5")
        self.assert_fails("write-set violations")
        written = self.written()
        self.assertIn("## What failed\n", written)
        self.assertIn("\n- write-set violations\n", written)
        self.assertIn("## T-ORACLE-1: the reference book agrees after every message\n\n"
                      "Verdict: **FAIL**", written)
        self.assertIn("| `hash_linked` | FAIL | PASS | PASS, 0 crossed and 0 locked | PASS | PASS |",
                      written)
        self.assertNotIn("Verdict: **PASS**\n\n| Check | Count |", written)

    def test_a_clean_day_has_no_failure_section(self):
        self.run_report()
        self.assertNotIn("What failed", self.written())

    def test_another_arms_failure_marks_that_arm_only(self):
        log, alloc = self.other_arm()
        self.arms = [(log.replace("divergences 0", "divergences 3"), alloc)]
        self.assert_fails("arm paged_flat: divergences")
        written = self.written()
        self.assertIn("- arm paged_flat: divergences", written)
        self.assertIn("| `paged_flat` | FAIL |", written)
        self.assertIn("| `hash_linked` | PASS |", written)

    def test_arms_that_disagree_are_not_said_to_agree(self):
        log, alloc = self.other_arm()
        self.arms = [(log.replace("peak_live_orders 2", "peak_live_orders 3"), alloc)]
        self.assert_fails("arm paged_flat disagrees with hash_linked on peak_live_orders")
        written = self.written()
        self.assertNotIn("Every line its logs print agrees", written)
        self.assertIn("The arms do not all agree with `hash_linked`", written)
        self.assertIn("- arm paged_flat disagrees with hash_linked on peak_live_orders", written)
        self.arms = [self.other_arm()]
        self.run_report()
        self.assertIn("Every line its logs print agrees with `hash_linked`'s", self.written())

    def test_t_inv_2_is_held_to_its_counts(self):
        # review r10-3: a PASS word over a crossed day.
        self.log = self.log.replace("t_inv2_crossed 0", "t_inv2_crossed 18958")
        self.assert_fails("T-INV-2 verdict is PASS, and 18958 crossed and 0 locked make it FAIL")
        self.assertIn("Verdict, strict: **FAIL**", self.written())
        self.log = GOOD_LOG.replace("t_inv2_locked 0", "t_inv2_locked 1")
        self.assert_fails("make it FAIL")
        self.log = GOOD_LOG.replace("verdict T-INV-2 PASS", "verdict T-INV-2 FAIL")
        self.assert_fails("T-INV-2 verdict is FAIL, and 0 crossed and 0 locked make it PASS")

    def test_t_alloc_1_is_held_to_its_count(self):
        # review r10-3: a PASS word over seven allocations.
        self.alloc = self.alloc.replace("fast_allocations 0", "fast_allocations 7")
        self.assert_fails("T-ALLOC-1 verdict is PASS, and 7 fast-book allocations make it FAIL")
        written = self.written()
        self.assertIn("## T-ALLOC-1: no allocation on the hot path\n\nVerdict: **FAIL**", written)
        self.assertIn("| Fast book | 4 | 7 |", written)

    def test_gnu_time_figures_are_read(self):
        # review r10-4: check_real_day.sh runs GNU time -v on Linux.
        gnu = ("\tUser time (seconds): 8.25\n"
               "\tSystem time (seconds): 1.00\n"
               "\tElapsed (wall clock) time (h:mm:ss or m:ss): 1:02.50\n"
               "\tMaximum resident set size (kbytes): 1048576\n")
        self.log = self.log.replace("        9.00 real         8.00 user         1.00 sys\n"
                                    "          1073741824  maximum resident set size\n"
                                    "          2147483648  peak memory footprint\n", gnu)
        code, output = self.run_report()
        self.assertEqual(code, 0, output)
        written = self.written()
        self.assertIn("- Elapsed: 1.5 s in the replay, 62.5 s real, 8.2 s user", written)
        self.assertIn("- Maximum RSS: 1.00 GiB, which includes the mapped file", written)
        self.assertIn("- Peak memory footprint: unavailable\n", written)
        self.assertEqual(report.wall_clock("1:02:03.5"), 3723.5)

    def test_absent_time_figures_are_unavailable_never_zero(self):
        self.log = self.log.split("        9.00 real")[0]
        self.arms = [self.other_arm()]
        code, output = self.run_report()
        self.assertEqual(code, 0, output)
        written = self.written()
        self.assertIn("- Elapsed: 1.5 s in the replay, unavailable real, unavailable user",
                      written)
        self.assertIn("- Maximum RSS: unavailable, which includes the mapped file", written)
        self.assertIn("- Peak memory footprint: unavailable\n", written)
        self.assertIn("| 1.5 | unavailable | unavailable | `log` |", written)
        self.assertNotIn("0.00", written)

    def test_a_log_of_another_file_fails(self):
        # review r10-7: the header names the pinned file, so the log must have replayed it.
        self.log = self.log.replace("bytes 120", "bytes 1234")
        self.assert_fails("bytes 1234, manifest 120: not the pinned file")

    def test_a_slot_violation_fails(self):
        self.log = self.log.replace("slot_violations 0", "slot_violations 1")
        self.assert_fails("slot violations")

    def test_each_named_reference_must_be_looked_up(self):
        self.log = self.log.replace("index_checks 4", "index_checks 3")
        self.assert_fails("index_checks 3, the manifest's messages name 4")

    def test_slot_checks_must_cover_the_writes(self):
        self.log = self.log.replace("level_slot_checks 12", "level_slot_checks 7")
        self.assert_fails("fewer level slots checked than choke-point writes")
        self.log = GOOD_LOG.replace("order_slot_checks 7", "order_slot_checks 4")
        self.assert_fails("order_slot_checks 4, fewer than the 5")

    def test_a_flat_arm_checks_each_block_against_the_arena(self):
        log, alloc = self.other_arm()
        flat = log.replace("empty_moved_checks 0", "empty_moved_checks 0\nlevel_relocations 3")
        self.arms = [(flat.replace("index_checks 4", "index_checks 4\narena_checks 3"), alloc)]
        self.assert_fails("arm paged_flat: fewer arena checks than first blocks and relocations")
        self.arms = [(flat.replace("index_checks 4", "index_checks 4\narena_checks 5"), alloc)]
        code, output = self.run_report()
        self.assertEqual(code, 0, output)
        self.arms = [(flat, alloc)]
        code, _ = self.run_report()
        self.assertEqual(code, 2)

    def test_the_slot_and_index_counts_are_shown(self):
        self.run_report()
        written = self.written()
        self.assertIn("| Order slots checked as a write went through them | 7 |", written)
        self.assertIn("| Level slots checked as a write went through them | 12 |", written)
        self.assertIn("| Slot violations | 0 |", written)
        self.assertIn("| References named, each looked up in both books | 4 |", written)

    def test_each_published_figure_names_its_source(self):
        # review r10-9 and D5.
        self.day_name = report.COMPARISON_DAY
        self.run_report()
        written = self.written()
        design = ("[carteret](https://github.com/EkanshSingh401/carteret/blob/"
                  "3df0e8757c979731b07a991de4ec7e81e81c8c08/docs/design.md), design.md record "
                  "036, Evidence")
        self.assertIn(f"| not in state T, locked | {design}, row Symbol not in state `T` | 62 |",
                      written)
        self.assertIn(f"| reopening window, locked | {design}, row Inside a reopening window | 8 |",
                      written)
        self.assertIn(f"| inside 09:30 to 16:00, locked | {design}, row In continuous trading | "
                      "70 |", written)
        self.assertIn(f"| reopening windows | {design}, the line under the table | 33 |", written)
        self.assertIn("| market open and T, checks | [Ironbook]"
                      "(https://github.com/Shaan50/nasdaq-itch-matching-engine/blob/"
                      "e99c55c8f219b1c297761b4ec33f48d90ac6f5e5/bench/results/"
                      "full_day_validation.txt), full_day_validation.txt, line crossed-book "
                      "checks (continuous) | 136,896,897 |", written)
        self.assertIn("[BENCHMARKS.md's T-INV-2 entry]"
                      "(../BENCHMARKS.md#t-inv-2-the-book-is-never-crossed)", written)

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
