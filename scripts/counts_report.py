#!/usr/bin/env python3
"""Writes the hardware counts by message type from bench_counts records (T-PERF-1, T-PERF-2).

    counts_report.py --record RUN.json... --load STDERR... --machine-check BEFORE AFTER \\
        --prediction P-NNNN --out results/counters_by_type_DESIGN_debian_DAY.md \\
        [--run-log results/run_log.md]
    counts_report.py --record AGE.json... --load STDERR... --machine-check BEFORE AFTER \\
        --machine-check-load BEFORE.err AFTER.err \\
        [--plain-record RUN.json... --plain-load STDERR...] --prediction P-NNNN \\
        --out results/counters_by_age_DESIGN_debian_DAY.md [--run-log results/run_log.md]
    counts_report.py --check FILE [--record ... --load ... --machine-check ... --prediction ...]

Records from `bench_counts --by type-age` make the second kind of file: the same counts
charged to message type and to the age of the order each message names, with the same
design counted by type alone (--plain-record) beside them to show what the age walk costs.
It carries no verdict: T-PERF-1 and T-PERF-2 are judged on the files by type. Each of its
machine checks is placed by the measure.sh line around it (--machine-check-load): the file
says when each began against the run, and says so when the check after was not taken for it.

P-0019 and P-0025 planned three processes a count. A file from fewer gives its count beside
its figures and its summary, and one from a single process gives no min, max or median.

The header is the first record's, and every record, by type alone too, must give the same
host, CPU, kernel, compiler, flags, commit and dataset, or the records are refused.

Each record is one fresh process. Its spans tile the pass, so by type they add up to the
whole pass by rdpmc exactly. That is checked here in every record, again on the totals the
file publishes, and a record where it does not hold is refused. The cost of counting, from
the empty spans each process measured, is shown beside every per-message figure and taken
off only in a column that says so.

On Zen 1, `cache-misses` counts L2 misses and `r0843:u` is a floor on DRAM demand fills
(results/chase_debian.md), and every line of the file that names either says so.

The file publishes every repetition's integers: each type's (or slot's) spans and counts,
the whole pass, and the empty spans before and after it, and those of the records by type
alone beside a type-age file. --check reads them back and works every figure out again from
them, the verdicts, medians, minima, maxima, empty-span costs, shares and the summary, and
compares the file line for line, so that no figure can say more than the counts; and it
judges the rest: the totals tile, the verdicts follow from the targets, the names are said,
nothing private is in it. Given the raw records too, it rebuilds the file and compares.

Each violation prints as `counts_report: FAIL <code>: <detail>`.

Exit codes: 0 written, or --check found nothing, 1 --check found something, 2 bad usage or
unusable input, a refused record among them.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402
import stamp_load  # noqa: E402

FILLS = "r0843:u (a floor on DRAM demand fills)"
L2 = "cache-misses (L2 misses on Zen 1)"
BRANCH = "branch-misses"
INSTRUCTIONS = "instructions"
CYCLES = "cycles"
EVENTS = (FILLS, BRANCH, INSTRUCTIONS, L2, CYCLES)
BOOK_TYPES = ("A", "F", "E", "C", "X", "D", "U", "P", "Q")
NAMING_TYPES = ("E", "C", "X", "D", "U")
WHOLE = "whole pass, rdpmc"
EMPTY_ROW = "empty spans {when} the pass"
TOTALS_TEXT = """\
Repetition {number}'s integers, which every figure in the tables above comes from: each
{each}'s spans and counts, the whole pass by `rdpmc`, and the empty spans before and after the
pass. Each column's {each}s add up to its whole pass, and `--check` works every figure above
out again from these tables."""
AGES = ("under 1 ms", "1 to 10 ms", "10 to 100 ms", "100 ms to 1 s", "1 s and over", "not live")
TYPE_NAMES = {
    "A": "Add Order", "F": "Add Order with MPID", "E": "Order Executed",
    "C": "Order Executed With Price", "X": "Order Cancel", "D": "Order Delete",
    "U": "Order Replace", "P": "Trade, non-cross", "Q": "Cross Trade", "B": "Broken Trade",
    "S": "System Event", "R": "Stock Directory", "H": "Stock Trading Action",
    "Y": "Reg SHO Restriction", "L": "Market Participant Position", "V": "MWCB Decline Level",
    "W": "MWCB Status", "K": "IPO Quoting Period", "J": "LULD Auction Collar",
    "h": "Operational Halt", "I": "Net Order Imbalance", "N": "Retail Price Improvement",
    "O": "DLCR Price Discovery", "none": "frames walked past, not decoded"}
T_PERF_1 = 0.623
T_PERF_2 = 2.04
# Fresh processes each count that P-0019 and P-0025 planned. A file from fewer gives its count
# beside its figures, and one from a single process gives no min, max or median of it.
PLANNED = 3
ONE = "n/a, one process"
# A machine check that began more than this many seconds before the first repetition or after
# the last one ended is not a check of the host as the run began or ended, and the file says so.
CHECK_GAP = 300
# What every record's header must give alike, as the other reports hold their records to.
SAME_FIELDS = ("host", "cpu", "kernel", "compiler", "flags", "commit", "dataset",
               "dataset_sha256")
SOURCE = {"A": 1.16, "D": 0.58, "E": 0.79, "U": 2.30, "book": 0.983}
VERDICT = re.compile(r"^- (T-PERF-[12]), .*?: \*\*(PASS|FAIL)\*\*\. Measured ([0-9.]+) raw",
                     re.MULTILINE)
COMMIT_FIELD = re.compile(r"^[0-9a-f]{12} ")
NAME_RULES = ((re.compile(r"cache-misses"), "L2 miss"),
              (re.compile(r"r0843"), "floor on DRAM demand fills"))

METHOD = """\
One process per repetition maps the day, reads every page, finds every page resident,
builds the book, and then, with all five events open in one group on core 4, runs:

1. {empty:,} spans with nothing in them;
2. the pass: one `rdpmc` of every event after each frame, the difference from the last
   reading added to that frame's message type, between a `read()` and an `rdpmc` of
   every event on each side;
3. {empty:,} empty spans again.

A message's span runs from the reading after the frame before it to the reading after its
own. It holds the frame walk, `apply()`, the five fenced reads and the addition to the
tally, and nothing falls between two spans, so the spans tile the pass: by type they add up
to the last reading less the first, exactly. An empty span holds one read and one addition
and nothing else, which is what counting adds to every message. The figures are exact per
message, with nothing sampled, and the pass is not timed: the reads are fenced, so time
here would mean nothing.

This is not the pass in the `bench_book_*` files, which reads before and after `apply()`
alone and leaves the frame walk out. Here the frame walk is in, so these figures are for
parse plus apply."""

CAVEATS = """\
- `r0843:u` is a floor on DRAM demand fills: demand fills from local DRAM only.
  A line the prefetcher brought in is not counted, so every fill figure here is a floor on
  last-level misses, not their count.
