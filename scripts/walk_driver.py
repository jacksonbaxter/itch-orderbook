#!/usr/bin/env python3
"""Times T-BT-3's engine on a ladder of shadow-order schedules, and writes one results file.

    walk_driver.py --bench BENCH_BACKTEST --walks BENCH_WALKS --probe BENCH_PROBE \\
        --input FILE --date YYYYMMDD --prediction P-NNNN --raw-dir DIR \\
        --out results/walks_ladder_HOST_DAY.md [--lives-s 60,600,3600,14400] \\
        [--orders N] [--repetitions N] [--core N] [--sibling N] [--wrapped-by TEXT] \\
        [--allow-dirty]

The question is what share of a message's time the walk of the simulated levels takes
(docs/TASKS.md, "T-BT-3's 100,000 shadow orders"). The schedules differ only in how long each
order lives, so they place the same orders at the same times, and the longer lives hold more
orders, on more levels, at once: the walk steps a message rise, and the rest of the work
barely moves, as the counts show beside the times. The time a message read against the walk
steps a message gives the cost of a step where the engine really takes it; the walk's share
of each schedule's time follows.

On a reported run this is measure.sh's direct child, as scripts/backtest_driver.py is:

    measure.sh taskset -c 4 python3 -B scripts/walk_driver.py ... --core 4 2> STDERR
    python3 -B scripts/stamp_load.py --results FILE --stderr STDERR

Hash the input, which also faults it in; confirm the prediction is on record; run
machine_check.sh, refusing on a failure; count each schedule's walks once with bench_walks,
untimed; then run the repetitions, each a round of one bench_backtest process a schedule,
the order of the schedules turned by one each round so that no schedule always runs first;
refuse if any process refused, if a schedule's processes disagree on any count, if one does
not give its count's event hash, or if the kernel swapped a page out; write the results file
with its load lines pending. Every run gets a row in results/run_log.md. Raw output stays in
--raw-dir, which is not committed.

Exit codes: 0 results written, 1 refused or failed, 2 bad usage, 128 + N stopped by
signal N.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest_driver  # noqa: E402
import bench_driver  # noqa: E402
import check_results  # noqa: E402
import walk_report  # noqa: E402

REPO_ROOT = bench_driver.REPO_ROOT
Refused = bench_driver.Refused
Stopped = bench_driver.Stopped
NS = 1_000_000_000


def rung_args(args, life_s):
    """bench_backtest's arguments for one schedule, in a raw folder of its own."""
    raw = args.raw_dir / f"life-{life_s}s"
    raw.mkdir(parents=True, exist_ok=True)
    return Namespace(**{**vars(args), "workload": "shadow", "raw_dir": raw,
                        "bench_arg": ["--shadow-orders", str(args.orders),
                                      "--shadow-life-ns", str(life_s * NS)]})


def count_walks(args, life_s, commit, digest):
    """One bench_walks process: the schedule's walks, untimed."""
    raw = args.raw_dir / f"life-{life_s}s"
    raw.mkdir(parents=True, exist_ok=True)
    record_path = raw / "walks.json"
    record_path.unlink(missing_ok=True)
    command = [str(args.walks), "--input", str(args.input), "--out", str(record_path),
               "--date", args.date, "--shadow-orders", str(args.orders),
               "--shadow-life-ns", str(life_s * NS), "--commit", commit,
               "--dataset", args.input.name, "--dataset-sha256", digest]
    where = f"counting the walks at a {life_s} s life"
    bench_driver.set_stage(where)
    with open(raw / "walks.log", "w", encoding="utf-8") as log:
        bench_driver.current_child = subprocess.Popen(command, stdout=log,
                                                      stderr=subprocess.STDOUT)
        code = bench_driver.current_child.wait()
        bench_driver.current_child = None
    if bench_driver.swap_watch is not None:
        bench_driver.swap_watch.check(where)
    if not record_path.is_file():
        raise Refused(where, f"exited {code} with no record")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    try:
        walk_report.validate_record(record)
    except walk_report.Refused as refusal:
        raise Refused(where, f"exited {code}: {refusal}") from None
    if code != 0:
        raise Refused(where, f"exited {code}")
    return record


def ns_per_message(record):
    return record["timed"]["ns"] / record["timed"]["messages"]


