#!/usr/bin/env python3
"""Checks a real-day oracle log against the data manifest and writes the results file.

    oracle_report.py --day 12302019 --log RAW [--alloc-log RAW] [--out results/x.md]
                     [--arm-log ORACLE_RAW ALLOC_RAW]... [--manifest scripts/data_manifest.json]
    oracle_report.py --print-flags build/release/compile_commands.json [SOURCE]

The tool's own verdicts are not enough here. A tool that diffed half the day could still
pass, so this compares its counters with the census pinned in the manifest. The third-
party figures in COMPARISON are claims to set ours beside, never expected values, and
were published for COMPARISON_DAY only: another day's file says it has none.

--log and --alloc-log are one arm of the fast book, and the detail of the results file is
that arm's. Each --arm-log is another arm's pair of logs. Every arm is held to the same hard
checks, and has to agree with the first on every line its logs print, apart from the lines
that only describe the arm, its moves and its run. A difference is a hard failure.

Exit codes: 0 every hard check passed and T-INV-2 passed. 1 a hard check failed, and
T-INV-3 is one. 2 the inputs could not be used. 3 only T-INV-2 failed: it is strict, so it
is reported as failing, and the code keeps it apart from a broken book.
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
# crossed U counts twice in its totals, and those rows use that counting. They describe
# that one session, so only its results file sets them beside ours.
COMPARISON_DAY = "12302019"
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
VIOLATION = re.compile(
    r"t_inv3_violation kind=(\w+) symbol=(\S+) locate=(\d+) time=(\S+) message=(\d+) type=(.) "
    r"depth=(\d+)")


# Lines that describe the arm, how it ran or what only that arm does. Every other line of
# an arm's logs has to equal the first arm's.
ARM_ONLY = {
    "oracle_index", "oracle_levels", "oracle_max_pages", "oracle_index_bits", "oracle_max_slots",
    "elapsed_seconds", "move_reports", "moved_checks", "empty_moved_checks",
    "index_fallback_inserts", "index_fallback_live_end", "index_peak_live_pages",
    "level_slots_used", "level_relocations", "book_index", "book_levels",
}


class ReportError(Exception):
    """The inputs cannot be used."""


def parse_log(text):
    """Pulls the tool's key-value lines and the time -l figures out of a raw log."""
    log = {"values": {}, "types": {}, "tallies": {}, "causes": {}, "symbols": [], "states": {},
           "pairs": {}, "verdicts": {}, "divergences": [], "time": {}, "run": {},
           "new_crosses": []}
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
        elif stripped.startswith("t_inv3_violation "):
            match = VIOLATION.fullmatch(stripped)
            if match is None:
                raise ReportError(f"bad violation line: {stripped}")
            log["new_crosses"].append(match.groups())
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
            if match is not None and key.startswith(("t_inv2_", "t_inv3_")):
                log["pairs"][key] = (int(match[1]), int(match[2]))
            elif value and " " not in value:
                log["values"][key] = value
    return log


def number(log, key):
    if key not in log["values"]:
        raise ReportError(f"the log has no {key}")
    return int(log["values"][key])


def hard_failures(log, entry):
    """Every reason the run does not meet T-ORACLE-1, T-INV-1 or T-INV-3 on this day."""
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
    require(log["verdicts"].get("T-INV-3") == "PASS", "T-INV-3 verdict is not PASS")
    new, leftover = pair_of(log, "t_inv3_new"), pair_of(log, "t_inv3_leftover")
    require(new == (0, 0), f"T-INV-3 new crosses: {new[0]} crossed and {new[1]} locked")
    require(len(log["new_crosses"]) == new[0] + new[1],
            f"{len(log['new_crosses'])} T-INV-3 violations printed, {new[0] + new[1]} counted")
    # The population is T-INV-2's own row, so a tally that lost an observation shows here.
    population = pair_of(log, "t_inv2_market_open_and_t")
    require((new[0] + leftover[0], new[1] + leftover[1]) == population,
            f"T-INV-3 accounts for {new[0] + leftover[0]} crossed and {new[1] + leftover[1]} "
            f"locked, the population holds {population[0]} and {population[1]}")
    require(number(log, "t_inv3_leftovers_opened") <= number(log, "t_inv2_reopening_windows"),
            "more leftovers than resumptions")
    return failures


def pair_of(log, key):
    if key not in log["pairs"]:
        raise ReportError(f"the log has no {key}")
    return log["pairs"][key]


def arm_of(log, index_key="oracle_index", levels_key="oracle_levels"):
    """The arm a log ran, as index_levels."""
    values = log["values"]
    if index_key not in values or levels_key not in values:
        raise ReportError(f"the log does not name its arm with {index_key} and {levels_key}")
    return f"{values[index_key]}_{values[levels_key]}"


