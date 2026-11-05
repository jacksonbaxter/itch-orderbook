#!/usr/bin/env python3
"""Writes the hardware counts by message type from bench_counts records (T-PERF-1, T-PERF-2).

    counts_report.py --record RUN.json... --load STDERR... --machine-check BEFORE AFTER \\
        --prediction P-NNNN --out results/counters_by_type_DESIGN_debian_DAY.md \\
        [--run-log results/run_log.md]
    counts_report.py --check FILE [--record ... --load ... --machine-check ... --prediction ...]

Each record is one fresh process. Its spans tile the pass, so by type they add up to the
whole pass by rdpmc exactly. That is checked here in every record, again on the totals the
file publishes, and a record where it does not hold is refused. The cost of counting, from
the empty spans each process measured, is shown beside every per-message figure and taken
off only in a column that says so.

On Zen 1, `cache-misses` counts L2 misses and `r0843:u` is a floor on DRAM demand fills
(results/chase_debian.md), and every line of the file that names either says so.

--check reads a published file and judges it again: the totals tile, the verdicts follow
from the figures and the targets, the names are said, nothing private is in it. Given the
raw records too, it rebuilds the file and compares.

Each violation prints as `counts_report: FAIL <code>: <detail>`.

Exit codes: 0 written, or --check found nothing, 1 --check found something, 2 bad usage or
unusable input, a refused record among them.
"""

from __future__ import annotations

import argparse
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
SOURCE = {"A": 1.16, "D": 0.58, "E": 0.79, "U": 2.30, "book": 0.983}
VERDICT = re.compile(r"^- (T-PERF-[12]), .*?: \*\*(PASS|FAIL)\*\*\. Measured ([0-9.]+) raw",
                     re.MULTILINE)
TOTAL_ROW = re.compile(r"^\| (`[^`]+`|all types|whole pass, rdpmc) \| (.*) \|$")
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
    types = {}
    for entry in record["types"]:
        sums = by_name(record, entry["sums"])
        types[entry["type"]] = {"spans": entry["spans"], "sums": sums}
        for name, value in sums.items():
            totals[name] += value
    if totals != whole or by_name(record, pass_["by_type_total"]) != whole or not pass_["tiled"]:
        raise ReportError("the spans by type do not add up to the whole pass")
    if sum(entry["spans"] for entry in types.values()) != pass_["frames"]:
        raise ReportError("the spans by type are not one a frame")
    empty = {}
    for name in record["events"]:
        before = by_name(record, record["empty_before"]["sums"])[name]
        after = by_name(record, record["empty_after"]["sums"])[name]
        empty[name] = {"before": before / record["empty_before"]["spans"],
                       "after": after / record["empty_after"]["spans"]}
        empty[name]["cost"] = (empty[name]["before"] + empty[name]["after"]) / 2
    book_spans = sum(types[code]["spans"] for code in BOOK_TYPES if code in types)
    if book_spans != record["mix"]["book_messages"]:
        raise ReportError(f"{book_spans} book-message spans, {record['mix']['book_messages']} "
                          "book messages counted by the warm-up walk")
    book = {name: sum(types[code]["sums"][name] for code in BOOK_TYPES if code in types)
            for name in record["events"]}
    return {"whole": whole, "types": types, "empty": empty, "book": book,
            "book_messages": book_spans, "messages": record["mix"]["messages"],
            "around_read": by_name(record, record["around_pass"]["read"]),
            "around_rdpmc": by_name(record, record["around_pass"]["rdpmc"])}


def per(value, count):
    return value / count if count else 0.0


def median_of(values):
    return statistics.median(values)


def fmt(value, places=4):
    return f"{value:,.{places}f}"


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
                    f"| {fmt(median_of(figures))} | {fmt(min(figures))} | {fmt(max(figures))} "
                    f"| {fmt(cost)} | {fmt(median_of(figures) - cost)} | {100 * share:.2f}% |")
    return rows


