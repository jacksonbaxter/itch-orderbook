#!/usr/bin/env python3
"""Writes the cost of each message type on one day's mix from bench_variants records.

    types_report.py --record RUN.json... --load STDERR... --prediction P-NNNN \\
        --out results/types_HOST_DAY.md [--machine-check BEFORE AFTER] \\
        [--host-state START END] [--sampler LOG] [--run-log results/run_log.md]
    types_report.py --check FILE

The records are `bench_variants --pass types` processes and `--pass lookup --block N`
processes of one arm on one input, each with its stderr from measure.sh in the same order.
A typed pass reads the clock after every N frames and keeps, for each quarter of the pass,
the sums a least-squares fit of a block's ticks on its counts by type needs
(bench/typed_time.hpp). This script fits them exactly, in rationals, so the figures do not
depend on rounding:

  - each type's cost, ns a message, from the typed passes of the `types` processes, which
    run the book as it ships;
  - each type's lookups, ns a message of the type, from the `lookup` processes: the fit of
    the check passes less the fit of the answer passes (bench/lookup_script.hpp);
  - the rest of each type's cost, the first less the second.

The rules are variants_report.py's: on macOS a CPU sampler's log is required and a process is
published only if the sampler ran over its whole span and saw no other process above 20% CPU;
on debian the machine check and the host's state at both ends. Every figure in the file is
worked from its table of passes and its block sums, so --check fits them again and fails on
any difference, and on a host label that does not match the file name, a load line that is
not measure.sh's, a macOS file that does not say its processes were sampled quiet, a whole-day
count that differs from scripts/data_manifest.json, and anything private.

Each violation prints as `types_report: FAIL <code>: <detail>`.

Exit codes: 0 written, or --check found nothing, 1 --check found something, 2 bad usage or
unusable input, a refused record among them.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402
import stamp_load  # noqa: E402
import variants_report  # noqa: E402

MANIFEST = Path(__file__).resolve().parent / "data_manifest.json"
DEBIAN = variants_report.DEBIAN
MAC = variants_report.MAC
HOST_FILE = variants_report.HOST_FILE
INTERFERENCE = variants_report.INTERFERENCE
COLUMNS = "AFDUECXPIL*"
OTHER = "*"
WHAT = {"A": "add", "F": "add, attributed", "D": "delete", "U": "replace: a delete and an add",
        "E": "execute", "C": "execute at a price", "X": "cancel part", "P": "trade, hidden",
        "I": "imbalance", "L": "participant position", OTHER: "everything else"}
# find() calls a message of each type makes on the book: an add's duplicate check, a named
# order's lookup, and a replace's two, the old reference and the new one's duplicate check.
LOOKUPS = {"A": 1, "F": 1, "D": 1, "U": 2, "E": 1, "C": 1, "X": 1}
HEADLINE = ("U", "D", "A")
QUARTER_SHARE = 0.01  # A type is shown by quarter if it is this share of the messages or more.
QUARTER_LEAST = 1000  # And a quarter's fit is shown only on this many of its messages.
TITLE = "Replace, delete and add: the cost of each message type"
FILE_NAME = re.compile(r"^types_(debian|mac)_([0-9A-Za-z]+)\.md$")
PASS_ROW = re.compile(r"^\| `(types|lookup)` \| (\d+) \| (\d+) \| (replay|typed|check|answer) \| "
                      r"([0-9,]+) \| ([0-9.]+) \| ([0-9,]+) \| ([0-9,]+|n/a) \|$")
SUMS_LINE = re.compile(r"^\{.*\}$")

METHOD = """\
Each process maps the input, reads every page and finds every page resident, then runs a
warm-up pass and four timed passes, each on a fresh book. Two kinds of process:

- **types**: `bench_variants --pass types`, the book as it ships, in the order replay,
  typed, typed, replay. A replay pass is one span of the clock over the whole input. A
  typed pass reads the clock once before the first frame and once after every {block}
  frames (`bench/typed_time.hpp`), so that each reading spans {block} messages, the mean
  block in the results below against the timer's floor in the header, and the blocks tile
  the pass: their ticks add up to the pass's exactly, which the bench checks.
- **lookup**: `bench_variants --pass lookup --block {block}`: the index wrapped by
  `ScriptedIndex` (`bench/lookup_script.hpp`), a recording pass as the warm-up, then check,
  answer, answer, check, each timed by blocks. A check pass asks the real index and compares
  the answer with the script's; an answer pass takes the script's entry and asks nothing.

