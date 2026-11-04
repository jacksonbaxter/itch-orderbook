#!/usr/bin/env python3
"""Runs book designs in fresh processes and writes one results file for each.

    bench_driver.py --bench BENCH_REPLAY --probe BENCH_PROBE --input FILE \\
        --prediction P-NNNN --raw-dir DIR --out results/bench_NAME.md \\
        [--design NAME]... [--input-mode ram|mapped] [--track-top] \\
        [--repetitions N] [--core N] [--sibling N] [--title TEXT] [--note TEXT] \\
        [--orders N] [--levels N] [--wrapped-by TEXT] [--allow-dirty] [--bench-arg ARG]...

On a reported run this is measure.sh's direct child, and it runs one process at a time,
so that signalling it stops everything:

    measure.sh [taskset -c 4] python3 -B scripts/bench_driver.py ... 2> STDERR
    python3 -B scripts/stamp_load.py --results FILE --stderr STDERR

The order is: hash the input, which also faults it in; confirm the prediction is on
record; run machine_check.sh, refusing on a failure; run the repetitions; refuse if any
one of them refused, or if the kernel swapped any page out since the run began; write
the results files with their load lines pending. Figures are
the median of the repetitions with min, max and CV beside it, never the best of them. A
percentile is shown only when every repetition resolved it, and as below floor otherwise.

Each repetition runs every design in turn, so that the designs share the machine's state
as closely as an unpinned host allows. Each design takes two processes a repetition: one
for the warm-up and the timed passes, and one for the untimed pass of counters and memory,
which builds no other book first, so that the memory it reports is that book's own. With
more than one design, --out must hold `{design}`, which becomes each design's name. A
fast design's file compares its throughput with every map design in the same run,
repetition by repetition (T-DESIGN-1), and every file holds each target beside the bar.

Every run gets a row in results/run_log.md, whatever became of it, a run stopped by
SIGTERM, SIGINT or SIGHUP included: the driver passes the signal to the process running,
waits for it, writes the row with where the run was, and exits 128 plus the signal.
Raw output stays in --raw-dir, which is not committed.

Exit codes: 0 results written, 1 refused or failed, 2 bad usage, 128 + N stopped by
signal N.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import mmap
import os
import platform
import shlex
import signal
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
HEADER_ORDER = ("host", "cpu", "kernel", "compiler", "flags", "commit", "dataset",
                "dataset_sha256", "timer", "quantum", "pair", "floor", "pinning", "migrations",
                "core", "governor", "frequency", "turbo", "smt", "isolation", "counters", "rdpmc",
                "page_size", "residency", "design", "input", "top_of_book")
METRICS = (("Throughput, messages/s", "messages_per_s", ",.0f"),
           ("Latency-pass rate, messages/s", "latency_messages_per_s", ",.0f"),
           ("Mean span of apply(), ns", "mean_ns", ".2f"),
           ("Instrumentation ratio, latency pass over throughput pass", "ratio", ".4f"))
BOOK_METRICS = (("Mean service time per message, ns", "service_ns", ".2f"),
                ("Batch mean per book message, ns", "batch_ns", ".2f"),
                ("Book memory, MiB", "book_mib", ",.1f"))
MAP_DESIGNS = ("map-fresh", "map-aged")
FAST_DESIGNS = ("hash-linked", "hash-flat", "paged-linked", "paged-flat")
OPERATIONS = ("add", "delete", "replace", "reduce")
OPERATION_PERCENTILES = (50.0, 99.0, 99.9)
BELOW_FLOOR = "below floor"


class Refused(Exception):
    """The run must not publish. The message is the reason on record."""

    def __init__(self, stage, reason):
        super().__init__(reason)
        self.stage = stage


class Stopped(BaseException):
    """A signal stopped the run. Not an Exception, so that nothing on the way catches it."""

    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


current_child = None
stage = "start"  # Where the run is, for the row of a run a signal stops.
stopping = False  # Set once a signal has stopped the run, or once its row is being written.
swap_watch = None  # The run's SwapWatch, so that every process is checked against it.
CHILD_GRACE_S = 5  # measure.sh sends SIGKILL 10 s after its SIGTERM.


def forward_signal(signum, _frame):
    """measure.sh signals only its direct child, so this stops the repetition too."""
    global stopping
    if current_child is not None and current_child.poll() is None:
        current_child.send_signal(signum)
    if not stopping:
        stopping = True
        raise Stopped(signum)


def stop_child():
    """Waits for the process the signal was passed to, and kills it if it will not stop."""
    if current_child is None or current_child.poll() is not None:
        return
    try:
        current_child.wait(timeout=CHILD_GRACE_S)
    except subprocess.TimeoutExpired:
        current_child.kill()
        current_child.wait()


class SwapWatch:
    """The kernel's count of pages swapped out, from /proc/vmstat, at the start of the run.

    A run must not push the host's other processes into swap, so any page swapped out
    after the start, by whatever process, refuses the run at the next check. A host with
    no /proc/vmstat, macOS among them, is not checked, and the results file says so."""

    def __init__(self, path):
        self.path = path
        self.start = self.read()
        self.end = self.start

    def read(self):
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return None
        for line in text.splitlines():
            name, _, value = line.partition(" ")
            if name == "pswpout":
                return int(value)
        return None

    def check(self, where):
        if self.start is None:
            return
        self.end = self.read()
        if self.end is None or self.end != self.start:
            moved = "could not be read" if self.end is None else (
                f"moved by {self.end - self.start:,} pages")
            raise Refused(where, f"the kernel's pswpout {moved} since the run began, by the "
                                 "end of this process: the run does not fit beside the host's "
                                 "other work")

    def line(self):
        if self.start is None:
            return "Swap-outs are not read on this host: it has no /proc/vmstat."
        return (f"The kernel's pswpout was {self.start:,} at the start of the run and "
                f"{self.end:,} after its last process: no page of any process was swapped out "
                "while it ran, and the driver refuses a run where one is.")


def utc_now():
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_commit(root, allow_dirty):
    """The commit under test: from git, or the COMMIT file a debian sync leaves."""
    if (root / ".git").exists():
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "bench",
                                 "include", "src", "tools", "scripts", "cmake", "CMakeLists.txt"],
                                capture_output=True, text=True, check=True).stdout
        if status and not allow_dirty:
            raise Refused("commit", "the tree has uncommitted changes")
        return f"{head} ({'with uncommitted changes' if status else 'clean'})"
    stamp = root / "COMMIT"
    if stamp.is_file():
        return f"{stamp.read_text(encoding='utf-8').strip()[:12]} (exported by debian_sync.sh)"
    return "unavailable: neither git nor a COMMIT file"


def set_stage(text):
    global stage
    stage = text


def hash_and_fault_in(path):
    """SHA-256 through a mapping, so that the pages stay in the cache for the run."""
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        if os.fstat(file.fileno()).st_size == 0:
            raise Refused("input", "the input is empty")
        with mmap.mmap(file.fileno(), 0, prot=mmap.PROT_READ) as mapped:
            digest.update(mapped)
    return digest.hexdigest()


def machine_check(args):
    command = ["bash", str(REPO_ROOT / "scripts" / "machine_check.sh"), "--input", str(args.input),
               "--probe", str(args.probe)]
    if args.core is not None:
        command += ["--core", str(args.core)]
    if args.sibling is not None:
        command += ["--sibling", str(args.sibling)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise Refused("machine_check", result.stdout.strip().splitlines()[-1]
                      if result.stdout.strip() else f"exited {result.returncode}")
    return result.stdout.strip()


def run_process(args, index, commit, digest, design, passes):
    """One bench_replay process: the timed passes, or the untimed one."""
    global current_child
    stem = f"{design}-repetition-{index}-{passes}" if design else f"repetition-{index}-{passes}"
    record_path = args.raw_dir / f"{stem}.json"
    log_path = args.raw_dir / f"{stem}.log"
    record_path.unlink(missing_ok=True)
    command = [str(args.bench), "--input", str(args.input), "--out", str(record_path),
               "--commit", commit, "--dataset", args.input.name, "--dataset-sha256", digest,
               "--passes", passes, "--input-mode", args.input_mode,
               "--track-top", "on" if args.track_top else "off"]
    if design:
        command += ["--design", design]
    if args.core is not None:
        command += ["--core", str(args.core)]
    if args.orders is not None:
        command += ["--orders", str(args.orders)]
    if args.levels is not None:
        command += ["--levels", str(args.levels)]
    command += args.bench_arg
    where = f"repetition {index}" + (f", {design}" if design else "")
    set_stage(f"{where}, {passes} process")
    with open(log_path, "w", encoding="utf-8") as log:
        current_child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        code = current_child.wait()
        current_child = None
    if swap_watch is not None:
        swap_watch.check(where)
    if not record_path.is_file():
        raise Refused(where, f"exited {code} with no record")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if code != 0 or record["refusals"]:
        raise Refused(where, f"exited {code}: {'; '.join(record['refusals']) or 'no reason given'}")
    return record


def run_repetition(args, index, commit, digest, design=None):
    """The timed record, with the counters and memory of its own untimed process."""
    timed = run_process(args, index, commit, digest, design, "timed")
    untimed = run_process(args, index, commit, digest, design, "untimed")
    memory = untimed.get("memory", {})
    if not memory.get("complete") or not memory.get("first_book"):
        raise Refused(f"repetition {index}", "the untimed process's memory figure is not the "
                                             "first book's, or a read of it failed")
    timed["counters"] = untimed["counters"]
    timed["memory"] = memory
    for name in ("counters", "rdpmc"):
        timed["header"][name] = untimed["header"][name]
    return timed


def validate_run(record):
    """What a record must show for its numbers to mean anything."""
    warmup = record["warmup"]["messages"]
    for name in ("throughput", "latency_pass"):
        if record[name]["messages"] != warmup:
            raise Refused("record", f"{name} timed {record[name]['messages']} messages, "
                                    f"one pass is {warmup}: the warm-up leaked in")
    if record["latency"]["samples"] != warmup:
        raise Refused("record", f"{record['latency']['samples']} latency samples for {warmup} "
                                "messages")
    floor = record["timer"]["floor_ns"]
    entries = [("", entry) for entry in record["latency"]["percentiles"]]
    for operation in record["latency"].get("operations", []):
        entries += [(f"{operation['op']} ", entry) for entry in operation["percentiles"]]
        mix = record.get("mix", {})
        if operation["op"] in mix and operation["samples"] != mix[operation["op"]]:
            raise Refused("record", f"{operation['samples']} {operation['op']} spans for "
                                    f"{mix[operation['op']]} {operation['op']} messages")
    for name, entry in entries:
        if entry["value_ns"] is not None and entry["value_ns"] < floor:
            raise Refused("record", f"{name}p{entry['percentile']} is below the {floor} ns floor")
    for status in record["counters"]["status"]:
        if status["state"] == "measured" and status["ratio"] < 1.0:
            raise Refused("record", f"{status['name']} is called measured at ratio "
                                    f"{status['ratio']}")


def spread(values):
    """Median, min, max and coefficient of variation."""
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    return (statistics.median(values), min(values), max(values),
            deviation / mean if mean else 0.0)


def validate_together(runs):
    """What every design of one run must agree on: the same day, and the same top of book."""
    first = next(iter(runs.values()))[0]
    for design, records in runs.items():
        for record in records:
            if record.get("mix") != first.get("mix"):
                raise Refused("record", f"{design} counted another mix of messages")
            for name in ("throughput", "latency_pass"):
                if record[name].get("top_changes") != first[name].get("top_changes"):
                    raise Refused("record", f"{design} saw {record[name].get('top_changes')} "
                                            f"moves of the top in its {name}, against "
                                            f"{first[name].get('top_changes')}")


def is_book_record(record):
    return "mix" in record and "memory" in record


def metric_values(records, key):
    if key == "messages_per_s":
        return [record["throughput"]["messages_per_s"] for record in records]
    if key == "latency_messages_per_s":
        return [record["latency_pass"]["messages_per_s"] for record in records]
    if key == "mean_ns":
        return [record["latency"]["mean_ns"] for record in records]
    if key == "service_ns":
        return [1e9 * record["throughput"]["seconds"] / record["throughput"]["messages"]
                for record in records]
    if key == "batch_ns":
        return [1e9 * record["throughput"]["seconds"] / record["mix"]["book_messages"]
                for record in records]
    if key == "book_mib":
        return [record["memory"]["book_bytes"] / 1024 ** 2 for record in records]
    return [record["instrumentation_ratio"] for record in records]


def summary_rows(records):
    rows = []
    metrics = METRICS + (BOOK_METRICS if is_book_record(records[0]) else ())
    for label, key, shape in metrics:
        median, low, high, variation = spread(metric_values(records, key))
        rows.append(f"| {label} | {median:{shape}} | {low:{shape}} | {high:{shape}} "
                    f"| {100 * variation:.2f}% |")
    return rows


def latency_rows(records):
    rows = []
    positions = [entry["percentile"] for entry in records[0]["latency"]["percentiles"]]
    pairs = [record["timer"]["pair_ns"] for record in records]
    book = is_book_record(records[0])
    for position, percentile in enumerate(positions):
        values = [record["latency"]["percentiles"][position]["value_ns"] for record in records]
        rows.append(percentile_row(f"p{percentile:g}", values, pairs if book else None))
    rows.append(percentile_row("max", [record["latency"]["max_ns"] for record in records],
                               pairs if book else None))
    return rows


def percentile_row(name, values, pairs=None):
    """A row of a percentile across repetitions. With pairs, the median with each
    repetition's own empty pair taken off goes in a last column."""
    resolved = [value for value in values if value is not None]
    if len(resolved) != len(values):
        if pairs is None:
            return (f"| {name} | unresolved | | | | resolved in {len(resolved)} of "
                    f"{len(values)} repetitions |")
        return (f"| {name} | {BELOW_FLOOR} | | | | resolved in {len(resolved)} of {len(values)} "
                f"repetitions | {BELOW_FLOOR} |")
    median, low, high, variation = spread(resolved)
    row = f"| {name} | {median:.1f} | {low:.1f} | {high:.1f} | {100 * variation:.2f}% | resolved |"
    if pairs is not None:
        row += f" {subtracted(values, pairs):.1f} |"
    return row