def totals_table(summary, names):
    rows = ["| Type | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]
    for code, entry in summary["types"].items():
        rows.append(f"| {type_label(code)} | "
                    + " | ".join(f"{entry['sums'][name]:,}" for name in names) + " |")
    rows.append("| all types | " + " | ".join(
        f"{sum(e['sums'][name] for e in summary['types'].values()):,}" for name in names) + " |")
    rows.append("| whole pass, rdpmc | "
                + " | ".join(f"{summary['whole'][name]:,}" for name in names) + " |")
    return rows


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


def render(records, stderrs, machine_checks, prediction):
    summaries = [summarise_one(record) for record in records]
    designs = {design_of(record) for record in records}
    if len(designs) != 1:
        raise ReportError(f"the records are of more than one design: {sorted(designs)}")
    design = designs.pop()
    lines = [load_lines(text) for text in stderrs]
    run_id = f"{prediction}-" + timestamp(lines[0][0]).replace(":", "").replace("-", "")
    header = records[0]["header"]
    day = header.get("dataset", "").split(".", 1)[0]
    peak = max(record["peak_rss_bytes"] for record in records) / 1024 ** 3
    migrations = sum(record["migrations"] for record in records)
    fills_book, fills_all = headline(summaries, FILLS)
    branch_book, branch_all = headline(summaries, BRANCH)
    fills_cost = median_of([s["empty"][FILLS]["cost"] for s in summaries])
    branch_cost = median_of([s["empty"][BRANCH]["cost"] for s in summaries])
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
            METHOD.format(empty=records[0]["empty_before"]["spans"]), "",
            "## Verdicts", "",
            verdict_line("T-PERF-1", T_PERF_1, median_of(fills_book), fills_cost,
                         "`r0843:u` per book message, a floor on DRAM demand fills",
                         f"min {fmt(min(fills_book), 3)}, max {fmt(max(fills_book), 3)}"),
            verdict_line("T-PERF-2", T_PERF_2, median_of(branch_all), branch_cost,
                         "branch misses per message, against the floor BENCHMARKS.md states, "
                         "with no top-tier bar claimed",
                         f"min {fmt(min(branch_all), 3)}, max {fmt(max(branch_all), 3)}"), "",
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
        out.append(f"| {label} | {fmt(median_of(values))} | {fmt(min(values))} "
                   f"| {fmt(max(values))} | {fmt(cost)} | {fmt(median_of(values) - cost)} |")
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
            "after the pass, median over the repetitions. Every message's span holds one.", "",
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
                "Each type's spans over its messages, median over the repetitions, with the "
                "empty span's cost beside it and taken off in the column that says so.", ""]
        out += type_table(summaries, name)
    out += ["", "## Totals by type, repetition 1", "",
            "The integers the figures come from, so that the tiling can be checked from this "
            "file: each column's types add up to its whole pass.", ""]
    out += totals_table(summaries[0], list(EVENTS))
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


def published_totals(text):
    """The totals table, as {row label: [integers]}, and its column names."""
    body = check_results.section(text, "Totals by type, repetition 1") or ""
    rows, names = {}, []
    for line in body.splitlines():
        if line.startswith("| Type |"):
            names = [cell.strip() for cell in line.strip("|").split("|")][1:]
        match = TOTAL_ROW.match(line)
        if match:
            rows[match.group(1)] = [int(cell.strip().replace(",", ""))
                                    for cell in match.group(2).split("|")]
    return rows, names


def figure_of(text, label):
    """The median of a row of the Verdicts table, and its empty span column."""
    for line in (check_results.section(text, "Verdicts") or "").splitlines():
        if line.startswith(f"| {label} |"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            return float(cells[1].replace(",", "")), float(cells[4].replace(",", ""))
    return None


def check_published(text):
    """What is wrong with a published file, read alone, as (code, detail) pairs."""
    fields = check_results.parse_header(text)
    if not fields:
        return [("header-missing", "no Header section")]
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
    rows, names = published_totals(text)
    if "all types" not in rows or "whole pass, rdpmc" not in rows or len(rows) < 3:
        found.append(("totals-missing", "the totals table is not whole"))
    else:
        types = [values for label, values in rows.items()
                 if label not in ("all types", "whole pass, rdpmc")]
        summed = [sum(column) for column in zip(*types)]
        for name, total, listed, whole in zip(names, summed, rows["all types"],
                                              rows["whole pass, rdpmc"]):
            if not total == listed == whole:
                found.append(("not-tiled", f"{name}: types add up to {total:,}, the file says "
                                           f"{listed:,} and the whole pass {whole:,}"))
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


def run_check(args):
    text = args.check.read_text(encoding="utf-8")
    violations = check_published(text)
    if args.record:
        rebuilt = render(*read_inputs(args), args.prediction)[0]
        if rebuilt != text:
            violations.append(("table-differs", "the file is not what the raw records give"))
    if violations:
        return report_violations(violations)
    print(f"counts_report: ok {args.check.name}")
    return 0


def run_report(args):
    text, run_id = render(*read_inputs(args), args.prediction)
    violations = check_published(text)
    if violations:
        return report_violations(violations)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    if args.run_log:
        commit = check_results.parse_header(text)["commit"]
        verdicts = ", ".join(f"{m.group(1)} {m.group(2)}" for m in VERDICT.finditer(text))
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
