#!/usr/bin/env python3
"""Runs T-ARENA-1's allocator arms in fresh processes and writes one results file.

    alloc_driver.py --bench BENCH_REPLAY --probe BENCH_PROBE --alloc-tool ITCH_ALLOC \\
        --compare-tool ITCH_MAP_COMPARE --input FILE --prediction P-NNNN --raw-dir DIR \\
        --out results/alloc_arms_HOST_DAY.md [--arm ARM]... [--pages REQUEST] \\
        [--repetitions N] [--core N] [--sibling N] [--quiet-before S] [--wrapped-by TEXT] \\
        [--allow-dirty] [--bench-arg ARG]...

Each arm is the node book of include/itch/node_book.hpp on one allocator policy, run by
bench_replay as the design alloc-ARM: malloc-fresh, malloc-aged, mimalloc, pmr-pool,
pmr-monotonic, pool-index, pool-pointer and pool-fifo, every one by default. On a reported
run this is measure.sh's direct child, as scripts/bench_driver.py is, and it shares that
driver's order and its signal handling:

    measure.sh /usr/bin/time -l python3 -B scripts/alloc_driver.py ... 2> STDERR
    python3 -B scripts/stamp_load.py --results FILE --stderr STDERR

Hash the input, which also faults it in; confirm the prediction is on record; run
machine_check.sh, refusing on a failure; run the repetitions, each running every arm in
turn, the order turned by one each time, every arm in two processes as bench_driver.py
runs a design: the warm-up and the two timed passes, then the untimed pass of memory, with
the day read into RAM. Then, untimed, each arm through itch_alloc --allocator, for its
allocations, and itch_map_compare --allocator, for its book against the fast book's. Refuse
if any process refused, if a record is not of its arm, if any book disagreed, if a pool arm
allocated, or if the kernel swapped a page out. Write the results file with its load lines
pending.

A figure is the median of the repetitions with min, max and CV beside it. A latency cell
is the median of the repetitions' figures, and `below floor` when any repetition could not
resolve it. Each timed pass carries the page faults it took, from getrusage on each side of
it, and the pool arms' must be zero: that is T-ARENA-1's fault check, which can fail, and
whose verdict goes in the header and the Page faults section. A heap arm's faults are shown
and judged by nothing.

On macOS the Mac's sampler rule applies to every timed process, as spsc_driver.py applies
it to a repetition: the sampler of scripts/wasm_bench.py runs beside the whole run, and a
timed process is published only if it covered the process's whole span with no gap over
5 s and saw no foreign process in it. Otherwise it is withdrawn, its raw files set aside,
with its own row in the run log, and run again. A process withdrawn --most-in-a-row
times running (3 unless told) ends the run, refused, so that a busy Mac is given back
rather than held while it stays busy; the run is tried again later. With --quiet-before S, each timed process
first waits until the sampler has seen S seconds with no foreign process. The untimed
processes publish counts and memory, which no other process can move, and are not judged.

Every run gets a row in results/run_log.md, whatever became of it. Raw output stays in
--raw-dir, which is not committed.

Exit codes: 0 results written, 1 refused or failed, 2 bad usage, 128 + N stopped by
signal N.
"""

from __future__ import annotations

import argparse
import os
import platform
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
import spsc_driver  # noqa: E402
import wasm_bench  # noqa: E402

REPO_ROOT = bench_driver.REPO_ROOT
Refused = bench_driver.Refused
Stopped = bench_driver.Stopped
ARMS = check_results.ALLOC_ARMS
POOLS = check_results.ALLOC_POOLS
PAGE_REQUESTS = ("base", "huge-if-offered", "transparent", "explicit")
OPERATIONS = ("all",) + bench_driver.OPERATIONS
PERCENTILES = (50.0, 99.0, 99.9)
BASELINE = "malloc-fresh"
MOST_WITHDRAWN = 40
MOST_IN_A_ROW = 3
QUIET_WAIT_S = 4 * 3600