def subtracted(values, pairs):
    """The median of the values, each less its own repetition's measured empty pair."""
    return statistics.median(value - pair for value, pair in zip(values, pairs))


def operation_value(record, operation, percentile):
    entry = next(item for item in record["latency"]["operations"] if item["op"] == operation)
    return next(item["value_ns"] for item in entry["percentiles"]
                if item["percentile"] == percentile)


def operation_rows(records):
    """T-LAT-1: each operation's spans, at the percentiles the target names."""
    rows = []
    pairs = [record["timer"]["pair_ns"] for record in records]
    for operation in OPERATIONS:
        for percentile in OPERATION_PERCENTILES:
            values = [operation_value(record, operation, percentile) for record in records]
            row = percentile_row(f"p{percentile:g}", values, pairs)
            rows.append(f"| {operation} | {row[2:]}")
        means = [next(item["mean_ns"] for item in record["latency"]["operations"]
                      if item["op"] == operation) for record in records]
        median, low, high, variation = spread(means)
        rows.append(f"| {operation} | mean | {median:.1f} | {low:.1f} | {high:.1f} "
                    f"| {100 * variation:.2f}% | a mean, finer than the quantum | |")
    return rows


def operation_counts(record):
    return ", ".join(f"{record['mix'][operation]:,} {operation}" for operation in OPERATIONS)


