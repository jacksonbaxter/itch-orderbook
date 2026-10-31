#!/usr/bin/env python3
"""Checks a real-day oracle log against the data manifest and writes the results file.

    oracle_report.py --day 12302019 --log RAW [--alloc-log RAW] [--out results/x.md]
                     [--manifest scripts/data_manifest.json]
    oracle_report.py --print-flags build/release/compile_commands.json [SOURCE]

The tool's own verdicts are not enough here. A tool that diffed half the day could still
pass, so this compares its counters with the census pinned in the manifest. The third-
party figures in COMPARISON are claims to set ours beside, never expected values.

Exit codes: 0 every hard check passed and T-INV-2 passed. 1 a hard check failed. 2 the
inputs could not be used. 3 only T-INV-2 failed: it is strict, so it is reported as
failing, and the code keeps it apart from a broken book.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOK_TYPES = "AFECXDU"
ONE_LEVEL_TYPES = "AFECXD"
IRONBOOK_TYPES = "AFU"

# Published on 12302019. Claims to compare with, not expected values. carteret's checks
# run after book messages, and its include/carteret/reference_book.hpp at commit 3df0e87
# observes a replace twice: once in add_retained() and again in on(OrderReplace). So a
# crossed U counts twice in its totals, and those rows use that counting.
COMPARISON = [
    ("book messages, crossed", "carteret", 8580, "crossed"),
    ("book messages, locked", "carteret", 70, "locked"),
    ("not in state T, crossed", "carteret", 7945, "not_in_t_crossed"),
    ("not in state T, locked", "carteret", 62, "not_in_t_locked"),
    ("reopening window, crossed", "carteret", 635, "window_crossed"),
    ("reopening window, locked", "carteret", 8, "window_locked"),
    ("inside 09:30 to 16:00, crossed", "carteret", 8372, "clock_crossed"),
    ("inside 09:30 to 16:00, locked", "carteret", 70, "clock_locked"),
    ("crossed symbols", "carteret", 13, "crossed_symbols"),
    ("locked symbols", "carteret", 6, "locked_symbols"),
    ("deepest crossing, price units", "carteret", 140000, "deepest"),
    ("reopening windows", "carteret", 33, "windows"),
    ("market open and T, checks", "Ironbook", 136896897, "ironbook_checks"),
    ("market open and T, crossed", "Ironbook", 10, "ironbook_crossed"),
    ("market open and T, locked", "Ironbook", 0, "ironbook_locked"),
]

PAIR = re.compile(r"crossed=(\d+) locked=(\d+)")
TALLY = re.compile(
    r"t_inv2_tally type=(.) scope=(\w+) checks=(\d+) two_sided=(\d+) crossed=(\d+) locked=(\d+)")
CAUSE = re.compile(r"t_inv2_cause type=(.) cause=(\w+) crossed=(\d+) locked=(\d+)")
SYMBOL = re.compile(
    r"t_inv2_symbol (\S+) locate=(\d+) crossed=(\d+) locked=(\d+) first=(\S+) last=(\S+) "
    r"deepest=(\d+) states=(\S*)")


class ReportError(Exception):
    """The inputs cannot be used."""


def parse_log(text):
    """Pulls the tool's key-value lines and the time -l figures out of a raw log."""
    log = {"values": {}, "types": {}, "tallies": {}, "causes": {}, "symbols": [], "states": {},
           "pairs": {}, "verdicts": {}, "divergences": [], "time": {}, "run": {}}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("run_"):
            key, _, value = stripped.partition(" ")
            log["run"][key] = value
        elif stripped.startswith("type "):
            _, kind, count = stripped.split()
            log["types"][kind] = int(count)
        elif stripped.startswith("t_inv2_tally "):
            match = TALLY.fullmatch(stripped)
            if match is None:
                raise ReportError(f"bad tally line: {stripped}")
            log["tallies"][(match[1], match[2])] = tuple(int(match[i]) for i in range(3, 7))
        elif stripped.startswith("t_inv2_cause "):
            match = CAUSE.fullmatch(stripped)
            if match is None:
                raise ReportError(f"bad cause line: {stripped}")
            log["causes"][(match[1], match[2])] = (int(match[3]), int(match[4]))
        elif stripped.startswith("t_inv2_symbol "):
            match = SYMBOL.fullmatch(stripped)
            if match is None:
                raise ReportError(f"bad symbol line: {stripped}")
            log["symbols"].append(match.groups())
        elif stripped.startswith("t_inv2_by_state "):
            match = PAIR.search(stripped)
            log["states"][stripped.split()[1]] = (int(match[1]), int(match[2]))
        elif stripped.startswith("verdict "):
            parts = stripped.split()
            log["verdicts"][parts[1]] = parts[2]
        elif stripped.startswith("DIVERGENCE "):
            log["divergences"].append(stripped)
        elif stripped.endswith("maximum resident set size"):
            log["time"]["max_rss"] = int(stripped.split()[0])
        elif stripped.endswith("peak memory footprint"):
            log["time"]["peak_footprint"] = int(stripped.split()[0])
        elif re.fullmatch(r"[\d.]+ real\s+[\d.]+ user\s+[\d.]+ sys", stripped):
            fields = stripped.split()
            log["time"].update(real=float(fields[0]), user=float(fields[2]), sys=float(fields[4]))
        else:
            match = PAIR.search(stripped)
            key, _, value = stripped.partition(" ")
            if match is not None and key.startswith("t_inv2_"):
                log["pairs"][key] = (int(match[1]), int(match[2]))
            elif value and " " not in value:
                log["values"][key] = value
    return log


