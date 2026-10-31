#!/usr/bin/env python3
"""Writes the committed oracle fixtures under tests/fixtures/oracle/.

Every byte here is invented. Nothing reads a Nasdaq file. This is a third statement of
the message layouts, written with struct, separate from the parser and from the
reference book's own decode.

    gen_oracle_fixtures.py          rewrite the fixtures
    gen_oracle_fixtures.py --check  fail if the committed fixtures differ

    lifecycle.itch       every message type once or more, every order path, no crossing,
                         and an empty book at the end
    session.itch         a seeded random session on several symbols, never crossed, that
                         leaves deep orders resting untouched from start to finish
    crossed.itch         crossed and locked observations in each state tests expect
    dead_reference.itch  messages naming dead orders, and other refused messages
"""

from __future__ import annotations

import argparse
import random
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "oracle"
OPEN_NS = 34_200_000_000_000  # 09:30:00
MINUTE_NS = 60_000_000_000


class Stream:
    """Frames messages, with a clock that only moves forward."""

    def __init__(self, start_ns=1_000_000_000):
        self.data = bytearray()
        self.now = start_ns
        self.track = 0

    def at(self, ns):
        self.now = ns

    def message(self, type_code, locate, body):
        self.track = (self.track + 1) & 0xFFFF
        self.now += 1_000
        stamp = struct.pack(">Q", self.now)[2:]
        payload = type_code.encode() + struct.pack(">HH", locate, self.track) + stamp + body
        self.data += struct.pack(">H", len(payload)) + payload

    def system(self, code):
        self.message("S", 0, code.encode())

    def directory(self, locate, symbol):
        body = symbol.ljust(8).encode() + b"QN" + struct.pack(">I", 100) + b"NCZ " + b"PN 1N"
        self.message("R", locate, body + struct.pack(">I", 0) + b"N")

    def trading(self, locate, symbol, state):
        self.message("H", locate, symbol.ljust(8).encode() + state.encode() + b" " + b"    ")

    def add(self, locate, ref, side, shares, price, symbol="SYM"):
        body = struct.pack(">Qc", ref, side.encode()) + struct.pack(">I", shares)
        self.message("A", locate, body + symbol.ljust(8).encode() + struct.pack(">I", price))

    def add_mpid(self, locate, ref, side, shares, price, symbol="SYM"):
        body = struct.pack(">Qc", ref, side.encode()) + struct.pack(">I", shares)
        self.message("F", locate,
                     body + symbol.ljust(8).encode() + struct.pack(">I", price) + b"MPID")

    def execute(self, locate, ref, shares, match=1):
        self.message("E", locate, struct.pack(">QIQ", ref, shares, match))

    def execute_price(self, locate, ref, shares, price, match=2):
        self.message("C", locate, struct.pack(">QIQcI", ref, shares, match, b"Y", price))

    def cancel(self, locate, ref, shares):
        self.message("X", locate, struct.pack(">QI", ref, shares))

    def delete(self, locate, ref):
        self.message("D", locate, struct.pack(">Q", ref))

    def replace(self, locate, old, new, shares, price):
        self.message("U", locate, struct.pack(">QQII", old, new, shares, price))

    def trade(self, locate, shares, price, symbol="SYM", match=3):
        body = struct.pack(">QcI", 0, b"B", shares) + symbol.ljust(8).encode()
        self.message("P", locate, body + struct.pack(">IQ", price, match))

    def cross(self, locate, shares, price, symbol="SYM", match=4):
        body = struct.pack(">Q", shares) + symbol.ljust(8).encode()
        self.message("Q", locate, body + struct.pack(">IQc", price, match, b"O"))


def session_open(stream, symbols):
    stream.system("O")
    for locate, symbol in symbols:
        stream.directory(locate, symbol)
    for locate, symbol in symbols:
        stream.trading(locate, symbol, "T")
    stream.system("S")
    stream.at(OPEN_NS)
    stream.system("Q")


def session_close(stream):
    stream.at(OPEN_NS + 390 * MINUTE_NS)
    stream.system("M")
    stream.system("E")
    stream.system("C")