- `cache-misses` counts L2 misses, data and instruction, not last-level misses.
- The empty span's cost is shown, and taken off only in the columns that say so. It is the
  cost in a span with nothing else in it; inside a message's span the reads overlap other
  work differently, so the column with it taken off is an estimate, and the raw column is
  the measurement.
- One process per repetition, on one host. The figures are for this book on this day and
  this Zen 1 core; a different L3 changes the fill count, which is the gap to T-PERF-1's
  bar."""


class ReportError(ValueError):
    """The records cannot make a report."""


def type_label(code):
    return "none" if code == "none" else f"`{code}`"


def by_name(record, values):
    return dict(zip(record["events"], values))


def summarise_one(record):
    """One record's figures, keyed by what the file prints. ReportError if it is unusable."""
    if record["refusals"]:
        raise ReportError(f"a refused record: {'; '.join(record['refusals'])}")
    missing = [name for name in EVENTS if name not in record["events"]]
    if missing:
        raise ReportError(f"the record has no {', '.join(missing)}")
    pass_ = record["pass"]
    whole = by_name(record, pass_["whole"])
    totals = {name: 0 for name in record["events"]}
    types, ages = {}, {}
    by_age = record.get("by", "type") == "type-age"
    for entry in record["types"]:
        sums = by_name(record, entry["sums"])
        one = types.setdefault(entry["type"],
                               {"spans": 0, "sums": {name: 0 for name in record["events"]}})
        one["spans"] += entry["spans"]
        for name, value in sums.items():
            one["sums"][name] += value
            totals[name] += value
        if "age" in entry:
            ages[(entry["type"], entry["age"])] = {"spans": entry["spans"], "sums": sums}
        elif by_age and entry["type"] in NAMING_TYPES:
            raise ReportError(f"a `{entry['type']}` span with no age in a type-age record")
    if by_age and record.get("age_frames") != pass_["frames"]:
        raise ReportError("the age walk did not give every frame of the pass a slot")
    if totals != whole or by_name(record, pass_["by_type_total"]) != whole or not pass_["tiled"]:
        raise ReportError("the spans by type do not add up to the whole pass")
    if sum(entry["spans"] for entry in types.values()) != pass_["frames"]:
        raise ReportError("the spans by type are not one a frame")
    if pass_["frames"] != record["mix"]["messages"]:
        raise ReportError(f"{pass_['frames']} frames in the pass, {record['mix']['messages']} "
                          "messages counted by the warm-up walk")
    book_spans = sum(types[code]["spans"] for code in BOOK_TYPES if code in types)
    if book_spans != record["mix"]["book_messages"]:
        raise ReportError(f"{book_spans} book-message spans, {record['mix']['book_messages']} "
                          "book messages counted by the warm-up walk")
    empties = {when: (record[f"empty_{when}"]["spans"],
                      by_name(record, record[f"empty_{when}"]["sums"]))
               for when in ("before", "after")}
    return derive(types, ages, by_age, whole, empties,
                  by_name(record, record["around_pass"]["read"]),
                  by_name(record, record["around_pass"]["rdpmc"]))


def derive(types, ages, by_age, whole, empties, around_read, around_rdpmc):
    """One process's figures from its integers, a record's or the ones a file publishes: the
    spans and counts of each type (and slot), the whole pass, and the empty spans' counts."""
    empty = {}
    for name in whole:
        before, after = (empties[when][1][name] / empties[when][0] for when in ("before", "after"))
        empty[name] = {"before": before, "after": after, "cost": (before + after) / 2}
    book = {name: sum(types[code]["sums"][name] for code in BOOK_TYPES if code in types)
            for name in whole}
    return {"whole": whole, "types": types, "ages": ages, "by_age": by_age,
            "empty": empty, "empties": empties, "book": book,
            "book_messages": sum(types[code]["spans"] for code in BOOK_TYPES if code in types),
            "messages": sum(entry["spans"] for entry in types.values()),
            "around_read": around_read, "around_rdpmc": around_rdpmc}


def per(value, count):
    return value / count if count else 0.0


def median_of(values):
    return statistics.median(values)


def fmt(value, places=4):
    return f"{value:,.{places}f}"


def processes(count):
    return "1 process" if count == 1 else f"{count} processes"


def over(count, what="the repetitions"):
    """How a figure over `count` processes is taken, with the plan beside it if they fall short."""
    if count >= PLANNED:
        return f"median over {what}"
    if count == 1:
        return f"from 1 process of the {PLANNED} planned, so no median, min or max"
    return f"median over {count} processes of the {PLANNED} planned"


def spread_text(values):
    """A verdict's range, in words: the count beside it if it falls short of the plan, and no
    range from a single process."""
    if len(values) < 2:
        return f"1 process of the {PLANNED} planned, so no range"
    text = f"min {fmt(min(values), 3)}, max {fmt(max(values), 3)}"
    return text if len(values) >= PLANNED else f"{text}, {len(values)} of the {PLANNED} planned"


def spread(values, places=4):
    """The min and max cells of a figure; a single process has neither."""
    if len(values) < 2:
        return [ONE, ONE]
    return [fmt(min(values), places), fmt(max(values), places)]


def headline(summaries, name):
    """Per book message and per message, raw, as (median, min, max) each."""
    book = [per(s["book"][name], s["book_messages"]) for s in summaries]
    every = [per(s["whole"][name], s["messages"]) for s in summaries]
    return book, every


def verdict_line(target, bar, figure, cost, what, spread):
    passed = figure <= bar
    gap = "" if passed else f", missing it by {fmt(figure - bar, 3)}"
    return (f"- {target}, {what}, against ≤ {bar}: **{'PASS' if passed else 'FAIL'}**. Measured "
            f"{fmt(figure, 3)} raw ({spread}){gap}; {fmt(figure - cost, 3)} with the empty "
            f"span's {fmt(cost, 4)} taken off.")


def type_table(summaries, name):
    codes = sorted({code for s in summaries for code in s["types"]},
                   key=lambda c: (c not in BOOK_TYPES, BOOK_TYPES.index(c)
                                  if c in BOOK_TYPES else 0, c))
    cost = median_of([s["empty"][name]["cost"] for s in summaries])
    rows = ["| Type | Message | Messages | Per message, raw | Min | Max | Empty span "
            "| Per message, empty span taken off | Share of the pass |",
            "|---|---|---|---|---|---|---|---|---|"]
    for code in codes:
        figures = [per(s["types"][code]["sums"][name], s["types"][code]["spans"])
                   for s in summaries if code in s["types"]]
        spans = summaries[0]["types"].get(code, {"spans": 0})["spans"]
        share = median_of([per(s["types"][code]["sums"][name], s["whole"][name])
                           for s in summaries if code in s["types"]])
        rows.append(f"| {type_label(code)} | {TYPE_NAMES.get(code, 'unnamed')} | {spans:,} "
                    f"| {fmt(median_of(figures))} | " + " | ".join(spread(figures))
                    + f" | {fmt(cost)} | {fmt(median_of(figures) - cost)} | {100 * share:.2f}% |")
    return rows


