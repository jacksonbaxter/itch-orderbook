#!/usr/bin/env python3
"""The walks of the backtester's simulated levels: the counts, and the timed ladder.

    walk_report.py counts --out results/walks_counts_DAY.md RECORD.json...
    walk_report.py check FILE...

`counts` writes the counts file from bench_walks records, one record a schedule of shadow
orders, the first being T-BT-3's, its header the first record's: every record must give the
same host, CPU, kernel, compiler, flags, commit, dataset and extract, or they are refused.
For each schedule it gives the walks, their level and order steps, by site and a message,
and the depth of the side at each walk in power-of-two buckets. The
counts are untimed and the same on any host; each record's counted engine gave the engine's
own stats and event hash, and a record that did not is refused.

scripts/walk_driver.py writes the ladder file, results/walks_ladder_HOST_DAY.md, with the
same count tables and the timed passes of bench_backtest on each schedule beside them: the
median time a message against the walk steps a message, the least-squares line through
them, and the walk's share of each schedule's time read off its slope.

`check` judges a published file again from its own tables: every total is the sum of its
sites, every figure a message is its total over the messages, every depth column sums to its
walks and its percentile rows follow from it, and the summary line is the one the tables
give. In a ladder file the summary of each schedule's times follows from its repetitions,
each repetition gave its schedule's event hash, and the line, the walk's time and the share
are recomputed from the figures printed. Anything else a results file needs is
check_results.py's, which calls this for a ladder file.

walk_driver.py works the ladder's figures out from the raw records, each time a message and
each schedule's walk steps a message unrounded, and rounds them only to print them. The
walk steps a message follow exactly from the totals and the messages the file prints, but the
times are printed to a hundredth of a ns, so check holds each figure worked out from them to
the range those printed times leave (check_results.py, "Figures computed from unrounded
values"). The line is linear in the times, so its range is exact.

Each violation prints as `walk_report: FAIL <code> <file>: <detail>`.

Exit codes: 0 written, or check found nothing; 1 check found something; 2 bad usage, or a
record refused.
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

SITES = ("execution", "cross", "depletion", "late_add", "hidden_print", "rest")
SITE_NAMES = {
    "execution": "execution (E, C)",
    "cross": "cross (an add or replace)",
    "depletion": "depletion (X, D, U)",
    "late_add": "late add",
    "hidden_print": "hidden print (P)",
    "rest": "rest (an order arriving)",
}
ALL = "all sites"
# What every record must give alike: the header prints the first record's.
SAME_FIELDS = ("host", "cpu", "kernel", "compiler", "flags", "commit", "dataset",
               "dataset_sha256")
TBT3 = {"shadow_orders": 100_000, "shadow_span_ns": 23_340_000_000_000,
        "shadow_life_ns": 60_000_000_000}
COUNT_TITLE = "The walks of the backtester's simulated levels, counted"
LADDER_TITLE = "The walks of the backtester's simulated levels, timed on a ladder of schedules"
PERCENTILES = (("Median", 50), ("90th percentile", 90), ("99th percentile", 99))  # Of 100.


class Refused(Exception):
    pass


# ---- From the records ----------------------------------------------------------------------


def schedule_name(record):
    """T-BT-3's own schedule by name, any other by what differs."""
    if all(record[key] == value for key, value in TBT3.items()):
        return "T-BT-3"
    life = record["shadow_life_ns"] / 1e9
    parts = [f"{record['shadow_orders']:,} orders", f"{life:,.6g} s life"]
    if record["shadow_span_ns"] != TBT3["shadow_span_ns"]:
        parts.append(f"{record['shadow_span_ns'] / 1e9:,.6g} s span")
    return ", ".join(parts)


def validate_record(record):
    if record.get("refusals"):
        raise Refused(f"the record was refused: {'; '.join(record['refusals'])}")
    for flag in ("identical", "consistent", "empty_at_end"):
        if record.get(flag) is not True:
            raise Refused(f"the record is not {flag.replace('_', ' ')}")
    if set(record["sites"]) != set(SITES):
        raise Refused(f"the record's sites are {sorted(record['sites'])}")


