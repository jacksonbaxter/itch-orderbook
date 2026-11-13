#!/usr/bin/env python3
"""The Linux sampler rule: a sampler of other work beside a pinned run, and the judge of its log.

    linux_sampler.py --log FILE --watch PID --core N --sibling N [--cpu N] [--every S]

debian is shared with other projects' long jobs, and measure.sh keeps only measurements
apart, so a reported Linux process is published only if this sampler ran beside it and saw
no other work where it could slow the process (docs/NOTES.md, "The Linux sampler rule"). It
needs no root and changes nothing on the host: every second it reads /proc only.

- /proc/stat: each logical CPU's busy and total time, so each CPU's busy share of the second.
- /proc/<pid>/stat for every process: its CPU time, parent, flags and the CPU it last ran
  on, so each process's share of a CPU over the second.
- /proc/<pid>/task/<tid>/schedstat for every thread of the measured tree: the time it waited
  on a run queue while it could run, which on a pinned core is the time other work took from it.

The measured tree is --watch (the driver) and every process under it, less the sampler. The
sampler, the measured tree and the wrappers above --watch (measure.sh, /usr/bin/time) are this
run's own; everything else is other work. Each second it writes one `sample` line, then a
FOREIGN line for each bar crossed, and a `busy` line for each other process above 1% of a
CPU, by kind and by name. The log stays in the raw directory and is never committed; only
the kinds ever reach a committed file.

The three bars, each judged on every second of a process's span:
- core: the measured tree waited on the run queue for more than CORE_WAIT_PCT of the second.
- sibling: the core's SMT sibling was busy for more than SIBLING_PCT of the second.
- others: the other logical CPUs together, less the sampler's own time, were busy for more
  than OTHERS_PCT of one CPU in the second.

judge() reads a log against a span. The span is clean only if a sample at or before its start
and one at or after its end exist, no two samples between are more than LONGEST_GAP_S apart,
no sample failed, every second that overlaps the span is under all three bars, and the samples
inside it saw the measured process. A driver withdraws any process that judge() does not pass.

The sampler pins itself to --cpu, by default the lowest CPU that is neither --core nor
--sibling, and runs until --watch exits or it is signalled, with one last sample either way.

Exit codes: 0 stopped as asked or after --watch exited, 1 failed to start, 2 bad usage.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import process_kinds  # noqa: E402
EVERY_S = 1.0
LONGEST_GAP_S = 2.5  # 2.5 times the interval, as the Mac rule's 5 s is of its 2 s
CORE_WAIT_PCT = 1.0
SIBLING_PCT = 5.0
OTHERS_PCT = 20.0
NAME_PCT = 1.0  # A busy line names each other process above this share of a CPU.
NAMED = 5  # The most busy lines a sample writes.
WRAPPERS = ("measure.sh", "/usr/bin/time")
PF_KTHREAD = 0x00200000  # include/linux/sched.h
STAMP = "%Y-%m-%dT%H:%M:%S.%fZ"

# What another process is, for anything committed: never its name, always one of
# process_kinds.KINDS, told from /proc alone (kind_of), so this needs no name-to-kind map.
KERNEL = process_kinds.KERNEL_THREAD
SERVICE = process_kinds.LINUX_DAEMON
OTHER_PROJECT = process_kinds.OTHER_PROJECT
THIS_PROJECT = process_kinds.THIS_PROJECT
OTHER = process_kinds.OTHER
UNNAMED = process_kinds.UNNAMED
# The words logs written before 2026-11-13 used, read as the kinds they now are.
EARLIER_WORDS = {"a system service": SERVICE, "another process of this user": OTHER}


def stamp_of(moment):
    return moment.astimezone(datetime.UTC).strftime(STAMP)


def utc_now():
    return stamp_of(datetime.datetime.now(datetime.UTC))


def at(stamp):
    return datetime.datetime.strptime(stamp, STAMP).replace(tzinfo=datetime.UTC)


# reading /proc

def cpu_times(proc):
    """{cpu number: (busy, total)} in clock ticks, from /proc/stat's per-CPU lines."""
    times = {}
    for line in (proc / "stat").read_text(encoding="utf-8").splitlines():
        words = line.split()
        if not words or not words[0].startswith("cpu") or words[0] == "cpu":
            continue
        values = [int(word) for word in words[1:9]]
        user, nice, system, idle, iowait, irq, softirq, steal = values + [0] * (8 - len(values))
        busy = user + nice + system + irq + softirq + steal  # guest time is inside user
        times[int(words[0][3:])] = (busy, busy + idle + iowait)
    return times


