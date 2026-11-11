#!/usr/bin/env python3
"""Writes the results of the book's hot-loop experiments from bench_variants records.

    variants_report.py --experiment lookup|prefetch|dispatch|layout|structure --record RUN.json... \\
        --load STDERR... --prediction P-NNNN --out results/EXPERIMENT_HOST_DAY.md \\
        [--machine-check BEFORE AFTER] [--host-state START END] [--sampler LOG] \\
        [--run-log results/run_log.md]
    variants_report.py --check FILE

Each record is one fresh process of one variant, and each --load is the stderr measure.sh
wrote around it, in the same order. On debian the machine check before and after, and the
host's state at the start and end (uptime, perf_event_paranoid, nice), are required. On
macOS, where nothing pins the benchmark and nothing holds other work off the cores, the log
of a CPU sampler that ran beside it is required, and a process is published only if the
sampler ran over its whole span and saw no other process above 20% CPU in it.

  lookup    the order lookup apart from the rest of the message: per arm, the check passes
            less the answer passes over the same day (bench/lookup_script.hpp).
  prefetch  E8: the look-ahead D against D = 0. Refuted if no D gains 10%.
  dispatch  E7: switch, table, threaded and predicate-only. Refuted if the branch misses a
            message are flat across the forms that dispatch, within 0.05 a message.
  layout    carteret's layout experiments on this book: the identity hash and separate
            chaining each against the multiply-shift open-addressing hash, and a level search
            that walks 16 entries or none before it binary-searches against one that walks
            the whole side.
  structure where the DRAM demand fills fall: one stage of the look-ahead for one kind of
            message at a time against none, each stage reading one structure. A structure is
            named the bottleneck only if taking its stage off the demand path takes 40% or
            more of the fills with it.

Every figure in the file comes from its own table of every timed pass, so --check rebuilds
the results and the finding from that table and fails on any difference. It also fails on
a host label that does not match the file name, a debian run that was not pinned to core 4
under the performance governor, a missing or failed machine check, perf_event_paranoid other
than 2 or a nice other than 0 at either end, a load line that is not measure.sh's, a line
that names `cache-misses` without saying it counts L2 misses or `r0843` without saying it is a
floor on DRAM demand fills, a macOS file that does not say its processes were sampled
quiet, and anything private.

Each violation prints as `variants_report: FAIL <code>: <detail>`.

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

DEBIAN = "debian"
MAC = "macOS (M4 Max)"
HOST_FILE = {DEBIAN: "debian", MAC: "mac"}
L2 = "cache-misses (L2 misses on Zen 1)"
FILLS = "r0843:u (a floor on DRAM demand fills)"
EVENTS = {
    DEBIAN: ("cycles", "instructions", "branch-misses", L2, FILLS),
    MAC: ("cycles (thread_selfcounts, user+kernel)",
          "instructions (thread_selfcounts, user+kernel)"),
}
SHORT = {"cycles": "cycles", "instructions": "instructions", "branch-misses": "branch misses",
         L2: "`cache-misses`, L2 misses", FILLS: "`r0843:u`, floor on DRAM demand fills",
         "cycles (thread_selfcounts, user+kernel)": "cycles, user and kernel",
         "instructions (thread_selfcounts, user+kernel)": "instructions, user and kernel"}
NAME_RULES = ((re.compile(r"cache-misses"), "L2 miss"),
              (re.compile(r"r0843"), "floor on DRAM demand fills"))
FORMS = ("switch", "table", "threaded", "predicate")
BOOK_FORMS = ("switch", "table", "threaded")
DESIGNS = ("paged-flat", "hash-flat", "paged-linked", "hash-linked")
LAYOUT_DESIGNS = ("hash-flat", "identity-flat", "chained-flat", "paged-flat", "paged-flat-scan16",
                  "paged-flat-bisect")
# Each experiment of the layout run: its arm, and the arm it is set against.
LAYOUT_PAIRS = (("hash policy", "identity-flat", "hash-flat"),
                ("open addressing against chaining", "chained-flat", "hash-flat"),
                ("search window of 16", "paged-flat-scan16", "paged-flat"),
                ("search window of 0", "paged-flat-bisect", "paged-flat"))
STRUCTURE_DESIGN = "paged-flat"
STRUCTURE_DISTANCE = 16
# Each variant of the structure run, and the structure its one stage reads on paged-flat.
STRUCTURES = {
    "D=0": "none: no look-ahead",
    "named:0": "index directory, for messages that name an order",
    "named:1": "index slot, for messages that name an order",
    "named:2": "order record",
    "named:3": "level record",
    "added:0": "index directory, for adds",
    "added:1": "index slot, for adds",
    "added:2": "side record",
    "added:3": "level array, its best end",
    "all:all": "every stage, every kind: E8's look-ahead",
}
BOTTLENECK_SHARE = 0.40
E8_GAIN = 0.10
E7_FLAT = 0.05
SAMPLE_GAP = 5  # seconds: the sampler writes a line every 2 s, so a longer gap is a stop
FOREIGN_CPU = 20.0
INTERFERENCE = ("sampled every 2 s over each process's whole span; no other process above "
                "20% CPU during any of them (docs/NOTES.md, \"The Mac rule for other work\")")
TITLES = {"lookup": "The order lookup apart from the level work",
          "prefetch": "E8: prefetch look-ahead on the book build",
          "dispatch": "E7: dispatch form",
          "layout": "carteret's layout experiments on this book",
          "structure": "Where the DRAM demand fills fall, by structure"}
PASS_ROW = re.compile(r"^\| `([^`]+)` \| (\d+) \| (check|answer|replay) \| ([0-9,]+) \| "
                      r"([0-9.]+) \| ([0-9,]+) \| (.*) \|$")
FILE_NAME = re.compile(r"^(lookup|prefetch|dispatch|layout|structure)_(debian|mac)_(\d{8})\.md$")

METHOD = {
    "lookup": """\