def timed_rows(lives, counts, timed):
    """The repetitions, then each schedule's summary, from the unrounded times."""
    repetitions, times = [], {life: [] for life in lives}
    for round_index, life, record in timed:
        name = walk_report.schedule_name(counts[life])
        times[life].append(check_results.exact(ns_per_message(record)))
        repetitions.append(f"| {round_index} | {name} | {ns_per_message(record):.2f} "
                           f"| {backtest_driver.message_rate(record):,.0f} "
                           f"| {record['timed']['event_hash']} "
                           f"| {record['peak_rss_bytes'] / 1024 ** 3:.2f} |")
    summary = []
    for life in lives:
        cells = walk_report.summary_cells(times[life])
        summary.append(f"| {walk_report.schedule_name(counts[life])} | " + " | ".join(cells) + " |")
    return repetitions, summary, times


def share_section(text, lives, counts, times):
    """The walk's share: the line through each schedule's unrounded median time against its
    unrounded walk steps a message, beside the cells the tables already in `text` print."""
    steps = {cells[0]: cells[4] for cells in walk_report.table(text, "A message's walks")}
    medians = {cells[0]: cells[1] for cells in walk_report.table(text, "Summary")}
    names = [walk_report.schedule_name(counts[life]) for life in lives]
    slope, intercept, rows = walk_report.share_rows(
        names, [steps[name] for name in names],
        [walk_report.steps_per_message(counts[life]) for life in lives],
        [medians[name] for name in names],
        [check_results.median_range(times[life]) for life in lives])
    lines = ["| Schedule | Walk steps a message | Median ns a message | Line ns a message "
             "| Walk ns a message | Walk's share |", "|---|---|---|---|---|---|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return slope, intercept, rows, lines


def header_lines(args, records, run_id, started, finished, lives):
    first = records[0]
    header = first["header"]
    lines = [f"- {name}: {header[name]}" for name in bench_driver.HEADER_ORDER if name in header]
    lines += [f"- ladder: {len(lives)} schedules of {args.orders:,} shadow orders each, lives "
              f"{', '.join(f'{life:,} s' for life in lives)}, on {first['symbols']}",
              f"- measure_load_start: {check_results.PENDING}",
              f"- measure_load_end: {check_results.PENDING}",
              *bench_driver.peak_lines(records),
              f"- prediction: {args.prediction}",
              f"- run_id: {run_id}",
              f"- repetitions: {args.repetitions}",
              f"- warmup_messages: {first['warmup']['messages']:,}",
              f"- timed_messages: {first['timed']['messages']:,}",
              f"- run_started: {started}",
              f"- run_finished: {finished}",
              "- slope: SLOPE ns a walk step",
              "- intercept: INTERCEPT ns a message",
              "- round_slopes: ROUNDS",
              "- summary: SUMMARY"]
    return lines


def render(args, lives, counts, timed, run_id, started, finished, machine_output, command,
           again=None):
    records = [record for _, _, record in timed]
    host = records[0]["header"]["host"]
    day = records[0]["header"]["dataset"].split(".")[0]
    count_records = [counts[life] for life in lives]
    repetitions, summary, times = timed_rows(lives, counts, timed)
    lines = [f"# {walk_report.LADDER_TITLE}, on {host}, {day}", "",
             "Generated by `scripts/walk_driver.py` from raw records that are not committed. "
             "Do not edit by hand.", ""]
    if again:
        lines += [f"Rendered again from the run's own raw records, unchanged: {again}", ""]
    lines += [f"Host: **{host}**. Numbers from the two hosts are never mixed.", "",
              "T-BT-3's engine and extract, timed by `bench_backtest --workload shadow` on "
              "schedules of shadow orders that differ only in each order's life, so that the "
              "longer lives hold more orders on more simulated levels and every walk from a "
              "side's best level has further to go. Each schedule's walks were counted once "
              "by `bench_walks` on this host, untimed, and every timed process gave its count's "
              "event hash. The line through each schedule's median time a message against its "
              "walk steps a message has the cost of one walk step for its slope, measured where "
              "the engine takes the step; a schedule's walk time is that slope times its steps, "
              "and the walk's share is that over its median. The line says nothing about the "
              "fixed part of a walk, the side's head and the branch out, which every schedule "
              "pays about once a message and which any structure keyed by price would still pay.",
              "", "## Header", ""]
    lines += header_lines(args, records, run_id, started, finished, lives)
    lines += ["", "## Summary", "",
              "Each schedule's time a message over its repetitions, one fresh bench_backtest "
              "process each: a warm-up pass, then the timed pass, the engine's `run()` over the "
              "whole extract.", "",
              "| Schedule, ns a message | Median | Min | Max | CV |", "|---|---|---|---|---|"]
    lines += summary
    lines += [""] + walk_report.count_sections(count_records)
    lines += ["", "## Repetitions", "",
              "In the order they ran. Each round runs every schedule once, starting one "
              "schedule later than the round before.", "",
              "| Round | Schedule | ns a message | Messages/s | Event hash | Peak RSS GiB |",
              "|---|---|---|---|---|---|"]
    lines += repetitions
    if bench_driver.swap_watch is not None:
        lines += ["", bench_driver.swap_watch.line()]
    lines += ["", "## machine_check output", "", "```", machine_output, "```", "",
              "## Commands", "", "```", command, "```", ""]
    text = "\n".join(lines)
    slope, intercept, rows, share_lines = share_section(text, lives, counts, times)
    text = text.replace("\n## Repetitions\n", "\n## The walk's share\n\n" + "\n".join(
        ["Least squares through every schedule's median against its walk steps a message, "
         f"over {len(rows)} schedules: {slope} ns a walk step, {intercept} ns a message at "
         "none.", ""] + share_lines) + "\n\n## Repetitions\n", 1)
    xs = {walk_report.schedule_name(counts[life]): walk_report.steps_per_message(counts[life])
          for life in lives}
    rounds = walk_report.round_rows(
        [(str(round_index), walk_report.schedule_name(counts[life]),
          check_results.exact(ns_per_message(record))) for round_index, life, record in timed],
        xs)
    text = text.replace("\n## Repetitions\n", "\n## Each round's line\n\n" + "\n".join(
        ["The same least squares through each round's own times, one process a schedule: how "
         "far the slope moves between rounds.", "",
         "| Round | Slope, ns a walk step | Intercept, ns a message |", "|---|---|---|"]
        + [f"| {name} | {slope_cell} | {intercept_cell} |"
           for name, slope_cell, intercept_cell in rounds]) + "\n\n## Repetitions\n", 1)
    text = text.replace("- round_slopes: ROUNDS",
                        f"- round_slopes: {walk_report.slope_range(rounds)}", 1)
    text = text.replace("- slope: SLOPE ns", f"- slope: {slope} ns", 1)
    text = text.replace("- intercept: INTERCEPT ns", f"- intercept: {intercept} ns", 1)
    return text.replace("- summary: SUMMARY", "- summary: " + walk_report.ladder_summary(
        slope, rows), 1)


def relative_command(args):
    def shown(path):
        path = Path(os.path.abspath(path))
        try:
            return str(path.relative_to(REPO_ROOT))
        except ValueError:
            return f"<raw>/{path.name}"
    words = ["python3", "-B", "scripts/walk_driver.py", "--bench", shown(args.bench),
             "--walks", shown(args.walks), "--probe", shown(args.probe),
             "--input", shown(args.input), "--date", args.date,
             "--prediction", args.prediction, "--lives-s", args.lives_s,
             "--orders", str(args.orders), "--repetitions", str(args.repetitions),
             "--raw-dir", "<raw>", "--out", shown(args.out)]
    for flag, value in (("--core", args.core), ("--sibling", args.sibling)):
        if value is not None:
            words += [flag, str(value)]
    text = " ".join(shlex.quote(word) if "<" not in word else word for word in words)
    return f"{args.wrapped_by} {text}" if args.wrapped_by else text


def run(args, run_id, started, commit):
    lives = args.lives
    bench_driver.swap_watch = bench_driver.SwapWatch(args.vmstat)
    bench_driver.set_stage("hashing the input")
    digest = bench_driver.hash_and_fault_in(args.input)
    bench_driver.set_stage("checking the prediction")
    code = subprocess.run([sys.executable, "-B", str(REPO_ROOT / "scripts" / "check_predictions.py"),
                           "--root", str(args.predictions_root), "--require", args.prediction],
                          capture_output=True, text=True, check=False)
    if code.returncode != 0:
        raise Refused("prediction", code.stdout.strip())
    bench_driver.set_stage("machine_check")
    machine_output = bench_driver.machine_check(args)
    counts = {life: count_walks(args, life, commit, digest) for life in lives}
    timed = []
    for round_index in range(1, args.repetitions + 1):
        turn = (round_index - 1) % len(lives)
        for life in lives[turn:] + lives[:turn]:
            record = backtest_driver.run_process(rung_args(args, life), round_index, commit, digest)
            backtest_driver.validate_run(record, "shadow")
            if record["timed"]["event_hash"] != counts[life]["engine"]["event_hash"] or \
                    record["timed"]["messages"] != counts[life]["engine"]["messages"]:
                raise Refused(f"round {round_index}, a {life} s life",
                              "the timed process did not give its count's event hash")
            timed.append((round_index, life, record))
    for life in lives:
        backtest_driver.validate_together([record for _, at, record in timed if at == life])
    bench_driver.set_stage("rendering the results file")
    text = render(args, lives, counts, timed, run_id, started, bench_driver.utc_now(),
                  machine_output, relative_command(args))
    violations = [found for found in check_results.check_text(text)
                  if found[0] != "load-not-stamped"]
    if violations:
        raise Refused("results", f"{args.out.name}: " + "; ".join(
            f"{code}: {detail}" for code, detail in violations))
    bench_driver.hold_signals()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    return args.out


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bench", type=Path, required=True)
    parser.add_argument("--walks", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lives-s", default="60,600,3600,14400",
                        help="each schedule's order life in seconds, T-BT-3's 60 first")
    parser.add_argument("--orders", type=int, default=100_000)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--core", type=int)
    parser.add_argument("--sibling", type=int)
    parser.add_argument("--wrapped-by", help="what the run was started under, for the record")
    parser.add_argument("--allow-dirty", action="store_true", help="for tests, not for a report")
    parser.add_argument("--bench-arg", action="append", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--predictions-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--run-log", type=Path, default=REPO_ROOT / "results" / "run_log.md",
                        help=argparse.SUPPRESS)
    parser.add_argument("--vmstat", type=Path, default=Path("/proc/vmstat"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.repetitions < 3:
        parser.error("median, min, max and CV need at least 3 repetitions")
    if len(args.date) != 8 or not args.date.isdigit():
        parser.error("--date is YYYYMMDD")
    try:
        args.lives = [int(word) for word in args.lives_s.split(",")]
    except ValueError:
        parser.error("--lives-s is whole seconds, comma-separated")
    if len(args.lives) < 3 or len(set(args.lives)) != len(args.lives) or min(args.lives) <= 0 or \
            args.lives != sorted(args.lives):
        parser.error("--lives-s needs at least 3 different lives, rising, for a line")
    if args.orders <= 0:
        parser.error("--orders must be positive")
    return args


def main(argv=None):
    args = parse_args(argv)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, bench_driver.forward_signal)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    started = bench_driver.utc_now()
    run_id = f"{args.prediction}-{started.replace(':', '').replace('-', '')}"
    commit = "unknown"
    try:
        commit = bench_driver.git_commit(REPO_ROOT, args.allow_dirty)
        written = run(args, run_id, started, commit)
    except Stopped as stop:
        bench_driver.stop_child()
        name = signal.Signals(stop.signum).name
        print(f"walk_driver: stopped by {name} at {bench_driver.stage}", file=sys.stderr)
        bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                                f"stopped by {name} at {bench_driver.stage}; nothing from it is "
                                "published; the row was written by the driver as it stopped")
        return 128 + stop.signum
    except Refused as refusal:
        bench_driver.hold_signals()
        print(f"walk_driver: refused at {refusal.stage}: {refusal}", file=sys.stderr)
        bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                                f"refused at {refusal.stage}: {refusal}")
        return 1
    except Exception as error:  # Last, so that no run ends without its row.
        return bench_driver.log_failure("walk_driver", args.run_log, run_id, args.prediction, commit,
                                        error)
    bench_driver.hold_signals()
    bench_driver.append_written(args.run_log, run_id, args.prediction, commit, [written])
    print(f"walk_driver: wrote {written}, load lines pending. Now run scripts/stamp_load.py.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