def parse_stat(text):
    """(comm, ppid, flags, ticks, start, cpu) from a /proc/<pid>/stat line. The name sits in
    parentheses and may hold spaces and parentheses itself, so the fields start after the last
    one; field N of proc(5) is rest[N - 3]."""
    opened, closed = text.index("("), text.rindex(")")
    rest = text[closed + 1:].split()
    return (text[opened + 1:closed], int(rest[1]), int(rest[6]), int(rest[11]) + int(rest[12]),
            int(rest[19]), int(rest[36]))


def processes(proc):
    """{pid: (comm, ppid, flags, ticks, start, cpu)} for every process that could be read."""
    found = {}
    for entry in os.scandir(proc):
        if not entry.name.isdigit():
            continue
        try:
            found[int(entry.name)] = parse_stat(
                (proc / entry.name / "stat").read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError, IndexError):
            continue  # It exited between the listing and the read.
    return found


def run_delays(proc, pid):
    """{tid: ns waited on a run queue} for each thread of pid, from schedstat's second field."""
    delays = {}
    try:
        tasks = os.listdir(proc / str(pid) / "task")
    except OSError:
        return delays
    for tid in tasks:
        try:
            fields = (proc / str(pid) / "task" / tid / "schedstat").read_text(
                encoding="utf-8").split()
            delays[int(tid)] = int(fields[1])
        except (OSError, ValueError, IndexError):
            continue
    return delays