Each process maps the day, reads every page and finds every page resident, then runs one
recording pass on a fresh book whose index is wrapped by `ScriptedIndex`
(`bench/lookup_script.hpp`): every `find()` the book makes is the real index's, and its
answer goes into a script, four bytes a lookup, in an arena built before the pass. The
recording pass is also the warm-up. Then four timed passes, each on a fresh book, in the
order check, answer, answer, check:

- **check**: every `find()` is the real index's, and the script's next entry is read and
  compared with it;
- **answer**: every `find()` is the script's next entry, and the index is not searched.

Everything else is the book as it ships, in both: the frame walk, the dispatch, every
insert and erase of the real index, the order records and the level work. So the check
passes less the answer passes, over the lookups the day makes, is what the lookups cost
the book where they sit, with whatever the rest of the message leaves in the caches.

What the difference holds: the index's own loads (the paged index's directory entry and
page slot; the hash's probe of its slots), and for the hash, its read of the order record
to confirm the key. What it does not hold: the order record's own line, which both passes
read when they first touch the order after the lookup, so the hash's confirming read
moves that miss earlier rather than adding one; the script's sequential read, which both
passes make; and the level search, which is level work. Every check pass compares every
entry with the real find, and a pass with any mismatch is refused.""",
    "prefetch": """\
Each process maps the day, reads every page and finds every page resident, then runs a
warm-up pass and one timed pass, each on a fresh book, with the frames walked by
`itch::LookaheadReader` (`include/itch/lookahead.hpp`) at look-ahead D. D = 0 is the plain
frame reader. For D > 0, before the reader hands over a frame it asks the book,
`FastBook::prefetch_stage()`, to start one load of a frame ahead of it: stage 0, the index's
first load, for the frame D ahead; each later stage, which reads what the stage before
fetched and asks for the next line of the chain, for a frame nearer, at max(K - s, D >> s)
ahead, K being the arm's number of stages. On `paged-flat`, K = 4: the directory entry, the
page slot, the order record, the order's level; for a new order the last two are the
side's record and the end of its key block. The hints only read the book, and the book
ends every message as without them (`tests/test_book_variants.cpp`, `itch_oracle
--prefetch`).""",
    "dispatch": """\
Each process maps the day, reads every page and finds every page resident, then runs a
warm-up pass and one timed pass, each on a fresh book, in one form (`bench/dispatch.hpp`):

- **switch**: `FastBook::apply()`, one `switch` on the type byte, as the book ships;
- **table**: 256 handlers indexed by the type byte, one indirect call a message, each
  handler `FastBook::apply_as<Type>()`;
- **threaded**: each handler applies its message, walks to the next frame and tail-calls
  that frame's handler through the same table with `[[clang::musttail]]`, so that every
  handler has its own indirect branch;
- **predicate**: no dispatch and no book, the frame walk and the header fields every
  message has (type, locate, tracking number, timestamp) folded into a sum: the floor.

The first three hand the same messages to the same book and leave it in the same state
(`tests/test_book_variants.cpp`).""",
    "layout": """\
Each process maps the day, reads every page and finds every page resident, then runs a
warm-up pass and one timed pass, each on a fresh book of one arm, through `FastBook::apply()`
with no look-ahead. carteret ran three layout experiments on its book; each is repeated here
by changing one policy of this book and holding the rest:

- **hash policy**: `identity-flat` against `hash-flat`. The same open-addressing table, at
  most half full, with linear probing and backward-shift deletion; the home slot is the
  reference's own low bits (`IdentityHashIndex`) in place of the multiply-shift's top bits
  (`HashIndex`).
- **open addressing against chaining**: `chained-flat` against `hash-flat`. The same
  multiply-shift and as many buckets as the table has slots; each bucket holds the first
  order of a chain, and each order's link to the next sits in an array beside the order
  pool (`ChainedIndex`).
- **window width against overflow**: this book has no price window and so no overflow.
  Every level of a side is an entry of one sorted array, searched from the best end, so a far
  price costs a longer search, never a second structure. The nearest real experiment is
  how far the search walks: `paged-flat-scan16` reads 16 entries, one line of keys, and then
  binary-searches the rest of the side; `paged-flat-bisect` binary-searches the whole side;
  `paged-flat`, as shipped, walks until it finds the place. Every one finds the same place
  (`tests/test_book.cpp`). How often a price falls outside carteret's window on this day is
  counted in `results/window_12302019.md`.

Every arm gives the same answers on every message: each passed the oracle over the whole
day (docs/NOTES.md).""",
    "structure": """\
Each process maps the day, reads every page and finds every page resident, then runs a
warm-up pass and one timed pass, each on a fresh `paged-flat` book. `D=0` reads frames with
no look-ahead. Every other variant reads them through `itch::LookaheadReader` 16 frames ahead
with the book wrapped in `StageFilter` (`bench/stage_filter.hpp`), which passes on one stage
of `FastBook::prefetch_stage()` for one kind of message and drops every other: `named` is E,
C, X, D and U, which name a live order, and `added` is A and F. Each stage asks for the line
of one structure, after the stages before it:

| Stage | For a message that names an order | For an add |
|---|---|---|
| 0 | the paged index's directory entry (for U, the new reference's too) | the directory entry of the new reference |
| 1 | the page slot | the page slot the duplicate check reads |
| 2 | the order's record | the side's record |
| 3 | the order's level record | the best end of the side's key array |

A stage that is started early turns the demand load `apply()` would make on that line into
a hit, or into a software prefetch's fill, which is not a demand fill and is not counted.
So `D=0`'s fills less one variant's are the demand fills from DRAM that the variant's
structure takes, as `r0843:u` sees them, a floor on DRAM demand fills. `all:all`
starts every stage for every kind, E8's look-ahead, and what it leaves is what no stage
reaches: the FIFO neighbours an add or a removal links, the entries a level's arrival or
departure shifts, the free lists, the page counts, the input, and any hint that came too
late or was evicted before its use.""",
}

CAVEATS = {
    "lookup": """\
