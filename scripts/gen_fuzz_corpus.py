#!/usr/bin/env python3
"""Writes the committed seed corpus for each fuzz target under fuzz/corpus/<target>/.

The seeds are built only from the hand-written bytes in tests/support/fixtures.hpp,
which this script parses so that there is one copy of them. Nothing here reads a
Nasdaq file.

    gen_fuzz_corpus.py          rewrite the corpus
    gen_fuzz_corpus.py --check  fail if the committed corpus differs

Adding a target: write a function returning {seed name: bytes}, or reuse one, and add
it to TARGETS. A file named regress-* is a saved crash input: it is left alone here,
and replayed like any other seed.
"""

from __future__ import annotations

import argparse
import random
import re
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "support" / "fixtures.hpp"
CORPUS = REPO_ROOT / "fuzz" / "corpus"

FIXTURE_ARRAY = re.compile(
    r"inline constexpr std::array<std::uint8_t, (\d+)> (k\w+)\{(.*?)\n\};", re.DOTALL
)
HEX_BYTE = re.compile(r"0x([0-9A-Fa-f]{2})\b")
LINE_COMMENT = re.compile(r"//.*")


def parse_fixtures(text):
    """Returns {name: bytes} for each array, checking its declared size."""
    fixtures = {}
    for size, name, body in FIXTURE_ARRAY.findall(text):
        data = bytes(int(h, 16) for h in HEX_BYTE.findall(LINE_COMMENT.sub("", body)))
        if len(data) != int(size):
            raise ValueError(f"{name}: declared {size} bytes, parsed {len(data)}")
        fixtures[name] = data
    if not fixtures:
        raise ValueError("no fixture arrays found")
    return fixtures


def messages_in_order(fixtures):
    """The 23 message fixtures, without the closing System Event."""
    return [data for name, data in fixtures.items() if name != "kEndOfMessages"]


def frame(payload):
    return struct.pack(">H", len(payload)) + payload


def seed_name(message):
    # Hex first: H and h would collide on a case-insensitive file system.
    return f"{message[0]:02x}-{chr(message[0])}"


def reader_seeds(fixtures):
    messages = messages_in_order(fixtures)
    end = frame(fixtures["kEndOfMessages"])
    stream = b"".join(frame(m) for m in messages) + end
    system = frame(fixtures["kSystemEvent"])
    add = frame(fixtures["kAddOrder"])
    seeds = {f"frame-{seed_name(m)}": frame(m) for m in messages}
    seeds.update(
        {
            "stream-all-types": stream,
            "stream-unknown-type": system + frame(b"z\x01\x02\x03") + add + end,
            "stream-zero-length": system + b"\x00\x00" + add + b"\x00\x00",
            "stream-bad-length-short": frame(fixtures["kAddOrder"][:-1]) + add + end,
            "stream-bad-length-long": frame(fixtures["kAddOrder"] + b"\x00") + add + end,
            "stream-oversized-length": system + b"\xff\xff" + fixtures["kAddOrder"],
            "stream-truncated-prefix": stream[:-len(end)] + b"\x00",
            "stream-truncated-payload": stream[:-1],
            "stream-empty": b"",
        }
    )
    return seeds


def decode_seeds(fixtures):
    messages = messages_in_order(fixtures)
    seeds = {f"msg-{seed_name(m)}": m for m in messages}
    seeds.update(
        {
            "msg-short": fixtures["kOrderDelete"][:-1],
            "msg-long": fixtures["kOrderDelete"] + b"\x00",
            "msg-unknown-type": b"z" + fixtures["kOrderDelete"][1:],
            "msg-empty": b"",
            "msg-all-ones": b"\xff" * 64,
        }
    )
    return seeds


def book_record(code, pick=0, shares=10, price=3, locate=1):
    return bytes((code, pick, shares, price, locate))


