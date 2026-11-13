#!/usr/bin/env python3
"""parse_driver.py: the parse rate's figures, its file, its check and its run loop.

The files are rendered by the driver from made-up bench_parse records of 12302019's counts,
stamped as stamp_load.py stamps a debian run, and checked by check_results.py, which hands a
parse file to the driver's own check. The expected rates are worked out by hand from the
day's 268,744,780 messages and 8,251,407,909 bytes, not by the driver. The run loop runs with
a stand-in for the sampler and for bench_parse, so it needs no Linux host.
"""

import argparse
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402
import parse_driver  # noqa: E402
import stamp_load  # noqa: E402
import test_linux_sampler  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = REPO_ROOT / "build" / "parse_driver_test"
MESSAGES = 268_744_780
BYTES = 8_251_407_909
# The day's messages of S, R, A, F, E, C, X, D, U and P.
TEN = (6 + 8_906 + 117_145_568 + 1_485_888 + 5_722_824 + 99_917 + 2_787_676 + 114_360_997 +
       21_639_067 + 1_218_602)
LOOPS = ["frame_walk: 20 instructions, no call and no jump to another function",
         "book_decode: 90 instructions, no call and no jump to another function",
         "strict_decode: 400 instructions, no call and no jump to another function"]
MACHINE = "machine_check: pass input-resident: 1 of 1 pages\nmachine_check: passed"
HEADER = dict(test_linux_sampler.DEBIAN, dataset="12302019.NASDAQ_ITCH50",
              counters="unavailable: bench_parse reads no counters",
              rdpmc="unavailable: bench_parse reads no counters",
              page_size="4096 bytes, base pages", residency="2014504 of 2014504 pages resident")
# Each pass's fastest second; its 15 passes take that and every 0.01 s more up to 0.14 s more,
# so the median pass takes 0.07 s more than the fastest.
FASTEST = {"framing": 0.80, "decode": 2.40, "strict": 4.00}
ORDER = ("framing", "decode", "strict")


def totals(name):
    decoded = {"framing": 0, "decode": TEN, "strict": MESSAGES}[name]
    return {"messages": MESSAGES, "decoded": decoded, "rejected": 0,
            "fold": {"framing": 0, "decode": 123, "strict": 456}[name], "stopped_at": BYTES}


def record(repetition, rounds=5):
    passes = []
    for round_index in range(rounds):
        for i in range(3):
            name = ORDER[(round_index + i) % 3]
            step = (repetition - 1) * rounds + round_index
            passes.append({"pass": name, "target": "", "round": round_index + 1, "ticks": 1,
                           "seconds": round(FASTEST[name] + 0.01 * step, 9),
                           "totals": totals(name)})
    return {"header": dict(HEADER), "input": "ram: read into anonymous memory", "bytes": BYTES,
            "rounds": rounds,
            "reference": {"frames": MESSAGES, "messages": MESSAGES, "unknown_type": 0,
                          "bad_length": 0, "zero_length": 0, "decode_types": TEN,
                          "consumed": BYTES},
            "warmup": {name: totals(name) for name in ORDER}, "passes": passes,
            "pinned": True, "migrations": 0, "peak_rss_bytes": 8_900_000_000, "refusals": []}


def args_for(**changes):
    values = {"prediction": "P-0029", "rounds": 5, "repetitions": 3, "wrapped_by": None,
              "core": 4, "sibling": 5}
    values.update(changes)
    return argparse.Namespace(**values)


def sampled():
    return {"every": 1.0, "cpu": 0, "published": test_linux_sampler.published(),
            "withdrawn": []}


def rendered(records=None):
    records = records or [record(index) for index in (1, 2, 3)]
    text = parse_driver.render(records, args_for(), "P-0029-x", *test_linux_sampler.RULE_DAY,
                               MACHINE, "python3 -B scripts/parse_driver.py", sampled(), LOOPS)
    return stamp_load.stamp(text, test_linux_sampler.stderr_for(*test_linux_sampler.RULE_DAY))


def rows(text, title):
    return (check_results.section(text, title) or "").splitlines()