- **Cost in place, not alone.** The figure is what the lookups add to the whole message,
  so it includes how the lookup's misses overlap with the rest of the message's work. A
  lookup timed alone would cost something else.
- **Not the order record.** Jane Street's "one dereference" is reference to order; here
  the order record's line is outside the figure, in both passes, and only the index's own
  structure is in it.
- **The script is read in both passes.** A check pass reads one more entry than nothing
  and compares it; that compare is in the difference, a few instructions a lookup.""",
    "prefetch": """\
- **The whole pass.** Counters run over the whole timed pass, the reader and its hints
  included, so a hint's own instructions are in the figures.
- **One day, one host per file.** The best D on one host need not be the best on another.""",
    "dispatch": """\
- **Branch misses are the whole pass's.** The forms differ in where the indirect branch
  sits, not in what the book does, so the difference between forms is the dispatch's;
  the absolute count holds the book's own branches too.
- **The threaded form needs `[[clang::musttail]]`.** On a compiler without it, GCC 14 among
  them, the form is not built and asking for it exits with an error.""",
    "layout": """\
- **One day, one host per file.** Each figure is one arm's whole timed pass, parse
  included, on this day and this host. Which arm wins depends on the caches and the memory
  system; the two hosts are separate files and never one figure.
- **Arms of this book, not carteret's book.** carteret's book keeps levels in a price window
  with a `std::map` beside it and its order records elsewhere; the arms here change one
  policy of this book. A gap that differs from carteret's is a fact about the two books as
  much as about the policy.
- **Counters are the whole pass's.** On debian, `cache-misses` counts L2 misses and
  `r0843:u` is a floor on DRAM demand fills; neither is a last-level miss count, and no
  L3 counter is open to a normal user there. On macOS only cycles and instructions are read.""",
    "structure": """\
- **A floor of a floor.** `r0843:u`, a floor on DRAM demand fills, never counts a prefetch's.
  A drop here is the fills a stage's hint took off the demand path; a hint that came too late
  still leaves a demand fill, so each figure is at most the structure's fills.
- **Moved, not removed.** Stages 1 to 3 read the lines before them to find their address,
  as demand loads, 16 frames or fewer ahead; those loads still count, at another message, so
  the whole pass is the unit, and no figure here is split by message type.
- **Two structures can share a line.** A page slot or level record that another message's
  stage already brought in is not counted twice, so the drops need not add up to the drop
  of `all:all`, and the file says by how much they do not.
- **L2 misses do not split this way.** `cache-misses` counts the hints' own misses in L2
  as well as demand misses, so its drop is not a structure's misses, and the file does not
  attribute it. The column is shown as counted.
- **No L3 counter.** Nothing here counts last-level misses: the L3 counters are not open to
  a normal user on this host. A DRAM demand fill is a last-level miss, so the drops are a
  floor on the last-level misses each structure takes, and not more.""",
}


class ReportError(Exception):
    """The input cannot become a results file."""


def median(values):
    return statistics.median(values)


def cv(values):
    if len(values) < 2 or statistics.mean(values) == 0:
        return 0.0
    return statistics.stdev(values) / statistics.mean(values)


def load_lines(stderr_text, host):
    start = stamp_load.one_line(stamp_load.START, stderr_text, "start")
    end = stamp_load.one_line(stamp_load.END, stderr_text, "end")
    return tuple(stamp_load.HOST.sub(f"host={host}", line) for line in (start, end))


def timestamp(line):
    match = check_results.TIMESTAMP.search(line)
    if match is None:
        raise ReportError(f"no timestamp in {line!r}")
    return match.group(1)


def variant_of(record, experiment):
    variant = record["variant"]
    if experiment in ("lookup", "layout"):
        return variant["design"]
    if experiment == "structure":
        if variant["prefetch"] == 0:
            return "D=0"
        return f"{variant['kinds']}:{variant['stages']}"
    if experiment == "prefetch":
        return f"D={variant['prefetch']}"
    return variant["dispatch"]


def variant_order(experiment, name):
    if experiment == "lookup":
        return DESIGNS.index(name)
    if experiment == "layout":
        return LAYOUT_DESIGNS.index(name)
    if experiment == "structure":
        return list(STRUCTURES).index(name)
    if experiment == "prefetch":
        return int(name[2:])
    return FORMS.index(name)


def rows_of(records, experiment, events):
    """Every timed pass, as the file's table holds it."""
    rows = []
    repetitions = {}
    for record in records:
        name = variant_of(record, experiment)
        repetitions[name] = repetitions.get(name, 0) + 1
        for one in record["passes"]:
            values = {item["name"]: item for item in one["counters"]}
            counts = []
            for event in events:
                item = values.get(event)
                if item is None or item["state"] != "measured":
                    raise ReportError(f"{name}: {event} was not measured: {item}")
                counts.append(int(item["value"]))
            rows.append({"variant": name, "repetition": repetitions[name], "mode": one["mode"],
                         "messages": one["messages"], "seconds": round(one["seconds"], 6),
                         "lookups": one["lookups"], "counts": counts})
    return rows


def per(value, count):
    return value / count if count else 0.0


def fmt(value, places=3):
    return f"{value:,.{places}f}"


def grouped(rows):
    out = {}
    for row in rows:
        out.setdefault(row["variant"], {}).setdefault(row["repetition"], []).append(row)
    return out


def replay_figures(rows, events):
    """Per variant: the median rate, ns a message and each event a message, and the spread."""
    figures = {}
    for name, reps in grouped(rows).items():
        rates, ns, counts = [], [], [[] for _ in events]
        for passes in reps.values():
            one = passes[0]
            rates.append(one["messages"] / one["seconds"])
            ns.append(one["seconds"] * 1e9 / one["messages"])
            for index in range(len(events)):
                counts[index].append(per(one["counts"][index], one["messages"]))
        figures[name] = {"rate": median(rates), "min": min(rates), "max": max(rates),
                         "cv": cv(rates), "ns": median(ns), "reps": len(rates),
                         "counts": [median(values) for values in counts]}
    return figures