def counter_rows(records):
    rows = []
    for position, status in enumerate(records[0]["counters"]["status"]):
        entries = [record["counters"]["status"][position] for record in records]
        worst = min(entries, key=lambda entry: entry["ratio"])
        counts = [entry["value"] for entry in entries]
        state = next((entry["state"] for entry in entries if entry["state"] != "measured"),
                     "measured")
        detail = "; ".join(sorted({entry["reason"] for entry in entries if entry["reason"]}))
        rows.append(f"| {status['name']} | {state} | {worst['ratio']:.4f} "
                    f"| {statistics.median(counts):,.0f} | {detail} |")
    return rows


def per_message_rows(records):
    rows = []
    published = [record for record in records if record["counters"]["per_message_published"]]
    if len(published) != len(records) or not records[0]["counters"]["per_message"]:
        return rows
    for position, entry in enumerate(records[0]["counters"]["per_message"]):
        means = [record["counters"]["per_message"][position]["mean"] for record in records]
        median, low, high, variation = spread(means)
        rows.append(f"| {entry['name']} | {median:.3f} | {low:.3f} | {high:.3f} "
                    f"| {100 * variation:.2f}% |")
    return rows


def agreement_rows(records):
    """rdpmc against read() over the whole pass, event by event, with the widest gap seen."""
    rows = []
    for position, entry in enumerate(records[0]["counters"].get("agreement_events", [])):
        entries = [record["counters"]["agreement_events"][position] for record in records]
        gaps = [abs(item["read"] - item["rdpmc"]) for item in entries]
        widest = max(gaps)
        base = entries[gaps.index(widest)]["read"]
        share = 100 * widest / base if base else 0.0
        summed = [next((item["sum"] for item in record["counters"]["per_message"]
                        if item["name"] == entry["name"]), None) for record in records]
        apply_sum = "" if None in summed else f"{statistics.median(summed):,.0f}"
        rows.append(f"| {entry['name']} | {statistics.median(e['read'] for e in entries):,.0f} "
                    f"| {statistics.median(e['rdpmc'] for e in entries):,.0f} "
                    f"| {widest:,} | {share:.4f}% | {apply_sum} |")
    return rows