def book_seeds(fixtures):
    """Records for fuzz_book: operation, pick, shares, price, locate."""
    add_bid = book_record(0, pick=1)
    add_ask = book_record(0, pick=0, price=9)
    names = ["add", "add-attributed", "execute", "execute-2", "execute-with-price", "cancel",
             "delete", "replace", "replace-same-price", "other-message", "dead-reference",
             "over-deduction", "wrong-locate", "duplicate-add", "replace-onto-live", "bad-side"]
    seeds = {f"op-{code:02d}-{name}": add_bid + add_ask + book_record(code)
             for code, name in enumerate(names)}
    # Twelve records is few enough to stay short of the bugs only the fuzzer should find.
    rng = random.Random(20191230)
    seeds["mixed-short"] = b"".join(
        book_record(rng.randrange(16), rng.randrange(4), rng.randrange(256), rng.randrange(4),
                    rng.randrange(4))
        for _ in range(12))
    seeds["crossing"] = (book_record(0, pick=1, price=9) + book_record(0, pick=0, price=3)
                         + book_record(6) + book_record(6))
    seeds["extreme-prices"] = book_record(0, pick=1, price=255) + book_record(0, pick=0, price=254)
    seeds["empty"] = b""
    seeds["partial-record"] = b"\x00\x01"
    return seeds


CROSS_HEADER = ("Date      ,Symbol,ListingMarket,OpeningCross,ClosingCross,IntradayCross,\r\n"
                "----------,------,-------------,------------,------------,-------------,\r\n")
CROSS_ROWS = ("12/30/2019,AAA   ,Q            ,          13,           0,            0,\r\n"
              "12/30/2019,BB.A  ,N            ,        4207,       11772,            5,\r\n")
CROSS_TOTAL = "12/30/2019,TOTAL ,NULL         ,        4220,       11772,            5,\r\n"


def cross_stats_seeds(_fixtures):
    """Invented CrossStats text: a good file, and one seed for each way a file can fail."""
    good = CROSS_HEADER + CROSS_ROWS + CROSS_TOTAL
    seeds = {
        "good": good,
        "good-lf": good.replace("\r\n", "\n"),
        "good-no-final-newline": good[:-2],
        "good-wrong-total": CROSS_HEADER + CROSS_ROWS + CROSS_TOTAL.replace("4220", "4221"),
        "good-only-total": CROSS_HEADER + CROSS_TOTAL,
        "empty": "",
        "bad-header": "Date,Symbol\r\n",
        "bad-rule": CROSS_HEADER.replace("-------------,\r\n", "x,\r\n"),
        "no-total": CROSS_HEADER + CROSS_ROWS,
        "short-row": CROSS_HEADER + "12/30/2019,AAA\r\n" + CROSS_TOTAL,
        "bad-date": CROSS_HEADER + "13/32/2019,AAA,Q,1,2,0,\r\n" + CROSS_TOTAL,
        "mixed-dates": CROSS_HEADER + CROSS_ROWS + CROSS_TOTAL.replace("12/30", "12/31"),
        "bad-symbol": CROSS_HEADER + "12/30/2019,TOOLONGSYM,Q,1,2,0,\r\n" + CROSS_TOTAL,
        "bad-number": CROSS_HEADER + "12/30/2019,AAA,Q,-1,2,0,\r\n" + CROSS_TOTAL,
        "huge-number": CROSS_HEADER + "12/30/2019,AAA,Q,18446744073709551616,2,0,\r\n"
                       + CROSS_TOTAL,
        "duplicate-symbol": CROSS_HEADER + CROSS_ROWS + CROSS_ROWS + CROSS_TOTAL,
        "rows-after-total": CROSS_HEADER + CROSS_TOTAL + CROSS_ROWS,
    }
    return {name: text.encode("ascii") for name, text in seeds.items()}


MIDAS_HEADER = ("Date,Security,Ticker,Exchange,McapRank,Cancels,Trades,LitTrades,OddLots,Hidden,"
                "OrderVol('000),TradeVol('000),LitVol('000),OddLotVol('000),HiddenVol('000)\n")
MIDAS_ROW = "20191230,Stock,AAA,Nasdaq,10.0,5.0,12.0,9.0,4.0,3.0,20.5,1.143,0.893,0.14300000000000002,0.25\n"
MIDAS_ETF = "20191230,ETF,BB.A,Nasdaq,,5.0,1.0,1.0,1.0,0.0,1.0,19.977999999999998,0.001,0.001,0.0\n"
MIDAS_OTHER = "20191230,Stock,AAA,Arca,10.0,5.0,-1.0,9.0,4.0,3.0,,-0.1,,0.1,\n"