class Figures(unittest.TestCase):
    def test_the_file_passes_both_checks(self):
        text = rendered()
        self.assertEqual(check_results.check_text(text), [])
        self.assertEqual(parse_driver.check_text(text), [])

    def test_the_summary_is_the_median_pass(self):
        summary = rows(rendered(), "Summary")
        self.assertIn("| T-PARSE-1, the framing walk, messages/s | 308,902,046 | 285,898,702 | "
                      "335,930,975 | 5.16% |", summary)
        self.assertIn("| T-PARSE-1, the framing walk, GB/s | 9.484 | 8.778 | 10.314 | 5.16% |",
                      summary)
        self.assertIn("| T-PARSE-2, itch-book's decode of its ten types, messages/s | "
                      "108,803,555 | 105,805,031 | 111,976,992 | 1.81% |", summary)
        self.assertIn("| T-PARSE-3, the strict decode of every field, GB/s | 2.027 | 1.993 | "
                      "2.063 | 1.10% |", summary)

    def test_the_best_pass_is_the_fastest(self):
        best = rows(rendered(), "Best pass")
        self.assertIn("| Target | Best of 15 passes, messages/s | Best of 15 passes, GB/s |", best)
        self.assertIn("| T-PARSE-1 | 335,930,975 | 10.314 |", best)
        self.assertIn("| T-PARSE-2 | 111,976,992 | 3.438 |", best)
        self.assertIn("| T-PARSE-3 | 67,186,195 | 2.063 |", best)

    def test_every_pass_is_counted_over_every_frame_and_the_whole_file(self):
        passes = rows(rendered(), "Every timed pass")
        self.assertIn("| 1 | 1 | framing | 0.800000000 | 268,744,780 | 335,930,975 | 10.314 | 0 |",
                      passes)
        self.assertIn("| 3 | 5 | strict | 4.140000000 | 268,744,780 | 64,914,198 | 1.993 | 456 |",
                      passes)

    def test_the_verdicts(self):
        targets = rows(rendered(), "Targets")
        self.assertIn("| T-PARSE-1 | ≥ 709 M msg/s and 21.8 GB/s | 308.90 M msg/s, 9.484 GB/s, "
                      "the median | missed: 400.10 M msg/s and 12.32 GB/s under the bar |",
                      targets)
        strict = next(row for row in targets if row.startswith("| T-PARSE-3 |"))
        self.assertIn("on 10302019 |", strict)
        self.assertIn("measured on 12302019, not the bar's 10302019", strict)
        self.assertEqual(parse_driver.verdict("T-PARSE-2", 200e6, 6.2, 199.0, 6.1, "12302019"),
                         "met")
        self.assertEqual(parse_driver.verdict("T-PARSE-2", 200e6, 6.0, 199.0, 6.1, "12302019"),
                         "missed: 0.10 GB/s under the bar")
        # A miss too small for two decimals is still a miss, and never printed as 0.00.
        self.assertEqual(parse_driver.verdict("T-PARSE-1", 708.999e6, 22.0, 709.0, 21.8,
                                              "12302019"),
                         "missed: 0.001 M msg/s under the bar")

    def test_the_caveats_set_the_two_days_side_by_side(self):
        caveats = rows(rendered(), "Caveats")
        self.assertIn("| | 10302019, the bar's day | 12302019, measured |", caveats)
        self.assertIn("| Bytes | 9,035,016,843 | 8,251,407,909 |", caveats)
        self.assertIn("| Messages | 293,989,079 | 268,744,780 |", caveats)
        self.assertIn("| Bytes a message | 30.732 | 30.704 |", caveats)
        self.assertIn("| A | 127,214,649, 43.272% | 117,145,568, 43.590% |", caveats)
        self.assertIn("| B | 1, 0.000% | 0, 0.000% |", caveats)
        self.assertIn("- " + LOOPS[0], caveats)
        text = rendered()
        self.assertIn("compared under Caveats", text)

    def test_the_header_says_what_was_counted(self):
        fields = check_results.parse_header(rendered())
        self.assertEqual(fields["bytes"], "8,251,407,909, the whole file, length prefixes included")
        self.assertEqual(fields["timed_messages"], "268,744,780")
        self.assertEqual(fields["repetitions"], "3")
        self.assertEqual(fields["rounds"], "5")