def totals_table(summary, names, head="Type", every="all types", slots=None):
    """One repetition's integers: each type's (or slot's) spans and counts, their sum, the whole
    pass by rdpmc, and the empty spans before and after it."""
    if slots is None:
        slots = [(type_label(code), entry) for code, entry in summary["types"].items()]

    def row(label, spans, sums):
        return f"| {label} | {spans:,} | " + " | ".join(f"{sums[name]:,}" for name in names) + " |"
    rows = [f"| {head} | Spans | " + " | ".join(names) + " |", "|---" * (len(names) + 2) + "|"]
    rows += [row(label, entry["spans"], entry["sums"]) for label, entry in slots]
    rows.append(row(every, sum(e["spans"] for e in summary["types"].values()),
                    {name: sum(e["sums"][name] for e in summary["types"].values())
                     for name in names}))
    rows.append(row(WHOLE, summary["messages"], summary["whole"]))
    for when in ("before", "after"):
        spans, sums = summary["empties"][when]
        rows.append(row(EMPTY_ROW.format(when=when), spans, sums))
    return rows


def totals_sections(summaries, title, every_label):
    """A totals section for each repetition, from which --check works every figure out again."""
    out = []
    for number, summary in enumerate(summaries, start=1):
        out += ["", f"## {title}, repetition {number}", "", TOTALS_TEXT.format(
            number=number, each=every_label), ""]
        out += (age_totals_table(summary, list(EVENTS)) if summary["by_age"] else
                totals_table(summary, list(EVENTS)))
    return out


def load_lines(stderr_text):
    start = stamp_load.one_line(stamp_load.START, stderr_text, "start")
    end = stamp_load.one_line(stamp_load.END, stderr_text, "end")
    return tuple(stamp_load.HOST.sub("host=debian", line) for line in (start, end))


def timestamp(line):
    match = check_results.TIMESTAMP.search(line)
    if match is None:
        raise ReportError(f"no timestamp in {line!r}")
    return match.group(1)


def design_of(record):
    return record["header"]["design"].split(":", 1)[0]


def same_header(records):
    """The header is the first record's, so every record must give the same host, build and
    day. ReportError if any differs."""
    for field in SAME_FIELDS:
        values = {record["header"][field] for record in records}
        if len(values) != 1:
            raise ReportError(f"the records differ in {field}: {sorted(values)}")


def render(records, stderrs, machine_checks, prediction):
    summaries = [summarise_one(record) for record in records]
    designs = {design_of(record) for record in records}
    if len(designs) != 1:
        raise ReportError(f"the records are of more than one design: {sorted(designs)}")
    design = designs.pop()
    same_header(records)
    lines = [load_lines(text) for text in stderrs]
    run_id = f"{prediction}-" + timestamp(lines[0][0]).replace(":", "").replace("-", "")
    header = records[0]["header"]
    day = header.get("dataset", "").split(".", 1)[0]
    peak = max(record["peak_rss_bytes"] for record in records) / 1024 ** 3
    migrations = sum(record["migrations"] for record in records)
    out = [f"# Hardware counts by message type, {design} on {header['host']}, {day}", "",
           "Generated by `scripts/counts_report.py` from raw records that are not committed. "
           "Do not edit by hand.", "", f"Host: **{header['host']}**. Numbers from the two hosts "
           "are never mixed.", "", "## Header", ""]
    out += [f"- {name}: {value}" for name, value in header.items() if name != "page_size"]
    out += [f"- prediction: {prediction}", f"- run_id: {run_id}",
            f"- repetitions: {len(records)}", f"- messages: {summaries[0]['messages']:,}",
            f"- book_messages: {summaries[0]['book_messages']:,}",
            f"- empty_spans: {records[0]['empty_before']['spans']:,} before and after the pass, "
            "each process",
            f"- migrations_all_repetitions: {migrations}",
            f"- peak_rss: {peak:.2f} GiB, getrusage of bench_counts, largest repetition",
            f"- measure_load_start: {lines[0][0]}", f"- measure_load_end: {lines[-1][1]}",
            f"- run_started: {timestamp(lines[0][0])}",
            f"- run_finished: {timestamp(lines[-1][1])}", "",
            "## How the counts are taken", "",
            METHOD.format(empty=records[0]["empty_before"]["spans"]), ""]
    out += type_figures(summaries)
    out += ["", "## What this does not show", "", CAVEATS, "",
            "## machine_check output", "", "Before the first repetition and after the last, "
            "each under `measure.sh taskset -c 4`.", "", "```"]
    out += [text.strip() for text in machine_checks]
    out += ["```", "", "## measure.sh load lines", "", "```"]
    out += [line for pair in lines for line in pair]
    out += ["```", "", "## Command", "", "```",
            f"measure.sh taskset -c 4 build/bench/bench_counts --input <raw>/{day}.NASDAQ_ITCH50 "
            f"--design {design} --core 4 --commit <commit> --out <raw>/run-N.json", "```", ""]
    return "\n".join(out), run_id


