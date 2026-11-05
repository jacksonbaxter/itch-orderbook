#!/usr/bin/env python3
"""Black-box test of itch_select on a day built here, every expected figure worked by hand.

Usage: test_select_cli.py <itch_select> <scratch dir>

The window is 09:30:10 to 09:30:20, 10 s. Every symbol's book is bid $10.00 against an ask
above it unless named otherwise, each order 100,000 shares, built before the open, and every
spread change is one message at a whole second. Executions come after the open, against the
bid, and count only in regular hours, `E` and printable `C` alike.

  - Universe, 10 of 19. Out: ANYS (category N), AADR (issue A), AETF (ETP), ATST (test
    issue), AHLT (halted 15 s to 16 s) and AHL2 (halted at the open), ACHP (close $3.005) and
    AXPN ($600.005), ANOB (no ask until 25 s, so no valid time in the window, though its close
    is valid).
  - Group A, average spread ≤ $0.015 and one tick ≥ 80% of the valid time:
      ATWO  one tick throughout, 500 shares: the pick.
      AEIG  one tick to 18 s, then two cents: 80% exactly, average 120 = $0.012, 400 shares.
      AONE  one tick throughout, 300 shares: 200 by `E` and 100 by a printable `C`; an `E` of
            1,000 before the open and a non-printable `C` of 500 do not count.
      ALCK  one tick to 11 s, locked to 12 s (valid, spread 0), crossed to 13 s (not valid),
            one tick to 20 s: valid 9 s, one tick 8 s = 88.889%, average 800/9 = $0.008889.
    Not in A: ASEV, one tick to 17 s and two cents after, 70%, though its average, $0.013,
    passes; BLOW, four cents.
  - Group B, average ≥ $0.05 and one tick ≤ 5%:
      BWID  six cents throughout, 200 shares: the pick.
      BONE  one tick to 10.5 s, six cents after: 5% exactly, average 575 = $0.0575, 150 shares.
      BFIV  five cents throughout, exactly the bar, 100 shares.
    Not in B: BSIX, one tick to 10.6 s, 6%; BLOW, four cents, 9,000 shares.
  - With the window cut to 09:30:17, ASEV is one tick throughout and, at 9,000 shares, A's pick.
  - A day with no wide symbol has no B pick, and exits 1.

A failure prints `select cli: FAIL <case>: <reason>`.
"""

import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gen_oracle_fixtures import OPEN_NS, Stream  # noqa: E402

MS = 1_000_000
SIZE = 100_000

# Locate: name, category, issue classification, ETP flag, authenticity.
SYMBOLS = {
    1: ("AONE", "Q", "C", "N", "P"),
    2: ("ATWO", "G", "C", "N", "P"),
    3: ("AETF", "S", "C", "Y", "P"),
    4: ("ANYS", "N", "C", "N", "P"),
    5: ("AADR", "Q", "A", "N", "P"),
    6: ("ATST", "Q", "C", "N", "T"),
    7: ("AHLT", "Q", "C", "N", "P"),
    8: ("AHL2", "Q", "C", "N", "P"),
    9: ("ACHP", "Q", "C", "N", "P"),
    10: ("AXPN", "Q", "C", "N", "P"),
    11: ("ASEV", "Q", "C", "N", "P"),
    12: ("AEIG", "Q", "C", "N", "P"),
    13: ("ALCK", "Q", "C", "N", "P"),
    14: ("BWID", "Q", "C", "N", "P"),
    15: ("BFIV", "Q", "C", "N", "P"),
    16: ("BLOW", "Q", "C", "N", "P"),
    17: ("BONE", "Q", "C", "N", "P"),
    18: ("BSIX", "Q", "C", "N", "P"),
    19: ("ANOB", "Q", "C", "N", "P"),
}
LOCATE = {fields[0]: locate for locate, fields in SYMBOLS.items()}