def command_line(proc, pid):
    try:
        raw = (proc / str(pid) / "cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()


def uid_of(proc, pid):
    try:
        for line in (proc / str(pid) / "status").read_text(encoding="utf-8").splitlines():
            if line.startswith("Uid:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def inside(path, root):
    try:
        Path(path).relative_to(root)
    except ValueError:
        return False
    return True


def kind_of(proc, pid, flags, uid, project):
    """What the process is, by kind: the words a committed file may carry."""
    if flags & PF_KTHREAD:
        return KERNEL
    if uid_of(proc, pid) != uid:
        return SERVICE
    places = []
    for link in ("exe", "cwd"):
        try:
            places.append(os.readlink(proc / str(pid) / link))
        except OSError:
            continue
    if any(inside(place, project) for place in places):
        return THIS_PROJECT
    if any(inside(place, project.parent) for place in places):
        return OTHER_PROJECT
    return OTHER


def descendants(found, root):
    children = {}
    for pid, row in found.items():
        children.setdefault(row[1], []).append(pid)
    tree, stack = set(), [root]
    while stack:
        pid = stack.pop()
        if pid in found and pid not in tree:
            tree.add(pid)
            stack.extend(children.get(pid, ()))
    return tree


class Sampler:
    """The state between samples. sample() reads /proc once and returns the lines for it."""

    def __init__(self, proc, watch, me, core, sibling, project, uid, clk_tck,
                 clock=time.monotonic, now=utc_now):
        self.proc, self.watch, self.me = Path(proc), watch, me
        self.core, self.sibling = core, sibling
        self.project, self.uid, self.clk_tck = Path(project), uid, clk_tck
        self.clock, self.now = clock, now
        self.last = None  # (clock, cpu times, processes, run delays) of the last good sample

    def own(self, found):
        """The measured tree, the sampler, and the wrappers above --watch."""
        measured = descendants(found, self.watch) - descendants(found, self.me)
        own = measured | descendants(found, self.me)
        above = found.get(self.watch, (None, 0))[1]
        while above in found and any(word in command_line(self.proc, above) for word in WRAPPERS):
            own.add(above)
            above = found[above][1]
        return measured, own

    def sample(self):
        stamp = self.now()
        try:
            moment = self.clock()
            times = cpu_times(self.proc)
            found = processes(self.proc)
            measured, own = self.own(found)
            delays = {(pid, tid): ns for pid in measured
                      for tid, ns in run_delays(self.proc, pid).items()}
        except (OSError, ValueError) as error:
            return [f"{stamp} failed {error!r}"]
        last, self.last = self.last, (moment, times, found, delays)
        listed = ",".join(str(pid) for pid in sorted(measured))
        if last is None:
            return [f"{stamp} sample dt=0.000 wait=0.000 sibling=0.000 others=0.000 "
                    f"measured={listed or '-'} cpus=-"]
        before_moment, before_times, before_found, before_delays = last
        dt = max(moment - before_moment, 1e-6)
        busy = {}
        for cpu, (used, total) in times.items():
            used0, total0 = before_times.get(cpu, (used, total))
            busy[cpu] = 100.0 * (used - used0) / (total - total0) if total > total0 else 0.0
        share = {}
        for pid, row in found.items():
            old = before_found.get(pid)
            ticks = row[3] - old[3] if old is not None and old[4] == row[4] else row[3]
            share[pid] = 100.0 * ticks / self.clk_tck / dt
        own_on = {}
        for pid in own:
            own_on[found[pid][5]] = own_on.get(found[pid][5], 0.0) + share[pid]
        waited = sum(ns - before_delays.get(key, 0) for key, ns in delays.items())
        wait = max(0.0, 100.0 * waited / (dt * 1e9))
        sibling = max(0.0, busy.get(self.sibling, 0.0) - own_on.get(self.sibling, 0.0))
        others = sum(max(0.0, value - own_on.get(cpu, 0.0)) for cpu, value in busy.items()
                     if cpu not in (self.core, self.sibling))
        named = sorted(((share[pid], pid) for pid in found if pid not in own
                        and share[pid] > NAME_PCT), reverse=True)[:NAMED]
        kinds = [(value, pid, kind_of(self.proc, pid, found[pid][2], self.uid, self.project))
                 for value, pid in named]
        top = kinds[0][2] if kinds else UNNAMED
        cpus = ",".join(f"{busy[cpu]:.1f}" for cpu in sorted(busy))
        lines = [f"{stamp} sample dt={dt:.3f} wait={wait:.3f} sibling={sibling:.3f} "
                 f"others={others:.3f} measured={listed or '-'} cpus={cpus}"]
        for where, value, bar in (("core", wait, CORE_WAIT_PCT), ("sibling", sibling, SIBLING_PCT),
                                  ("others", others, OTHERS_PCT)):
            if value > bar:
                lines.append(f"{stamp} FOREIGN {where} {value:.2f}% {top}")
        for value, pid, kind in kinds:
            lines.append(f"{stamp} busy {value:.1f}% cpu{found[pid][5]} {kind}: "
                         f"{found[pid][0]} [{pid}]")
        return lines


# judging a log

def parse_sample(line):
    """(time, {name: value}) from a sample line, or None for any other line."""
    words = line.split()
    if len(words) < 2 or words[1] != "sample":
        return None
    try:
        moment = at(words[0])
    except ValueError:
        return None
    values = {}
    for word in words[2:]:
        name, _, value = word.partition("=")
        values[name] = value
    return moment, values


def judge(lines, begin, end, pid=None):
    """Why the log does not show the span from begin to end clean, or None."""
    begin, end = at(begin), at(end)
    samples = [parsed for parsed in map(parse_sample, lines) if parsed is not None]
    before = [moment for moment, _ in samples if moment <= begin]
    after = [moment for moment, _ in samples if moment >= end]
    if not before or not after:
        return "the sampler did not cover the whole span"
    low, high = max(before), min(after)
    span = [(moment, values) for moment, values in samples if low <= moment <= high]
    for (earlier, _), (later, _) in zip(span, span[1:]):
        gap = (later - earlier).total_seconds()
        if gap > LONGEST_GAP_S:
            return f"the sampler went {gap:.1f} s without a sample after {stamp_of(earlier)}"
    for line in lines:
        words = line.split(None, 2)
        if len(words) > 1 and words[1] == "failed" and low < at(words[0]) <= high:
            return f"the sampler failed at {words[0]}"
    for moment, values in span[1:]:
        for where, bar, what in (
                ("wait", CORE_WAIT_PCT, "the measured core: its process waited {}% of a second "
                                        "on the run queue"),
                ("sibling", SIBLING_PCT, "the core's SMT sibling: {}% busy"),
                ("others", OTHERS_PCT, "the other cores: {}% of a CPU busy")):
            value = float(values.get(where, "nan"))
            if not value <= bar:
                return (f"the sampler saw {what.format(f'{value:.2f}')} at {stamp_of(moment)}, "
                        f"the most of it {top_kind(lines, moment)}")
    if pid is not None:
        within = [values for moment, values in span if begin < moment < end]
        if not within or any(str(pid) not in values.get("measured", "").split(",")
                             for values in within):
            return "the sampler did not see the measured process through its span"
    return None


def top_kind(lines, moment):
    """The kind of the busiest other process at a sample, from its first busy line."""
    for line in lines:
        words = line.split(None, 4)
        if len(words) == 5 and words[1] == "busy" and at(words[0]) == moment:
            kind = words[4].split(":", 1)[0]
            return EARLIER_WORDS.get(kind, kind)
    return UNNAMED


def summary(lines, begin, end, core, sibling):
    """A clean span in words, with the figures check_results.py holds to the bars."""
    begin, end = at(begin), at(end)
    samples = [parsed for parsed in map(parse_sample, lines) if parsed is not None]
    low = max(moment for moment, _ in samples if moment <= begin)
    high = min(moment for moment, _ in samples if moment >= end)
    span = [(moment, values) for moment, values in samples if low <= moment <= high]
    gap = max(((later - earlier).total_seconds() for (earlier, _), (later, _)
               in zip(span, span[1:])), default=0.0)
    most = {name: max((float(values[name]) for _, values in span[1:]), default=0.0)
            for name in ("wait", "sibling", "others")}
    return (f"{len(span)} samples from {stamp_of(low)} to {stamp_of(high)}, the longest gap "
            f"{gap:.2f} s; at most {most['wait']:.2f}% run-queue wait on core {core}, "
            f"{most['sibling']:.2f}% busy on cpu{sibling}, {most['others']:.2f}% of a CPU on "
            "the other cores")


def summary_breaks(text):
    """Why a summary's figures break the rule, or None. For check_results.py."""
    match = re.fullmatch(
        r"(\d+) samples from \S+ to \S+, the longest gap ([\d.]+) s; at most ([\d.]+)% "
        r"run-queue wait on core \d+, ([\d.]+)% busy on cpu\d+, ([\d.]+)% of a CPU on the "
        r"other cores", text)
    if match is None:
        return f"not a sampler summary: {text!r}"
    count, gap, wait, sibling, others = (float(group) for group in match.groups())
    if count < 2:
        return f"{count:.0f} samples cannot cover a span"
    for value, bar, what in ((gap, LONGEST_GAP_S, "the longest gap"),
                             (wait, CORE_WAIT_PCT, "the core's run-queue wait"),
                             (sibling, SIBLING_PCT, "the sibling's busy share"),
                             (others, OTHERS_PCT, "the other cores' busy share")):
        if value > bar:
            return f"{what}, {value:g}, is over the rule's {bar:g}"
    return None


# the driver's side

class Watch:
    """A sampler process beside a driver, writing its log; the driver reads it back."""

    def __init__(self, log, process, every):
        self.log, self.process, self.every = log, process, every

    @classmethod
    def start(cls, log, core, sibling, cpu=None, every=EVERY_S):
        log.unlink(missing_ok=True)
        cpu = default_cpu(core, sibling) if cpu is None else cpu
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--log", str(log),
                   "--watch", str(os.getpid()), "--core", str(core), "--sibling", str(sibling),
                   "--cpu", str(cpu), "--every", f"{every:g}"]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   preexec_fn=lambda: os.sched_setaffinity(0, {cpu}))
        watch = cls(log, process, every)
        give_up = time.monotonic() + 30
        while not any(parse_sample(line) for line in watch.lines()):
            if process.poll() is not None or time.monotonic() > give_up:
                watch.stop()
                raise OSError(f"the sampler wrote no sample (exit {process.poll()})")
            time.sleep(0.1)
        return watch, cpu

    def lines(self):
        """The log's whole lines: one the sampler is still writing is left for the next read."""
        try:
            text = self.log.read_text(encoding="utf-8")
        except OSError:
            return []
        return text[:text.rfind("\n") + 1].splitlines()

    def wait_past(self, stamp):
        """Until a sample at or after stamp, so that the log covers a span's end."""
        give_up = time.monotonic() + 3 * self.every + LONGEST_GAP_S
        while time.monotonic() < give_up:
            samples = [parsed[0] for parsed in map(parse_sample, self.lines()) if parsed]
            if samples and samples[-1] >= at(stamp):
                return
            time.sleep(min(self.every, 0.2))

    def quiet_for(self, seconds):
        """Whether the log's last `seconds` pass the rule, with no process to look for."""
        samples = [parsed[0] for parsed in map(parse_sample, self.lines()) if parsed]
        if not samples:
            return False
        end = samples[-1]
        begin = end - datetime.timedelta(seconds=seconds)
        return samples[0] <= begin and judge(self.lines(), stamp_of(begin), stamp_of(end)) is None

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def default_cpu(core, sibling):
    """The lowest CPU this process may use that is neither the measured core nor its sibling."""
    allowed = sorted(set(range(os.cpu_count() or 1)) - {core, sibling})
    return allowed[0] if allowed else core