def design(arm):
    return f"alloc-{arm}"


def rotated(arms, index):
    turn = (index - 1) % len(arms)
    return arms[turn:] + arms[:turn]


# the processes

def set_aside(args, arm, index, count):
    """Keeps a withdrawn timed process's raw files under a name of their own."""
    for suffix in (".json", ".log"):
        path = args.raw_dir / f"{design(arm)}-repetition-{index}-timed{suffix}"
        if path.is_file():
            path.rename(args.raw_dir /
                        f"{design(arm)}-repetition-{index}-timed-withdrawn-{count}{suffix}")


def validate_arm(record, arm):
    """What one arm's timed record must show, beyond what bench_driver checks of any book."""
    named = record["header"].get("design", "").split(":")[0]
    if named != design(arm):
        raise Refused("record", f"the record is of {named!r}, not {design(arm)}")
    bench_driver.validate_run(record)
    for name in TIMED:
        if arm in POOLS and record[name].get("faults") is None:
            raise Refused("record", f"{arm}: the {name} read no page faults, so the fault "
                                    "check cannot be made")
    if arm in POOLS and "pool_pages" not in record["header"]:
        raise Refused("record", f"{arm}: no pool_pages in the header")


def withdraw(args, arm, index, begin, end, why, sampled, commit):
    sampled["withdrawn"].append((f"{arm}, repetition {index}", begin, end, why))
    count = len(sampled["withdrawn"])
    set_aside(args, arm, index, count)
    bench_driver.append_log(
        args.run_log, f"{args.prediction}-{begin.replace(':', '').replace('-', '')}",
        args.prediction, commit,
        f"withdrawn: alloc arms, {arm}, repetition {index} on the Mac, exit 0; {why}, inside "
        f"its span, so the sampler rule does not publish it; nothing from it is published, "
        f"and it is rerun under {args.prediction}; the row was written by the driver")
    print(f"alloc_driver: {arm} repetition {index} withdrawn: {why}", file=sys.stderr)
    if count > args.most_withdrawn:
        raise Refused(f"{arm}, repetition {index}",
                      f"{count} timed processes withdrawn under the sampler rule, over the "
                      f"{args.most_withdrawn} allowed")


def timed_process(args, arm, index, commit, digest, sampler, sampled):
    """One arm's timed process, rerun until the sampler rule publishes it."""
    in_a_row = 0
    while True:
        spsc_driver.wait_for_quiet(sampler, args.quiet_before, args.quiet_wait)
        if sampler is not None:
            bench_driver.set_stage("reading the input again")
            if bench_driver.hash_and_fault_in(args.input) != digest:
                raise Refused("input", "the input changed during the run")
        begin = bench_driver.utc_now()
        record = bench_driver.run_process(args, index, commit, digest, design(arm), "timed")
        validate_arm(record, arm)
        if sampler is None:
            return record
        time.sleep(1)  # So that the span covers all of the process.
        end = bench_driver.utc_now()
        sampler.wait_past(end)
        lines = list(sampler.lines)
        why = spsc_driver.span_unquiet(lines, begin, end)
        if why:
            withdraw(args, arm, index, begin, end, why, sampled, commit)
            in_a_row += 1
            if in_a_row >= args.most_in_a_row:
                raise Refused(f"{arm}, repetition {index}",
                              f"withdrawn {in_a_row} times in a row under the sampler rule, so "
                              "the Mac is given back and the run is tried again later")
            continue
        sampled["published"].append((f"{arm}, repetition {index}", begin, end,
                                     spsc_driver.span_summary(lines, begin, end)))
        return record


def untimed_process(args, arm, index, commit, digest, record):
    untimed = bench_driver.run_process(args, index, commit, digest, design(arm), "untimed")
    memory = untimed.get("memory", {})
    if not memory.get("complete") or not memory.get("first_book"):
        raise Refused(f"{arm}, repetition {index}", "the untimed process's memory figure is not "
                                                    "the first book's, or a read of it failed")
    record["memory"] = memory
    record["counters"] = untimed["counters"]
    for name in ("counters", "rdpmc"):
        record["header"][name] = untimed["header"][name]
    return record