def number(log, key):
    if key not in log["values"]:
        raise ReportError(f"the log has no {key}")
    return int(log["values"][key])


def hard_failures(log, entry):
    """Every reason the run does not meet T-ORACLE-1 or T-INV-1 on this day."""
    failures = []
    messages = entry["messages"]
    counts = entry["per_type_counts"]

    def require(condition, text):
        if not condition:
            failures.append(text)

    for key in ("frames", "messages_diffed", "status_checks", "write_set_checks"):
        require(number(log, key) == messages, f"{key} {number(log, key)}, manifest {messages}")
    require(number(log, "bbo_checks") == 2 * messages, "bbo_checks is not two per message")
    require(log["types"] == counts, "per-type counts differ from the manifest")
    single = sum(counts.get(kind, 0) for kind in ONE_LEVEL_TYPES)
    replaces = counts.get("U", 0)
    touched = number(log, "touched_checks") + number(log, "empty_touched_checks")
    require(single + replaces <= touched <= single + 2 * replaces,
            f"{touched} touched levels, outside {single + replaces} to {single + 2 * replaces}")
    require(number(log, "write_set_violations") == 0, "write-set violations")
    every = number(log, "oracle_hash_every")
    if every:
        require(number(log, "periodic_hash_checks") == messages // every,
                "periodic hash count is wrong for the interval")
    require(number(log, "final_hash_checks") == 1, "no final hash")
    require(number(log, "malformed_frames") == 0, "malformed frames")
    require(log["values"].get("end") == "end_of_input", "the stream did not end cleanly")
    require(number(log, "divergences") == 0 and not log["divergences"], "divergences")
    require(number(log, "t_inv1_violations") == 0, "T-INV-1 violations")
    require(log["verdicts"].get("T-ORACLE-1") == "PASS", "T-ORACLE-1 verdict is not PASS")
    require(log["verdicts"].get("T-INV-1") == "PASS", "T-INV-1 verdict is not PASS")
    return failures


def sum_tallies(log, types, scope):
    rows = [value for (kind, tally_scope), value in log["tallies"].items()
            if tally_scope == scope and kind in types]
    return tuple(sum(row[i] for row in rows) for i in range(4))


def cause_totals(log, cause):
    """Crossed and locked after book messages, for one cause, counting a U twice."""
    crossed = locked = 0
    for (kind, tally_cause), (cross, lock) in log["causes"].items():
        if tally_cause == cause and kind in BOOK_TYPES:
            weight = 2 if kind == "U" else 1
            crossed += weight * cross
            locked += weight * lock
    return crossed, locked


def measured_figures(log):
    ironbook = sum_tallies(log, IRONBOOK_TYPES, "open_t")
    not_t = cause_totals(log, "not_t")
    window = cause_totals(log, "window")
    continuous = cause_totals(log, "continuous")
    clock = cause_totals(log, "clock")
    return {
        "crossed": not_t[0] + window[0] + continuous[0],
        "locked": not_t[1] + window[1] + continuous[1],
        "not_in_t_crossed": not_t[0],
        "not_in_t_locked": not_t[1],
        "window_crossed": window[0],
        "window_locked": window[1],
        "clock_crossed": clock[0],
        "clock_locked": clock[1],
        "crossed_symbols": number(log, "t_inv2_crossed_symbols"),
        "locked_symbols": number(log, "t_inv2_locked_symbols"),
        "deepest": number(log, "t_inv2_deepest_crossing"),
        "windows": number(log, "t_inv2_reopening_windows"),
        "ironbook_checks": ironbook[0],
        "ironbook_crossed": ironbook[2],
        "ironbook_locked": ironbook[3],
    }


def comparison_rows(log):
    """Third-party figures beside ours."""
    measured = measured_figures(log)
    return [(label, source, claim, measured[key]) for label, source, claim, key in COMPARISON]


def format_table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines


def count(value):
    return f"{value:,}"