# the sampler's own loop

class Stop(Exception):
    pass


def stop_on_signal(signum, _frame):
    raise Stop(signum)


def alive(proc, pid):
    try:
        state = (proc / str(pid) / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state not in ("Z", "X")


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--watch", type=int, required=True)
    parser.add_argument("--core", type=int, required=True)
    parser.add_argument("--sibling", type=int, required=True)
    parser.add_argument("--cpu", type=int)
    parser.add_argument("--every", type=float, default=EVERY_S)
    parser.add_argument("--project", type=Path,
                        default=REPO_ROOT.parent if REPO_ROOT.name == "src" else REPO_ROOT,
                        help="this project's folder; a process inside its parent but outside "
                             "it is another project's")
    parser.add_argument("--proc", type=Path, default=Path("/proc"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 0 < args.every <= EVERY_S:
        parser.error(f"--every must be above 0 and at most the rule's {EVERY_S:g} s")
    return args


def main(argv=None):
    args = parse_args(argv)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, stop_on_signal)
    cpu = default_cpu(args.core, args.sibling) if args.cpu is None else args.cpu
    try:
        if hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, {cpu})
        sampler = Sampler(args.proc, args.watch, os.getpid(), args.core, args.sibling,
                          args.project.resolve(), os.getuid(), os.sysconf("SC_CLK_TCK"))
        out = open(args.log, "a", encoding="utf-8")  # noqa: SIM115
    except (OSError, ValueError) as error:
        print(f"linux_sampler: cannot start: {error}", file=sys.stderr)
        return 1
    with out:
        out.write(f"{utc_now()} start watch={args.watch} core={args.core} "
                  f"sibling={args.sibling} cpu={cpu} every={args.every:g}\n")
        start, count = time.monotonic(), 0
        try:
            while True:
                out.write("".join(line + "\n" for line in sampler.sample()))
                out.flush()
                if not alive(args.proc, args.watch):
                    break
                count += 1
                time.sleep(max(0.0, start + count * args.every - time.monotonic()))
        except Stop:
            out.write("".join(line + "\n" for line in sampler.sample()))
        out.write(f"{utc_now()} stop\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
