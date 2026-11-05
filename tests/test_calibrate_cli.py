#!/usr/bin/env python3
"""Black-box test of itch_calibrate on a day built here, every expected figure worked by hand.

Usage: test_calibrate_cli.py <itch_calibrate> <scratch dir>

Three symbols trade for eight seconds after the open, Δ = S = 1 s, τ = one cent (100):
  - AAA, a one-tick spread, bid 100.00, ask 100.01, mid2 2,000,100: only half-tick depths are
    open. Bid side, interval 0 reaches 2.5 ticks, 1 reaches 1.5 (at exactly the end of it),
    2 and 3 reach 0.5; the ask side the same in 4 to 7. Hits 8, 4, 2 of exposure 8 at 0.5,
    1.5 and 2.5, so λ = hits/16 = 0.5, 0.25, 0.125: an exact line, k = ln 2 = 0.693147 and
    A = 0.5·2^0.5 = 0.707107. In interval 3 the best ask leaves and comes back, so the 0.5-tick
    execution between is 1 tick from the mid of the moment: the depth must use the start mid.
    That move is AAA's only change of mid: two jumps of half a tick in 8 valid seconds, so
    σ_jump = sqrt(0.5/8) = 0.25, and no clock sample sees it, σ_clock = 0 over 7 changes.
    Decoys: an execution before the open, a non-printable C at 2.5 ticks, a P trade far
    below, and executions on ZZZ, which is not asked for.
  - BBB, a two-tick spread, bid 50.00, ask 50.02, mid2 1,000,200: only whole-tick depths.
    Hits 4, 2, 2 of 8 at 1, 2, 3, λ = 0.25, 0.125, 0.125. With a = ln 2: x̄ = 2, ȳ = −8a/3,
    Sxx = 2, Sxy = −a, so k = a/2 = 0.346574, A = e^(−8a/3 + a) = 2^(−5/3) = 0.314980, and
    R² = (a²/2)/(2a²/3) = 0.75.
  - CCC, no executions, a moving mid and a halt from 4.5 s to 5.5 s. Clock samples at 0 to 7 s,
    in mid2 above 400,000: 100, 100, 200, 300, 200, halted, 100, 0. Five changes between valid
    samples, 0, ±0.5 ticks, Δ̄ = 0, so σ_clock = sqrt(1.0/5) = 0.447214. Jumps: +0.5 at 1.5 s,
    +0.5 at 2.5 s, none at 3.2 s, where a delete and an add in one nanosecond cancel, −0.5 at
    3.6 s, none across the halt, −0.5 at 6.5 s: four, Σ J² = 1.0 in 4.5 + 2.5 = 7.0 valid
    seconds, σ_jump = sqrt(1/7) = 0.377964. Seven intervals with a valid start, no fit.
A failure prints `calibrate cli: FAIL <case>: <reason>`.
"""

import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gen_oracle_fixtures import OPEN_NS, Stream  # noqa: E402

MS = 1_000_000
AAA, BBB, CCC, ZZZ = 1, 2, 3, 4
NAMES = {AAA: "AAA", BBB: "BBB", CCC: "CCC", ZZZ: "ZZZ"}


def at(stream, ms):
    """The next message at exactly OPEN_NS + ms."""
    stream.at(OPEN_NS + (ms * MS) - 1_000)


def add(stream, locate, ref, side, price):
    stream.add(locate, ref, side, 1_000, price, NAMES[locate])


