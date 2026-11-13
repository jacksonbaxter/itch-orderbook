#!/usr/bin/env python3
"""Black-box tests of what three tools accept and count, apart from their own runs.

Usage: test_tools_cli.py <itch_alloc> <oracle fixtures dir>

itch_alloc's runs are tested in test_oracle_cli.py; this holds its capacities: --max-orders
and --max-levels of 0 or past 32 bits are bad usage on every book it can run. A failure
prints `tools cli: FAIL <case>: <reason>`.
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


def main():
    alloc, oracle_fixtures = sys.argv[1:3]
    session = Path(oracle_fixtures) / "session.itch"
    failures = alloc_failures(alloc, session)
    for failure in failures:
        print(f"tools cli: FAIL {failure}")
    if failures:
        return 1
    print("tools cli: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