def render(log, entry, day, alloc):
    run = log["run"]
    values = log["values"]
    time = log["time"]
    gib = 1024.0 ** 3
    lines = [
        f"# Oracle replay of {day}",
        "",
        "Generated by `scripts/oracle_report.py` from a raw log, which is not committed. Do "
        "not edit by hand.",
        "",
        f"- Commit: {run.get('run_commit', 'unavailable')}",
        f"- Compiler: {run.get('run_compiler', 'unavailable')}",
        f"- Flags: `{run.get('run_flags', 'unavailable')}`",
        f"- Host: {run.get('run_host', 'unavailable')}. Mac numbers stay labelled macOS.",
        f"- Dataset: `{entry['name']}`, SHA-256 of the unpacked file `{entry['unpacked_sha256']}`",
        f"- Command: `{run.get('run_command', 'unavailable')}`",
        f"- Elapsed: {values.get('elapsed_seconds', '?')} s in the replay, "
        f"{time.get('real', 0.0):.1f} s real, {time.get('user', 0.0):.1f} s user",
        f"- Maximum RSS: {time.get('max_rss', 0) / gib:.2f} GiB, which includes the mapped file",
        f"- Peak memory footprint: {time.get('peak_footprint', 0) / gib:.2f} GiB",
        "",
        "## T-ORACLE-1: the reference book agrees after every message",
        "",
        f"Verdict: **{log['verdicts'].get('T-ORACLE-1', 'missing')}**",
        "",
    ]
    touched = number(log, "touched_checks")
    lines += format_table(("Check", "Count"), [
        ("Messages in the file (manifest)", count(entry["messages"])),
        ("Messages diffed", count(number(log, "messages_diffed"))),
        ("Status comparisons", count(number(log, "status_checks"))),
        ("Touched levels compared, FIFO, aggregate and count", count(touched)),
        ("Touched levels both books agree are gone", count(number(log, "empty_touched_checks"))),
        ("Best bid and offer comparisons", count(number(log, "bbo_checks"))),
        ("Messages with the write set checked", count(number(log, "write_set_checks"))),
        ("Choke-point writes, all inside the touched set", count(number(log, "write_set_writes"))),
        ("Write-set violations", count(number(log, "write_set_violations"))),
        (f"Whole-book hashes, every {count(number(log, 'oracle_hash_every'))} messages",
         count(number(log, "periodic_hash_checks"))),
        ("End-of-day hashes", count(number(log, "final_hash_checks"))),
        ("Default-instantiation hashes", count(number(log, "production_hash_checks"))),
        ("Divergences", count(number(log, "divergences"))),
        ("Peak live orders", count(number(log, "peak_live_orders"))),
        ("Peak live levels", count(number(log, "peak_live_levels"))),
        ("Live orders at the end of the day", count(number(log, "live_orders_end"))),
    ])
    lines += [
        "",
        "## T-INV-1: every execute, cancel, delete and replace names a live order",
        "",
        f"Verdict: **{log['verdicts'].get('T-INV-1', 'missing')}**, "
        f"{count(number(log, 't_inv1_violations'))} violations.",
        "",
        "## T-INV-2: the book is never crossed",
        "",
        f"Verdict, strict: **{log['verdicts'].get('T-INV-2', 'missing')}**. The strict result "
        "stands as reported. No scoped version is a pass condition.",
        "",
        "An observation is taken after every message, on that message's locate, and needs both "
        "sides present. A locked book is counted apart. Trading state comes from `H`, market "
        "hours from `S`.",
        "",
    ]
    pairs = log["pairs"]
    every = sum_tallies(log, [chr(i) for i in range(256)], "all")
    book = sum_tallies(log, BOOK_TYPES, "all")
    open_t = sum_tallies(log, [chr(i) for i in range(256)], "open_t")
    open_book = sum_tallies(log, BOOK_TYPES, "open_t")
    ironbook = sum_tallies(log, IRONBOOK_TYPES, "open_t")
    lines += format_table(("Population", "Checks", "Two-sided", "Crossed", "Locked"), [
        (label, count(row[0]), count(row[1]), count(row[2]), count(row[3])) for label, row in (
            ("All observations, every message", every),
            ("All observations, after A F E C X D U", book),
            ("Market open and T, every message", open_t),
            ("Market open and T, after A F E C X D U", open_book),
            ("Market open and T, after A F U, as Ironbook checks", ironbook),
        )])
    lines += ["", "### Every crossed and locked observation, by cause", ""]
    labels = (
        ("Symbol not in state T", "t_inv2_not_in_t"),
        ("of which no H seen yet", "t_inv2_no_action_yet"),
        ("State T, reopening window open", "t_inv2_in_reopening_window"),
        ("State T, no window open", "t_inv2_in_t_outside_window"),
        ("State T, within 50 ms of a resumption", "t_inv2_in_t_within_50ms_of_resumption"),
        ("State T, later than 50 ms after one", "t_inv2_in_t_later_than_50ms"),
        ("State T, symbol never resumed", "t_inv2_in_t_never_resumed"),
        ("Market open", "t_inv2_market_open"),
        ("Market open and T", "t_inv2_market_open_and_t"),
        ("Clock inside 09:30 to 16:00", "t_inv2_clock_0930_1600"),
    )
    lines += format_table(("Cause", "Crossed", "Locked"),
                          [(label, count(pairs[key][0]), count(pairs[key][1]))
                           for label, key in labels])
    lines += ["", "### By trading state", ""]
    lines += format_table(("State", "Crossed", "Locked"),
                          [(f"`{state}`", count(pair[0]), count(pair[1]))
                           for state, pair in sorted(log["states"].items())])
    lines += ["", "### By symbol", ""]
    lines += format_table(
        ("Symbol", "Crossed", "Locked", "First", "Last", "Deepest, price units",
         "State:crossed/locked"),
        [(symbol, count(int(crossed)), count(int(locked)), first, last, count(int(deepest)),
          f"`{states}`")
         for symbol, _, crossed, locked, first, last, deepest, states in log["symbols"]])
    lines += [
        "",
        "### Against published figures",
        "",
        "Third-party claims, set beside ours and not used as expected values. carteret "
        "checks after book messages only, and its source observes a replace twice, so its "
        f"rows here count a crossed `U` twice. We count {count(book[2])} crossed once per "
        "message, and the Ironbook rows use its own check: after `A`, `F` and `U`, with "
        "the market open and the symbol in state `T`.",
        "",
    ]
    lines += format_table(
        ("Figure", "Source", "Published", "Ours", "Difference"),
        [(label, source, count(claim), count(ours), f"{ours - claim:+,}")
         for label, source, claim, ours in comparison_rows(log)])
    if alloc:
        lines += [
            "",
            "## T-ALLOC-1: no allocation on the hot path",
            "",
            f"Verdict: **{alloc['verdicts'].get('T-ALLOC-1', 'missing')}**",
            "",
        ]
        lines += format_table(("Book", "Messages", "Allocations after warm-up"), [
            ("Fast book", count(number(alloc, "fast_messages")),
             count(number(alloc, "fast_allocations"))),
            ("Control book, the reference", count(number(alloc, "control_messages")),
             count(number(alloc, "control_allocations"))),
        ])
    return "\n".join(lines) + "\n"


