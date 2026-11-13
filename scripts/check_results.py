#!/usr/bin/env python3
"""Refuses a benchmark results file that claims more than the run could show.

    check_results.py FILE...

Reads the file as published, so it holds whatever wrote it to the same rules: every
header field present, an unreadable one written as `unavailable: <reason>`, no pin that
was not read back, no percentile below the timer's floor, no multiplexed counter called
measured, the warm-up out of the timed counts, the input resident, load lines stamped
from a real measure.sh run, a quiet CPU sampler beside any macOS run from 2026-11-05 on,
machine_check's output present and passing, and nothing private. scripts/bench_driver.py and scripts/stamp_load.py run the same checks, so that
a file failing them is never written as finished.

A backtest file, one with a `workload` field (scripts/backtest_driver.py), has no latency
or counter tables. It is held instead to its workload's fields, the summary's three rates
with each median inside its min and max and matching the median of the repetitions table,
a statement of what one event is and of the rate's denominator, the counts with the queue
anomalies, the macOS note on a Mac, and its target's verdict in the fixed words, consistent
with the median and the bar: T-BT-1's bar was set on x86, so a macOS file may not call it
met or missed, while T-BT-3's was set on a Mac. Its command passes each sibling the machine
check read as idle.

A book file, any other file not labelled as the Phase 2 stub-book harness proof, is held to more:
the design, input and top-of-book fields, a statement of how the run read the day beside
itch-book's two methods, the latency table with its pair-subtracted
column, the per-operation table, the book's memory, the summary's service and batch
means, the top of book when it was tracked, the design comparison, and a verdict for every
target in words from a fixed set. On macOS, a bar set on x86 Linux may not be called met
or missed.

A book file whose `passes` field says `throughput only` ran no latency pass, and the field
must say why. Its file is held to the opposite: no percentile row, no per-operation row, no
latency-pass figure in the summary, each latency section saying it was not measured, and
every latency target not measured in this run. A file with no `passes` field predates it
and ran every pass.

An SPSC file, one with a `suite` field (scripts/spsc_driver.py), has no counter table and
no single percentile column. It is held to its suite's fields, the element and capacity
every queue shared, a summary with each median inside its min and max, every latency cell
at or above the floor or written `below floor`, a burst replayed at the day's own pace, the
book agreement of a pipeline file, the macOS note that unpinned cross-core latency is weak
evidence, and a row for each of its suite's comparisons in SPSC_COMPARISONS: the ratio of
the summary's medians as shown, the bar, and the verdict in the fixed words, consistent with
both. Every SPSC bar was set on x86, so a macOS file may only say the host differs. A macOS
SPSC file also says the Mac's sampler rule was applied, in its interference field and its
Sampler section, with a quiet span for each published repetition; a debian one with one-way
latencies says the two cores' clocks were checked and agree. Its command passes each sibling
the machine check read as idle.

An allocator file, one with an `allocators` field (scripts/alloc_driver.py, T-ARENA-1), is
held to its own fields: the arms, the one capacity they all ran on, the pages asked for and
the fault check's verdict. Its summary has each median inside its min and max; every
latency cell, by arm and operation, is at or above the floor or written `below floor`; the
Page faults section has a row for every arm, a pool arm's with every count read and its
check `zero` only when every count is 0; the header's fault_check is PASS only when every
pool arm's is zero, and FAIL when one is not; every pool arm passed itch_alloc on no
allocation; every arm's book agreed with the fast book's, with no divergence; and the
T-ARENA-1 row says it has no bar, beside the fault check's verdict. A macOS allocator file
also says the sampler rule was applied, with a quiet span for every published timed process.
Its command passes each sibling the machine check read as idle.

A walks ladder file, from scripts/walk_driver.py, is held to scripts/walk_report.py's checks
of its own tables, and its command passes each sibling the machine check read as idle.

Each violation prints as `check_results: FAIL <code> <file>: <detail>`.

Exit codes: 0 every file passed, 1 at least one failed, 2 bad usage.
"""

from __future__ import annotations

import argparse
import math
import re
import statistics
import sys
from pathlib import Path

REQUIRED_FIELDS = (
    "host", "cpu", "kernel", "compiler", "flags", "commit", "dataset", "dataset_sha256",
    "timer", "quantum", "pair", "floor", "pinning", "migrations", "core", "governor",
    "frequency", "turbo", "smt", "isolation", "counters", "rdpmc", "page_size", "residency",
    "measure_load_start", "measure_load_end", "prediction", "run_id", "repetitions",
    "warmup_messages", "timed_messages", "run_started", "run_finished",
)
BOOK_FIELDS = ("design", "input", "top_of_book", "book_messages")
STUB_LABEL = "Stub-book harness proof, not a book result."
DESIGNS = ("hash-linked", "hash-flat", "paged-linked", "paged-flat", "map-fresh", "map-aged")
OPERATIONS = ("add", "delete", "replace", "reduce")
OPERATION_PERCENTILES = ("p50", "p99", "p99.9")
BOOK_SUMMARY = ("Mean service time per message, ns", "Batch mean per book message, ns",
                "Book memory, MiB")
PASSES = ("all: ", "throughput only: ")
LATENCY_SUMMARY = ("Latency-pass rate, messages/s", "Mean span of apply(), ns",
                   "Instrumentation ratio, latency pass over throughput pass")
LATENCY_TARGETS = ("T-LAT-1", "T-LAT-2", "T-LAT-3", "T-LAT-4", "T-LAT-5")
NOT_MEASURED = "Not measured: "
TARGETS = ("T-BOOK-1", "T-BOOK-2", "T-BOOK-3", "T-BOOK-4", "T-LAT-1", "T-LAT-2", "T-LAT-3",
           "T-LAT-4", "T-LAT-5", "T-DESIGN-1")
