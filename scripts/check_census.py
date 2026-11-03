#!/usr/bin/env python3
"""Runs itch_census on a day and requires exact agreement with the data manifest.

The manifest's numbers were counted by scripts/fetch_data.py, in Python, when the day
was pinned. itch_census walks the file with the C++ reader. The two share no code, so
agreement on every count is evidence for both, and any difference is a failure: there
is no tolerance anywhere in this script.

    check_census.py --day 12302019
    check_census.py --day NAME --file F --manifest M --census BIN

Exit codes: 0 every field agrees, 1 at least one field differs or the census failed,
2 the check could not be made (no such day, unpinned day, unreadable census output).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

TYPE_LINE = re.compile(r"type (0x[0-9a-f]{2}|\S) (\d+)")
LAST_LINE = re.compile(
    r"last_message (\S) (\S) locate=(\d+) tracking=(\d+) timestamp_ns=(\d+) offset=(\d+)"
)
BYTE_COUNT_LINE = re.compile(
    r"(market_category|authenticity|test_type|after_e_type) (0x[0-9a-f]{2}|\S) (\d+)"
)
ROUND_LOT_LINE = re.compile(r"round_lot (\d+) (\d+)")
CROSS_LINE = re.compile(
    r"cross (0x[0-9a-f]{2}|\S) count=(\d+) zero_shares=(\d+) zero_price=(\d+) zero_both=(\d+)"
)
NUMERIC_KEYS = frozenset(
    {"bytes", "consumed", "messages", "unknown_type", "bad_length", "zero_length",
     "decode_failures", "directory_repeats", "test_symbols", "test_symbols_with_orders"}
)


class CheckError(Exception):
    """The comparison could not be made at all."""


def parse_census(text):
    """Parses itch_census output. A line it does not recognise is an error."""
    parsed = {"types": {}, "market_category": {}, "authenticity": {}, "test_type": {},
              "after_e_type": {}, "round_lot": {}, "cross": {}}
    for line in text.splitlines():
        match = TYPE_LINE.fullmatch(line)
        if match:
            name = match[1]
            if name in parsed["types"]:
                raise CheckError(f"census printed type {name} twice")
            parsed["types"][name] = int(match[2])
            continue
        match = LAST_LINE.fullmatch(line)
        if match:
            parsed["last_message"] = {
                "type": match[1],
                "event_code": match[2],
                "stock_locate": int(match[3]),
                "tracking_number": int(match[4]),
                "timestamp_ns": int(match[5]),
                "offset": int(match[6]),
            }
            continue
        match = BYTE_COUNT_LINE.fullmatch(line)
        if match:
            parsed[match[1]][match[2]] = int(match[3])
            continue
        match = ROUND_LOT_LINE.fullmatch(line)
        if match:
            parsed["round_lot"][int(match[1])] = int(match[2])
            continue
        match = CROSS_LINE.fullmatch(line)
        if match:
            parsed["cross"][match[1]] = {"count": int(match[2]), "zero_shares": int(match[3]),
                                         "zero_price": int(match[4]), "zero_both": int(match[5])}
            continue
        key, _, value = line.partition(" ")
        if key in NUMERIC_KEYS and value.isdigit():
            parsed[key] = int(value)
        elif key == "end" and value:
            parsed["end"] = value
        else:
            raise CheckError(f"unrecognised census line: {line!r}")
    missing = (NUMERIC_KEYS | {"end"}) - parsed.keys()
    if missing:
        raise CheckError(f"census output has no {sorted(missing)}")
    return parsed


def load_day(manifest_path, day):
    """The manifest entry for a day, refusing one that has no pinned counts."""
    days = json.loads(Path(manifest_path).read_text(encoding="utf-8"))["days"]
    if day not in days:
        raise CheckError(f"day {day} is not in {manifest_path}")
    entry = days[day]
    for field in ("name", "unpacked_bytes", "messages", "per_type_counts", "last_message"):
        if entry.get(field) is None:
            raise CheckError(f"day {day} has no pinned {field} in {manifest_path}")
    return entry


def compare(census, entry):
    """Every (field, expected, actual) that differs."""
    pairs = [
        ("bytes", entry["unpacked_bytes"], census["bytes"]),
        ("consumed", entry["unpacked_bytes"], census["consumed"]),
        ("messages", entry["messages"], census["messages"]),
        ("unknown_type", 0, census["unknown_type"]),
        ("bad_length", 0, census["bad_length"]),
        ("zero_length", 0, census["zero_length"]),
        ("decode_failures", 0, census["decode_failures"]),
        ("end", "end_of_input", census["end"]),
    ]
    # The counts stated by the source, where the manifest keeps them apart from its own.
    stated = entry.get("expected", {})
    if "messages" in stated:
        pairs.append(("expected.messages", stated["messages"], census["messages"]))
    if "unpacked_bytes" in stated:
        pairs.append(("expected.unpacked_bytes", stated["unpacked_bytes"], census["bytes"]))

    expected_types = entry["per_type_counts"]
    for name in sorted(expected_types.keys() | census["types"].keys()):
        pairs.append((f"type {name}", expected_types.get(name), census["types"].get(name)))

    expected_last = entry["last_message"]
    actual_last = census.get("last_message", {})
    for field in sorted(expected_last.keys() | actual_last.keys()):
        pairs.append((f"last_message.{field}", expected_last.get(field), actual_last.get(field)))

    # Each split of what the spec leaves out has to account for every message it splits, or
    # a value it cannot name went missing.
    directories = expected_types.get("R", 0)
    for field in ("market_category", "authenticity", "round_lot"):
        pairs.append((f"{field} total", directories, sum(census[field].values())))
    crosses = sum(cross["count"] for cross in census["cross"].values())
    pairs.append(("cross total", expected_types.get("Q", 0), crosses))

    return [pair for pair in pairs if pair[1] != pair[2]]


def run(args):
    entry = load_day(args.manifest, args.day)
    path = Path(args.file) if args.file else REPO_ROOT / "data" / entry["name"]
    if sum(entry["per_type_counts"].values()) != entry["messages"]:
        raise CheckError(f"day {args.day}: the manifest's per-type counts do not sum to messages")

    done = subprocess.run([str(args.census), str(path)], capture_output=True, text=True,
                          check=False)
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    if done.returncode not in (0, 1):
        print(f"census: FAIL itch_census exited {done.returncode}")
        return 1

    differences = compare(parse_census(done.stdout), entry)
    for field, expected, actual in differences:
        print(f"census: MISMATCH {field}: manifest {expected}, census {actual}")
    if done.returncode != 0:
        print("census: FAIL itch_census reported a failure")
        return 1
    if differences:
        print(f"census: FAIL {len(differences)} fields differ from the manifest")
        return 1
    print(f"census: OK {args.day}: {census_summary(entry)} agree exactly with the manifest")
    return 0


def census_summary(entry):
    return (f"{entry['messages']} messages, {entry['unpacked_bytes']} bytes, "
            f"{len(entry['per_type_counts'])} types, last message "
            f"{entry['last_message']['type']}/{entry['last_message']['event_code']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--day", required=True, help="key under days in the manifest")
    parser.add_argument("--file", help="the day's file; default data/<name in the manifest>")
    parser.add_argument("--manifest", default=REPO_ROOT / "scripts" / "data_manifest.json")
    parser.add_argument("--census", default=REPO_ROOT / "build" / "release" / "tools" / "itch_census")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (CheckError, OSError, ValueError, KeyError) as error:
        print(f"census: ERROR {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