def tool_flags(compile_commands, source="tools/itch_oracle.cpp"):
    """The flags a tool was built with, with no paths in them."""
    entries = json.loads(Path(compile_commands).read_text(encoding="utf-8"))
    entry = next(e for e in entries if e["file"].endswith(source))
    words = entry["command"].split()[1:]
    kept = []
    skip = False
    for word in words:
        if skip:
            skip = False
        elif word in ("-o", "-c", "-MF", "-MT", "-isystem", "-I", "-isysroot"):
            skip = True
        elif not word.startswith(("-I", "/")) and word != "-MD":
            kept.append(word)
    return " ".join(kept)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "--print-flags":
        print(tool_flags(*argv[1:]))
        return 0
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--day", required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--alloc-log", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--manifest", type=Path,
                        default=REPO_ROOT / "scripts" / "data_manifest.json")
    args = parser.parse_args(argv)
    try:
        days = json.loads(args.manifest.read_text(encoding="utf-8"))["days"]
        if args.day not in days:
            raise ReportError(f"no day {args.day} in the manifest")
        entry = days[args.day]
        log = parse_log(args.log.read_text(encoding="utf-8", errors="replace"))
        failures = hard_failures(log, entry)
        alloc = None
        if args.alloc_log:
            alloc = parse_log(args.alloc_log.read_text(encoding="utf-8", errors="replace"))
            if alloc["verdicts"].get("T-ALLOC-1") != "PASS":
                failures.append("T-ALLOC-1 verdict is not PASS")
            if number(alloc, "fast_messages") != entry["messages"]:
                failures.append("the allocation run did not cover the whole day")
    except (OSError, ValueError, KeyError, ReportError) as error:
        print(f"oracle report: {error!r}", file=sys.stderr)
        return 2

    for failure in failures:
        print(f"oracle report: FAIL {failure}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render(log, entry, args.day, alloc), encoding="utf-8")
    if failures:
        return 1
    inv2 = log["verdicts"].get("T-INV-2")
    print(f"oracle report: {args.day} T-ORACLE-1 PASS, T-INV-1 PASS, "
          f"{number(log, 'messages_diffed')} messages diffed, T-INV-2 {inv2}")
    return 0 if inv2 == "PASS" else 3


if __name__ == "__main__":
    sys.exit(main())