def lookup_figures(rows, events):
    """Per arm: check less answer, a lookup and a message, and as a share of the check pass."""
    figures = {}
    for name, reps in grouped(rows).items():
        per_lookup, per_message, share, check_ns, answer_ns = [], [], [], [], []
        counts = [[] for _ in events]
        lookups = None
        for passes in reps.values():
            checks = [one for one in passes if one["mode"] == "check"]
            answers = [one for one in passes if one["mode"] == "answer"]
            if not checks or not answers:
                raise ReportError(f"{name}: a repetition without both modes")
            messages = checks[0]["messages"]
            lookups = checks[0]["lookups"]
            check_s = statistics.mean(one["seconds"] for one in checks)
            answer_s = statistics.mean(one["seconds"] for one in answers)
            per_lookup.append((check_s - answer_s) * 1e9 / lookups)
            per_message.append((check_s - answer_s) * 1e9 / messages)
            share.append((check_s - answer_s) / check_s)
            check_ns.append(check_s * 1e9 / messages)
            answer_ns.append(answer_s * 1e9 / messages)
            for index in range(len(events)):
                check_count = statistics.mean(one["counts"][index] for one in checks)
                answer_count = statistics.mean(one["counts"][index] for one in answers)
                counts[index].append((check_count - answer_count) / lookups)
        figures[name] = {"per_lookup": median(per_lookup), "min": min(per_lookup),
                         "max": max(per_lookup), "per_message": median(per_message),
                         "share": median(share), "check_ns": median(check_ns),
                         "answer_ns": median(answer_ns), "lookups": lookups,
                         "reps": len(per_lookup), "counts": [median(v) for v in counts]}
    return figures


LAYOUT_BASE = {"identity-flat": "hash-flat", "chained-flat": "hash-flat",
               "paged-flat-scan16": "paged-flat", "paged-flat-bisect": "paged-flat",
               "hash-flat": "paged-flat"}


def layout_section(names, figures, heads):
    out = ["Medians over the repetitions. Each arm's rate is set against the arm it changes one "
           "policy of; `hash-flat` against `paged-flat`, the default. The counter columns are "
           "each event over the whole timed pass, a message.", "",
           "| Arm | Repetitions | Median messages/s | Min | Max | CV | ns a message | Against "
           "| Over it | " + " | ".join(f"{head}, a message" for head in heads) + " |",
           "|---|---|---|---|---|---|---|---|---|" + "---|" * len(heads)]
    for name in names:
        f = figures[name]
        base = LAYOUT_BASE.get(name)
        ratio = (f"{f['rate'] / figures[base]['rate']:.4f}" if base in figures else "n/a")
        out.append(f"| `{name}` | {f['reps']} | {f['rate']:,.0f} | {f['min']:,.0f} "
                   f"| {f['max']:,.0f} | {f['cv'] * 100:.2f}% | {fmt(f['ns'], 2)} "
                   f"| {f'`{base}`' if base else 'n/a'} | {ratio} | "
                   + " | ".join(fmt(v, 4) for v in f["counts"]) + " |")
    return out


def structure_section(names, figures, events, heads):
    at = events.index(FILLS)
    base = figures["D=0"]["counts"][at]
    out = ["Medians over the repetitions. The counter columns are each event over the whole "
           "timed pass, a message. The last two columns are `D=0`'s `r0843:u`, a floor on DRAM "
           "demand fills, less the variant's, a message, and that as a share of `D=0`'s.", "",
           "| Variant | Its one stage reads | Repetitions | Median messages/s | ns a message | "
           + " | ".join(f"{head}, a message" for head in heads)
           + " | Taken off the demand path, `r0843:u`, floor on DRAM demand fills | Share |",
           "|---|---|---|---|---|" + "---|" * len(heads) + "---|---|"]
    for name in names:
        f = figures[name]
        drop = base - f["counts"][at]
        taken = "n/a" if name == "D=0" else fmt(drop, 4)
        share = "n/a" if name == "D=0" else f"{drop / base * 100:.1f}%"
        out.append(f"| `{name}` | {STRUCTURES[name]} | {f['reps']} | {f['rate']:,.0f} "
                   f"| {fmt(f['ns'], 2)} | " + " | ".join(fmt(v, 4) for v in f["counts"])
                   + f" | {taken} | {share} |")
    return out


def results_section(experiment, rows, events, host):
    names = sorted({row["variant"] for row in rows}, key=lambda n: variant_order(experiment, n))
    heads = [SHORT[event] for event in events]
    if experiment == "layout":
        figures = replay_figures(rows, events)
        return layout_section(names, figures, heads), figures
    if experiment == "structure":
        if FILLS not in events or "D=0" not in names:
            raise ReportError("the structure run needs r0843:u and a D=0 variant")
        figures = replay_figures(rows, events)
        return structure_section(names, figures, events, heads), figures
    if experiment == "lookup":
        figures = lookup_figures(rows, events)
        out = ["Medians over the repetitions. Per lookup and per message are the check passes "
               "less the answer passes; the counter columns are the same difference, a lookup.",
               "", "| Arm | Lookups | Check pass, ns a message | Answer pass, ns a message | "
               "Lookup, ns a lookup | Min | Max | Lookup, ns a message | Share of the check "
               "pass | " + " | ".join(f"{head}, a lookup" for head in heads) + " |",
               "|---|---|---|---|---|---|---|---|---|" + "---|" * len(heads)]
        for name in names:
            f = figures[name]
            out.append(f"| `{name}` | {f['lookups']:,} | {fmt(f['check_ns'], 2)} "
                       f"| {fmt(f['answer_ns'], 2)} | {fmt(f['per_lookup'], 2)} "
                       f"| {fmt(f['min'], 2)} | {fmt(f['max'], 2)} | {fmt(f['per_message'], 2)} "
                       f"| {f['share'] * 100:.1f}% | "
                       + " | ".join(fmt(v, 3) for v in f["counts"]) + " |")
        return out, figures
    figures = replay_figures(rows, events)
    base = "D=0" if experiment == "prefetch" else "switch"
    label = "Look-ahead" if experiment == "prefetch" else "Form"
    out = [f"Medians over the repetitions. The ratio is each median rate over `{base}`'s; the "
           "counter columns are each event over the whole timed pass, a message.", "",
           f"| {label} | Repetitions | Median messages/s | Min | Max | CV | ns a message | "
           f"Over `{base}` | " + " | ".join(f"{head}, a message" for head in heads) + " |",
           "|---|---|---|---|---|---|---|---|" + "---|" * len(heads)]
    for name in names:
        f = figures[name]
        ratio = f["rate"] / figures[base]["rate"] if base in figures else float("nan")
        out.append(f"| `{name}` | {f['reps']} | {f['rate']:,.0f} | {f['min']:,.0f} "
                   f"| {f['max']:,.0f} | {f['cv'] * 100:.2f}% | {fmt(f['ns'], 2)} "
                   f"| {ratio:.4f} | " + " | ".join(fmt(v, 4) for v in f["counts"]) + " |")
    return out, figures