# Bars set on x86 hardware, which a macOS figure neither passes nor fails.
X86_TARGETS = ("T-BOOK-1", "T-BOOK-2", "T-LAT-1", "T-LAT-2", "T-LAT-4", "T-LAT-5")
# Each backtest workload's target, its bar in millions of messages a second, and whether
# the bar was set on x86.
BACKTEST_TARGETS = {"quoting": ("T-BT-1", 2.0, True), "shadow": ("T-BT-3", 4.61, False)}
BACKTEST_FIELDS = ("workload", "symbols", "input", "latency")
BACKTEST_SUMMARY = ("Replay rate, messages/s", "Engine events/s", "Mean time per message, ns")
VERDICTS = ("met", "missed: ", "host differs: ", "below floor: ", "not measured in this run: ")
BELOW_FLOOR = "below floor"
HOST_LABELS = ("debian", "macOS (M4 Max)", "unknown")
MAC_HOST = "macOS (M4 Max)"
# Fields macOS has no way to read, so any value other than unavailable is made up.
MAC_UNREADABLE = ("governor", "frequency", "turbo", "isolation", "rdpmc")
PENDING = "pending: stamp with scripts/stamp_load.py"
SPSC_SUITES = ("queues", "ablation", "pipeline")
SPSC_FIELDS = ("suite", "element", "capacity", "cache_line", "peer_core", "peer_pinning")
SPSC_PIPELINE_FIELDS = ("design", "input", "warmup_messages", "timed_messages")
SPSC_PER_PASS = ("warmup_messages", "timed_messages")
RTT_UNIT = "ns a round trip"
RATE_UNIT = "M items/s"
PIPELINE_RATIO = "Pipelined time over single-threaded time"
SPSC_PERCENTILES = ("p50", "p90", "p99", "p99.9", "p99.99", "max")
SPSC_MAC_NOTE = ("is not a pinned host.", "Unpinned cross-core latency on the Mac is weak evidence")
SPSC_SAMPLER_RULE = (
    "The Mac's sampler rule was applied: a CPU sampler ran beside every repetition from before "
    "it started to after it ended, and a repetition is published only if the sampler covered "
    "its whole span with no gap over 5 s and saw no other process above 20% CPU in it. A "
    "repetition that failed the rule was withdrawn, with its own row in results/run_log.md, "
    "and run again; none was withdrawn for its numbers.")
SPSC_INTERFERENCE = re.compile(r"sampled every [\d.]+ s by the driver over each repetition's "
                               r"whole span; no other process above 20% CPU in any published "
                               r"repetition; \d+ withdrawn under the rule")
ALLOC_ARMS = ("malloc-fresh", "malloc-aged", "mimalloc", "pmr-pool", "pmr-monotonic",
              "pool-index", "pool-pointer", "pool-fifo")
ALLOC_POOLS = ("pool-index", "pool-pointer", "pool-fifo")
ALLOC_FIELDS = ("allocators", "node_capacity", "pages_requested", "fault_check", "design",
                "input", "book_messages")
ALLOC_OPERATIONS = ("all", "add", "delete", "replace", "reduce")
FAULTS_ZERO = "zero"
FAULTS_FAULTED = "faulted"
FAULTS_HEAP = "not judged: a heap arm"
FAULTS_NOT_RUN = "not run: "
ARENA_VERDICT = "no bar to meet; fault check "
ALLOC_SAMPLER_RULE = (
    "The Mac's sampler rule was applied: a CPU sampler ran beside every timed process from "
    "before it started to after it ended, and a timed process is published only if the sampler "
    "covered its whole span with no gap over 5 s and saw no other process above 20% CPU in it. "
    "A timed process that failed the rule was withdrawn, with its own row in results/run_log.md, "
    "and run again; none was withdrawn for its numbers. The untimed processes, of memory alone, "
    "are not judged.")
ALLOC_INTERFERENCE = SPSC_INTERFERENCE
_T1_HOST = "Ryzen 7 5825U, Linux, pinned"
_T2_HOST = "i7-9700 under WSL2"
_T3_HOST = "Ryzen 9 5950X, Linux, pinned"
_COMPARISON_QUEUES = "the comparison queues were not built (-DITCH_COMPARISON_QUEUES=ON)"


def _rtt(queue):
    return f"{queue}, rtt, {RTT_UNIT}"


def _rate(variant):
    return f"{variant}, saturated, {RATE_UNIT}"


# Every comparison an SPSC file reports, keyed by target and the words in its row: the
# suite that runs it, the summary rows it divides (num over den) or the one it reads
# (value), the side of the bar that meets it, the bar, BENCHMARKS.md's host for it, and
# why a run may lack it. T-SPSC-2's pairs in Frasch's own order come first: cached against
# uncached with %, then the mask against % with cached indices (LC-7).
SPSC_COMPARISONS = {
    ("T-SPSC-1", "boost rtt / project rtt"): {
        "suite": "queues", "num": _rtt("boost"), "den": _rtt("project"), "op": ">=",
        "bar": 1.16, "host": _T1_HOST, "missing": _COMPARISON_QUEUES},
    ("T-SPSC-1", "project rtt / moodycamel rtt"): {
        "suite": "queues", "num": _rtt("project"), "den": _rtt("moodycamel"), "op": "<=",
        "bar": 1.00, "host": _T1_HOST, "missing": _COMPARISON_QUEUES},
    ("T-SPSC-1", "project rtt / rigtorp rtt"): {
        "suite": "queues", "num": _rtt("project"), "den": _rtt("rigtorp"), "op": "<=",
        "bar": 1.00, "host": _T1_HOST, "missing": _COMPARISON_QUEUES},
    ("T-SPSC-2", "cached over uncached, with %: modulo / uncached-modulo"): {
        "suite": "ablation", "num": _rate("modulo"), "den": _rate("uncached-modulo"),
        "op": ">=", "bar": 3.9, "host": _T2_HOST, "missing": "no such variant ran"},
    ("T-SPSC-2", "cached over uncached, with the mask: shipped / uncached"): {
        "suite": "ablation", "num": _rate("shipped"), "den": _rate("uncached"),
        "op": ">=", "bar": 3.9, "host": _T2_HOST, "missing": "no such variant ran"},
    ("T-SPSC-2", "mask over %, cached: shipped / modulo"): {
        "suite": "ablation", "num": _rate("shipped"), "den": _rate("modulo"),
        "op": ">=", "bar": 4.2, "host": _T2_HOST, "missing": "no such variant ran"},
    ("T-SPSC-2", "mask over %, uncached: uncached / uncached-modulo"): {
        "suite": "ablation", "num": _rate("uncached"), "den": _rate("uncached-modulo"),
        "op": ">=", "bar": 4.2, "host": _T2_HOST, "missing": "no such variant ran"},
    ("T-SPSC-3", "pipelined time / single-threaded time"): {
        "suite": "pipeline", "value": PIPELINE_RATIO, "op": "<=", "bar": 1.31,
        "host": _T3_HOST, "missing": "no pipelined pass ran"},
}


