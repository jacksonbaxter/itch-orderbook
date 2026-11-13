#!/usr/bin/env python3
"""Stamps a run's measure.sh load lines into its results file.

    stamp_load.py --results FILE --stderr FILE [--sampler LOG] [--out FILE]

measure.sh prints its end line after the benchmark exits, so the file is finished from
outside the lock, from the stderr that measure.sh wrote:

    measure.sh bench_driver.py ... 2> STDERR
    stamp_load.py --results RESULTS --stderr STDERR

The lines are copied, never written from nothing, and refused if they are missing, doubled,
or do not bracket the run. measure.sh's start line names the machine's hostname, which
never goes into a repo, so that one word is replaced by the header's fixed host label.
If `/usr/bin/time -l` wrapped the run, its peak memory lines are stamped in as well.

A macOS run from 2026-11-05 on also needs the log of a CPU sampler that ran beside it,
since other projects share the Mac and do not all wait for measure.sh's lock. The sampler
writes `<UTC time> sample` every 2 s and `<UTC time> FOREIGN <cpu> <command>` for each
process other than the measured ones above 20% CPU. The run is stamped only if the
sampler's lines bracket measure.sh's start and end with no gap over 5 s between them and
no FOREIGN line falls inside: otherwise the run is refused, to be withdrawn and rerun.

A debian run from 2026-11-13T03:00Z on is held to the Linux sampler rule. A driver that
applies it to each process (backtest_driver.py) writes the file's load_rule field itself; any
other run needs the log of scripts/linux_sampler.py, started beside measure.sh with --watch
naming measure.sh's process, and is stamped only if linux_sampler.judge() finds the whole run,
from measure.sh's start to a second past its end, clean: otherwise it is refused, to be
withdrawn and rerun.

The driver's own row in results/run_log.md (--run-log) says the file is written, pending the
load stamp. This appends the outcome row under the same run id, naming the file: published, or
withdrawn with the reason, when the stamp or check_results.py refuses it. No row is ever
edited. A file that is not pending, or whose run already has an outcome row for it, is refused
with no row, so that a run once withdrawn is never stamped published after it.

Exit codes: 0 stamped and the file passes check_results.py, 1 refused, 2 bad usage.
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_driver  # noqa: E402
import check_results  # noqa: E402
import linux_sampler  # noqa: E402

START = re.compile(r"^measure\.sh: start .*$", re.MULTILINE)
END = re.compile(r"^measure\.sh: end .*$", re.MULTILINE)
HOST = re.compile(r"\bhost=\S+")
MAX_RSS = re.compile(r"^\s*(\d+)\s+maximum resident set size$", re.MULTILINE)
FOOTPRINT = re.compile(r"^\s*(\d+)\s+peak memory footprint$", re.MULTILINE)
SAMPLE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) (sample|FOREIGN .*)$")
SAMPLE_GAP = 5  # seconds: the sampler writes a line every 2 s, so a longer gap is a stop


def gib(bytes_text):
    return f"{int(bytes_text) / 1024 ** 3:.2f} GiB"


def one_line(pattern, text, name):
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise ValueError(f"wanted one measure.sh {name} line, found {len(matches)}")
    return matches[0]


def seconds(stamp_text):
    return datetime.datetime.strptime(stamp_text, "%Y-%m-%dT%H:%M:%SZ").timestamp()


def sampler_line(sampler_text, start, end):
    """The header value for a sampler that kept watch from start to end. Raises ValueError
    if it did not cover the span or saw another process busy in it."""
    samples, foreign = [], []
    for line in sampler_text.splitlines():
        match = SAMPLE.match(line)
        if match is None:
            raise ValueError(f"the sampler's log has a line it cannot read: {line!r}")
        (samples if match.group(2) == "sample" else foreign).append((match.group(1), line))
    before = [time for time, _ in samples if time <= start]
    after = [time for time, _ in samples if time >= end]
    if not before or not after:
        raise ValueError(f"the sampler did not run over the whole run, {start} to {end}")
    span = [time for time, _ in samples if before[-1] <= time <= after[0]]
    gaps = [seconds(later) - seconds(earlier) for earlier, later in zip(span, span[1:])]
    if max(gaps, default=0) > SAMPLE_GAP:
        raise ValueError(f"the sampler stopped for {max(gaps):.0f} s between {start} and {end}")
    inside = [line for time, line in foreign if start <= time <= end]
    if inside:
        raise ValueError(f"the sampler saw {len(inside)} other busy processes in the run, the "
                         f"first {inside[0]!r}")
    return (f"quiet: the sampler rule was applied: {len(span)} lines from {span[0]} to "
            f"{span[-1]}, no gap over {SAMPLE_GAP} s, bracket the run from {start} to {end}, "
            "and no other process went above 20% CPU in it")


def linux_rule_line(sampler_text, start, end):
    """The load_rule value for a Linux sampler that saw the whole run clean. Raises ValueError
    if it did not. measure.sh's times are whole seconds, so the span runs to a second past its
    end line."""
    lines = sampler_text.splitlines()
    begin = start[:-1] + ".000000Z"
    finish = linux_sampler.stamp_of(linux_sampler.at(end[:-1] + ".000000Z") +
                                    datetime.timedelta(seconds=1))
    why = linux_sampler.judge(lines, begin, finish)
    if why is not None:
        raise ValueError(f"the Linux sampler rule refuses the run: {why}")
    first = next((line for line in lines if line.split()[1:2] == ["start"]), None)
    if first is None:
        raise ValueError("the Linux sampler's log has no start line")
    settings = dict(word.split("=", 1) for word in first.split()[2:])
    return check_results.LINUX_RULE_WHOLE + linux_sampler.summary(
        lines, begin, finish, settings["core"], settings["sibling"])


def stamp(results_text, stderr_text, sampler_text=None):
    """The results text with the lines stamped in. Raises ValueError to refuse."""
    fields = check_results.parse_header(results_text)
    host = fields.get("host")
    if host is None:
        raise ValueError("the results file has no host field")
    start = HOST.sub(f"host={host}", one_line(START, stderr_text, "start"))
    end = HOST.sub(f"host={host}", one_line(END, stderr_text, "end"))
    sampler = None
    linux = None
    if sampler_text is not None:
        opened, closed = check_results.TIMESTAMP.search(start), check_results.TIMESTAMP.search(end)
        if opened is None or closed is None:
            raise ValueError("the measure.sh lines carry no time to hold the sampler to")
        if host == "debian":
            linux = linux_rule_line(sampler_text, opened.group(1), closed.group(1))
        else:
            sampler = sampler_line(sampler_text, opened.group(1), closed.group(1))
    elif check_results.needs_linux_rule(fields) and "load_rule" not in fields:
        raise ValueError("a debian run from 2026-11-13T03:00Z on needs the Linux sampler's log "
                         "(--sampler), or a driver that applied the rule to each process")
    elif check_results.needs_sampler(fields):
        raise ValueError("a macOS run from 2026-11-05 on needs the CPU sampler's log (--sampler)")

    pending = check_results.PENDING
    for name, line in (("measure_load_start", start), ("measure_load_end", end)):
        old = f"- {name}: {pending}"
        if old not in results_text:
            raise ValueError(f"{name} is not pending in the results file")
        results_text = results_text.replace(old, f"- {name}: {line}")
    if sampler is None and check_results.before_sampler_rule(fields):
        sampler = check_results.SAMPLER_BEFORE_RULE
    if sampler is not None:
        results_text = results_text.replace(f"- measure_load_end: {end}",
                                            f"- measure_load_end: {end}\n- sampler: {sampler}")
    if linux is not None:
        if "load_rule" in fields:
            raise ValueError("the file's driver applied the Linux sampler rule already")
        results_text = results_text.replace(f"- measure_load_end: {end}",
                                            f"- measure_load_end: {end}\n- load_rule: {linux}")
    for name, pattern, note in (("peak_rss", MAX_RSS, ", the largest process in the run"),
                                ("peak_footprint", FOOTPRINT,
                                 ", of the driver alone: time sees its direct child")):
        match = pattern.search(stderr_text)
        if match:
            old = f"- {name}: unavailable: /usr/bin/time did not wrap the run"
            results_text = results_text.replace(old, f"- {name}: {gib(match.group(1))}{note}")
    return results_text


def system_of(host):
    """The run's OS, in the run log's word for it, from the file's host label."""
    return {"debian": "Linux", check_results.MAC_HOST: "Darwin"}.get(host)


