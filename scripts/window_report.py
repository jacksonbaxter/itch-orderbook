#!/usr/bin/env python3
"""Writes carteret's window-width experiment, as counts over a day of this book, from
itch_window's output (docs/NOTES.md, "carteret's layout experiments on our book").

    window_report.py --counts WINDOW.out --time TIME.err --commit COMMIT --day DAY \\
        --prediction P-NNNN --out results/window_DAY.md [--run-log results/run_log.md]
    window_report.py --check FILE [--counts ... --time ... --commit ... --day ... --prediction ...]

itch_window counts, at every message that adds or names a live order, how far the price sits
behind its side's best in whole cents, and so whether a window of W ticks anchored on the best
would hold it, and how many live levels this book's search passes before it finds the place.
The counts are the same on any host: nothing is timed. The file says the host the tool ran on
all the same, with the run's peak memory from /usr/bin/time -l.

--check reads a published file and judges it again: each width's count is no larger than the
narrower one's, the sub-cent prices are outside every width, the depth buckets add up to the
searches, every percentage is its count over its total, the summary is what the tables give,
nothing private is in it. Given the raw output too, it rebuilds the file and compares.

Each violation prints as `window_report: FAIL <code>: <detail>`.

Exit codes: 0 written, or --check found nothing, 1 --check found something, 2 bad usage or
unusable input, a walk that was not clean among them.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402
import stamp_load  # noqa: E402

WIDTHS = (64, 128, 256, 512, 1024, 2048, 4096)
DEPTHS = ("0", "1", "2-3", "4-7", "8-15", "16-63", "64-255", "256+")
KINDS = {"adds": "adds: A, F and a U's new order", "named": "named: E, C, X, D and a U's old order"}
SEARCHES = {"add": "at an add", "close": "at a message that removes its level's last order"}
HOST = "macOS (M4 Max)"
CARTERET = {256: 4.90, 2048: 1.03}  # carteret's overflow on 12302019, % of adds (docs/NOTES.md)
COMMIT_FIELD = re.compile(r"^[0-9a-f]{12}( \(.*\))?$")

METHOD = """\
`itch_window` replays the day through `FastBook<>`, the default arm, and before the book
applies each message that adds an order (A, F, the new order of a U) or names a live one (E,
C, X, D, the old order of a U), it counts two things.

- The window. carteret keeps each side's levels in a flat array of W one-cent ticks that
  slides with the side's best, and a level outside it in a `std::map`, the overflow. For each
  price the tool takes its distance behind the side's best in whole cents, rounded up, and
  counts it outside a window of W if that distance is W or more. A price at or better than the
  best, or on an empty side, is inside: the window slides to it. A price with a fraction of a
  cent is outside at every width, since no window of cents can index it. carteret places the
  best a margin inside its window's edge and moves the window with hysteresis; neither is
  modelled. So this is the overflow of a window that follows the best exactly, and it is
  neither a floor nor a ceiling on carteret's at the same width: a margin leaves fewer ticks
  behind the best, so more prices overflow, and hysteresis leaves the window where it was when
  the best moves, so it can hold prices behind the old best that this one does not.
- This book's search. The book keeps no window: a side is one sorted array of its live
  levels, best at the end, searched from there. For each search `FlatLevels` makes, at every
  add and at every message that removes its level's last order, the tool counts how many live
  levels on the side are better than the price. The search reads one key for each such level
  and one more, so this is what a window of W entries, a search that walks W entries and no
  further, would leave to the rest of the side. `paged-flat-scan16` and `paged-flat-bisect`
  are that search with W = 16 and W = 0, and their throughput is in
  `results/layout_debian_12302019.md`."""

CAVEATS = """\
- A count, not a time: how often a window would overflow says nothing alone about what the
  overflow costs. carteret's +15.2% and -7.2% are its own book's, on its own host.
- The window is idealized: it follows the best exactly, with no margin and no hysteresis, so
  its figures and carteret's differ in definition as well as in book, and neither bounds the
  other.
- A price is placed against the side's best before the message applies. A U's new order is
  placed against the book with its old order still in it; its search is counted as
  `FlatLevels` makes it, after the old order has gone.