def day():
    s = Stream()
    s.system("O")
    for locate, name in NAMES.items():
        s.directory(locate, name)
        s.trading(locate, name, "T")
    s.system("S")
    for ref, side, price in ((1, "B", 1_000_000), (2, "B", 999_900), (3, "B", 999_800),
                             (4, "S", 1_000_100), (5, "S", 1_000_200), (6, "S", 1_000_300)):
        add(s, AAA, ref, side, price)
    for ref, side, price in ((11, "B", 500_000), (12, "B", 499_900), (13, "B", 499_800),
                             (14, "S", 500_200), (15, "S", 500_300), (16, "S", 500_400)):
        add(s, BBB, ref, side, price)
    for ref, side, price in ((21, "B", 200_000), (24, "B", 199_900), (22, "S", 200_100),
                             (23, "S", 200_200)):
        add(s, CCC, ref, side, price)
    add(s, ZZZ, 31, "B", 100_000)
    add(s, ZZZ, 32, "S", 100_100)
    s.execute(AAA, 3, 10)  # Before the open.

    at(s, 0)
    s.system("Q")
    events = (
        (300, lambda: s.execute(AAA, 1, 10)),
        (301, lambda: s.execute(AAA, 2, 10)),
        (302, lambda: s.execute(AAA, 3, 10)),  # Interval 0, bid: 2.5 ticks.
        (400, lambda: s.execute(BBB, 13, 10)),  # Interval 0, bid: 3 ticks.
        (500, lambda: s.execute(ZZZ, 31, 10)),
        (1_400, lambda: s.execute(BBB, 16, 10)),  # Interval 1, ask: 3 ticks.
        (1_500, lambda: s.delete(CCC, 22)),
        (2_000, lambda: s.execute(AAA, 2, 10)),  # The end of interval 1, bid: 1.5 ticks.
        (2_200, lambda: s.execute(AAA, 1, 10)),  # Interval 2, bid: 0.5.
        (2_300, lambda: s.message("C", AAA, struct.pack(">QIQcI", 3, 10, 9, b"N", 999_800))),
        (2_400, lambda: s.execute(BBB, 11, 10)),  # Interval 2, bid: 1 tick.
        (2_500, lambda: add(s, CCC, 25, "B", 200_100)),
        (3_100, lambda: s.delete(AAA, 4)),
        (3_150, lambda: s.execute(AAA, 1, 10)),  # Interval 3, bid: 0.5 from the start mid.
        (3_200, lambda: add(s, AAA, 7, "S", 1_000_100)),
        # A delete and an add in one nanosecond: CCC's mid leaves and comes back, no jump.
        (3_200, lambda: s.delete(CCC, 25)),
        (3_200, lambda: add(s, CCC, 26, "B", 200_100)),
        (3_400, lambda: s.execute(BBB, 14, 10)),  # Interval 3, ask: 1 tick.
        (3_600, lambda: s.delete(CCC, 26)),
        (4_300, lambda: s.execute(AAA, 7, 10)),
        (4_301, lambda: s.execute(AAA, 5, 10)),
        (4_302, lambda: s.execute(AAA, 6, 10)),  # Interval 4, ask: 2.5.
        (4_500, lambda: s.trading(CCC, "CCC", "H")),
        (5_000, lambda: s.delete(CCC, 21)),
        (5_500, lambda: s.execute(AAA, 5, 10)),  # Interval 5, ask: 1.5.
        (5_500, lambda: s.trading(CCC, "CCC", "T")),
        (6_500, lambda: s.execute(AAA, 7, 10)),  # Interval 6, ask: 0.5.
        (6_500, lambda: add(s, CCC, 27, "S", 200_100)),
        (6_600, lambda: s.trade(AAA, 100, 990_000, "AAA")),
        (7_500, lambda: s.execute(AAA, 7, 10)),  # Interval 7, ask: 0.5.
    )
    for ms, event in events:
        at(s, ms)
        event()
    at(s, 8_000)
    s.system("M")
    s.system("E")
    s.system("C")
    return s.data


EXPECTED = {
    "AAA": [
        "valid_seconds 8.000000",
        "sigma_clock_changes 7",
        "sigma_clock 0.000000",
        "sigma_jump_count 2",
        "sigma_jump 0.250000",
        "intervals 8",
        "intervals_off_grid 0",
        "executions 12",
        "depth 0.5 exposure 8 hits 8 lambda 0.500000",
        "depth 1.5 exposure 8 hits 4 lambda 0.250000",
        "depth 2.5 exposure 8 hits 2 lambda 0.125000",
        "fit points 3 k 0.693147 A 0.707107 r_squared 1.000000",
    ],
    "BBB": [
        "valid_seconds 8.000000",
        "sigma_clock 0.000000",
        "sigma_jump_count 0",
        "sigma_jump 0.000000",
        "intervals 8",
        "executions 4",
        "depth 1.0 exposure 8 hits 4 lambda 0.250000",
        "depth 2.0 exposure 8 hits 2 lambda 0.125000",
        "depth 3.0 exposure 8 hits 2 lambda 0.125000",
        "fit points 3 k 0.346574 A 0.314980 r_squared 0.750000",
    ],
    "CCC": [
        "valid_seconds 7.000000",
        "sigma_clock_changes 5",
        "sigma_clock 0.447214",
        "sigma_jump_count 4",
        "sigma_jump 0.377964",
        "intervals 7",
        "executions 0",
        "fit none, fewer than two depths with a hit",
    ],
}