def spsc_interference(every):
    """The start of a macOS SPSC file's interference field, as spsc_driver.py writes it."""
    return (f"sampled every {every:g} s by the driver over each repetition's whole span; no "
            "other process above 20% CPU in any published repetition")


def comparison_value(spec, medians):
    """The comparison's ratio from the summary's medians by label, or None if one is absent."""
    if "value" in spec:
        return medians.get(spec["value"])
    num, den = medians.get(spec["num"]), medians.get(spec["den"])
    if num is None or den is None or den == 0:
        return None
    return num / den
# From this day every macOS run needs a quiet CPU sampler beside it (stamp_load.py).
SAMPLER_RULE_FROM = "2026-11-05T00:00:00Z"

HOME_PATH = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+")
ADDRESS = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
HEADER_LINE = re.compile(r"^- ([a-z0-9_]+): (.*)$")
TIMESTAMP = re.compile(r"\bat=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")
EXIT_CODE = re.compile(r"\bexit=(\d+)")
HOST_WORD = re.compile(r"\bhost=(\S.*?)(?: at=|$)")
PERCENTILE_ROW = re.compile(r"^\| (p[0-9.]+|max) \| (.*?) \|")
CELLS = re.compile(r"^\|(.*)\|$")
COUNTER_ROW = re.compile(r"^\| (.+?) \| (measured|multiplexed|unavailable) \| ([^|]*) \|")
RESIDENT = re.compile(r"^(\d+) of (\d+) pages resident$")
FIRST_NUMBER = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?")


def section(text, title):
    """The text under a `## title` heading, or None."""
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else None


def parse_header(text):
    fields = {}
    for line in (section(text, "Header") or "").splitlines():
        match = HEADER_LINE.match(line)
        if match:
            fields[match.group(1)] = match.group(2)
    return fields


def number(text):
    match = FIRST_NUMBER.search(text)
    return float(match.group(0).replace(",", "")) if match else None


# Figures computed from unrounded values. A report works each figure out from the raw values
# and rounds it only to print it, but its tables print those values rounded, so a check that
# reads the file alone cannot work such a figure out again exactly. It holds the figure to the
# range the printed values leave instead. Each helper takes and gives ranges, (low, high): a
# value printed to N places stands for anything within half a unit of its last place, and a
# raw value is the range (value, value), which gives the figure itself.

def printed_range(cell, places):
    """The values a cell printed to `places` decimals can stand for."""
    value = cell_value(cell)
    # A hair over half a unit, so that the binary value of a printed decimal is never left out.
    half = 0.5 * 10 ** -places * (1 + 1e-9)
    return value - half, value + half


def exact(value):
    return value, value


def cell_value(cell):
    return float(cell.replace(",", "").rstrip("%×"))


def median_range(ranges):
    return (statistics.median(low for low, _ in ranges),
            statistics.median(high for _, high in ranges))


def min_range(ranges):
    return min(low for low, _ in ranges), min(high for _, high in ranges)


def max_range(ranges):
    return max(low for low, _ in ranges), max(high for _, high in ranges)


def cv_range(ranges):
    """The coefficient of variation, stdev over mean. Moving each value by at most h_i moves
    the mean by at most the mean of the h_i, and the stdev by at most sqrt(sum h_i² / (n - 1)),
    since the stdev is a norm of the values less their mean, scaled."""
    if len(ranges) < 2:
        return 0.0, 0.0
    middles = [(low + high) / 2 for low, high in ranges]
    halves = [(high - low) / 2 for low, high in ranges]
    mean, stdev = statistics.fmean(middles), statistics.stdev(middles)
    mean_half = statistics.fmean(halves)
    stdev_half = math.sqrt(sum(half * half for half in halves) / (len(ranges) - 1))
    return max(0.0, stdev - stdev_half) / (mean + mean_half), (stdev + stdev_half) / (mean - mean_half)


def shown_within(shown, bounds, fmt):
    """What a file should print for a figure in `bounds`, printed by `fmt`: its own cell
    `shown`, if a value in bounds prints as that cell, and otherwise the middle of the bounds.
    Given a raw value's bounds and no cell, it is the figure printed."""
    low, high = bounds
    if shown is not None:
        try:
            value = cell_value(shown)
        except ValueError:
            value = None
        if value is not None and fmt(value) == shown and \
                cell_value(fmt(low)) <= value <= cell_value(fmt(high)):
            return shown
    return fmt((low + high) / 2)


def check_fields(fields, skipped=()):
    for name in REQUIRED_FIELDS:
        if name in skipped:
            continue
        if name not in fields:
            yield "header-field-missing", name
        elif not fields[name].strip():
            yield "header-field-empty", name
    for name, value in fields.items():
        if value.strip().startswith("unavailable") and \
                not re.match(r"unavailable: \S", value.strip()):
            yield "unavailable-without-reason", name


def check_host(fields):
    host = fields.get("host", "")
    kernel = fields.get("kernel", "")
    if host not in HOST_LABELS:
        yield "host-label", repr(host)
    if host == "debian" and not kernel.startswith("Linux"):
        yield "host-label", f"{host} on {kernel}"
    if host == MAC_HOST and not kernel.startswith("Darwin"):
        yield "host-label", f"{host} on {kernel}"
    if host == MAC_HOST:
        for name in MAC_UNREADABLE:
            if name in fields and not fields[name].startswith("unavailable: "):
                yield "unavailable-faked", f"{name}: {fields[name]}"