def type_figures(summaries):
    """Every figure of a file by type, from the verdicts to the totals, from the processes'
    integers alone: render writes it from the records, and --check again from the totals."""
    fills_book, fills_all = headline(summaries, FILLS)
    branch_book, branch_all = headline(summaries, BRANCH)
    fills_cost = median_of([s["empty"][FILLS]["cost"] for s in summaries])
    branch_cost = median_of([s["empty"][BRANCH]["cost"] for s in summaries])
    out = ["## Verdicts", "",
           verdict_line("T-PERF-1", T_PERF_1, median_of(fills_book), fills_cost,
                        "`r0843:u` per book message, a floor on DRAM demand fills",
                        spread_text(fills_book)),
           verdict_line("T-PERF-2", T_PERF_2, median_of(branch_all), branch_cost,
                        "branch misses per message, against the floor BENCHMARKS.md states, "
                        "with no top-tier bar claimed",
                        spread_text(branch_all)), "",
           "T-PERF-1's bar is carteret's, on a Ryzen 9 5950X (Zen 3) with 32 MB of L3 to one "
           "core and its identity hash. This host is a Ryzen Embedded V1605B (Zen 1) with 4 MB "
           "of L3 shared by four cores, so far less of the book's working set stays on chip "
           "and more of its lines come from DRAM. The bar is kept, and the gap is the host's "
           "and the design's together: the source's own book, as shipped, gave "
           f"{SOURCE['book']} on its host.", "",
           "| Figure | Median | Min | Max | Empty span | Median, empty span taken off |",
           "|---|---|---|---|---|---|"]
    for label, values, cost in (
            ("`r0843:u` per book message, a floor on DRAM demand fills", fills_book, fills_cost),
            ("`r0843:u` per message, a floor on DRAM demand fills", fills_all, fills_cost),
            ("branch misses per book message", branch_book, branch_cost),
            ("branch misses per message", branch_all, branch_cost)):
        out.append(f"| {label} | {fmt(median_of(values))} | " + " | ".join(spread(values))
                   + f" | {fmt(cost)} | {fmt(median_of(values) - cost)} |")
    out += ["", "## Against the source by type", "",
            "`r0843:u` per message of each type, a floor on DRAM demand fills, beside carteret's "
            "figures for its book as shipped, on its host. Both are demand fills from DRAM on "
            "the core; the hosts and the books differ.", "",
            "| Type | Here, raw | Here, empty span taken off | carteret as shipped |",
            "|---|---|---|---|"]
    for code in ("A", "D", "E", "U", "book"):
        if code == "book":
            here = median_of(fills_book)
            label = "every book message"
        else:
            here = median_of([per(s["types"][code]["sums"][FILLS], s["types"][code]["spans"])
                              for s in summaries if code in s["types"]])
            label = f"`{code}`"
        out.append(f"| {label} | {fmt(here, 3)} | {fmt(here - fills_cost, 3)} "
                   f"| {SOURCE[code]} |")
    out += ["", "## The cost of counting", "",
            "What one empty span counts, the mean over each process's empty spans before and "
            f"after the pass, {over(len(summaries))}. Every message's span holds one.", "",
            "| Event | Before the pass | After the pass | Used beside the figures |",
            "|---|---|---|---|"]
    for name in EVENTS:
        out.append(f"| {name} | {fmt(median_of([s['empty'][name]['before'] for s in summaries]))}"
                   f" | {fmt(median_of([s['empty'][name]['after'] for s in summaries]))} "
                   f"| {fmt(median_of([s['empty'][name]['cost'] for s in summaries]))} |")
    out += ["", "## The whole pass", "",
            "Each repetition: the spans by type summed, the whole pass by `rdpmc` (the last "
            "reading less the first), and the `rdpmc` and `read()` taken outside the pass. The "
            "first two must be equal, and are. The last two also hold the code between them and "
            "the pass's first and last readings: the setup of the pass and, for `read()`, the "
            "user-mode part of the system call.", "",
            "| Repetition | Event | By type | Whole pass, rdpmc | Equal | Around the pass, "
            "rdpmc | Around the pass, read() | read() less by type |",
            "|---|---|---|---|---|---|---|---|"]
    for number, summary in enumerate(summaries, start=1):
        for name in EVENTS:
            by_type = sum(entry["sums"][name] for entry in summary["types"].values())
            whole = summary["whole"][name]
            out.append(f"| {number} | {name} | {by_type:,} | {whole:,} "
                       f"| {'yes' if by_type == whole else 'NO'} "
                       f"| {summary['around_rdpmc'][name]:,} | {summary['around_read'][name]:,} "
                       f"| {summary['around_read'][name] - by_type:,} |")
    for name in EVENTS:
        out += ["", f"## Per message type: {name}", "",
                f"Each type's spans over its messages, {over(len(summaries))}, with the "
                "empty span's cost beside it and taken off in the column that says so.", ""]
        out += type_table(summaries, name)
    return out + totals_sections(summaries, "Totals by type", "type")


AGE_METHOD = """\
Before anything is counted, each process walks the day once through a book of the same arm
of its own, which it then frees (`bench/order_age.hpp`). For every message that names a live
order, E, C, X, D and U, it takes the message's timestamp less that of the add that made the
order, an A, F or U, and gives the frame the slot of its type and that age's class. Every
other frame keeps its type byte's slot. The counted pass then charges each span to its
frame's slot, reading one byte a frame from that walk in order, so the book under count makes
no lookup it would not make without it. A and F name no order, so they have no age."""

ATTRIBUTES = """\
- `r0843:u`, a floor on DRAM demand fills, counts the demand loads that missed the L1, the
  L2 and the L3 and were filled from local DRAM. Each such fill is a last-level miss, so the
  figure is a floor on last-level misses: a fill a prefetcher brought in, a store's, or
  one from another node is not in it. It says which message's span the fill fell in, and so
  its type and the age of the order it named; it does not say which structure the line
  belonged to.
- `cache-misses` counts L2 misses on Zen 1: data and instruction requests that miss the L2,
  L2 prefetches not included. An L2 miss may still hit the L3, so it is not a last-level
  miss, and the two counts are not comparable as one quantity.
- No L3 counter is open to a normal user on this host: the L3 PMU has no per-task mode and
  the generic `LLC-*` events are not supported. No figure here is a last-level miss count.
- Which structure the fills come from is a separate experiment, with its own method:
  `results/structure_debian_12302019.md`."""

AGE_CAVEATS = """\
- Age is the feed's own clock: the message's timestamp less the add's, in nanoseconds since
  midnight. A U's age is its old order's: the new order it makes is an add.
- The walk is a second book of the same arm, built and freed before the pass, and the pass
  reads one byte a frame from it in order. What that costs is shown against the same arm
  counted by type alone in the same session, where that run is given.
- One process per repetition, on one host: this Zen 1 core with 4 MB of L3 shared by four
  cores. A larger L3 keeps more of the old orders' lines, so the age at which fills rise is
  this host's, not the book's alone."""


def age_rows(summaries, name):
    """Per age class over every naming type, median over the repetitions, a message."""
    rows = []
    named_total = [sum(s["types"][code]["sums"][name] for code in NAMING_TYPES
                       if code in s["types"]) for s in summaries]
    for age in AGES:
        spans = [sum(s["ages"].get((code, age), {"spans": 0})["spans"] for code in NAMING_TYPES)
                 for s in summaries]
        if not any(spans):
            continue
        sums = [sum(s["ages"].get((code, age), {"sums": {name: 0}})["sums"][name]
                    for code in NAMING_TYPES) for s in summaries]
        figures = [per(value, count) for value, count in zip(sums, spans)]
        rows.append({"age": age, "messages": spans[0], "figure": median_of(figures),
                     "spread": spread(figures),
                     "named_share": median_of([per(v, t) for v, t in zip(sums, named_total)]),
                     "pass_share": median_of([per(v, s["whole"][name])
                                              for v, s in zip(sums, summaries)])})
    return rows


def age_table(summaries, name):
    rows = ["| Age of the order named | Messages | Per message | Min | Max | Share of the "
            "named messages' count | Share of the pass |", "|---|---|---|---|---|---|---|"]
    for row in age_rows(summaries, name):
        rows.append(f"| {row['age']} | {row['messages']:,} | {fmt(row['figure'])} | "
                    + " | ".join(row["spread"]) + f" | {100 * row['named_share']:.2f}% "
                    f"| {100 * row['pass_share']:.2f}% |")
    return rows


