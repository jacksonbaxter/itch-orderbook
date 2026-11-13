#!/usr/bin/env python3
"""Runs the SPSC benchmarks in fresh processes and writes one results file.

    spsc_driver.py --suite queues|ablation|pipeline --bench BENCH --probe BENCH_PROBE \\
        --input FILE --prediction P-NNNN --raw-dir DIR --out results/spsc_NAME.md \\
        [--repetitions N] [--core N] [--peer-core N] [--sibling N] [--peer-sibling N] \\
        [--quiet-before S] [--wrapped-by TEXT] [--allow-dirty] [--bench-arg ARG]...

queues and ablation run bench_spsc (T-SPSC-1 with the comparison, T-SPSC-2); pipeline runs
bench_pipeline (T-SPSC-3). On a reported run this is measure.sh's direct child, as
scripts/bench_driver.py is, and it shares that driver's order and its signal handling:

    measure.sh /usr/bin/time -l python3 -B scripts/spsc_driver.py ... 2> STDERR
    python3 -B scripts/stamp_load.py --results FILE --stderr STDERR --raw-dir RAW/RUN_ID

Hash the input, which also faults it in; confirm the prediction is on record; run
machine_check.sh, refusing on a failure; run the repetitions, one process each, the
order of the queues or modes turned by one each time; refuse if any refused, if the
records are not of the suite, or if the kernel swapped a page out; write the results file
with its load lines pending. Figures are the median of the repetitions with min, max and
CV beside it. A latency cell is the median of the repetitions' figures, a figure under
the timer's floor counting as below every other, and is written `below floor` when that
median is under it.

On macOS, where other work shares the machine and does not always wait for measure.sh, the
Mac's sampler rule applies to every repetition. A CPU sampler runs beside the whole run,
every 2 s a `sample` line and a `FOREIGN` line for each process above 20% CPU that is not
this driver, a process under it or the measure.sh and /usr/bin/time above it (the sampler
of scripts/wasm_bench.py). A repetition is published only if the sampler covered its whole
span with no gap over 5 s and saw no foreign process in it. Otherwise it is withdrawn: its
raw files are set aside, it gets its own row in the run log saying why, and it runs again,
until --repetitions are published or more than --most-withdrawn are withdrawn, which
refuses the run. With --quiet-before S, each repetition first waits until the sampler has
seen S seconds with no foreign process, for at most --quiet-wait S, and the input is read
again, since a long wait can see the day leave the page cache. The sampler's lines go
to <raw-dir>/sampler.txt and every span and its outcome into the results file.

With --peer-core on Linux, machine_check.sh runs once on each core, pinned to it by
taskset, the second with --peer-sibling as the idle sibling; and before any repetition
`bench_probe --cross-core` checks that the two cores' clocks agree, since a one-way latency
subtracts one core's stamp from the other's. The run is refused if they do not, and the
bound goes into the header.

Each target is compared as a ratio, computed from the summary's medians, with the bar
beside it. Every SPSC bar was set on x86, so a macOS file gives each the verdict `host
differs` with the ratio and the bar in words; on debian the verdict is met or missed.
The checks and the comparisons themselves are check_results.py's.

A paced or burst latency is measured from the push, not from the time the message was due,
so a wait behind earlier slow pushes is in no cell, only in the late count; the file labels
its columns so and says what that leaves out, with each row's late share (r4-2).

    spsc_driver.py render --raw-dir DIR --published results/FILE --stderr STDERR \\
        --out-dir OUT [--rendered-again REASON]

renders a published file again from its run's raw records: every figure from the records,
every Sampler verdict worked out again from DIR/sampler.txt, and only what the run printed
into the file alone (its command, run id and times, the machine check, the cross-core and
swap lines) taken from it; from a record of the new kind, which kept the machine check and
the cross-core output, those two from the record, and a record that lacks any part refused.

Every run gets a row in results/run_log.md, whatever became of it.
Raw output stays in --raw-dir/<run id>, a folder of the run's own that the driver
refuses to write into if another run has, with run.json beside it: the commit and tree
state, each binary's sha256, the dataset hashed, and every tool output the file quotes
(scripts/run_record.py). None of it is committed.

Exit codes: 0 results written, 1 refused or failed, 2 bad usage, 128 + N stopped by
signal N.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import re
import shlex
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_driver  # noqa: E402
import check_results  # noqa: E402
import run_record  # noqa: E402
import wasm_bench  # noqa: E402

REPO_ROOT = bench_driver.REPO_ROOT
Refused = bench_driver.Refused
Stopped = bench_driver.Stopped
PERCENTILES = (("p50", 50.0), ("p90", 90.0), ("p99", 99.0), ("p99.9", 99.9),
               ("p99.99", 99.99), ("max", 100.0))
MODES = ("single", "pipelined", "decoded")
# About half of 12302019's 263,241,937 ops the book acts on: the day's book is empty at the
# close, and full here.
CHECKPOINT_OPS = 131_000_000
MODE_NAMES = {"single": "Single-threaded", "pipelined": "Pipelined", "decoded": "Decoded"}
MOST_WITHDRAWN = 40
QUIET_WAIT_S = 4 * 3600

SUITES = {
    "queues": {
        "title": "T-SPSC-1 and the queue comparison",
        "what": (
            "Every queue of `bench/spsc_queues.hpp` by the four protocols of "
            "`bench/spsc_protocols.hpp`, on two threads: rtt, a ping-pong through two queues "
            "with one item in flight, the mean over {trips} round trips after a tenth as many "
            "untimed; saturated, {saturated} sequence numbers pushed as fast as the queue "
            "takes them, the first lap of the ring left out of the span; paced, one stamped "
            "item every 1 µs and every 10 µs, {paced} of each after a tenth as many untimed, "
            "the one-way latency of each recorded; and burst, the {burst} messages of the day "
            "from {burst_start} for {burst_span}, each in a 64-byte item with its own length of "
            "real bytes, sent at the gaps between their ITCH timestamps, the one-way latency "
            "of each recorded. Every queue holds the same element: an 8-byte integer in rtt, "
            "saturated and paced, and the 64-byte item in burst. Every queue is asked for the "
            "same capacity, {capacity} items. Each third-party queue runs as shipped, through "
            "its own non-blocking single-item calls, with its own padding."),
    },
    "ablation": {
        "title": "T-SPSC-2: the project ring with one technique taken away at a time",
        "what": (
            "The project ring of `include/itch/spsc.hpp` in every configuration "
            "`bench_spsc --suite ablation` builds, each differing from the shipped ring in one "
            "switch, or two for uncached-modulo, Frasch's Fifo3 against his Fifo4 and Fifo4a "
            "(LC-7): rtt, saturated and burst as in the comparison, {trips} round trips and "
            "{saturated} items, an 8-byte integer, capacity {capacity}; and batch publish "
            "against a store of the index per item, by saturated through the batch calls, "
            "{batch} items a call on each side. The ring pads to {cache_line} bytes on this "
            "host; padding-8 is no padding at all."),
    },
    "pipeline": {
        "title": "T-SPSC-3: the SPSC ring inside the replay",
        "what": (
            "The whole day three ways on the `{design}` book, the shipped arm, each on a fresh "
            "book: single, the shipped single-threaded replay, the frame walk and `apply()` on "
            "each payload; pipelined, a parser thread that walks the frames, decodes each "
            "message the book acts on into a {op_bytes}-byte op and pushes the ops through the "
            "ring, {batch} to a publish, to the book thread, which pops them {batch} at a time "
            "and applies each; and decoded, the pipeline's work on one thread with no "
            "handover. The ring holds {capacity} ops. Each pass also hashes the book once, "
            "untimed, just after op {checkpoint_ops:,} of those the book acts on: the day's "
            "book is empty again at the close. `bench/pipeline.hpp`."),
    },
}


# the Mac's sampler rule

def processes(ps):
    """(pid, parent pid, %CPU, command) for every process, as ps gives them."""
    listed = subprocess.run([ps, "-Ao", "pid=,ppid=,pcpu=,args="], capture_output=True,
                            text=True, check=True).stdout
    rows = []
    for line in listed.splitlines():
        words = line.split(None, 3)
        if len(words) >= 3:
            rows.append((int(words[0]), int(words[1]), float(words[2]),
                         words[3] if len(words) == 4 else ""))
    return rows


class Sampler(wasm_bench.Sampler):
    """wasm_bench.py's sampler, reading the process table through `ps`, every `every` s."""

    def __init__(self, path, ps, every):
        super().__init__(path)
        self.ps = ps
        self.every = every

    def sample(self):
        try:
            lines = wasm_bench.sample_lines(processes(self.ps), os.getpid(),
                                            bench_driver.utc_now())
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            lines = [f"{bench_driver.utc_now()} failed {error!r}"]
        self.lines += lines
        with open(self.path, "a", encoding="utf-8") as out:
            out.write("".join(line + "\n" for line in lines))

    def loop(self):
        while not self.done.wait(self.every):
            self.sample()

    def sample_stamps(self):
        return [wasm_bench.at(line.split()[0]) for line in list(self.lines)
                if line.split()[1:2] == ["sample"]]

    def wait_past(self, stamp):
        """Until a sample at or after `stamp`, so that the sampler covers a span's end."""
        give_up = time.monotonic() + 3 * self.every + wasm_bench.LONGEST_GAP_S
        while time.monotonic() < give_up:
            stamps = self.sample_stamps()
            if stamps and stamps[-1] >= wasm_bench.at(stamp):
                return
            time.sleep(min(self.every, 0.5))