def layout_finding(figures, events):
    lines, parts = [], []
    for label, arm, base in LAYOUT_PAIRS:
        if arm not in figures or base not in figures:
            continue
        a, b = figures[arm], figures[base]
        gain = a["rate"] / b["rate"] - 1
        apart = a["min"] > b["max"] or a["max"] < b["min"]
        line = (f"- {label.capitalize()}: `{arm}` {gain * 100:+.2f}% against `{base}` "
                f"({a['rate']:,.0f} against {b['rate']:,.0f} messages/s); "
                + ("their ranges over the repetitions do not overlap"
                   if apart else "their ranges over the repetitions overlap, so the two are "
                                 "not told apart") + ".")
        at = events.index("instructions") if "instructions" in events else None
        if at is not None and FILLS not in events:
            line += (f" Instructions a message, user and kernel: {fmt(a['counts'][at], 1)} "
                     f"against {fmt(b['counts'][at], 1)}.")
        lines.append(line)
        if FILLS in events:
            fills, l2 = events.index(FILLS), events.index(L2)
            branch, instr = events.index("branch-misses"), events.index("instructions")
            lines.append(f"  - a message: `r0843:u`, a floor on DRAM demand fills, "
                         f"{fmt(a['counts'][fills], 3)} against {fmt(b['counts'][fills], 3)}; "
                         f"`cache-misses`, L2 misses, {fmt(a['counts'][l2], 3)} against "
                         f"{fmt(b['counts'][l2], 3)}; instructions {fmt(a['counts'][instr], 1)} "
                         f"against {fmt(b['counts'][instr], 1)}; branch misses "
                         f"{fmt(a['counts'][branch], 3)} against {fmt(b['counts'][branch], 3)}.")
        parts.append(f"{label} {gain * 100:+.2f}% ({arm} over {base}"
                     + ("" if apart else ", ranges overlap") + ")")
    return lines, "; ".join(parts)


def structure_finding(figures, events):
    at = events.index(FILLS)
    base = figures["D=0"]["counts"][at]
    singles = {name: base - f["counts"][at] for name, f in figures.items()
               if name not in ("D=0", "all:all")}
    lines = [f"- `D=0` makes {fmt(base, 3)} `r0843:u` a message, a floor on DRAM demand fills."]
    for name, drop in sorted(singles.items(), key=lambda item: -item[1]):
        lines.append(f"- `{name}`, {STRUCTURES[name]}: {fmt(drop, 3)} a message taken off the "
                     f"demand path, {drop / base * 100:.1f}% of `D=0`'s.")
    if "all:all" in figures:
        left = figures["all:all"]["counts"][at]
        lines.append(f"- The single stages together take {fmt(sum(singles.values()), 3)} a "
                     f"message; every stage at once (`all:all`) takes {fmt(base - left, 3)}, "
                     f"and leaves {fmt(left, 3)}, {left / base * 100:.1f}% of `D=0`'s, on "
                     "lines no stage reaches or reaches in time.")
    largest = max(singles, key=singles.get)
    share = singles[largest] / base
    if share >= BOTTLENECK_SHARE:
        lines.append(f"- The rule fixed before the run names a bottleneck only at 40% or more "
                     f"of the fills: **{STRUCTURES[largest]}** (`{largest}`) takes "
                     f"{share * 100:.1f}%, so it is the bottleneck by that rule.")
        verdict = f"bottleneck by the 40% rule: {STRUCTURES[largest]}"
    else:
        lines.append(f"- The rule fixed before the run names a bottleneck only at 40% or more "
                     f"of the fills: no single structure reaches it. The largest, "
                     f"{STRUCTURES[largest]} (`{largest}`), takes {share * 100:.1f}%, so no "
                     "bottleneck is claimed.")
        verdict = "no structure reaches the 40% rule, no bottleneck claimed"
    summary = (f"largest single structure {STRUCTURES[largest]} at {share * 100:.1f}% of the "
               f"floor on DRAM demand fills; {verdict}")
    return lines, summary