def peak_lines(records):
    """Linux has no /usr/bin/time here, and a wrapper would break measure.sh's signal path."""
    if not records[0]["header"]["kernel"].startswith("Linux"):
        return ["- peak_rss: unavailable: /usr/bin/time did not wrap the run",
                "- peak_footprint: unavailable: /usr/bin/time did not wrap the run"]
    peak = max(record["peak_rss_bytes"] for record in records) / 1024 ** 3
    return [f"- peak_rss: {peak:.2f} GiB, getrusage of the benchmark, largest repetition",
            "- peak_footprint: unavailable: a macOS figure, with no Linux equivalent"]


def repetition_rows(records):
    rows = []
    for index, record in enumerate(records, start=1):
        peak = record["peak_rss_bytes"] / 1024 ** 3
        rows.append(f"| {index} | {record['throughput']['messages_per_s']:,.0f} "
                    f"| {record['instrumentation_ratio']:.4f} "
                    f"| {record['timer']['quantum_ns']:.3f} | {record['timer']['pair_ns']:.3f} "
                    f"| {record['migrations']} | {peak:.2f} |")
    return rows


def header_lines(records, args, run_id, started, finished):
    header = dict(records[0]["header"])
    lines = [f"- {name}: {header[name]}" for name in HEADER_ORDER if name in header]
    warmup = records[0]["warmup"]["messages"]
    lines += [f"- measure_load_start: {check_results.PENDING}",
              f"- measure_load_end: {check_results.PENDING}",
              *peak_lines(records),
              f"- prediction: {args.prediction}",
              f"- run_id: {run_id}",
              f"- repetitions: {len(records)}",
              f"- warmup_messages: {warmup:,}",
              f"- timed_messages: {records[0]['throughput']['messages']:,}"]
    if is_book_record(records[0]):
        lines.append(f"- book_messages: {records[0]['mix']['book_messages']:,}")
    lines += [f"- run_started: {started}",
              f"- run_finished: {finished}"]
    return lines


def design_of(record):
    return record["header"]["design"].split(":")[0]


def median_of(records, key):
    return statistics.median(metric_values(records, key))


def all_message(records, percentile):
    """The median raw and pair-subtracted value of one all-message percentile, or Nones."""
    values = [next(entry["value_ns"] for entry in record["latency"]["percentiles"]
                   if entry["percentile"] == percentile) for record in records]
    if None in values:
        return None, None
    return statistics.median(values), subtracted(values, [r["timer"]["pair_ns"] for r in records])


def operation_median(records, operation, percentile):
    values = [operation_value(record, operation, percentile) for record in records]
    return None if None in values else statistics.median(values)


def paired_ratios(records, baseline):
    """Each repetition's rate over the baseline's in the same repetition."""
    return [mine["throughput"]["messages_per_s"] / theirs["throughput"]["messages_per_s"]
            for mine, theirs in zip(records, baseline)]


# Each target: its bar, the hardware it was set on, and whether that bar holds on any host.
# A bar set on x86 Linux is not passed or failed by a macOS figure: the row says the host
# differs and gives the measured gap, signed.
X86_BARS = {"T-BOOK-1", "T-BOOK-2", "T-LAT-1", "T-LAT-2", "T-LAT-4", "T-LAT-5"}
SET_ON = {
    "T-BOOK-1": "Ryzen 7 9700X, x86 Linux",
    "T-BOOK-2": "Ryzen 7 9700X, x86 Linux",
    "T-BOOK-3": "a size, comparable on any host",
    "T-BOOK-4": "Apple M2 Pro, macOS, day 01302019",
    "T-LAT-1": "Ryzen 7 9700X, x86 Linux",
    "T-LAT-2": "Core i5-12400, x86 Linux under WSL2",
    "T-LAT-3": "a rate budget, for every host",
    "T-LAT-4": "Ryzen 9 5950X, x86 Linux",
    "T-LAT-5": "Core i7-13650HX, x86",
    "T-DESIGN-1": "a ratio on one host, comparable on any host",
}
T_LAT_1 = {"add": (60, 447, 683), "delete": (70, 599, 768), "replace": (110, 709, 945),
           "reduce": (50, 548, 734)}
MEGA = 1e6
GIB_LIMIT = 1.4e9