def check_pin(fields):
    host = fields.get("host", "")
    pinning = fields.get("pinning", "")
    if pinning.startswith("pinned"):
        if host == MAC_HOST:
            yield "pin-claimed", "macOS cannot pin"
        core = re.match(r"pinned to core (\d+): .*read back \[(\d+)\]$", pinning)
        if core is None or core.group(1) != core.group(2):
            yield "pin-claimed", f"no matching read-back: {pinning}"
    elif not pinning.startswith("not pinned: "):
        yield "pin-claimed", f"pinning says neither: {pinning!r}"
    elif host == "debian":
        yield "pin-failed", pinning


def check_residency(fields):
    match = RESIDENT.match(fields.get("residency", ""))
    if match is None or match.group(1) != match.group(2) or match.group(2) == "0":
        yield "input-not-resident", fields.get("residency", "no field")


def check_warmup(fields):
    warmup = fields.get("warmup_messages")
    timed = fields.get("timed_messages")
    if warmup is not None and timed is not None and number(warmup) != number(timed):
        yield "warmup-included", f"{timed} timed against {warmup} in one pass"


def throughput_only(fields):
    return fields.get("passes", "").startswith(PASSES[1])


def check_percentiles(text, fields):
    floor = number(fields.get("floor", ""))
    table = section(text, "Latency")
    if table is None:
        yield "latency-table-missing", "no Latency section"
        return
    if is_book_file(text) and throughput_only(fields):
        return
    rows = 0
    for line in table.splitlines():
        match = PERCENTILE_ROW.match(line)
        if match is None:
            continue
        rows += 1
        value = match.group(2).strip()
        if value in ("unresolved", BELOW_FLOOR):
            continue
        figure = number(value)
        if figure is None or floor is None or figure < floor:
            yield "percentile-below-floor", f"{match.group(1)} is {value}, floor {floor} ns"
    if rows == 0:
        yield "latency-table-missing", "no percentile rows"


def check_counters(text):
    table = section(text, "Counters")
    if table is None:
        yield "counter-table-missing", "no Counters section"
        return
    for line in table.splitlines():
        match = COUNTER_ROW.match(line)
        if match is None:
            continue
        name, state, ratio = match.group(1), match.group(2), match.group(3).strip()
        if state == "measured" and ratio != "1.0000":
            yield "multiplexed-as-measured", f"{name} ran for {ratio} of the pass"
        if state == "multiplexed" and number(ratio) is None:
            yield "multiplexed-without-ratio", name


def check_statistics(text):
    table = section(text, "Summary")
    if table is None:
        yield "summary-missing", "no Summary section"
    elif "| Median | Min | Max | CV |" not in table:
        yield "summary-missing", "median, min, max and CV are not all there"


def check_load(fields):
    start = fields.get("measure_load_start", "")
    end = fields.get("measure_load_end", "")
    if start == PENDING or end == PENDING:
        yield "load-not-stamped", "the measure.sh lines were never stamped in"
        return
    for name, line, prefix in (("start", start, "measure.sh: start"),
                               ("end", end, "measure.sh: end")):
        if not line.startswith(prefix) or "load" not in line:
            yield "load-line-invalid", f"{name}: {line!r}"
    host = HOST_WORD.search(start)
    if host is not None and host.group(1) != fields.get("host"):
        yield "load-line-hostname", f"host={host.group(1)}"
    code = EXIT_CODE.search(end)
    if code is not None and code.group(1) != "0":
        yield "load-line-invalid", f"exit={code.group(1)}"
    opened = TIMESTAMP.search(start)
    closed = TIMESTAMP.search(end)
    started = fields.get("run_started", "")
    finished = fields.get("run_finished", "")
    if opened and closed and (opened.group(1) > started or closed.group(1) < finished):
        yield "load-line-invalid", (f"lines {opened.group(1)} to {closed.group(1)} do not "
                                    f"bracket the run {started} to {finished}")


def needs_sampler(fields):
    """A macOS run from the rule's day on, but for an SPSC file: spsc_driver.py samples each
    repetition itself, and check_spsc_sampler holds the file to its own Sampler section."""
    return fields.get("host") == MAC_HOST and fields.get("run_started", "") >= SAMPLER_RULE_FROM \
        and not is_spsc_file(fields)


def check_sampler(fields):
    """A macOS run from the rule's day on was stamped with a sampler that saw it quiet."""
    if not needs_sampler(fields) or fields.get("measure_load_start") == PENDING:
        return
    value = fields.get("sampler")
    if value is None:
        yield "sampler-missing", "a macOS run from 2026-11-05 on has no sampler line"
    elif not value.startswith("quiet: the sampler rule was applied: "):
        yield "sampler-not-quiet", value


def check_machine(text):
    output = section(text, "machine_check output")
    if output is None:
        yield "machine-check-missing", "no machine_check section"
        return
    if "machine_check: passed" not in output:
        yield "machine-check-failed", "the embedded output does not end in a pass"
    failures = re.findall(r"^machine_check: FAIL .*$", output, re.MULTILINE)
    for line in failures:
        yield "machine-check-failed", line


def check_private(text, fields):
    for name, pattern in (("home path", HOME_PATH), ("address", ADDRESS)):
        match = pattern.search(text)
        if match:
            yield "private-text", f"{name}: {match.group(0)}"


def is_book_file(text):
    return STUB_LABEL not in text


