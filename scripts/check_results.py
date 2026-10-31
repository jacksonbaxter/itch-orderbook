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
        if value == "unresolved":
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