def lifecycle():
    s = Stream()
    aaa, bbb, zzz = 1, 2, 65535
    session_open(s, [(aaa, "AAA"), (bbb, "BBB"), (zzz, "ZZZ")])
    s.add(aaa, 1, "B", 100, 1_000_000)
    s.add(aaa, 2, "B", 200, 1_000_000)
    s.add_mpid(aaa, 3, "S", 100, 1_001_000)
    s.add(aaa, 4, "S", 300, 1_002_000)
    s.execute(aaa, 1, 30)
    s.execute_price(aaa, 3, 40, 1_000_500)
    s.cancel(aaa, 2, 50)
    s.replace(aaa, 2, 5, 150, 1_000_000)
    s.replace(aaa, 4, 6, 300, 1_001_500)
    s.delete(aaa, 1)
    s.execute(aaa, 5, 150)
    s.trade(aaa, 100, 1_000_000, "AAA")
    s.cross(aaa, 500, 1_000_000, "AAA")
    s.message("B", aaa, struct.pack(">Q", 4))
    s.add(bbb, 7, "B", 500, 500_000, "BBB")
    s.add(bbb, 8, "B", 500, 490_000, "BBB")
    s.add(bbb, 9, "B", 500, 480_000, "BBB")
    s.add(bbb, 10, "S", 500, 510_000, "BBB")
    s.add(zzz, 0xFFFFFFFFFFFFFFFF, "S", 0xFFFFFFFF, 0xFFFFFFFF, "ZZZ")
    s.add(zzz, 0, "B", 1, 1, "ZZZ")
    s.message("Y", bbb, b"BBB     0")
    s.message("L", bbb, b"MPID" + b"BBB     " + b"YNA")
    s.message("I", bbb, struct.pack(">QQ", 100, 50) + b"B" + b"BBB     "
              + struct.pack(">III", 500_000, 500_000, 500_000) + b"OL")
    s.message("N", bbb, b"BBB     A")
    s.message("J", bbb, b"BBB     " + struct.pack(">IIII", 500_000, 510_000, 490_000, 1))
    s.message("K", 0, b"BBB     " + struct.pack(">IcI", 34_200, b"A", 500_000))
    s.message("O", bbb, b"BBB     Y" + struct.pack(">IIIQII", 1, 2, 3, 4, 5, 6))
    s.message("h", bbb, b"BBB     QH")
    s.message("V", 0, struct.pack(">QQQ", 1, 2, 3))
    s.message("W", 0, b"1")
    s.replace(bbb, 8, 11, 400, 505_000)
    s.delete(bbb, 7)
    s.delete(bbb, 9)
    s.delete(bbb, 10)
    s.delete(bbb, 11)
    s.delete(aaa, 3)
    s.delete(aaa, 6)
    s.delete(zzz, 0xFFFFFFFFFFFFFFFF)
    s.delete(zzz, 0)
    session_close(s)
    return bytes(s.data)


def session():
    rng = random.Random(20191230)
    s = Stream()
    symbols = [(locate, f"S{locate}") for locate in (1, 2, 3, 4, 5, 6)]
    session_open(s, symbols)
    next_ref = 1
    live = {}  # ref -> [locate, side, shares, price]

    def new_ref():
        nonlocal next_ref
        next_ref += 1
        return next_ref

    # Deep orders that no later message touches. Every locate has a level on each side
    # that only a whole-book walk ever reads again.
    for locate, symbol in symbols:
        s.add(locate, new_ref(), "B", 700, 500_000, symbol)
        s.add(locate, new_ref(), "S", 700, 1_500_000, symbol)

    def price_for(side):
        tick = rng.randrange(0, 40) * 100
        return 999_900 - tick if side == "B" else 1_000_100 + tick

    def new_order():
        locate, symbol = rng.choice(symbols)
        side = rng.choice("BS")
        ref = new_ref()
        shares = rng.randrange(1, 40) * 100
        price = price_for(side)
        (s.add if rng.random() < 0.9 else s.add_mpid)(locate, ref, side, shares, price, symbol)
        live[ref] = [locate, side, shares, price]

    for _ in range(300):
        new_order()
    for _ in range(3000):
        action = rng.random()
        if action < 0.40 or not live:
            new_order()
            continue
        ref = rng.choice(list(live))
        locate, side, shares, price = live[ref]
        if action < 0.55:
            s.delete(locate, ref)
            del live[ref]
        elif action < 0.70:
            taken = rng.randrange(1, shares + 1)
            if rng.random() < 0.7:
                s.execute(locate, ref, taken)
            else:
                s.execute_price(locate, ref, taken, price)
            live[ref][2] -= taken
            if live[ref][2] == 0:
                del live[ref]
        elif action < 0.80:
            if shares > 1:
                taken = rng.randrange(1, shares)
                s.cancel(locate, ref, taken)
                live[ref][2] -= taken
        elif action < 0.95:
            new = new_ref()
            moved = rng.randrange(1, 40) * 100
            target = price if rng.random() < 0.3 else price_for(side)
            s.replace(locate, ref, new, moved, target)
            del live[ref]
            live[new] = [locate, side, moved, target]
        else:
            s.trade(locate, 100, price, f"S{locate}")
    session_close(s)
    return bytes(s.data)