def comparable(log):
    """Everything a log says that does not depend on the arm."""
    kept = {key: value for key, value in log.items() if key not in ("run", "time", "values")}
    kept["values"] = {key: value for key, value in log["values"].items() if key not in ARM_ONLY}
    return kept


def disagreements(first, other):
    """The parts and lines of two logs that differ, with the arm's own lines left out."""
    a, b = comparable(first), comparable(other)
    keys = []
    for part in sorted(set(a) | set(b)):
        if part == "values":
            keys += [key for key in sorted(set(a["values"]) | set(b["values"]))
                     if a["values"].get(key) != b["values"].get(key)]
        elif a.get(part) != b.get(part):
            keys.append(part)
    return keys


def arm_failures(arms, entry):
    """Each arm is held to the hard checks, and every arm to the first one's answers."""
    failures = []
    seen = set()
    first = arms[0]
    for arm in arms:
        name = arm["name"]
        if name in seen:
            failures.append(f"arm {name} is given twice")
        seen.add(name)
        if arm is not first:
            failures += [f"arm {name}: {text}" for text in hard_failures(arm["log"], entry)]
            failures += [f"arm {name}: {text}" for text in alloc_failures(arm["alloc"], entry)]
        if arm["alloc"] is not None and arm_of(arm["alloc"], "book_index", "book_levels") != name:
            failures.append(f"arm {name}: its allocation log ran another arm")
        differ = disagreements(first["log"], arm["log"])
        if arm["alloc"] is not None and first["alloc"] is not None:
            differ += [f"allocation {key}" for key in disagreements(first["alloc"], arm["alloc"])]
        if differ:
            failures.append(f"arm {name} disagrees with {first['name']} on {', '.join(differ)}")
    return failures


def alloc_failures(alloc, entry):
    if alloc is None:
        return []
    failures = []
    if alloc["verdicts"].get("T-ALLOC-1") != "PASS":
        failures.append("T-ALLOC-1 verdict is not PASS")
    if number(alloc, "fast_messages") != entry["messages"]:
        failures.append("the allocation run did not cover the whole day")
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


def inv3_section(log):
    """T-INV-3, apart from T-INV-2, whose strict result this does not touch."""
    new, leftover = pair_of(log, "t_inv3_new"), pair_of(log, "t_inv3_leftover")
    lines = [
        "",
        "## T-INV-3: no new cross in normal trading",
        "",
        f"Verdict: **{log['verdicts'].get('T-INV-3', 'missing')}**, "
        f"{count(new[0] + new[1])} violations. This check is separate from T-INV-2 and does "
        "not change it.",
        "",
        "The rule, as the user approved it after seeing real data: during normal trading, a "
        "book may only be crossed if the cross is left over from a halt and hasn't cleared "
        "yet. Any new cross is a failure. The population is market open with the symbol in "
        "state `T`, and locked counts apart from crossed. A leftover exists only if that "
        "symbol's book is crossed or locked at the moment of a resumption. It ends on the "
        "first observation that is neither, or on a cross message. No time limit enters the "
        "rule.",
        "",
    ]
    lines += format_table(("In the population", "Crossed", "Locked"), [
        ("With a leftover in force, allowed", count(leftover[0]), count(leftover[1])),
        ("With none, a violation", count(new[0]), count(new[1])),
    ])
    lines += [""]
    lines += format_table(("Resumptions and leftovers", "Count"), [
        ("Resumptions, `H` into `T`", count(number(log, "t_inv2_reopening_windows"))),
        ("Of which found the book crossed or locked, so a leftover opened",
         count(number(log, "t_inv3_leftovers_opened"))),
        ("Leftovers ended by an observation neither crossed nor locked",
         count(number(log, "t_inv3_ended_by_book"))),
        ("Leftovers ended by a cross message", count(number(log, "t_inv3_ended_by_cross"))),
        ("Leftovers still in force at the end of the day",
         count(number(log, "t_inv3_still_in_force"))),
        ("Longest leftover, ns, reported and not limited",
         count(number(log, "t_inv3_longest_leftover_ns"))),
    ])
    if log["new_crosses"]:
        lines += ["", "### Every violation", ""]
        lines += format_table(
            ("Kind", "Symbol", "Time", "Message", "Type", "Depth, price units"),
            [(kind, symbol, time, count(int(message)), f"`{kind_of}`", count(int(depth)))
             for kind, symbol, _, time, message, kind_of, depth in log["new_crosses"]])
    return lines