def judge(target, host, parts):
    """The measured and verdict cells. Each part is (label, value or None, bar, sense, unit),
    where None is a percentile below the floor and sense is "max" or "min"."""
    measured = "; ".join(f"{label} {BELOW_FLOOR}" if value is None else f"{label} {value:,.2f}{unit}"
                         for label, value, _, _, unit in parts)
    gaps = []
    missed = []
    below = [label for label, value, _, _, _ in parts if value is None]
    for label, value, bar, sense, unit in parts:
        if value is None:
            continue
        gaps.append(f"{label} {value - bar:+,.2f}{unit}")
        if (sense == "max" and value > bar) or (sense == "min" and value < bar):
            missed.append(f"{label} by {abs(value - bar):,.2f}{unit}")
    if target in X86_BARS and host == check_results.MAC_HOST:
        verdict = ("host differs: a macOS figure against a bar set on x86 Linux, neither a "
                   f"pass nor a fail; against the bar: {', '.join(gaps) or 'nothing resolved'}")
        if below:
            verdict += f"; {', '.join(below)} {BELOW_FLOOR}, not measurable here"
        return measured, verdict
    if missed:
        verdict = f"missed: {', '.join(missed)}"
    elif below:
        verdict = (f"{BELOW_FLOOR}: {', '.join(below)} cannot be resolved on this host; "
                   "every resolved figure is inside the bar")
    else:
        verdict = "met"
    return measured, verdict


def target_rows(records, runs):
    """Every target beside its bar, measured from this file's design in this run."""
    first = records[0]
    host = first["header"]["host"]
    design = design_of(first)
    ram = first["header"]["input"].startswith("ram:")
    tracked = first["header"]["top_of_book"].startswith("tracked")
    rows = []

    def row(target, bar, measured, verdict):
        rows.append(f"| {target} | {bar} | {SET_ON[target]} | {measured} | {verdict} |")

    def absent(target, bar, why):
        row(target, bar, "", f"not measured in this run: {why}")

    if design in MAP_DESIGNS:
        why = "the targets are the fast book's, and this is the baseline it is measured against"
        for target, bar in (("T-BOOK-1", "≥ 16.4 M msg/s"), ("T-BOOK-2", "≥ 12.5 M msg/s"),
                            ("T-BOOK-3", "≤ 1.4 GB"), ("T-BOOK-4", "≥ 14.4 M msg/s"),
                            ("T-LAT-1", "per operation"), ("T-LAT-2", "p50 ≤ 121 ns"),
                            ("T-LAT-3", "mean ≤ 393 ns"), ("T-LAT-4", "p50 ≤ 100 ns"),
                            ("T-LAT-5", "p50 ≤ 127 ns"), ("T-DESIGN-1", "≥ 4.0×")):
            absent(target, bar, why)
        return rows

    rate = median_of(records, "messages_per_s") / MEGA
    if ram and not tracked:
        row("T-BOOK-1", "≥ 16.4 M msg/s",
            *judge("T-BOOK-1", host, [("rate", rate, 16.4, "min", " M msg/s")]))
    else:
        absent("T-BOOK-1", "≥ 16.4 M msg/s", "needs the input in memory and no top-of-book tracking")
    if not ram and tracked:
        row("T-BOOK-2", "≥ 12.5 M msg/s",
            *judge("T-BOOK-2", host, [("rate", rate, 12.5, "min", " M msg/s")]))
    else:
        absent("T-BOOK-2", "≥ 12.5 M msg/s", "needs the input mapped and the top of book tracked")
    book_gb = statistics.median(record["memory"]["book_bytes"] for record in records) / 1e9
    row("T-BOOK-3", "≤ 1.4 GB of book",
        *judge("T-BOOK-3", host, [("book", book_gb, GIB_LIMIT / 1e9, "max", " GB")]))
    if first["header"]["dataset"].startswith("01302019") and host == check_results.MAC_HOST:
        row("T-BOOK-4", "≥ 14.4 M msg/s on 01302019",
            *judge("T-BOOK-4", host, [("rate", rate, 14.4, "min", " M msg/s")]))
    else:
        absent("T-BOOK-4", "≥ 14.4 M msg/s on 01302019", "set on day 01302019 on macOS only")
    parts = []
    for operation, bars in T_LAT_1.items():
        for percentile, bar in zip(OPERATION_PERCENTILES, bars):
            parts.append((f"{operation} p{percentile:g}",
                          operation_median(records, operation, percentile), bar, "max", " ns"))
    row("T-LAT-1", "add 60, 447, 683; delete 70, 599, 768; replace 110, 709, 945; reduce 50, "
                   "548, 734 ns at p50, p99, p99.9, raw", *judge("T-LAT-1", host, parts))
    raw = {p: all_message(records, p)[0] for p in (50.0, 99.0, 99.9)}
    net = {p: all_message(records, p)[1] for p in (50.0, 99.0, 99.9)}
    row("T-LAT-2", "p50 ≤ 121, p99 ≤ 552, p99.9 ≤ 874 ns, raw",
        *judge("T-LAT-2", host, [(f"p{p:g}", raw[p], bar, "max", " ns")
                                 for p, bar in zip(raw, (121, 552, 874))]))
    row("T-LAT-3", "mean service ≤ 393 ns a message, p99.9 ≤ 750 ns",
        *judge("T-LAT-3", host, [("mean", median_of(records, "service_ns"), 393, "max", " ns"),
                                 ("p99.9", raw[99.9], 750, "max", " ns")]))
    row("T-LAT-4", "p50 ≤ 100, p99 ≤ 620, p99.9 ≤ 1,080 ns, pair subtracted; batch mean ≤ "
                   "142.36 ns a book message",
        *judge("T-LAT-4", host, [*((f"p{p:g}", net[p], bar, "max", " ns")
                                   for p, bar in zip(net, (100, 620, 1080))),
                                 ("batch mean", median_of(records, "batch_ns"), 142.36, "max",
                                  " ns")]))
    row("T-LAT-5", "p50 ≤ 127, p99 ≤ 446, p99.9 ≤ 778 ns, raw",
        *judge("T-LAT-5", host, [(f"p{p:g}", raw[p], bar, "max", " ns")
                                 for p, bar in zip(raw, (127, 446, 778))]))
    maps = [name for name in MAP_DESIGNS if name in runs]
    if maps:
        parts = [(f"over {name}", statistics.median(paired_ratios(records, runs[name])), 4.0,
                  "min", "×") for name in maps]
        row("T-DESIGN-1", "≥ 4.0× the map book's throughput, same run", *judge("T-DESIGN-1", host,
                                                                                parts))
    else:
        absent("T-DESIGN-1", "≥ 4.0× the map book's throughput, same run",
               "no map design ran beside this one")
    return rows


