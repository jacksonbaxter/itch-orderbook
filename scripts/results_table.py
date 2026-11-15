#!/usr/bin/env python3
"""Generates the results tables in RESULTS.md and the README's headline from results/, and
fails if either page differs.

    results_table.py [--root DIR] [--write | --check]

With neither flag it prints every block. --write puts each between its page's two marker
lines: RESULTS.md's summary, its full tables and its table of every target missed or not
measured, README.md's headline and its count of the summary's verdicts, and WRITEUP.md's short
list of the misses; --check fails on any line there that differs from a fresh run.

The summary is one row a target of BENCHMARKS.md, in its order, with its one verdict
(one_verdict(): missed if any row its bar judges is missed or FAIL, met if every such row is met
or PASS, none if its bar judges none) and its rows counted by host and verdict; a target with
no row is held by tests (HELD_BY_TESTS) or accounted (ACCOUNTED), or refused. The number gate
holds every verdict word in prose to the same rows (target_rows()). The table of every target
missed or not measured has a row for each file, host, day or comparison, with its why in full,
each why giving what was measured before what is not established; WRITEUP.md's has one short
line a target, a plain sentence from the row that decides its verdict or the worst, and links
the full table. T-PARSE-3's row follows whether
its bar's day, 10302019, has a results file: with none, the other day's row says 10302019 is not
measured yet and that the prediction for its run is committed; with one, the row is that file's
miss, or none if it is met.

Every verdict in every results file reaches RESULTS.md, failures included. A results file
with no reader here is refused, and so is a file with a verdict no row took up, so that
neither can drop out unseen. A verdict is a **PASS**, **FAIL** or **NOT_AVAILABLE** mark, or
a row of the file's Targets table, counted by its target and its verdict's words: each book
file's whole targets table becomes rows of the Book targets table, misses with their gaps,
figures no bar on their host can judge and targets the run did not measure with the reason
included. A book benchmark file missing a field its row needs is refused as well. A
stub-book harness proof is known by its label on the line just above its Header, where
bench_driver.py writes it, not by the words anywhere in the file, and a `bench_book_` file so
labelled is refused. An oracle or trades file runs each arm of the fast book, and a
row says whether every arm gave its verdict. An arm that gave another one makes the row a
failure that names it. A UDP replay file gives a T-URING-2 row when it
holds a bisection at 1,400 bytes a datagram, T-URING-2's packet size, and a verdict in a file
at any other size is refused. Each bisection, at either size, gives each mode's largest
lossless rate to the T-URING-1 table, which has no verdict, each row labelled with its
packet size. The fuzzers' long run, a `fuzz_` file from scripts/fuzz_report.py, gives a row
beside the fault matrix's, and WRITEUP.md's table counts the Fuzzing target met only while the
file MET_BY_FILE names shows every target clean. The final review's record,
`final_review.md` from scripts/review_report.py, gives a row there too: its reviewers, findings
and outcomes, and whether it is complete. predictions.md is not a result, and
run_log.md gets its own table, every row of it, failed runs included; a line of its table that
is not a run is refused. Each row cites its file, so scripts/gate_numbers.py holds the
row's figures to that file.

The headline is a fixed choice of four or five targets, HEADLINE, each read from the results
file named beside it: its bar, what was measured, the host and the verdict all come from that
file, cut short (short_bar(), short_host()), and only the words on what each measures are
written here. A headline row whose file gives it no verdict of met or missed is refused, and so
is a headline with no missed target in it.

A backtest target is compared in its bar's unit. T-BT-1's bar is in events a second, engine
events/s in the results files, and T-BT-3's in ITCH messages a second. The bar and its unit
are read from BENCHMARKS.md; a unit there other than BACKTEST_UNITS', or a results file whose
targets table gives another bar, is refused. The backtest table's median in that unit comes
from the file's Summary row (`Engine events/s` or `Replay rate, messages/s`), with the other
rate labelled beside it; the headline's T-BT-1 row is the file's own targets row. That row's
figures and verdict are held to the Summary by check_results.py.

Each violation prints as `results_table: FAIL <place>: <detail>`.

Exit codes: 0 passed, 1 a page differs or a results file cannot be read, 2 bad usage.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
BEGIN = "<!-- results table: begin, generated by scripts/results_table.py, do not edit -->"
END = "<!-- results table: end -->"
HEAD_BEGIN = "<!-- headline: begin, generated by scripts/results_table.py, do not edit -->"
HEAD_END = "<!-- headline: end -->"
SUMMARY_BEGIN = "<!-- summary: begin, generated by scripts/results_table.py, do not edit -->"
SUMMARY_END = "<!-- summary: end -->"
FULL_MISS_BEGIN = ("<!-- every target missed: begin, generated by scripts/results_table.py, do "
                   "not edit -->")
FULL_MISS_END = "<!-- every target missed: end -->"
RESULTS_PAGE = "RESULTS.md"
README_PAGE = "README.md"
NOT_RESULTS = {"predictions.md", "run_log.md"}
VERDICT_MARK = re.compile(r"\*\*(?:Verdict: )?(PASS|FAIL|NOT_AVAILABLE)\*\*")
TRADE_CHECKS = {"T-TRADE-1", "T-TRADE-2", "T-TRADE-3", "T-TRADE-5", "PRINTS"}
HARNESS_LABEL = "Stub-book harness proof, not a book result."
# T-ARENA-1's verdict in an allocator file's targets table: these words, then its fault check.
ARENA_VERDICT = "no bar to meet; fault check "
SHOWN = {"PASS": "PASS", "FAIL": "**FAIL**", "NOT_AVAILABLE": "**NOT AVAILABLE**"}
RUN_HEADS = ("Run", "Prediction", "Commit", "OS", "Outcome")
# A run's id: its prediction, the time it began, and for a run of many processes or a
# window, what each row is of, as P-0601-20261106T031311Z_scalar-w2-1. The fuzzers' long run
# is a check, not a measurement, and has no prediction: its id is scripts/fuzz_report.py's,
# fuzz-30min and the time it began.
RUN_ID = r"(?:P-\d+|fuzz-30min)-[\w-]+"


class TableError(Exception):
    """A results file cannot be turned into rows."""


def find(pattern, text, name):
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise TableError(f"results/{name}: nothing matches {pattern}")
    return match


def table_row(label, text, name):
    """The cells of the Markdown row whose first cell is label."""
    row = find(rf"^\| `?{re.escape(label)}`? \|(.*)\|$", text, name)
    return [cell.strip() for cell in row[1].split("|")]


def day_of(name):
    """The day's manifest key: its date, and a venue suffix such as _BX for another feed."""
    return find(r"(\d{8}(?:_[A-Z]+)?)", name, name)[1]


def header_host(text, name):
    return find(r"^- Host: (.*?)\. ", text, name)[1]


def header_commit(text, name):
    return find(r"^- [Cc]ommit: (\w+)", text, name)[1]