def at(stream, ms):
    """The next message at exactly OPEN_NS + ms."""
    stream.at(OPEN_NS + (ms * MS) - 1_000)


def directory(stream, locate, name, category, issue, etp, authenticity):
    body = (name.ljust(8).encode() + category.encode() + b"N" + struct.pack(">I", 100) + b"N" +
            issue.encode() + b"Z " + authenticity.encode() + b"N 1" + etp.encode())
    stream.message("R", locate, body + struct.pack(">I", 0) + b"N")


def add(stream, name, ref, side, price):
    stream.add(LOCATE[name], ref, side, SIZE, price, name)


def execute(stream, name, ref, shares):
    stream.execute(LOCATE[name], ref, shares)


def build(symbols):
    """The day for the given locates. Order references are locate·10 + n."""
    s = Stream()
    s.system("O")
    for locate in symbols:
        name, *fields = SYMBOLS[locate]
        directory(s, locate, name, *fields)
        s.trading(locate, name, "H" if name == "AHL2" else "T")
    s.system("S")
    asks = {"ACHP": (30_100, 30_200), "AXPN": (6_000_100, 6_000_200), "BWID": (100_600,),
            "BFIV": (100_500,), "BLOW": (100_400,), "BONE": (100_100, 100_600),
            "BSIX": (100_100, 100_600), "ANOB": ()}
    for locate in symbols:
        name = SYMBOLS[locate][0]
        prices = asks.get(name, (100_100, 100_200))
        bid = prices[0] - 100 if name in ("ACHP", "AXPN") else 100_000
        add(s, name, locate * 10, "B", bid)
        for n, price in enumerate(prices, start=1):
            add(s, name, (locate * 10) + n, "S", price)
    names = {SYMBOLS[locate][0] for locate in symbols}

    def when(ms, name, event):
        if name in names:
            at(s, ms)
            event()

    when(-1_000, "AONE", lambda: execute(s, "AONE", 10, 1_000))  # Before the open.
    at(s, 0)
    s.system("Q")
    when(5_000, "AHL2", lambda: s.trading(LOCATE["AHL2"], "AHL2", "T"))
    when(10_500, "BONE", lambda: s.delete(LOCATE["BONE"], 171))
    when(10_600, "BSIX", lambda: s.delete(LOCATE["BSIX"], 181))
    when(11_000, "ALCK", lambda: add(s, "ALCK", 133, "S", 100_000))  # Locked.
    when(12_000, "ALCK", lambda: s.replace(LOCATE["ALCK"], 133, 134, SIZE, 99_900))  # Crossed.
    when(13_000, "ALCK", lambda: s.delete(LOCATE["ALCK"], 134))
    when(15_000, "AHLT", lambda: s.trading(LOCATE["AHLT"], "AHLT", "H"))
    when(16_000, "AHLT", lambda: s.trading(LOCATE["AHLT"], "AHLT", "T"))
    when(17_000, "ASEV", lambda: s.delete(LOCATE["ASEV"], 111))
    when(18_000, "AEIG", lambda: s.delete(LOCATE["AEIG"], 121))
    when(25_000, "ANOB", lambda: add(s, "ANOB", 191, "S", 100_100))
    executed = {"ATWO": 500, "AEIG": 400, "ALCK": 50, "BWID": 200, "BONE": 150, "BFIV": 100}
    for name in ("AETF", "ANYS", "AADR", "ATST", "AHLT", "AHL2", "ACHP", "AXPN", "ASEV",
                 "BLOW", "BSIX", "ANOB"):
        executed[name] = 9_000
    for ms, (name, shares) in enumerate(executed.items(), start=26_000):
        when(ms, name, lambda name=name, shares=shares: execute(s, name, LOCATE[name] * 10,
                                                                 shares))
    when(27_000, "AONE", lambda: execute(s, "AONE", 10, 200))
    when(27_001, "AONE",
         lambda: s.message("C", 1, struct.pack(">QIQcI", 10, 500, 9, b"N", 100_000)))
    when(27_002, "AONE", lambda: s.execute_price(1, 10, 100, 100_000))
    at(s, 30_000)
    s.system("M")
    s.system("E")
    s.system("C")
    return s.data