def sections(stdout):
    """{symbol: [lines]} from the tool's output."""
    out = {}
    current = None
    for line in stdout.splitlines():
        if line.startswith("symbol "):
            current = out.setdefault(line.split()[1], [])
        elif current is not None:
            current.append(line)
    return out


def main():
    tool, scratch = sys.argv[1:3]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    failures = []
    path = scratch / "day.itch"
    path.write_bytes(bytes(day()))
    good = str(path)

    def run(*args):
        return subprocess.run([tool, *args], capture_output=True, text=True, check=False)

    timing = ["--interval-ms", "1000", "--sample-ms", "1000"]
    done = run(good, "--symbols", "AAA,BBB,CCC", *timing)
    if done.returncode != 0:
        failures.append(("day", f"exit {done.returncode}: {done.stdout}{done.stderr}"))
    found = sections(done.stdout)
    for symbol, wanted in EXPECTED.items():
        have = found.get(symbol, [])
        for line in wanted:
            if line not in have:
                failures.append((symbol, f"no line {line!r} in {have}"))
    depths = [line for line in found.get("AAA", []) if line.startswith("depth ")]
    if len(depths) != 3:
        failures.append(("AAA", f"whole-tick depths are not open on a one-tick spread: {depths}"))

    # On a 1.5 s clock CCC's samples are 100, 200, 300, halted (4.5 s, the halt's own
    # nanosecond), 100, 0: changes of 0.5, 0.5 and -0.5 ticks, Δ̄ = 1/6, (1/3)·Σ(Δ - Δ̄)² = 2/9,
    # σ_clock = sqrt((2/9)/1.5) = 0.384900. Without the mean taken out it would be 0.408248.
    slow = sections(run(good, "--symbols", "CCC", "--interval-ms", "1000",
                        "--sample-ms", "1500").stdout)
    for line in ("sigma_clock_changes 3", "sigma_clock 0.384900"):
        if line not in slow.get("CCC", []):
            failures.append(("CCC at 1.5 s", f"no line {line!r}"))

    capped = sections(run(good, "--symbols", "AAA", *timing, "--max-depth-ticks", "1").stdout)
    for line in ("depth 0.5 exposure 8 hits 8 lambda 0.500000",
                 "fit none, fewer than two depths with a hit"):
        if line not in capped.get("AAA", []):
            failures.append(("cap at 1 tick", f"no line {line!r}"))
    two = sections(run(good, "--symbols", "AAA", *timing, "--max-depth-ticks", "2").stdout)
    # At a cap of 2 ticks the 2.5-tick sides count at 1.5: hits 8 and 4, the same line.
    wanted = "fit points 2 k 0.693147 A 0.707107 r_squared 1.000000 exact, two points test nothing"
    if wanted not in two.get("AAA", []):
        failures.append(("cap at 2 ticks", f"no line {wanted!r}: {two.get('AAA')}"))

    absent = run(good, "--symbols", "AAA,QQQ", *timing)
    if absent.returncode != 1 or "symbol QQQ not in the directory" not in absent.stdout:
        failures.append(("absent symbol", f"exit {absent.returncode}, not 1: {absent.stdout}"))

    for name, args in (("no file", ("--symbols", "AAA")),
                       ("no symbols", (good,)),
                       ("empty symbol", (good, "--symbols", "AAA,,BBB")),
                       ("long symbol", (good, "--symbols", "ABCDEFGHI")),
                       ("zero interval", (good, "--symbols", "AAA", "--interval-ms", "0")),
                       ("unknown flag", (good, "--symbols", "AAA", "--fast")),
                       ("missing file", (str(scratch / "absent.itch"), "--symbols", "AAA"))):
        done = run(*args)
        if done.returncode != 2:
            failures.append((name, f"exit {done.returncode}, not 2"))

    for case, reason in failures:
        print(f"calibrate cli: FAIL {case}: {reason}")
    if failures:
        return 1
    print("calibrate cli: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