def table_rows(text, title):
    """The cells of every row of the tables under a heading, the head and rule left out."""
    rows = []
    for line in (section(text, title) or "").splitlines():
        match = CELLS.match(line.strip())
        if match is None:
            continue
        cells = [cell.strip() for cell in match.group(1).split("|")]
        if all(re.fullmatch(r"-*", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows


def check_value(name, value, floor):
    """A latency cell: a figure at or above the floor, or below floor."""
    if value == BELOW_FLOOR:
        return
    figure = number(value) if re.fullmatch(r"[0-9][0-9,]*(?:\.[0-9]+)?", value) else None
    if figure is None:
        yield "latency-value-malformed", f"{name}: {value!r}"
    elif floor is None or figure < floor:
        yield "percentile-below-floor", f"{name} is {value}, floor {floor} ns"


def check_book_fields(fields):
    for name in BOOK_FIELDS:
        if name not in fields:
            yield "header-field-missing", name
    design = fields.get("design", "")
    if design and not any(design.startswith(f"{name}: ") for name in DESIGNS):
        yield "design-unknown", design
    source = fields.get("input", "")
    if source and not source.startswith(("ram: ", "mapped: ")):
        yield "input-mode-unknown", source
    top = fields.get("top_of_book", "")
    if top and top != "not tracked" and not top.startswith("tracked: "):
        yield "top-of-book-malformed", top
    messages = fields.get("book_messages", "")
    if messages and not re.fullmatch(r"[0-9][0-9,]*", messages):
        yield "book-messages-malformed", messages
    passes = fields.get("passes")
    if passes is not None and not passes.startswith(PASSES):
        yield "passes-malformed", passes
    elif passes is not None and throughput_only(fields) and \
            not re.search(r"\bWhy: \S", passes):
        yield "passes-malformed", f"throughput only with no reason: {passes}"


def check_no_latency(text):
    """A file that ran no latency pass shows no latency figure, and says so."""
    for title in ("Latency", "Per-operation latency"):
        body = section(text, title)
        if body is None or NOT_MEASURED not in body:
            yield "latency-not-measured", f"the {title} section does not say it was not measured"
        if any(len(cells) > 1 for cells in table_rows(text, title)):
            yield "latency-not-measured", f"the {title} section has a table"
    for cells in table_rows(text, "Summary"):
        if cells[0] in LATENCY_SUMMARY:
            yield "latency-not-measured", f"the summary gives {cells[0]}"
    for cells in table_rows(text, "Repetitions"):
        if len(cells) > 2 and number(cells[2]) is not None:
            yield "latency-not-measured", f"repetition {cells[0]} gives a ratio of {cells[2]}"
    for cells in table_rows(text, "Targets"):
        if cells[0] in LATENCY_TARGETS and \
                not cells[-1].startswith("not measured in this run: "):
            yield "latency-not-measured", f"{cells[0]}: {cells[-1]!r}"


def check_book_latency(text, fields):
    """Every all-message row carries a raw and a pair-subtracted figure, both above the
    floor or both below it."""
    floor = number(fields.get("floor", ""))
    rows = [cells for cells in table_rows(text, "Latency") if PERCENTILE_ROW.match(
        "| " + " | ".join(cells) + " |")]
    for cells in rows:
        if len(cells) != 7:
            yield "latency-subtracted-missing", f"{cells[0]} has {len(cells)} cells, not 7"
            continue
        if (cells[1] == BELOW_FLOOR) != (cells[6] == BELOW_FLOOR):
            yield "latency-subtracted-missing", f"{cells[0]}: {cells[1]} raw, {cells[6]} less pair"
        yield from check_value(f"{cells[0]} less the pair", cells[6], floor)


def check_operations(text, fields):
    floor = number(fields.get("floor", ""))
    rows = table_rows(text, "Per-operation latency")
    seen = set()
    for cells in rows:
        if len(cells) < 8 or cells[0] not in OPERATIONS:
            continue
        if cells[1] in OPERATION_PERCENTILES:
            seen.add((cells[0], cells[1]))
            yield from check_value(f"{cells[0]} {cells[1]}", cells[2], floor)
            yield from check_value(f"{cells[0]} {cells[1]} less the pair", cells[7], floor)
    for operation in OPERATIONS:
        for percentile in OPERATION_PERCENTILES:
            if (operation, percentile) not in seen:
                yield "operation-table-missing", f"no {operation} {percentile} row"


def check_book_summary(text):
    rows = {cells[0]: cells for cells in table_rows(text, "Summary")}
    for label in BOOK_SUMMARY:
        if label not in rows:
            yield "summary-figure-missing", label
        elif number(rows[label][1]) is None:
            yield "summary-figure-missing", f"{label}: {rows[label][1]!r}"


def check_memory(text):
    rows = {cells[0]: cells for cells in table_rows(text, "Book memory")}
    book = rows.get("Book memory, MiB")
    if book is None or number(book[1]) is None:
        yield "memory-missing", "no book memory figure"


def check_method(text):
    body = section(text, "How this run reads the day") or ""
    if "itch-book's" not in body:
        yield "method-missing", "no statement of how the run reads the day against itch-book's"


def check_top(text, fields):
    if fields.get("top_of_book", "").startswith("tracked: "):
        body = section(text, "Top of book") or ""
        if not re.search(r"moved after [0-9][0-9,]* of the [0-9][0-9,]* messages", body):
            yield "top-of-book-missing", "tracked, with no count of the moves"


def check_comparison(text, fields):
    body = section(text, "Design comparison")
    if body is None:
        yield "design-comparison-missing", "no Design comparison section"
        return
    design = fields.get("design", "").split(":")[0]
    ratios = [cells for cells in table_rows(text, "Design comparison") if cells[0] in DESIGNS]
    if design.startswith("map-") or "No map design ran beside it" in body:
        return
    if not ratios:
        yield "design-comparison-missing", "a fast design with no ratio and no reason"
    for cells in ratios:
        if number(cells[1]) is None:
            yield "design-comparison-missing", f"{cells[0]}: {cells[1]!r}"


def check_targets(text, fields):
    rows = [cells for cells in table_rows(text, "Targets") if cells[0] in TARGETS]
    for target in TARGETS:
        if not any(cells[0] == target for cells in rows):
            yield "target-missing", target
    for cells in rows:
        if len(cells) != 5:
            yield "target-verdict-invalid", f"{cells[0]} has {len(cells)} cells, not 5"
            continue
        verdict = cells[4]
        if not (verdict == "met" or verdict.startswith(VERDICTS[1:])):
            yield "target-verdict-invalid", f"{cells[0]}: {verdict!r}"
        elif (fields.get("host") == MAC_HOST and cells[0] in X86_TARGETS
              and verdict.startswith(("met", "missed: ", "below floor: "))):
            yield "target-host-blurred", f"{cells[0]} is {verdict.split(':')[0]} on macOS"


def is_backtest_file(fields):
    return "workload" in fields


def is_walks_file(fields):
    """scripts/walk_driver.py's ladder of shadow-order schedules."""
    return "ladder" in fields


def check_walks(text):
    # Imported here: walk_report imports this module.
    import walk_report  # noqa: PLC0415

    return walk_report.check_text(text)


def check_backtest_summary(text, fields):
    rows = {cells[0]: cells for cells in table_rows(text, "Summary")}
    for label in BACKTEST_SUMMARY:
        cells = rows.get(label)
        figures = [number(cell) for cell in cells[1:4]] if cells and len(cells) == 5 else None
        if figures is None or None in figures:
            yield "summary-figure-missing", label
        elif not figures[1] <= figures[0] <= figures[2]:
            yield "summary-inconsistent", f"{label}: median {cells[1]} outside {cells[2]} to {cells[3]}"
    repetitions = [cells for cells in table_rows(text, "Repetitions")
                   if cells and re.fullmatch(r"[0-9]+", cells[0])]
    if len(repetitions) != number(fields.get("repetitions", "0") or "0"):
        yield "summary-inconsistent", (f"{len(repetitions)} repetition rows, the header says "
                                       f"{fields.get('repetitions')}")
    rates = [number(cells[1]) for cells in repetitions if len(cells) > 1]
    rate = rows.get("Replay rate, messages/s")
    if rates and None not in rates and rate and number(rate[1]) is not None:
        median = sorted(rates)[len(rates) // 2] if len(rates) % 2 else \
            (sorted(rates)[len(rates) // 2 - 1] + sorted(rates)[len(rates) // 2]) / 2
        if abs(median - number(rate[1])) > 1.0:
            yield "summary-inconsistent", (f"the summary's median rate {rate[1]} is not the "
                                           f"repetitions' median, {median:,.0f}")


def check_backtest_target(text, fields):
    workload = fields.get("workload", "")
    if workload not in BACKTEST_TARGETS:
        yield "workload-unknown", workload
        return
    target, bar, x86 = BACKTEST_TARGETS[workload]
    rows = [cells for cells in table_rows(text, "Targets") if cells[0] == target]
    if not rows:
        yield "target-missing", target
        return
    cells = rows[0]
    if len(cells) != 4:
        yield "target-verdict-invalid", f"{target} has {len(cells)} cells, not 4"
        return
    verdict = cells[3]
    measured = number(cells[2])
    rate = {row[0]: row for row in table_rows(text, "Summary")}.get("Replay rate, messages/s")
    if not (verdict == "met" or verdict.startswith(VERDICTS[1:])):
        yield "target-verdict-invalid", f"{target}: {verdict!r}"
    elif fields.get("host") == MAC_HOST and x86 and verdict.startswith(("met", "missed: ")):
        yield "target-host-blurred", f"{target} is {verdict.split(':')[0]} on macOS"
    elif measured is None or rate is None or number(rate[1]) is None or \
            abs(measured - number(rate[1]) / 1e6) > 0.006:
        yield "target-verdict-wrong", f"{target} measured {cells[2]!r} is not the summary's median"
    elif (verdict == "met") != (number(rate[1]) / 1e6 >= bar) and not verdict.startswith(
            VERDICTS[2:]):
        yield "target-verdict-wrong", (f"{target} is {verdict.split(':')[0]} at {rate[1]} "
                                       f"messages/s against {bar} M")


def check_backtest(text, fields):
    for name in BACKTEST_FIELDS:
        if name not in fields:
            yield "header-field-missing", name
    yield from check_backtest_summary(text, fields)
    event = section(text, "What an event is") or ""
    if "One event here is" not in event or "denominator" not in event:
        yield "event-definition-missing", "no statement of what one event is and its denominator"
    counts = {cells[0] for cells in table_rows(text, "Counts")}
    if "Queue anomalies" not in counts:
        yield "counts-missing", "no queue anomaly count"
    if fields.get("host") == MAC_HOST and "is not a pinned host." not in text:
        yield "host-note-missing", "a macOS file without the note that macOS cannot pin"
    yield from check_backtest_target(text, fields)
    yield from check_command_siblings(text)


def is_spsc_file(fields):
    return "suite" in fields


def spsc_medians(text):
    """The summary's medians as shown, by label, for the rows that parse."""
    medians = {}
    for cells in table_rows(text, "Summary"):
        if len(cells) == 5 and cells[0] != "Figure" and number(cells[1]) is not None:
            medians[cells[0]] = number(cells[1])
    return medians


def check_spsc_summary(text):
    rows = [cells for cells in table_rows(text, "Summary") if cells[0] != "Figure"]
    if not rows:
        yield "summary-figure-missing", "no figure in the Summary"
    for cells in rows:
        figures = [number(cell) for cell in cells[1:4]] if len(cells) == 5 else None
        if figures is None or None in figures:
            yield "summary-figure-missing", cells[0]
        elif not figures[1] <= figures[0] <= figures[2]:
            yield "summary-inconsistent", (f"{cells[0]}: median {cells[1]} outside {cells[2]} "
                                           f"to {cells[3]}")


def check_spsc_latency(text, fields):
    floor = number(fields.get("floor", ""))
    rows = [cells for cells in table_rows(text, "Latency") if cells[0] != "Queue"]
    if not rows:
        yield "latency-table-missing", "no latency row"
    for cells in rows:
        if len(cells) != len(SPSC_PERCENTILES) + 4:
            yield "latency-value-malformed", f"{cells[0]} has {len(cells)} cells"
            continue
        for name, value in zip(SPSC_PERCENTILES, cells[2:2 + len(SPSC_PERCENTILES)]):
            yield from check_value(f"{cells[0]} {cells[1]} {name}", value, floor)


def check_spsc_targets(text, fields, suite):
    host = fields.get("host")
    medians = spsc_medians(text)
    rows = {(cells[0], cells[1]): cells for cells in table_rows(text, "Targets")
            if len(cells) >= 2}
    for key, spec in SPSC_COMPARISONS.items():
        if spec["suite"] != suite:
            continue
        cells = rows.get(key)
        if cells is None:
            yield "target-missing", f"{key[0]}: {key[1]}"
            continue
        if len(cells) != 6:
            yield "target-verdict-invalid", f"{key[0]} has {len(cells)} cells, not 6"
            continue
        verdict = cells[5]
        value = comparison_value(spec, medians)
        holds = None if value is None else (
            value >= spec["bar"] if spec["op"] == ">=" else value <= spec["bar"])
        if not (verdict == "met" or verdict.startswith(VERDICTS[1:])):
            yield "target-verdict-invalid", f"{key[0]} {key[1]}: {verdict!r}"
        elif value is None:
            if not verdict.startswith(VERDICTS[4]):
                yield "target-verdict-wrong", f"{key[0]} {key[1]}: no figure, yet {verdict!r}"
        elif number(cells[3]) is None or abs(number(cells[3]) - value) > 0.0051:
            yield "target-verdict-wrong", (f"{key[0]} {key[1]}: {cells[3]!r} is not "
                                           f"{value:.2f}, the summary's ratio")
        elif host == MAC_HOST and not verdict.startswith(VERDICTS[2]):
            yield "target-host-blurred", f"{key[0]} {key[1]} is {verdict.split(':')[0]} on macOS"
        elif host == MAC_HOST and ("which it would meet" in verdict) != holds:
            yield "target-verdict-wrong", (f"{key[0]} {key[1]}: {value:.2f} against "
                                           f"{spec['op']} {spec['bar']}, yet {verdict!r}")
        elif host != MAC_HOST and (verdict != "met" if holds else
                                   not verdict.startswith(VERDICTS[1])):
            yield "target-verdict-wrong", (f"{key[0]} {key[1]} is {verdict.split(':')[0]} at "
                                           f"{value:.2f} against {spec['op']} {spec['bar']}")


def check_spsc_sampler(text, fields):
    """The Mac's sampler rule: said, and a quiet span for every published repetition."""
    if not SPSC_INTERFERENCE.fullmatch(fields.get("interference", "")) or \
            SPSC_SAMPLER_RULE not in text:
        yield "sampler-rule-unsaid", repr(fields.get("interference"))
        return
    published = [cells for cells in table_rows(text, "Sampler")
                 if len(cells) == 4 and cells[0].startswith("repetition ")]
    quiet = [cells for cells in published if cells[3].startswith("published: ")]
    if len(quiet) != len(published) or str(len(quiet)) != fields.get("repetitions"):
        yield "sampler-not-quiet", (f"{len(quiet)} quiet spans of {len(published)} for "
                                    f"{fields.get('repetitions')} repetitions")


def check_command_siblings(text):
    """Each sibling the machine check found idle was asked for, so the command shown carries
    it: the check reads a sibling only when the driver is given one."""
    command = section(text, "Commands") or ""
    for sibling in re.findall(r"^machine_check: \w+ sibling-idle: cpu(\d+) ", text, re.MULTILINE):
        if not re.search(rf" --(?:peer-)?sibling {sibling}(?:\s|$)", command):
            yield "command-differs", (f"the machine check read cpu{sibling} as a sibling, and the "
                                      f"command shown passes no --sibling {sibling}")


def check_spsc(text, fields):
    suite = fields.get("suite", "")
    if suite not in SPSC_SUITES:
        yield "suite-unknown", suite
        return
    for name in SPSC_FIELDS + (SPSC_PIPELINE_FIELDS if suite == "pipeline" else ("burst",)):
        if name not in fields:
            yield "header-field-missing", name
    if suite != "pipeline" and not re.search(r", speedup 1$", fields.get("burst", "")):
        yield "burst-hurried", f"the burst was not replayed at the day's pace: {fields.get('burst')!r}"
    if fields.get("host") == MAC_HOST and not all(note in text for note in SPSC_MAC_NOTE):
        yield "host-note-missing", ("a macOS file without the note that it is unpinned and "
                                    "that cross-core latency there is weak evidence")
    if fields.get("host") == MAC_HOST:
        yield from check_spsc_sampler(text, fields)
    if fields.get("host") == "debian" and suite != "pipeline" and \
            not fields.get("cross_core_timer", "").startswith("agree: "):
        yield "cross-core-timer-unchecked", ("a one-way latency on two cores with no check "
                                             "that their clocks agree")
    yield from check_spsc_summary(text)
    if suite == "pipeline":
        agreement = section(text, "Book agreement") or ""
        if "left the book" not in agreement:
            yield "book-agreement-missing", "no statement that every pass left the same book"
        # The day's book is empty at the close, so the closing hash alone agrees on nothing.
        held = re.search(r"At the checkpoint, .*? every pass held the book `[0-9a-f]{16}`, "
                         r"([\d,]+) orders", agreement)
        if not held:
            yield "checkpoint-missing", "no statement that every pass held the same book mid-run"
        elif int(held.group(1).replace(",", "")) == 0:
            yield "checkpoint-empty", "the book was empty at the checkpoint"
    else:
        yield from check_spsc_latency(text, fields)
    yield from check_spsc_targets(text, fields, suite)
    yield from check_command_siblings(text)


def is_alloc_file(fields):
    return "allocators" in fields


def check_alloc_sampler(text, fields, arms):
    if not ALLOC_INTERFERENCE.fullmatch(fields.get("interference", "")) or \
            ALLOC_SAMPLER_RULE not in text:
        yield "sampler-rule-unsaid", repr(fields.get("interference"))
        return
    published = [cells for cells in table_rows(text, "Sampler")
                 if len(cells) == 4 and not cells[0].startswith(("Process", "withdrawn"))]
    quiet = [cells for cells in published if cells[3].startswith("published: ")]
    wanted = (number(fields.get("repetitions", "")) or 0) * len(arms)
    if len(quiet) != len(published) or len(quiet) != wanted:
        yield "sampler-not-quiet", (f"{len(quiet)} quiet spans of {len(published)} for "
                                    f"{wanted:g} timed processes")


def check_alloc_faults(text, fields, arms):
    rows = {cells[0]: cells for cells in table_rows(text, "Page faults")
            if len(cells) == 4 and cells[0] != "Arm"}
    pools_zero = []
    for arm in arms:
        cells = rows.get(arm)
        if cells is None:
            yield "fault-row-missing", arm
            continue
        if arm not in ALLOC_POOLS:
            if cells[3] != FAULTS_HEAP:
                yield "fault-check-wrong", f"{arm} is a heap arm, judged {cells[3]!r}"
            continue
        counts = []
        for cell in cells[1:3]:
            parts = [part.strip() for part in cell.split("/")]
            if not all(re.fullmatch(r"[0-9][0-9,]*", part) for part in parts):
                yield "faults-unread", f"{arm}: {cell!r}"
                counts = None
                break
            counts += [int(part.replace(",", "")) for part in parts]
        if counts is None:
            continue
        zero = all(count == 0 for count in counts)
        pools_zero.append(zero)
        if cells[3] != (FAULTS_ZERO if zero else FAULTS_FAULTED):
            yield "fault-check-wrong", f"{arm}: counts {counts} judged {cells[3]!r}"
    verdict = fields.get("fault_check", "")
    if not pools_zero:
        if not verdict.startswith(FAULTS_NOT_RUN):
            yield "fault-check-wrong", f"no pool arm ran, yet {verdict!r}"
    elif all(pools_zero) != verdict.startswith("PASS: ") or \
            not verdict.startswith(("PASS: ", "FAIL: ")):
        yield "fault-check-wrong", f"the pool arms' rows against {verdict!r}"


def check_alloc(text, fields):
    for name in ALLOC_FIELDS:
        if name not in fields:
            yield "header-field-missing", name
    arms = [arm.strip() for arm in fields.get("allocators", "").split(",") if arm.strip()]
    unknown = [arm for arm in arms if arm not in ALLOC_ARMS]
    if not arms or unknown:
        yield "allocator-unknown", repr(fields.get("allocators"))
        return
    if fields.get("host") == MAC_HOST:
        if "is not a pinned host." not in text:
            yield "host-note-missing", "a macOS file without the note that macOS cannot pin"
        yield from check_alloc_sampler(text, fields, arms)
    yield from check_spsc_summary(text)
    floor = number(fields.get("floor", ""))
    latency = [cells for cells in table_rows(text, "Latency") if cells[0] != "Arm"]
    for arm in arms:
        for operation in ALLOC_OPERATIONS:
            if not any(cells[:2] == [arm, operation] for cells in latency):
                yield "latency-row-missing", f"{arm} {operation}"
    for cells in latency:
        if len(cells) != 6:
            yield "latency-value-malformed", f"{' '.join(cells[:2])} has {len(cells)} cells"
            continue
        for percentile, value in zip(("p50", "p99", "p99.9"), cells[2:5]):
            yield from check_value(f"{cells[0]} {cells[1]} {percentile}", value, floor)
    yield from check_alloc_faults(text, fields, arms)
    yield from check_command_siblings(text)
    allocations = {cells[0]: cells for cells in table_rows(text, "Allocations")
                   if len(cells) == 7 and cells[0] != "Arm"}
    agreement = {cells[0]: cells for cells in table_rows(text, "Book agreement")
                 if len(cells) == 7 and cells[0] != "Arm"}
    for arm in arms:
        cells = allocations.get(arm)
        if cells is None:
            yield "allocation-row-missing", arm
        elif arm in ALLOC_POOLS and (cells[2] != "0" or cells[6] != "PASS"):
            yield "pool-arm-allocated", f"{arm}: {cells[2]} calls, {cells[6]}"
        elif arm not in ALLOC_POOLS and cells[6] != "REPORT":
            yield "allocation-verdict-wrong", f"{arm}: {cells[6]}"
        cells = agreement.get(arm)
        if cells is None:
            yield "book-agreement-missing", arm
        elif cells[1] != "PASS" or cells[5] != "0":
            yield "book-disagrees", f"{arm}: {cells[1]}, {cells[5]} divergences"
    hashes = {cells[6] for cells in agreement.values()}
    if len(hashes) > 1:
        yield "book-disagrees", f"the arms ended on other books: {sorted(hashes)}"
    targets = [cells for cells in table_rows(text, "Targets") if cells[0] == "T-ARENA-1"]
    if not targets or len(targets[0]) != 4:
        yield "target-missing", "T-ARENA-1"
    elif targets[0][3] != ARENA_VERDICT + fields.get("fault_check", ""):
        yield "target-verdict-invalid", f"T-ARENA-1: {targets[0][3]!r}"


def check_book(text, fields):
    latency = ((check_no_latency(text),) if throughput_only(fields) else
               (check_book_latency(text, fields), check_operations(text, fields)))
    for found in (check_book_fields(fields), *latency, check_book_summary(text),
                  check_memory(text), check_method(text), check_top(text, fields),
                  check_comparison(text, fields), check_targets(text, fields)):
        yield from found


def check_text(text):
    """Every violation in one results file, as (code, detail) pairs."""
    fields = parse_header(text)
    if not fields:
        return [("header-missing", "no Header section")]
    violations = []
    skipped = SPSC_PER_PASS if is_spsc_file(fields) and fields["suite"] != "pipeline" else ()
    for found in (check_fields(fields, skipped), check_host(fields), check_pin(fields),
                  check_residency(fields), check_warmup(fields), check_statistics(text),
                  check_load(fields), check_sampler(fields), check_machine(text),
                  check_private(text, fields)):
        violations.extend(found)
    if is_walks_file(fields):
        violations.extend(check_walks(text))
        violations.extend(check_command_siblings(text))
        return violations
    if is_backtest_file(fields):
        violations.extend(check_backtest(text, fields))
        return violations
    if is_spsc_file(fields):
        violations.extend(check_spsc(text, fields))
        return violations
    if is_alloc_file(fields):
        violations.extend(check_alloc(text, fields))
        return violations
    violations.extend(check_percentiles(text, fields))
    violations.extend(check_counters(text))
    if is_book_file(text):
        violations.extend(check_book(text, fields))
    return violations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    failed = 0
    for path in args.files:
        violations = check_text(path.read_text(encoding="utf-8"))
        for code, detail in violations:
            print(f"check_results: FAIL {code} {path.name}: {detail}")
        if violations:
            failed += 1
        else:
            print(f"check_results: ok {path.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
