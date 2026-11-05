#!/usr/bin/env python3
"""Black-box test of itch_extract and bench_demo on a day generated here.

Usage: test_demo_cli.py <itch_extract> <bench_demo> <scratch dir>

The day is gen_oracle_fixtures.session(), a seeded random session on six symbols, written
into the scratch directory at test time: nothing the demo replays is committed.
"""

import json
import struct
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gen_oracle_fixtures  # noqa: E402


def frames(data):
    """(type, locate, payload) of every frame."""
    at = 0
    while at < len(data):
        (length,) = struct.unpack_from(">H", data, at)
        payload = data[at + 2:at + 2 + length]
        yield chr(payload[0]), struct.unpack_from(">H", payload, 1)[0], payload
        at += 2 + length


def run(*command):
    return subprocess.run([str(word) for word in command], capture_output=True, text=True,
                          check=False)


def main():
    extract_tool, bench, scratch = sys.argv[1:4]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    day = scratch / "day.itch"
    day.write_bytes(gen_oracle_fixtures.session())
    failures = []

    def check(condition, what):
        if not condition:
            failures.append(what)

    # The cut: every frame on S3's locate from its directory on, and every System Event.
    cut = scratch / "s3.itch"
    result = run(extract_tool, day, "S3", cut)
    check(result.returncode == 0, f"itch_extract S3 exited {result.returncode}: {result.stderr}")
    summary = json.loads(result.stdout or "{}")
    data = cut.read_bytes() if cut.is_file() else b""
    kept = list(frames(data))
    whole = list(frames(day.read_bytes()))
    want = [f for f in whole if f[0] == "S" or (f[1] == 3)]
    check([f[2] for f in kept] == [f[2] for f in want],
          "the extract is not exactly S3's frames and the System Events, in order")
    check(summary.get("symbol") == "S3" and summary.get("locate") == 3, f"symbol: {summary}")
    check(summary.get("messages") == len(want) and summary.get("bytes") == len(data),
          f"counts: {summary.get('messages')} messages, {summary.get('bytes')} bytes")
    check(summary.get("day_messages") == len(whole), "day_messages")
    check(summary.get("extract_status") == "ok" and summary.get("applied") == len(want),
          f"replay: {summary}")
    statuses = summary.get("statuses", {})
    check(statuses.get("applied", 0) + statuses.get("no_book_effect", 0) == len(want),
          f"refusals in a clean extract: {statuses}")
    check(summary.get("fallback_inserts") == 0, "the paged index fell back")
    check(len(summary.get("digest", "")) == 16, "digest")

    # A symbol not in the day, a day cut short, and bad usage.
    missing = run(extract_tool, day, "NOPE", scratch / "nope.itch")
    check(missing.returncode == 1 and "no Stock Directory" in missing.stderr,
          f"an unknown symbol: exit {missing.returncode}")
    short = scratch / "short.itch"
    short.write_bytes(day.read_bytes()[:-3])
    cut_short = run(extract_tool, short, "S3", scratch / "short_s3.itch")
    check(cut_short.returncode == 2 and "frame boundary" in cut_short.stderr,
          f"a truncated day: exit {cut_short.returncode}")
    check(run(extract_tool, day, "S3").returncode == 2, "usage")
    check(run(extract_tool, day, "TOOLONGSYM", cut).returncode == 2, "a nine-character symbol")

    # bench_demo times the same replay and reaches the same digest.
    record_path = scratch / "native.json"
    timed = run(bench, "--extract", cut, "--out", record_path, "--repetitions", "3",
                "--commit", "test", "--dataset", "s3.itch", "--dataset-sha256", "0" * 64)
    check(timed.returncode == 0, f"bench_demo exited {timed.returncode}: {timed.stderr}")
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else {}
    check(record.get("digest") == summary.get("digest"),
          f"bench_demo's digest {record.get('digest')} is not itch_extract's")
    repetitions = record.get("repetitions", [])
    check(len(repetitions) == 3 and all(r["messages"] == len(want) and r["ns"] > 0
                                        for r in repetitions), f"repetitions: {repetitions}")
    check(record.get("statuses") == statuses, "statuses differ between the tools")
    check(record.get("refusals") == [], f"refusals: {record.get('refusals')}")
    check(record.get("header", {}).get("commit") == "test", "the header lost the commit")
    check(record.get("peak_rss_bytes", 0) > 0, "peak_rss_bytes")

    # The whole day is six symbols: bench_demo refuses it, and still writes the record.
    refused_path = scratch / "refused.json"
    refused = run(bench, "--extract", day, "--out", refused_path, "--repetitions", "3")
    check(refused.returncode == 3 and "not one symbol's" in refused.stderr,
          f"bench_demo on a six-symbol day: exit {refused.returncode}")
    check(refused_path.is_file() and json.loads(refused_path.read_text())["refusals"],
          "the refused record")
    check(run(bench, "--extract", cut, "--out", record_path, "--repetitions", "2").returncode == 2,
          "fewer than 3 repetitions")

    for failure in failures:
        print(f"FAIL: {failure}")
    print(f"test_demo_cli: {'FAILED' if failures else 'ok'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
