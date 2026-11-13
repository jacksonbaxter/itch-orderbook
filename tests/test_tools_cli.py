#!/usr/bin/env python3
"""Black-box tests of what three tools accept and count, apart from their own runs.

Usage: test_tools_cli.py <itch_alloc> <itch_as_tables> <itch_window> <oracle fixtures dir>

itch_alloc's runs are tested in test_oracle_cli.py; this holds its capacities: --max-orders
and --max-levels of 0 or past 32 bits are bad usage on every book it can run.
itch_as_tables's tables are held by scripts/as_tables_report.py check; this holds its numbers:
each is all of its argument, and γ is above 0 and finite.
itch_window's whole-day counts are held by scripts/window_report.py; this holds its search
count at a U on a stream built here, every count worked by hand. A failure prints
`tools cli: FAIL <case>: <reason>`.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from gen_oracle_fixtures import Stream  # noqa: E402

DOLLAR = 10_000  # Price4


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


def window_stream():
    """Four sides, one locate each. FlatLevels searches for a U's new price after the old order
    has gone, so a level that order held alone is closed by then."""
    s = Stream()
    # 1, review finding r2-3: the best bid, alone at 100.00, moves behind 99.00 to 98.00. Adds
    # pass 0 and 1 better levels; the U's search passes 99.00 alone, so 1, not 2. Its old
    # level's close passes 0.
    s.add(1, 1, "B", 100, 100 * DOLLAR)
    s.add(1, 2, "B", 100, 99 * DOLLAR)
    s.replace(1, 1, 3, 100, 98 * DOLLAR)
    # 2: the same U with another order still at 100.00: nothing closes, so 100.00 and 99.00,
    # 2. The adds pass 0, 0 and 1.
    s.add(2, 11, "B", 100, 100 * DOLLAR)
    s.add(2, 12, "B", 100, 100 * DOLLAR)
    s.add(2, 13, "B", 100, 99 * DOLLAR)
    s.replace(2, 11, 14, 100, 98 * DOLLAR)
    # 3: 99.00, alone, moves ahead to 101.00. Its level was worse than the new price, so its
    # closing takes nothing off: 0. The adds pass 0 and 1, and its close passes 100.00, 1.
    s.add(3, 21, "B", 100, 100 * DOLLAR)
    s.add(3, 22, "B", 100, 99 * DOLLAR)
    s.replace(3, 22, 23, 100, 101 * DOLLAR)
    # 4: case 1 on the ask side, 100.00 alone moving behind 101.00 to 102.00: 0, 1, then 1.
    s.add(4, 31, "S", 100, 100 * DOLLAR)
    s.add(4, 32, "S", 100, 101 * DOLLAR)
    s.replace(4, 31, 33, 100, 102 * DOLLAR)
    s.system("C")
    return s.data


# Adds 0: 1 + 2 + 2 + 1 (the first add of each side, case 2's second at 100.00 and case 3's
# U); 1: 2 + 1 + 1 + 2 (the add at 99.00 or 101.00, and the U of cases 1 and 4); 2-3: case
# 2's U. Closes: case 1's at 0 and case 4's at 0, case 3's at 1.
WINDOW_LINES = ("messages 14", "refused 0", "walk clean", "adds 13", "add_searches 13",
                "better add 0 6", "better add 1 6", "better add 2-3 1", "better add 4-7 0",
                "close_searches 3", "better close 0 2", "better close 1 1",
                "better close 2-3 0")


def window_failures(window, scratch):
    path = scratch / "window.itch"
    path.write_bytes(bytes(window_stream()))
    done = subprocess.run([window, str(path)], capture_output=True, text=True, check=False)
    lines = done.stdout.splitlines()
    failures = [] if done.returncode == 0 else [f"window: exit {done.returncode}"]
    failures += [f"window: no line {line!r}" for line in WINDOW_LINES if line not in lines]
    return failures


def main():
    alloc, as_tables, window, oracle_fixtures = sys.argv[1:5]
    session = Path(oracle_fixtures) / "session.itch"
    failures = alloc_failures(alloc, session) + as_tables_failures(as_tables)
    with tempfile.TemporaryDirectory() as scratch:
        failures += window_failures(window, Path(scratch))
    for failure in failures:
        print(f"tools cli: FAIL {failure}")
    if failures:
        return 1
    print("tools cli: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