def arms_section(arms):
    """One row per arm. The verdicts here are not marked up: the sections below hold them."""
    gib = 1024.0 ** 3
    first = arms[0]["name"]
    lines = [
        "",
        "## Every arm of the fast book",
        "",
        f"Each arm replayed the whole day on its own. Every line its logs print agrees with "
        f"`{first}`'s, apart from the lines that describe the arm, what it moved and how long "
        "it ran, and the report fails on any other difference. The sections after this one "
        f"are `{first}`'s. The raw logs are not committed.",
        "",
    ]
    rows = []
    for arm in arms:
        log, alloc, time = arm["log"], arm["alloc"], arm["log"]["time"]
        crossed, locked = number(log, "t_inv2_crossed"), number(log, "t_inv2_locked")
        rows.append((
            f"`{arm['name']}`",
            log["verdicts"].get("T-ORACLE-1", "missing"),
            log["verdicts"].get("T-INV-1", "missing"),
            f"{log['verdicts'].get('T-INV-2', 'missing')}, {count(crossed)} crossed and "
            f"{count(locked)} locked",
            log["verdicts"].get("T-INV-3", "missing"),
            alloc["verdicts"].get("T-ALLOC-1", "missing") if alloc else "not run",
            count(number(log, "messages_diffed")),
            f"`{log['values'].get('last_hash', '?')}`",
            count(int(log["values"].get("move_reports", 0))),
            count(int(log["values"].get("moved_checks", 0))),
            log["values"].get("elapsed_seconds", "?"),
            f"{time.get('max_rss', 0) / gib:.2f}",
            f"{time.get('peak_footprint', 0) / gib:.2f}",
            f"`{arm['raw']}`",
        ))
    lines += format_table(
        ("Arm", "T-ORACLE-1", "T-INV-1", "T-INV-2 strict", "T-INV-3", "T-ALLOC-1",
         "Messages diffed", "Last hash", "Moves reported", "Moved levels compared",
         "Replay, s", "Maximum RSS, GiB", "Peak footprint, GiB", "Raw log"), rows)
    return lines


def render(log, entry, day, alloc, arms):
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
        f"- Book: `FastBook`, {len(arms)} arm{'s' if len(arms) != 1 else ''} below. The run "
        f"above and the detail after the table are `{arms[0]['name']}`.",
    ]
    lines += arms_section(arms)
    lines += [
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
    lines += ["", "### Against published figures", ""]
    if day == COMPARISON_DAY:
        lines += [
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
    else:
        lines.append(f"None. The figures this file could set beside ours were published for "
                     f"{COMPARISON_DAY} only.")
    lines += inv3_section(log)
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
    parser.add_argument("--arm-log", nargs=2, type=Path, action="append", default=[],
                        metavar=("ORACLE_RAW", "ALLOC_RAW"))
    parser.add_argument("--manifest", type=Path,
                        default=REPO_ROOT / "scripts" / "data_manifest.json")
    args = parser.parse_args(argv)

    def read(path):
        return parse_log(path.read_text(encoding="utf-8", errors="replace"))

    try:
        days = json.loads(args.manifest.read_text(encoding="utf-8"))["days"]
        if args.day not in days:
            raise ReportError(f"no day {args.day} in the manifest")
        entry = days[args.day]
        log = read(args.log)
        failures = hard_failures(log, entry)
        alloc = read(args.alloc_log) if args.alloc_log else None
        failures += alloc_failures(alloc, entry)
        arms = [{"name": arm_of(log), "log": log, "alloc": alloc, "raw": args.log.name}]
        for oracle_path, alloc_path in args.arm_log:
            other = read(oracle_path)
            arms.append({"name": arm_of(other), "log": other, "alloc": read(alloc_path),
                         "raw": oracle_path.name})
        failures += arm_failures(arms, entry)
    except (OSError, ValueError, KeyError, ReportError) as error:
        print(f"oracle report: {error!r}", file=sys.stderr)
        return 2

    for failure in failures:
        print(f"oracle report: FAIL {failure}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render(log, entry, args.day, alloc, arms), encoding="utf-8")
    if failures:
        return 1
    inv2 = log["verdicts"].get("T-INV-2")
    print(f"oracle report: {args.day} T-ORACLE-1 PASS, T-INV-1 PASS, T-INV-3 PASS, "
          f"{number(log, 'messages_diffed')} messages diffed, T-INV-2 {inv2}, "
          f"the same on {len(arms)} arm{'s' if len(arms) != 1 else ''}")
    return 0 if inv2 == "PASS" else 3


if __name__ == "__main__":
    sys.exit(main())