def depth_sum(site):
    return sum(depth * walks for depth, walks in site["depth"])


def site_totals(record):
    """Each site's totals and their sum: walks, non-empty, level steps, order steps, levels."""
    rows = {}
    for name in SITES:
        site = record["sites"][name]
        rows[name] = (site["walks"], site["nonempty"], site["level_steps"], site["order_steps"],
                      depth_sum(site))
    rows[ALL] = tuple(sum(row[i] for row in rows.values()) for i in range(5))
    return rows


def merged_depths(record):
    merged = {}
    for name in SITES:
        for depth, walks in record["sites"][name]["depth"]:
            merged[depth] = merged.get(depth, 0) + walks
    return merged


def bucket_of(depth):
    """0, 1, 2-3, 4-7, ...: the bucket's index."""
    return depth.bit_length()


def bucket_label(index):
    if index <= 1:
        return str(index)
    low, high = 1 << (index - 1), (1 << index) - 1
    return f"{low:,}-{high:,}"


# ---- Rendering -----------------------------------------------------------------------------


def schedule_rows(records):
    rows = []
    for record in records:
        engine = record["engine"]
        messages = engine["messages"]
        resting = record["resting_after_message"]
        rows.append(f"| {schedule_name(record)} | {record['shadow_orders']:,} "
                    f"| {record['shadow_span_ns'] / 1e9:,.6g} | {record['shadow_life_ns'] / 1e9:,.6g} "
                    f"| {messages:,} | {engine['event_hash']} | {resting['sum'] / messages:,.1f} "
                    f"| {resting['max']:,} | {engine['fills']:,} | {engine['arrivals']:,} "
                    f"| {engine['reports']:,} | {engine['wakes']:,} |")
    return rows


def totals_rows(records):
    rows = []
    for record in records:
        name = schedule_name(record)
        for site, row in site_totals(record).items():
            label = SITE_NAMES.get(site, ALL)
            rows.append(f"| {name} | {label} | " + " | ".join(f"{value:,}" for value in row) + " |")
    return rows


def message_cells(messages, walks, levels, orders, depths):
    return (f"{walks / messages:.3f}", f"{levels / messages:.3f}", f"{orders / messages:.3f}",
            f"{(levels + orders) / messages:.3f}", f"{levels / walks:.2f}" if walks else "0.00",
            f"{depths / walks:.2f}" if walks else "0.00")


def steps_per_message(record):
    """A schedule's walk steps a message, level steps and order steps, unrounded."""
    _, _, levels, orders, _ = site_totals(record)[ALL]
    return (levels + orders) / record["engine"]["messages"]


def message_rows(records):
    rows = []
    for record in records:
        walks, _, levels, orders, depths = site_totals(record)[ALL]
        cells = message_cells(record["engine"]["messages"], walks, levels, orders, depths)
        rows.append(f"| {schedule_name(record)} | " + " | ".join(cells) + " |")
    return rows