Every block of a typed pass is counted by type, in eleven columns: `A`, `F`, `D`, `U`, `E`,
`C`, `X`, `P`, `I`, `L` and everything else. A type's cost is the coefficient of its count
in a least-squares fit, with no intercept, of each block's ticks on its counts by type, from
the sums the pass keeps, solved exactly. It is the time a block takes for each more message
of that type, with the other types of the block held: the type's cost where it sits in the
day, with whatever its neighbours leave in the caches, the frame walk, parse and dispatch
included. A type's lookups are the same fit on the check passes less the fit on the answer
passes; the rest of its cost, the first less the second, is everything but the index's own
loads: the parse, the order record, the level change, and the index's insert and erase.

The fit assumes a message's cost depends on its type alone. So each pass keeps the sums of
its four quarters apart, and the quarters are fitted on their own as well: a type whose cost
moves across the day shows it there. No figure is a percentile, and no single message is
timed."""

CAVEATS = """\
- **A fit, not a reading.** Each type's figure is a regression coefficient over every block
  of the pass, not a message timed alone. Where two types' costs depend on each other, the
  fit shares their time between them by their counts; the quarters show how far the
  coefficients move with the time of day.
- **Cost in place.** The figures hold how a message's misses overlap with its neighbours'.
  A type that comes in bursts on one stock finds the caches warmer than one that does not.
- **The blocks' own cost.** A typed pass counts each frame's type and reads the clock every
  block; the replay passes, which do neither, give what that costs, and it is spread over
  every message alike, since every block holds the same number of frames.
- **The rest is not the level change alone.** The lookup run splits off the index's own
  loads, as `results/lookup_*` does; what is left holds the parse, the order record, the
  level's unlink and append and the index's insert and erase together. No arm here splits
  them further.
- **One host, one input per file.** Mac figures come from wall time, since the Mac reads no
  miss counter a message; `debian`'s counters are not in this file."""


class ReportError(Exception):
    """The input cannot become a results file."""


def solve(gram, cross, counts):
    """The least-squares coefficients, ticks a message, exactly; None for an empty column."""
    used = [i for i in range(len(COLUMNS)) if counts[i] > 0]
    size = len(used)
    rows = [[Fraction(gram[i][j]) for j in used] + [Fraction(cross[i])] for i in used]
    for col in range(size):
        pivot = next((r for r in range(col, size) if rows[r][col] != 0), None)
        if pivot is None:
            raise ReportError(f"the fit is singular at column {COLUMNS[used[col]]}")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(size):
            if r != col and rows[r][col] != 0:
                factor = rows[r][col] / rows[col][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col])]
    out = [None] * len(COLUMNS)
    for k, i in enumerate(used):
        out[i] = rows[k][size] / rows[k][k]
    return out


def full_gram(upper):
    """The symmetric matrix from its upper triangle, row by row."""
    n = len(COLUMNS)
    gram = [[0] * n for _ in range(n)]
    at = 0
    for i in range(n):
        for j in range(i, n):
            gram[i][j] = gram[j][i] = upper[at]
            at += 1
    return gram


def upper_of(gram_flat):
    n = len(COLUMNS)
    return [gram_flat[i * n + j] for i in range(n) for j in range(i, n)]