def arm_verdicts(text, name):
    """Each arm's verdict under each column of the file's table of every arm."""
    section = find(r"^## Every arm of the fast book\n((?s:.*?))(?=^## |\Z)", text, name)[1]
    lines = [line for line in section.splitlines() if line.startswith("| ")]
    if len(lines) < 2 or not lines[0].startswith("| Arm |"):
        raise TableError(f"results/{name}: the table of every arm has no rows")
    heads = [cell.strip() for cell in lines[0].strip("|").split("|")]
    arms = {}
    for line in lines[1:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        arms[cells[0].strip("`")] = {head: cell.split(",")[0] for head, cell in zip(heads, cells)}
    return arms


def across_arms(arms, column, verdict, detail):
    """The verdict and detail of a row once every arm's verdict in its column is read."""
    other = [f"{arm} {cells.get(column, 'missing')}" for arm, cells in arms.items()
             if cells.get(column) != verdict]
    if other:
        return "FAIL", f"{detail}; the arms disagree: {', '.join(other)}"
    return verdict, f"{detail}; the same on {len(arms)} arm{'s' if len(arms) != 1 else ''}"


def in_all_designs(measured):
    """A headline cell's "the same on 4 arms" in words a reader knows."""
    return re.sub(r"; the same on (\d+) arms$",
                  lambda match: f"; the same in all {in_words(int(match[1]))} designs", measured)


def source(name):
    return f"[results/{name}](results/{name})"


def verdict_row(check, day, host, verdict, detail, commit, name):
    return (check, day, host, SHOWN[verdict], detail, f"`{commit}`", source(name))


def oracle_rows(name, text):
    day, host, commit = day_of(name), header_host(text, name), header_commit(text, name)
    arms = arm_verdicts(text, name)
    columns = {"T-INV-2": "T-INV-2 strict"}

    def row(check, detail):
        verdict = re.search(rf"^## {re.escape(check)}: .*?^Verdict[^*\n]*\*\*(\w+)\*\*", text,
                            re.MULTILINE | re.DOTALL)
        if verdict is None:
            raise TableError(f"results/{name}: no verdict for {check}")
        shown, detail = across_arms(arms, columns.get(check, check), verdict[1], detail)
        return verdict_row(check, day, host, shown, detail, commit, name)

    messages = table_row("Messages diffed", text, name)[0]
    divergences = table_row("Divergences", text, name)[0]
    _, two_sided, crossed, locked = table_row("All observations, every message", text, name)
    _, open_two_sided, open_crossed, open_locked = table_row(
        "Market open and T, every message", text, name)
    allocations = table_row("Fast book", text, name)[1]
    violations = find(r"^## T-INV-1:.*\n\nVerdict: \*\*\w+\*\*, (.*?)\.$", text, name)[1]
    new_crosses = find(r"^## T-INV-3:.*\n\nVerdict: \*\*\w+\*\*, (.*?)\.", text, name)[1]
    new_crossed, new_locked = table_row("With none, a violation", text, name)
    left_crossed, left_locked = table_row("With a leftover in force, allowed", text, name)
    resumptions = table_row("Resumptions, `H` into `T`", text, name)[0]
    leftovers = table_row("Of which found the book crossed or locked, so a leftover opened",
                          text, name)[0]
    return [
        row("T-ORACLE-1", f"{divergences} divergences in {messages} messages"),
        row("T-INV-1", violations),
        row("T-INV-2", f"strict: {crossed} crossed and {locked} locked of {two_sided} two-sided "
                       f"observations; {open_crossed} and {open_locked} of {open_two_sided} "
                       "with the market open and the symbol trading"),
        row("T-INV-3", f"{new_crosses}: {new_crossed} crossed and {new_locked} locked with no "
                       f"leftover from a halt; {left_crossed} and {left_locked} with one in force; "
                       f"{leftovers} of {resumptions} resumptions found the book crossed or "
                       "locked"),
        row("T-ALLOC-1", f"{allocations} allocations after warm-up, fast book"),
    ]


def sessions_row(name, text):
    """T-ORACLE-2, from scripts/oracle_sessions.py's sum of every session's oracle file."""
    verdict = find(r"^Verdict: \*\*(\w+)\*\*, (.*)\.$", text, name)
    commits = find(r"^- Commits: (.*)$", text, name)[1]
    return ("T-ORACLE-2", "every session", header_host(text, name), SHOWN[verdict[1]], verdict[2],
            ", ".join(f"`{commit}`" for commit in commits.split(", ")), source(name))


def odd_lot_detail(text, name):
    symbols = sorted(set(re.findall(r"^- `t3_odd_lots .*symbol=(\S+)", text, re.MULTILINE)))
    equal, total = find(r"^- equal_odd_lots (\d+) of (\d+) symbols", text, name).groups()
    return f"odd-lot columns wrong on {' and '.join(symbols)}, equal on {equal} of {total} symbols"


def pinned_shares(text, name):
    """How many compared symbols agree with MIDAS on shares in the pinned window."""
    window = find(r"Pinned window: `(\w+)`", text, name)[1]
    shares = table_row(window, text, name)[0]
    compared = table_row("Symbols compared", text, name)[0]
    return f"shares equal on {shares} of {compared}"


def t3_failure_detail(text, name):
    """What T-TRADE-3 found wrong: the columns and symbols the tool printed, by kind."""
    found = re.findall(r"^- `(t3_\w+) .*?symbol=(\S+)", text, re.MULTILINE)
    other = sorted({(check, symbol) for check, symbol in found if not check.startswith("t3_odd")})
    parts = []
    if other:
        equal = re.findall(r"^- (equal_(?!odd)\w+) (\d+) of (\d+) symbols$", text, re.MULTILINE)
        if not equal:
            raise TableError(f"results/{name}: T-TRADE-3 failed with no equal_ line")
        parts.append(", ".join(f"{check[len('t3_'):]} wrong on {symbol}" for check, symbol in other)
                     + "; " + ", ".join(f"{key} {have} of {of}" for key, have, of in equal))
    if not other or any(check.startswith("t3_odd") for check, _ in found):
        parts.append(odd_lot_detail(text, name))
    # Every failing row ends with the pinned window's share count, whatever failed.
    parts.append(pinned_shares(text, name))
    return "; ".join(parts)


def trades_rows(name, text):
    day, host, commit = day_of(name), header_host(text, name), header_commit(text, name)
    arms = arm_verdicts(text, name)
    rows = []
    for check, verdict, violations in re.findall(r"^\| ([\w-]+) \| \*\*(\w+)\*\* \| (\d+) \|$",
                                                 text, re.MULTILINE):
        if verdict == "NOT_AVAILABLE":
            detail = find(rf"^{check} is not available on \w+: (.*?), so ", text, name)[1]
        elif check == "T-TRADE-3" and verdict == "FAIL":
            detail = t3_failure_detail(text, name)
        elif check == "T-TRADE-3" and "odd-lot columns were not compared" in text:
            detail = f"{violations} violations; odd-lot columns not compared, the SEC's rule ended"
        else:
            detail = f"{violations} violations"
        shown, detail = across_arms(arms, check, verdict, detail)
        rows.append(verdict_row(check, day, host, shown, detail, commit, name))
    missing = TRADE_CHECKS - {row[0] for row in rows}
    if missing:
        raise TableError(f"results/{name}: no verdict for {sorted(missing)}")
    return rows


def chase_rows(name, text):
    verdict = find(r"^\*\*Verdict: (\w+)\*\*", text, name)[1]
    host = find(r"^- host: (.*)$", text, name)[1]
    commit = header_commit(text, name)
    return [verdict_row("Counter chase", "none", host, verdict,
                        "the fill event stands in for last-level misses on this host",
                        commit, name)]


def counters_rows(name, text):
    """T-PERF-1 and T-PERF-2 from a file of counts by message type."""
    host = find(r"^- host: (.*)$", text, name)[1]
    commit = header_commit(text, name)
    design = find(r"^- design: ([\w-]+):", text, name)[1]
    rows = []
    for target in ("T-PERF-1", "T-PERF-2"):
        what, verdict, figure = find(
            rf"^- {target}, (.*?), against ≤ [0-9.]+: \*\*(PASS|FAIL)\*\*\. Measured ([0-9.]+) raw",
            text, name).groups()
        rows.append(verdict_row(target, day_of(name), host, verdict,
                                f"{figure} {what.split(',')[0]}, `{design}`, the empty span's "
                                "cost shown beside it", commit, name))
    return rows


def summary_row(name, text):
    """A flamegraph, a macOS profile or a counter table: what it is, from its own summary."""
    title = find(r"^# (.*)$", text, name)[1]
    host = find(r"^- host: (.*)$", text, name)[1]
    summary = find(r"^- summary: (.*)$", text, name)[1]
    return (title, host, summary, f"`{header_commit(text, name)}`", source(name))


SUMMARY_PREFIXES = ("flamegraph_", "profile_", "counters_zen1_", "counters_by_age_", "lookup_",
                    "prefetch_", "dispatch_", "simd_", "layout_", "structure_", "window_",
                    "types_", "walks_")


def mm_rows(name, text):
    """T-MM-2: the average spread against the paper's at two decimals, and the other columns."""
    verdict = find(r"^\*\*Verdict: (\w+)\*\*", text, name)[1]
    spreads = re.findall(r"^\| [\d.]+ \| ([\d.]+) \| ([\d.]+) \| [\d.]+ \| (?:matches|does not "
                         r"match) \|$", text, re.MULTILINE)
    if len(spreads) != 3:
        raise TableError(f"results/{name}: {len(spreads)} average spreads, not 3")
    matched = find(r"^- Average spread: (\d+ of \d+) match", text, name)[1]
    columns = find(r"^- Other columns: (\d+ of \d+) within", text, name)[1]
    detail = (f"average spread {', '.join(measured for _, measured in spreads)} against the "
              f"paper's {', '.join(paper for paper, _ in spreads)}: {matched} match at two "
              f"decimals; other columns {columns} within 2 standard errors")
    return [verdict_row("T-MM-2", "none", header_host(text, name), verdict, detail,
                        header_commit(text, name), name)]


def pnl_rows(name, text):
    """The market maker's report day: each symbol's runs, their fills, shares and PnL in dollars."""
    rows = []
    for symbol, section in re.findall(r"^## ([A-Z]+)\n((?s:.*?))(?=^## |\Z)", text, re.MULTILINE):
        counts = re.findall(r"^\| ([^|]+?) \| ([\d,]+) \| [\d,]+ \| [\d,]+ \| ([\d,]+) \|",
                            section, re.MULTILINE)
        money = section[section.index("PnL in dollars:"):section.index("PnL in dollars a share:")]
        amounts = re.findall(r"^\| ([^|]+?) \| ([^|]+) \| [^|]+ \| ([^|]+) \| ([^|]+) \| "
                             r"([^|]+) \| ([^|]+) \|$", money, re.MULTILINE)[1:]
        ticks = section[section.index("PnL in ticks a share:"):]
        per_share = re.findall(r"^\| ([^|]+?) \|(?: [^|]+ \|){5} ([^|]+) \|$", ticks,
                               re.MULTILINE)[1:]
        if not counts or len(counts) != len(amounts) or len(counts) != len(per_share):
            raise TableError(f"results/{name}: {symbol}'s tables do not have one row a run")
        for (run, fills, shares), amount, (_, total_ticks) in zip(counts, amounts, per_share,
                                                                    strict=True):
            rows.append((symbol, run, fills, shares, *(cell.strip() for cell in amount[2:]),
                         total_ticks.strip(), source(name)))
    if not rows:
        raise TableError(f"results/{name}: no symbol's runs")
    return rows


def fault_row(name, text):
    result = find(r"^- Result: (.*)$", text, name)[1]
    controls = find(r"^- Controls: (.*)$", text, name)[1]
    selection = find(r"^- Selection: (.*)$", text, name)[1]
    host = find(r"^- Host: (.*)$", text, name)[1]
    commit = header_commit(text, name)
    # Faults for another system are not run on this host; the row says so, and where their run
    # on a host of that system is recorded, so that the count is not read as every fault.
    elsewhere = re.search(r"^- Not run on this host: (\d+), for (.+?) only\b", text, re.MULTILINE)
    note = (f"; {elsewhere[1]} faults for {elsewhere[2]} only are not run on this host, and "
            "their run on a host of that system is recorded in docs/NOTES.md" if elsewhere else "")
    return ("Fault matrix", "none", host, result, f"{selection}; controls: {controls}{note}",
            f"`{commit}`", source(name))


def fuzz_row(name, text):
    """The fuzzers' long run, a check beside the fault matrix: scripts/fuzz_report.py writes
    the file only for a run in which every target was clean."""
    result = find(r"^- Result: (.*)$", text, name)[1]
    seconds = find(r"^- Seconds a target: (\d{1,3}(?:,\d{3})*)", text, name)[1]
    return ("Fuzzing", "none", host_label(text, name), result,
            f"every target {seconds} s, exit 0 and 0 crashes", f"`{header_commit(text, name)}`",
            source(name))


def review_row(name, text):
    """The final review's record, a check beside the fault matrix: scripts/review_report.py
    writes it from scripts/final_review.json, and its result says complete only when every
    finding is settled and every reviewer has reported."""
    result = find(r"^- Result: (.*)$", text, name)[1]
    reviewers = find(r"^- Reviewers: (\d+)$", text, name)[1]
    findings = find(r"^- Findings: (\d+)$", text, name)[1]
    severities = find(r"^- Severities: (.*)$", text, name)[1]
    outcomes = find(r"^- Outcomes: (.*)$", text, name)[1]
    return ("Final review", "none", "none", result,
            f"{reviewers} reviewers, {findings} findings ({severities}); {outcomes}",
            f"`{header_commit(text, name)}`", source(name))


def sampler_note(text):
    """For a macOS run made before the sampler rule, the file's own sampler line, which says
    that no sampler ran beside it, for each row that quotes the file (r5-2); else nothing."""
    sampler = re.search(r"^- sampler: (.*)$", text, re.MULTILINE)
    if sampler and sampler[1] == check_results.SAMPLER_BEFORE_RULE:
        return f"; sampler: {sampler[1]}"
    return ""


def pinning_cell(text, name):
    """Where the run ran, with the sampler note."""
    return find(r"^- pinning: (.*?)(?::|$)", text, name)[1] + sampler_note(text)


def throughput_row(name, text):
    median, low, high, spread = table_row("Throughput, messages/s", text, name)
    host = find(r"^- host: (.*)$", text, name)[1]
    cpu = find(r"^- cpu: (.*)$", text, name)[1]
    pinning = pinning_cell(text, name)
    commit = header_commit(text, name)
    return ("Harness proof, stub book", day_of(name), host, cpu, pinning, median, low, high, spread,
            f"`{commit}`", source(name))


def harness_proof(text):
    """A stub-book harness proof: its label is the line just above its Header, where
    bench_driver.py writes it, not the words anywhere in the file."""
    above = text.split("\n## Header\n", 1)[0] if "\n## Header\n" in text else ""
    lines = [line for line in above.splitlines() if line.strip()]
    return bool(lines) and lines[-1].startswith(f"**{HARNESS_LABEL} ")


def header_field(text, field, name):
    return find(rf"^- {field}: (.*)$", text, name)[1]


def summary_median(text, label, name):
    median = table_row(label, text, name)[0]
    if not re.fullmatch(r"[0-9][0-9,]*(?:\.[0-9]+)?", median):
        raise TableError(f"results/{name}: {label} is {median!r}, not a figure")
    return median


def ratio_cell(text, design, name):
    """The median ratio over a map design, or a dash where the run had none."""
    section = find(r"^## Design comparison\n((?s:.*?))(?=^## |\Z)", text, name)[1]
    match = re.search(rf"^\| {re.escape(design)} \| ([0-9.]+) \|", section, re.MULTILINE)
    return f"{match[1]}×" if match else "–"


def book_row(name, text):
    """One design's run: what it ran on, its rate and memory, and its ratio over the map book."""
    design = find(r"^- design: ([a-z-]+): ", text, name)[1]
    input_mode = find(r"^- input: (ram|mapped): ", text, name)[1]
    top = header_field(text, "top_of_book", name)
    if top != "not tracked" and not top.startswith("tracked: "):
        raise TableError(f"results/{name}: top_of_book is {top!r}")
    host = header_field(text, "host", name)
    pinning = pinning_cell(text, name)
    rate = summary_median(text, "Throughput, messages/s", name)
    spread = table_row("Throughput, messages/s", text, name)[3]
    batch = summary_median(text, "Batch mean per book message, ns", name)
    memory = summary_median(text, "Book memory, MiB", name)
    commit = header_commit(text, name)
    return (f"`{design}`", input_mode, "tracked" if top != "not tracked" else "not tracked",
            day_of(name), host, pinning, rate, spread, batch, memory,
            ratio_cell(text, "map-fresh", name), ratio_cell(text, "map-aged", name),
            f"`{commit}`", source(name))


TARGET_ID = re.compile(r"T-[A-Z0-9]+-\d+")


def targets_table(name, text):
    """Each row of the file's Targets table, by the table's own heads, or [] for a file with no
    such table. A row of a target with a cell missing or over is refused."""
    section = re.search(r"^## Targets\n((?s:.*?))(?=^## |\Z)", text, re.MULTILINE)
    lines = [line for line in (section[1] if section else "").splitlines()
             if line.startswith("| ")]
    if not lines:
        return []
    heads = [cell.strip() for cell in lines[0].strip().strip("|").split("|")]
    rows = []
    for line in lines[1:]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not TARGET_ID.fullmatch(cells[0]):
            continue
        if len(cells) != len(heads):
            raise TableError(f"results/{name}: {cells[0]}'s row has {len(cells)} cells, its "
                             f"targets table {len(heads)}")
        rows.append(dict(zip(heads, cells)))
    return rows


def verdict_word(verdict):
    """A Targets-table verdict's words before its detail: met, missed, host differs ..."""
    return verdict.split(":")[0].strip()


def book_target_rows(name, text, book):
    """Every target in a book file's Targets table, with its bar, what was measured and its
    verdict as the file gives it: a miss with its gap, a figure no bar on this host can judge
    with why, and a target the run did not measure with the reason. book is the file's row in
    the table of designs, whose design, input, top of book, day, host and commit it shares."""
    design, input_mode, top, day, host = book[:5]
    rows = []
    for cells in targets_table(name, text):
        verdict = cells["Verdict"]
        word, _, detail = verdict.partition(": ")
        rows.append((cells["Target"], design, input_mode, top, day, host, cells["Bar"],
                     cells["Bar set on"], cells["Measured here"] or "–",
                     f"**missed**: {ratio_gap(detail)}" if word == "missed" else ratio_gap(verdict),
                     book[-2],
                     source(name)))
    if not rows:
        raise TableError(f"results/{name}: a book file with no target in its targets table")
    return rows


# Each backtest target's bar is in the unit BENCHMARKS.md gives it: T-BT-1's in events a
# second, which the results files give as engine events/s, T-BT-3's in ITCH messages a second.
# Here, the row of a results file's Summary that holds the median in that unit, and the unit
# as the files print it. backtest_bar holds the unit to BENCHMARKS.md.
BACKTEST_UNITS = {"T-BT-1": ("Engine events/s", "engine events/s"),
                  "T-BT-3": ("Replay rate, messages/s", "messages/s")}
BENCHMARKS_PAGE = "BENCHMARKS.md"
BACKTEST_SECOND = {"Engine events/s": ("Replay rate, messages/s", "messages/s"),
                   "Replay rate, messages/s": ("Engine events/s", "engine events/s")}


def backtest_bar(root, target):
    """A backtest target's bar from BENCHMARKS.md, in millions, and its unit as the results
    files print it; TableError if the unit there is not the one BACKTEST_UNITS compares the
    target in."""
    path = root / BENCHMARKS_PAGE
    if not path.is_file():
        raise TableError(f"{BENCHMARKS_PAGE}: not there, and {target}'s bar is read from it")
    match = re.search(rf"^### {re.escape(target)}: .*\n- \*\*Target:\*\* ≥ ([0-9.]+) M "
                      r"(?:ITCH )?(events|messages)/s\b", path.read_text(encoding="utf-8"),
                      re.MULTILINE)
    if match is None:
        raise TableError(f"{BENCHMARKS_PAGE}: no bar in M events/s or M messages/s for {target}")
    unit = f"{match[2]}/s"
    compared = BACKTEST_UNITS.get(target, ("", "no unit"))[1]
    if compared.split(" ")[-1] != unit:
        raise TableError(f"{BENCHMARKS_PAGE}: {target}'s bar is in {unit}, and this script "
                         f"compares it in {compared}")
    return float(match[1]), compared


def backtest_judged(root, name, text):
    """A backtest file's target, the bar its targets table gives, held to BENCHMARKS.md's bar
    and unit, and the Summary row and median in that unit. The file's verdict and its row's
    figures are held to its Summary by check_results.py, not here."""
    cells = find(r"^\| (T-BT-\d) \| ([^|]+) \| [^|]+ \| [^|]+ \|$", text, name)
    target, shown = cells[1], cells[2].strip()
    if target not in BACKTEST_UNITS:
        raise TableError(f"results/{name}: {target} is not a backtest target this script "
                         "knows the unit of")
    bar, unit = backtest_bar(root, target)
    if shown != f"≥ {bar:.2f} M {unit}":
        raise TableError(f"results/{name}: {target}'s bar is {shown!r}, and BENCHMARKS.md's "
                         f"is ≥ {bar:.2f} M {unit}")
    label = BACKTEST_UNITS[target][0]
    return target, shown, label, summary_median(text, label, name)


def backtest_row(root, name, text):
    """One backtester run: its workload, its bar, the median in the bar's unit with its CV,
    the other rate labelled beside it, and the target's verdict."""
    workload = header_field(text, "workload", name)
    host = header_field(text, "host", name)
    pinning = pinning_cell(text, name)
    target, bar, label, median = backtest_judged(root, name, text)
    verdict = find(r"^\| T-BT-\d \| [^|]+ \| [^|]+ \| ([^|]+) \|$", text, name)[1].strip()
    word, _, detail = verdict.partition(": ")
    # A miss with its gap, and a figure no bar can judge with why, as the file gives them.
    if word == "missed":
        verdict = f"**missed**: {detail}"
    spread = table_row(label, text, name)[3]
    other_label, other_unit = BACKTEST_SECOND[label]
    other = f"{summary_median(text, other_label, name)} {other_unit}"
    commit = header_commit(text, name)
    return (workload, target, day_of(name), host, pinning, bar,
            f"{median} {BACKTEST_UNITS[target][1]}", spread, other, verdict, f"`{commit}`",
            source(name))


def backtest_headline(target, name, text, root):
    """T-BT-1's headline row, the file's own targets row, once its bar is held to
    BENCHMARKS.md's bar and unit."""
    found = backtest_judged(root, name, text)[0]
    if found != target:
        raise TableError(f"results/{name}: its targets table is {found}'s, not {target}'s")
    return targets_headline(target, name, text)


def spsc_rows(name, text):
    """An SPSC file: one row for each comparison in its targets table."""
    host = header_field(text, "host", name)
    pinning = pinned_cores(text, name)
    commit = header_commit(text, name)
    section = find(r"^## Targets\n((?s:.*?))(?=^## |\Z)", text, name)[1]
    rows = []
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 6 or not re.fullmatch(r"T-SPSC-\d", cells[0]):
            continue
        target, comparison, bar, measured, _, verdict = cells
        rows.append((target, comparison, day_of(name), host, pinning, measured, bar,
                     verdict.split(":")[0], f"`{commit}`", source(name)))
    if not rows:
        raise TableError(f"results/{name}: no SPSC target row")
    return rows


def wasm_row(name, text):
    """T-WASM-1: the demo's replay in a browser against native, on one host, and its verdict's
    first clause."""
    host = header_field(text, "host", name)
    browser = header_field(text, "browser", name).split(", headless")[0]
    symbol = header_field(text, "extract", name).split(" on ")[0]
    native = summary_median(text, "Native, messages/s", name)
    wasm = summary_median(text, "WebAssembly in the browser, messages/s", name)
    _, measured, said = table_row("T-WASM-1", text, name)
    return ("T-WASM-1", symbol, day_of(name), host, browser, native, wasm, measured,
            said.split(";")[0], f"`{header_commit(text, name)}`", source(name))


def section_rows(text, title, name):
    """The cells of each row below the head of the table in the file's section of that title."""
    section = find(rf"^## {re.escape(title)}\n((?s:.*?))(?=^## |\Z)", text, name)[1]
    rows = [[cell.strip() for cell in line.strip("|").split("|")]
            for line in section.splitlines() if line.startswith("| ")]
    return rows[1:]


def alloc_rows(name, text):
    """T-ARENA-1: one row for each allocator arm, its rate, its tails over every operation and
    what the fault check made of its timed passes."""
    arms = [arm.strip() for arm in header_field(text, "allocators", name).split(",")]
    host = header_field(text, "host", name)
    pinning = find(r"^- pinning: (.*?)(?::|$)", text, name)[1]
    commit = header_commit(text, name)
    ratios = {cells[0]: f"{cells[1]}×" for cells in section_rows(text, "Comparison", name)}
    tails = {cells[0]: (cells[3], cells[4]) for cells in section_rows(text, "Latency", name)
             if len(cells) == 6 and cells[1] == "all"}
    faults = {cells[0]: cells[3] for cells in section_rows(text, "Page faults", name)
              if len(cells) == 4}
    rows = []
    for arm in arms:
        if arm not in tails or arm not in faults:
            raise TableError(f"results/{name}: {arm} has no latency or page-fault row")
        rate = summary_median(text, f"{arm}, throughput, messages/s", name)
        spread = table_row(f"{arm}, throughput, messages/s", text, name)[3]
        memory = summary_median(text, f"{arm}, book memory, MiB", name)
        rows.append((arm, day_of(name), host, pinning, rate, spread, ratios.get(arm, "–"),
                     *tails[arm], faults[arm], memory, f"`{commit}`", source(name)))
    return rows, header_field(text, "fault_check", name), header_field(text, "pages_requested", name)


# T-URING-2's packet size, bytes a datagram (P-0902), as scripts/udp_driver.py has it.
TURING2_DATAGRAM = 1400


def udp_rows(name, text):
    """A UDP replay file (scripts/udp_report.py): T-URING-2's verdict when the file has a
    bisection at 1,400 bytes a datagram, and T-URING-1's largest lossless rate for each mode,
    which has no bar, at the file's packet size. Those rates come from the bisect step's file
    alone: a later step's file that draws on the same bisection repeats its T-URING-1 table,
    and would list every mode twice."""
    host = header_field(text, "host", name)
    commit = header_commit(text, name)
    datagram = int(find(r"^([0-9,]+) bytes a datagram",
                        header_field(text, "packet_size_cap", name), name)[1].replace(",", ""))
    rows = []
    if re.search(r"^## T-URING-2: ", text, re.MULTILINE):
        if datagram != TURING2_DATAGRAM:
            raise TableError(f"results/{name}: a T-URING-2 verdict at {datagram:,} bytes a "
                             f"datagram; T-URING-2 is judged at {TURING2_DATAGRAM:,}")
        verdict, detail = find(r"^Verdict: \*\*(PASS|FAIL)\*\*, (.*)\.$", text, name).groups()
        rows.append(verdict_row("T-URING-2", day_of(name), host, verdict, detail, commit, name))
    modes = []
    if header_field(text, "step", name) == "bisect":
        for cells in section_rows(text, "T-URING-1: io_uring against sockets", name):
            if len(cells) != 3:
                raise TableError(f"results/{name}: a T-URING-1 row of {len(cells)} cells")
            modes.append((cells[0], f"{datagram:,} bytes", *cells[1:], day_of(name), host,
                          f"`{commit}`", source(name)))
        if not modes:
            raise TableError(f"results/{name}: T-URING-1 has no mode")
    return rows, modes


def run_rows(text):
    """A row for every line of the log's table. A line of it that is not the head, the rule
    or a run with its five cells is refused, never skipped, so no run drops out unseen.

    A row is never removed, and a run's outcome never rewritten (the one change to the log,
    on 2026-11-13, gave other programs by their kinds), so a run's later row says what became
    of it: withdrawn after it was published, or published again from the same records. Of the
    rows with one run id, the last stands, and each earlier one names the line of the log that
    does, in a code span, since a line number is not a measured figure."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells == list(RUN_HEADS) or all(re.fullmatch(r"-+", cell) for cell in cells):
            continue
        if len(cells) != 5 or not re.fullmatch(RUN_ID, cells[0]):
            raise TableError(f"results/run_log.md:{number}: a line of the table that is not a "
                             f"run with five cells: {line.strip()[:80]}")
        found.append((number, cells))
    last = {cells[0]: number for number, cells in found}
    rows = []
    for number, (run, prediction, commit, system, outcome) in found:
        stands = "yes" if last[run] == number else f"no: `run_log.md:{last[run]}` stands"
        rows.append((f"`{run}`", f"`{prediction}`", f"`{commit.split()[0]}`", system,
                     f"`{outcome.split(':')[0]}`", stands))
    return rows


def parse_rows(name, text):
    """A parse-rate file (scripts/parse_driver.py): one row a pass, its target's median, min, max
    and CV in messages a second, its median in GB a second, its bar and the file's own verdict,
    a miss with its gap and a bar set on another day with that day named, as the file words it.
    A bar set on a day other than the file's is refused a verdict word: its figure is above or
    under the bar on the day measured, never met or missed (other_day_words)."""
    host = header_field(text, "host", name)
    pinning = pinning_cell(text, name)
    commit = header_commit(text, name)
    rows = []
    for cells in targets_table(name, text):
        target = cells["Target"]
        other_day_words(target, name, cells)
        label = find(rf"^\| ({re.escape(target)}, [^|]*), messages/s \|", text, name)[1]
        median, low, high, spread = table_row(f"{label}, messages/s", text, name)
        gbs = table_row(f"{label}, GB/s", text, name)[0]
        word, _, detail = cells["Verdict"].partition(": ")
        verdict = f"**missed**: {detail}" if word == "missed" else cells["Verdict"]
        rows.append((target, label.split(", ", 1)[1], day_of(name), host, pinning, median, low,
                     high, spread, gbs, cells["Bar"], verdict, f"`{commit}`", source(name)))
    if not rows:
        raise TableError(f"results/{name}: a parse-rate file with no target in its targets table")
    return rows


def markdown_table(heads, rows):
    lines = ["| " + " | ".join(heads) + " |", "|" + "---|" * len(heads)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def generate(root):
    """The lines of the tables, or TableError."""
    verdicts = []
    faults = []
    throughput = []
    books = []
    book_targets = []
    backtests = []
    browsers = []
    summaries = []
    spsc = []
    pnl = []
    arenas = []
    turing1 = []
    parses = []
    parse_files = []
    for path in sorted((root / "results").glob("*.md")):
        name = path.name
        if name in NOT_RESULTS:
            continue
        text = path.read_text(encoding="utf-8")
        # The Targets-table verdicts a row below takes up, as (target, verdict's words).
        taken = []
        if name == "oracle_sessions.md":
            rows = [sessions_row(name, text)]
        elif name.startswith("oracle_"):
            rows = oracle_rows(name, text)
        elif name.startswith("trades_"):
            rows = trades_rows(name, text)
        elif name.startswith("chase_"):
            rows = chase_rows(name, text)
        elif name.startswith("counters_by_type_"):
            rows = counters_rows(name, text)
        elif name.startswith(SUMMARY_PREFIXES):
            summaries.append(summary_row(name, text))
            rows = []
        elif name == "fault_matrix.md":
            faults.append(fault_row(name, text))
            rows = []
        elif name.startswith("fuzz_"):
            faults.append(fuzz_row(name, text))
            rows = []
        elif name == "final_review.md":
            faults.append(review_row(name, text))
            rows = []
        elif name.startswith("bench_") and harness_proof(text):
            if name.startswith("bench_book_"):
                raise TableError(f"results/{name}: a book file's name, labelled the stub's")
            throughput.append(throughput_row(name, text))
            rows = []
        elif name.startswith("bench_"):
            book = book_row(name, text)
            books.append(book)
            targets = book_target_rows(name, text, book)
            book_targets += targets
            taken = [(row[0], verdict_word(row[9].replace("**", ""))) for row in targets]
            rows = []
        elif name.startswith("backtest_"):
            backtest = backtest_row(root, name, text)
            backtests.append(backtest)
            taken = [(backtest[1], verdict_word(backtest[9].replace("**", "")))]
            rows = []
        elif name.startswith("spsc_"):
            found = spsc_rows(name, text)
            spsc += found
            taken = [(row[0], row[7]) for row in found]
            rows = []
        elif name.startswith("wasm_"):
            browsers.append(wasm_row(name, text))
            rows = []
        elif name.startswith("alloc_"):
            arms, fault_check, pages = alloc_rows(name, text)
            arenas.append((name, fault_check, pages, arms))
            taken = [("T-ARENA-1", verdict_word(f"{ARENA_VERDICT}{fault_check}"))]
            rows = []
        elif name.startswith("mm_pnl_"):
            pnl += pnl_rows(name, text)
            rows = []
        elif name.startswith("mm_"):
            rows = mm_rows(name, text)
        elif name.startswith("udp_"):
            rows, modes = udp_rows(name, text)
            turing1 += modes
        elif name.startswith("parse_rate_"):
            found = parse_rows(name, text)
            parses += found
            parse_files.append((name, text))
            taken = [(row[0], verdict_word(row[11].replace("**", ""))) for row in found]
            rows = []
        else:
            raise TableError(f"results/{name}: this script has no reader for it")
        marked = len(VERDICT_MARK.findall(text))
        if marked != len(rows):
            raise TableError(f"results/{name}: {marked} verdicts in the file, {len(rows)} rows")
        tabled = sorted((cells["Target"], verdict_word(cells.get("Verdict", "no verdict")))
                        for cells in targets_table(name, text))
        if tabled != sorted(taken):
            raise TableError(f"results/{name}: its targets table gives {len(tabled)} verdicts, "
                             f"and the rows here take up {len(taken)}: "
                             f"{sorted(set(tabled) ^ set(taken))[:4]}")
        verdicts += rows
    run_log = (root / "results" / "run_log.md").read_text(encoding="utf-8")

    # By target, in BENCHMARKS.md's order, each target's rows in the order of their files; the
    # checks that are no target after them.
    order = {target: index for index, target in enumerate(benchmark_targets(root)[1])}
    verdicts.sort(key=lambda row: order.get(row[0], len(order)))
    lines = markdown_table(("Check", "Day", "Host", "Verdict", "What it found", "Commit", "File"),
                           verdicts + faults)
    lines += ["", "Throughput. These rows prove the harness on the stub book. They say nothing "
                  "about a real book, and the two hosts are not to be compared.", ""]
    lines += markdown_table(("Run", "Day", "Host", "CPU", "Pinning", "Median messages/s", "Min",
                             "Max", "CV", "Commit", "File"), throughput)
    if books:
        lines += ["", "Book designs. Each row is one design's results file, with its ratio over "
                      "each map design that ran in the same repetitions (T-DESIGN-1). A macOS row "
                      "is not pinned: macOS cannot pin a thread, so it is a macOS figure and never "
                      "a Linux one, and the file's own targets table says so beside each bar.", ""]
        lines += markdown_table(("Design", "Input", "Top of book", "Day", "Host", "Pinning",
                                 "Median messages/s", "CV", "Batch mean ns a book message",
                                 "Book memory MiB", "Over map-fresh", "Over map-aged", "Commit",
                                 "File"), books)
    if book_targets:
        order = list(dict.fromkeys(row[0] for row in book_targets))
        book_targets.sort(key=lambda row: (order.index(row[0]), row[-1]))
        lines += ["", "Book targets. Every target of every book design's results file, from the "
                      "file's own targets table: its bar, where the bar was set, what this run "
                      "measured and the verdict, misses with their gaps. A bar set on x86 Linux "
                      "neither passes nor fails a macOS figure, so such a row says the host "
                      "differs, and a target the run was not set up to measure says why.", ""]
        lines += markdown_table(("Target", "Design", "Input", "Top of book", "Day", "Host", "Bar",
                                 "Bar set on", "Measured", "Verdict", "Commit", "File"),
                                book_targets)
    if parses:
        parses = [bar_day_judged(root, row) for row in parses]
        lines += ["", "The parse rate. Each row is one pass of a parse-rate file over the whole day "
                      "held in memory on one pinned thread: its rate over every framed message, and "
                      "its GB a second over the file's bytes, length prefixes included, as both "
                      "sources count; the verdict is on the median. A bar set on a day other than "
                      "the file's gets no verdict, met or missed: the row says whether the median "
                      "is above or under the bar on the day measured, and where the bar's own day "
                      "is judged, or that it was not measured." + mac_parse_words(parses), "",
                  parse_day_line(root), "", input_conditions(parse_files), ""]
        lines += markdown_table(("Target", "Pass", "Day", "Host", "Pinning", "Median messages/s",
                                 "Min", "Max", "CV", "Median GB/s", "Bar", "Verdict", "Commit",
                                 "File"), parses)
    if backtests:
        lines += ["", "The backtester. Each row is one workload's results file: T-BT-1 replays the "
                      "whole day with a quoting strategy, T-BT-3 queuetruth's four symbols with "
                      "its shadow orders. Each file says what one event is and what its rate is "
                      "over, beside its source's. Each bar is in the unit BENCHMARKS.md gives it, "
                      "T-BT-1's in events a second and T-BT-3's in ITCH messages a second, and "
                      "the verdict compares the median in that unit, which the CV is of; the "
                      "other rate is beside it, labelled.", ""]
        lines += markdown_table(("Workload", "Target", "Day", "Host", "Pinning", "Bar",
                                 "Median, in the bar's unit", "CV", "The other rate", "Verdict",
                                 "Commit", "File"), backtests)
    if browsers:
        lines += ["", "The browser demo's replay of one symbol's extract, in a browser and natively "
                      "on the same Mac, the same C++ on each side. Rates come from whole replays "
                      "only: the browser cannot time one message. The bar was set in Google "
                      "Chrome, so another browser neither meets nor misses it.", ""]
        lines += markdown_table(("Target", "Symbol", "Day", "Host", "Browser", "Native messages/s",
                                 "Browser messages/s", "Browser over native", "Verdict", "Commit",
                                 "File"), browsers)
    if summaries:
        lines += ["", "Profiles, counter tables and the book's hot-loop experiments. A "
                      "flamegraph shows where the samples fell, not a rate, and a macOS profile "
                      "never stands in for `perf` on `debian`. Each file says what it cannot "
                      "show.", ""]
        lines += markdown_table(("What", "Host", "Summary", "Commit", "File"), summaries)
    if spsc:
        lines += ["", "The SPSC ring. Each row is one comparison from a results file's targets "
                      "table: a ratio of two of its medians, or for T-SPSC-3 the median of paired "
                      "ratios, against BENCHMARKS.md's bar. Every bar was set on x86, so a macOS "
                      "row says the host differs, and an unpinned Mac's cross-core figures are "
                      "weak evidence. The files' paced and burst latencies, which no row here "
                      "uses, are measured from the push, not from the time each message was due; "
                      "no latency from the due time is measured, since that needs the due time "
                      "carried in each item, a change to measured code that no run has had "
                      "(BENCHMARKS.md, \"What the paced and burst latencies measure\").", ""]
        lines += markdown_table(("Target", "Comparison", "Day", "Host", "Pinning", "Measured",
                                 "Bar", "Verdict", "Commit", "File"), spsc)
    if turing1:
        lines += ["", "T-URING-1, io_uring against sockets: each receive mode's largest lossless "
                      "message rate on loopback, and its ratio over S3's, from its results file, "
                      "at each packet size it ran at, a datagram's bytes with its header. The "
                      "target is UNCONFIRMED, so there is no bar and no verdict. Loopback runs "
                      "the receive softirq on the sender's CPU, so these are syscall-and-copy "
                      "figures on one host, not network ones.", ""]
        # The larger packet first: 1,400 bytes is T-URING-2's size and the headline pair's.
        turing1.sort(key=lambda row: -int(row[1].split()[0].replace(",", "")))
        lines += markdown_table(("Mode", "Packet size", "Largest lossless msgs/s", "Over S3",
                                 "Day", "Host", "Commit", "File"), turing1)
    for name, fault_check, pages, arms in arenas:
        lines += ["", f"T-ARENA-1, the node book on each allocator arm, from "
                      f"[results/{name}](results/{name}), the pools' pages requested as "
                      f"`{pages}`. Only the allocator changes between arms. T-ARENA-1 is UNCONFIRMED, so no bar is met or missed; the check is "
                      "that no timed pass of a pool arm takes a page fault, and it read: "
                      f"{fault_check}. Tails are over every operation, in ns.", ""]
        lines += markdown_table(("Arm", "Day", "Host", "Pinning", "Median messages/s", "CV",
                                 "Over malloc-fresh", "p99", "p99.9", "Page faults, timed passes",
                                 "Book memory MiB", "Commit", "File"), arms)
    if pnl:
        lines += ["", "The market maker on its report day, 12302019, a late-December session, "
                      "with every parameter chosen on 10302019, an FOMC day. No "
                      f"edge is claimed. {len({row[0] for row in pnl})} symbols on one day: each "
                      "is its own symbol-day. The file gives S gross, fees, the markout curve and "
                      "how Q-off and L-off each change the PnL. Dollars at the base fee tier; a "
                      "replayed feed cannot react, so market impact is absent.", ""]
        lines += markdown_table(("Symbol", "Run", "Fills", "Shares", "S, $", "A, $", "I, $",
                                 "Total, $", "Total, ticks a share", "File"), pnl)
    lines += ["", held_by_tests()]
    lines += ["", "Every benchmark run, failed ones included, from "
                  "[results/run_log.md](results/run_log.md). A row is never removed, and a "
                  "run's outcome never rewritten (the one change to the log, on 2026-11-13, "
                  "gave other programs by their kinds), so a run with more than one row has a "
                  "later one saying what became of it: of the rows with one run id, the last "
                  "stands.", ""]
    lines += markdown_table(RUN_HEADS + ("Stands",), run_rows(run_log))
    return lines


def held_by_tests():
    """The sentence that names the targets no results file holds, and what holds each."""
    def names(items):
        quoted = [f"`{item}`" for item in items]
        return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} and {quoted[-1]}"
    held = []
    for target, asks, tests, gates in HELD_BY_TESTS:
        by = f"the ctest entries {names(tests)}"
        if gates:
            by += f", and the gate {names(gates)} in `scripts/gates.py`"
        held.append(f"{target}, {asks}, by {by}")
    return (f"{' and '.join(target for target, *_ in HELD_BY_TESTS)} have no row here: no "
            "results file holds them, and no figure was measured for them. Each is held by "
            f"tests instead: {'; '.join(held)}. CI and `scripts/check.sh` run the ctest "
            "entries, and `scripts/check.sh` the gate.")


def ratio_gap(text):
    """A gap between two ratios written as a difference, never as a factor: "over map-fresh by
    1.46×, over map-aged by 1.19×" as "over map-fresh 1.46 below the bar; over map-aged 1.19 below
    the bar", and "within the 1.55× bar by 0.36×" as "0.36 inside the 1.55× bar"."""
    text = re.sub(r"\bover (\S+) by (\d+(?:\.\d+)?)×", r"over \1 \2 below the bar", text)
    text = text.replace(" below the bar, over ", " below the bar; over ")
    return re.sub(r"\bwithin the (\S+×) bar by (\d+(?:\.\d+)?)×", r"\2 inside the \1 bar",
                  text)


# The map books as the README's headline names them: a reader does not know map-fresh, nor
# what an aged heap is. Each gap is the ratio's shortfall, named as the ratio's.
MAP_WORDS = (("map-fresh", "a `std::map` book on a fresh heap",
              "the ratio {} short of the bar on a fresh heap"),
             ("map-aged", "one on an aged heap (fragmented first, then churned before each add)",
              "{} short on an aged one"))


def headline_ratios(measured, gap):
    """T-DESIGN-1's figures and gap as the headline gives them: each ratio over the map book
    named in words, and each gap as how far below the bar the ratio is."""
    for name, book, where in MAP_WORDS:
        measured = re.sub(rf"\bover {name} (\d+(?:\.\d+)?×)", rf"\1 {book}", measured)
        gap = re.sub(rf"\bover {name} by (\d+(?:\.\d+)?)×",
                     lambda match, where=where: where.format(match[1]), gap)
    return measured, gap


def shown_verdict(verdict, gap=""):
    """A headline verdict, met or missed, with the gap its file gives a miss: "by 11.25 M msg/s"
    as "11.25 M msg/s short"."""
    if verdict == "met":
        return "met"
    gap = re.sub(r"^by (.+)$", r"\1 short", gap)
    return f"**missed**: {gap}" if gap else "**missed**"


def pinned_cores(text, name):
    """Where the run's threads ran, from the file's pinning fields: a second thread's peer
    pinning too when the file has one, so a two-core run names both cores."""
    pinnings = [find(r"^- pinning: (.*?)(?::|$)", text, name)[1]]
    peer = re.search(r"^- peer_pinning: (.*?)(?::|$)", text, re.MULTILINE)
    if peer is None:
        return pinnings[0]
    pinnings.append(peer[1])
    cores = [re.fullmatch(r"pinned to core (\d+)", pinning) for pinning in pinnings]
    if all(cores):
        return f"pinned to cores {cores[0][1]} and {cores[1][1]}"
    if all(pinning == "not pinned" for pinning in pinnings):
        return "neither thread pinned"
    return f"{pinnings[0]}; the second thread {pinnings[1]}"


def debian_host(text, name):
    """A benchmark file's host, its CPU and where its threads ran, from the file's header."""
    host = header_field(text, "host", name)
    cpu = header_field(text, "cpu", name).split(" with ")[0]
    return f"{host}, {cpu}, {pinned_cores(text, name)}"


def sessions_headline(target, name, text):
    """T-ORACLE-2 from the sum of every session's oracle file."""
    bar = find(r"^- Target: (.*?)\. ", text, name)[1]
    verdict, measured, gap = find(
        r"^Verdict: \*\*(PASS|FAIL)\*\*, ([\d,]+ divergences in [\d,]+ messages over \d+ "
        r"sessions)(?:; the gap: (.*))?[,.]", text, name).groups()
    return (bar, measured, header_host(text, name), "met" if verdict == "PASS" else "missed",
            gap or "")


def alloc_headline(target, name, text):
    """T-ALLOC-1 from one day's oracle file: the fast book's count on every arm the file ran, and
    its control's, which shows the counter does see a book's allocations."""
    bar, verdict = find(r"^## T-ALLOC-1: (.*)\n\nVerdict: \*\*(PASS|FAIL)\*\*", text, name).groups()
    messages, allocations = table_row("Fast book", text, name)
    control = table_row("Control book, the reference", text, name)[1]
    verdict, measured = across_arms(
        arm_verdicts(text, name), target, verdict,
        f"{allocations} allocations after warm-up in {messages} messages")
    measured = (f"{in_all_designs(measured)}; the counter sees {control} in the second, simple "
                "book, so it does catch allocations")
    return (bar, measured, header_host(text, name), "met" if verdict == "PASS" else "missed",
            "")


def targets_headline(target, name, text):
    """A target's row in a benchmark file's targets table, read by the table's own heads."""
    section = find(r"^## Targets\n((?s:.*?))(?=^## |\Z)", text, name)[1]
    lines = [line for line in section.splitlines() if line.startswith("| ")]
    if not lines:
        raise TableError(f"results/{name}: the targets table has no rows")
    heads = [cell.strip() for cell in lines[0].strip().strip("|").split("|")]
    found = [dict(zip(heads, cells)) for cells in
             ([cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines[1:])
             if cells[0] == target and len(cells) == len(heads)]
    if len(found) != 1:
        raise TableError(f"results/{name}: {len(found)} {target} rows in its targets table, "
                         "not 1")
    cells = found[0]
    measured = cells.get("Measured here", cells.get("Measured", ""))
    measured = measured.removeprefix("rate ")
    if "Comparison" in cells:
        measured = f"{measured}, {cells['Comparison']}"
    bar = cells["Bar"]
    where = cells.get("Bar set on") or cells.get("Host", "").partition("the bar's: ")[2]
    if where:
        bar = f"{bar}, set on {where}"
    verdict, _, gap = cells["Verdict"].partition(": ")
    return bar, measured, debian_host(text, name), verdict, gap.removeprefix("rate ")


def book_conditions(text, name):
    """What a book benchmark ran on, from its header: the design, the input and the top."""
    design = header_field(text, "design", name).split(":")[0]
    source_mode = header_field(text, "input", name).split(":")[0]
    if source_mode not in ("ram", "mapped"):
        raise TableError(f"results/{name}: input is {source_mode!r}")
    top = header_field(text, "top_of_book", name)
    return (f"`{design}`, {'file in memory' if source_mode == 'ram' else 'file mapped'}, top of "
            f"book {'not tracked' if top == 'not tracked' else 'tracked'}")


def book_headline(target, name, text):
    """A book target's row, with the design it ran and where the day was, in memory or mapped;
    top-of-book tracking is in RESULTS.md's rows."""
    bar, measured, host, verdict, gap = targets_headline(target, name, text)
    design = header_field(text, "design", name).split(":")[0]
    source_mode = header_field(text, "input", name).split(":")[0]
    if source_mode not in ("ram", "mapped"):
        raise TableError(f"results/{name}: input is {source_mode!r}")
    where = "day in memory" if source_mode == "ram" else "day mapped"
    return bar, f"{measured}; `{design}`, {where}", host, verdict, gap


def short_bar(bar):
    """A headline bar as its file gives it, with where it was set in brackets after it, the
    host's name only, and T-ORACLE-2's bar to its count of sessions."""
    bar = re.sub(r" and at least [\d,]+ messages compared, on the Mac$", "", bar)
    return re.sub(r", set on ([^,]*)(?:,.*)?$", r" (\1)", bar)


def short_host(host):
    """A headline host by the name the README's pitch gives it: the Zen 1 host, or the Mac's
    model; where its threads ran is in RESULTS.md."""
    if host.startswith("debian"):
        return "Zen 1 host"
    return host.removeprefix("Darwin arm64 ")


# The README's headline: these targets, in this order, each from the results file beside it.
# Only the words on what each measures are written here; every value comes from the file.
HEADLINE = (
    ("T-ORACLE-2", "oracle_sessions.md", sessions_headline,
     "Agreement with a second, simple book that shares no code with the fast one, over whole "
     "sessions"),
    ("T-ALLOC-1", "oracle_12302019.md", alloc_headline,
     "Heap allocations on the hot path over a whole day"),
    ("T-BOOK-1", "bench_book_paged-flat_ram_debian_12302019.md", book_headline,
     "Every symbol's full-depth book rebuilt from a whole day"),
    ("T-DESIGN-1", "bench_book_paged-flat_ram_debian_12302019.md", book_headline,
     "Speed over a `std::map` book, same run and core"),
    ("T-BT-1", "backtest_quoting_debian_12302019.md", backtest_headline,
     "The backtester replaying a whole day, quoting, with its queue model; an engine event is a "
     "message, or a delivery, arrival, report or wake it causes"),
)
# Figures under the headline that no bar can judge, because the bar was set on another host:
# each a line of its own, saying so, never a row with a verdict; each with what it measures.
UNJUDGED = (
    ("T-BOOK-1", "bench_book_paged-flat_ram_mac_12302019.md"),
)
UNJUDGED_WHAT = {"T-BOOK-1": "the same full-depth rebuild"}
HEADLINE_SIZE = (4, 5)
HEADLINE_FIRST = "What it measures (its row id in RESULTS.md)"


def headline(root, chosen=HEADLINE, unjudged=UNJUDGED):
    """The lines of the README's headline table and the unjudged lines under it, or
    TableError."""
    if not HEADLINE_SIZE[0] <= len(chosen) <= HEADLINE_SIZE[1]:
        raise TableError(f"the headline has {len(chosen)} rows, not "
                         f"{HEADLINE_SIZE[0]} to {HEADLINE_SIZE[1]}")
    rows = []
    missed = 0
    for target, name, reader, what in chosen:
        path = root / "results" / name
        if not path.is_file():
            raise TableError(f"results/{name}: the headline's {target} row names it, and it is "
                             "not there")
        text = path.read_text(encoding="utf-8")
        # The backtester's bar and its unit come from BENCHMARKS.md, under the root.
        bar, measured, host, verdict, gap = (reader(target, name, text, root)
                                             if reader is backtest_headline else
                                             reader(target, name, text))
        if verdict not in ("met", "missed"):
            raise TableError(f"results/{name}: {target}'s verdict is {verdict!r}, neither met "
                             "nor missed, so it cannot be in the headline")
        missed += verdict == "missed"
        if target == "T-DESIGN-1":
            measured, gap = headline_ratios(measured, gap)
        rows.append((f"{what} ({target})", short_bar(bar), in_all_designs(measured),
                     short_host(host), shown_verdict(verdict, ratio_gap(gap)), source(name)))
    if not missed:
        raise TableError("the headline shows no missed target: at least one row must be a "
                         "miss, so that a reader sees one on the first page")
    lines = markdown_table((HEADLINE_FIRST, "Bar", "Measured", "Host", "Verdict", "File"), rows)
    for target, name in unjudged:
        lines += ["", unjudged_line(root, target, name)]
    return lines


def unjudged_line(root, target, name):
    """A figure from a host its target's bar was not set on: its value, conditions and host,
    and the file's own words on why it is neither a pass nor a fail."""
    path = root / "results" / name
    if not path.is_file():
        raise TableError(f"results/{name}: the headline's unjudged {target} line names it, and "
                         "it is not there")
    text = path.read_text(encoding="utf-8")
    host = header_field(text, "host", name)
    if host == "debian":
        raise TableError(f"results/{name}: its host is debian, which {target}'s bar can judge, "
                         "so it belongs in the table")
    bar, measured, _, verdict, why = targets_headline(target, name, text)
    if verdict != "host differs":
        raise TableError(f"results/{name}: {target}'s verdict is {verdict!r}, not host differs, "
                         "so it cannot stand as an unjudged figure")
    repetitions = header_field(text, "repetitions", name)
    cpu = header_field(text, "cpu", name)
    unsampled = " and with no load sampler beside it" if sampler_note(text) else ""
    figure, _, set_on = bar.partition(", set on ")
    machine, _, system = set_on.partition(", ")
    if not (machine and system):
        raise TableError(f"results/{name}: {target}'s bar {bar!r} names no host and system")
    return (f"On the {cpu}, {UNJUDGED_WHAT[target]} ({target}) ran at {measured}, the median of "
            f"{in_words(int(repetitions))} repetitions, {pinned_cores(text, name)}{unsampled} "
            f"({source(name)}). It has no verdict, since its bar, {figure}, was set on a "
            f"{machine} under {system}.")


# WRITEUP.md's table of every target missed or not measured. Each row is read from the
# results file named beside it, and a row whose file no longer gives the verdict the row
# stands for is refused, so the table cannot drift from the files. Only the words on why
# are written here, and they carry no figure.
WRITEUP_PAGE = "WRITEUP.md"
MISS_BEGIN = "<!-- missed targets: begin, generated by scripts/results_table.py, do not edit -->"
MISS_END = "<!-- missed targets: end -->"
NOT_MEASURED_MARK = "<!-- figures: not measured -->"
BOOK_DEBIAN = "bench_book_paged-flat_ram_debian_12302019.md"
PARSE_DEBIAN = "parse_rate_debian_12302019.md"
# Each why is (the full words, for RESULTS.md, and a few words, for WRITEUP.md's table). A
# full why gives what was measured, then what is not established; neither leads with a cause
# that is not established.
BOOK_WHY = ("measured: every fast arm runs about the same instructions a message, and its speed "
            "follows its DRAM fills; not established: how much of the gap is the host's, since no "
            "x86 host with a larger cache was available; the bar was set on the x86 host named "
            "beside it",
            "speed follows DRAM fills; the host's share not established")
TAIL_WHY = ("measured: messages that name an order older than a second take most of the DRAM "
            "fills; not established: what sets the tail, which no run traced",
            "old orders take most fills; the tail's cause not established")


def targets_rows(name, text):
    """Every row of a file's targets table, each a dict by the table's own heads, read by
    targets_table() as the Book targets table reads it; a file whose heading is the singular
    "## Target" is read the same way."""
    rows = targets_table(name, re.sub(r"^## Target\n", "## Targets\n", text, flags=re.MULTILINE))
    if not rows:
        raise TableError(f"results/{name}: the targets table has no rows")
    return rows


def host_label(text, name):
    """debian or macOS, from the file's header: a Mac figure is never shown as Linux."""
    host = find(r"^- [Hh]ost: (.*?)(?:\. |$)", text, name)[1]
    if host.startswith(("Darwin", "macOS")):
        return "macOS"
    if host != "debian":
        raise TableError(f"results/{name}: host {host!r} is neither debian nor macOS")
    return "debian"


def one_target_row(target, name, text, comparison=None):
    found = [cells for cells in targets_rows(name, text) if cells.get("Target") == target
             and (comparison is None or cells.get("Comparison") == comparison)]
    if len(found) != 1:
        raise TableError(f"results/{name}: {len(found)} {target} rows in its targets table, "
                         "not 1")
    return found[0]


def missed_row(target, name, text, comparison=None):
    """A target the file's targets table calls missed: its bar, figures and gap."""
    cells = one_target_row(target, name, text, comparison)
    verdict, _, gap = cells["Verdict"].partition(": ")
    if verdict != "missed":
        raise TableError(f"results/{name}: {target} is {cells['Verdict']!r}, not missed, and "
                         f"{WRITEUP_PAGE}'s table names it as a miss")
    bar = cells["Bar"]
    if cells.get("Bar set on"):
        bar = f"{bar} ({cells['Bar set on']})"
    measured = cells.get("Measured here", cells.get("Measured", ""))
    label = f"{target}, {comparison}" if comparison else target
    return label, bar, measured, gap, host_label(text, name)


def other_day_words(target, name, cells):
    """The bar's day, when a parse-rate file's bar is set on a day other than the file's, else
    None. Such a bar gets no verdict word, since a verdict on a bar is a verdict on its day: the
    file must say its figure is above or under the bar on the day measured, and met or missed is
    refused, with or without words after it."""
    day = day_of(name)
    bar_day = re.search(r", on (\d{8})$", cells["Bar"])
    if bar_day is None and "a day other than the bar's" in cells["Verdict"]:
        raise TableError(f"results/{name}: {target}'s bar is {cells['Bar']!r}, not one set on "
                         f"another day than {day}, though the file says its figure is on a day "
                         "other than the bar's")
    if bar_day is None or bar_day[1] == day:
        return None
    verdict = cells["Verdict"]
    if verdict_word(verdict.split(";")[0]) in ("met", "missed") or not verdict.startswith(
            (f"above the bar on {day}, a day other than the bar's; ",
             f"under the bar on {day} by ")):
        raise TableError(f"results/{name}: {target}'s bar is set on {bar_day[1]}, and the file, "
                         f"of {day}, gives it {verdict!r}: another day's bar is not met or "
                         "missed")
    return bar_day[1]


def other_day_row(target, name, text):
    """A target measured only on a day other than its bar's: the file's figure, said to be on its
    own day, and the file's words, above or under a bar that is another day's, never met or
    missed."""
    cells = one_target_row(target, name, text)
    day = day_of(name)
    if other_day_words(target, name, cells) is None:
        raise TableError(f"results/{name}: {target}'s bar is {cells['Bar']!r}, not one set on "
                         f"another day than {day}, and {WRITEUP_PAGE}'s table shows it so")
    return (target, cells["Bar"], f"{cells['Measured']}, on {day}", cells["Verdict"],
            host_label(text, name))


def fault_check_row(target, name, text):
    """T-ARENA-1: no bar, and a fault check its file says failed."""
    cells = one_target_row(target, name, text)
    failed = re.search(r"fault check FAIL: (.*)$", cells["Verdict"])
    if failed is None:
        raise TableError(f"results/{name}: {target}'s fault check is {cells['Verdict']!r}, not a "
                         f"FAIL, and {WRITEUP_PAGE}'s table names it as one")
    pages = header_field(text, "pages_requested", name)
    return (f"{target}, the fault check, pools' pages {pages}",
            f"{cells['Bar']}; the check: no page fault in a pool arm's timed passes",
            failed[1], "the check allows none", host_label(text, name))


def unjudged_row(target, name, text):
    """A target measured where its bar cannot judge it, by its file's own verdict."""
    cells = one_target_row(target, name, text)
    verdict, _, why = cells["Verdict"].partition(": ")
    if verdict not in ("browser differs", "host differs"):
        raise TableError(f"results/{name}: {target} is {cells['Verdict']!r}, and "
                         f"{WRITEUP_PAGE}'s table shows it as judged by no bar")
    return (target, cells["Bar"], cells["Measured"], f"no verdict, {verdict}: {why}",
            host_label(text, name))


def perf_row(target, name, text):
    """T-PERF-1 or T-PERF-2 from a file of counts by message type."""
    what, bar, verdict, figure, gap = find(
        rf"^- {target}, (.*?), against (≤ [0-9.]+): \*\*(PASS|FAIL)\*\*\. Measured ([0-9.]+) raw "
        r"\(min [0-9.]+, max [0-9.]+\), missing it by ([0-9.]+);", text, name).groups()
    if verdict != "FAIL":
        raise TableError(f"results/{name}: {target} is {verdict}, and {WRITEUP_PAGE}'s table "
                         "names it as a miss")
    design = header_field(text, "design", name).split(":")[0]
    return (target, f"{what.split(',')[0]} {bar}", f"{figure} raw, `{design}`", f"by {gap}",
            host_label(text, name))


def spread_row(target, name, text):
    """T-MM-2: the paper's average spreads against the simulation's."""
    verdict = find(rf"^## {target}: .*\n\n\*\*Verdict: (\w+)\*\*", text, name)[1]
    if verdict != "FAIL":
        raise TableError(f"results/{name}: {target} is {verdict}, and {WRITEUP_PAGE}'s table "
                         "names it as a miss")
    rows = re.findall(r"^\| ([0-9.]+) \| ([0-9.]+) \| ([0-9.]+) \| ([0-9.]+) \| (matches|does not "
                      r"match) \|$", text, re.MULTILINE)
    if not rows or all(verdict == "matches" for *_, verdict in rows):
        raise TableError(f"results/{name}: {target} has no spread that does not match")
    gap = "; ".join(f"γ = {gamma}: {two} at two decimals against {paper}"
                    for gamma, paper, _, two, verdict in rows if verdict != "matches")
    return (target, ", ".join(f"{paper} at γ = {gamma}" for gamma, paper, *_ in rows),
            ", ".join(measured for _, _, measured, *_ in rows), gap, host_label(text, name))


# Small counts a generated sentence gives in words, so that the number gate does not read
# them as figures: "nine of ten sessions" is a count of files, held by this script's --check.
COUNT_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
               "ten", "eleven", "twelve")


def in_words(count):
    if not 0 <= count < len(COUNT_WORDS):
        raise TableError(f"{count} is past the counts this script writes in words")
    return COUNT_WORDS[count]


def inv2_row(target, name, text, root):
    """T-INV-2 strict on the reference day, and how many sessions it fails on."""
    days = sorted(path.name for path in (root / "results").glob("oracle_*.md")
                  if path.name != "oracle_sessions.md")
    failing = 0
    for day in days:
        rows = oracle_rows(day, (root / "results" / day).read_text(encoding="utf-8"))
        failing += any(row[0] == target and row[3] == SHOWN["FAIL"] for row in rows)
    row = [row for row in oracle_rows(name, text) if row[0] == target][0]
    if row[3] != SHOWN["FAIL"]:
        raise TableError(f"results/{name}: {target} passes, and {WRITEUP_PAGE}'s table names it "
                         "as failing")
    return (target, "never crossed, checked strictly after every message",
            f"{day_of(name)}, {row[4]}", f"fails strictly on {in_words(failing)} of {in_words(len(days))} sessions",
            host_label(text, name))


def trade_row(target, name, text):
    """A trade-print check its day's file says failed, with its count of violations."""
    found = re.search(rf"^\| {re.escape(target)} \| \*\*(\w+)\*\* \| (\d+) \|$", text, re.MULTILINE)
    if found is None or found[1] != "FAIL":
        raise TableError(f"results/{name}: {target} did not fail, and {WRITEUP_PAGE}'s table "
                         "names it as failing")
    count = found[2]
    return (f"{target}, {day_of(name)}", "exact, with no tolerance",
            f"{count} violation{'' if count == '1' else 's'}",
            trade_gap(target, name, text), host_label(text, name))


def trade_gap(target, name, text):
    """How far a failing trade-print check is off, from the violations its file prints in full:
    T-TRADE-3's shares, the feed's against MIDAS, else every column it printed; T-TRADE-5's
    `E` prices against the best, as ITCH prints prices."""
    if target == "T-TRADE-5":
        check, shown = "e_not_at_best", r"symbol=(\S+) order at (\d+), best (\d+)"
        form = "{0} at {1} with the best at {2}"
        lead = "each `E`'s price against the best, as ITCH prints prices"
    else:
        check, shown = "t3_trade_shares", r"symbol=(\S+) feed (\d+), MIDAS (\d+)"
        form = "{0} {1} against {2}"
        lead = "shares, the feed's against MIDAS"
        if not re.search(r"^- `t3_trade_shares ", text, re.MULTILINE):
            check, shown = r"t3_\w+", r"symbol=(\S+) feed (\d+), MIDAS (\d+)"
            lead = "the feed's against MIDAS, each column printed"
    found = re.findall(rf"^- `{check} [^`]*?{shown}`$", text, re.MULTILINE)
    counted = sum(int(count) for count in re.findall(
        rf"^- check {check} \({re.escape(target)}\): (\d+) violations?$", text, re.MULTILINE))
    if not found or len(found) != counted:
        raise TableError(f"results/{name}: {target} prints {len(found)} violations in full and "
                         f"counts {counted}, so its gap cannot be given whole")
    return f"{lead}: " + "; ".join(form.format(*violation) for violation in found)


# (target, file, reader, why). A reader returns (target, bar, measured, gap, host). T-PARSE-3's
# row is parse3_entry()'s, by whether its bar's own day has a results file yet.
SPSC_WHY = ("not established: no counters were read on the queue runs",
            "no counters read; cause not established")
TRADE_WHY = "the feed above MIDAS; cause not established"
MISSED = (
    ("T-BOOK-1", BOOK_DEBIAN, missed_row, BOOK_WHY),
    ("T-BOOK-2", "bench_book_paged-flat_mapped_top_debian_12302019.md", missed_row, BOOK_WHY),
    ("T-LAT-1", BOOK_DEBIAN, missed_row, TAIL_WHY),
    ("T-LAT-2", BOOK_DEBIAN, missed_row, TAIL_WHY),
    ("T-LAT-3", BOOK_DEBIAN, missed_row, TAIL_WHY),
    ("T-LAT-4", BOOK_DEBIAN, missed_row, TAIL_WHY),
    ("T-LAT-5", BOOK_DEBIAN, missed_row, TAIL_WHY),
    ("T-DESIGN-1", BOOK_DEBIAN, missed_row,
     ("measured: the map book's extra time here is DRAM misses, not instructions; not "
      "established: why the fast book gains less over it on Zen 1 than on the Mac",
      "the map's extra time is DRAM misses")),
    ("T-DESIGN-1", "bench_book_paged-flat_ram_mac_12302019.md", missed_row,
     ("measured: the Mac's counters give no misses; not established: why the ratio is under "
      "the bar", "cause not established")),
    ("T-PERF-1", "counters_by_type_paged-flat_debian_12302019.md", perf_row,
     ("measured: messages that name an order a second old or more take most of the fills; not "
      "established: how much of the count is this host's smaller L3, since the bar was set on a "
      "Zen 3 with far more", "old orders take most fills; the host's share not established")),
    ("T-PERF-2", "counters_by_type_paged-flat_debian_12302019.md", perf_row,
     ("measured: replaces take the most a message, and adds the largest share of the whole "
      "day's; not established: why", "cause not established")),
    ("T-INV-2", "oracle_12302019.md", inv2_row,
     ("measured: every cross is in a symbol not in normal trading or inside a reopening window, "
      "so the crosses are the feed's, not the book's; T-INV-3, the scoped rule I chose for "
      "this, passes", "the feed's crosses, outside normal trading")),
    ("T-TRADE-3", "trades_01302020.md", trade_row,
     ("measured: TAP alone; MIDAS lists it twice that day and the tool sums both rows, as "
      "docs/NOTES.md records", "TAP listed twice in MIDAS")),
    ("T-TRADE-3", "trades_03272019.md", trade_row,
     ("measured: PLAB alone, the feed above MIDAS; not established: why", TRADE_WHY)),
    ("T-TRADE-3", "trades_05302019.md", trade_row,
     ("measured: ten symbols, the feed above MIDAS; not established: why", TRADE_WHY)),
    ("T-TRADE-3", "trades_08302019.md", trade_row,
     ("measured: INTC alone, the feed above MIDAS in every column; not established: why",
      TRADE_WHY)),
    ("T-TRADE-5", "trades_03272019.md", trade_row,
     ("measured: two `E` on RBZ away from the best, just after a resumption with its book "
      "crossed, and the oracle agrees with the reference book that day; not established: why",
      "just after a crossed resumption; cause not established")),
    ("T-TRADE-5", "trades_05302019.md", trade_row,
     ("measured: one `E` on ELTK, as with RBZ on the day above; not established: why",
      "just after a crossed resumption; cause not established")),
    ("T-PARSE-1", PARSE_DEBIAN, missed_row,
     ("measured: the median of the passes, where the bar is the best of three on a Zen 5 with "
      "DDR5; not established: how much of the gap is the host's, since bench_parse reads no "
      "counters", "the host's share not established")),
    ("T-PARSE-2", PARSE_DEBIAN, missed_row,
     ("as T-PARSE-1", "the host's share not established")),
    ("T-BT-3", "backtest_shadow_debian_12302019.md", missed_row,
     ("measured: the queue walk is a small share of a message's time, so it is not the cause; "
      "not established: the rest; the bar was set on a Mac, where it is met",
      "not the queue walk; cause not established")),
    ("T-MM-2", "mm_as_tables.md", spread_row,
     ("measured: other time grids move it, and the file traces it to the simulation's time "
      "grid", "the simulation's time grid")),
    ("T-SPSC-1", "spsc_queues_debian_12302019.md",
     lambda t, n, x: missed_row(t, n, x, "boost rtt / project rtt"), SPSC_WHY),
    ("T-SPSC-1", "spsc_queues_debian_12302019.md",
     lambda t, n, x: missed_row(t, n, x, "project rtt / moodycamel rtt"), SPSC_WHY),
    ("T-SPSC-1", "spsc_queues_debian_12302019.md",
     lambda t, n, x: missed_row(t, n, x, "project rtt / rigtorp rtt"), SPSC_WHY),
    ("T-SPSC-2", "spsc_ablation_debian_12302019.md",
     lambda t, n, x: missed_row(t, n, x, "cached over uncached, with %: modulo / uncached-modulo"),
     ("not established: no counters were read; the bars were set under WSL2 on another host",
      SPSC_WHY[1])),
    ("T-SPSC-2", "spsc_ablation_debian_12302019.md",
     lambda t, n, x: missed_row(t, n, x, "cached over uncached, with the mask: shipped / uncached"),
     ("as above", SPSC_WHY[1])),
    ("T-SPSC-2", "spsc_ablation_debian_12302019.md",
     lambda t, n, x: missed_row(t, n, x, "mask over %, cached: shipped / modulo"),
     ("as above", SPSC_WHY[1])),
    ("T-SPSC-2", "spsc_ablation_debian_12302019.md",
     lambda t, n, x: missed_row(t, n, x, "mask over %, uncached: uncached / uncached-modulo"),
     ("as above", SPSC_WHY[1])),
    ("T-ARENA-1", "alloc_arms_base_debian_12302019.md", fault_check_row,
     ("measured: the page faults in the timed passes; not established: their cause; a "
      "diagnostic run on a host doing other work, so not published, points to the kernel "
      "migrating small pages under the running book during compaction, and collapsing the "
      "index into huge pages, and its counts are in docs/NOTES.md",
      "cause not established")),
    ("T-ARENA-1", "alloc_arms_transparent_debian_12302019.md", fault_check_row,
     ("as with base pages", "cause not established")),
    ("T-WASM-1", "wasm_replay_mac_12302019.md", unjudged_row,
     ("the bar names Google Chrome, which is not installed here",
      "the bar's browser is not installed")),
)
# T-PARSE-3's bar is set on another day than the one its first file measured, 10302019. Until
# that day has a results file, the row is the other day's, and says 10302019 is not measured
# yet and that its run's prediction is committed; once it has one, the row is that file's
# verdict on the bar's own day, a miss with its gap, or none if met.
PARSE_BAR_DAY = "parse_rate_debian_10302019.md"
PARSE3_UNRUN = ("measured: the median on 12302019 only. Its bar is set on 10302019. It has not been "
                "measured on 10302019 yet; the prediction for that run is committed, with the day "
                "read into the bench's own memory, since on this host the file and a copy of it do "
                "not fit in memory together",
                "It has not been measured on 10302019 yet; the prediction for that run is "
                "committed")
# RESULTS.md's line on T-PARSE-3's day, in its own paragraph: (no file yet, the file).
PARSE_DAY_LINES = (
    "T-PARSE-3's bar is set on 10302019. It has not been measured on 10302019 yet; the "
    "prediction for that run is committed (docs/NOTES.md, \"The parse rate on `debian`\").",
    "T-PARSE-3 is judged on its bar's own day, 10302019, in "
    f"[results/{PARSE_BAR_DAY}](results/{PARSE_BAR_DAY}), whose input condition is the bench's "
    "own copy of the day, every page of it checked before and after each timed pass (its Input "
    "condition section); every other file's is the file in the page cache.")


def bar_day_judged(root, row):
    """A parse row of another day, whose file was written before its bar's day had one, says
    that day was not measured; once it has one, the row says where it is judged instead."""
    unrun = "; not measured on 10302019"
    if not (root / "results" / PARSE_BAR_DAY).is_file() or not row[11].endswith(unrun):
        return row
    return (*row[:11], row[11][:-len(unrun)] + f"; judged on 10302019, in results/{PARSE_BAR_DAY}",
            *row[12:])


# The input condition of a parse-rate file, by its header: with no `input_condition` line, the
# file in the page cache, which the machine check found before the first process; with one, the
# bench's own copy of the day.
CONDITION_LINE = re.compile(r"^- input_condition: (.+)$", re.MULTILINE)
CACHE_WORDS = ("the machine check found every page of the file in the page cache before the "
               "first process ran")
COPY_WORDS = ("the condition is the bench's own copy of the day: every page of it found in "
              "memory before and after each timed pass, in every process (its Input condition "
              "section), the file's cached pages recorded, not required")


def input_conditions(parse_files):
    """One plain sentence on the input condition of each parse-rate file, from its header, and,
    while no file uses the bench's own copy, that this condition is committed, not yet used."""
    said, copied = [], False
    for name, text in sorted(parse_files):
        copy = CONDITION_LINE.search(text)
        copied |= bool(copy)
        said.append(f"On [results/{name}](results/{name}) "
                    f"{COPY_WORDS if copy else CACHE_WORDS}.")
    if not copied:
        said.append("The bench's own copy of the day is the input condition the prediction for "
                    "10302019 commits, and no run has used it yet.")
    lead = "Two input conditions are used." if copied and len(said) > 1 else \
        "One input condition is used."
    return " ".join([lead, *said])


def mac_parse_words(parses):
    """Why the parse table has no Mac row, while it has none."""
    if any(row[3] != "debian" for row in parses):
        return ""
    return (" No parse rate was timed on the Mac: the three bars are set on x86 Linux, which "
            "gives a macOS figure no verdict.")


def parse_day_line(root):
    """RESULTS.md's line on T-PARSE-3's day, by whether the day has a results file."""
    return PARSE_DAY_LINES[(root / "results" / PARSE_BAR_DAY).is_file()]
PARSE3_MISSED = ("measured: the median on its bar's own day; not established: how much of the "
                 "gap is the host's, since bench_parse reads no counters",
                 "the host's share not established")


def parse3_entry(root):
    """T-PARSE-3's entry of MISSED, or None when its bar's day has a file that says met."""
    path = root / "results" / PARSE_BAR_DAY
    if not path.is_file():
        return ("T-PARSE-3", PARSE_DEBIAN, other_day_row, PARSE3_UNRUN)
    verdict = one_target_row("T-PARSE-3", PARSE_BAR_DAY, path.read_text(encoding="utf-8"))[
        "Verdict"]
    if verdict_word(verdict) == "met":
        return None
    return ("T-PARSE-3", PARSE_BAR_DAY, missed_row, PARSE3_MISSED)


def missed_entries(root):
    """MISSED with T-PARSE-3's entry in its place, after T-PARSE-2's."""
    entries = list(MISSED)
    found = parse3_entry(root)
    if found is not None:
        at = next(index for index, entry in enumerate(entries) if entry[0] == "T-PARSE-2")
        entries.insert(at + 1, found)
    return entries


# (target, predictions whose runs would have measured it, file or None, reason). Each is a
# row with no figure measured, its bar from the file named or from BENCHMARKS.md.
NOT_MEASURED = (
    ("T-BOOK-4", ("P-0502",), "bench_book_paged-flat_ram_mac_12302019.md",
     "macOS only; every try was withdrawn or stopped under the rule that withdraws a process "
     "while another is busy"),
    ("T-ARENA-1, macOS", ("P-0701",), None, "every try was withdrawn or stopped under the same "
     "rule; debian's arms are above"),
    ("T-TRADE-2, 01302019_BX", (), "trades_01302019_BX.md", None),
)
# Every other target in BENCHMARKS.md, and where it stands, so that none is left out unseen.
ACCOUNTED = {
    "T-BOOK-3": "met", "T-SIMD-1": "no bar", "T-ALLOC-1": "met", "T-ORACLE-1": "met",
    "T-ORACLE-2": "met", "T-INV-1": "met", "T-INV-3": "met", "T-TRADE-1": "met",
    "T-TRADE-2": "met on every Nasdaq day", "T-TRADE-4": "held", "T-BT-1": "met",
    "T-BT-2": "met", "T-MM-1": "met", "T-SPSC-3": "met", "T-ARENA-1": "no bar",
    "T-URING-1": "no bar", "T-URING-2": "met", "Flamegraphs": "met",
    "Fuzzing": "met",
}
# A target accounted met with no row of its own here, and the results file that must show it.
MET_BY_FILE = {"Fuzzing": "fuzz_30min_mac.md"}
# The targets no results file holds, what each asks, and the ctest entries and gates that hold
# it instead; test_results_table.py holds each name to tests/CMakeLists.txt and gates.py.
HELD_BY_TESTS = (
    ("T-BT-2", "the backtester's validity gates",
     ("test_backtest", "test_backtest_validity", "test_backtest_cli"), ()),
    ("T-MM-1", "the PnL split summing exactly to the ledger",
     ("test_attribution", "test_market_maker"), ("attribution-without-ledger",)),
)


def benchmark_targets(root):
    """Every target BENCHMARKS.md sets: its T- ids and its unnumbered Target sections."""
    text = (root / "BENCHMARKS.md").read_text(encoding="utf-8")
    ids = re.findall(r"^### (T-[A-Z0-9]+-\d+):", text, re.MULTILINE)
    ids += re.findall(r"^## (\w+)\n\n- \*\*Target:\*\*", text, re.MULTILINE)
    return text, ids


def benchmark_bar(text, target):
    heading = rf"^### {re.escape(target)}:.*" if target.startswith("T-") else rf"^## {target}\n"
    return find(rf"{heading}\n+- \*\*Target:\*\* (.*?)\.?(?: `\w+`\.)?$", text,
                "../BENCHMARKS.md")[1]


def run_count(root, target, predictions):
    """What the run log holds for a target never measured: every run of its predictions, of
    which none may be published."""
    if not predictions:
        return "no run logged"
    log = (root / "results" / "run_log.md").read_text(encoding="utf-8")
    outcomes = [outcome for prediction, outcome in
                re.findall(r"^\| P-[\w-]+ \| (P-\d+) \| [^|]* \| \w+ \| (.*) \|$", log, re.MULTILINE)
                if prediction in predictions]
    if not outcomes or any(outcome.startswith("published") for outcome in outcomes):
        raise TableError(f"results/run_log.md: {', '.join(predictions)} has a published run or "
                         f"none, and {WRITEUP_PAGE}'s table says {target} was never measured")
    withdrawn = sum(outcome.startswith("withdrawn") for outcome in outcomes)
    return (f"{len(outcomes)} runs logged under {', '.join(predictions)}, none published: "
            f"{withdrawn} withdrawn, {len(outcomes) - withdrawn} stopped or refused")


def missed_rows(root):
    """Every target missed or not measured, as (target, bar, measured, gap, host, why, short why,
    file cell, not measured), or TableError."""
    text, ids = benchmark_targets(root)
    entries = missed_entries(root)
    listed = ({target for target, *_ in entries} | {row[0].split(",")[0] for row in NOT_MEASURED}
              | set(ACCOUNTED) | ({"T-PARSE-3"} if parse3_entry(root) is None else set()))
    if set(ids) != listed:
        raise TableError(f"BENCHMARKS.md: {sorted(set(ids) - listed)} in no row or account of "
                         f"{WRITEUP_PAGE}'s table, and {sorted(listed - set(ids))} not a target")
    for target, name in MET_BY_FILE.items():
        path = root / "results" / name
        shown = path.read_text(encoding="utf-8") if path.is_file() else ""
        result = re.search(r"^- Result: (\d+) of (\d+) clean$", shown, re.MULTILINE)
        if result is None or result[1] != result[2]:
            raise TableError(f"results/{name}: {WRITEUP_PAGE}'s table accounts {target} met, and "
                             "the file does not show every target clean")
    rows = []
    for target, name, reader, (why, short) in entries:
        path = root / "results" / name
        if not path.is_file():
            raise TableError(f"results/{name}: {WRITEUP_PAGE}'s {target} row names it, and it is "
                             "not there")
        file_text = path.read_text(encoding="utf-8")
        found = (reader(target, name, file_text, root) if reader is inv2_row
                 else reader(target, name, file_text))
        rows.append((*found, why, short, source(name), False))
    for target, predictions, name, why in NOT_MEASURED:
        bare = target.split(",")[0]
        host, cited = "none", "none"
        if name is None:
            bar = benchmark_bar(text, bare)
            if "macOS" in target:
                bar, host = "none: UNCONFIRMED in BENCHMARKS.md", "macOS"
        else:
            file_text = (root / "results" / name).read_text(encoding="utf-8")
            host, cited = host_label(file_text, name), source(name)
        if name is not None and bare == "T-TRADE-2":
            bar = "exact per symbol against CrossStats"
            why = find(rf"^{bare} is not available on \w+: (.*?), so ", file_text, name)[1]
        elif name is not None:
            cells = one_target_row(bare, name, file_text)
            if not cells["Verdict"].startswith("not measured"):
                raise TableError(f"results/{name}: {bare} is {cells['Verdict']!r}, and "
                                 f"{WRITEUP_PAGE}'s table says it was never measured")
            bar = f"{cells['Bar']} ({cells['Bar set on']})"
        short = NOT_MEASURED_SHORT.get(target) or why
        if predictions:
            why = f"{why}; {run_count(root, bare, predictions)}"
        rows.append((target, bar, "not measured", "not measured", host, why, short, cited, True))
    return rows


# A not-measured row's few words, where its full why is not already few.
NOT_MEASURED_SHORT = {"T-BOOK-4": "macOS only; every try withdrawn or stopped",
                      "T-ARENA-1, macOS": "every try withdrawn or stopped"}
MISSED_ANCHOR = "every-target-missed-or-not-measured"


def missed_full(root):
    """The lines of RESULTS.md's table of every target missed or not measured: each row with its
    bar, figures, gap and why in full."""
    lines = ["Each target missed or not measured, a row for each file, host, day or comparison: "
             "its bar, what was measured, the gap and why. Each why gives what was measured, then "
             "what is not established.", ""]
    lines += markdown_table(("Target", "Bar", "Measured", "Gap", "Host", "Why", "File"),
                            [row[:3] + (ratio_gap(row[3]),) + row[4:6] + row[7:8]
                             for row in missed_rows(root) if not row[8]])
    lines += [f"| {target} | {bar} | {measured} | {gap} | {host} | {why} | {cited} | "
              f"{NOT_MEASURED_MARK}"
              for target, bar, measured, gap, host, why, _, cited, unmeasured in missed_rows(root)
              if unmeasured]
    return lines


MISS_OPENING = (
    "Each line is on the Zen 1 host unless it names the M4 Max, and gives the deciding row, or the "
    "worst. Where a bar is another host's published figure, the host's share of the gap was not "
    "measured. In the book's runs, messages naming an order a second old or "
    "more make most DRAM fills.")
# What each target asks, and whose bar it is, in words a stranger reads alone; and its why in a
# few plain words, "" for none, or None for a trade check's, which is its row's. Only words are
# written here: every figure comes from a row.
PLAIN = {
    "T-BOOK-1": ("every symbol's book from a whole day, vs itch-book, an open-source book",
                 ""),
    "T-BOOK-2": ("the same, tracking the best bid and offer, vs itch-book",
                 ""),
    "T-LAT-1": ("each operation's latency, vs itch-book", ""),
    "T-LAT-2": ("every message's latency, vs nasdaq-feed-handler, an open-source book, "
                "under WSL2", ""),
    "T-LAT-3": ("the budget Nasdaq's peak rate sets, on any host", ""),
    "T-LAT-4": ("every message's latency, vs carteret, an open-source FIFO book, same day, "
                "timer cost subtracted", ""),
    "T-LAT-5": ("the tightest tail published, PitchFork's, an open-source book, nothing "
                "subtracted", ""),
    "T-DESIGN-1": ("speed over a `std::map` book in the same run",
                   "the map book's extra time is DRAM misses"),
    "T-PERF-1": ("last-level misses, a floor on DRAM fills, per book message (one that can change "
                 "a book), vs carteret on a Zen 3", ""),
    "T-PERF-2": ("branch misses per message, vs Market-Core, an open-source project", ""),
    "T-INV-2": ("the never-crossed rule",
                "every cross is the feed's, outside normal trading or inside a reopening window"),
    "T-TRADE-3": ("each symbol's continuous volume vs SEC MIDAS", None),
    "T-TRADE-5": ("every continuous execution at the best price", None),
    "T-PARSE-1": ("the parser's framing walk, vs itch-book", ""),
    "T-PARSE-2": ("decoding the types that change the book, vs itch-book", ""),
    "T-PARSE-3": ("the strict decode of every field, vs Lunary's benchmark", ""),
    "T-BT-3": ("the backtester's shadow-order replay, vs queuetruth, an open-source project, "
               "on a Mac", "not the queue walk, a small share of the time"),
    "T-MM-2": ("Avellaneda and Stoikov's own average spreads",
               "the simulation's time grid moves it"),
    "T-SPSC-1": ("the ring's round trip vs boost, moodycamel and rigtorp",
                 ""),
    "T-SPSC-2": ("the known ring techniques' gains, vs Frasch's talk",
                 ""),
    "T-ARENA-1": ("the pool allocator, with no published bar", ""),
    "T-WASM-1": ("the browser replay vs native", ""),
    "T-BOOK-4": ("the rebuild on Apple silicon on a busier day", ""),
    "T-TRADE-2": ("each symbol's cross volume vs Nasdaq's CrossStats", ""),
}
# A trade-print row's few words, by its short why, in plain words.
PLAIN_TRADE_WHY = {
    "TAP listed twice in MIDAS": "MIDAS lists TAP twice that day",
    "the feed above MIDAS; cause not established":
        "the feed counts more shares than MIDAS",
    "just after a crossed resumption; cause not established":
        "both just after a crossed book resumed",
}
# Each ring comparison of T-SPSC-1 and T-SPSC-2 in words.
RING_WORDS = {
    "boost rtt / project rtt": "boost's round trip over this ring's",
    "project rtt / moodycamel rtt": "this ring's round trip over moodycamel's",
    "project rtt / rigtorp rtt": "this ring's round trip over rigtorp's",
    "cached over uncached, with %: modulo / uncached-modulo":
        "caching the indices, with `%` wrapping,",
    "cached over uncached, with the mask: shipped / uncached":
        "caching the indices, with a mask wrapping,",
    "mask over %, cached: shipped / modulo": "a mask over `%`, with cached indices,",
    "mask over %, uncached: uncached / uncached-modulo": "a mask over `%`, with uncached indices,",
}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")


def day_words(day):
    """A day as Nasdaq names its file, month first, in words: 12302019 is 30 December 2019."""
    found = re.fullmatch(r"(\d\d)(\d\d)(\d{4})(?:_BX)?", day)
    if found is None:
        raise TableError(f"{day!r} is not a day")
    return f"{int(found[2])} {MONTHS[int(found[1]) - 1]} {found[3]}"


def bar_words(bar):
    """A bar's first figure, its sign in words: "≥ 16.4 M msg/s (...)" as "16.4 M msg/s"."""
    bar = re.sub(r" \([^()]*\)$", "", bar).split(" and ")[0].split(", ")[0]
    return re.sub(r"^[≥≤] ", "", bar)


def bounded(bar):
    """"at least" or "at most", by the bar's sign."""
    return "at most" if "≤" in bar else "at least"


def gap_figure(gap):
    """The figure of a gap: "rate by 11.25 M msg/s" or "410.02 M msg/s and 12.62 GB/s under the
    bar" as "11.25 M msg/s" or "410.02 M msg/s"."""
    gap = re.sub(r"^(?:rate )?by ", "", gap.split(", ")[0])
    return re.sub(r" under the bar$", "", gap.split(" and ")[0])


def rate_line(rows):
    """A rate vs a bar it is under."""
    _, bar, measured, gap, *_ = rows[0]
    measured = measured.removeprefix("rate ").split(", ")[0]
    return (f"{measured} vs {bar_words(bar)}, {gap_figure(gap)} short", 0, rows[0])


def latency_bar(bar, item):
    """The bar's figure for one quantile of one operation, or of every message."""
    operation, _, quantile = item.rpartition(" ")
    figure = r"\d+(?:,\d{3})*(?:\.\d+)?"
    found = re.search(rf"(?<![\w.]){re.escape(quantile)} ≤ ({figure})", bar)
    if found and not operation:
        return f"{found[1]} ns"
    quantiles = re.search(r" ns at (p[\d.]+(?:, p[\d.]+)*)", bar)
    values = re.search(rf"\b{re.escape(operation)} ({figure}(?:, {figure})*)", bar) \
        if operation else None
    if quantiles is None or values is None:
        raise TableError(f"no bar for {item!r} in {bar!r}")
    return f"{values[1].split(', ')[quantiles[1].split(', ').index(quantile)]} ns"


def latency_line(rows):
    """The first quantile a latency target misses."""
    _, bar, measured, gap, *_ = rows[0]
    gaps = gap.split(", ")
    item, _, over = gaps[0].partition(" by ")
    value = next(part.removeprefix(item + " ") for part in measured.split("; ")
                 if part.startswith(item + " "))
    operation, _, quantile = item.rpartition(" ")
    who = "a message" if not operation else f"an {operation}" if operation[0] in "aeiou" \
        else f"a {operation}"
    return (f"at {quantile} {who} takes {value} vs {latency_bar(bar, item)}, {over} over",
            len(gaps) - 1, rows[0])


def ratio_line(rows):
    """T-DESIGN-1: the ratio over the map book on a fresh heap, and how far below the bar."""
    _, bar, measured, gap, *_ = rows[0]
    name, ratio = re.match(r"over (\S+) (\S+×)", measured).groups()
    below = re.match(rf"over {re.escape(name)} by ([\d.]+)×", gap)[1]
    book = dict((key, words) for key, words, _ in MAP_WORDS)[name]
    more = sum(len(row[3].split(", ")) for row in rows) - 1
    limit = re.match(r"[≥≤] ([\d.]+×)", bar)[1]
    return (f"measured at {ratio} the speed of {book} vs {limit}: missed, {below} below it",
            more, rows[0])


def perf_line(rows):
    """T-PERF-1 or T-PERF-2: the figure per message, or per book message, as its bar counts it."""
    _, bar, measured, gap, *_ = rows[0]
    figure = measured.split(" ")[0]
    over, limit = re.search(r"\bper (book message|message) ≤ ([\d.]+)$", bar).groups()
    return (f"{figure} per {over} vs at most {limit}, {gap.removeprefix('by ')} over", 0,
            rows[0])


def inv_line(rows):
    return (f"on the M4 Max it {rows[0][3]}", 0, rows[0])


def trade_line(rows):
    """The day with the most violations."""
    worst = max(rows, key=lambda row: int(row[2].split(" ")[0]))
    day = worst[0].split(", ")[1]
    return (f"on the M4 Max, {worst[2]} on {day_words(day)}, none allowed",
            len(rows) - 1, worst)


def parse3_line(rows):
    """T-PARSE-3 on a day other than its bar's: its figure, and that the bar's day is not yet
    measured, in the words WRITEUP.md's prose uses too."""
    _, bar, measured, *_ = rows[0]
    day = re.search(r", on (\d{8})$", measured)[1]
    bar_day = re.search(r", on (\d{8})$", bar)[1]
    return (f"{measured.split(', ')[0]} on {day}, above its bar of {bar_words(bar)}, set on "
            f"{bar_day}. {PARSE3_UNRUN[1]}", 0, rows[0])


def spread_line(rows):
    """T-MM-2: the spread that does not match the paper's."""
    _, bar, measured, gap, *_ = rows[0]
    gammas = re.findall(r"at γ = ([\d.]+)", bar)
    misses = gap.split("; ")
    gamma, rest = re.match(r"γ = ([\d.]+): (.*)$", misses[0]).groups()
    value = measured.split(", ")[gammas.index(gamma)]
    rest = rest.replace(" against ", " vs the paper's ")
    if not 0 < len(misses) < len(gammas):
        raise TableError(f"T-MM-2: {len(misses)} of {len(gammas)} spreads do not match")
    return (f"on the M4 Max the spread at γ = {gamma} is {value}, {rest}", len(misses) - 1,
            rows[0])


def ring_line(rows):
    """The ring comparison furthest from its bar, named in words."""
    def distance(row):
        figure, limit = float(row[2].rstrip("×")), float(bar_words(row[1]).rstrip("×"))
        return figure / limit if "≤" in row[1] else limit / figure
    worst = max(rows, key=distance)
    words = RING_WORDS[worst[0].split(", ", 1)[1]]
    return (f"{words} is measured at {worst[2]} vs a bar of {bounded(worst[1])} "
            f"{bar_words(worst[1])}",
            len(rows) - 1, worst)


def arena_line(rows):
    """T-ARENA-1: the pool arm with the most page faults in its timed passes."""
    judged = [row for row in rows if not row[8]]
    found = [(int(count), arm, row) for row in judged
             for arm, count in re.findall(r"(pool-\w+) (\d+)", row[2])]
    count, arm, row = max(found, key=lambda item: item[0])
    pages = row[0].rsplit(" ", 1)[1]
    return (f"its fault check allows no page fault in a timed pass; on {pages} pages `{arm}` "
            f"takes {count}", len(found) - 1 + len(rows) - len(judged), row)


def wasm_line(rows):
    _, bar, measured, gap, *_ = rows[0]
    used, wanted = re.search(r"browser differs: (.+?) [\d.]+, not (.+?);", gap).groups()
    limit = re.match(r"≤ ([\d.]+×)", bar)[1]
    return (f"on the M4 Max, {measured} native vs at most {limit}, but in {used}, not "
            f"{wanted}: no verdict", 0, rows[0])


def book4_line(rows):
    figure, day, machine = re.match(r"≥ (.+?) on (\d{8}) \((.+?), ", rows[0][1]).groups()
    return (f"not measured: its bar, {figure} on {day_words(day)}, is an {machine}'s; every M4 Max "
            "run was dropped while other work ran", 0, rows[0])


def cross_line(rows):
    day = rows[0][0].split(", ")[1]
    return (f"not measured on {day_words(day)}, a BX day: CrossStats lists only Nasdaq's own "
            "crosses", 0, rows[0])


PLAIN_LINES = {"T-BOOK-1": rate_line, "T-BOOK-2": rate_line, "T-LAT-1": latency_line,
               "T-LAT-2": latency_line, "T-LAT-3": latency_line, "T-LAT-4": latency_line,
               "T-LAT-5": latency_line, "T-DESIGN-1": ratio_line, "T-PERF-1": perf_line,
               "T-PERF-2": perf_line, "T-INV-2": inv_line, "T-TRADE-3": trade_line,
               "T-TRADE-5": trade_line, "T-PARSE-1": rate_line, "T-PARSE-2": rate_line,
               "T-BT-3": rate_line, "T-MM-2": spread_line, "T-SPSC-1": ring_line,
               "T-SPSC-2": ring_line, "T-ARENA-1": arena_line, "T-WASM-1": wasm_line,
               "T-BOOK-4": book4_line, "T-TRADE-2": cross_line}


def missed_targets(root):
    """The lines of WRITEUP.md's list of every target missed or not measured: one plain sentence
    a target, which a reader can take alone, from the row that decides its verdict or the
    worst, with how many more rows RESULTS.md has, or TableError. One figure vs its bar,
    the gap in words; whose bar it is in the target's words; no cut cell, ellipsis or code word.
    That the host's share of a gap is not established is said once, above the lines."""
    groups = {}
    for row in missed_rows(root):
        groups.setdefault(row[0].split(",")[0], []).append(row)
    lines = [MISS_OPENING, ""]
    for target, rows in groups.items():
        if target not in PLAIN:
            raise TableError(f"{target} has no plain words in {WRITEUP_PAGE}'s list")
        gloss, why = PLAIN[target]
        reader = PLAIN_LINES.get(target)
        if target == "T-PARSE-3":
            # On another day than its bar's (other_day_row), else missed on the bar's own day.
            reader = parse3_line if re.search(r", on \d{8}$", rows[0][2]) else rate_line
        sentence, more, row = reader(rows)
        if why is None:
            why = PLAIN_TRADE_WHY[row[6]]
        more = f"; {in_words(more)} more in RESULTS.md" if more else ""
        cited = "" if row[7] == "none" else f" [file]({row[7].split('](')[1]}"
        tail = f"; {why}" if why else ""
        line = f"- **{target}, {gloss}:** {sentence[0].upper()}{sentence[1:]}{more}{tail}.{cited}"
        lines.append(f"{line} {NOT_MEASURED_MARK}" if row[8] else line)
    lines += ["", f"Every row, with its why in full: [RESULTS.md](RESULTS.md#{MISSED_ANCHOR})."]
    return lines


SHORT_HOSTS = {"debian": "Zen 1", "macOS": "M4 Max"}


# A row's verdict as the summary and the number gate read it: met, missed, or None for a row
# no bar judges (another host, browser or day, not measured, not available, no bar). A fault
# check that failed is a miss: T-ARENA-1 has no bar, and its check allows no page fault.
def verdict_class(verdict):
    word = verdict.replace("**", "").strip()
    clean = re.fullmatch(r"(\d+) of (\d+) clean", word)
    if re.match(r"(?:PASS|met)\b", word) or (clean and clean[1] == clean[2]):
        return "met"
    if re.match(r"(?:FAIL|missed)\b", word) or "fault check FAIL" in word:
        return "missed"
    return None


def host_kind(host):
    """macOS or debian, as a row's host cell names it, else the cell."""
    if host.startswith(("Darwin", "macOS")):
        return "macOS"
    return "debian" if host.startswith("debian") else host


def target_rows(root, lines=None):
    """Every row of RESULTS.md's tables that gives a target, or the fuzzers' run, a verdict,
    as (target, day, host, design, verdict, class); and T-ARENA-1's fault check, one row for
    each allocator file. lines are generate()'s, made here when not given."""
    lines = generate(root) if lines is None else lines
    rows, heads = [], None
    for index, line in enumerate(lines):
        if line.startswith("|---"):
            continue
        if not line.startswith("| "):
            heads = None
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if index + 1 < len(lines) and lines[index + 1].startswith("|---"):
            heads = cells
            continue
        if heads is None or "Verdict" not in heads:
            continue
        named = dict(zip(heads, cells))
        target = named.get("Target", named.get("Check", ""))
        if not TARGET_ID.fullmatch(target) and target != "Fuzzing":
            continue
        design = named.get("Design", named.get("Comparison", named.get("Workload", "")))
        rows.append((target, named.get("Day", "none"), host_kind(named.get("Host", "none")),
                     design.strip("`"), named["Verdict"], verdict_class(named["Verdict"])))
    for path in sorted((root / "results").glob("alloc_*.md")):
        text = path.read_text(encoding="utf-8")
        fault_check = header_field(text, "fault_check", path.name)
        rows.append(("T-ARENA-1", day_of(path.name), host_kind(header_field(text, "host",
                                                                            path.name)),
                     header_field(text, "pages_requested", path.name),
                     f"{ARENA_VERDICT}{fault_check}", verdict_class(fault_check)))
    return rows


def one_verdict(rows):
    """A target's one verdict from its rows: missed if any row its bar judges is missed or
    failed, met if every such row is met or passed, None if its bar judges none."""
    classes = {row[-1] for row in rows} - {None}
    if not classes:
        return None
    return "missed" if "missed" in classes else "met"


def unrowed_verdict(target):
    """The verdict of a target that has no row: met for one held by tests, met or None from
    ACCOUNTED (no bar, held), and KeyError for a target this script does not account."""
    if target in {held[0] for held in HELD_BY_TESTS}:
        return "met"
    return "met" if ACCOUNTED[target] == "met" else None


def benchmark_title(text, target):
    """A target's title in BENCHMARKS.md: the words after its id, or for a section with no id
    its target's words, to the first `, from`."""
    if target.startswith("T-"):
        return find(rf"^### {re.escape(target)}: (.*)$", text, "../BENCHMARKS.md")[1]
    return benchmark_bar(text, target).split(", from ")[0]


# The flamegraph target: a user-space flamegraph of each hot path on debian, beside the
# counters, with the frame-pointer build's throughput published beside the normal build's.
FLAMEGRAPH_FILES = "flamegraph_*_debian_*.md"
FRAME_POINTER_FILES = ("bench_book_paged-flat_mapped_fpbuild_debian_12302019.md",
                       "bench_book_paged-flat_mapped_normalbuild_debian_12302019.md")


def flamegraphs_stand(root):
    drawn = sorted((root / "results").glob(FLAMEGRAPH_FILES))
    missing = [name for name in FRAME_POINTER_FILES if not (root / "results" / name).is_file()]
    if not drawn or missing:
        raise TableError(f"results/: Flamegraphs is accounted met, and there are {len(drawn)} "
                         f"flamegraph files on debian and {missing or 'no'} frame-pointer build "
                         "files missing")
    return (f"debian: {len(drawn)} flamegraphs, in the profiles table, and the frame-pointer "
            "build's throughput beside the normal build's, in the book designs table")


def how_rows_stand(rows):
    """A target's rows, counted by host and by the verdict's words: "debian: 4 missed"."""
    hosts = {}
    for _, _, host, _, verdict, _ in rows:
        verdict = verdict.replace("**", "")
        if verdict.startswith(ARENA_VERDICT):
            verdict = "fault check " + verdict.removeprefix(ARENA_VERDICT)
        word = re.split(r"[:;]", verdict)[0].strip()
        word = re.sub(r"^(\d+) of \1 clean$", "run, every target clean", word)
        word = re.sub(r" on \d{8}(?:_[A-Z]+)?, a day other than the bar's$", " on another day",
                      word)
        hosts.setdefault(host, {}).setdefault(word, 0)
        hosts[host][word] += 1
    return "; ".join(f"{host}: " + ", ".join(f"{count} {word}" for word, count in words.items())
                     for host, words in hosts.items())


# A target with no row: what stands for its verdict, beside ACCOUNTED's word for it.
UNROWED_HOW = {"no bar": "no bar to judge, so no verdict", "held": "held, so not run",
               "met": "met, as accounted in scripts/results_table.py"}


def summary_rows(root):
    """(target, what it asks, verdict as shown, how its rows stand) for every target
    BENCHMARKS.md sets, in its order: its one verdict (one_verdict()) and how its rows stand."""
    text, ids = benchmark_targets(root)
    rows = {}
    for row in target_rows(root):
        rows.setdefault(row[0], []).append(row)
    held = {target: tests for target, _, tests, _ in HELD_BY_TESTS}
    lines = []
    for target in ids:
        found = rows.get(target, [])
        if found:
            verdict = one_verdict(found)
            shown = {"met": "met", "missed": "**missed**", None: "no verdict"}[verdict]
            stand = how_rows_stand(found)
        elif target in held:
            shown, stand = "met", f"held by tests, not by a results file: {', '.join(held[target])}"
        elif target == "Flamegraphs" and ACCOUNTED.get(target) == "met":
            shown, stand = "met", flamegraphs_stand(root)
        else:
            word = ACCOUNTED.get(target)
            if word not in UNROWED_HOW:
                raise TableError(f"BENCHMARKS.md: {[target]} in no row or account of "
                                 f"{RESULTS_PAGE}'s summary")
            shown = "met" if word == "met" else "no verdict"
            stand = UNROWED_HOW[word]
        lines.append((target, benchmark_title(text, target), shown, stand))
    return lines


def summary(root):
    """The lines of RESULTS.md's summary: one row a target, from summary_rows()."""
    return (["One row a target of [BENCHMARKS.md](BENCHMARKS.md), in its order. A target's one "
             "verdict: missed if any row its bar judges below is missed or FAIL, met if every "
             "such row is met or PASS, and no verdict if its bar judges none (another host, "
             "browser or day, or not measured). The last column counts the rows below by host "
             "and verdict.", ""]
            + markdown_table(("Target", "What it asks", "Verdict", "The rows below"),
                             summary_rows(root)))


TALLY_BEGIN = ("<!-- verdict count: begin, generated by scripts/results_table.py, do not "
               "edit -->")
TALLY_END = "<!-- verdict count: end -->"


def tally(root):
    """The README's line under its list of what is built: how many of the summary's targets
    are met, missed and of no verdict, so that a short list hides no miss."""
    counts = {"met": 0, "**missed**": 0, "no verdict": 0}
    for _, _, shown, _ in summary_rows(root):
        counts[shown] += 1
    met, missed, none = counts.values()
    are = lambda count: "is" if count == 1 else "are"  # noqa: E731
    have = "has" if none == 1 else "have"
    return [f"Of the {met + missed + none} targets in [RESULTS.md's summary](RESULTS.md#summary), "
            f"{met} {are(met)} met, {missed} {are(missed)} missed and {none} {have} no verdict."]


# Each page, the markers its generated lines sit between, and what generates them.
PAGES = ((RESULTS_PAGE, SUMMARY_BEGIN, SUMMARY_END, summary),
         (RESULTS_PAGE, BEGIN, END, generate), (README_PAGE, HEAD_BEGIN, HEAD_END, headline),
         (RESULTS_PAGE, FULL_MISS_BEGIN, FULL_MISS_END, missed_full),
         (WRITEUP_PAGE, MISS_BEGIN, MISS_END, missed_targets),
         (README_PAGE, TALLY_BEGIN, TALLY_END, tally))


def region_of(text, begin=BEGIN, end=END):
    """(start, end) line indices of what lies between the markers, or None."""
    lines = text.splitlines()
    if lines.count(begin) != 1 or lines.count(end) != 1:
        return None
    start, stop = lines.index(begin) + 1, lines.index(end)
    return (start, stop) if start <= stop else None


def page_region(root, page, begin, end):
    """The page's lines and its region, or None after saying what is wrong."""
    path = root / page
    if not path.is_file():
        print(f"results_table: FAIL {page}: the page is not there")
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    region = region_of("\n".join(lines), begin, end)
    if region is None:
        print(f"results_table: FAIL {page}: the two marker lines are not there exactly once")
        return None
    return lines, region


def check_page(root, page, begin, end, fresh):
    found = page_region(root, page, begin, end)
    if found is None:
        return 1
    text, (start, stop) = found
    lines = text[start:stop]
    differs = False
    for index in range(max(len(lines), len(fresh))):
        have = lines[index] if index < len(lines) else "<no line>"
        want = fresh[index] if index < len(fresh) else "<no line>"
        if have != want:
            print(f"results_table: FAIL {page}:{start + index + 1}: differs from a fresh run")
            print(f"  have: {have}")
            print(f"  want: {want}")
            differs = True
    if differs:
        return 1
    print(f"results_table: ok, {page} matches {len(fresh)} generated lines")
    return 0


def check(root, fresh):
    """Every page against its fresh lines, each page checked even when another fails."""
    codes = [check_page(root, page, begin, end, fresh[page, begin])
             for page, begin, end, _ in PAGES]
    return max(codes)


def write(root, fresh):
    for page, begin, end, _ in PAGES:
        found = page_region(root, page, begin, end)
        if found is None:
            return 1
        lines, (start, stop) = found
        (root / page).write_text("\n".join(lines[:start] + fresh[page, begin] + lines[stop:])
                                 + "\n",
                                 encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        fresh = {(page, begin): make(root) for page, begin, _, make in PAGES}
    except (TableError, OSError) as error:
        print(f"results_table: FAIL {error}")
        return 1
    if args.write:
        return write(root, fresh)
    if args.check:
        return check(root, fresh)
    for page, begin, _, _ in PAGES:
        print(f"{page}:")
        print("\n".join(fresh[page, begin]))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