def settled(log_text, run_id, name):
    """Whether the run log already has an outcome row for this run's file."""
    for line in log_text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 5 and cells[0] == run_id and \
                cells[4].startswith((f"published: {name}", f"withdrawn: {name}")):
            return True
    return False


def outcome(fields, name, why, sampler_given):
    """The outcome row's text: published, or withdrawn with why."""
    if why is None:
        rule = ("; the sampler rule applied from the sampler's log" if sampler_given else "")
        return (f"published: {name}, its load lines stamped from measure.sh's stderr by "
                f"scripts/stamp_load.py{rule}; the file passes check_results.py")
    return (f"withdrawn: {name}: scripts/stamp_load.py refused it: {why}; nothing from it is "
            f"published; the row was written by stamp_load.py")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--stderr", type=Path, required=True)
    parser.add_argument("--sampler", type=Path,
                        help="the CPU sampler's log: a macOS run's, or linux_sampler.py's")
    parser.add_argument("--out", type=Path, help="default: stamp --results in place")
    parser.add_argument("--run-log", type=Path,
                        default=bench_driver.REPO_ROOT / "results" / "run_log.md",
                        help="where the outcome row goes (default results/run_log.md)")
    args = parser.parse_args(argv)
    results_text = args.results.read_text(encoding="utf-8")
    fields = check_results.parse_header(results_text)
    name = (args.out or args.results).name
    pending = all(f"- {field}: {check_results.PENDING}" in results_text
                  for field in ("measure_load_start", "measure_load_end"))
    log_text = args.run_log.read_text(encoding="utf-8") if args.run_log.is_file() else ""
    row = pending and all(fields.get(key) for key in ("run_id", "prediction", "commit"))
    if row and settled(log_text, fields["run_id"], name):
        print(f"stamp_load: refused: {fields['run_id']} already has an outcome row for {name} "
              "in the run log", file=sys.stderr)
        return 1
    why = None
    try:
        sampler = args.sampler.read_text(encoding="utf-8") if args.sampler else None
        stamped = stamp(results_text, args.stderr.read_text(encoding="utf-8", errors="replace"),
                        sampler)
    except ValueError as error:
        print(f"stamp_load: refused: {error}", file=sys.stderr)
        why = str(error)
    else:
        violations = check_results.check_text(stamped)
        for code, detail in violations:
            print(f"stamp_load: FAIL {code}: {detail}", file=sys.stderr)
        if violations:
            why = "; ".join(f"{code}: {detail}" for code, detail in violations)
    if row and not args.run_log.parent.is_dir():
        # A scratch copy of the tree with no results/, as the fault matrix makes: said, not hidden.
        print(f"stamp_load: no folder for the run log at {args.run_log}, so no outcome row",
              file=sys.stderr)
    elif row:
        bench_driver.append_log(args.run_log, fields["run_id"], fields["prediction"],
                                fields["commit"], outcome(fields, name, why, args.sampler),
                                system_of(fields.get("host")))
    if why is not None:
        return 1
    target = args.out or args.results
    target.write_text(stamped, encoding="utf-8")
    print(f"stamp_load: stamped {target.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