def percentile_bucket(counts, percent):
    """The bucket holding the walk at this rank, nearest-rank, in whole numbers."""
    rank = max(1, -(-sum(counts) * percent // 100))
    seen = 0
    for index, count in enumerate(counts):
        seen += count
        if seen >= rank:
            return index
    return len(counts) - 1


def depth_rows(records):
    merged = [merged_depths(record) for record in records]
    deepest = max(max(depths) for depths in merged)
    buckets = bucket_of(deepest) + 1
    columns = []
    for depths in merged:
        counts = [0] * buckets
        for depth, walks in depths.items():
            counts[bucket_of(depth)] += walks
        columns.append(counts)
    rows = [f"| {bucket_label(index)} | " + " | ".join(f"{column[index]:,}" for column in columns)
            + " |" for index in range(buckets)]
    rows.append("| All walks | " + " | ".join(f"{sum(column):,}" for column in columns) + " |")
    for label, percent in PERCENTILES:
        rows.append(f"| {label} | " + " | ".join(
            bucket_label(percentile_bucket(column, percent)) for column in columns) + " |")
    rows.append("| Deepest | " + " | ".join(f"{max(depths):,}" for depths in merged) + " |")
    return rows


def summary_line(message_table):
    """The first schedule against the one with most level steps a message, from the message
    table's own cells."""
    first = message_table[0]
    last = max(message_table, key=lambda cells: float(cells[2]))
    return (f"{first[0]}: {first[1]} walks, {first[2]} level steps and {first[3]} order steps "
            f"a message, {first[6]} levels on the side at a walk on average; {last[0]}: "
            f"{last[1]}, {last[2]} and {last[3]}, {last[6]} levels")


def count_sections(records):
    """The tables a counts file and a ladder file share."""
    lines = ["## Schedules", "",
             "Each schedule places its orders as T-BT-3's does, at evenly spaced times over the "
             "span from the open, cycling over the symbols and sides, each at the view book's best "
             "on its side then, every latency zero; each order is cancelled when its life ends, "
             "and any still resting at the close expires. Resting orders are read after each "
             "message of the extract.", "",
             "| Schedule | Orders | Span s | Life s | Messages | Event hash | Resting orders "
             "after a message, mean | Max | Fills | Arrivals | Reports | Wakes |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    lines += schedule_rows(records)
    lines += ["", "## A message's walks", "",
              "Over the extract's messages. A level step is one simulated level whose price a "
              "walk read, the one it stopped at included; an order step is one simulated order "
              "it visited on a level; walk steps are both. Levels at a walk is the side's depth "
              "when the walk began, the levels it could have read.", "",
              "| Schedule | Walks | Level steps | Order steps | Walk steps | Level steps a walk "
              "| Levels at a walk |", "|---|---|---|---|---|---|---|"]
    lines += message_rows(records)
    lines += ["", "## Totals by site", "",
              "Levels summed is the side's depth at each walk, summed over the walks.", "",
              "| Schedule | Site | Walks | Of a side with a level | Level steps | Order steps "
              "| Levels summed |", "|---|---|---|---|---|---|---|"]
    lines += totals_rows(records)
    lines += ["", "## Side depth at each walk", "",
              "Walks of every site by the levels on their side when they began, in buckets; the "
              "percentile rows name the bucket.", "",
              "| Levels on the side | " + " | ".join(schedule_name(record) for record in records)
              + " |", "|---|" + "---|" * len(records)]
    lines += depth_rows(records)
    return lines


def table(text, title):
    """The body rows of the tables under a heading: each table's head, the row above its
    rule, is left out."""
    rows = []
    for line in (check_results.section(text, title) or "").splitlines():
        match = check_results.CELLS.match(line.strip())
        if match is None:
            continue
        cells = [cell.strip() for cell in match.group(1).split("|")]
        if all(re.fullmatch(r"-*", cell) for cell in cells):
            rows.pop()
            continue
        rows.append(cells)
    return rows


def render_counts(records, command):
    for field in SAME_FIELDS:
        values = {record["header"][field] for record in records}
        if len(values) != 1:
            raise Refused(f"the records differ in {field}: {sorted(values)}")
    if len({record["symbols"] for record in records}) != 1:
        raise Refused(f"the records ran different extracts: "
                      f"{sorted({record['symbols'] for record in records})}")
    first = records[0]
    header = first["header"]
    lines = [f"# {COUNT_TITLE}, on {header['host']}, {header['dataset'].split('.')[0]}", "",
             "Generated by `scripts/walk_report.py` from bench_walks records that are not "
             "committed. Do not edit by hand.", "",
             "The engine keeps each side's simulated orders as a list of levels from the best "
             "price, and six places walk it from the best: an execution, a cross, a depletion, a "
             "late add, a hidden print and a rest (`bench/walk_counts.hpp`). `bench_walks` runs "
             f"the extract of {first['symbols'].replace(' ', ', ')} through a copy of the engine "
             "made at build time with a count at each walk (`cmake/ItchWalkEngine.cmake`), beside "
             "the engine itself; each schedule's counted engine gave the engine's own stats and "
             "event hash. Nothing here is a time. The counts are the same on any host.", "",
             "## Header", ""]
    lines += [f"- host: {header['host']}; the counts are the same on any host, since nothing is "
              "timed"]
    lines += [f"- {name}: {header[name]}" for name in ("cpu", "kernel", "compiler", "flags",
                                                         "commit", "dataset", "dataset_sha256")]
    lines += [f"- symbols: {first['symbols']}",
              f"- schedules: {len(records)}",
              f"- peak_rss: {max(record['peak_rss_bytes'] for record in records) / 1024 ** 3:.2f} "
              "GiB, getrusage of bench_walks, largest schedule, the mapped day counted",
              "- summary: SUMMARY", ""]
    lines += count_sections(records)
    lines += ["", "## Commands", "", "```", command, "```", ""]
    text = "\n".join(lines)
    return text.replace("SUMMARY", summary_line(table(text, "A message's walks")), 1)


# ---- Checking ------------------------------------------------------------------------------


def number(cell):
    return int(cell.replace(",", ""))


def check_counts(text):
    """The count tables against each other. Yields (code, detail)."""
    messages = {cells[0]: number(cells[4]) for cells in table(text, "Schedules")}
    hashes = {cells[0]: cells[5] for cells in table(text, "Schedules")}
    if not messages:
        yield "table-missing", "no Schedules table"
        return
    totals = {}
    for cells in table(text, "Totals by site"):
        totals.setdefault(cells[0], {})[cells[1]] = [number(cell) for cell in cells[2:7]]
    message_table = table(text, "A message's walks")
    if [cells[0] for cells in message_table] != list(messages):
        yield "schedules-differ", "the message table's schedules are not the Schedules table's"
    for name, sites in totals.items():
        if name not in messages:
            yield "schedules-differ", f"totals for {name}, which is not a schedule"
            continue
        if list(sites) != [SITE_NAMES[site] for site in SITES] + [ALL]:
            yield "sites-missing", f"{name}: sites {list(sites)}"
            continue
        whole = [sum(row[i] for label, row in sites.items() if label != ALL) for i in range(5)]
        if whole != sites[ALL]:
            yield "total-not-sum", f"{name}: {sites[ALL]} against the sites' {whole}"
        if any(row[1] > row[0] for row in sites.values()):
            yield "total-not-sum", f"{name}: more walks of a side with a level than walks"
    for cells in message_table:
        name = cells[0]
        if name not in totals or ALL not in totals[name]:
            yield "sites-missing", f"{name}: no totals"
            continue
        walks, _, levels, orders, depths = totals[name][ALL]
        expected = message_cells(messages[name], walks, levels, orders, depths)
        if tuple(cells[1:]) != expected:
            yield "message-figure", f"{name}: {cells[1:]} against {list(expected)} from the totals"
    depth_table = table(text, "Side depth at each walk")
    names = list(messages)
    buckets = [cells for cells in depth_table if re.fullmatch(r"[0-9,]+(?:-[0-9,]+)?", cells[0])]
    labelled = {cells[0]: cells[1:] for cells in depth_table if cells not in buckets}
    if [cells[0] for cells in buckets] != [bucket_label(i) for i in range(len(buckets))]:
        yield "depth-buckets", f"buckets {[cells[0] for cells in buckets]}"
        return
    for column, name in enumerate(names):
        counts = [number(cells[column + 1]) for cells in buckets]
        walks = totals.get(name, {}).get(ALL, [None])[0]
        if number(labelled.get("All walks", [""] * len(names))[column] or "0") != sum(counts) or \
                walks != sum(counts):
            yield "depth-not-walks", f"{name}: the depth column sums to {sum(counts):,}, not its walks"
        for label, percent in PERCENTILES:
            want = bucket_label(percentile_bucket(counts, percent))
            if labelled.get(label, [""] * len(names))[column] != want:
                yield "depth-percentile", f"{name}: {label} is {want}"
        deepest = labelled.get("Deepest", [""] * len(names))[column]
        top = max((i for i, count in enumerate(counts) if count), default=0)
        if not deepest or bucket_of(number(deepest)) != top:
            yield "depth-deepest", f"{name}: deepest {deepest!r} is not in the top bucket"
    fields = check_results.parse_header(text)
    if text.startswith(f"# {COUNT_TITLE}") and fields.get("summary") != summary_line(message_table):
        yield "summary-stale", "the summary line is not the one the tables give"
    if not all(re.fullmatch(r"[0-9a-f]{16}", value) for value in hashes.values()):
        yield "hash-missing", "a schedule has no event hash"


def check_text(text):
    """Every violation in a counts or ladder file, as (code, detail) pairs."""
    title = text.splitlines()[0] if text else ""
    violations = list(check_counts(text))
    if title.startswith(f"# {LADDER_TITLE}"):
        violations += list(check_ladder(text))
    elif not title.startswith(f"# {COUNT_TITLE}"):
        violations.append(("title", repr(title)))
    for name, pattern in (("home path", check_results.HOME_PATH),
                          ("address", check_results.ADDRESS)):
        if pattern.search(text):
            violations.append(("private", name))
    return violations


# ---- The ladder ----------------------------------------------------------------------------


# The line's figures over ranges of y, x exact: each is a sum of weights times the ys, so its
# least and greatest come from taking each y at the end its weight's sign picks.

def least_squares(points):
    """Slope and intercept of y on x."""
    slope, intercept = line_weights([x for x, _ in points])
    return (sum(w * y for w, (_, y) in zip(slope, points)),
            sum(w * y for w, (_, y) in zip(intercept, points)))


def line_weights(xs):
    """The weights that give the least-squares slope and intercept from the ys."""
    mean_x = statistics.fmean(xs)
    spread = sum((x - mean_x) ** 2 for x in xs)
    slope = [(x - mean_x) / spread for x in xs]
    return slope, [1 / len(xs) - mean_x * weight for weight in slope]


def weighted_range(weights, ranges):
    low = sum(w * (lo if w >= 0 else hi) for w, (lo, hi) in zip(weights, ranges))
    high = sum(w * (hi if w >= 0 else lo) for w, (lo, hi) in zip(weights, ranges))
    return low, high


def fixed(places, suffix=""):
    return lambda value: f"{value:.{places}f}{suffix}"


def share_rows(names, steps, xs, medians, ranges, shown=None):
    """The line through every schedule's median, and the walk's share at each: the slope and
    intercept cells, and a row a schedule. `steps` and `medians` are the cells the file
    prints; `xs` the walk steps a message, exact; `ranges` each median as a range. `shown` is
    the file's own cells, {"slope", "intercept", "rows": {name: row}}, which check gives."""
    shown = shown or {}
    slope_w, intercept_w = line_weights(xs)
    slope = check_results.shown_within(shown.get("slope"), weighted_range(slope_w, ranges),
                                       fixed(3))
    intercept = check_results.shown_within(shown.get("intercept"),
                                           weighted_range(intercept_w, ranges), fixed(2))
    rows = []
    for index, name in enumerate(names):
        x = xs[index]
        mine = (shown.get("rows") or {}).get(name) or [None] * 6
        line = weighted_range([v + x * w for v, w in zip(intercept_w, slope_w)], ranges)
        walk = weighted_range([x * w for w in slope_w], ranges)
        low, high = ranges[index]
        shares = [100 * a / b for a in walk for b in (low, high)]
        rows.append((name, steps[index], medians[index],
                     check_results.shown_within(mine[3], line, fixed(2)),
                     check_results.shown_within(mine[4], walk, fixed(2)),
                     check_results.shown_within(mine[5], (min(shares), max(shares)),
                                                fixed(2, "%"))))
    return slope, intercept, rows


def round_rows(repetitions, xs, shown=None):
    """Each round's own line: (round, slope, intercept). `repetitions` is (round, schedule,
    time a message as a range) in the order run, `xs` each schedule's walk steps a message,
    exact, and `shown` the file's own rows by round, which check gives."""
    rounds = {}
    for name, schedule, times in repetitions:
        rounds.setdefault(name, []).append((xs[schedule], times))
    rows = []
    for name, points in rounds.items():
        mine = (shown or {}).get(name) or (name, None, None)
        if len({x for x, _ in points}) > 1:
            slope_w, intercept_w = line_weights([x for x, _ in points])
            ranges = [times for _, times in points]
            slope = weighted_range(slope_w, ranges)
            intercept = weighted_range(intercept_w, ranges)
        else:
            slope = intercept = (0.0, 0.0)
        rows.append((name, check_results.shown_within(mine[1], slope, fixed(3)),
                     check_results.shown_within(mine[2], intercept, fixed(2))))
    return rows


def check_ladder(text):
    """The ladder's timed tables against its repetitions, its counts and its line."""
    hashes = {cells[0]: cells[5] for cells in table(text, "Schedules")}
    steps = {cells[0]: cells[4] for cells in table(text, "A message's walks")}
    # Each schedule's walk steps a message, exact, from its totals and its messages.
    messages = {cells[0]: number(cells[4]) for cells in table(text, "Schedules")}
    xs = {cells[0]: (number(cells[4]) + number(cells[5])) / messages[cells[0]]
          for cells in table(text, "Totals by site") if cells[1] == ALL and cells[0] in messages}
    repetitions = {}
    for cells in table(text, "Repetitions"):
        if len(cells) != 6:
            yield "repetition-row", f"{cells}"
            continue
        repetitions.setdefault(cells[1], []).append(cells)
        if hashes.get(cells[1]) != cells[4]:
            yield "repetition-hash", f"{cells[1]} round {cells[0]}: {cells[4]}"
    summary = {cells[0]: cells[1:] for cells in table(text, "Summary")}
    if list(summary) != list(hashes):
        yield "schedules-differ", "the Summary's schedules are not the Schedules table's"
    times = {}
    for name, cells in summary.items():
        values = [check_results.printed_range(row[2], 2) for row in repetitions.get(name, [])]
        if len(values) < 3:
            yield "repetitions-few", f"{name}: {len(values)} repetitions"
            continue
        times[name] = values
        want = summary_cells(values, cells)
        if cells != want:
            yield "summary-figure", f"{name}: {cells} against {want} from the repetitions"
    share = table(text, "The walk's share")
    names = [cells[0] for cells in share]
    if names != list(hashes):
        yield "schedules-differ", "the share table's schedules are not the Schedules table's"
        return
    for cells in share:
        if cells[1] != steps.get(cells[0]) or cells[2] != summary.get(cells[0], [None])[0]:
            yield "share-input", f"{cells[0]}: steps {cells[1]} or median {cells[2]} not as printed"
    if any(name not in xs or name not in times for name in names):
        yield "share-input", "a schedule with no totals or too few repetitions"
        return
    fields = check_results.parse_header(text)
    shown = {"slope": fields.get("slope", "").split(" ")[0],
             "intercept": fields.get("intercept", "").split(" ")[0],
             "rows": {cells[0]: cells for cells in share}}
    slope, intercept, rows = share_rows(
        names, [cells[1] for cells in share], [xs[name] for name in names],
        [cells[2] for cells in share],
        [check_results.median_range(times[name]) for name in names], shown)
    if [tuple(cells) for cells in share] != rows:
        yield "share-figure", "the fit, the walk's time or the share is not what the figures give"
    known = [(cells[0], cells[1], check_results.printed_range(cells[2], 2))
             for cells in table(text, "Repetitions") if cells[1] in xs]
    printed = [tuple(cells) for cells in table(text, "Each round's line")]
    rounds = round_rows(known, xs, {cells[0]: cells for cells in printed})
    if printed != rounds:
        yield "round-line", "a round's line is not what its repetitions give"
    if slope_range(rounds) != fields.get("round_slopes"):
        yield "line-stale", f"round_slopes is {fields.get('round_slopes')!r}"
    if fields.get("slope") != f"{slope} ns a walk step" or \
            fields.get("intercept") != f"{intercept} ns a message":
        yield "line-stale", f"the line is {slope} ns a walk step and {intercept} ns"
    if fields.get("summary") != ladder_summary(slope, rows):
        yield "summary-stale", "the summary line is not the one the tables give"


def slope_range(rounds):
    slopes = [float(slope) for _, slope, _ in rounds]
    return f"{min(slopes):.3f} to {max(slopes):.3f} ns a walk step over {len(slopes)} rounds"


def ladder_summary(slope, rows):
    """The step's cost, and the walk's share of the first and the last schedule's time."""
    first, last = rows[0], rows[-1]
    return (f"{slope} ns a walk step over {len(rows)} schedules; the walk {first[5]} of "
            f"{first[0]}'s {first[2]} ns a message and {last[5]} of {last[0]}'s {last[2]} ns")


def summary_cells(ranges, cells=None):
    """Median, min, max and CV of the times a message, each a range, as the Summary prints
    them: each the file's own cell in `cells` if the ranges allow it."""
    cells = cells or [None] * 4
    out = [check_results.shown_within(cell, bounds, fixed(2)) for cell, bounds in zip(
        cells, (check_results.median_range(ranges), check_results.min_range(ranges),
                check_results.max_range(ranges)))]
    low, high = check_results.cv_range(ranges)
    out.append(check_results.shown_within(cells[3], (low * 100, high * 100), fixed(2, "%")))
    return out


# ---- Command line --------------------------------------------------------------------------


def count_command(record, name):
    """The bench_walks command that wrote a record, as run from the repository."""
    words = ["build/release/bench/bench_walks", "--input",
             f"data/{record['header']['dataset']}", "--out", f"<raw>/{name}", "--date",
             str(record["date"])]
    for key, flag in (("shadow_orders", "--shadow-orders"), ("shadow_span_ns", "--shadow-span-ns"),
                      ("shadow_life_ns", "--shadow-life-ns")):
        if record[key] != TBT3[key]:
            words += [flag, str(record[key])]
    return " ".join(words)


def load(paths):
    records = []
    for path in paths:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        try:
            validate_record(record)
        except Refused as refusal:
            raise Refused(f"{Path(path).name}: {refusal}") from None
        records.append(record)
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    counts = commands.add_parser("counts")
    counts.add_argument("--out", type=Path, required=True)
    counts.add_argument("records", nargs="+", type=Path)
    check = commands.add_parser("check")
    check.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)

    if args.command == "counts":
        try:
            records = load(args.records)
        except (Refused, OSError, ValueError, KeyError) as error:
            print(f"walk_report: refused: {error}", file=sys.stderr)
            return 2
        command = "\n".join(
            [count_command(record, path.name) for record, path in zip(records, args.records)]
            + ["python3 -B scripts/walk_report.py counts --out "
               f"results/{args.out.name} " + " ".join(f"<raw>/{path.name}"
                                                      for path in args.records)])
        try:
            text = render_counts(records, command)
        except Refused as error:
            print(f"walk_report: refused: {error}", file=sys.stderr)
            return 2
        violations = check_text(text)
        if violations:
            for code, detail in violations:
                print(f"walk_report: FAIL {code} {args.out.name}: {detail}", file=sys.stderr)
            return 2
        args.out.write_text(text, encoding="utf-8")
        print(f"walk_report: wrote {args.out}")
        return 0

    failed = 0
    for path in args.files:
        violations = check_text(path.read_text(encoding="utf-8"))
        for code, detail in violations:
            print(f"walk_report: FAIL {code} {path.name}: {detail}")
        failed += 1 if violations else 0
        if not violations:
            print(f"walk_report: ok {path.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
