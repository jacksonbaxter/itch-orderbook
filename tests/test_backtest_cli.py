#!/usr/bin/env python3
"""Black-box test of itch_backtest on the committed oracle fixture.

Usage: test_backtest_cli.py <itch_backtest> <oracle fixtures dir>

session.itch is a seeded random session on symbols S1 to S5. The trivial strategy trades
on the named ones, so every check the tool prints must pass, with fills, no allocation
after construction and the replayed book equal to a plain one. The same run twice gives the
same event hash, and other symbols give another. Bad usage, an unreadable file and a day
with no fee schedule exit 2; a run that never trades exits 1. Faults that the tool's checks
catch are in scripts/faults/backtester.json. A failure prints
`backtest cli: FAIL <case>: <reason>`.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

CHECKS = ("allocations", "no refused message", "no refused delivery", "no ring overflow",
          "no fee error", "simulated orders traded", "replay identical to a plain book")


def main():
    tool, fixtures = sys.argv[1:3]
    session = str(Path(fixtures) / "session.itch")
    failures = []

    def run(*args):
        return subprocess.run([tool, *args], capture_output=True, text=True, check=False)

    def field(output, name):
        match = re.search(rf"^{name} (\S+)", output, re.MULTILINE)
        return match.group(1) if match else None

    base = (session, "--date", "20191230", "--small")
    first = run(*base, "--symbol", "S1", "--symbol", "S2")
    if first.returncode != 0:
        failures.append(("traded run", f"exit {first.returncode}: {first.stdout}{first.stderr}"))
    for check in CHECKS:
        if f"check {check}: pass" not in first.stdout:
            failures.append(("traded run", f"no passing line for {check}"))
    fills = re.search(r"^fills (\d+) ", first.stdout, re.MULTILINE)
    if fills is None or int(fills.group(1)) < 20:
        failures.append(("traded run", f"too few fills: {fills and fills.group(1)}"))
    if field(first.stdout, "allocations_after_construction") != "0":
        failures.append(("traded run", "allocations after construction"))

    again = run(*base, "--symbol", "S1", "--symbol", "S2")
    if field(again.stdout, "event_hash") != field(first.stdout, "event_hash"):
        failures.append(("repeat", "the same run gave another event hash"))
    other = run(*base, "--symbol", "S3")
    if other.returncode != 0 or field(other.stdout, "event_hash") == field(first.stdout,
                                                                           "event_hash"):
        failures.append(("other symbol", "same hash or a failed run"))

    unchecked = run(*base, "--symbol", "S1", "--no-plain-check")
    if unchecked.returncode != 0 or "replay identical" in unchecked.stdout:
        failures.append(("no plain check", "the replay check ran or the run failed"))

    idle = run(*base, "--symbol", "NOPE")
    if idle.returncode != 1 or "check simulated orders traded: FAIL" not in idle.stdout:
        failures.append(("no trades", f"exit {idle.returncode}"))

    for name, args in (("no file", ("--date", "20191230")),
                       ("no date", (session,)),
                       ("short date", (session, "--date", "2019123")),
                       ("long symbol", (*base, "--symbol", "ABCDEFGHI")),
                       ("unknown flag", (*base, "--fast")),
                       ("missing file", (str(Path(fixtures) / "absent.itch"), "--date",
                                         "20191230")),
                       ("no fee schedule", (session, "--date", "20200102", "--small"))):
        done = run(*args)
        if done.returncode != 2:
            failures.append((name, f"exit {done.returncode}, not 2"))

    # The day after a frame of a type the spec does not define, cut inside its last frame: the
    # replay would end it like a whole day, so the framing check refuses it before the run.
    with tempfile.TemporaryDirectory() as scratch:
        cut = Path(scratch) / "cut.itch"
        cut.write_bytes(b"\x00\x03z\x00\x00" + Path(session).read_bytes()[:-1])
        done = run(str(cut), *base[1:])
        if done.returncode != 1 or done.stdout or \
                "FAIL framing: truncated_payload at offset" not in done.stderr or \
                "skipped frames 1 (unknown_type 1, bad_length 0)" not in done.stderr:
            failures.append(("cut", f"exit {done.returncode}: {done.stdout}{done.stderr}"))

    for case, reason in failures:
        print(f"backtest cli: FAIL {case}: {reason}")
    if failures:
        return 1
    print(f"backtest cli: all cases passed, {fills.group(1)} fills")
    return 0


if __name__ == "__main__":
    sys.exit(main())