def design_rows(records, runs):
    rows = []
    for name in MAP_DESIGNS:
        if name in runs and name != design_of(records[0]):
            ratios = paired_ratios(records, runs[name])
            median, low, high, variation = spread(ratios)
            rows.append(f"| {name} | {median:.2f} | {low:.2f} | {high:.2f} "
                        f"| {100 * variation:.2f}% | {len(ratios)} |")
    return rows


def memory_rows(records):
    rows = []
    for label, key in (("Book memory, MiB", "book_bytes"),
                       ("Charged before the book was built, MiB", "baseline_bytes"),
                       ("Charged once it was built, before any message, MiB", "built_bytes"),
                       ("Charged at the most, MiB", "peak_bytes")):
        median, low, high, variation = spread([record["memory"][key] / 1024 ** 2
                                               for record in records])
        rows.append(f"| {label} | {median:,.1f} | {low:,.1f} | {high:,.1f} "
                    f"| {100 * variation:.2f}% |")
    return rows


def method_lines(header):
    """What the input mode and the top-of-book tracking did, and which of itch-book's two
    methods that is."""
    ram = header["input"].startswith("ram:")
    tracked = header["top_of_book"].startswith("tracked")
    read = ("The day was read into anonymous memory before the warm-up and never mapped, so "
            "each pass reads a copy in RAM." if ram else
            "The day was mapped read-only, and each pass reads the mapping in place.")
    top = ("After each message the book applied, the best bid and ask on its locate, price and "
           "shares, were read and compared with the last seen there: inside the throughput "
           "pass's time, outside each latency span." if tracked else
           "Nothing read the top of book: each pass timed the book alone.")
    if ram and not tracked:
        method = ("This is itch-book's T-BOOK-1 method: its book_throughput reads the whole file "
                  "into a buffer and times the book alone, with no top-of-book work.")
    elif tracked and not ram:
        method = ("This is itch-book's T-BOOK-2 method, `itch-replay --book`: it maps the file "
                  "and checks the best bid and ask, price and shares, after every book event.")
    else:
        method = ("This pairing is neither of itch-book's methods, so it stands for neither "
                  "T-BOOK-1 nor T-BOOK-2.")
    return ["", "## How this run reads the day", "", f"{read} {top} {method}"]


def book_sections(records, runs):
    """The sections only a book design's file has."""
    first = records[0]
    memory = first["memory"]
    lines = method_lines(first["header"])
    lines += ["", "## Per-operation latency", "",
             f"T-LAT-1. The latency pass's spans, by what the message asks of the book: "
             f"{operation_counts(first)} messages a pass. `reduce` is `E`, `C` and `X`. The "
             "last column takes each repetition's own measured empty pair off its figure.", "",
             "| Operation | Percentile | Median ns | Min ns | Max ns | CV | Status "
             "| Median ns, pair subtracted |", "|---|---|---|---|---|---|---|---|"]
    lines += operation_rows(records)
    ager = ""
    if memory.get("ager_held_bytes") is not None:
        ager = (f" The ager's ballast is inside the figure: it holds "
                f"{memory['ager_held_bytes'] / 1024 ** 2:,.1f} MiB of blocks at the end of the "
                "pass, and the pages of the blocks it freed stay charged.")
    lines += ["", "## Book memory", "",
              f"T-BOOK-3. Read from {memory['source']}, in a process of its own that builds "
              "no other book first: once before the book is built, once after, every "
              "1,048,576 messages of the untimed pass and at its end. The book's memory is the "
              "most charged at any reading less the reading before it was built. That is what "
              "the book itself takes, not the mapped file or the input copy, which are in the "
              "first reading, and not the latency samples, which that process never makes. "
              "itch-book's figure is peak RSS less the input buffer, the same quantity read "
              f"another way.{ager}", "",
              "| Figure | Median | Min | Max | CV |", "|---|---|---|---|---|"]
    lines += memory_rows(records)
    if first["header"]["top_of_book"].startswith("tracked"):
        changes = first["throughput"]["top_changes"]
        lines += ["", "## Top of book", "",
                  f"The top of book moved after {changes:,} of the "
                  f"{first['throughput']['messages']:,} messages in every pass of every "
                  "repetition. Every design in this run saw the same count, and the driver "
                  "refuses a run where any did not."]
    lines += ["", "## Design comparison", ""]
    comparison = design_rows(records, runs)
    if design_of(first) in MAP_DESIGNS:
        lines += ["This is the map book, the baseline of T-DESIGN-1. Each fast design's file "
                  "in this run gives its throughput over this one's."]
        if comparison:
            lines += ["", "Over the other map design, repetition by repetition:", "",
                      "| Against | Median ratio | Min | Max | CV | Repetitions |",
                      "|---|---|---|---|---|---|", *comparison]
    elif comparison:
        lines += ["T-DESIGN-1. This design's throughput over each map design's, taken "
                  "repetition by repetition, since each repetition ran every design back to "
                  "back.", "", "| Against | Median ratio | Min | Max | CV | Repetitions |",
                  "|---|---|---|---|---|---|", *comparison]
    else:
        lines += ["No map design ran beside it in this run, so T-DESIGN-1 is not measured here."]
    lines += ["", "## Targets", "",
              "Each target from BENCHMARKS.md beside the bar, from this file's medians. A bar "
              "set on other hardware is the bar and context, not proof about this host.", "",
              "| Target | Bar | Bar set on | Measured here | Verdict |", "|---|---|---|---|---|"]
    lines += target_rows(records, runs)
    return lines