def tool_lines(command, log_path):
    """A tool's output as key and value, from its first word, and its exit code."""
    bench_driver.set_stage(f"{Path(command[0]).name} {' '.join(command[2:])}")
    with open(log_path, "w", encoding="utf-8") as log:
        bench_driver.current_child = subprocess.Popen(command, stdout=log,
                                                      stderr=subprocess.STDOUT)
        code = bench_driver.current_child.wait()
        bench_driver.current_child = None
    values = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(" ")
        values.setdefault(key, value)
    return code, values


def arm_checks(args, arm):
    """itch_alloc and itch_map_compare on this arm, untimed: its calls and its book."""
    pages = ["--pages", args.pages]
    code, alloc = tool_lines([str(args.alloc_tool), str(args.input), "--allocator", arm, *pages],
                             args.raw_dir / f"itch_alloc-{arm}.log")
    verdict = alloc.get("verdict", "")
    wanted = "PASS" if arm in POOLS else "REPORT"
    if code != 0 or not verdict.startswith(f"T-ARENA-1-ALLOC {wanted} "):
        raise Refused(f"itch_alloc {arm}", f"exited {code}: {verdict or 'no verdict'}")
    code, compare = tool_lines([str(args.compare_tool), str(args.input), "--allocator", arm,
                                *pages], args.raw_dir / f"itch_map_compare-{arm}.log")
    if code != 0 or compare.get("verdict") != "NODE-BOOK PASS" or compare.get("divergences") != "0":
        raise Refused(f"itch_map_compare {arm}",
                      f"exited {code}: {compare.get('verdict', 'no verdict')}, "
                      f"{compare.get('divergences', '?')} divergences")
    return {"alloc": alloc, "compare": compare}


# the results file

def spread(values):
    return bench_driver.spread(values)


def faults_of(record, name):
    return record[name].get("faults")


TIMED = ("throughput", "latency_pass")


def fault_verdict(runs):
    """T-ARENA-1's fault check over every pool arm that ran: PASS only on no fault at all."""
    pools = [arm for arm in POOLS if arm in runs]
    faulted = []
    for arm in pools:
        counts = [faults_of(r, name) for r in runs[arm] for name in TIMED]
        if any(count != 0 for count in counts):
            faulted.append(f"{arm} {sum(count or 0 for count in counts):,}")
    if not pools:
        return f"{check_results.FAULTS_NOT_RUN}no pool arm ran"
    if faulted:
        return f"FAIL: page faults in the timed passes of {', '.join(faulted)}"
    return (f"PASS: no page fault in any timed pass of {', '.join(pools)}, "
            f"{2 * len(runs[pools[0]])} passes each")


def percentile_value(record, operation, percentile):
    if operation == "all":
        return next(entry["value_ns"] for entry in record["latency"]["percentiles"]
                    if entry["percentile"] == percentile)
    return bench_driver.operation_value(record, operation, percentile)


def latency_cell(records, operation, percentile):
    values = [percentile_value(r, operation, percentile) for r in records]
    if any(value is None for value in values):
        return check_results.BELOW_FLOOR
    return f"{statistics.median(values):.1f}"


def samples_of(record, operation):
    if operation == "all":
        return record["latency"]["samples"]
    return next(item["samples"] for item in record["latency"]["operations"]
                if item["op"] == operation)