- One day, 12302019. A day with wider spreads or more sub-penny prices moves every figure."""


class ReportError(ValueError):
    """The tool's output cannot make a report."""


def parse_counts(text):
    """itch_window's `key value` lines, as a dict. ReportError if any is missing or odd."""
    counts = {"outside": {}, "better": {}}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "walk":
            counts["walk"] = " ".join(parts[1:])
        elif parts[0] == "outside" and len(parts) == 4:
            counts["outside"][(parts[1], int(parts[2]))] = int(parts[3])
        elif parts[0] == "better" and len(parts) == 4:
            counts["better"][(parts[1], parts[2])] = int(parts[3])
        elif len(parts) == 2 and parts[1].isdigit():
            counts[parts[0]] = int(parts[1])
        else:
            raise ReportError(f"a line itch_window does not write: {line!r}")
    wanted = ["messages", "refused", "walk"]
    for kind in KINDS:
        wanted += [kind, f"{kind}_opens_side", f"{kind}_improves", f"{kind}_subcent"]
        wanted += [("outside", kind, width) for width in WIDTHS]
    for kind in SEARCHES:
        wanted += [f"{kind}_searches"] + [("better", kind, depth) for depth in DEPTHS]
    for key in wanted:
        if isinstance(key, tuple):
            have = counts[key[0]].get(key[1:])
        else:
            have = counts.get(key)
        if have is None:
            raise ReportError(f"itch_window's output has no {key}")
    if counts["walk"] != "clean" or counts["refused"] != 0:
        raise ReportError(f"the walk was not clean: {counts['walk']}, {counts['refused']} refused")
    return counts


def pct(count, total):
    return 100 * count / total if total else 0.0


def outside_table(counts):
    rows = ["| Window, whole-cent ticks | " + " | ".join(f"{kind.capitalize()}, outside"
                                                       for kind in KINDS)
            + " | " + " | ".join(f"{kind.capitalize()}, share" for kind in KINDS) + " |",
            "|---|" + "---|" * (2 * len(KINDS))]
    for width in WIDTHS:
        cells = [f"{counts['outside'][(kind, width)]:,}" for kind in KINDS]
        cells += [f"{pct(counts['outside'][(kind, width)], counts[kind]):.3f}%" for kind in KINDS]
        rows.append(f"| {width:,} | " + " | ".join(cells) + " |")
    return rows


def price_table(counts):
    rows = ["| Prices placed | Count | Share |", "|---|---|---|"]
    for kind, label in KINDS.items():
        rows.append(f"| {label} | {counts[kind]:,} | 100.000% |")
        for key, said in (("opens_side", "on an empty side"),
                          ("improves", "better than the side's best"),
                          ("subcent", "with a fraction of a cent")):
            value = counts[f"{kind}_{key}"]
            rows.append(f"| {kind}, {said} | {value:,} | {pct(value, counts[kind]):.3f}% |")
    return rows


def depth_table(counts):
    rows = ["| Live levels better than the price | "
            + " | ".join(f"Searches {said}" for said in SEARCHES.values())
            + " | " + " | ".join(f"Share, {kind}" for kind in SEARCHES) + " |",
            "|---|" + "---|" * (2 * len(SEARCHES))]
    for depth in DEPTHS:
        cells = [f"{counts['better'][(kind, depth)]:,}" for kind in SEARCHES]
        cells += [f"{pct(counts['better'][(kind, depth)], counts[f'{kind}_searches']):.3f}%"
                  for kind in SEARCHES]
        rows.append(f"| {depth} | " + " | ".join(cells) + " |")
    rows.append("| all | " + " | ".join(f"{counts[f'{kind}_searches']:,}" for kind in SEARCHES)
                + " | " + " | ".join("100.000%" for _ in SEARCHES) + " |")
    return rows


def deep_share(counts, kind, floor_depth):
    at = DEPTHS.index(floor_depth)
    deep = sum(counts["better"][(kind, depth)] for depth in DEPTHS[at:])
    return pct(deep, counts[f"{kind}_searches"])


def shallow_share(counts, kind, last_depth):
    """The share of searches that pass at most the levels of the bucket `last_depth`."""
    upto = DEPTHS.index(last_depth) + 1
    shallow = sum(counts["better"][(kind, depth)] for depth in DEPTHS[:upto])
    return pct(shallow, counts[f"{kind}_searches"])


