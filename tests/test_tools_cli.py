#!/usr/bin/env python3
"""Black-box tests of what three tools accept and count, apart from their own runs.

Usage: test_tools_cli.py <itch_alloc> <itch_as_tables> <oracle fixtures dir>

itch_alloc's runs are tested in test_oracle_cli.py; this holds its capacities: --max-orders
and --max-levels of 0 or past 32 bits are bad usage on every book it can run.
itch_as_tables's tables are held by scripts/as_tables_report.py check; this holds its numbers:
each is all of its argument, and γ is above 0 and finite. A failure prints
`tools cli: FAIL <case>: <reason>`.
"""

import subprocess
import sys
from pathlib import Path


def alloc_failures(alloc, session):
    """A capacity of 0 or past 32 bits is bad usage, never a book with one hash slot: 0
    orders gave the hash indexes a shift of 64, and 2^32 wrapped to 0. 1 is a capacity."""
    failures = []
    for flag, value in (("--max-orders", "0"), ("--max-orders", "4294967296"),
                        ("--max-levels", "0"), ("--max-levels", "4294967297")):
        for book in ([], ["--index", "hash"], ["--allocator", "pool-index"]):
            code = subprocess.run([alloc, str(session), *book, flag, value], capture_output=True,
                                  check=False).returncode
            if code != 2:
                failures.append(f"alloc capacity {flag} {value} {' '.join(book)}: exit {code}, "
                                "wanted 2")
    code = subprocess.run([alloc, str(session), "--index", "hash", "--max-orders", "1",
                           "--max-levels", "1"], capture_output=True, check=False).returncode
    if code != 1:
        failures.append(f"alloc capacity of 1: exit {code}, wanted 1, the book refusing adds")
    return failures


def as_tables_failures(as_tables):
    """stoi and stod read "5abc" as 5 and "0.1x" as 0.1, stoull "-1" as 2^64 - 1, and γ of 0,
    below it or not finite gave NaN quotes."""
    failures = []
    for name, args in (("trailing paths", ["--paths", "5abc"]),
                       ("trailing gamma", ["--paths", "5", "--gamma", "0.1x"]),
                       ("trailing seed", ["--paths", "5", "--seed", "7x"]),
                       ("negative seed", ["--paths", "5", "--seed", "-1"]),
                       ("paths past int", ["--paths", "99999999999"]),
                       ("one path", ["--paths", "1"])) + tuple(
            (f"gamma {value}", ["--paths", "5", "--gamma", value])
            for value in ("0", "-1", "nan", "inf", "1e12")):
        done = subprocess.run([as_tables, *args], capture_output=True, text=True, check=False)
        if done.returncode != 2 or "nan" in done.stdout:
            failures.append(f"as_tables {name}: exit {done.returncode}, wanted 2")
    done = subprocess.run([as_tables, "--paths", "5", "--seed", "7", "--gamma", "0.1"],
                          capture_output=True, text=True, check=False)
    if done.returncode != 0 or "as_tables paths 5 seed 7 " not in done.stdout \
            or "table gamma 0.1" not in done.stdout or "nan" in done.stdout:
        failures.append(f"as_tables good numbers: exit {done.returncode}: {done.stdout}")
    return failures


def main():
    alloc, as_tables, oracle_fixtures = sys.argv[1:4]
    session = Path(oracle_fixtures) / "session.itch"
    failures = alloc_failures(alloc, session) + as_tables_failures(as_tables)
    for failure in failures:
        print(f"tools cli: FAIL {failure}")
    if failures:
        return 1
    print("tools cli: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