def summary_rows(runs):
    rows = []
    for arm, records in runs.items():
        for label, values, shape in (
                ("throughput, messages/s",
                 [r["throughput"]["messages_per_s"] for r in records], ",.0f"),
                ("mean service time per message, ns",
                 [1e9 * r["throughput"]["seconds"] / r["throughput"]["messages"]
                  for r in records], ".2f"),
                ("mean span of apply(), ns", [r["latency"]["mean_ns"] for r in records], ".2f"),
                ("book memory, MiB",
                 [r["memory"]["book_bytes"] / 1024 ** 2 for r in records], ",.1f")):
            median, low, high, variation = spread(values)
            rows.append(f"| {arm}, {label} | {median:{shape}} | {low:{shape}} | {high:{shape}} "
                        f"| {100 * variation:.2f}% |")
    return rows


def comparison_rows(runs):
    """Each arm's throughput over the baseline's, repetition by repetition."""
    if BASELINE not in runs:
        return []
    base = [r["throughput"]["messages_per_s"] for r in runs[BASELINE]]
    rows = []
    for arm, records in runs.items():
        if arm == BASELINE:
            continue
        ratios = [r["throughput"]["messages_per_s"] / b for r, b in zip(records, base)]
        median, low, high, variation = spread(ratios)
        rows.append(f"| {arm} | {median:.3f} | {low:.3f} | {high:.3f} | {100 * variation:.2f}% "
                    f"| {len(ratios)} |")
    return rows


def latency_rows(runs):
    rows = []
    for arm, records in runs.items():
        for operation in OPERATIONS:
            cells = [latency_cell(records, operation, p) for p in PERCENTILES]
            rows.append(f"| {arm} | {operation} | {' | '.join(cells)} "
                        f"| {samples_of(records[0], operation):,} |")
    return rows


def fault_rows(runs):
    rows = []
    for arm, records in runs.items():
        cells = []
        for name in TIMED:
            values = [faults_of(r, name) for r in records]
            cells.append("unread" if None in values else
                         " / ".join(f"{value:,}" for value in values))
        if arm in POOLS:
            zero = all(faults_of(r, name) == 0 for r in records for name in TIMED)
            judged = check_results.FAULTS_ZERO if zero else check_results.FAULTS_FAULTED
        else:
            judged = check_results.FAULTS_HEAP
        rows.append(f"| {arm} | {cells[0]} | {cells[1]} | {judged} |")
    return rows


def pages_rows(runs):
    rows = []
    for arm, records in runs.items():
        if arm not in POOLS:
            rows.append(f"| {arm} | none: its nodes come from a heap |")
            continue
        seen = sorted({r["header"]["pool_pages"] for r in records})
        rows.append(f"| {arm} | {' / '.join(seen)} |")
    return rows


def allocation_rows(runs, checks):
    rows = []
    for arm in runs:
        alloc = checks[arm]["alloc"]
        verdict = alloc["verdict"].split()[1]
        rows.append(f"| {arm} | {alloc['node_allocations_counted_by']} "
                    f"| {int(alloc['node_book_allocations']):,} "
                    f"| {float(alloc['node_allocations_per_message']):.6f} "
                    f"| {int(alloc['node_nodes_made']):,} | {int(alloc['node_messages']):,} "
                    f"| {verdict} |")
    return rows


def agreement_rows(runs, checks):
    rows = []
    for arm in runs:
        compare = checks[arm]["compare"]
        rows.append(f"| {arm} | {compare['verdict'].split()[1]} "
                    f"| {int(compare['messages_compared']):,} "
                    f"| {int(compare['top_checks']):,} | {int(compare['periodic_hash_checks']):,} "
                    f"| {compare['divergences']} | `{compare['last_hash']}` |")
    return rows


def memory_rows(runs):
    rows = []
    for arm, records in runs.items():
        built = statistics.median(r["memory"]["built_bytes"] - r["memory"]["baseline_bytes"]
                                  for r in records) / 1024 ** 2
        book = statistics.median(r["memory"]["book_bytes"] for r in records) / 1024 ** 2
        peak = max(r["peak_rss_bytes"] for r in records) / 1024 ** 3
        rows.append(f"| {arm} | {built:,.1f} | {book:,.1f} | {peak:.2f} |")
    return rows