def summed(quarters):
    n = len(COLUMNS)
    out = {"blocks": 0, "ticks": 0, "ticks_squared": 0, "counts": [0] * n, "cross": [0] * n,
           "gram": [0] * (n * (n + 1) // 2)}
    for q in quarters:
        for key in ("blocks", "ticks", "ticks_squared"):
            out[key] += q[key]
        for key in ("counts", "cross", "gram"):
            out[key] = [a + b for a, b in zip(out[key], q[key])]
    return out


def fit(sums, ns_per_tick):
    """Coefficients in ns a message, the fit's R squared, and the fitted share of the ticks."""
    gram = full_gram(sums["gram"])
    coef = solve(gram, sums["cross"], sums["counts"])
    used = [i for i, c in enumerate(coef) if c is not None]
    explained = sum(coef[i] * sums["cross"][i] for i in used)
    fitted = sum(coef[i] * sums["counts"][i] for i in used)
    residual = sums["ticks_squared"] - explained  # c'Gc equals c'h at the solution.
    total = sums["ticks_squared"] - Fraction(sums["ticks"] ** 2, sums["blocks"])
    return {"ns": [None if c is None else float(c) * ns_per_tick for c in coef],
            "r2": float(1 - residual / total) if total else 1.0,
            "fitted": float(fitted / sums["ticks"]) if sums["ticks"] else 0.0}


def mean_of(values):
    present = [v for v in values if v is not None]
    return statistics.mean(present) if len(present) == len(values) and values else None


def column_means(fits):
    return [mean_of([f["ns"][i] for f in fits]) for i in range(len(COLUMNS))]


def spread(values):
    present = [v for v in values if v is not None]
    if not present:
        return None, None, None
    return statistics.median(present), min(present), max(present)


def figures_of(passes, sums):
    """Every figure the file gives, from its table of passes and its block sums."""
    by_process = {}
    for row in passes:
        by_process.setdefault((row["variant"], row["repetition"]), []).append(row)
    sums_by = {(s["variant"], s["repetition"], s["pass"]): s for s in sums}
    types_coef, quarter_coef, r2, fitted, replay_ns, typed_ns, cost = [], [], [], [], [], [], []
    lookup_coef, per_lookup, block_ns = [], [], []
    for (variant, repetition), rows in sorted(by_process.items()):
        def fits(mode, quarter=None):
            out = []
            for row in rows:
                if row["mode"] != mode:
                    continue
                one = sums_by.get((variant, repetition, row["pass"]))
                if one is None:
                    raise ReportError(f"no block sums for {variant} {repetition} pass "
                                      f"{row['pass']}")
                if quarter is None:
                    out.append(fit(summed(one["quarters"]), one["ns_per_tick"]))
                    continue
                try:
                    out.append(fit(summed([one["quarters"][quarter]]), one["ns_per_tick"]))
                except (ReportError, ZeroDivisionError):
                    # A quarter too thin to fit every column alone is shown as n/a.
                    out.append({"ns": [None] * len(COLUMNS), "r2": 0.0, "fitted": 0.0})
            return out
        if variant == "types":
            typed, replays = fits("typed"), [r for r in rows if r["mode"] == "replay"]
            block_ns += [summed(s["quarters"])["ticks"] * s["ns_per_tick"] /
                         summed(s["quarters"])["blocks"] for (v, n, _), s in sums_by.items()
                         if v == variant and n == repetition]
            if not typed or not replays:
                raise ReportError(f"types {repetition}: wanted typed and replay passes")
            types_coef.append(column_means(typed))
            r2.append(statistics.mean(f["r2"] for f in typed))
            fitted.append(statistics.mean(f["fitted"] for f in typed))
            quarter_coef.append([column_means(fits("typed", q)) for q in range(4)])
            messages = rows[0]["messages"]
            replay_s = statistics.mean(r["seconds"] for r in replays)
            typed_s = statistics.mean(r["seconds"] for r in rows if r["mode"] == "typed")
            replay_ns.append(replay_s * 1e9 / messages)
            typed_ns.append(typed_s * 1e9 / messages)
            cost.append(typed_s / replay_s - 1)
        else:
            checks, answers = fits("check"), fits("answer")
            if not checks or not answers:
                raise ReportError(f"lookup {repetition}: wanted check and answer passes")
            check, answer = column_means(checks), column_means(answers)
            lookup_coef.append([None if a is None or b is None else a - b
                                for a, b in zip(check, answer)])
            check_s = statistics.mean(r["seconds"] for r in rows if r["mode"] == "check")
            answer_s = statistics.mean(r["seconds"] for r in rows if r["mode"] == "answer")
            per_lookup.append((check_s - answer_s) * 1e9 / rows[0]["lookups"])
    if not types_coef:
        raise ReportError("no types process")
    out = {"columns": {}, "r2": statistics.median(r2), "fitted": statistics.median(fitted),
           "replay_ns": statistics.median(replay_ns), "typed_ns": statistics.median(typed_ns),
           "cost": statistics.median(cost), "types_reps": len(types_coef),
           "lookup_reps": len(lookup_coef),
           "per_lookup": statistics.median(per_lookup) if per_lookup else None,
           "block_ns": statistics.median(block_ns),
           "under_floor": sum(q["under_floor"] for s in sums for q in s["quarters"]),
           "blocks": sum(q["blocks"] for s in sums for q in s["quarters"])}
    for i, name in enumerate(COLUMNS):
        med, low, high = spread([c[i] for c in types_coef])
        look = spread([c[i] for c in lookup_coef])[0] if lookup_coef else None
        out["columns"][name] = {
            "ns": med, "min": low, "max": high,
            "lookup": look if name in LOOKUPS else None,
            "quarters": [spread([q[k][i] for q in quarter_coef])[0] for k in range(4)]}
    return out


def counts_of(sums):
    """Each column's messages, from the first typed pass's sums, and each quarter's."""
    first = next(s for s in sums if s["variant"] == "types")
    whole = summed(first["quarters"])["counts"]
    return whole, [q["counts"] for q in first["quarters"]]


def fmt(value, places=2):
    return "n/a" if value is None else f"{value:,.{places}f}"


def results_lines(figures, counts, quarter_counts, block_share):
    total = sum(counts)
    out = ["Medians over the processes; each process's figure is the mean of its two typed "
           "passes, or of its two check passes less its two answer passes. ns a message is "
           "of a message of that type. The share of the time is the type's cost times its "
           "messages over a typed pass's time.", "",
           "| Type | What it does | Messages | Share of the messages | ns a message | Min | Max "
           "| Share of the time | Lookups a message | Lookups, ns a message | The rest, ns a "
           "message |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, name in enumerate(COLUMNS):
        f = figures["columns"][name]
        look = f["lookup"]
        rest = None if look is None or f["ns"] is None else f["ns"] - look
        label = "everything else" if name == OTHER else f"`{name}`"
        share_time = block_share[i]
        out.append(f"| {label} | {WHAT[name]} | {counts[i]:,} | {counts[i] / total * 100:.2f}% "
                   f"| {fmt(f['ns'])} | {fmt(f['min'])} | {fmt(f['max'])} "
                   f"| {fmt(None if share_time is None else share_time * 100, 1)}"
                   f"{'' if share_time is None else '%'} | {LOOKUPS.get(name, 0)} "
                   f"| {fmt(look)} | {fmt(rest)} |")
    out += ["", "By quarter of the input, each quarter's blocks fitted on their own, for each "
            f"type that is {QUARTER_SHARE * 100:.0f}% of the messages or more; n/a where the "
            f"quarter holds fewer than {QUARTER_LEAST:,} of the type. ns a message, medians "
            "over the processes.", "",
            "| Type | Whole | First quarter | Second | Third | Fourth |", "|---|---|---|---|---|---|"]
    for i, name in enumerate(COLUMNS):
        if name == OTHER or counts[i] < QUARTER_SHARE * total:
            continue
        f = figures["columns"][name]
        cells = [fmt(v) if quarter_counts[k][i] >= QUARTER_LEAST else "n/a"
                 for k, v in enumerate(f["quarters"])]
        out.append(f"| `{name}` | {fmt(f['ns'])} | " + " | ".join(cells) + " |")
    out += ["", "| Whole input | Median |", "|---|---|",
            f"| Replay passes, ns a message, the book as it ships | {fmt(figures['replay_ns'])} |",
            f"| Typed passes, ns a message | {fmt(figures['typed_ns'])} |",
            f"| What the blocks cost: typed over replay, less one | "
            f"{figures['cost'] * 100:+.2f}% |",
            f"| Mean block of a typed pass, ns | {fmt(figures['block_ns'])} |",
            f"| Blocks under the timer's floor, of every block of every pass | "
            f"{figures['under_floor']:,} of {figures['blocks']:,} |",
            f"| The fit's R squared over the blocks | {figures['r2']:.4f} |",
            f"| Fitted costs times counts, over the blocks' ticks | {figures['fitted']:.6f} |",
            f"| Lookup run, check less answer, ns a lookup | {fmt(figures['per_lookup'])} |",
            f"| Types processes; lookup processes | {figures['types_reps']}; "
            f"{figures['lookup_reps']} |"]
    return out


def shares_of_time(figures, counts):
    """Each column's cost times its count over a typed pass's time, both medians."""
    whole = figures["typed_ns"] * sum(counts)
    return [None if figures["columns"][name]["ns"] is None
            else figures["columns"][name]["ns"] * counts[i] / whole
            for i, name in enumerate(COLUMNS)]


def finding_lines(figures, counts):
    total = sum(counts)
    cols = figures["columns"]
    lines = []
    for name in HEADLINE:
        f = cols[name]
        i = COLUMNS.index(name)
        look = f["lookup"]
        part = "" if look is None else (
            f"; its {LOOKUPS[name]} lookup{'s' if LOOKUPS[name] > 1 else ''} "
            f"{fmt(look)} ns ({look / f['ns'] * 100:.0f}%), the rest {fmt(f['ns'] - look)} ns")
        lines.append(f"- `{name}`, {WHAT[name]}: {fmt(f['ns'])} ns a message (min "
                     f"{fmt(f['min'])}, max {fmt(f['max'])}), {counts[i] / total * 100:.2f}% "
                     f"of the messages{part}.")
    u, d, a = (cols[n]["ns"] for n in HEADLINE)
    lines.append(f"- A replace against a delete and an add: {fmt(u)} against {fmt(d)} + "
                 f"{fmt(a)} = {fmt(d + a)} ns, {u / (d + a):.3f} times.")
    lines.append(f"- The whole input: {fmt(figures['replay_ns'])} ns a message on the book as "
                 f"it ships; the typed passes' blocks cost {figures['cost'] * 100:+.2f}% of "
                 f"that; the fit's R squared {figures['r2']:.4f}.")
    summary = (f"U {u:.2f} ns, D {d:.2f} ns, A {a:.2f} ns a message; U's lookups "
               f"{fmt(cols['U']['lookup'])} ns; {figures['replay_ns']:.2f} ns a message over "
               f"the input; U {counts[COLUMNS.index('U')] / total * 100:.1f}% of the messages")
    return lines, summary


def mix_lines(types, whole_day):
    total = sum(types.values())
    out = ["The input as run, counted by the bench before any pass, by type."]
    if whole_day:
        out[0] += (" The whole day's counts are the ones pinned in "
                   "`scripts/data_manifest.json`.")
        day_total = sum(whole_day.values())
        out += ["", "| Type | Messages | Share | The whole day | Share |", "|---|---|---|---|---|"]
        for name in sorted(set(types) | set(whole_day)):
            mine, day = types.get(name, 0), whole_day.get(name, 0)
            out.append(f"| `{name}` | {mine:,} | {mine / total * 100:.3f}% | {day:,} "
                       f"| {day / day_total * 100:.3f}% |")
        out.append(f"| all | {total:,} | 100.000% | {day_total:,} | 100.000% |")
    else:
        out += ["", "| Type | Messages | Share |", "|---|---|---|"]
        for name in sorted(types):
            out.append(f"| `{name}` | {types[name]:,} | {types[name] / total * 100:.3f}% |")
        out.append(f"| all | {total:,} | 100.000% |")
    return out


MIX_ROW = re.compile(r"^\| `(.)` \| ([0-9,]+) \| [0-9.]+% (?:\| ([0-9,]+) \| [0-9.]+% )?\|$")


def parse_mix(text):
    types, day = {}, {}
    for line in (check_results.section(text, "The mix") or "").splitlines():
        match = MIX_ROW.match(line)
        if match:
            types[match.group(1)] = int(match.group(2).replace(",", ""))
            if match.group(3) is not None:
                day[match.group(1)] = int(match.group(3).replace(",", ""))
    return types, day


def computed(passes, sums, types, whole_day):
    figures = figures_of(passes, sums)
    counts, quarter_counts = counts_of(sums)
    share = shares_of_time(figures, counts)
    results = results_lines(figures, counts, quarter_counts, share)
    lines, summary = finding_lines(figures, counts)
    return mix_lines(types, whole_day), results, lines, summary


def judge(records):
    kinds = {record["variant"]["pass"] for record in records}
    if "types" not in kinds or not kinds <= {"types", "lookup"}:
        raise ReportError(f"wanted types processes, and lookup ones if any: {sorted(kinds)}")
    for record in records:
        variant = record["variant"]
        if record["refusals"]:
            raise ReportError(f"a refused record: {record['refusals']}")
        if variant["dispatch"] != "switch" or variant["prefetch"] != 0 or not variant.get("block"):
            raise ReportError(f"a record of another kind: {variant}")
        for one in record["passes"]:
            if one["messages"] != record["mix"]["messages"] or one["rejected"] != 0:
                raise ReportError("a pass that did not walk the input cleanly")
            if variant["pass"] == "lookup" and (not one["script_exact"] or one["mismatches"]):
                raise ReportError("a lookup pass that did not read its script exactly")
            typed = one.get("typed")
            if one["mode"] != "replay" and typed is None:
                raise ReportError(f"a {one['mode']} pass with no blocks")
            if typed is not None and sum(q["ticks"] for q in typed["quarters"]) != (
                    typed["last"] - typed["first"]):
                raise ReportError("a typed pass whose blocks do not tile it")
            if typed is not None and typed["columns"] != COLUMNS:
                raise ReportError(f"columns {typed['columns']!r}, wanted {COLUMNS!r}")
    for field in ("host", "cpu", "kernel", "compiler", "flags", "commit", "dataset", "input"):
        values = {record["header"][field] for record in records}
        if len(values) != 1:
            raise ReportError(f"the records differ in {field}: {sorted(values)}")
    for field in ("design", "block", "first_messages"):
        values = {record["variant"].get(field) for record in records}
        if len(values) != 1:
            raise ReportError(f"the records differ in {field}: {sorted(map(str, values))}")
    if len({json.dumps(record["input"], sort_keys=True) for record in records}) != 1:
        raise ReportError("the records ran different inputs")


def rows_and_sums(records):
    passes, sums, reps = [], [], {}
    for record in records:
        variant = record["variant"]["pass"]
        reps[variant] = reps.get(variant, 0) + 1
        for number, one in enumerate(record["passes"], start=1):
            typed = one.get("typed")
            passes.append({"variant": variant, "repetition": reps[variant], "pass": number,
                           "mode": one["mode"], "messages": one["messages"],
                           "seconds": round(one["seconds"], 6), "lookups": one["lookups"],
                           "blocks": None if typed is None else sum(
                               q["blocks"] for q in typed["quarters"])})
            if typed is not None:
                sums.append({"variant": variant, "repetition": reps[variant], "pass": number,
                             "ns_per_tick": record["ns_per_tick"],
                             "quarters": [{"blocks": q["blocks"], "ticks": q["ticks"],
                                           "ticks_squared": q["ticks_squared"],
                                           "under_floor": q["under_floor"],
                                           "most_ticks": q["most_ticks"],
                                           "counts": q["counts"], "cross": q["cross"],
                                           "gram": upper_of(q["gram"])}
                                          for q in typed["quarters"]]})
    return passes, sums


def pass_table(passes):
    out = ["| Process | Repetition | Pass | Mode | Messages | Seconds | Lookups | Blocks |",
           "|---|---|---|---|---|---|---|---|"]
    for row in passes:
        blocks = "n/a" if row["blocks"] is None else f"{row['blocks']:,}"
        out.append(f"| `{row['variant']}` | {row['repetition']} | {row['pass']} | {row['mode']} "
                   f"| {row['messages']:,} | {row['seconds']:.6f} | {row['lookups']:,} "
                   f"| {blocks} |")
    return out


def parse_passes(text):
    passes = []
    for line in (check_results.section(text, "Every timed pass") or "").splitlines():
        match = PASS_ROW.match(line)
        if match is None:
            continue
        blocks = match.group(8)
        passes.append({"variant": match.group(1), "repetition": int(match.group(2)),
                       "pass": int(match.group(3)), "mode": match.group(4),
                       "messages": int(match.group(5).replace(",", "")),
                       "seconds": float(match.group(6)),
                       "lookups": int(match.group(7).replace(",", "")),
                       "blocks": None if blocks == "n/a" else int(blocks.replace(",", ""))})
    return passes


def parse_sums(text):
    return [json.loads(line) for line in (check_results.section(text, "Block sums") or
                                          "").splitlines() if SUMS_LINE.match(line)]


def whole_day_counts(dataset, sliced):
    """The day's pinned counts by type, for an input that is the first part of the day."""
    if not sliced:
        return {}
    days = json.loads(MANIFEST.read_text(encoding="utf-8"))["days"]
    for entry in days.values():
        if entry.get("name") == dataset:
            return entry.get("per_type_counts") or {}
    raise ReportError(f"no pinned day named {dataset!r} in {MANIFEST.name}")


def render(records, stderrs, prediction, machine_checks, host_states, sampler=None):
    judge(records)
    types_record = next(r for r in records if r["variant"]["pass"] == "types")
    header = types_record["header"]
    host = header["host"]
    if host not in HOST_FILE:
        raise ReportError(f"no host known as {host!r}")
    if host == DEBIAN and (len(machine_checks) != 2 or len(host_states) != 2):
        raise ReportError("a debian run needs the machine check and the host state at both ends")
    lines = [variants_report.load_lines(text, host) for text in stderrs]
    if host == MAC:
        if sampler is None:
            raise ReportError("a macOS run needs the CPU sampler's log (--sampler)")
        variants_report.quiet_spans(sampler, lines)
    run_id = f"{prediction}-" + variants_report.timestamp(lines[0][0]).replace(":", "").replace(
        "-", "")
    passes, sums = rows_and_sums(records)
    variant = types_record["variant"]
    sliced = bool(variant.get("first_messages"))
    whole_day = whole_day_counts(header["dataset"], sliced)
    mix, results, found, summary = computed(passes, sums, types_record["input"]["types"],
                                            whole_day)
    day = header["dataset"].split(".", 1)[0].split("-", 1)[0]
    lookup_record = next((r for r in records if r["variant"]["pass"] == "lookup"), None)
    out = [f"# {TITLE}, {HOST_FILE[host]}, {day}", "",
           "Generated by `scripts/types_report.py` from raw records that are not committed. "
           "Do not edit by hand.", "",
           f"Host: **{host}**. Numbers from the two hosts are never mixed.", "", "## Header", ""]
    out += [f"- {name}: {value}" for name, value in header.items()
            if name not in ("design", "page_size", "rdpmc", "counters")]
    out += ["- experiment: types", f"- prediction: {prediction}", f"- run_id: {run_id}",
            f"- processes: {len(records)}", f"- messages: {types_record['mix']['messages']:,}",
            f"- slice: " + (f"the first {variant['first_messages']:,} messages of the day, "
                            f"{types_record['input']['bytes']:,} of its "
                            f"{types_record['input']['file_bytes']:,} bytes, the book built "
                            "from the day's start" if sliced else "the whole day"),
            f"- block: {variant['block']} frames",
            f"- book: {header['design']}"]
    if lookup_record is not None:
        out.append(f"- lookup_book: {lookup_record['header']['design']}")
    out += [f"- migrations_all_processes: {sum(r['migrations'] for r in records)}",
            f"- peak_rss: {variants_report.peak_rss(records, stderrs)}",
            f"- measure_load_start: {lines[0][0]}", f"- measure_load_end: {lines[-1][1]}",
            f"- run_started: {variants_report.timestamp(lines[0][0])}",
            f"- run_finished: {variants_report.timestamp(lines[-1][1])}"]
    if host == MAC:
        out.append(f"- interference: {INTERFERENCE}")
    for name, state in zip(("host_state_start", "host_state_end"), host_states):
        out.append(f"- {name}: {stamp_load.HOST.sub(f'host={host}', state.strip())}")
    out += [f"- summary: {summary}", "", "## The mix", ""]
    out += mix
    out += ["", "## What was run", "", METHOD.format(block=variant["block"]), "", "## Results", ""]
    out += results
    out += ["", "## Finding", ""]
    out += found
    out += ["", "## Every timed pass", "", "One row a timed pass, in the order run.", ""]
    out += pass_table(passes)
    out += ["", "## Block sums", "",
            "One line a typed, check or answer pass, as the bench kept them: for each quarter, "
            "its blocks, their ticks and ticks squared, the blocks under the timer's floor, the "
            "longest block, and for the columns `" + COLUMNS + "` (`*` everything else) each "
            "column's messages, its messages times ticks, and the upper triangle of messages "
            "times messages, row by row. Every figure above is fitted from these lines.", "",
            "```"]
    out += [json.dumps(one, separators=(",", ":")) for one in sums]
    out += ["```", "", "## What this does not show", "", CAVEATS]
    if machine_checks:
        out += ["", "## machine_check output", "", "Before the first process and after the last.",
                "", "```"]
        out += [text.strip() for text in machine_checks]
        out += ["```"]
    out += ["", "## measure.sh load lines", "", "```"]
    out += [line for pair in lines for line in pair]
    out += ["```", "", "## Command", "", "```", command_line(host, variant), "```", ""]
    return "\n".join(out), run_id, summary


def command_line(host, variant):
    prefix = "measure.sh taskset -c 4" if host == DEBIAN else "measure.sh /usr/bin/time -l"
    core = " --core 4" if host == DEBIAN else ""
    first = f" --messages {variant['first_messages']}" if variant.get("first_messages") else ""
    return (f"{prefix} build/bench/bench_variants --input <raw>/DAY --design {variant['design']} "
            f"--pass types|lookup --block {variant['block']}{first} [CAPACITY]{core} "
            "--commit <commit> --out <raw>/PROCESS-N.json")


def check_published(text, name=None):
    """What is wrong with a published file, read alone, as (code, detail) pairs."""
    fields = check_results.parse_header(text)
    if not fields:
        return [("header-missing", "no Header section")]
    found = []
    host = fields.get("host")
    if host not in HOST_FILE or fields.get("experiment") != "types":
        return [("host-label", f"{host!r}, experiment {fields.get('experiment')!r}")]
    if name is not None:
        match = FILE_NAME.match(name)
        if match is None or match.group(1) != HOST_FILE[host]:
            found.append(("host-label", f"{name} holds a run on {host}"))
    if not re.match(r"^[0-9a-f]{12} ", fields.get("commit", "")):
        found.append(("commit-missing", repr(fields.get("commit"))))
    found += list(check_results.check_load(fields))
    found += list(check_results.check_private(text, fields))
    if host == MAC:
        if fields.get("pinning", "").startswith("pinned"):
            found.append(("pin-claimed", "macOS cannot pin"))
        if fields.get("interference") != INTERFERENCE:
            found.append(("interference-unsaid", repr(fields.get("interference"))))
    passes, sums = parse_passes(text), parse_sums(text)
    if not passes or not sums:
        return found + [("table-missing", "no timed pass or no block sums")]
    types, day = parse_mix(text)
    try:
        sliced = not fields.get("slice", "").startswith("the whole day")
        pinned = whole_day_counts(fields.get("dataset", ""), sliced)
        if day != pinned:
            found.append(("day-mix-differs", "the whole day's counts are not the manifest's"))
        mix, results, lines, summary = computed(passes, sums, types, day)
    except (ReportError, KeyError, ValueError, ZeroDivisionError, StopIteration,
            statistics.StatisticsError) as error:
        return found + [("table-unusable", repr(error))]
    counts = counts_of(sums)[0]
    by_column = {name: 0 for name in COLUMNS}
    for kind, number in types.items():
        by_column[kind if kind in COLUMNS else OTHER] += number
    if [by_column[name] for name in COLUMNS] != counts:
        found.append(("mix-differs", "the mix is not what the blocks counted"))
    for section, want in (("The mix", mix), ("Results", results), ("Finding", lines)):
        if check_results.section(text, section) != "\n" + "\n".join(want) + "\n\n":
            found.append((f"{section.lower().replace(' ', '-')}-differ",
                          f"{section} is not what the passes and sums give"))
    if fields.get("summary") != summary:
        found.append(("summary-differs", f"{fields.get('summary')!r} against {summary!r}"))
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", type=Path, nargs="+", default=[])
    parser.add_argument("--load", type=Path, nargs="+", default=[])
    parser.add_argument("--machine-check", type=Path, nargs="+", default=[])
    parser.add_argument("--host-state", type=Path, nargs="+", default=[])
    parser.add_argument("--sampler", type=Path)
    parser.add_argument("--prediction")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--run-log", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args(argv)
    if args.check is None and (args.out is None or not args.record or not args.prediction):
        parser.error("wanted --check FILE, or the raw records, --prediction and --out")
    try:
        if args.check is not None:
            violations = check_published(args.check.read_text(encoding="utf-8"), args.check.name)
            for code, detail in violations:
                print(f"types_report: FAIL {code}: {detail}")
            if violations:
                return 1
            print(f"types_report: ok {args.check.name}")
            return 0
        records = [json.loads(path.read_text(encoding="utf-8")) for path in args.record]
        stderrs = [path.read_text(encoding="utf-8", errors="replace") for path in args.load]
        if len(records) != len(stderrs):
            raise ReportError("wanted one --load for each --record")
        checks = [path.read_text(encoding="utf-8") for path in args.machine_check]
        states = [path.read_text(encoding="utf-8") for path in args.host_state]
        sampler = args.sampler.read_text(encoding="utf-8") if args.sampler else None
        text, run_id, summary = render(records, stderrs, args.prediction, checks, states, sampler)
        violations = check_published(text, args.out.name)
        for code, detail in violations:
            print(f"types_report: FAIL {code}: {detail}")
        if violations:
            return 1
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        if args.run_log:
            commit = check_results.parse_header(text)["commit"]
            system = "Linux" if records[0]["header"]["host"] == DEBIAN else "Darwin"
            with open(args.run_log, "a", encoding="utf-8") as log:
                log.write(f"| {run_id} | {args.prediction} | {commit} | {system} | published: "
                          f"{summary} |\n")
        print(f"types_report: wrote {args.out.name}")
        return 0
    except (ReportError, variants_report.ReportError, OSError, ValueError, KeyError, IndexError,
            TypeError, ZeroDivisionError, StopIteration) as error:
        print(f"types_report: unusable input: {error!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
