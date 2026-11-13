#!/usr/bin/env python3
"""The Linux sampler rule: linux_sampler.py's sampler on a made-up /proc, its judge on made-up
logs, and how backtest_driver.py, stamp_load.py and check_results.py hold a debian file to it.

The sampler cases build a /proc tree in a scratch folder, change its counters between two
samples and read what the sampler wrote. The judge cases write logs by hand, one broken thing
each. The last class starts the real sampler beside a busy child, so it needs Linux.
"""

import argparse
import datetime
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest_driver  # noqa: E402
import check_results  # noqa: E402
import linux_sampler  # noqa: E402
import stamp_load  # noqa: E402
import test_backtest_results  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
UID = 1000
CORE, SIBLING = 4, 5
T0 = datetime.datetime(2026, 11, 13, 4, 0, 0, tzinfo=datetime.UTC)


def stamp(seconds):
    return linux_sampler.stamp_of(T0 + datetime.timedelta(seconds=seconds))


class FakeProc:
    """A /proc tree: /proc/stat, and a stat, status, cmdline, links and threads per process."""

    def __init__(self, root):
        self.root = root
        root.mkdir()

    def cpus(self, busy):
        """busy: {cpu: (busy ticks, total ticks)}."""
        lines = ["cpu  0 0 0 0 0 0 0 0 0 0"]
        for cpu, (used, total) in sorted(busy.items()):
            lines.append(f"cpu{cpu} {used} 0 0 {total - used} 0 0 0 0 0 0")
        (self.root / "stat").write_text("\n".join(lines) + "\nintr 0\n", encoding="utf-8")

    def process(self, pid, comm, ppid, ticks, cpu, *, flags=0, uid=UID, cmdline="", exe=None,
                cwd=None, delays=None, start=1):
        folder = self.root / str(pid)
        folder.mkdir(exist_ok=True)
        rest = ["0"] * 37
        rest[0], rest[1], rest[6], rest[11], rest[19], rest[36] = (
            "R", str(ppid), str(flags), str(ticks), str(start), str(cpu))
        (folder / "stat").write_text(f"{pid} ({comm}) {' '.join(rest)}\n", encoding="utf-8")
        (folder / "status").write_text(f"Name:\t{comm}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n",
                                       encoding="utf-8")
        (folder / "cmdline").write_bytes(cmdline.replace(" ", "\0").encode())
        for name, target in (("exe", exe), ("cwd", cwd)):
            link = folder / name
            if link.is_symlink():
                link.unlink()
            if target is not None:
                link.symlink_to(target)
        for tid, ns in (delays or {}).items():
            task = folder / "task" / str(tid)
            task.mkdir(parents=True, exist_ok=True)
            (task / "schedstat").write_text(f"123 {ns} 4\n", encoding="utf-8")


class Clock:
    def __init__(self):
        self.seconds = 0.0

    def __call__(self):
        return self.seconds

    def stamp(self):
        return stamp(self.seconds)


def quiet_host(proc, driver_ticks=0, bench_ticks=0, bench_wait=0, busy=None, sampler_ticks=1):
    """The driver (100) and its bench (101) on core 4, the sampler (102) on cpu0, under
    measure.sh (99); a quiet host, unless `busy` says how busy each CPU is."""
    proc.cpus(busy or {cpu: (0, 0) for cpu in range(8)})
    project = proc.root.parent / "projects" / "itch-orderbook"
    proc.process(99, "measure.sh", 1, 0, 2, cmdline="bash /x/bin/measure.sh taskset -c 4 py")
    proc.process(100, "python3", 99, driver_ticks, CORE, cwd=project, delays={100: 0})
    proc.process(101, "bench_backtest", 100, bench_ticks, CORE, cwd=project,
                 delays={101: bench_wait})
    proc.process(102, "python3", 100, sampler_ticks, 0, cwd=project)
    return project


def sampler_on(proc, project, clock):
    return linux_sampler.Sampler(proc.root, 100, 102, CORE, SIBLING, project, UID, 100,
                                 clock=clock, now=clock.stamp)