def repetition_rows(runs):
    rows = []
    for arm, records in runs.items():
        for index, record in enumerate(records, start=1):
            rows.append(f"| {arm} | {index} | {record['throughput']['messages_per_s']:,.0f} "
                        f"| {record['latency']['mean_ns']:.2f} "
                        f"| {record['instrumentation_ratio']:.4f} "
                        f"| {record['timer']['pair_ns']:.3f} | {record['migrations']} "
                        f"| {record['peak_rss_bytes'] / 1024 ** 3:.2f} |")
    return rows


def sampler_rows(sampled):
    lines = ["## Sampler", "", check_results.ALLOC_SAMPLER_RULE, "",
             "| Process | Started | Finished | Outcome |", "|---|---|---|---|"]
    for what, begin, end, said in sampled["published"]:
        lines.append(f"| {what} | {begin} | {end} | {said} |")
    for index, (what, begin, end, why) in enumerate(sampled["withdrawn"], start=1):
        lines.append(f"| withdrawn {index}: {what} | {begin} | {end} | withdrawn: {why} |")
    return lines


def header_lines(runs, args, run_id, started, finished, sampled):
    first = next(iter(runs.values()))[0]
    header = dict(first["header"])
    header["design"] = ("NodeBook, linked levels and FIFOs of nodes over handles, with only "
                        "the allocator changed between arms: see Arms")
    lines = [f"- {name}: {header[name]}" for name in bench_driver.HEADER_ORDER
             if name in header]
    capacities = {r["header"].get("node_capacity") for records in runs.values() for r in records}
    if len(capacities) != 1 or None in capacities:
        raise Refused("record", f"the arms ran on other capacities: {sorted(map(str, capacities))}")
    lines += [f"- allocators: {', '.join(runs)}",
              f"- node_capacity: {capacities.pop()}, the same for every arm",
              f"- pages_requested: {args.pages}",
              f"- fault_check: {fault_verdict(runs)}"]
    if sampled is not None:
        lines.append(f"- interference: {check_results.spsc_interference(sampled['every'])}; "
                     f"{len(sampled['withdrawn'])} withdrawn under the rule")
    lines += [f"- measure_load_start: {check_results.PENDING}",
              f"- measure_load_end: {check_results.PENDING}",
              *bench_driver.peak_lines(next(iter(runs.values()))),
              f"- prediction: {args.prediction}",
              f"- run_id: {run_id}",
              f"- repetitions: {len(next(iter(runs.values())))}",
              f"- warmup_messages: {first['warmup']['messages']:,}",
              f"- timed_messages: {first['throughput']['messages']:,}",
              f"- book_messages: {first['mix']['book_messages']:,}",
              f"- run_started: {started}",
              f"- run_finished: {finished}"]
    return lines


