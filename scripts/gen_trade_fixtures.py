#!/usr/bin/env python3
"""Writes the committed trade-print fixtures under tests/fixtures/trades/.

Every byte here is invented, the reference files included. Nothing reads a Nasdaq file.
The ITCH messages are a separate statement of the layouts, built on the oracle fixture's
Stream. The reference text is typed out by hand, not computed from the stream, so that it
stands for an outside party: tests/test_trades_cli.py works out the same numbers by hand.

    gen_trade_fixtures.py          rewrite the fixtures
    gen_trade_fixtures.py --check  fail if the committed fixtures differ

    day.itch        one session on six symbols that reaches every class of print
    day_cross.txt   the cross file for that session
    day_midas.csv   the MIDAS rows for that session, with other days and venues beside it
    lots.itch       a session of hidden prints on the SEC's round-lot exceptions
    lots_midas.csv  its MIDAS rows, on dates either side of the end of that rule
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gen_oracle_fixtures import Stream  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "trades"
DATE = 20200106

ALFA, BRVO, HLTD, OUTS, ZTST, NONE = 1, 2, 3, 4, 5, 6
SYMBOLS = {ALFA: "ALFA", BRVO: "BRVO", HLTD: "HLTD", OUTS: "OUTS", ZTST: "ZTST", NONE: "NONE"}


def clock(hours, minutes=0, seconds=0, nanos=0):
    return ((hours * 3600 + minutes * 60 + seconds) * 1_000_000_000) + nanos


class TradeStream(Stream):
    """Adds what the prints need: exact stamps, match numbers and a test symbol."""

    def __init__(self):
        super().__init__()
        self.pinned = None

    def at(self, ns):
        self.pinned = None
        self.now = ns

    def pin(self, ns):
        """Stamps every message exactly ns until the next at() or pin()."""
        self.pinned = ns

    def message(self, type_code, locate, body):
        if self.pinned is not None:
            self.now = self.pinned - 1_000
        super().message(type_code, locate, body)

    def listing(self, locate, symbol, authenticity="P", round_lot=100):
        body = (symbol.ljust(8).encode() + b"QN" + struct.pack(">I", round_lot) + b"NCZ "
                + authenticity.encode() + b"N 1N")
        self.message("R", locate, body + struct.pack(">I", 0) + b"N")

    def buy(self, locate, ref, shares, price):
        self.add(locate, ref, "B", shares, price, SYMBOLS[locate])

    def sell(self, locate, ref, shares, price):
        self.add(locate, ref, "S", shares, price, SYMBOLS[locate])

    def printable(self, locate, ref, shares, price, match):
        self.message("C", locate, struct.pack(">QIQcI", ref, shares, match, b"Y", price))

    def non_printable(self, locate, ref, shares, price, match):
        self.message("C", locate, struct.pack(">QIQcI", ref, shares, match, b"N", price))

    def hidden(self, locate, shares, price, match):
        self.trade(locate, shares, price, SYMBOLS[locate], match)

    def bulk(self, locate, shares, price, match, cross_type):
        body = struct.pack(">Q", shares) + SYMBOLS[locate].ljust(8).encode()
        self.message("Q", locate, body + struct.pack(">IQc", price, match, cross_type.encode()))

    def broken(self, locate, match):
        self.message("B", locate, struct.pack(">Q", match))


def premarket(s):
    s.at(clock(7))
    s.buy(ALFA, 1, 200, 1_000_000)
    s.sell(ALFA, 2, 200, 1_001_000)
    s.execute(ALFA, 1, 50, 1001)  # Before the open. Counted, not judged by T-TRADE-5.
    s.at(clock(9, 29))
    s.sell(ALFA, 3, 300, 999_000)


def opening_cross(s):
    s.at(clock(9, 30))
    s.system("Q")
    s.pin(clock(9, 30, 0, 1_000_000))
    s.non_printable(ALFA, 1, 150, 999_500, 1002)  # Both sides short of the cross's 200.
    s.non_printable(ALFA, 3, 150, 999_500, 1003)
    s.bulk(ALFA, 200, 999_500, 1004, "O")
    s.bulk(BRVO, 300, 500_000, 1005, "O")
    s.bulk(ZTST, 40, 100_000, 1006, "O")  # A test symbol, with no row in the cross file.
    s.bulk(HLTD, 0, 0, 1007, "O")  # No cross took place, and the feed says so with a Q.
    s.bulk(OUTS, 120, 50_000, 1008, "O")
    s.bulk(NONE, 0, 0, 1009, "O")


def continuous_alfa(s):
    s.at(clock(9, 31))
    s.delete(ALFA, 3)
    s.buy(ALFA, 4, 300, 1_000_000)
    s.add_mpid(ALFA, 5, "B", 200, 1_000_000, "ALFA")  # Behind 4 in the queue.
    s.at(clock(9, 32))
    s.execute(ALFA, 4, 100, 1010)  # Open and in T, before the MIDAS window.
    s.pin(clock(9, 34, 59, 999_999_999))
    s.execute(ALFA, 4, 20, 1011)  # The last nanosecond outside the window.
    s.pin(clock(9, 35))
    s.hidden(ALFA, 30, 1_000_500, 1012)  # The first nanosecond inside.
    s.at(clock(9, 40))
    s.execute(ALFA, 5, 200, 1013)  # At the best price, not at the front.
    s.at(clock(9, 41))
    s.printable(ALFA, 2, 50, 1_001_200, 1014)  # A price the order does not rest at.
    s.at(clock(9, 42))
    s.printable(ALFA, 2, 50, 1_001_000, 1015)  # The order's own price.
    s.at(clock(9, 43))
    s.cancel(ALFA, 4, 30)
    s.at(clock(9, 44))
    s.replace(ALFA, 4, 6, 400, 1_000_100)  # 150 leave with 4, and 6 opens with 400.
    s.pin(clock(9, 45))
    s.execute(ALFA, 6, 100, 1016)
    s.non_printable(ALFA, 2, 100, 1_000_100, 1016)  # The far leg of that E.
    s.at(clock(9, 46))
    s.sell(ALFA, 7, 100, 1_002_000)
    s.pin(clock(9, 46, 1))
    s.hidden(ALFA, 100, 1_000_600, 1017)
    s.non_printable(ALFA, 7, 100, 1_000_600, 1017)  # The far leg of that P.
    s.at(clock(9, 47))
    s.sell(ALFA, 8, 100, 1_002_000)
    s.buy(ALFA, 9, 100, 1_000_000)
    s.pin(clock(9, 47, 1))
    s.printable(ALFA, 8, 60, 1_002_000, 1018)
    s.non_printable(ALFA, 9, 60, 1_002_000, 1018)  # The far leg of that C.
    s.at(clock(9, 48))
    s.cancel(ALFA, 8, 40)  # A cancel of the last shares: no D follows.
    s.at(clock(9, 49))
    s.delete(ALFA, 9)


def continuous_others(s):
    s.at(clock(10))
    s.buy(BRVO, 20, 1000, 500_000)
    s.execute(BRVO, 20, 400, 1030)
    s.hidden(BRVO, 99, 500_100, 1031)
    s.delete(BRVO, 20)


def halt_and_cross(s):
    s.at(clock(10, 10))
    s.buy(HLTD, 30, 300, 200_000)
    s.buy(HLTD, 31, 100, 190_000)
    s.sell(HLTD, 32, 100, 201_000)
    s.execute(HLTD, 30, 100, 1040)
    s.at(clock(10, 20))
    s.trading(HLTD, "HLTD", "H")
    s.at(clock(10, 21))
    s.execute(HLTD, 31, 100, 1041)  # Halted, and not at the best. Counted by reason.
    s.pin(clock(10, 30))
    s.bulk(HLTD, 150, 200_000, 1042, "H")
    s.pin(clock(10, 30, 0, 5_000_000))
    s.non_printable(HLTD, 30, 150, 200_000, 1043)  # A halt cross's legs follow its print.
    s.pin(clock(10, 30, 0, 6_000_000))
    s.non_printable(HLTD, 32, 100, 200_000, 1044)
    s.at(clock(10, 31))
    s.trading(HLTD, "HLTD", "T")
    s.at(clock(10, 40))
    s.delete(HLTD, 30)
    s.hidden(HLTD, 1000, 200_000, 1045)  # Past a thousand, so MIDAS has a whole part.


def symbols_outside_midas(s):
    s.at(clock(11))
    s.sell(OUTS, 40, 500, 50_000)
    s.execute(OUTS, 40, 200, 1050)
    s.delete(OUTS, 40)
    s.at(clock(11, 10))
    s.buy(ZTST, 50, 100, 100_000)
    s.execute(ZTST, 50, 100, 1060)
    s.at(clock(11, 20))
    s.broken(ZTST, 1060)


def close(s):
    s.at(clock(15, 58))
    s.delete(ALFA, 6)
    s.at(clock(15, 59))
    s.buy(ALFA, 11, 500, 1_000_000)
    s.pin(clock(15, 59, 59, 999_999_999))
    s.hidden(ALFA, 200, 1_000_000, 1070)  # The last nanosecond inside the window.
    s.pin(clock(16))
    s.execute(ALFA, 11, 100, 1071)  # The first nanosecond outside it.
    s.at(clock(16, 0, 0, 100_000))
    s.system("M")
    s.at(clock(16, 0, 0, 200_000))
    s.sell(ALFA, 12, 200, 999_000)
    s.pin(clock(16, 0, 0, 500_000_000))
    s.non_printable(ALFA, 11, 200, 1_000_200, 1072)
    s.non_printable(ALFA, 12, 200, 1_000_200, 1073)
    s.bulk(ALFA, 350, 1_000_200, 1074, "C")
    s.bulk(BRVO, 250, 500_200, 1075, "C")
    s.bulk(HLTD, 60, 200_500, 1076, "C")
    s.bulk(OUTS, 0, 0, 1077, "C")
    s.bulk(ZTST, 0, 0, 1078, "C")
    s.bulk(NONE, 0, 0, 1079, "C")
    s.at(clock(16, 30))
    s.execute(ALFA, 11, 100, 1080)  # After hours.
    s.delete(ALFA, 11)
    s.at(clock(20))
    s.system("E")
    s.at(clock(20, 5))
    s.system("C")


def day():
    s = TradeStream()
    s.at(clock(3))
    s.system("O")
    for locate, symbol in SYMBOLS.items():
        # BRVO's directory says lots of 10 and the SEC's rule says 100, so its P of 99 is odd.
        s.listing(locate, symbol, "T" if locate == ZTST else "P", 10 if locate == BRVO else 100)
    for locate, symbol in SYMBOLS.items():
        s.trading(locate, symbol, "T")
    s.at(clock(4))
    s.system("S")
    premarket(s)
    opening_cross(s)
    continuous_alfa(s)
    continuous_others(s)
    halt_and_cross(s)
    symbols_outside_midas(s)
    close(s)
    return bytes(s.data)


CROSS_FILE = (
    "Date      ,Symbol,ListingMarket,OpeningCross,ClosingCross,IntradayCross,\r\n"
    "----------,------,-------------,------------,------------,-------------,\r\n"
    "01/06/2020,ALFA  ,Q            ,         200,         350,            0,\r\n"
    "01/06/2020,BRVO  ,Q            ,         300,         250,            0,\r\n"
    "01/06/2020,HLTD  ,Q            ,           0,          60,            0,\r\n"
    "01/06/2020,NONE  ,Q            ,           0,           0,            0,\r\n"
    "01/06/2020,OUTS  ,N            ,         120,           0,            0,\r\n"
    "01/06/2020,TOTAL ,NULL         ,         620,         660,            0,\r\n"
)

MIDAS_HEADER = (
    "Date,Security,Ticker,Exchange,McapRank,TurnRank,VolatilityRank,PriceRank,Cancels,Trades,"
    "LitTrades,OddLots,Hidden,TradesForHidden,OrderVol('000),TradeVol('000),LitVol('000),"
    "OddLotVol('000),HiddenVol('000),TradeVolForHidden('000)\n"
)
MIDAS_ROWS = (
    "20200103,Stock,ALFA,Nasdaq,1.0,1.0,1.0,1.0,5.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0\n"
    "20200106,Stock,ALFA,Arca,1.0,1.0,,1.0,5.0,7.0,7.0,7.0,7.0,7.0,7.0,-7.0,7.0,7.0,,7.0\n"
    "20200106,Stock,ALFA,Nasdaq,1.0,1.0,1.0,1.0,5.0,8.0,5.0,4.0,3.0,8.0,4.7,"
    "0.7900000000000001,0.46,0.19,0.33,0.79\n"
    "20200106,ETF,BRVO,Nasdaq,1.0,1.0,1.0,1.0,5.0,2.0,1.0,1.0,1.0,2.0,1.0,0.499,0.4,0.099,"
    "0.09899999999999999,0.499\n"
    "20200106,Stock,HLTD,Nasdaq,1.0,1.0,1.0,1.0,5.0,3.0,2.0,0.0,1.0,3.0,0.5,1.2,0.2,0.0,1.0,1.2\n"
    "20200106,Stock,NONE,Nasdaq,,,,,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0\n"
    "20200107,Stock,ZZZZ,Nasdaq,1.0,1.0,1.0,1.0,5.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0\n"
)


# Directory lot and the sizes printed. ALFA is the plain case. BH and SEB are the SEC's
# exceptions, listed at those lots here as the directory lists them. BRK.A is the third,
# which no held day exercises. BH.A shares a stem with BH and is not on the SEC's list. MKL
# is the case the directory has wrong. SEB and BRK.A print below 10 and are not odd.
LOT_PRINTS = {
    "ALFA": (100, (99, 100, 150)),
    "BH": (10, (9, 10, 99)),
    "BH.A": (10, (50,)),
    "SEB": (1, (1, 5, 9, 99)),
    "BRK.A": (1, (1, 7)),
    "MKL": (10, (9, 10, 99)),
}

# Typed out, not computed: Trades, then OddLots, then TradeVol and OddLotVol in thousands.
LOT_ROWS = (
    ("ALFA", "3.0", "1.0", "0.349", "0.099"),
    ("BH", "3.0", "1.0", "0.118", "0.009"),
    ("BH.A", "1.0", "1.0", "0.05", "0.05"),
    ("SEB", "4.0", "0.0", "0.114", "0.0"),
    ("BRK.A", "2.0", "0.0", "0.008", "0.0"),
    ("MKL", "3.0", "3.0", "0.118", "0.118"),
)


def lots():
    s = TradeStream()
    s.at(clock(3))
    s.system("O")
    for locate, (symbol, (lot, _)) in enumerate(LOT_PRINTS.items(), start=1):
        s.listing(locate, symbol, "P", lot)
    for locate, symbol in enumerate(LOT_PRINTS, start=1):
        s.trading(locate, symbol, "T")
    s.at(clock(4))
    s.system("S")
    s.at(clock(9, 30))
    s.system("Q")
    s.at(clock(10))
    match = 5000
    for locate, (symbol, (_, sizes)) in enumerate(LOT_PRINTS.items(), start=1):
        for size in sizes:
            match += 1
            s.trade(locate, size, 1_000_000, symbol, match)
    s.at(clock(16))
    s.system("M")
    s.system("E")
    s.system("C")
    return bytes(s.data)


def lot_row(date, symbol, trades, odd_lots, volume, odd_volume):
    return (f"{date},Stock,{symbol},Nasdaq,1.0,1.0,1.0,1.0,5.0,{trades},0.0,{odd_lots},{trades},"
            f"{trades},1.0,{volume},0.0,{odd_volume},{volume},{volume}\n")


def lots_midas():
    """Before the rule ends the odd lots are the SEC's. After it they are something else."""
    rows = [lot_row(date, *row) for date in (20200106, 20251031) for row in LOT_ROWS]
    rows += [lot_row(20251103, symbol, trades, "7.0", volume, "0.007")
             for symbol, trades, _, volume, _ in LOT_ROWS]
    return MIDAS_HEADER + "".join(rows)