def type_age_table(summaries, name):
    present = [age for age in AGES if any((code, age) in s["ages"] for s in summaries
                                          for code in NAMING_TYPES)]
    rows = ["| Type | " + " | ".join(present) + " |", "|---" * (len(present) + 1) + "|"]
    for code in NAMING_TYPES:
        cells = []
        for age in present:
            figures = [per(s["ages"][(code, age)]["sums"][name], s["ages"][(code, age)]["spans"])
                       for s in summaries if (code, age) in s["ages"]]
            cells.append(fmt(median_of(figures)) if figures else "none")
        rows.append(f"| `{code}` | " + " | ".join(cells) + " |")
    rows += ["", "Messages of each type and age, repetition 1:", "",
             "| Type | " + " | ".join(present) + " |", "|---" * (len(present) + 1) + "|"]
    for code in NAMING_TYPES:
        rows.append(f"| `{code}` | " + " | ".join(
            f"{summaries[0]['ages'].get((code, age), {'spans': 0})['spans']:,}"
            for age in present) + " |")
    return rows


def age_totals_table(summary, names):
    """totals_table with each naming type's slots by age in place of its one row."""
    slots = []
    for code, entry in summary["types"].items():
        if code in NAMING_TYPES:
            slots += [(f"`{code}` {age}", summary["ages"][(code, age)]) for age in AGES
                      if (code, age) in summary["ages"]]
        else:
            slots.append((type_label(code), entry))
    return totals_table(summary, names, "Slot", "all slots", slots)


def age_summary(summaries):
    """The file's one-line summary, with the count of processes if it falls short of the plan."""
    rows = {row["age"]: row for row in age_rows(summaries, FILLS)}
    young, old = rows.get(AGES[0]), rows.get(AGES[4])
    spans = {age: row["messages"] for age, row in rows.items()}
    old_share = per(spans.get(AGES[4], 0), sum(spans.values()))
    return (f"floor on DRAM demand fills a message naming an order: "
            f"{fmt(young['figure'] if young else 0.0, 3)} under 1 ms old, "
            f"{fmt(old['figure'] if old else 0.0, 3)} at 1 s and over; "
            f"{100 * old_share:.1f}% of those messages name an order 1 s old or more"
            + (f"; {processes(len(summaries))} of the {PLANNED} planned"
               if len(summaries) < PLANNED else ""))


def check_times(check_loads, lines):
    """When each machine check began, from measure.sh's start line around it, and how far it
    falls from the first repetition's start and the last one's end. ReportError if the check
    before does not come before the first repetition or the check after not after the last."""
    if len(check_loads) != 2:
        raise ReportError("wanted the measure.sh stderr of the machine check before and after "
                          "(--machine-check-load)")
    before, after = (timestamp(load_lines(text)[0]) for text in check_loads)
    first = min(timestamp(start) for start, _ in lines)
    last = max(timestamp(end) for _, end in lines)
    lead, gap = seconds_between(before, first), seconds_between(last, after)
    if lead < 0 or gap < 0:
        raise ReportError(f"a machine check inside the run: before at {before}, after at {after}, "
                          f"the run from {first} to {last}")
    return before, lead, after, gap


def seconds_between(earlier, later):
    def instant(text):
        return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    return int((instant(later) - instant(earlier)).total_seconds())


def check_placement(before, lead, after, gap):
    """The machine check section's opening, said from when each check ran."""
    text = (f"The check before began at {before}, {lead:,} s before the first repetition began. "
            f"The check after began at {after}, {gap:,} s after the last repetition ended. Each "
            "ran under `measure.sh taskset -c 4`.")
    for which, seconds, when in (("before", lead, "began"), ("after", gap, "ended")):
        if seconds > CHECK_GAP:
            text += (f" The check {which} was not taken for this run: {seconds:,} s lie between "
                     f"them, so it is not a check of the host as the run {when}.")
    return text


def render_ages(records, stderrs, machine_checks, prediction, plain=None, plain_stderrs=(),
                check_loads=(), not_published=()):
    summaries = [summarise_one(record) for record in records]
    if not all(s["by_age"] for s in summaries):
        raise ReportError("a record counted by type alone among the type-age records")
    designs = {design_of(record) for record in records}
    plain_summaries = [summarise_one(record) for record in plain or []]
    if len(designs) != 1 or any(design_of(r) not in designs for r in plain or []):
        raise ReportError(f"the records are of more than one design: {sorted(designs)}")
    if any(s["by_age"] for s in plain_summaries):
        raise ReportError("a type-age record given as counted by type alone")
    design = designs.pop()
    same_header(records + list(plain or []))
    lines = [load_lines(text) for text in stderrs]
    plain_lines = [load_lines(text) for text in plain_stderrs]
    run_id = f"{prediction}-" + timestamp(lines[0][0]).replace(":", "").replace("-", "")
    before, lead, after, gap = check_times(check_loads, lines + plain_lines)
    header = records[0]["header"]
    day = header.get("dataset", "").split(".", 1)[0]
    peak = max(record["peak_rss_bytes"] for record in records + list(plain or [])) / 1024 ** 3
    migrations = sum(record["migrations"] for record in records + list(plain or []))
    out = [f"# Misses by message type and by the age of the order named, {design} on "
           f"{header['host']}, {day}", "",
           "Generated by `scripts/counts_report.py` from raw records that are not committed. "
           "Do not edit by hand.", "", f"Host: **{header['host']}**. Numbers from the two hosts "
           "are never mixed.", "", "## Header", ""]
    out += [f"- {name}: {value}" for name, value in header.items() if name != "page_size"]
    out += [f"- prediction: {prediction}", f"- run_id: {run_id}", "- by: type-age",
            f"- repetitions: {len(records)}",
            f"- repetitions_by_type_alone: {len(plain_summaries)}",
            f"- messages: {summaries[0]['messages']:,}",
            f"- book_messages: {summaries[0]['book_messages']:,}",
            f"- empty_spans: {records[0]['empty_before']['spans']:,} before and after the pass, "
            "each process",
            f"- migrations_all_repetitions: {migrations}",
            f"- peak_rss: {peak:.2f} GiB, getrusage of bench_counts, largest repetition",
            f"- measure_load_start: {lines[0][0]}", f"- measure_load_end: {lines[-1][1]}",
            f"- run_started: {timestamp(lines[0][0])}",
            f"- run_finished: {timestamp((plain_lines or lines)[-1][1])}",
            f"- machine_check_before_at: {before}, {lead:,} s before the first repetition began",
            f"- machine_check_after_at: {after}, {gap:,} s after the last repetition ended",
            f"- summary: {age_summary(summaries)}", "",
            "## How the counts are taken", "",
            METHOD.format(empty=records[0]["empty_before"]["spans"]), "", AGE_METHOD, "",
            "## What each counter can attribute", "", ATTRIBUTES, ""]
    out += age_figures(summaries, plain_summaries)
    out += ["", "## What this does not show", "", CAVEATS, AGE_CAVEATS, ""]
    if not_published:
        out += ["## Processes of this arm not published", "",
                "Each has its own run-log row. What keeps it out is the rule each row gives; a "
                "process is never kept out for its figures.", ""]
        out += [f"- {line}" for line in not_published]
        out.append("")
    out += ["## machine_check output", "", check_placement(before, lead, after, gap), "", "```"]
    out += [text.strip() for text in machine_checks]
    out += ["```", "", "## measure.sh load lines", "", "```"]
    out += [line for pair in lines + plain_lines for line in pair]
    out += ["```", "", "## Command", "", "```",
            f"measure.sh taskset -c 4 build/bench/bench_counts --input <raw>/{day}.NASDAQ_ITCH50 "
            f"--design {design} --by type-age --core 4 --commit <commit> --out <raw>/run-N.json",
            f"measure.sh taskset -c 4 build/bench/bench_counts --input <raw>/{day}.NASDAQ_ITCH50 "
            f"--design {design} --by type --core 4 --commit <commit> --out <raw>/plain-N.json",
            "```", ""]
    return "\n".join(out), run_id