def counter_names(host):
    """What each event counts on debian, said the same way in every file from that host."""
    if host != "debian":
        return ""
    return (" On this host, a Zen 1 core: `cache-misses` counts L2 misses on Zen 1, "
            "`r0843:u` is a floor on DRAM demand fills, and `branch-misses` is branch misses.")


def host_note(records):
    header = records[0]["header"]
    if header["host"] != check_results.MAC_HOST:
        return []
    return [f"**{check_results.MAC_HOST} is not a pinned host.** macOS offers no way to pin a "
            "thread, so each process ran wherever the scheduler put it, beside whatever else "
            "the machine was doing under `measure.sh`'s lock. Every figure here is a macOS "
            "figure, and a bar set on x86 Linux is not passed or failed by one.", ""]


def render(records, args, run_id, started, finished, machine_output, command, runs=None):
    design = design_of(records[0]) if "design" in records[0]["header"] else ""
    title = (args.title or f"bench_replay on {args.input.name}").replace("{design}", design)
    host = records[0]["header"]["host"]
    book = is_book_record(records[0])
    lines = [f"# {title}", "",
             "Generated by `scripts/bench_driver.py` from raw records that are not committed. "
             "Do not edit by hand.", "", f"Host: **{host}**. Numbers from the two hosts are "
             "never mixed.", ""]
    if book:
        lines += host_note(records)
    if args.note:
        lines += [f"**{args.note}**", ""]
    lines += ["## Header", ""]
    lines += header_lines(records, args, run_id, started, finished)
    passes = ("one warm-up pass, excluded from every figure here, then a throughput pass and a "
              "separate latency pass, each on a fresh book")
    if book:
        passes += (", and a second process for the untimed pass of counters and memory. Every "
                   "design of the run ran in each repetition, one after the other")
    lines += ["", "## Summary", "",
              f"Each repetition is a fresh process: {passes}. {len(records)} repetitions.", ""]
    if book:
        lines += ["The mean service time is the throughput pass's time over every message, frame "
                  "walk included (T-LAT-3). The batch mean is the same time over the "
                  f"{records[0]['mix']['book_messages']:,} book messages, carteret's "
                  "denominator: `A`, `F`, `E`, `C`, `X`, `D`, `U`, `P` and `Q` (T-LAT-4).", ""]
    lines += ["| Figure | Median | Min | Max | CV |", "|---|---|---|---|---|"]
    lines += summary_rows(records)
    if book:
        lines += ["", "## Latency", "",
                  "One span is one `apply()`, with the empty pair inside it, and the frame walk "
                  "outside. HdrHistogram saw the samples after the pass. A percentile less than "
                  "the floor above the pair cost is not a measurement, so it shows as below "
                  "floor and has no value here. The last column takes each repetition's own "
                  "measured empty pair off its figure (T-LAT-4); the others are raw.",
                  "", "| Percentile | Median ns | Min ns | Max ns | CV | Status "
                  "| Median ns, pair subtracted |", "|---|---|---|---|---|---|---|"]
    else:
        lines += ["", "## Latency", "",
                  "One span is one `apply()`, with the empty pair inside it, and the frame walk "
                  "outside. HdrHistogram saw the samples after the pass. A percentile less than "
                  "the floor above the pair cost is not a measurement, so it has no value here.",
                  "", "| Percentile | Median ns | Min ns | Max ns | CV | Status |",
                  "|---|---|---|---|---|---|"]
    lines += latency_rows(records)
    if book:
        lines += book_sections(records, runs or {design: records})
    lines += ["", "## Counters", "",
              "From a separate pass that is not timed, median over the repetitions. `rdpmc` "
              "in the header is the agreement with `read()` on the descriptors."
              + counter_names(host), "",
              "| Event | State | Running ratio | Median count | Reason |",
              "|---|---|---|---|---|"]
    lines += counter_rows(records)
    per_message = per_message_rows(records)
    if per_message:
        lines += ["", "Per-message means, through `rdpmc`:", "",
                  "| Event | Median | Min | Max | CV |", "|---|---|---|---|---|"]
        lines += per_message
    agreement = agreement_rows(records)
    if agreement:
        lines += ["", "`rdpmc` against `read()` over the whole pass, median counts with the "
                  "widest gap of any repetition, and the per-message deltas summed, which leave "
                  "out the frame walk:", "",
                  "| Event | read() | rdpmc | Widest gap | Gap | Sum over apply() |",
                  "|---|---|---|---|---|---|"]
        lines += agreement
    lines += ["", "## Repetitions", "",
              "| Repetition | Messages/s | Instrumentation ratio | Quantum ns | Empty pair ns "
              "| Migrations | Peak RSS GiB |", "|---|---|---|---|---|---|---|"]
    lines += repetition_rows(records)
    if swap_watch is not None:
        lines += ["", swap_watch.line()]
    lines += ["", "## machine_check output", "", "```", machine_output, "```", "",
              "## Commands", "", "```", command, "```", ""]
    return "\n".join(lines)


