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


TARGETS = {
    "fuzz_reader": reader_seeds,
    "fuzz_decode": decode_seeds,
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