def age_figures(summaries, plain_summaries):
    """Every figure of a file by type and age, from the tables by age to the totals, from the
    processes' integers alone: render writes it from the records, and --check again from the
    totals."""
    out = []
    for name in (FILLS, L2):
        out += [f"## By the age of the order named: {name}", "",
                f"E, C, X, D and U pooled, each age's spans over its messages, "
                f"{over(len(summaries))}, raw: the empty span's cost is in the next section.", ""]
        out += age_table(summaries, name)
        out += ["", f"## By type and age: {name}", "",
                f"Per message of each type and age, {over(len(summaries))}, raw.", ""]
        out += type_age_table(summaries, name)
        out += ["", f"## By message type: {name}", "",
                f"Each type's spans over its messages, {over(len(summaries))}, with the "
                "empty span's cost beside it.", ""]
        out += type_table(summaries, name)
        out.append("")
    if plain_summaries:
        out += ["## What the age walk costs", "",
                "The same arm counted by type alone, in the same session, against the type-age "
                "processes: each event per book message, "
                + (over(len(plain_summaries), "each one's repetitions") + "."
                   if len(plain_summaries) >= PLANNED and len(summaries) >= PLANNED else
                   f"by type alone {over(len(plain_summaries))}, and by type and age "
                   f"{over(len(summaries))}."),
                "", "| Event | By type alone | By type and age | Difference |", "|---|---|---|---|"]
        for name in EVENTS:
            alone = median_of(headline(plain_summaries, name)[0])
            aged = median_of(headline(summaries, name)[0])
            out.append(f"| {name} | {fmt(alone)} | {fmt(aged)} "
                       f"| {100 * per(aged - alone, alone):+.2f}% |")
        out.append("")
    out += ["## The cost of counting", "",
            "What one empty span counts, the mean over each process's empty spans before and "
            f"after the pass, {over(len(summaries), 'the type-age repetitions')}.", "",
            "| Event | Before the pass | After the pass | Used beside the figures |",
            "|---|---|---|---|"]
    for name in EVENTS:
        out.append(f"| {name} | {fmt(median_of([s['empty'][name]['before'] for s in summaries]))}"
                   f" | {fmt(median_of([s['empty'][name]['after'] for s in summaries]))} "
                   f"| {fmt(median_of([s['empty'][name]['cost'] for s in summaries]))} |")
    return (out + totals_sections(summaries, "Totals by type and age", "slot")
            + totals_sections(plain_summaries, "Totals by type alone", "type"))


def check_ages(text):
    """What is wrong with a published file of counts by type and age, read alone."""
    fields = check_results.parse_header(text)
    if not fields:
        return [("header-missing", "no Header section")]
    found = header_violations(text, fields)
    figures, summaries = figure_violations(text, fields, by_age=True)
    if not summaries:
        return found + figures
    found += figures
    if not any(code == "E" for code, _ in summaries[0]["ages"]):
        found.append(("ages-missing", "no slot of an E by age in the totals"))
    table = check_results.section(text, f"By the age of the order named: {FILLS}") or ""
    figures = {}
    for line in table.splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 7 and cells[0] in AGES:
            figures[cells[0]] = float(cells[2].replace(",", ""))
    summary = fields.get("summary", "")
    for age, said in ((AGES[0], "under 1 ms old"), (AGES[4], "at 1 s and over")):
        if age in figures and not said_near(summary, said, figures[age]):
            found.append(("summary-differs", f"{age}: the table gives {figures[age]}, the "
                                             f"summary says {summary!r}"))
    return found + count_violations(text, fields) + placement_violations(text, fields)


def count_violations(text, fields, by_age=True):
    """A file from fewer processes than planned gives the count in every section that takes a
    median (and, by age, in its summary), uses no wording that implies the plan was met, and
    from a single process gives no min or max."""
    found = []
    try:
        count = int(fields.get("repetitions", ""))
        plain = int(fields.get("repetitions_by_type_alone", "0"))
    except ValueError:
        return [("count-unsaid", "no count of repetitions in the header")]
    if count < PLANNED:
        said = f"{processes(count)} of the {PLANNED} planned"
        if by_age and said not in fields.get("summary", ""):
            found.append(("count-unsaid", f"the summary does not say it rests on {said}"))
        for line in text.splitlines():
            if "median over the repetitions" in line or "median over the type-age" in line:
                found.append(("count-unsaid", f"a median over {count} of the {PLANNED} "
                                              f"processes planned, said as over all: {line!r}"))
    if 0 < plain < PLANNED and (f"by type alone {over(plain)}"
                                not in (check_results.section(text, "What the age walk costs")
                                        or "")):
        found.append(("count-unsaid", f"the age walk's cost rests on {plain} of the {PLANNED} "
                                      "processes by type alone planned and does not say so"))
    for line in text.splitlines():
        if count < PLANNED and line.startswith("- T-PERF-") and (
                f"of the {PLANNED} planned" not in line or (count == 1 and "(min " in line)):
            found.append(("count-unsaid", f"a verdict from {count} of the {PLANNED} processes "
                                          f"planned that does not say so: {line!r}"))
        if count != 1 or not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        # Where the min sits: the verdicts table's third cell, the age table's fourth, the type
        # table's fifth.
        at = (2 if len(cells) == 6 and cells[0].startswith(("`r0843", "branch misses")) else
              3 if len(cells) == 7 and cells[0] in AGES else
              4 if len(cells) == 9 and cells[0].startswith(("`", "none")) else None)
        if at is not None and cells[at] != ONE:
            found.append(("range-from-one", f"a min and max from one process: {line!r}"))
    return found