def builders():
    return {
        "day.itch": day(),
        "day_cross.txt": CROSS_FILE.encode("ascii"),
        "day_midas.csv": (MIDAS_HEADER + MIDAS_ROWS).encode("ascii"),
        "lots.itch": lots(),
        "lots_midas.csv": lots_midas().encode("ascii"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare, do not write")
    args = parser.parse_args(argv)
    wanted = builders()
    problems = []
    if args.check:
        for name, data in wanted.items():
            path = FIXTURES / name
            if not path.is_file():
                problems.append(f"{name}: missing")
            elif path.read_bytes() != data:
                problems.append(f"{name}: bytes differ")
        found = {path.name for path in FIXTURES.iterdir()} if FIXTURES.is_dir() else set()
        problems += [f"{name}: not produced by the generator"
                     for name in sorted(found - wanted.keys())]
        for problem in problems:
            print(f"trade fixtures: {problem}", file=sys.stderr)
        if problems:
            print("trade fixtures: stale. Run scripts/gen_trade_fixtures.py", file=sys.stderr)
            return 1
        print(f"trade fixtures: {len(wanted)} files match the generator")
        return 0
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, data in wanted.items():
        (FIXTURES / name).write_bytes(data)
    print(f"trade fixtures: wrote {len(wanted)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