def midas_seeds(_fixtures):
    """Invented MIDAS text: a good file, and one seed for each way a file can fail."""
    good = MIDAS_HEADER + MIDAS_ROW + MIDAS_ETF + MIDAS_OTHER
    seeds = {
        "good": good,
        "good-no-final-newline": good[:-1],
        "good-crlf": good.replace("\n", "\r\n"),
        "good-two-rows-for-a-symbol": MIDAS_HEADER + MIDAS_ROW + MIDAS_ROW,
        "good-other-day-only-beside": MIDAS_HEADER + MIDAS_ROW.replace("20191230", "20191231")
                                      + MIDAS_ROW,
        "empty": "",
        "header-only": MIDAS_HEADER,
        "missing-column": "Date,Ticker\n" + MIDAS_ROW,
        "duplicate-column": "Date," + MIDAS_HEADER + MIDAS_ROW,
        "short-row": MIDAS_HEADER + "20191230,Stock\n",
        "bad-date": MIDAS_HEADER + MIDAS_ROW.replace("20191230", "2019123x"),
        "bad-security": MIDAS_HEADER + MIDAS_ROW.replace("Stock", "Bond"),
        "bad-ticker": MIDAS_HEADER + MIDAS_ROW.replace("AAA", "TOOLONGSYM"),
        "bad-count": MIDAS_HEADER + MIDAS_ROW.replace("12.0", "12.5"),
        "bad-volume": MIDAS_HEADER + MIDAS_ROW.replace("1.143", "-1.143"),
        "part-share": MIDAS_HEADER + MIDAS_ROW.replace("1.143", "1.1435"),
        "huge-volume": MIDAS_HEADER + MIDAS_ROW.replace("1.143", "99999999999999999999.0"),
    }
    return {name: text.encode("ascii") for name, text in seeds.items()}


TARGETS = {
    "fuzz_reader": reader_seeds,
    "fuzz_decode": decode_seeds,
    "fuzz_book": book_seeds,
    "fuzz_crossstats": cross_stats_seeds,
    "fuzz_midas": midas_seeds,
}


def expected_corpus(fixture_text):
    fixtures = parse_fixtures(fixture_text)
    return {target: build(fixtures) for target, build in TARGETS.items()}


def is_regression_input(path):
    return path.name.startswith("regress-")


def differences(corpus_dir, expected):
    problems = []
    for target, seeds in expected.items():
        directory = corpus_dir / target
        found = {}
        if directory.is_dir():
            found = {p.name: p.read_bytes() for p in directory.iterdir()
                     if p.is_file() and not is_regression_input(p)}
        for name in sorted(seeds.keys() - found.keys()):
            problems.append(f"{target}/{name}: missing")
        for name in sorted(found.keys() - seeds.keys()):
            problems.append(f"{target}/{name}: not produced by the generator")
        for name in sorted(seeds.keys() & found.keys()):
            if seeds[name] != found[name]:
                problems.append(f"{target}/{name}: bytes differ")
    return problems


def write_corpus(corpus_dir, expected):
    for target, seeds in expected.items():
        directory = corpus_dir / target
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.iterdir():
            if path.is_file() and not is_regression_input(path) and path.name not in seeds:
                path.unlink()
        for name, data in seeds.items():
            (directory / name).write_bytes(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare, do not write")
    args = parser.parse_args(argv)

    expected = expected_corpus(FIXTURES.read_text(encoding="utf-8"))
    total = sum(len(seeds) for seeds in expected.values())
    if args.check:
        problems = differences(CORPUS, expected)
        for problem in problems:
            print(f"corpus: {problem}", file=sys.stderr)
        if problems:
            print("corpus: stale. Run scripts/gen_fuzz_corpus.py", file=sys.stderr)
            return 1
        print(f"corpus: {total} seeds match the fixtures")
        return 0
    write_corpus(CORPUS, expected)
    print(f"corpus: wrote {total} seeds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