def summary_of(counts):
    adds = counts["adds"]
    return (f"adds outside a window of 256 ticks {pct(counts['outside'][('adds', 256)], adds):.3f}%"
            f", of 2,048 {pct(counts['outside'][('adds', 2048)], adds):.3f}%"
            f" (carteret 4.90% and 1.03%); sub-cent {pct(counts['adds_subcent'], adds):.3f}%"
            f"; add searches past 1 or fewer better levels "
            f"{shallow_share(counts, 'add', '1'):.2f}%, "
            f"past 16 or more {deep_share(counts, 'add', '16-63'):.2f}%")


def finding(counts):
    adds = counts["adds"]
    lines = [f"- Of {adds:,} prices added, "
             f"{pct(counts['outside'][('adds', 256)], adds):.3f}% fall outside a window of 256 "
             f"ticks that follows the best, and {pct(counts['outside'][('adds', 2048)], adds):.3f}% "
             f"outside 2,048; carteret measured {CARTERET[256]:.2f}% and {CARTERET[2048]:.2f}% "
             "for its own window on this day, with its margin and hysteresis.",
             f"- {pct(counts['adds_subcent'], adds):.3f}% of the prices added have a fraction "
             "of a cent, outside a window of cents at every width.",
             f"- {shallow_share(counts, 'add', '1'):.2f}% of this book's add searches pass 1 or "
             f"fewer better levels, {shallow_share(counts, 'add', '2-3'):.2f}% pass 3 or fewer, "
             f"{deep_share(counts, 'add', '16-63'):.2f}% pass 16 or more and "
             f"{deep_share(counts, 'add', '256+'):.2f}% pass 256 or more. At a message that "
             f"removes a level's last order, {shallow_share(counts, 'close', '1'):.2f}% pass 1 "
             f"or fewer and {deep_share(counts, 'close', '16-63'):.2f}% pass 16 or more."]
    return lines


def render(window_text, time_text, commit, day, prediction):
    counts = parse_counts(window_text)
    match = stamp_load.MAX_RSS.search(time_text)
    if match is None:
        raise ReportError("no /usr/bin/time -l maximum resident set size in --time")
    if not COMMIT_FIELD.match(commit):
        raise ReportError(f"a commit is twelve hex digits: {commit!r}")
    out = [f"# carteret's window width against overflow, on this book, {day}", "",
           "Generated by `scripts/window_report.py` from `itch_window`'s output, which is not "
           "committed. Do not edit by hand.", "", "## Header", "",
           f"- host: {HOST}; the counts are the same on any host, since nothing is timed",
           f"- commit: {commit}", f"- dataset: {day}.NASDAQ_ITCH50",
           "- arm: paged-flat, `FastBook<>`", f"- prediction: {prediction}",
           f"- run_id: {prediction}-window-{day}",
           f"- messages: {counts['messages']:,}",
           f"- peak_rss: {stamp_load.gib(match.group(1))}, /usr/bin/time -l, the mapped day "
           "included", f"- summary: {summary_of(counts)}", "",
           "## How the counts are taken", "", METHOD, "",
           "## The window", "",
           "Each width's prices outside the window, a count and a share of the prices placed.", ""]
    out += outside_table(counts)
    out += ["", "## The prices placed", ""]
    out += price_table(counts)
    out += ["", "## This book's search", "",
            "How many live levels each search passes before its place, by the search's kind.", ""]
    out += depth_table(counts)
    out += ["", "## Finding", ""] + finding(counts)
    out += ["", "## What this does not show", "", CAVEATS, "", "## Command", "", "```",
            f"heavy.sh /usr/bin/time -l build/release/tools/itch_window <raw>/{day}.NASDAQ_ITCH50",
            "```", ""]
    return "\n".join(out)


def table_cells(text, title):
    return check_results.table_rows(text, title)


def number(cell):
    return int(cell.replace(",", ""))