def crossed():
    """One symbol per situation. test_oracle_cli.py states the expected tallies by hand."""
    halted, paused, trading, unnamed = 1, 2, 3, 4
    s = Stream()
    session_open(s, [(halted, "HLT"), (paused, "PSD"), (trading, "TRD")])
    s.directory(unnamed, "NOH")

    # Crossed while halted, inside regular hours, then through the resumption: crossed
    # while the reopening window is open, and once more after the cross has closed it.
    s.at(OPEN_NS + 30 * MINUTE_NS)
    s.trading(halted, "HLT", "H")
    s.add(halted, 1, "B", 100, 1_010_000, "HLT")
    s.add(halted, 2, "S", 100, 1_000_000, "HLT")  # crossed, state H, depth 10000
    s.add(halted, 3, "S", 100, 1_020_000, "HLT")  # crossed, state H
    s.trading(halted, "HLT", "T")                 # crossed, state T, window open
    s.add(halted, 4, "B", 100, 900_000, "HLT")    # crossed, state T, window open
    s.cross(halted, 100, 1_005_000, "HLT")        # crossed, state T, window closed
    s.delete(halted, 2)                           # uncrossed
    s.delete(halted, 1)
    s.delete(halted, 3)
    s.delete(halted, 4)

    # Locked while paused.
    s.trading(paused, "PSD", "P")
    s.add(paused, 10, "B", 100, 2_000_000, "PSD")
    s.add(paused, 11, "S", 100, 2_000_000, "PSD")  # locked, state P
    s.trading(paused, "PSD", "T")                  # locked, state T, window open
    s.delete(paused, 11)
    s.delete(paused, 10)

    # Crossed in continuous trading: nothing in the feed explains this one.
    s.add(trading, 20, "S", 100, 3_000_000, "TRD")
    s.add(trading, 21, "B", 100, 3_000_100, "TRD")  # crossed, state T, market open
    s.execute(trading, 21, 100)
    s.delete(trading, 20)

    # After the close, on a symbol with no trading action at all.
    s.at(OPEN_NS + 390 * MINUTE_NS)
    s.system("M")
    s.add(unnamed, 30, "B", 100, 4_000_100, "NOH")
    s.add(unnamed, 31, "S", 100, 4_000_000, "NOH")  # crossed, no action yet, market closed
    s.delete(unnamed, 31)
    s.delete(unnamed, 30)
    s.system("E")
    s.system("C")
    return bytes(s.data)


def dead_reference():
    s = Stream()
    session_open(s, [(1, "AAA"), (2, "BBB")])
    s.add(1, 1, "B", 100, 1_000_000)
    s.execute(1, 999, 10)                 # unknown_order
    s.execute_price(1, 999, 10, 1)        # unknown_order
    s.cancel(1, 999, 10)                  # unknown_order
    s.delete(1, 999)                      # unknown_order
    s.replace(1, 999, 1000, 10, 1)        # unknown_order
    s.execute(2, 1, 10)                   # locate_mismatch
    s.execute(1, 1, 101)                  # over_deduction
    s.cancel(1, 1, 101)                   # over_deduction
    s.add(1, 1, "B", 100, 1_000_000)      # duplicate_reference
    s.add(1, 2, "B", 100, 1_000_000)
    s.replace(1, 1, 2, 100, 1_000_000)    # duplicate_reference
    s.add(1, 3, "X", 100, 1_000_000)      # bad_side
    s.delete(1, 1)
    s.delete(1, 1)                        # unknown_order: dead now
    s.delete(1, 2)
    session_close(s)
    return bytes(s.data)


BUILDERS = {
    "lifecycle.itch": lifecycle,
    "session.itch": session,
    "crossed.itch": crossed,
    "dead_reference.itch": dead_reference,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare, do not write")
    args = parser.parse_args(argv)
    problems = []
    for name, build in BUILDERS.items():
        path = FIXTURES / name
        data = build()
        if args.check:
            if not path.is_file():
                problems.append(f"{name}: missing")
            elif path.read_bytes() != data:
                problems.append(f"{name}: bytes differ")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    if args.check:
        found = {path.name for path in FIXTURES.iterdir()} if FIXTURES.is_dir() else set()
        problems += [f"{name}: not produced by the generator"
                     for name in sorted(found - BUILDERS.keys())]
        for problem in problems:
            print(f"oracle fixtures: {problem}", file=sys.stderr)
        if problems:
            print("oracle fixtures: stale. Run scripts/gen_oracle_fixtures.py", file=sys.stderr)
            return 1
        print(f"oracle fixtures: {len(BUILDERS)} files match the generator")
        return 0
    print(f"oracle fixtures: wrote {len(BUILDERS)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