def render(runs, checks, args, run_id, started, finished, machine_output, command,
           sampled=None):
    first = next(iter(runs.values()))[0]
    host = first["header"]["host"]
    day = first["header"]["dataset"].split(".")[0]
    repetitions = len(next(iter(runs.values())))
    lines = [f"# T-ARENA-1: the node book on each allocator arm, on {host}, {day}", "",
             "Generated by `scripts/alloc_driver.py` from raw records that are not committed. "
             "Do not edit by hand.", "",
             f"Host: **{host}**. Numbers from the two hosts are never mixed.", ""]
    if host == check_results.MAC_HOST:
        lines += [f"**{host} is not a pinned host.** macOS offers no way to pin a thread, so "
                  "each process ran wherever the scheduler put it, under `measure.sh`'s lock and "
                  "the Mac's sampler rule. Every figure here is a macOS figure.", ""]
    lines += [
        "One book, `include/itch/node_book.hpp`, on each allocator arm of "
        "`include/itch/node_arms.hpp`: a linked-levels book whose orders and levels are nodes, "
        "each taken from the arm when it opens and given back when it closes, so that the "
        "allocator sits on the hot path of every add, delete and replace. Between arms only "
        "the allocator changes: the index (16-byte slots, open addressing, sized up front for "
        "the capacity), the side table, the walk and the capacity rule are one code. The pool "
        "arms hold their nodes by 32-bit index or by pointer, so their node is 24 or 40 bytes; "
        "every heap arm holds them by pointer, as the pool by pointer does. Its book matches "
        "the fast book's message by message on the day (Book agreement below), and the fast "
        "book matches the oracle (`results/oracle_12302019.md`).", "",
        "## Header", ""]
    lines += header_lines(runs, args, run_id, started, finished, sampled)
    lines += ["", "## Arms", "", "| Arm | Design, as bench_replay names it |", "|---|---|"]
    lines += [f"| {arm} | {records[0]['header']['design']} |" for arm, records in runs.items()]
    lines += ["", "## Summary", "",
              f"Each arm ran in {repetitions} repetitions, each a fresh process: the warm-up, on "
              "the fast book for every arm, then each timed pass's code warmed on a node book of "
              "its own over the day's first book operations, untimed, then a throughput pass and "
              "a latency pass, each on a fresh node book, and a second process for the untimed "
              "pass of memory. Every arm "
              "ran in each repetition, one after another, the order turned by one each time. "
              "The day was read into RAM before the warm-up. The service time is the throughput "
              "pass over every message, frame walk included.", "",
              "| Figure | Median | Min | Max | CV |", "|---|---|---|---|---|"]
    lines += summary_rows(runs)
    comparison = comparison_rows(runs)
    if comparison:
        lines += ["", "## Comparison", "",
                  f"Each arm's throughput over {BASELINE}'s, repetition by repetition, since "
                  "each repetition ran every arm.", "",
                  "| Arm | Median ratio | Min | Max | CV | Repetitions |",
                  "|---|---|---|---|---|---|", *comparison]
    lines += ["", "## Latency", "",
              "One span is one `apply()`, the frame walk outside it, by what the message asks of "
              "the book; `reduce` is `E`, `C` and `X`. Each cell is the median over the "
              f"repetitions in ns, raw, with the empty pair inside. `{check_results.BELOW_FLOOR}` "
              f"is under the timer's floor of {first['header']['floor']}, where this clock "
              "cannot resolve a figure, in any repetition.", "",
              "| Arm | Operation | p50 | p99 | p99.9 | Samples |", "|---|---|---|---|---|---|"]
    lines += latency_rows(runs)
    lines += ["", "## Page faults", "",
              "Page faults, minor and major, from getrusage just before each timed pass started "
              "and just after it stopped, one figure per repetition. A pool arm maps and touches "
              "every node before the pass, so it must take none: T-ARENA-1's fault check, "
              f"`fault_check` in the header. A heap arm's check reads `{check_results.FAULTS_HEAP}`: "
              "it takes its pages from the system as its book grows, so it faults by design.", "",
              "| Arm | Throughput pass | Latency pass | Check |", "|---|---|---|---|"]
    lines += fault_rows(runs)
    lines += ["", "## Pages", "",
              "What each pool arm asked for and got, from the pool's own record of every "
              "mapping, for each book a repetition built; a size is claimed only where the "
              "kernel said so.", "", "| Arm | Pages |", "|---|---|"]
    lines += pages_rows(runs)
    lines += ["", "## Allocations", "",
              "`itch_alloc --allocator`, untimed, on the whole day, counted from the first "
              "message after the book was built. A pool arm passes on none. A heap arm "
              "allocates by design, so it is reported: the global operator new's calls, which "
              "is all the counter sees, the aged heap's ballast taken off, or for mimalloc, "
              "whose `mi_new` the counter never sees, one call per node the book made.", "",
              "| Arm | Counted by | Calls | Per message | Nodes made | Messages | Verdict |",
              "|---|---|---|---|---|---|---|"]
    lines += allocation_rows(runs, checks)
    lines += ["", "## Book agreement", "",
              "`itch_map_compare --allocator`, untimed, on the whole day: after every message "
              "the node book and the fast book gave the same status and the same top of book "
              "on both sides of its locate, and every 1,000,000 messages and at the end the "
              "canonical hash of each whole book (`tests/oracle/canonical.hpp`) agreed.", "",
              "| Arm | Verdict | Messages | Top checks | Periodic hash checks | Divergences "
              "| Last hash |", "|---|---|---|---|---|---|---|"]
    lines += agreement_rows(runs, checks)
    lines += ["", "## Memory", "",
              f"From the untimed process, which builds no other book first, read from "
              f"{first['memory']['source']}: what the book was charged once built, before any "
              "message, and at its most over the day, less what the process held before it "
              "was built, medians in MiB; and the largest getrusage peak of the timed process, "
              "which holds the day's copy and the latency samples too.", "",
              "| Arm | Built, MiB | Book at its most, MiB | Timed process peak RSS, GiB |",
              "|---|---|---|---|"]
    lines += memory_rows(runs)
    lines += ["", "## Targets", "",
              "| Target | Bar | Measured here | Verdict |", "|---|---|---|---|",
              f"| T-ARENA-1 | none: UNCONFIRMED in BENCHMARKS.md | throughput and p50, p99 and "
              f"p99.9 by operation for each arm; page faults in the timed passes "
              f"| {check_results.ARENA_VERDICT}{fault_verdict(runs)} |"]
    lines += ["", "## Repetitions", "",
              "| Arm | Repetition | Messages/s | Mean span ns | Instrumentation ratio "
              "| Empty pair ns | Migrations | Peak RSS GiB |", "|---|---|---|---|---|---|---|---|"]
    lines += repetition_rows(runs)
    if bench_driver.swap_watch is not None:
        lines += ["", bench_driver.swap_watch.line()]
    if sampled is not None:
        lines += [""] + sampler_rows(sampled)
    lines += ["", "## machine_check output", "", "```", machine_output, "```", "",
              "## Commands", "", "```", command, "```", ""]
    return "\n".join(lines)