def placement_violations(text, fields):
    """Each machine check is placed against the run, and one more than CHECK_GAP from it is
    said not to be this run's."""
    found = []
    try:
        first, last = fields["run_started"], fields["run_finished"]
        before = fields["machine_check_before_at"].split(",")[0]
        after = fields["machine_check_after_at"].split(",")[0]
        lead, gap = seconds_between(before, first), seconds_between(last, after)
    except (KeyError, ValueError) as error:
        return [("machine-check-unplaced", f"no time for a machine check: {error!r}")]
    if lead < 0 or gap < 0:
        found.append(("machine-check-unplaced", f"a check inside the run: {before}, {after}"))
    if (fields["machine_check_before_at"] != f"{before}, {lead:,} s before the first repetition "
                                                "began"
            or fields["machine_check_after_at"] != f"{after}, {gap:,} s after the last "
                                                   "repetition ended"):
        found.append(("machine-check-unplaced", "the header's gaps are not the times'"))
    output = check_results.section(text, "machine_check output") or ""
    for which, seconds in (("before", lead), ("after", gap)):
        if (seconds > CHECK_GAP) != (f"The check {which} was not taken for this run" in output):
            found.append(("machine-check-unplaced", f"the check {which} is {seconds:,} s from the "
                                                    "run, and the file says otherwise"))
    if "Before the first repetition and after the last" in output:
        found.append(("machine-check-unplaced", "the old wording, which claims a check after "
                                                "the run whatever its time"))
    return found


def said_near(summary, said, figure):
    """True if the summary gives `figure` to three places just before `said`. The table's
    four-place figure and the summary are each rounded from the same median, which with an
    even number of repetitions is a mean of two, so they may round apart by half a unit."""
    match = re.search(rf"(\d+\.\d{{3}}) {re.escape(said)}", summary)
    return match is not None and abs(float(match.group(1)) - figure) <= 0.00055


def around_counts(text):
    """The rdpmc and read() around each repetition's pass, from the whole-pass table, as
    {repetition: (read, rdpmc)}; a file by type and age has no such table."""
    around = {}
    for line in (check_results.section(text, "The whole pass") or "").splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 8 and cells[0].isdigit():
            read, rdpmc = around.setdefault(int(cells[0]), ({}, {}))
            rdpmc[cells[1]], read[cells[1]] = integer(cells[5]), integer(cells[6])
    return around


def integer(cell):
    return int(cell.replace(",", ""))


def published_summaries(text, title, every):
    """Each repetition's totals section read back into what summarise_one gives the records,
    with the counts around each pass from the whole-pass table. ReportError if a section is not
    whole."""
    summaries, around, number = [], around_counts(text), 1
    totals = (every, WHOLE, EMPTY_ROW.format(when="before"), EMPTY_ROW.format(when="after"))
    while (body := check_results.section(text, f"{title}, repetition {number}")) is not None:
        names, rows = None, {}
        for line in body.splitlines():
            if not line.startswith("| "):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if cells[1] == "Spans":
                names = cells[2:]
            else:
                rows[cells[0]] = (integer(cells[1]), [integer(cell) for cell in cells[2:]])
        missing = [label for label in totals if label not in rows]
        if names != list(EVENTS) or missing:
            raise ReportError(f"{title}, repetition {number}: columns {names}, rows {missing} "
                              "missing")
        types, ages = {}, {}
        for label, (spans, values) in rows.items():
            sums = dict(zip(names, values))
            slot = re.fullmatch(r"`([^`]+)` (.+)", label)
            if label in totals:
                continue
            if slot is None:
                types["none" if label == "none" else label.strip("`")] = {"spans": spans,
                                                                          "sums": sums}
                continue
            ages[slot.groups()] = {"spans": spans, "sums": sums}
            one = types.setdefault(slot.group(1), {"spans": 0, "sums": dict.fromkeys(names, 0)})
            one["spans"] += spans
            for name in names:
                one["sums"][name] += sums[name]
        empties = {when: (rows[EMPTY_ROW.format(when=when)][0],
                          dict(zip(names, rows[EMPTY_ROW.format(when=when)][1])))
                   for when in ("before", "after")}
        summary = derive(types, ages, every == "all slots", dict(zip(names, rows[WHOLE][1])),
                         empties, *around.get(number, ({}, {})))
        summary["printed"] = {"every": rows[every], "whole": rows[WHOLE]}
        summaries.append(summary)
        number += 1
    return summaries


def figure_violations(text, fields, by_age):
    """Every figure the file prints from its processes, worked out again from the integers it
    publishes for each repetition, and compared line for line; and those integers tile. Returns
    the violations and the summaries read back."""
    title, every = (("Totals by type and age", "all slots") if by_age else
                    ("Totals by type", "all types"))
    try:
        summaries = published_summaries(text, title, every)
        plain = published_summaries(text, "Totals by type alone", "all types") if by_age else []
    except (ReportError, ValueError, IndexError) as error:
        return [("totals-missing", str(error))], []
    found = []
    wanted = {"repetitions": len(summaries)}
    if by_age:
        wanted["repetitions_by_type_alone"] = len(plain)
    for name, count in wanted.items():
        if (name == "repetitions" and not count) or fields.get(name) != str(count):
            found.append(("totals-missing", f"{count} repetitions' totals, and the header's "
                                            f"{name} is {fields.get(name)!r}"))
    if not summaries:
        return found, []
    for number, summary in enumerate(summaries + plain, start=1):
        listed, whole = summary["printed"]["every"], summary["printed"]["whole"]
        by_type = [sum(e["spans"] for e in summary["types"].values())] + [
            sum(e["sums"][name] for e in summary["types"].values()) for name in EVENTS]
        for name, total, said, pass_ in zip(("spans", *EVENTS), by_type,
                                            [listed[0], *listed[1]], [whole[0], *whole[1]]):
            if not total == said == pass_:
                found.append(("not-tiled", f"totals {number}, {name}: the rows add up to "
                                           f"{total:,}, the file says {said:,} and the whole "
                                           f"pass {pass_:,}"))
    if found:
        return found, summaries
    first = summaries[0]
    said = {"messages": f"{first['messages']:,}", "book_messages": f"{first['book_messages']:,}",
            "empty_spans": f"{first['empties']['before'][0]:,} before and after the pass, each "
                           "process"}
    if by_age:
        said["summary"] = age_summary(summaries)
    for name, value in said.items():
        if fields.get(name) != value:
            found.append(("figures-differ", f"header {name}: the totals give {value!r}, the file "
                                            f"says {fields.get(name)!r}"))
    try:
        lines = age_figures(summaries, plain) if by_age else type_figures(summaries)
    except (KeyError, ZeroDivisionError, ValueError) as error:
        return found + [("totals-missing", f"the totals cannot give the figures: {error!r}")], \
            summaries
    lines = "\n".join(lines).split("\n")
    start = text.find("\n" + lines[0] + "\n") + 1
    end = text.find("\n\n## What this does not show")
    printed = text[start:end].split("\n") if start and end > start else []
    for number, (want, got) in enumerate(zip(lines, printed), start=1):
        if want != got:
            found.append(("figures-differ", f"line {number} of the figures: the totals give "
                                            f"{want!r}, the file says {got!r}"))
            break
    else:
        if len(lines) != len(printed):
            found.append(("figures-differ", f"the totals give {len(lines)} lines of figures, the "
                                            f"file has {len(printed)}"))
    return found, summaries