class Check(unittest.TestCase):
    def codes(self, text):
        return {code for code, _ in parse_driver.check_text(text)}

    def test_a_summary_figure_that_is_not_the_passes(self):
        text = rendered().replace("| 9.484 | 8.778 |", "| 9.500 | 8.778 |")
        self.assertIn("parse-figure-differs", self.codes(text))
        self.assertIn("parse-figure-differs", {c for c, _ in check_results.check_text(text)})

    def test_a_pass_whose_seconds_changed(self):
        text = rendered().replace("| 1 | 1 | framing | 0.800000000 |", "| 1 | 1 | framing | "
                                  "0.700000000 |")
        self.assertIn("parse-figure-differs", self.codes(text))

    def test_a_pass_dropped(self):
        text = rendered()
        row = next(line for line in rows(text, "Every timed pass") if "| decode |" in line)
        self.assertIn("parse-passes-count", self.codes(text.replace(row + "\n", "")))

    def test_passes_that_disagree_on_their_fold(self):
        text = rendered().replace("| 1.993 | 456 |", "| 1.993 | 457 |")
        self.assertIn("parse-passes-disagree", self.codes(text))

    def test_a_verdict_changed(self):
        text = rendered().replace("missed: 400.10 M msg/s", "missed: 400.00 M msg/s")
        self.assertIn("parse-figure-differs", self.codes(text))

    def test_rates_over_fewer_messages_than_every_frame(self):
        # The framing passes over no message at all, and the decode over the messages it
        # decoded: the file's own figures agree with its table, but not with the frame count.
        records = [record(index) for index in (1, 2, 3)]
        for each in records:
            for p in each["passes"]:
                p["totals"] = dict(p["totals"], messages=p["totals"]["decoded"])
        text = rendered(records)
        self.assertIn("parse-messages-not-frames", self.codes(text))
        self.assertIn("parse-messages-not-frames", {c for c, _ in check_results.check_text(text)})

    def test_a_frame_count_or_size_that_is_not_the_manifests(self):
        records = [record(index) for index in (1, 2, 3)]
        for each in records:
            each["bytes"] += 1
            each["reference"]["frames"] += 1
            for p in each["passes"]:
                p["totals"] = dict(p["totals"], messages=MESSAGES + 1)
        found = parse_driver.check_text(rendered(records))
        self.assertEqual({code for code, _ in found}, {"parse-not-the-manifest"})
        self.assertEqual(len(found), 2)

    def test_a_day_the_manifest_does_not_pin(self):
        text = rendered().replace("- dataset: 12302019.NASDAQ_ITCH50", "- dataset: other.itch")
        self.assertIn("parse-day-unpinned", self.codes(text))

    def test_an_inline_check_line_dropped(self):
        text = rendered().replace("- " + LOOPS[1] + "\n", "")
        self.assertIn("parse-loops-missing", self.codes(text))
        self.assertIn("parse-loops-missing", {c for c, _ in check_results.check_text(text)})

    def test_the_caveats_prose_changed(self):
        text = rendered().replace("every page of it was found resident before and after each "
                                  "timed pass", "no timed pass takes a page fault")
        self.assertNotEqual(text, rendered())
        self.assertIn("parse-figure-differs", self.codes(text))

    def test_a_caveat_figure_changed(self):
        text = rendered().replace("| Bytes a message | 30.732 |", "| Bytes a message | 30.704 |")
        self.assertIn("parse-figure-differs", self.codes(text))

    def test_a_driver_bar_that_is_not_benchmarks_md(self):
        text = (REPO_ROOT / "BENCHMARKS.md").read_text(encoding="utf-8")
        passes = tuple((name, target, what, 70.9 if target == "T-PARSE-1" else msgs, gbs)
                       for name, target, what, msgs, gbs in parse_driver.PASSES)
        with mock.patch.object(parse_driver, "PASSES", passes):
            found = [target for target, _ in check_results.bar_disagreements(text)]
        self.assertEqual(found, ["T-PARSE-1"])


# objdump -d --no-show-raw-insn, cut down: GNU objdump on x86-64, llvm-objdump on arm64.
X86 = """
0000000000401200 <_ZN4itch5bench5parse10frame_walkESt4spanIKSt4byteLm18446744073709551615EE>:
  401200:\tendbr64
  401204:\tmov    %rsi,%rax
  401207:\tjmp    401220 <_ZN4itch5bench5parse10frame_walkESt4spanIKSt4byteLm18446744073709551615EE+0x20>
  401209:\tnotrack jmp *%rax
  40120c:\tlea    0xe2d(%rip),%rdx        # 402040 <_ZN4itch12kLengthByTypeE>
  401213:\tret

0000000000401300 <_ZN4itch5bench5parse11book_decodeESt4spanIKSt4byteLm18446744073709551615EE>:
  401300:\tpush   %rbx
  401301:\tcall   401500 <_ZZN4itch5bench5parse11book_decodeEvENKUlcPKSt4byteE_clEcS4_>
  401306:\tret

0000000000401400 <_ZN4itch5bench5parse13strict_decodeESt4spanIKSt4byteLm18446744073709551615EE>:
  401400:\tje     401600 <_ZN4itch6decodeESt4spanIKSt4byteLm18446744073709551615EE>
  401406:\tret
"""
ARM64 = """
0000000100003000 <__ZN4itch5bench5parse10frame_walkENSt3__14spanIKSt4byteLm18446744073709551615EEE>:
100003000: \tldrh\tw8, [x0]
100003004: \tb.ne\t0x100003000 <__ZN4itch5bench5parse10frame_walkENSt3__14spanIKSt4byteLm18446744073709551615EEE>
100003008: \tbr\tx9
10000300c: \tret
0000000100003100 <__ZN4itch5bench5parse11book_decodeENSt3__14spanIKSt4byteLm18446744073709551615EEE>:
100003100: \tadd\tx8, x8, #1
100003104: \tret
0000000100003200 <__ZN4itch5bench5parse13strict_decodeENSt3__14spanIKSt4byteLm18446744073709551615EEE>:
100003200: \tblr\tx8
100003204: \tret
"""