def relative_command(args):
    def shown(path):
        path = Path(path).resolve()
        try:
            return str(path.relative_to(REPO_ROOT))
        except ValueError:
            return f"<raw>/{path.name}"
    words = ["python3", "-B", "scripts/bench_driver.py", "--bench", shown(args.bench),
             "--probe", shown(args.probe), "--input", shown(args.input),
             "--prediction", args.prediction, "--repetitions", str(args.repetitions),
             "--raw-dir", "<raw>", "--out", shown(args.out)]
    for design in args.design:
        words += ["--design", design]
    words += ["--input-mode", args.input_mode]
    if args.track_top:
        words.append("--track-top")
    if args.core is not None:
        words += ["--core", str(args.core)]
    text = " ".join(shlex.quote(word) if "<" not in word else word for word in words)
    return f"{args.wrapped_by} {text}" if args.wrapped_by else text


def append_log(path, run_id, prediction, commit, outcome):
    new = not path.is_file()
    with open(path, "a", encoding="utf-8") as log:
        if new:
            log.write("# Run log\n\nOne row for every run of `scripts/bench_driver.py`, failed "
                      "ones included. Rows are never changed or removed.\n\n"
                      "| Run | Prediction | Commit | OS | Outcome |\n|---|---|---|---|---|\n")
        outcome_text = outcome.replace("|", "/").replace("\n", " ")
        log.write(f"| {run_id} | {prediction} | {commit} | {platform.system()} | {outcome_text} |\n")


def run(args, run_id, started, commit):
    global swap_watch
    swap_watch = SwapWatch(args.vmstat)
    set_stage("hashing the input")
    digest = hash_and_fault_in(args.input)
    set_stage("checking the prediction")
    code = subprocess.run([sys.executable, "-B", str(REPO_ROOT / "scripts" / "check_predictions.py"),
                           "--root", str(args.predictions_root), "--require", args.prediction],
                          capture_output=True, text=True, check=False)
    if code.returncode != 0:
        raise Refused("prediction", code.stdout.strip())
    set_stage("machine_check")
    machine_output = machine_check(args)
    designs = args.design or [None]
    runs = {}
    for index in range(1, args.repetitions + 1):
        for design in designs:
            record = run_repetition(args, index, commit, digest, design)
            validate_run(record)
            runs.setdefault(design_of(record), []).append(record)
    validate_together(runs)
    set_stage("rendering the results files")
    finished = utc_now()
    texts = {}
    for design, records in runs.items():
        out = Path(str(args.out).replace("{design}", design))
        texts[out] = render(records, args, run_id, started, finished, machine_output,
                            relative_command(args), runs)
        violations = [found for found in check_results.check_text(texts[out])
                      if found[0] != "load-not-stamped"]
        if violations:
            raise Refused("results", f"{out.name}: " + "; ".join(
                f"{code}: {detail}" for code, detail in violations))
    # A signal from here on is passed on but stops nothing, so no file is left half written.
    hold_signals()
    for out, text in texts.items():
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return list(texts)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bench", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--core", type=int)
    parser.add_argument("--sibling", type=int)
    parser.add_argument("--design", action="append", default=[],
                        choices=FAST_DESIGNS + MAP_DESIGNS,
                        help="repeatable; every one runs in each repetition, in this order")
    parser.add_argument("--input-mode", choices=("ram", "mapped"), default="mapped")
    parser.add_argument("--track-top", action="store_true")
    parser.add_argument("--orders", type=int)
    parser.add_argument("--levels", type=int)
    parser.add_argument("--title")
    parser.add_argument("--note")
    parser.add_argument("--wrapped-by", help="what the run was started under, for the record")
    parser.add_argument("--allow-dirty", action="store_true", help="for tests, not for a report")
    parser.add_argument("--bench-arg", action="append", default=[], metavar="ARG")
    parser.add_argument("--predictions-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--run-log", type=Path, default=REPO_ROOT / "results" / "run_log.md",
                        help=argparse.SUPPRESS)
    parser.add_argument("--vmstat", type=Path, default=Path("/proc/vmstat"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.repetitions < 3:
        parser.error("median, min, max and CV need at least 3 repetitions")
    if len(args.design) != len(set(args.design)):
        parser.error("a design is named twice")
    if len(args.design) > 1 and "{design}" not in str(args.out):
        parser.error("with more than one design, --out must hold {design}")
    return args


def hold_signals():
    """From here a signal is passed on but no longer stops the driver: its row is next."""
    global stopping
    stopping = True


def main(argv=None):
    args = parse_args(argv)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, forward_signal)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    run_id = f"{args.prediction}-{started.replace(':', '').replace('-', '')}"
    commit = "unknown"
    written = []
    try:
        commit = git_commit(REPO_ROOT, args.allow_dirty)
        written = run(args, run_id, started, commit)
    except Stopped as stop:
        stop_child()
        name = signal.Signals(stop.signum).name
        print(f"bench_driver: stopped by {name} at {stage}", file=sys.stderr)
        append_log(args.run_log, run_id, args.prediction, commit,
                   f"stopped by {name} at {stage}; nothing from it is published; the row was "
                   "written by the driver as it stopped")
        return 128 + stop.signum
    except Refused as refusal:
        hold_signals()
        print(f"bench_driver: refused at {refusal.stage}: {refusal}", file=sys.stderr)
        append_log(args.run_log, run_id, args.prediction, commit,
                   f"refused at {refusal.stage}: {refusal}")
        return 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        hold_signals()
        print(f"bench_driver: failed: {error!r}", file=sys.stderr)
        append_log(args.run_log, run_id, args.prediction, commit, f"failed: {error!r}")
        return 1
    hold_signals()
    append_log(args.run_log, run_id, args.prediction, commit, "published")
    for out in written:
        print(f"bench_driver: wrote {out}, load lines pending. Now run scripts/stamp_load.py.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