def figure_of(text, label):
    """The median of a row of the Verdicts table, and its empty span column."""
    for line in (check_results.section(text, "Verdicts") or "").splitlines():
        if line.startswith(f"| {label} |"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            return float(cells[1].replace(",", "")), float(cells[4].replace(",", ""))
    return None


def header_violations(text, fields):
    """What every file of counts must say of its host and its run, and how it names events."""
    found = []
    if fields.get("host") != "debian":
        found.append(("host-label", repr(fields.get("host"))))
    if not COMMIT_FIELD.match(fields.get("commit", "")):
        found.append(("commit-missing", repr(fields.get("commit"))))
    if not re.match(r"pinned to core 4: .*read back \[4\]$", fields.get("pinning", "")):
        found.append(("pin-failed", fields.get("pinning", "no field")))
    if fields.get("governor") != "performance":
        found.append(("governor", fields.get("governor", "no field")))
    found += list(check_results.check_private(text, fields))
    for name in ("measure_load_start", "measure_load_end"):
        line = fields.get(name, "")
        code = check_results.EXIT_CODE.search(line)
        if not line.startswith("measure.sh: ") or (code is not None and code.group(1) != "0"):
            found.append(("load-line-invalid", f"{name}: {line!r}"))
    output = check_results.section(text, "machine_check output") or ""
    if output.count("machine_check: passed") != 2:
        found.append(("machine-check-failed", "wanted a passing check before and after"))
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern, said in NAME_RULES:
            if pattern.search(line) and said not in line:
                found.append(("name-unsaid", f"line {number} names {pattern.pattern} "
                                             f"without '{said}'"))
    return found


def check_published(text):
    """What is wrong with a published file, read alone, as (code, detail) pairs."""
    fields = check_results.parse_header(text)
    if not fields:
        return [("header-missing", "no Header section")]
    found = header_violations(text, fields)
    found += figure_violations(text, fields, by_age=False)[0]
    found += count_violations(text, fields, by_age=False)
    if "| NO |" in (check_results.section(text, "The whole pass") or ""):
        found.append(("not-tiled", "a repetition's spans do not add up to its whole pass"))
    if "Empty span" not in (check_results.section(text, "Per message type: " + FILLS) or ""):
        found.append(("cost-unshown", "the fill figures have no empty span beside them"))
    printed = {match.group(1): (match.group(2), float(match.group(3)))
               for match in VERDICT.finditer(text)}
    for target, bar, label in (
            ("T-PERF-1", T_PERF_1, "`r0843:u` per book message, a floor on DRAM demand fills"),
            ("T-PERF-2", T_PERF_2, "branch misses per message")):
        figure = figure_of(text, label)
        if target not in printed or figure is None:
            found.append(("verdict-missing", target))
            continue
        expected = "PASS" if figure[0] <= bar else "FAIL"
        if printed[target][0] != expected or abs(printed[target][1] - figure[0]) > 0.00055:
            found.append(("verdict-faked", f"{target}: the table gives {figure[0]} and "
                                           f"{expected}, the line says {printed[target]}"))
    return found


def read_inputs(args):
    records = [json.loads(path.read_text(encoding="utf-8")) for path in args.record]
    stderrs = [path.read_text(encoding="utf-8", errors="replace") for path in args.load]
    checks = [path.read_text(encoding="utf-8") for path in args.machine_check]
    if not records or len(records) != len(stderrs) or len(checks) != 2:
        raise ReportError("wanted one --record and --load for each repetition, and two "
                          "--machine-check")
    return records, stderrs, checks


def report_violations(violations):
    for code, detail in violations:
        print(f"counts_report: FAIL {code}: {detail}")
    return 1 if violations else 0


def is_age_file(text):
    return "- by: type-age" in text.split("## How the counts are taken", 1)[0]


def build(args):
    """The file and its run id: by type and age if the records were counted so, else by type."""
    records, stderrs, checks = read_inputs(args)
    if all(record.get("by", "type") != "type-age" for record in records):
        if args.plain_record:
            raise ReportError("--plain-record goes with records counted by type and age")
        return render(records, stderrs, checks, args.prediction)
    plain = [json.loads(path.read_text(encoding="utf-8")) for path in args.plain_record]
    plain_stderrs = [path.read_text(encoding="utf-8", errors="replace")
                     for path in args.plain_load]
    if len(plain) != len(plain_stderrs):
        raise ReportError("wanted one --plain-load for each --plain-record")
    check_loads = [path.read_text(encoding="utf-8", errors="replace")
                   for path in args.machine_check_load]
    return render_ages(records, stderrs, checks, args.prediction, plain, plain_stderrs,
                       check_loads, args.not_published)


def judge(text):
    return check_ages(text) if is_age_file(text) else check_published(text)


def run_check(args):
    text = args.check.read_text(encoding="utf-8")
    violations = judge(text)
    if args.record:
        rebuilt = build(args)[0]
        if rebuilt != text:
            violations.append(("table-differs", "the file is not what the raw records give"))
    if violations:
        return report_violations(violations)
    print(f"counts_report: ok {args.check.name}")
    return 0


def run_report(args):
    text, run_id = build(args)
    violations = judge(text)
    if violations:
        return report_violations(violations)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    if args.run_log:
        commit = check_results.parse_header(text)["commit"]
        verdicts = (", ".join(f"{m.group(1)} {m.group(2)}" for m in VERDICT.finditer(text))
                    or "by type and age, " + check_results.parse_header(text)["summary"])
        with open(args.run_log, "a", encoding="utf-8") as log:
            log.write(f"| {run_id} | {args.prediction} | {commit} | Linux | published: "
                      f"{verdicts} |\n")
    print(f"counts_report: wrote {args.out.name}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", type=Path, nargs="+", default=[])
    parser.add_argument("--load", type=Path, nargs="+", default=[])
    parser.add_argument("--machine-check", type=Path, nargs="+", default=[])
    parser.add_argument("--plain-record", type=Path, nargs="+", default=[])
    parser.add_argument("--plain-load", type=Path, nargs="+", default=[])
    parser.add_argument("--machine-check-load", type=Path, nargs="+", default=[])
    parser.add_argument("--not-published", action="append", default=[], metavar="TEXT")
    parser.add_argument("--prediction")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--run-log", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args(argv)
    if args.record and not args.prediction:
        parser.error("the raw records need --prediction")
    if args.check is None and (args.out is None or not args.record):
        parser.error("wanted --check FILE, or the raw records and --out")
    try:
        return run_check(args) if args.check else run_report(args)
    except (OSError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError) as error:
        print(f"counts_report: unusable input: {error!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