def stamp_of(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def span_lines(lines, begin, end):
    """The sampler's lines from its last sample at or before begin to its first at or after
    end: the span and the sample either side of it. The edge samples are kept to show the
    span covered, and for its gaps; wasm_bench.unquiet judges FOREIGN and failed lines only
    where they are stamped inside [begin, end], so a FOREIGN line at an edge sample outside
    the span withdraws nothing (r5-10)."""
    stamps = [wasm_bench.at(line.split()[0]) for line in lines
              if line.split()[1:2] == ["sample"]]
    before = [t for t in stamps if t <= wasm_bench.at(begin)]
    after = [t for t in stamps if t >= wasm_bench.at(end)]
    if not before or not after:
        return []
    low, high = max(before), min(after)
    return [line for line in lines if low <= wasm_bench.at(line.split()[0]) <= high]


def span_unquiet(lines, begin, end):
    """Why the sampler does not show the span quiet, or None."""
    return wasm_bench.unquiet(span_lines(lines, begin, end), begin, end)


def span_summary(lines, begin, end):
    stamps = [wasm_bench.at(line.split()[0]) for line in span_lines(lines, begin, end)
              if line.split()[1:2] == ["sample"]]
    gap = max((b - a).total_seconds() for a, b in zip(stamps, stamps[1:])) if len(stamps) > 1 \
        else 0
    return (f"published: {len(stamps)} samples, the longest gap {gap:g} s, no other process "
            f"above {wasm_bench.FOREIGN_CPU:g}% CPU")


def wait_for_quiet(sampler, seconds, most):
    """Until the sampler has seen `seconds` with no foreign process, for at most `most` s."""
    if sampler is None or seconds <= 0:
        return
    bench_driver.set_stage("waiting for the Mac to be quiet")
    give_up = time.monotonic() + most
    while True:
        stamps = sampler.sample_stamps()
        if stamps:
            end = stamps[-1]
            begin = end - datetime.timedelta(seconds=seconds)
            if stamps[0] <= begin and span_unquiet(list(sampler.lines), stamp_of(begin),
                                                   stamp_of(end)) is None:
                return
        if time.monotonic() > give_up:
            raise Refused("waiting for the Mac to be quiet",
                          f"the sampler never saw {seconds:g} s with no other process above "
                          f"{wasm_bench.FOREIGN_CPU:g}% CPU in {most:g} s")
        time.sleep(sampler.every)


def set_aside(args, index, count):
    """Keeps a withdrawn repetition's raw files under a name of their own."""
    for suffix in (".json", ".log"):
        path = args.raw_dir / f"{args.suite}-repetition-{index}{suffix}"
        if path.is_file():
            path.rename(args.raw_dir / f"{args.suite}-repetition-{index}-withdrawn-{count}{suffix}")


# the two cores on Linux

def machine_checks(args):
    """machine_check.sh's output: once, or with a peer core on Linux, once on each core,
    pinned to it, each with its own idle sibling."""
    if args.peer_core is None or platform.system() != "Linux":
        return bench_driver.machine_check(args)
    outputs = []
    for core, sibling in ((args.core, args.sibling), (args.peer_core, args.peer_sibling)):
        command = ["taskset", "-c", str(core), "bash",
                   str(REPO_ROOT / "scripts" / "machine_check.sh"), "--input", str(args.input),
                   "--probe", str(args.probe), "--core", str(core)]
        if sibling is not None:
            command += ["--sibling", str(sibling)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise Refused("machine_check", f"core {core}: " + (
                result.stdout.strip().splitlines()[-1] if result.stdout.strip()
                else f"exited {result.returncode}"))
        outputs.append(f"core {core}, under taskset -c {core}:\n{result.stdout.strip()}")
    return "\n\n".join(outputs)


def cross_core_check(args):
    """bench_probe --cross-core's lines, refusing a run whose two cores' clocks disagree."""
    if args.peer_core is None:
        return None
    bench_driver.set_stage("checking the two cores' clocks")
    command = [str(args.probe), "--cross-core", str(args.peer_core)]
    if args.core is not None:
        command += ["--core", str(args.core)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    lines = dict(line.split(": ", 1) for line in result.stdout.splitlines() if ": " in line)
    verdict = lines.get("cross_core_verdict", f"no verdict, exit {result.returncode}")
    if result.returncode != 0 or not verdict.startswith("agree"):
        raise Refused("cross-core timer", verdict)
    args.record.output(CROSS_CORE, result.stdout)
    return cross_core_line(result.stdout)


CROSS_CORE = "cross_core.txt"


def cross_core_line(stdout):
    """The header's cross_core_timer line, from bench_probe --cross-core's output."""
    lines = dict(line.split(": ", 1) for line in stdout.splitlines() if ": " in line)
    verdict = lines["cross_core_verdict"]
    return (f"{verdict.split(':')[0]}: {lines['cross_core_offset']}, over "
            f"{lines['cross_core_trips']}; the fastest round trip "
            f"{lines['cross_core_fastest_round_trip']}; {lines['cross_core_backwards']}")


def run_process(args, index, commit, digest):
    """One benchmark process."""
    stem = f"{args.suite}-repetition-{index}"
    record_path = args.raw_dir / f"{stem}.json"
    log_path = args.raw_dir / f"{stem}.log"
    record_path.unlink(missing_ok=True)
    command = [str(args.bench), "--input", str(args.input), "--out", str(record_path),
               "--commit", commit, "--dataset", args.input.name, "--dataset-sha256", digest,
               "--rotate", str(index - 1)]
    if args.suite != "pipeline":
        command += ["--suite", args.suite]
    else:
        command += ["--checkpoint-ops", str(args.checkpoint_ops)]
    if args.core is not None:
        command += ["--core", str(args.core)]
    if args.peer_core is not None:
        command += ["--peer-core", str(args.peer_core)]
    command += args.bench_arg
    where = f"repetition {index}"
    bench_driver.set_stage(where)
    with open(log_path, "w", encoding="utf-8") as log:
        bench_driver.current_child = subprocess.Popen(command, stdout=log,
                                                      stderr=subprocess.STDOUT)
        code = bench_driver.current_child.wait()
        bench_driver.current_child = None
    if bench_driver.swap_watch is not None:
        bench_driver.swap_watch.check(where)
    if not record_path.is_file():
        raise Refused(where, f"exited {code} with no record")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if code != 0 or record["refusals"]:
        raise Refused(where, f"exited {code}: {'; '.join(record['refusals']) or 'no reason given'}")
    return record


def validate_run(record, suite):
    """What one record must show for its numbers to mean anything."""
    if record.get("suite") != suite:
        raise Refused("record", f"the record is of suite {record.get('suite')!r}")
    if suite == "pipeline":
        passes = record["warmup"] + record["timed"]
        if sorted(p["mode"] for p in record["timed"]) != sorted(MODES):
            raise Refused("record", "the timed passes are not one of each mode")
        if len({p["book_hash"] for p in passes}) != 1:
            raise Refused("record", "the passes left different books")
        if not record.get("checkpoint_ops") or any(p["checkpoint_hash"] is None for p in passes):
            raise Refused("record", "a pass took no checkpoint")
        if len({p["checkpoint_hash"] for p in passes}) != 1:
            raise Refused("record", "the passes held different books at the checkpoint")
        if passes[0]["checkpoint_orders"] == 0:
            raise Refused("record", "the book was empty at the checkpoint, which then proves "
                                    "nothing")
        if any(p["ns"] <= 0 for p in passes):
            raise Refused("record", "a pass took no time")
        return
    if record["burst"]["speedup"] != 1:
        raise Refused("record", "the burst ran faster than the day")
    for row in record["rows"]:
        if row["errors"] != 0:
            raise Refused("record", f"{row['queue']} {row['protocol']}: {row['errors']} wrong")
        if row["ns"] <= 0 or row["items"] <= 0:
            raise Refused("record", f"{row['queue']} {row['protocol']} timed nothing")


def row_keys(record):
    return [(row["queue"], row["protocol"]) for row in record["rows"]]


def validate_together(records):
    first = records[0]
    for index, record in enumerate(records[1:], start=2):
        if first["suite"] == "pipeline":
            if record["timed"][0]["book_hash"] != first["timed"][0]["book_hash"]:
                raise Refused("record", f"repetition {index} left another book")
            if record["timed"][0]["checkpoint_hash"] != first["timed"][0]["checkpoint_hash"]:
                raise Refused("record", f"repetition {index} held another book at the checkpoint")
        elif sorted(row_keys(record)) != sorted(row_keys(first)):
            raise Refused("record", f"repetition {index} ran other rows")


def rows_by_key(record):
    return {(row["queue"], row["protocol"]): row for row in record["rows"]}


def pass_of(record, mode):
    return next(p for p in record["timed"] if p["mode"] == mode)


def figure(record, label):
    """The value of one summary figure in one record."""
    if record["suite"] == "pipeline":
        for mode in MODES:
            name = MODE_NAMES[mode]
            if label == f"{name} replay, s":
                return pass_of(record, mode)["ns"] / 1e9
            if label == f"{name} replay, messages/s":
                timed = pass_of(record, mode)
                return timed["messages"] / (timed["ns"] / 1e9)
        if label == check_results.PIPELINE_RATIO:
            return pass_of(record, "pipelined")["ns"] / pass_of(record, "single")["ns"]
        if label == "Decoded time over single-threaded time":
            return pass_of(record, "decoded")["ns"] / pass_of(record, "single")["ns"]
        raise KeyError(label)
    queue, protocol, unit = (part.strip() for part in label.split(",", 2))
    row = rows_by_key(record)[(queue, protocol)]
    if unit == check_results.RTT_UNIT:
        return row["ns_per_item"]
    if unit == check_results.RATE_UNIT:
        return row["items_per_s"] / 1e6
    if unit == "mean one-way ns":
        return row["latency"]["mean_ns"]
    raise KeyError(label)


def summary_labels(record):
    if record["suite"] == "pipeline":
        labels = []
        for mode in MODES:
            labels += [f"{MODE_NAMES[mode]} replay, s", f"{MODE_NAMES[mode]} replay, messages/s"]
        return labels + [check_results.PIPELINE_RATIO, "Decoded time over single-threaded time"]
    labels = []
    for row in record["rows"]:
        protocol = row["protocol"]
        if protocol == "rtt":
            unit = check_results.RTT_UNIT
        elif protocol.startswith("saturated"):
            unit = check_results.RATE_UNIT
        else:
            unit = "mean one-way ns"
        labels.append(f"{row['queue']}, {protocol}, {unit}")
    return labels


def ordered_labels(records):
    """Each label once, in the order of the first repetition with rotation undone."""
    labels = summary_labels(records[0])
    if records[0]["suite"] == "pipeline":
        return labels
    order = {name: i for i, name in enumerate(
        ["project", "rigtorp", "boost", "moodycamel", "folly", "atomic_queue", "xenium_vyukov",
         "mutex", "shipped", "uncached", "modulo", "uncached-modulo", "padding-8", "padding-64",
         "padding-128", "padding-256", "batch-publish", "publish-each"])}
    return sorted(labels, key=lambda label: (order.get(label.split(",")[0], 99),
                                             labels.index(label)))


def shown(value):
    """At least four significant figures, so that a ratio of two shown medians is good to
    the two decimals it is written with."""
    if value >= 1000:
        return f"{value:,.0f}"
    if value >= 100:
        return f"{value:.1f}"
    if value >= 10:
        return f"{value:.2f}"
    return f"{value:.3f}"


def summary_rows(records):
    rows = []
    for label in ordered_labels(records):
        median, low, high, cv = bench_driver.spread([figure(record, label) for record in records])
        rows.append(f"| {label} | {shown(median)} | {shown(low)} | {shown(high)} "
                    f"| {cv * 100:.2f}% |")
    # The comparisons divide the medians as shown, as check_results.py reads them back.
    return rows, check_results.spsc_medians("## Summary\n" + "\n".join(rows) + "\n")


def percentile_value(row, percentile):
    for entry in row["latency"]["percentiles"]:
        if entry["percentile"] == percentile:
            return entry["value_ns"]
    raise KeyError(percentile)


def median_cell(values):
    """None is under the floor, so below every figure."""
    ordered = sorted(values, key=lambda value: -1.0 if value is None else value)
    middle = ordered[len(ordered) // 2] if len(ordered) % 2 else None
    if len(ordered) % 2 == 0:
        low, high = ordered[len(ordered) // 2 - 1], ordered[len(ordered) // 2]
        middle = None if low is None or high is None else (low + high) / 2
    return check_results.BELOW_FLOOR if middle is None else f"{middle:,.1f}"


def late_share(row):
    """A row's late sends over the sends its late count covers, from the raw counts."""
    return row["late"] / check_results.spsc_sends(row["protocol"], row["latency"]["samples"])


def latency_rows(records):
    rows = []
    keyed = [rows_by_key(record) for record in records]
    for key in [(row["queue"], row["protocol"]) for row in records[0]["rows"]]:
        if "latency" not in keyed[0][key]:
            continue
        cells = [median_cell([percentile_value(k[key], value) for k in keyed])
                 for _, value in PERCENTILES]
        late = statistics.median(k[key]["late"] for k in keyed)
        share = statistics.median(late_share(k[key]) for k in keyed)
        samples = keyed[0][key]["latency"]["samples"]
        rows.append(f"| {key[0]} | {key[1]} | " + " | ".join(cells) +
                    f" | {late:,.0f} | {check_results.share_cell(share)} | {samples:,} |")
    return sorted(rows, key=lambda row: (row.split(" | ")[1] != "burst", row))


def ratio_text(value):
    return f"{value:.2f}×"


def target_rows(records, medians):
    host = records[0]["header"]["host"]
    suite = records[0]["suite"]
    rows = []
    for (target, comparison), spec in check_results.SPSC_COMPARISONS.items():
        if spec["suite"] != suite:
            continue
        value = check_results.comparison_value(spec, medians)
        bar = f"{'≥' if spec['op'] == '>=' else '≤'} {spec['bar']:.2f}×"
        where = f"{host}, {'pinned' if records[0]['header']['pinning'].startswith('pinned') else 'not pinned'}; the bar's: {spec['host']}"
        if value is None:
            rows.append(f"| {target} | {comparison} | {bar} | – | {where} | not measured in "
                        f"this run: {spec['missing']} |")
            continue
        holds = value >= spec["bar"] if spec["op"] == ">=" else value <= spec["bar"]
        if host == check_results.MAC_HOST:
            verdict = (f"host differs: {ratio_text(value)} on this Mac against a bar set on "
                       f"{spec['host']}, which it would {'meet' if holds else 'miss'}")
        elif holds:
            verdict = "met"
        else:
            verdict = f"missed: {ratio_text(value)} against {bar}"
        rows.append(f"| {target} | {comparison} | {bar} | {ratio_text(value)} | {where} "
                    f"| {verdict} |")
    return rows


def header_lines(records, args, run_id, started, finished, sampled=None, cross_core=None):
    first = records[0]
    header = dict(first["header"])
    lines = [f"- {name}: {header[name]}" for name in bench_driver.HEADER_ORDER if name in header]
    peer = first["peer_core"]
    if first["suite"] == "pipeline":
        pins = {p["parser_pinning"] for r in records for p in r["timed"] if p["mode"] == "pipelined"}
        lines += [f"- design: {first['design']}: FastBook<> with PagedIndex and FlatLevels",
                  f"- input: {first['input']}",
                  f"- warmup_messages: {pass_of(first, 'single')['messages']:,}",
                  f"- timed_messages: {pass_of(first, 'single')['messages']:,}"]
    else:
        pins = {row["peer_pinning"] for r in records for row in r["rows"]}
        burst = first["burst"]
        lines += [f"- burst: {burst['messages']:,} messages from {burst['start_ns']:,} ns after "
                  f"midnight for {burst['span_ns']:,} ns, speedup {burst['speedup']}"]
    lines += [f"- suite: {first['suite']}",
              f"- element: {'a 32-byte decoded op' if first['suite'] == 'pipeline' else 'an 8-byte integer in rtt, saturated and paced; a 64-byte item in burst'}",
              f"- capacity: {first['capacity']:,} "
              + ("ops in the ring" if first["suite"] == "pipeline" else
                 "items requested of every queue"),
              f"- cache_line: {first['cache_line']} bytes, the ring's padding constant",
              f"- peer_core: {peer if peer >= 0 else 'none requested'}",
              f"- peer_pinning: {' / '.join(sorted(pins))}"]
    if cross_core is not None:
        lines.append(f"- cross_core_timer: {cross_core}")
    if sampled is not None:
        lines.append(f"- interference: {check_results.spsc_interference(sampled['every'])}; "
                     f"{len(sampled['withdrawn'])} withdrawn under the rule")
    lines += [
              f"- measure_load_start: {check_results.PENDING}",
              f"- measure_load_end: {check_results.PENDING}",
              *bench_driver.peak_lines(records),
              f"- prediction: {args.prediction}",
              f"- run_id: {run_id}",
              f"- repetitions: {len(records)}",
              f"- run_started: {started}",
              f"- run_finished: {finished}"]
    return lines


def host_lines(records):
    host = records[0]["header"]["host"]
    if host != check_results.MAC_HOST:
        return [f"Host: **{host}**. Numbers from the two hosts are never mixed.", ""]
    return [f"Host: **{host}**. Numbers from the two hosts are never mixed.", "",
            f"**{host} is not a pinned host.** macOS offers no way to pin a thread, so both "
            "threads of every row ran wherever the scheduler put them, beside whatever else "
            "the machine was doing under `measure.sh`'s lock. Unpinned cross-core latency on "
            "the Mac is weak evidence: the two threads may share a cluster or not, and a line "
            "moves between P clusters in about three times the time it takes within one "
            "(LC-28). The cores each thread last ran on are in the repetitions table. Every "
            "figure here is a macOS figure, and every bar below was set on x86, so none is met "
            "or missed here.", ""]


def describe(records):
    first = records[0]
    spec = SUITES[first["suite"]]
    if first["suite"] == "pipeline":
        return spec["what"].format(design=first["design"], op_bytes=first["op_bytes"],
                                   batch=first["batch"], capacity=f"{first['capacity']:,}",
                                   checkpoint_ops=first["checkpoint_ops"])
    rows = rows_by_key(first)
    any_row = next(iter(rows.values()))
    trips = next(row["items"] for row in first["rows"] if row["protocol"] == "rtt")
    saturated = next(row["items"] for row in first["rows"] if row["protocol"] == "saturated")
    paced = next((row["items"] for row in first["rows"] if row["protocol"].startswith("paced")),
                 0)
    burst = first["burst"]
    del any_row
    return spec["what"].format(
        trips=f"{trips:,}", saturated=f"{saturated + first['capacity']:,}",
        paced=f"{paced:,}", burst=f"{burst['messages']:,}",
        burst_start=f"{burst['start_ns'] / 3.6e12:.0f}h after midnight, {burst['start_ns']:,} ns",
        burst_span=f"{burst['span_ns'] / 1e9:g} s", capacity=f"{first['capacity']:,}",
        batch=first["batch"], cache_line=first["cache_line"])


def repetition_rows(records):
    rows = []
    for index, record in enumerate(records, start=1):
        if record["suite"] == "pipeline":
            for p in record["timed"]:
                rows.append(f"| {index} | {p['mode']} | {p['ns'] / 1e9:.3f} | {p['messages']:,} "
                            f"| {p['ops']:,} | {p['full_waits']:,} | {p['empty_waits']:,} "
                            f"| {p['checkpoint_hash']} | {p['book_hash']} "
                            f"| {record['peak_rss_bytes'] / 1024 ** 3:.2f} |")
            continue
        for row in record["rows"]:
            value = row["ns_per_item"] if row["protocol"] == "rtt" else (
                row["items_per_s"] / 1e6 if row["protocol"].startswith("saturated")
                else row["latency"]["mean_ns"])
            rows.append(f"| {index} | {row['queue']} | {row['protocol']} | {shown(value)} "
                        f"| {row['self_core']} | {row['peer_core']} |")
    return rows


def pipeline_sections(records):
    first = records[0]
    single = pass_of(first, "single")
    pipelined = pass_of(first, "pipelined")
    statuses = ", ".join(f"{name} {count:,}" for name, count in single["statuses"].items()
                         if count)
    lines = ["## Book agreement", "",
             f"Every pass of every repetition, warm-up and timed, left the book "
             f"`{single['book_hash']}`, by the oracle's canonical hash of every side, level and "
             "order (`tests/oracle/canonical.hpp`), and the driver refuses a run where one "
             f"differs. At the close it held {single['live_orders']:,} orders on "
             f"{single['live_levels']:,} levels. At the checkpoint, just after op "
             f"{first['checkpoint_ops']:,} of those the book acts on, every pass held the book "
             f"`{single['checkpoint_hash']}`, {single['checkpoint_orders']:,} orders on "
             f"{single['checkpoint_levels']:,} levels, hashed on the book's thread in "
             f"{single['checkpoint_ns'] / 1e6:.1f} ms that the timed span leaves out; the driver "
             "refuses a run where one differs there too, or where that book is empty. "
             f"The single-threaded replay walked "
             f"{single['messages']:,} messages, and `apply()` returned {statuses}; the pipeline "
             f"handed the book {pipelined['ops']:,} of them, every one it acts on, and each "
             "status but no_book_effect came out the same.", "",
             "## Handover", "",
             "How often each side of the ring found it in the way, counted once per failed try, "
             "in the timed pipelined pass of each repetition: the parser finding it full, and "
             "the book thread finding it empty.", "",
             "| Repetition | Full waits | Empty waits | Parser pinning |", "|---|---|---|---|"]
    for index, record in enumerate(records, start=1):
        p = pass_of(record, "pipelined")
        lines.append(f"| {index} | {p['full_waits']:,} | {p['empty_waits']:,} "
                     f"| {p['parser_pinning']} |")
    return lines + [""]


def sampler_sections(sampled):
    """Every repetition's span and what the sampler rule made of it, withdrawn ones too."""
    lines = ["## Sampler", "", check_results.SPSC_SAMPLER_RULE, "",
             "| Process | Started | Finished | Outcome |", "|---|---|---|---|"]
    for index, (begin, end, said) in enumerate(sampled["published"], start=1):
        lines.append(f"| repetition {index} | {begin} | {end} | {said} |")
    for index, (begin, end, why) in enumerate(sampled["withdrawn"], start=1):
        lines.append(f"| withdrawn {index} | {begin} | {end} | withdrawn: {why} |")
    return lines + [""]


def render(records, args, run_id, started, finished, machine_output, command, sampled=None,
           cross_core=None):
    first = records[0]
    suite = first["suite"]
    host = first["header"]["host"]
    day = first["header"]["dataset"].split(".")[0]
    summary, medians = summary_rows(records)
    lines = [f"# {SUITES[suite]['title']}, on {host}, {day}", "",
             "Generated by `scripts/spsc_driver.py` from raw records that are not committed. "
             "Do not edit by hand.", ""]
    lines += host_lines(records)
    lines += [describe(records), "", "## Header", ""]
    lines += header_lines(records, args, run_id, started, finished, sampled, cross_core)
    lines += ["", "## Summary", "",
              f"Each repetition is a fresh process. {len(records)} repetitions. "
              + ("Each runs every mode once untimed, as a warm-up, then every mode once timed; "
                 "the span is the replay of the whole day, from the first frame to the last "
                 "op applied, and nothing before or after. The time ratios are each "
                 "repetition's own, paired." if suite == "pipeline" else
                 "Each runs every row once, in a fresh queue, after its untimed start. An rtt "
                 "figure is the mean of one span over every round trip, a saturated figure the "
                 "items over the span, and a mean one-way figure the mean of the histogram, "
                 "measured from the push as the Latency section says: of "
                 "samples each a whole number of timer quanta, read on two cores, so a mean under "
                 "the floor is an average over many reads, not one the clock resolved."),
              "", "| Figure | Median | Min | Max | CV |", "|---|---|---|---|---|"]
    lines += summary
    if suite != "pipeline":
        lines += ["", "## Latency", "",
                  "One-way, measured from the push: from the sender's clock read just before it "
                  "pushes the item to the receiver's just after it pops it, in ns, each cell the "
                  "median over the repetitions. The two reads are on two cores, so the pair cost "
                  "is not subtracted. `below floor` is under the timer's floor of "
                  f"{first['header']['floor']}, where this clock cannot resolve a figure. A send "
                  "is late when it goes a whole gap behind its time, or, in burst, after the next "
                  f"message was due. {check_results.SPSC_LATE_SHARE}", "",
                  "| Queue | Protocol | " + " | ".join(
                      f"{name} {check_results.SPSC_FROM_PUSH}" for name, _ in PERCENTILES)
                  + " | Late sends | Late share | Samples |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        lines += latency_rows(records)
        lines += ["", check_results.SPSC_LEFT_OUT]
    else:
        lines += [""] + pipeline_sections(records)[:-1]
    lines += ["", "## Targets", "",
              "Each ratio is of the summary's medians above" +
              (", the pipelined one a median of paired ratios." if suite == "pipeline" else ".")
              + " Every bar is BENCHMARKS.md's.", "",
              "| Target | Comparison | Bar | Measured | Host | Verdict |",
              "|---|---|---|---|---|---|"]
    lines += target_rows(records, medians)
    if suite == "pipeline":
        lines += ["", "## Repetitions", "",
                  "| Repetition | Mode | Timed pass s | Messages | Ops | Full waits | Empty waits "
                  "| Checkpoint hash | Book hash | Peak RSS GiB |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
    else:
        lines += ["", "## Repetitions", "",
                  "Each row's figure as in the summary, and the core each thread last ran on.", "",
                  "| Repetition | Queue | Protocol | Figure | This thread's core | Peer's core |",
                  "|---|---|---|---|---|---|"]
    lines += repetition_rows(records)
    if bench_driver.swap_watch is not None:
        lines += ["", bench_driver.swap_watch.line()]
    if sampled is not None:
        lines += [""] + sampler_sections(sampled)[:-1]
    lines += ["", "## machine_check output", "", "```", machine_output, "```", "",
              "## Commands", "", "```", command, "```", ""]
    return "\n".join(lines)


def relative_command(args):
    def shown_path(path):
        # Absolute without following links, so that a data/ link in a worktree shows as data/.
        path = Path(os.path.abspath(path))
        try:
            return str(path.relative_to(REPO_ROOT))
        except ValueError:
            return f"<raw>/{path.name}"
    words = ["python3", "-B", "scripts/spsc_driver.py", "--suite", args.suite,
             "--bench", shown_path(args.bench), "--probe", shown_path(args.probe),
             "--input", shown_path(args.input), "--prediction", args.prediction,
             "--repetitions", str(args.repetitions), "--raw-dir", "<raw>",
             "--out", shown_path(args.out)]
    if args.suite == "pipeline":
        words += ["--checkpoint-ops", str(args.checkpoint_ops)]
    for flag, value in (("--core", args.core), ("--peer-core", args.peer_core),
                        ("--sibling", args.sibling), ("--peer-sibling", args.peer_sibling)):
        if value is not None:
            words += [flag, str(value)]
    # The Mac's waits, when they are not the defaults, are part of what ran.
    for flag, value, default in (("--quiet-before", args.quiet_before, 0.0),
                                 ("--quiet-wait", args.quiet_wait, QUIET_WAIT_S),
                                 ("--most-withdrawn", args.most_withdrawn, MOST_WITHDRAWN)):
        if value != default:
            words += [flag, f"{value:g}"]
    for argument in args.bench_arg:
        words += [f"--bench-arg={argument}"]
    text = " ".join(shlex.quote(word) if "<" not in word else word for word in words)
    return f"{args.wrapped_by} {text}" if args.wrapped_by else text


def withdraw(args, index, begin, end, why, sampled, commit):
    """One repetition the sampler rule does not publish: its raw files set aside, its own row
    in the run log, and its span in the results file."""
    sampled["withdrawn"].append((begin, end, why))
    count = len(sampled["withdrawn"])
    set_aside(args, index, count)
    bench_driver.append_log(
        args.run_log, f"{args.prediction}-{begin.replace(':', '').replace('-', '')}",
        args.prediction, commit,
        f"withdrawn: {args.suite}, repetition {index} on the Mac, exit 0; {why}, inside its "
        f"span, so the sampler rule does not publish it; nothing from it is published, and it "
        f"is rerun under {args.prediction}; the row was written by the driver")
    print(f"spsc_driver: repetition {index} withdrawn: {why}", file=sys.stderr)
    if count > args.most_withdrawn:
        raise Refused(f"repetition {index}", f"{count} repetitions withdrawn under the sampler "
                                             f"rule, over the {args.most_withdrawn} allowed")


def run(args, run_id, started, commit, sampler):
    bench_driver.swap_watch = bench_driver.SwapWatch(args.vmstat)
    bench_driver.set_stage("hashing the input")
    digest = bench_driver.hash_and_fault_in(args.input)
    args.record.dataset(args.input, digest)
    bench_driver.set_stage("checking the prediction")
    code = subprocess.run([sys.executable, "-B", str(REPO_ROOT / "scripts" / "check_predictions.py"),
                           "--root", str(args.predictions_root), "--require", args.prediction],
                          capture_output=True, text=True, check=False)
    if code.returncode != 0:
        raise Refused("prediction", code.stdout.strip())
    bench_driver.set_stage("machine_check")
    machine_output = machine_checks(args)
    args.record.output(bench_driver.MACHINE_CHECK, machine_output)
    cross_core = cross_core_check(args)
    records = []
    sampled = None if sampler is None else {
        "every": sampler.every, "published": [], "withdrawn": []}
    while len(records) < args.repetitions:
        index = len(records) + 1
        wait_for_quiet(sampler, args.quiet_before, args.quiet_wait)
        if sampler is not None:
            # A wait for quiet, or a repetition withdrawn, can outlast the page cache's hold
            # on the day: the first run under the rule found 3% of it resident after 45 min.
            bench_driver.set_stage("reading the input again")
            if bench_driver.hash_and_fault_in(args.input) != digest:
                raise Refused("input", "the input changed during the run")
        begin = bench_driver.utc_now()
        record = run_process(args, index, commit, digest)
        validate_run(record, args.suite)
        if sampler is not None:
            # The end is stamped a second late, so that the span covers all of the process.
            time.sleep(1)
            end = bench_driver.utc_now()
            sampler.wait_past(end)
            lines = list(sampler.lines)
            why = span_unquiet(lines, begin, end)
            if why:
                withdraw(args, index, begin, end, why, sampled, commit)
                continue
            sampled["published"].append((begin, end, span_summary(lines, begin, end)))
        records.append(record)
    validate_together(records)
    bench_driver.set_stage("rendering the results file")
    text = render(records, args, run_id, started, bench_driver.utc_now(), machine_output,
                  relative_command(args), sampled=sampled, cross_core=cross_core)
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
    parser.add_argument("--suite", choices=tuple(SUITES), required=True)
    parser.add_argument("--bench", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--core", type=int)
    parser.add_argument("--peer-core", type=int)
    parser.add_argument("--sibling", type=int)
    parser.add_argument("--peer-sibling", type=int,
                        help="Linux: the peer core's SMT sibling, which must be idle")
    parser.add_argument("--quiet-before", type=float, default=0.0, metavar="S",
                        help="macOS: wait for S seconds with no other busy process before each "
                             "repetition")
    parser.add_argument("--quiet-wait", type=float, default=QUIET_WAIT_S, metavar="S",
                        help="macOS: refuse after waiting S seconds for quiet")
    parser.add_argument("--most-withdrawn", type=int, default=MOST_WITHDRAWN,
                        help="macOS: refuse after more repetitions than this are withdrawn")
    parser.add_argument("--checkpoint-ops", type=int, default=CHECKPOINT_OPS,
                        help="pipeline: hash the book after this many ops it acts on")
    parser.add_argument("--wrapped-by", help="what the run was started under, for the record")
    parser.add_argument("--allow-dirty", action="store_true", help="for tests, not for a report")
    parser.add_argument("--bench-arg", action="append", default=[], metavar="ARG")
    parser.add_argument("--predictions-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--run-log", type=Path, default=REPO_ROOT / "results" / "run_log.md",
                        help=argparse.SUPPRESS)
    parser.add_argument("--vmstat", type=Path, default=Path("/proc/vmstat"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--ps", default="ps", help=argparse.SUPPRESS)
    parser.add_argument("--sample-every", type=float, default=wasm_bench.SAMPLE_EVERY_S,
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.repetitions < 3:
        parser.error("median, min, max and CV need at least 3 repetitions")
    return args


# render

def published_sampled(published, raw_dir, every):
    """The Sampler section's spans, each outcome worked out again from the raw sampler.txt,
    refusing one the raw lines do not give back; None for a file with no Sampler section.
    The log's stamps are to the second, so this gives back the run's own verdicts only for
    a sampler that took at most one sample a second, as a run's, every 2 s, does."""
    if check_results.section(published, "Sampler") is None:
        return None
    if every is None:
        raise Refused("published", "a Sampler section with no interference field to say how "
                                   "often the sampler ran")
    path = raw_dir / "sampler.txt"
    if not path.is_file():
        raise Refused("raw", "the file has a Sampler section and the raw folder no sampler.txt")
    lines = path.read_text(encoding="utf-8").splitlines()
    sampled = {"every": every, "published": [], "withdrawn": []}
    for cells in check_results.table_rows(published, "Sampler"):
        if len(cells) != 4 or cells[0] == "Process":
            continue
        what, begin, end, said = cells
        why = span_unquiet(lines, begin, end)
        if what.startswith("repetition "):
            again = span_summary(lines, begin, end) if why is None else f"unquiet: {why}"
            sampled["published"].append((begin, end, again))
        elif what.startswith("withdrawn "):
            again = "quiet" if why is None else f"withdrawn: {why}"
            sampled["withdrawn"].append((begin, end, why))
        else:
            raise Refused("published", f"a Sampler row of no repetition: {what!r}")
        if again != said:
            raise Refused("raw", f"{what}: sampler.txt gives {again!r}, the file {said!r}")
    return sampled


def render_again(raw_dir, published, stderr, again=None):
    """The published file rendered again from its run's raw records, stamped. What the run
    printed only into its file, its command, run id and times, the machine check's output,
    the cross-core line and the swap line, is taken from it; every figure from the raw
    records, and every sampler verdict from the raw sampler.txt."""
    import stamp_load  # Here, since stamp_load imports bench_driver.

    fields = check_results.parse_header(published)
    command = (check_results.section(published, "Commands") or "").strip().split("\n```\n")[0]
    command = command.strip().strip("`").strip()
    wrapped, found, words = command.partition("python3 -B scripts/spsc_driver.py ")
    if not found:
        raise Refused("published", "no spsc_driver.py command in its Commands section")
    args = parse_args(shlex.split(words))
    args.wrapped_by = wrapped.strip() or None
    if relative_command(args) != command:
        raise Refused("published", "its command is not the one this driver prints for it")
    records = []
    for index in range(1, args.repetitions + 1):
        record = bench_driver.raw_record(raw_dir, f"{args.suite}-repetition-{index}")
        validate_run(record, args.suite)
        records.append(record)
    validate_together(records)
    machine = (check_results.section(published, "machine_check output") or "").strip()
    machine = machine.removeprefix("```").removesuffix("```").strip("\n")
    cross_core = fields.get("cross_core_timer")
    needs = (bench_driver.MACHINE_CHECK,
             *((CROSS_CORE,) if cross_core else ()),
             *(("sampler.txt",) if check_results.section(published, "Sampler") else ()))
    run_record.refuse_incomplete(raw_dir, needs, fields.get("run_id"))
    if run_record.is_new_kind(raw_dir):
        # The run kept each tool's output: the render quotes those, not the file.
        machine = run_record.kept_output(raw_dir, bench_driver.MACHINE_CHECK)
        if cross_core:
            cross_core = cross_core_line(run_record.kept_output(raw_dir, CROSS_CORE))
    swap = next((line for line in published.splitlines()
                 if line.startswith(("The kernel's pswpout", "Swap-outs are not read"))), None)
    # How often the sampler ran is not on the command line (--sample-every is for tests).
    every = re.match(r"sampled every ([\d.]+) s ", fields.get("interference", ""))
    sampled = published_sampled(published, raw_dir, float(every.group(1)) if every else None)
    bench_driver.swap_watch = bench_driver.PrintedSwap(swap) if swap else None
    try:
        text = render(records, args, fields["run_id"], fields["run_started"],
                      fields["run_finished"], machine, command, sampled=sampled,
                      cross_core=cross_core)
    finally:
        bench_driver.swap_watch = None
    text = bench_driver.with_earlier_renders(text, published, again)
    return Path(args.out).name, stamp_load.stamp(text, stderr)


def render_main(argv):
    parser = argparse.ArgumentParser(prog="spsc_driver.py render",
                                     description="render a published file again from raw")
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--published", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--stderr", type=Path, required=True, help="measure.sh's stderr")
    parser.add_argument("--rendered-again", metavar="REASON")
    args = parser.parse_args(argv)
    try:
        published = args.published.read_text(encoding="utf-8")
        name, text = render_again(args.raw_dir, published,
                                  args.stderr.read_text(encoding="utf-8"), args.rendered_again)
        if name != args.published.name:
            raise Refused("published", f"its command writes {name}, not {args.published.name}")
        if text.split("\n", 1)[0] != published.split("\n", 1)[0]:
            raise Refused("title", "the render's title is not the published one")
        violations = check_results.check_text(text)
        if violations:
            raise Refused("results", f"{name}: " + "; ".join(
                f"{code}: {detail}" for code, detail in violations))
    except (Refused, run_record.Incomplete, OSError, ValueError, KeyError) as error:
        print(f"spsc_driver: render refused: {error}", file=sys.stderr)
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / name).write_text(text, encoding="utf-8")
    print(f"spsc_driver: wrote {args.out_dir / name}", file=sys.stderr)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["render"]:
        return render_main(argv[1:])
    args = parse_args(argv)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, bench_driver.forward_signal)
    sampler = None
    started = bench_driver.utc_now()
    run_id = f"{args.prediction}-{started.replace(':', '').replace('-', '')}"
    commit = "unknown"
    try:
        run_record.open_run(args, run_id)
        if platform.system() == "Darwin":
            sampler = Sampler(args.raw_dir / "sampler.txt", args.ps, args.sample_every)
            sampler.start()
        commit = bench_driver.git_commit(REPO_ROOT, args.allow_dirty)
        run_record.start_record(args, "spsc_driver", run_id, commit, (args.bench, args.probe))
        written = run(args, run_id, started, commit, sampler)
    except Stopped as stop:
        bench_driver.stop_child()
        name = signal.Signals(stop.signum).name
        print(f"spsc_driver: stopped by {name} at {bench_driver.stage}", file=sys.stderr)
        bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                                f"stopped by {name} at {bench_driver.stage}; nothing from it is "
                                "published; the row was written by the driver as it stopped")
        return 128 + stop.signum
    except (Refused, run_record.Incomplete) as refusal:
        bench_driver.hold_signals()
        print(f"spsc_driver: refused at {refusal.stage}: {refusal}", file=sys.stderr)
        bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                                f"refused at {refusal.stage}: {refusal}")
        return 1
    except Exception as error:  # Last, so that no run ends without its row.
        return bench_driver.log_failure("spsc_driver", args.run_log, run_id, args.prediction, commit,
                                        error)
    finally:
        run_record.close_sampler(args, sampler)
    bench_driver.hold_signals()
    bench_driver.append_written(args.run_log, run_id, args.prediction, commit, [written])
    print(f"spsc_driver: wrote {written}, load lines pending. Now run scripts/stamp_load.py.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