def finding(experiment, figures, events, host):
    """The finding, worked from the figures alone, and a one-line summary of it."""
    if experiment == "layout":
        return layout_finding(figures, events)
    if experiment == "structure":
        return structure_finding(figures, events)
    if experiment == "prefetch":
        base = figures["D=0"]["rate"]
        gains = {name: f["rate"] / base - 1 for name, f in figures.items() if name != "D=0"}
        best = max(gains, key=gains.get)
        refuted = gains[best] < E8_GAIN
        lines = [f"- Best look-ahead: `{best}`, {gains[best] * 100:+.2f}% over `D=0` "
                 f"({figures[best]['rate']:,.0f} against {base:,.0f} messages/s).",
                 "- Every look-ahead against `D=0`: " + ", ".join(
                     f"`{name}` {gain * 100:+.2f}%" for name, gain in
                     sorted(gains.items(), key=lambda item: int(item[0][2:]))) + ".",
                 f"- E8's kill criterion, refuted if under {E8_GAIN * 100:.0f}% gain at any D: "
                 + ("**refuted**: no look-ahead gains 10%." if refuted else
                    f"**not refuted**: `{best}` gains {gains[best] * 100:.2f}%.")]
        summary = (f"best look-ahead {best} at {gains[best] * 100:+.2f}% over D=0; E8 "
                   + ("refuted (under 10% at every D)" if refuted else "not refuted"))
        return lines, summary
    if experiment == "dispatch":
        base = figures["switch"]["rate"]
        lines = ["- Against `switch`: " + ", ".join(
            f"`{name}` {(f['rate'] / base - 1) * 100:+.2f}%" for name, f in
            sorted(figures.items(), key=lambda item: FORMS.index(item[0])) if name != "switch")
            + "."]
        book = [name for name in BOOK_FORMS if name in figures]
        floor = figures.get("predicate")
        if floor is not None:
            lines.append(f"- The floor, `predicate`: {fmt(floor['ns'], 2)} ns a message; the "
                         "book forms stand " + ", ".join(
                             f"`{name}` {fmt(figures[name]['ns'] - floor['ns'], 2)}"
                             for name in book) + " ns a message above it.")
        if "branch-misses" in events:
            at = events.index("branch-misses")
            misses = {name: figures[name]["counts"][at] for name in book}
            spread = max(misses.values()) - min(misses.values())
            refuted = spread < E7_FLAT
            lines.append("- Branch misses a message: " + ", ".join(
                f"`{name}` {fmt(value, 4)}" for name, value in misses.items())
                + f"; spread {fmt(spread, 4)}.")
            lines.append(f"- E7's kill criterion, refuted if the mispredict count is flat "
                         f"across forms, within {E7_FLAT} a message: "
                         + ("**refuted**: the forms are within 0.05 a message of each other."
                            if refuted else
                            f"**not refuted**: the forms differ by {fmt(spread, 4)} a message."))
            verdict = "refuted" if refuted else "not refuted"
            summary = (f"fastest form {max(book, key=lambda n: figures[n]['rate'])}; "
                       f"branch misses a message spread {spread:.4f} across the book forms; "
                       f"E7 {verdict}")
        else:
            lines.append("- E7's kill criterion needs a branch-miss count, which this host's "
                         "harness does not have: not judged here.")
            summary = (f"fastest form {max(book, key=lambda n: figures[n]['rate'])}; "
                       "E7 not judged on this host, no branch-miss counter")
        return lines, summary
    lines = []
    for name, f in sorted(figures.items(), key=lambda item: DESIGNS.index(item[0])):
        lines.append(f"- `{name}`: the lookups cost {fmt(f['per_lookup'], 2)} ns each "
                     f"(min {fmt(f['min'], 2)}, max {fmt(f['max'], 2)}), "
                     f"{fmt(f['per_message'], 2)} ns a message, {f['share'] * 100:.1f}% of "
                     "the check pass.")
    if "paged-flat" in figures and "hash-flat" in figures:
        paged = figures["paged-flat"]["per_lookup"]
        hashed = figures["hash-flat"]["per_lookup"]
        lines.append(f"- The hash's lookup over the paged index's: {hashed / paged:.3f} times.")
    summary = "; ".join(f"{name} lookup {f['per_lookup']:.2f} ns, {f['share'] * 100:.1f}% of "
                        f"the pass" for name, f in sorted(
                            figures.items(), key=lambda item: DESIGNS.index(item[0])))
    return lines, summary


def pass_table(rows, events):
    heads = [SHORT[event] for event in events]
    out = ["| Variant | Repetition | Pass | Messages | Seconds | Lookups | "
           + " | ".join(heads) + " |", "|---|---|---|---|---|---|" + "---|" * len(heads)]
    for row in rows:
        out.append(f"| `{row['variant']}` | {row['repetition']} | {row['mode']} "
                   f"| {row['messages']:,} | {row['seconds']:.6f} | {row['lookups']:,} | "
                   + " | ".join(f"{count:,}" for count in row["counts"]) + " |")
    return out


def parse_pass_table(text, events):
    rows = []
    for line in (check_results.section(text, "Every timed pass") or "").splitlines():
        match = PASS_ROW.match(line)
        if match is None:
            continue
        counts = [int(cell.strip().replace(",", "")) for cell in match.group(7).split("|")]
        if len(counts) != len(events):
            raise ReportError(f"a row of {len(counts)} counts, wanted {len(events)}")
        rows.append({"variant": match.group(1), "repetition": int(match.group(2)),
                     "mode": match.group(3), "messages": int(match.group(4).replace(",", "")),
                     "seconds": float(match.group(5)),
                     "lookups": int(match.group(6).replace(",", "")), "counts": counts})
    return rows


def computed_sections(experiment, rows, events, host):
    results, figures = results_section(experiment, rows, events, host)
    lines, summary = finding(experiment, figures, events, host)
    return results, lines, summary


def judge(records, experiment):
    for record in records:
        if record["refusals"]:
            raise ReportError(f"a refused record: {record['refusals']}")
        if record["variant"]["pass"] != ("lookup" if experiment == "lookup" else "replay"):
            raise ReportError(f"a {record['variant']['pass']} record in a {experiment} report")
        if experiment == "prefetch" and record["variant"]["dispatch"] != "switch":
            raise ReportError("a look-ahead record with another form")
        if experiment == "dispatch" and record["variant"]["prefetch"] != 0:
            raise ReportError("a dispatch record with look-ahead")
        if experiment == "layout" and (record["variant"]["prefetch"] != 0
                                       or record["variant"]["dispatch"] != "switch"):
            raise ReportError("a layout record with look-ahead or another form")
        if experiment == "structure":
            variant = record["variant"]
            filtered = variant.get("filtered", False)
            if (variant["design"] != STRUCTURE_DESIGN or variant["dispatch"] != "switch"
                    or variant["prefetch"] not in (0, STRUCTURE_DISTANCE)
                    or filtered != (variant["prefetch"] != 0)
                    or variant_of(record, experiment) not in STRUCTURES):
                raise ReportError(f"a structure record of another kind: {variant}")
        for one in record["passes"]:
            if one["messages"] != record["mix"]["messages"] or one["rejected"] != 0:
                raise ReportError("a pass that did not walk the day cleanly")
            if experiment == "lookup" and (not one["script_exact"] or one["mismatches"]):
                raise ReportError("a lookup pass that did not read its script exactly")
    for field in ("host", "cpu", "kernel", "compiler", "flags", "commit", "dataset"):
        values = {record["header"][field] for record in records}
        if len(values) != 1:
            raise ReportError(f"the records differ in {field}: {sorted(values)}")