class Inline(unittest.TestCase):
    def test_a_pass_with_no_call_passes(self):
        lines, problems = parse_driver.inline_verdicts(X86)
        self.assertEqual(lines, ["frame_walk: 6 instructions, no call and no jump to another "
                                 "function"])
        self.assertEqual(len(problems), 2)

    def test_a_call_in_a_pass_is_refused(self):
        _, problems = parse_driver.inline_verdicts(X86)
        self.assertTrue(any(p.startswith("book_decode: 1 instructions leave the pass: 401301:")
                            for p in problems), problems)

    def test_a_jump_to_another_function_is_refused(self):
        _, problems = parse_driver.inline_verdicts(X86)
        self.assertTrue(any(p.startswith("strict_decode: 1 instructions leave the pass: 401400:")
                            for p in problems), problems)

    def test_arm64_calls_and_branches(self):
        lines, problems = parse_driver.inline_verdicts(ARM64)
        self.assertEqual([line.split(":")[0] for line in lines], ["frame_walk", "book_decode"])
        self.assertEqual([p.split(":")[0] for p in problems], ["strict_decode"])

    def test_a_pass_that_is_not_a_function_of_its_own_is_refused(self):
        cut = X86[:X86.index("0000000000401300")]
        _, problems = parse_driver.inline_verdicts(cut)
        self.assertIn("book_decode: not in the binary as a function of its own", problems)

    def test_the_kept_code_is_the_three_passes(self):
        code = parse_driver.pass_code(X86)
        self.assertIn("401301:", code)
        self.assertNotIn("<_ZN4itch12kLengthByTypeE>:", code)