EXPECTED = [
    "directory_symbols 19",
    "excluded category 1 issue 1 etp 1 test 1 halted 2 close 2 no_valid_book 1",
    "universe 10",
    "group A members 4",
    "  ATWO locate 2 executed 500 average_spread 0.010000 one_tick_pct 100.000 "
    "valid_seconds 10.000 close 10.0050",
    "  AEIG locate 12 executed 400 average_spread 0.012000 one_tick_pct 80.000 "
    "valid_seconds 10.000 close 10.0100",
    "  AONE locate 1 executed 300 average_spread 0.010000 one_tick_pct 100.000 "
    "valid_seconds 10.000 close 10.0050",
    "  ALCK locate 13 executed 50 average_spread 0.008889 one_tick_pct 88.889 "
    "valid_seconds 9.000 close 10.0050",
    "group A ATWO",
    "group B members 3",
    "  BWID locate 14 executed 200 average_spread 0.060000 one_tick_pct 0.000 "
    "valid_seconds 10.000 close 10.0300",
    "  BONE locate 17 executed 150 average_spread 0.057500 one_tick_pct 5.000 "
    "valid_seconds 10.000 close 10.0300",
    "  BFIV locate 15 executed 100 average_spread 0.050000 one_tick_pct 0.000 "
    "valid_seconds 10.000 close 10.0250",
    "group B BWID",
]


def main():
    tool, scratch = sys.argv[1:3]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    failures = []
    path = scratch / "day.itch"
    path.write_bytes(bytes(build(sorted(SYMBOLS))))
    good = str(path)

    def run(*args):
        return subprocess.run([tool, *args], capture_output=True, text=True, check=False)

    window = ["--from", "09:30:10", "--to", "09:30:20"]
    done = run(good, *window)
    if done.returncode != 0:
        failures.append(("day", f"exit {done.returncode}: {done.stdout}{done.stderr}"))
    lines = done.stdout.splitlines()
    body = lines[1:]
    if body != EXPECTED:
        for index in range(max(len(body), len(EXPECTED))):
            have = body[index] if index < len(body) else "<no line>"
            want = EXPECTED[index] if index < len(EXPECTED) else "<no line>"
            if have != want:
                failures.append(("day", f"line {index + 2}: have {have!r}, want {want!r}"))
                break

    cut = run(good, "--from", "09:30:10", "--to", "09:30:17")
    if "group A ASEV" not in cut.stdout.splitlines():
        failures.append(("window to 17 s", f"ASEV is not A's pick: {cut.stdout}"))

    narrow = scratch / "narrow.itch"
    narrow.write_bytes(bytes(build([LOCATE["AONE"], LOCATE["ATWO"]])))
    empty = run(str(narrow), *window)
    if empty.returncode != 1 or "group B none" not in empty.stdout.splitlines():
        failures.append(("no wide symbol", f"exit {empty.returncode}, not 1: {empty.stdout}"))

    for name, args in (("no file", tuple(window)),
                       ("bad clock", (good, "--from", "9:30:10")),
                       ("minute 60", (good, "--from", "09:60:00")),
                       ("empty window", (good, "--from", "09:30:20", "--to", "09:30:10")),
                       ("zero top", (good, "--top", "0")),
                       ("unknown flag", (good, "--fast")),
                       ("missing file", (str(scratch / "absent.itch"),))):
        done = run(*args)
        if done.returncode != 2:
            failures.append((name, f"exit {done.returncode}, not 2"))

    for case, reason in failures:
        print(f"select cli: FAIL {case}: {reason}")
    if failures:
        return 1
    print("select cli: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