def peak_rss(records, stderrs):
    """The largest getrusage peak, and the largest /usr/bin/time one if it wrapped the run."""
    rusage = max(record["peak_rss_bytes"] for record in records) / 1024 ** 3
    timed = [int(match.group(1)) for text in stderrs
             for match in stamp_load.MAX_RSS.finditer(text)]
    line = f"{rusage:.2f} GiB, getrusage of bench_variants, largest process"
    if timed:
        line += f"; {max(timed) / 1024 ** 3:.2f} GiB, /usr/bin/time -l, largest process"
    return line


def render(experiment, records, stderrs, prediction, machine_checks, host_states,
           sampler=None):
    judge(records, experiment)
    header = records[0]["header"]
    host = header["host"]
    if host not in EVENTS:
        raise ReportError(f"no events known for host {host!r}")
    events = EVENTS[host]
    if host == DEBIAN and (len(machine_checks) != 2 or len(host_states) != 2):
        raise ReportError("a debian run needs the machine check and the host state at both ends")
    lines = [load_lines(text, host) for text in stderrs]
    if host == MAC:
        if sampler is None:
            raise ReportError("a macOS run needs the CPU sampler's log (--sampler)")
        quiet_spans(sampler, lines)
    run_id = f"{prediction}-" + timestamp(lines[0][0]).replace(":", "").replace("-", "")
    rows = rows_of(records, experiment, events)
    results, found, summary = computed_sections(experiment, rows, events, host)
    day = header.get("dataset", "").split(".", 1)[0]
    migrations = sum(record["migrations"] for record in records)
    out = [f"# {TITLES[experiment]}, {HOST_FILE[host]}, {day}", "",
           "Generated by `scripts/variants_report.py` from raw records that are not committed. "
           "Do not edit by hand.", "",
           f"Host: **{host}**. Numbers from the two hosts are never mixed.", "", "## Header", ""]
    out += [f"- {name}: {value}" for name, value in header.items()
            if name not in ("design", "page_size", "rdpmc", "counters")]
    out += [f"- experiment: {experiment}", f"- prediction: {prediction}", f"- run_id: {run_id}",
            f"- processes: {len(records)}", f"- messages: {records[0]['mix']['messages']:,}",
            f"- migrations_all_processes: {migrations}",
            f"- peak_rss: {peak_rss(records, stderrs)}",
            f"- measure_load_start: {lines[0][0]}", f"- measure_load_end: {lines[-1][1]}",
            f"- run_started: {timestamp(lines[0][0])}",
            f"- run_finished: {timestamp(lines[-1][1])}"]
    if host == MAC:
        out.append(f"- interference: {INTERFERENCE}")
    for name, state in zip(("host_state_start", "host_state_end"), host_states):
        out.append(f"- {name}: {stamp_load.HOST.sub(f'host={host}', state.strip())}")
    out += [f"- summary: {summary}", "", "## What was run", "", METHOD[experiment], "",
            "Counted on this host, over each whole timed pass, between the counter group's "
            "start and stop: " + ", ".join(SHORT[event] for event in events) + ".", "",
            "## Results", ""]
    out += results
    out += ["", "## Finding", ""]
    out += found
    out += ["", "## Every timed pass", "",
            "One row a timed pass, in the order run. Each counter is the pass's whole count. "
            "Every figure above is worked from these rows.", ""]
    out += pass_table(rows, events)
    out += ["", "## What this does not show", "", CAVEATS[experiment]]
    if machine_checks:
        out += ["", "## machine_check output", "",
                "Before the first process and after the last.", "", "```"]
        out += [text.strip() for text in machine_checks]
        out += ["```"]
    out += ["", "## measure.sh load lines", "", "```"]
    out += [line for pair in lines for line in pair]
    out += ["```", "", "## Command", "", "```", command_line(experiment, host, day), "```", ""]
    return "\n".join(out), run_id, summary


def command_line(experiment, host, day):
    prefix = "measure.sh taskset -c 4" if host == DEBIAN else "measure.sh /usr/bin/time -l"
    core = " --core 4" if host == DEBIAN else ""
    tail = {"lookup": "--pass lookup --design ARM",
            "prefetch": "--design paged-flat --prefetch D",
            "dispatch": "--design paged-flat --dispatch FORM",
            "layout": "--design ARM",
            "structure": "--design paged-flat [--prefetch 16 --stages S --kinds K]"}[experiment]
    return (f"{prefix} build/bench/bench_variants --input <raw>/{day}.NASDAQ_ITCH50 {tail}"
            f"{core} --commit <commit> --out <raw>/VARIANT-N.json")


def instant(text):
    return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")