def busy_cpus(per_cpu, seconds=1.0, base=0):
    """{cpu: (busy, total)} after `seconds` at the busy percentages given, from `base` ticks."""
    return {cpu: (base + round(per_cpu.get(cpu, 0.0) * seconds), base + round(100 * seconds))
            for cpu in range(8)}


class SamplerOnAMadeUpProc(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.proc = FakeProc(Path(self.scratch.name) / "proc")
        self.clock = Clock()

    def tearDown(self):
        self.scratch.cleanup()

    def two_samples(self, *, per_cpu, bench_wait=0, others=()):
        project = quiet_host(self.proc, busy=busy_cpus({}, 0))
        for pid, comm, kwargs in others:
            given = dict(kwargs)
            given.pop("ticks"), given.pop("cpu_after", None)
            self.proc.process(pid, comm, 1, 0, given.pop("cpu", 0), **given)
        sampler = sampler_on(self.proc, project, self.clock)
        first = sampler.sample()
        self.clock.seconds = 1.0
        quiet_host(self.proc, bench_ticks=100, bench_wait=bench_wait, busy=busy_cpus(per_cpu),
                   sampler_ticks=2)
        for pid, comm, kwargs in others:
            given = dict(kwargs)
            ticks, _ = given.pop("ticks"), given.pop("cpu", None)
            self.proc.process(pid, comm, 1, ticks, given.pop("cpu_after", 0), **given)
        return first, sampler.sample()

    def test_the_first_sample_is_a_baseline(self):
        first, _ = self.two_samples(per_cpu={CORE: 100.0})
        self.assertEqual(len(first), 1)
        moment, values = linux_sampler.parse_sample(first[0])
        self.assertEqual(moment, T0)
        self.assertEqual(values["dt"], "0.000")
        self.assertEqual(values["measured"], "100,101")

    def test_a_quiet_second(self):
        _, lines = self.two_samples(per_cpu={CORE: 100.0, 0: 1.0})
        self.assertEqual(len(lines), 1, lines)
        _, values = linux_sampler.parse_sample(lines[0])
        self.assertEqual((values["wait"], values["sibling"], values["others"]),
                         ("0.000", "0.000", "0.000"))
        # The sampler's own 1% on cpu0 is taken off; the measured tree excludes the sampler.
        self.assertEqual(values["measured"], "100,101")
        self.assertEqual(values["cpus"].split(",")[CORE], "100.0")

    def test_the_wait_of_the_measured_tree(self):
        _, lines = self.two_samples(per_cpu={CORE: 100.0}, bench_wait=30_000_000)
        _, values = linux_sampler.parse_sample(lines[0])
        self.assertEqual(values["wait"], "3.000")
        self.assertIn("FOREIGN core 3.00%", "\n".join(lines))

    def test_a_busy_sibling(self):
        other = {"ticks": 40, "cpu": SIBLING, "cpu_after": SIBLING}
        _, lines = self.two_samples(per_cpu={CORE: 100.0, SIBLING: 40.0},
                                    others=[(500, "other job", other)])
        _, values = linux_sampler.parse_sample(lines[0])
        self.assertEqual(values["sibling"], "40.000")
        self.assertIn("FOREIGN sibling 40.00% another process of this user", "\n".join(lines))

    def test_the_other_cores_add_up(self):
        # Three CPUs at 8% each: each under the bar alone, 24% together.
        _, lines = self.two_samples(per_cpu={CORE: 100.0, 1: 8.0, 2: 8.0, 6: 8.0})
        _, values = linux_sampler.parse_sample(lines[0])
        self.assertEqual(values["others"], "24.000")
        self.assertIn("FOREIGN others 24.00% no process to name", "\n".join(lines))

    def test_the_kinds(self):
        projects = Path(self.scratch.name) / "projects"
        others = [
            (600, "kworker/2:1", {"ticks": 30, "cpu_after": 2, "flags": linux_sampler.PF_KTHREAD}),
            (601, "daemon", {"ticks": 20, "cpu_after": 3, "uid": 0}),
            (602, "unit tests", {"ticks": 50, "cpu_after": 6, "cwd": projects / "other" / "b"}),
            (603, "a (strange) name", {"ticks": 10, "cpu_after": 7}),
            (604, "build", {"ticks": 5, "cpu_after": 1,
                            "exe": projects / "itch-orderbook" / "build" / "x"}),
        ]
        _, lines = self.two_samples(per_cpu={CORE: 100.0, 1: 5.0, 2: 30.0, 3: 20.0, 6: 50.0,
                                             7: 10.0}, others=others)
        busy = [line for line in lines if " busy " in line]
        self.assertEqual([line.split(" ", 3)[3] for line in busy], [
            "cpu6 another project's process: unit tests [602]",
            "cpu2 a kernel thread: kworker/2:1 [600]",
            "cpu3 a system service: daemon [601]",
            "cpu7 another process of this user: a (strange) name [603]",
            "cpu1 another process of this project: build [604]"])
        self.assertIn("FOREIGN others 115.00% another project's process", "\n".join(lines))

    def test_a_read_that_fails_is_logged(self):
        project = quiet_host(self.proc)
        (self.proc.root / "stat").unlink()
        lines = sampler_on(self.proc, project, self.clock).sample()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^\S+ failed ")

    def test_a_name_with_parentheses_and_spaces(self):
        row = linux_sampler.parse_stat(
            "7 (a) b (c)) R 3 0 0 0 -1 64 0 0 0 0 11 12 0 0 20 0 1 0 99 0 0 0 0 0 0 0 0 0 0 0 0 "
            "0 0 0 17 6 0")
        self.assertEqual(row, ("a) b (c)", 3, 64, 23, 99, 6))


def log(*seconds_and_values, pid=101, busy=()):
    """A log: a sample at each time, with the values given (wait, sibling, others)."""
    lines = [f"{stamp(-1)} start watch=100 core=4 sibling=5 cpu=0 every=1"]
    for seconds, (wait, sibling, others) in seconds_and_values:
        lines.append(f"{stamp(seconds)} sample dt=1.000 wait={wait} sibling={sibling} "
                     f"others={others} measured=100,{pid} cpus=0,0,0,0,100,0,0,0")
        lines.extend(f"{stamp(seconds)} {text}" for when, text in busy if when == seconds)
    return lines


QUIET = (0.0, 0.0, 0.0)


def steady(first, last, values=QUIET):
    return [(second, values) for second in range(first, last + 1)]


class Judge(unittest.TestCase):
    def test_a_quiet_span_is_clean(self):
        self.assertIsNone(linux_sampler.judge(log(*steady(0, 12)), stamp(0.5), stamp(10.5), 101))

    def test_no_sampler(self):
        self.assertEqual(linux_sampler.judge([], stamp(0), stamp(1)),
                         "the sampler did not cover the whole span")

    def test_a_span_the_sampler_did_not_cover(self):
        lines = log(*steady(2, 12))
        self.assertIn("did not cover", linux_sampler.judge(lines, stamp(1.5), stamp(10), 101))
        lines = log(*steady(0, 9))
        self.assertIn("did not cover", linux_sampler.judge(lines, stamp(1), stamp(9.5), 101))

    def test_a_gap(self):
        lines = log(*steady(0, 4), *steady(7, 12))
        self.assertEqual(linux_sampler.judge(lines, stamp(0.5), stamp(10.5), 101),
                         f"the sampler went 3.0 s without a sample after {stamp(4)}")
        # 2.5 s is still a sample's interval and a half: not a gap.
        self.assertIsNone(linux_sampler.judge(
            log(*steady(0, 4), (6.5, QUIET), *steady(8, 12)), stamp(0.5), stamp(10.5), 101))

    def test_a_failed_sample(self):
        lines = log(*steady(0, 12))
        lines.insert(6, f"{stamp(4.5)} failed OSError('no /proc/stat')")
        self.assertIn("the sampler failed at", linux_sampler.judge(lines, stamp(0.5),
                                                                   stamp(10.5), 101))

    def test_each_bar(self):
        for values, words in (((1.01, 0.0, 0.0), "the measured core: its process waited 1.01%"),
                              ((0.0, 5.01, 0.0), "the core's SMT sibling: 5.01% busy"),
                              ((0.0, 0.0, 20.01), "the other cores: 20.01% of a CPU busy")):
            lines = log(*steady(0, 5), (6, values), *steady(7, 12),
                        busy=[(6, "busy 20.1% cpu2 another project's process: unit tests [7]")])
            why = linux_sampler.judge(lines, stamp(0.5), stamp(10.5), 101)
            self.assertIn(words, why)
            self.assertIn(f"at {stamp(6)}, the most of it another project's process", why)
            self.assertNotIn("unit tests", why)

    def test_the_bars_are_not_crossed_at_the_bar(self):
        lines = log(*steady(0, 12, (1.0, 5.0, 20.0)))
        self.assertIsNone(linux_sampler.judge(lines, stamp(0.5), stamp(10.5), 101))

    def test_the_seconds_either_side(self):
        busy = (0.0, 0.0, 50.0)
        # The sample at or before the start closes a second before the span: not judged.
        lines = log((0, busy), *steady(1, 12))
        self.assertIsNone(linux_sampler.judge(lines, stamp(0.5), stamp(10.5), 101))
        # The first sample after the end closes a second that the span ends inside: judged.
        lines = log(*steady(0, 10), (11, busy), *steady(12, 13))
        self.assertIn("the other cores", linux_sampler.judge(lines, stamp(0.5), stamp(10.5), 101))
        # Any later one is not.
        lines = log(*steady(0, 11), (12, busy))
        self.assertIsNone(linux_sampler.judge(lines, stamp(0.5), stamp(10.5), 101))

    def test_with_no_kind_to_give(self):
        lines = log(*steady(0, 5), (6, (0.0, 0.0, 30.0)), *steady(7, 12))
        self.assertIn("the most of it no process to name, so kernel or interrupt work",
                      linux_sampler.judge(lines, stamp(0.5), stamp(10.5), 101))

    def test_the_measured_process_unseen(self):
        lines = log(*steady(0, 12), pid=999)
        self.assertEqual(linux_sampler.judge(lines, stamp(0.5), stamp(10.5), 101),
                         "the sampler did not see the measured process through its span")
        self.assertIsNone(linux_sampler.judge(lines, stamp(0.5), stamp(10.5)))

    def test_a_value_it_cannot_read(self):
        lines = [line.replace("others=0.0", "others=x") if stamp(5) in line else line
                 for line in log(*steady(0, 12))]
        self.assertRaises(ValueError, linux_sampler.judge, lines, stamp(0.5), stamp(10.5), 101)
        lines = [line.replace(" others=0.0", "") if stamp(5) in line else line
                 for line in log(*steady(0, 12))]
        self.assertIn("the other cores: nan%", linux_sampler.judge(lines, stamp(0.5),
                                                                   stamp(10.5), 101))

    def test_the_summary_and_its_check(self):
        lines = log(*steady(0, 4), (5, (0.4, 2.0, 12.5)), *steady(6, 12))
        text = linux_sampler.summary(lines, stamp(0.5), stamp(10.5), CORE, SIBLING)
        self.assertEqual(text, f"12 samples from {stamp(0)} to {stamp(11)}, the longest gap "
                               "1.00 s; at most 0.40% run-queue wait on core 4, 2.00% busy on "
                               "cpu5, 12.50% of a CPU on the other cores")
        self.assertIsNone(linux_sampler.summary_breaks(text))
        for old, new, words in (("1.00 s", "2.60 s", "the longest gap"),
                                ("0.40%", "1.01%", "run-queue wait"),
                                ("2.00%", "5.01%", "sibling"),
                                ("12.50%", "20.01%", "other cores"),
                                ("12 samples", "1 samples", "cannot cover")):
            self.assertIn(words, linux_sampler.summary_breaks(text.replace(old, new)))
        self.assertIn("not a sampler summary", linux_sampler.summary_breaks("quiet"))

    def test_a_partial_last_line_is_left_for_later(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            path = Path(directory) / "sampler.txt"
            path.write_text("\n".join(log(*steady(0, 2))) + f"\n{stamp(3)} sample dt=1.0 wa",
                            encoding="utf-8")
            watch = linux_sampler.Watch(path, None, 1.0)
            self.assertEqual(len(watch.lines()), 4)
            self.assertTrue(watch.lines()[-1].startswith(stamp(2)))

    def test_quiet_for(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            path = Path(directory) / "sampler.txt"
            watch = linux_sampler.Watch(path, None, 1.0)
            self.assertFalse(watch.quiet_for(5))
            path.write_text("\n".join(log(*steady(0, 12))) + "\n", encoding="utf-8")
            self.assertTrue(watch.quiet_for(10))
            self.assertFalse(watch.quiet_for(20))
            path.write_text("\n".join(log(*steady(0, 9), (10, (0.0, 9.0, 0.0)), *steady(11, 12)))
                            + "\n", encoding="utf-8")
            self.assertFalse(watch.quiet_for(5))


# The driver, the stamp and the check on a debian file.

DEBIAN = dict(test_backtest_results.HEADER, host="debian", cpu="AMD Ryzen Embedded V1605B",
              kernel="Linux 6.12.116+deb13-amd64",
              pinning="pinned to core 4: pthread_setaffinity_np returned 0, read back [4]",
              core="4", governor="performance", frequency="3593108 kHz", turbo="on",
              isolation="none", smt="on", timer="lfence;rdtsc and rdtscp;lfence, calibrated "
              "1997.961 MHz", quantum="20 ticks, 10.010 ns", floor="40.041 ns",
              migrations="0 seen in 3 samples, core 4 to core 4")
RULE_DAY = ("2026-11-13T05:00:01Z", "2026-11-13T05:00:29Z")
BEFORE_THE_RULE = ("2026-11-11T06:17:06Z", "2026-11-11T06:17:34Z")


def stderr_for(started, finished):
    def second(stamp_text, offset):
        moment = datetime.datetime.strptime(stamp_text, "%Y-%m-%dT%H:%M:%SZ")
        return (moment + datetime.timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (f"measure.sh: start load1=1.01 host=debian at={second(started, -1)} cpus=4\n"
            f"measure.sh: end load1=1.01 exit=0 at={second(finished, 1)} cpus=4\n")


def debian_record(seconds):
    record = test_backtest_results.record("shadow", seconds)
    record["header"] = dict(DEBIAN)
    return record


def published(count=3):
    lines = log(*steady(0, 60))
    return [(index, stamp(10 * index), stamp(10 * index + 8),
             linux_sampler.summary(lines, stamp(10 * index), stamp(10 * index + 8), CORE, SIBLING))
            for index in range(1, count + 1)]


def debian_text(when=RULE_DAY, sampled=None):
    """The driver's file before its stamp; sampled False for a run with no sampler."""
    records = [debian_record(value) for value in (1.0, 1.1, 0.9)]
    args = argparse.Namespace(prediction="P-0106", workload="shadow", wrapped_by=None)
    if sampled is None:
        sampled = {"every": 1.0, "cpu": 0, "published": published(), "withdrawn": []}
    return backtest_driver.render(records, args, "P-0106-x", *when,
                                  test_backtest_results.MACHINE,
                                  "python3 -B scripts/backtest_driver.py", sampled)


def by_hand(text, when=RULE_DAY):
    """The load lines put in without stamp_load.py, as a run with no sampler would be."""
    start, end = stderr_for(*when).splitlines()
    return text.replace(f"- measure_load_start: {check_results.PENDING}",
                        f"- measure_load_start: {start}").replace(
        f"- measure_load_end: {check_results.PENDING}", f"- measure_load_end: {end}")


def debian_file(when=RULE_DAY, sampled=None):
    return stamp_load.stamp(debian_text(when, sampled), stderr_for(*when))


def codes(text):
    return {code for code, _ in check_results.check_text(text)}


class DebianFiles(unittest.TestCase):
    def test_a_sampled_file_passes(self):
        text = debian_file()
        self.assertEqual(check_results.check_text(text), [])
        self.assertIn("- load_rule: the Linux sampler rule was applied: sampled every 1 s from "
                      "/proc by scripts/linux_sampler.py on cpu0, over each process's whole span; "
                      "every published process quiet; 0 withdrawn under the rule", text)
        self.assertIn("## Sampler", text)

    def test_a_file_from_before_the_rule_is_not_held_to_it(self):
        text = debian_file(BEFORE_THE_RULE, sampled=False)
        self.assertNotIn("load_rule", text)
        self.assertEqual(check_results.check_text(text), [])

    def test_a_file_from_the_rule_on_with_no_sampler(self):
        text = by_hand(debian_text(sampled=False))
        self.assertIn("load-rule-missing", codes(text))
        self.assertNotIn("load-rule-missing", codes(debian_text(sampled=False)))

    def test_a_published_process_over_a_bar(self):
        text = debian_file()
        row = re.search(r"^\| 2 \| 2026.*$", text, re.MULTILINE).group(0)
        broken = text.replace(row, row.replace("0.00% of a CPU", "20.50% of a CPU"))
        self.assertNotEqual(broken, text)
        self.assertIn("load-rule-not-quiet", codes(broken))

    def test_a_repetition_with_no_sampler_row(self):
        text = debian_file()
        row = re.search(r"^\| 3 \| 2026.*\n", text, re.MULTILINE).group(0)
        self.assertIn("load-rule-not-quiet", codes(text.replace(row, "")))

    def test_the_rule_words_changed(self):
        text = debian_file()
        broken = text.replace("every published process quiet", "most processes quiet")
        self.assertIn("load-rule-invalid", codes(broken))

    def test_the_withdrawn_count_is_the_sections(self):
        sampled = {"every": 1.0, "cpu": 0, "published": published(),
                   "withdrawn": [("repetition 2", stamp(1), "the sampler saw x")]}
        text = debian_file(sampled=sampled)
        self.assertEqual(check_results.check_text(text), [])
        self.assertIn("1 withdrawn under the rule", text)
        self.assertIn("| withdrawn 1, repetition 2 |", text)
        broken = text.replace("1 withdrawn under the rule", "0 withdrawn under the rule")
        self.assertIn("load-rule-not-quiet", codes(broken))

    def test_the_stamp_refuses_a_debian_run_without_its_sampler(self):
        text = debian_text(sampled=False)
        stderr = stderr_for(*RULE_DAY)
        with self.assertRaisesRegex(ValueError, "Linux sampler"):
            stamp_load.stamp(text, stderr)
        clean = "\n".join(line.replace("T04:", "T05:") for line in log(*steady(-2, 33)))
        stamped = stamp_load.stamp(text, stderr, clean)
        self.assertIn("- load_rule: quiet: the Linux sampler rule was applied over the whole "
                      "run: ", stamped)
        self.assertEqual(check_results.check_text(stamped), [])
        busy = clean.replace("T05:00:10.000000Z sample dt=1.000 wait=0.0 sibling=0.0 others=0.0",
                             "T05:00:10.000000Z sample dt=1.000 wait=0.0 sibling=0.0 others=40.0")
        self.assertNotEqual(busy, clean)
        with self.assertRaisesRegex(ValueError, "the other cores: 40.00%"):
            stamp_load.stamp(text, stderr, busy)
        with self.assertRaisesRegex(ValueError, "did not cover"):
            stamp_load.stamp(text, stderr, "\n".join(clean.splitlines()[:30]))
        with self.assertRaisesRegex(ValueError, "applied the Linux sampler rule already"):
            stamp_load.stamp(debian_text(), stderr, clean)
        broken = stamped.replace("0.00% of a CPU on the other cores",
                                 "30.00% of a CPU on the other cores")
        self.assertIn("load-rule-not-quiet", codes(broken))


class FakeWatch:
    """A Watch whose log is a list the test fills, one span at a time."""

    def __init__(self, spans):
        self.spans = list(spans)  # values for each process in turn
        self.lines_now = []
        self.every = 1.0

    def lines(self):
        return list(self.lines_now)

    def wait_past(self, end):
        values = self.spans.pop(0)
        end_at = linux_sampler.at(end)
        self.lines_now = [
            f"{linux_sampler.stamp_of(end_at + datetime.timedelta(seconds=offset))} sample "
            f"dt=1.000 wait=0.0 sibling=0.0 others={values} measured=1,2 cpus=-"
            for offset in (-3, -2, -1, -0.1, 0.5)]

    def quiet_for(self, _seconds):
        return True


class Driver(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        root = Path(self.scratch.name)
        self.args = argparse.Namespace(prediction="P-0106", workload="shadow", core=4, sibling=5,
                                       raw_dir=root / "raw", run_log=root / "run_log.md",
                                       most_in_a_row=3, most_withdrawn=20, quiet_before=0,
                                       quiet_wait=0)
        self.args.raw_dir.mkdir()
        self.calls = 0

    def tearDown(self):
        self.scratch.cleanup()

    def fake_process(self, args, index, _commit, _digest):
        self.calls += 1
        for suffix in (".json", ".log"):
            (args.raw_dir / f"shadow-repetition-{index}{suffix}").write_text("x", encoding="utf-8")
        time.sleep(0.3)
        backtest_driver.last_pid = 2
        return debian_record(1.0)

    def judged(self, spans):
        watch = FakeWatch(spans)
        sampled = {"every": 1.0, "cpu": 0, "published": [], "withdrawn": []}
        original = backtest_driver.run_process
        backtest_driver.run_process = self.fake_process
        try:
            record = backtest_driver.judged_process(self.args, 1, "c", "d", watch, sampled)
        finally:
            backtest_driver.run_process = original
        return record, sampled

    def test_a_quiet_process_is_published(self):
        _, sampled = self.judged(["0.0"])
        self.assertEqual(self.calls, 1)
        self.assertEqual(len(sampled["published"]), 1)
        self.assertFalse(self.args.run_log.exists())

    def test_a_busy_process_is_withdrawn_and_run_again(self):
        _, sampled = self.judged(["30.0", "0.0"])
        self.assertEqual(self.calls, 2)
        self.assertEqual(len(sampled["withdrawn"]), 1)
        row = self.args.run_log.read_text(encoding="utf-8").splitlines()[-1]
        self.assertIn("withdrawn: T-BT-3, repetition 1 on debian, exit 0; the sampler saw the "
                      "other cores: 30.00% of a CPU busy", row)
        self.assertIn("so the Linux sampler rule does not publish it; nothing from it is "
                      "published, and it is rerun under P-0106", row)
        self.assertTrue((self.args.raw_dir / "shadow-repetition-1-withdrawn-1.json").is_file())

    def test_three_in_a_row_give_the_host_back(self):
        with self.assertRaisesRegex(backtest_driver.Refused, "withdrawn 3 times in a row"):
            self.judged(["30.0", "30.0", "30.0", "0.0"])
        self.assertEqual(self.calls, 3)
        rows = [line for line in self.args.run_log.read_text(encoding="utf-8").splitlines()
                if line.startswith("| P-0106-")]
        self.assertEqual(len(rows), 3)

    def test_a_host_that_never_quiets(self):
        self.args.quiet_before, self.args.quiet_wait = 10, 0
        watch = FakeWatch([])
        watch.quiet_for = lambda _seconds: False
        with self.assertRaisesRegex(backtest_driver.Refused, "never saw 10 s"):
            backtest_driver.wait_for_quiet(watch, self.args)


@unittest.skipUnless(sys.platform.startswith("linux"), "the sampler reads a real /proc")
class OnLinux(unittest.TestCase):
    def test_the_sampler_beside_a_busy_child(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            path = Path(directory) / "sampler.txt"
            cpus = sorted(os.sched_getaffinity(0))
            core = cpus[-1]
            watch, cpu = linux_sampler.Watch.start(path, core, core, cpu=cpus[0])
            try:
                begin = linux_sampler.utc_now()
                child = subprocess.Popen([sys.executable, "-c", "import time\nend = time.time() "
                                          "+ 3\nwhile time.time() < end: pass"],
                                         preexec_fn=lambda: os.sched_setaffinity(0, {core}))
                child.wait()
                end = linux_sampler.utc_now()
                watch.wait_past(end)
                lines = watch.lines()
            finally:
                watch.stop()
            self.assertEqual(cpu, cpus[0])
            samples = [linux_sampler.parse_sample(line) for line in lines]
            samples = [values for values in samples if values]
            self.assertGreaterEqual(len(samples), 4, lines)
            self.assertTrue(any(str(child.pid) in values["measured"].split(",")
                                for _, values in samples), lines)
            self.assertTrue(lines[-1].endswith(" stop") or watch.process.returncode is not None)
            summary = linux_sampler.summary(lines, begin, end, core, core)
            self.assertRegex(summary, r"^\d+ samples from ")


if __name__ == "__main__":
    unittest.main()
