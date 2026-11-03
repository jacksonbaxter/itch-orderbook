#!/usr/bin/env python3
"""Refuses a benchmark results file that claims more than the run could show.

    check_results.py FILE...

Reads the file as published, so it holds whatever wrote it to the same rules: every
header field present, an unreadable one written as `unavailable: <reason>`, no pin that
was not read back, no percentile below the timer's floor, no multiplexed counter called
measured, the warm-up out of the timed counts, the input resident, load lines stamped
from a real measure.sh run, machine_check's output present and passing, and nothing
private. scripts/bench_driver.py and scripts/stamp_load.py run the same checks, so that
a file failing them is never written as finished.

A book file, any file not labelled as the Phase 2 stub-book harness proof, is held to more:
the design, input and top-of-book fields, a statement of how the run read the day beside
itch-book's two methods, the latency table with its pair-subtracted
column, the per-operation table, the book's memory, the summary's service and batch
means, the top of book when it was tracked, the design comparison, and a verdict for every
target in words from a fixed set. On macOS, a bar set on x86 Linux may not be called met
or missed.

Each violation prints as `check_results: FAIL <code> <file>: <detail>`.

Exit codes: 0 every file passed, 1 at least one failed, 2 bad usage.
"""

from __future__ import annotations

import argparse
import re
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
TARGETS = ("T-BOOK-1", "T-BOOK-2", "T-BOOK-3", "T-BOOK-4", "T-LAT-1", "T-LAT-2", "T-LAT-3",
           "T-LAT-4", "T-LAT-5", "T-DESIGN-1")
# Bars set on x86 hardware, which a macOS figure neither passes nor fails.
X86_TARGETS = ("T-BOOK-1", "T-BOOK-2", "T-LAT-1", "T-LAT-2", "T-LAT-4", "T-LAT-5")
VERDICTS = ("met", "missed: ", "host differs: ", "below floor: ", "not measured in this run: ")
BELOW_FLOOR = "below floor"
HOST_LABELS = ("debian", "macOS (M4 Max)", "unknown")
MAC_HOST = "macOS (M4 Max)"
# Fields macOS has no way to read, so any value other than unavailable is made up.
MAC_UNREADABLE = ("governor", "frequency", "turbo", "isolation", "rdpmc")
PENDING = "pending: stamp with scripts/stamp_load.py"

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


def check_fields(fields):
    for name in REQUIRED_FIELDS:
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


def check_percentiles(text, fields):
    floor = number(fields.get("floor", ""))
    table = section(text, "Latency")
    if table is None:
        yield "latency-table-missing", "no Latency section"
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


def check_book(text, fields):
    for found in (check_book_fields(fields), check_book_latency(text, fields),
                  check_operations(text, fields), check_book_summary(text), check_memory(text),
                  check_method(text), check_top(text, fields), check_comparison(text, fields),
                  check_targets(text, fields)):
        yield from found


def check_text(text):
    """Every violation in one results file, as (code, detail) pairs."""
    fields = parse_header(text)
    if not fields:
        return [("header-missing", "no Header section")]
    violations = []
    for found in (check_fields(fields), check_host(fields), check_pin(fields),
                  check_residency(fields), check_warmup(fields), check_percentiles(text, fields),
                  check_counters(text), check_statistics(text), check_load(fields),
                  check_machine(text), check_private(text, fields)):
        violations.extend(found)
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