def quiet_spans(sampler_text, lines):
    """Raises unless the sampler ran over each process's span and saw nothing else busy.

    The sampler writes `<UTC time> sample` every 2 s, and `<UTC time> FOREIGN <cpu> <command>`
    for each other process above 20% CPU at that sample. A process is quiet if a sample falls
    at or before its start and at or after its end, no two samples between are more than
    SAMPLE_GAP seconds apart, and no FOREIGN line falls from its start to its end."""
    samples, foreign = [], []
    for line in sampler_text.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) >= 2 and parts[1] == "sample":
            samples.append(instant(parts[0]))
        elif len(parts) >= 3 and parts[1] == "FOREIGN" and float(parts[2]) > FOREIGN_CPU:
            foreign.append((instant(parts[0]), line))
    samples.sort()
    for start_line, end_line in lines:
        start, end = instant(timestamp(start_line)), instant(timestamp(end_line))
        covered = [t for t in samples if start - datetime.timedelta(seconds=SAMPLE_GAP) <= t
                   <= end + datetime.timedelta(seconds=SAMPLE_GAP)]
        if not covered or covered[0] > start or covered[-1] < end or any(
                (b - a).total_seconds() > SAMPLE_GAP for a, b in zip(covered, covered[1:])):
            raise ReportError(f"the sampler did not run over the whole of {start_line!r}")
        busy = [line for t, line in foreign if start <= t <= end]
        if busy:
            raise ReportError(f"another process was busy during {start_line!r}: {busy[0]!r}")


def check_published(text, name=None):
    """What is wrong with a published file, read alone, as (code, detail) pairs."""
    fields = check_results.parse_header(text)
    if not fields:
        return [("header-missing", "no Header section")]
    found = []
    host = fields.get("host")
    experiment = fields.get("experiment")
    if host not in EVENTS or experiment not in TITLES:
        return [("host-label", f"{host!r}, experiment {experiment!r}")]
    if name is not None:
        match = FILE_NAME.match(name)
        if match is None or match.group(1) != experiment or match.group(2) != HOST_FILE[host]:
            found.append(("host-label", f"{name} holds {experiment} on {host}"))
    if not re.match(r"^[0-9a-f]{12} ", fields.get("commit", "")):
        found.append(("commit-missing", repr(fields.get("commit"))))
    found += [(code, detail) for code, detail in check_results.check_load(fields)]
    found += [(code, detail) for code, detail in check_results.check_private(text, fields)]
    if host == DEBIAN:
        if not re.match(r"pinned to core 4: .*read back \[4\]$", fields.get("pinning", "")):
            found.append(("pin-failed", fields.get("pinning", "no field")))
        if fields.get("governor") != "performance":
            found.append(("governor", fields.get("governor", "no field")))
        output = check_results.section(text, "machine_check output") or ""
        if output.count("machine_check: passed") != 2 or "machine_check: FAIL" in output:
            found.append(("machine-check-failed", "wanted a passing check before and after"))
        for state in ("host_state_start", "host_state_end"):
            line = fields.get(state, "")
            if "perf_event_paranoid=2 " not in line + " " or not re.search(r"\bnice 0\b", line):
                found.append(("host-state", f"{state}: {line!r}"))
    elif fields.get("pinning", "").startswith("pinned"):
        found.append(("pin-claimed", "macOS cannot pin"))
    if host == MAC and fields.get("interference") != INTERFERENCE:
        found.append(("interference-unsaid", repr(fields.get("interference"))))
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern, said in NAME_RULES:
            if pattern.search(line) and said not in line:
                found.append(("name-unsaid", f"line {number} names {pattern.pattern} "
                                             f"without '{said}'"))
    events = EVENTS[host]
    try:
        rows = parse_pass_table(text, events)
        if not rows:
            return found + [("table-missing", "no timed pass in the table")]
        results, lines, summary = computed_sections(experiment, rows, events, host)
    except (ReportError, KeyError, ValueError, ZeroDivisionError, statistics.StatisticsError) as error:
        return found + [("table-unusable", repr(error))]
    if check_results.section(text, "Results") != "\n" + "\n".join(results) + "\n\n":
        found.append(("results-differ", "the results are not what the table of passes gives"))
    if check_results.section(text, "Finding") != "\n" + "\n".join(lines) + "\n\n":
        found.append(("finding-differs", "the finding is not what the table of passes gives"))
    if fields.get("summary") != summary:
        found.append(("summary-differs", f"{fields.get('summary')!r} against {summary!r}"))
    return found


def read_inputs(args):
    records = [json.loads(path.read_text(encoding="utf-8")) for path in args.record]
    stderrs = [path.read_text(encoding="utf-8", errors="replace") for path in args.load]
    checks = [path.read_text(encoding="utf-8") for path in args.machine_check]
    states = [path.read_text(encoding="utf-8") for path in args.host_state]
    if not records or len(records) != len(stderrs):
        raise ReportError("wanted one --load for each --record")
    return records, stderrs, checks, states


def report_violations(violations):
    for code, detail in violations:
        print(f"variants_report: FAIL {code}: {detail}")
    return 1 if violations else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--experiment", choices=sorted(TITLES))
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
    if args.check is None and (args.out is None or not args.record or not args.prediction
                               or not args.experiment):
        parser.error("wanted --check FILE, or --experiment, the raw records, --prediction "
                     "and --out")
    try:
        if args.check is not None:
            violations = check_published(args.check.read_text(encoding="utf-8"),
                                         args.check.name)
            if violations:
                return report_violations(violations)
            print(f"variants_report: ok {args.check.name}")
            return 0
        records, stderrs, checks, states = read_inputs(args)
        sampler = args.sampler.read_text(encoding="utf-8") if args.sampler else None
        text, run_id, summary = render(args.experiment, records, stderrs, args.prediction,
                                       checks, states, sampler)
        violations = check_published(text, args.out.name)
        if violations:
            return report_violations(violations)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        if args.run_log:
            commit = check_results.parse_header(text)["commit"]
            system = "Linux" if records[0]["header"]["host"] == DEBIAN else "Darwin"
            with open(args.run_log, "a", encoding="utf-8") as log:
                log.write(f"| {run_id} | {args.prediction} | {commit} | {system} | published: "
                          f"{summary} |\n")
        print(f"variants_report: wrote {args.out.name}")
        return 0
    except (ReportError, OSError, ValueError, KeyError, IndexError, TypeError,
            ZeroDivisionError) as error:
        print(f"variants_report: unusable input: {error!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