class Run(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.log = SCRATCH / "run_log.md"
        self.log.unlink(missing_ok=True)
        self.day = {"unpacked_bytes": BYTES, "messages": MESSAGES,
                    "per_type_counts": {"S": 6, "R": 8_906, "A": 117_145_568, "F": 1_485_888,
                                        "E": 5_722_824, "C": 99_917, "X": 2_787_676,
                                        "D": 114_360_997, "U": 21_639_067, "P": 1_218_602,
                                        "Q": 17_836}}

    def test_a_good_record_is_accepted(self):
        parse_driver.validate_run(record(1), args_for(), self.day)

    def test_a_count_the_manifest_does_not_pin_is_refused(self):
        bad = record(1)
        bad["reference"]["decode_types"] -= 1
        with self.assertRaises(parse_driver.Refused):
            parse_driver.validate_run(bad, args_for(), self.day)

    def test_a_pass_that_is_not_its_warmup_is_refused(self):
        bad = record(1)
        bad["passes"][4]["totals"] = dict(bad["passes"][4]["totals"], fold=1)
        with self.assertRaises(parse_driver.Refused):
            parse_driver.validate_run(bad, args_for(), self.day)

    def test_a_record_with_too_few_passes_is_refused(self):
        with self.assertRaises(parse_driver.Refused):
            parse_driver.validate_run(record(1, rounds=4), args_for(), self.day)

    def test_repetitions_that_disagree_are_refused(self):
        other = record(2)
        other["warmup"]["strict"] = dict(other["warmup"]["strict"], fold=7)
        with self.assertRaises(parse_driver.Refused):
            parse_driver.validate_together([record(1), other])

    def test_the_ten_types_are_itch_books(self):
        self.assertEqual(sorted(parse_driver.TEN_TYPES), sorted("SRAFECXDUP"))

    def test_a_day_the_manifest_does_not_pin_is_refused_before_anything_runs(self):
        with mock.patch.object(parse_driver.platform, "system", return_value="Linux"), \
                mock.patch.object(parse_driver, "inline_check") as inline:
            with self.assertRaises(parse_driver.Refused) as caught:
                parse_driver.run(args_for(input=Path("other.itch")), "P-0029-x", "now", "abc")
        self.assertEqual(caught.exception.stage, "manifest")
        inline.assert_not_called()

    def test_a_call_in_a_timed_loop_is_refused_before_anything_runs(self):
        with mock.patch.object(parse_driver.platform, "system", return_value="Linux"), \
                mock.patch.object(parse_driver, "disassemble", return_value=X86), \
                mock.patch.object(parse_driver.bench_driver, "hash_and_fault_in") as hashed:
            with self.assertRaises(parse_driver.Refused) as caught:
                parse_driver.run(args_for(input=Path("12302019.NASDAQ_ITCH50"), bench="b"),
                                 "P-0029-x", "now", "abc")
        self.assertEqual(caught.exception.stage, "inline")
        hashed.assert_not_called()

    def test_another_host_is_refused_before_anything_runs(self):
        with mock.patch.object(parse_driver.platform, "system", return_value="Darwin"):
            with self.assertRaises(parse_driver.Refused) as caught:
                parse_driver.run(args_for(), "P-0029-x", "now", "abc (clean)")
        self.assertEqual(caught.exception.stage, "host")

    def test_a_process_the_rule_refuses_is_withdrawn_and_run_again(self):
        watch = mock.Mock(every=1.0)
        watch.lines.return_value = []
        verdicts = iter(["the sibling was 40% busy", None])
        args = args_for(input=SCRATCH / "day", raw_dir=SCRATCH, run_log=self.log,
                        quiet_before=0, most_withdrawn=20, most_in_a_row=3)
        sampled = {"every": 1.0, "cpu": 0, "published": [], "withdrawn": []}
        with mock.patch.object(parse_driver, "fault_in"), \
                mock.patch.object(parse_driver, "run_process", return_value=record(1)), \
                mock.patch.object(parse_driver.linux_sampler, "judge",
                                  side_effect=lambda *a: next(verdicts)), \
                mock.patch.object(parse_driver.linux_sampler, "summary", return_value="quiet"):
            got = parse_driver.judged_process(args, 1, "abc", "00", watch, sampled, self.day)
        self.assertEqual(got, record(1))
        self.assertEqual(len(sampled["withdrawn"]), 1)
        self.assertEqual(len(sampled["published"]), 1)
        self.assertIn("withdrawn: T-PARSE-1 to T-PARSE-3, repetition 1",
                      self.log.read_text(encoding="utf-8"))

    def test_three_withdrawn_in_a_row_refuse_the_run(self):
        watch = mock.Mock(every=1.0)
        watch.lines.return_value = []
        args = args_for(input=SCRATCH / "day", raw_dir=SCRATCH, run_log=self.log,
                        quiet_before=0, most_withdrawn=20, most_in_a_row=3)
        sampled = {"every": 1.0, "cpu": 0, "published": [], "withdrawn": []}
        with mock.patch.object(parse_driver, "fault_in"), \
                mock.patch.object(parse_driver, "run_process", return_value=record(1)), \
                mock.patch.object(parse_driver.linux_sampler, "judge", return_value="busy"):
            with self.assertRaises(parse_driver.Refused):
                parse_driver.judged_process(args, 1, "abc", "00", watch, sampled, self.day)
        self.assertEqual(len(sampled["withdrawn"]), 3)

    def test_the_command_shown_carries_the_core_and_its_sibling(self):
        args = args_for(bench=REPO_ROOT / "build" / "bench_parse", probe=Path("/x/bench_probe"),
                        input=Path("/x/12302019.NASDAQ_ITCH50"),
                        out=REPO_ROOT / "results" / "parse_rate_debian_12302019.md",
                        sampler_cpu=None, quiet_before=parse_driver.QUIET_BEFORE_S,
                        quiet_wait=parse_driver.QUIET_WAIT_S,
                        most_in_a_row=parse_driver.MOST_IN_A_ROW,
                        most_withdrawn=parse_driver.MOST_WITHDRAWN)
        self.assertEqual(parse_driver.relative_command(args),
                         "python3 -B scripts/parse_driver.py --bench build/bench_parse --probe "
                         "<raw>/bench_probe --input <raw>/12302019.NASDAQ_ITCH50 --prediction "
                         "P-0029 --repetitions 3 --rounds 5 --raw-dir <raw> --out "
                         "results/parse_rate_debian_12302019.md --core 4 --sibling 5")

    def test_two_repetitions_are_not_enough(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            parse_driver.parse_args(["--bench", "b", "--probe", "p", "--input", "i",
                                     "--prediction", "P-0029", "--raw-dir", "r", "--out", "o",
                                     "--repetitions", "2"])


if __name__ == "__main__":
    unittest.main()