def check_published(text):
    """What is wrong with a published file, read alone, as (code, detail) pairs."""
    fields = check_results.parse_header(text)
    if not fields:
        return [("header-missing", "no Header section")]
    found = list(check_results.check_private(text, fields))
    if not fields.get("host", "").startswith(HOST):
        found.append(("host-label", repr(fields.get("host"))))
    if not COMMIT_FIELD.match(fields.get("commit", "")):
        found.append(("commit-missing", repr(fields.get("commit"))))
    counts = {"outside": {}, "better": {}}
    rows = table_cells(text, "The window")[1:]
    if [number(row[0]) for row in rows] != list(WIDTHS):
        return found + [("table-missing", "the window table is not one row a width")]
    for row in rows:
        for at, kind in enumerate(KINDS):
            counts["outside"][(kind, number(row[0]))] = number(row[1 + at])
    for row in table_cells(text, "The prices placed")[1:]:
        for kind, label in KINDS.items():
            if row[0] == label:
                counts[kind] = number(row[1])
            for key, said in (("opens_side", "on an empty side"),
                              ("improves", "better than the side's best"),
                              ("subcent", "with a fraction of a cent")):
                if row[0] == f"{kind}, {said}":
                    counts[f"{kind}_{key}"] = number(row[1])
    depth_rows = table_cells(text, "This book's search")[1:]
    for row in depth_rows:
        for at, kind in enumerate(SEARCHES):
            if row[0] == "all":
                counts[f"{kind}_searches"] = number(row[1 + at])
            else:
                counts["better"][(kind, row[0])] = number(row[1 + at])
    try:
        rebuilt_rows = outside_table(counts) + price_table(counts) + depth_table(counts)
    except (KeyError, ZeroDivisionError) as error:
        return found + [("table-missing", f"a table lacks {error}")]
    for line in rebuilt_rows:
        if line not in text:
            found.append(("share-differs", f"a row is not its counts over its total: {line}"))
    for kind in KINDS:
        widths = [counts["outside"][(kind, width)] for width in WIDTHS]
        if any(wider > narrower for narrower, wider in zip(widths, widths[1:])):
            found.append(("not-monotone", f"{kind}: a wider window holds fewer prices"))
        if widths[-1] < counts[f"{kind}_subcent"]:
            found.append(("subcent-inside", f"{kind}: a sub-cent price inside the widest window"))
    for kind in SEARCHES:
        if sum(counts["better"][(kind, depth)] for depth in DEPTHS) != counts[f"{kind}_searches"]:
            found.append(("depths-not-tiled", f"{kind}: the buckets do not add up to the searches"))
    if fields.get("summary") != summary_of(counts):
        found.append(("summary-differs", f"{fields.get('summary')!r} against {summary_of(counts)!r}"))
    if "\n".join(finding(counts)) not in text:
        found.append(("finding-differs", "the finding is not what the tables give"))
    return found


def report_violations(violations):
    for code, detail in violations:
        print(f"window_report: FAIL {code}: {detail}")
    return 1 if violations else 0


def build(args):
    return render(args.counts.read_text(encoding="utf-8"),
                  args.time.read_text(encoding="utf-8", errors="replace"), args.commit,
                  args.day, args.prediction)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--counts", type=Path)
    parser.add_argument("--time", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--day", default="12302019")
    parser.add_argument("--prediction")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--run-log", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args(argv)
    raw = args.counts is not None
    if raw and (args.time is None or args.commit is None or args.prediction is None):
        parser.error("the raw output needs --time, --commit and --prediction")
    if args.check is None and (args.out is None or not raw):
        parser.error("wanted --check FILE, or the raw output and --out")
    try:
        if args.check:
            text = args.check.read_text(encoding="utf-8")
            violations = check_published(text)
            if raw and build(args) != text:
                violations.append(("table-differs", "the file is not what the raw output gives"))
            if violations:
                return report_violations(violations)
            print(f"window_report: ok {args.check.name}")
            return 0
        text = build(args)
        violations = check_published(text)
        if violations:
            return report_violations(violations)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        if args.run_log:
            fields = check_results.parse_header(text)
            with open(args.run_log, "a", encoding="utf-8") as log:
                log.write(f"| {args.prediction}-window-{args.day} | {args.prediction} "
                          f"| {fields['commit']} | Darwin | published: {fields['summary']} |\n")
        print(f"window_report: wrote {args.out.name}")
        return 0
    except (OSError, ValueError, KeyError, IndexError) as error:
        print(f"window_report: unusable input: {error!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