def relative_command(args):
    def shown(path):
        path = Path(os.path.abspath(path))
        try:
            return str(path.relative_to(REPO_ROOT))
        except ValueError:
            return f"<raw>/{path.name}"
    words = ["python3", "-B", "scripts/alloc_driver.py", "--bench", shown(args.bench),
             "--probe", shown(args.probe), "--alloc-tool", shown(args.alloc_tool),
             "--compare-tool", shown(args.compare_tool), "--input", shown(args.input),
             "--prediction", args.prediction, "--repetitions", str(args.repetitions),
             "--pages", args.pages, "--raw-dir", "<raw>", "--out", shown(args.out)]
    if args.arm != list(ARMS):
        for arm in args.arm:
            words += ["--arm", arm]
    for flag, value in (("--core", args.core), ("--quiet-before", args.quiet_before or None)):
        if value is not None:
            words += [flag, f"{value:g}" if isinstance(value, float) else str(value)]
    for argument in args.user_bench_arg:
        words += [f"--bench-arg={argument}"]
    text = " ".join(shlex.quote(word) if "<" not in word else word for word in words)
    return f"{args.wrapped_by} {text}" if args.wrapped_by else text


def run(args, run_id, started, commit, sampler):
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
    sampled = None if sampler is None else {"every": sampler.every, "published": [],
                                            "withdrawn": []}
    runs = {arm: [] for arm in args.arm}
    for index in range(1, args.repetitions + 1):
        for arm in rotated(args.arm, index):
            record = timed_process(args, arm, index, commit, digest, sampler, sampled)
            runs[arm].append(untimed_process(args, arm, index, commit, digest, record))
    bench_driver.validate_together({design(arm): records for arm, records in runs.items()})
    checks = {arm: arm_checks(args, arm) for arm in args.arm}
    made = {arm: c["alloc"].get("node_nodes_made") for arm, c in checks.items()}
    if len(set(made.values())) != 1:
        raise Refused("itch_alloc", f"the arms made other numbers of nodes: {made}")
    hashes = {arm: c["compare"].get("last_hash") for arm, c in checks.items()}
    if len(set(hashes.values())) != 1:
        raise Refused("itch_map_compare", f"the arms ended on other books: {hashes}")
    if bench_driver.swap_watch is not None:
        bench_driver.swap_watch.check("the checks")
    bench_driver.set_stage("rendering the results file")
    text = render(runs, checks, args, run_id, started, bench_driver.utc_now(), machine_output,
                  relative_command(args), sampled)
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
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--alloc-tool", type=Path, required=True)
    parser.add_argument("--compare-tool", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--arm", action="append", default=[], choices=ARMS,
                        help="repeatable; every arm by default")
    parser.add_argument("--pages", choices=PAGE_REQUESTS, default="huge-if-offered",
                        help="what the pool arms map their nodes on")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--core", type=int)
    parser.add_argument("--sibling", type=int)
    parser.add_argument("--quiet-before", type=float, default=0.0, metavar="S",
                        help="macOS: wait for S seconds with no other busy process before each "
                             "timed process")
    parser.add_argument("--quiet-wait", type=float, default=QUIET_WAIT_S, metavar="S")
    parser.add_argument("--most-withdrawn", type=int, default=MOST_WITHDRAWN)
    parser.add_argument("--most-in-a-row", type=int, default=MOST_IN_A_ROW, metavar="N",
                        help="end the run once one timed process is withdrawn N times running")
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
    if len(args.arm) != len(set(args.arm)):
        parser.error("an arm is named twice")
    args.arm = args.arm or list(ARMS)
    # What bench_driver.run_process reads of a run: the day in RAM, no top of book, the
    # node book's own capacity, and the pool's pages before anything the caller adds.
    args.input_mode = "ram"
    args.track_top = False
    args.orders = None
    args.levels = None
    args.throughput_only = []
    args.user_bench_arg = list(args.bench_arg)
    args.bench_arg = ["--pages", args.pages, *args.bench_arg]
    return args


def main(argv=None):
    args = parse_args(argv)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, bench_driver.forward_signal)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    sampler = None
    if platform.system() == "Darwin":
        sampler = spsc_driver.Sampler(args.raw_dir / "sampler.txt", args.ps, args.sample_every)
        sampler.start()
    started = bench_driver.utc_now()
    run_id = f"{args.prediction}-{started.replace(':', '').replace('-', '')}"
    commit = "unknown"
    try:
        commit = bench_driver.git_commit(REPO_ROOT, args.allow_dirty)
        written = run(args, run_id, started, commit, sampler)
    except Stopped as stop:
        bench_driver.stop_child()
        name = signal.Signals(stop.signum).name
        print(f"alloc_driver: stopped by {name} at {bench_driver.stage}", file=sys.stderr)
        bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                                f"stopped by {name} at {bench_driver.stage}; nothing from it is "
                                "published; the row was written by the driver as it stopped")
        return 128 + stop.signum
    except Refused as refusal:
        bench_driver.hold_signals()
        print(f"alloc_driver: refused at {refusal.stage}: {refusal}", file=sys.stderr)
        bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                                f"refused at {refusal.stage}: {refusal}")
        return 1
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as error:
        bench_driver.hold_signals()
        print(f"alloc_driver: failed: {error!r}", file=sys.stderr)
        bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                                f"failed: {error!r}")
        return 1
    finally:
        if sampler is not None:
            sampler.done.set()
    bench_driver.hold_signals()
    bench_driver.append_log(args.run_log, run_id, args.prediction, commit, "published")
    print(f"alloc_driver: wrote {written}, load lines pending. Now run scripts/stamp_load.py.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
