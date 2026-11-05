#!/usr/bin/env python3
"""Black-box test of bench_counts on the committed oracle fixture.

Usage: test_bench_counts_cli.py <bench_counts> <scratch dir> <session.itch>

Where user-mode rdpmc works, as on debian, the run publishes, and its spans by type must add
up to the whole pass exactly, one span a frame, with every book message in a book type.
Where it does not, as on macOS or a CI runner, the run is refused with its record still
written and the reason in it. Either way every header field is there. map-aged and bad usage
exit 2. On Linux, where an unpinned run is refused, the run asks for the first core the test
may use. A failure prints `bench counts cli: FAIL <case>: <reason>`.
"""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

HEADER = ("host", "cpu", "kernel", "compiler", "flags", "commit", "dataset", "dataset_sha256",
          "timer", "quantum", "pair", "floor", "pinning", "migrations", "core", "governor",
          "frequency", "turbo", "smt", "isolation", "counters", "rdpmc", "residency", "design",
          "input")
SESSION_MESSAGES = 3330
BOOK_TYPES = set("AFECXDUPQ")


def core_arguments():
    if platform.system() == "Linux":
        return ["--core", str(min(os.sched_getaffinity(0)))]
    return []


def check_published(data, failures):
    events = data["events"]
    whole = data["pass"]["whole"]
    for index, name in enumerate(events):
        summed = sum(entry["sums"][index] for entry in data["types"])
        if summed != whole[index] or data["pass"]["by_type_total"][index] != whole[index]:
            failures.append(("tiling", f"{name}: types {summed}, whole {whole[index]}"))
    if not data["pass"]["tiled"]:
        failures.append(("tiling", "the binary says the spans do not tile"))
    spans = sum(entry["spans"] for entry in data["types"])
    if spans != data["pass"]["frames"] or data["mix"]["messages"] != SESSION_MESSAGES:
        failures.append(("spans", f"{spans} spans, {data['pass']['frames']} frames"))
    book = sum(entry["spans"] for entry in data["types"] if entry["type"] in BOOK_TYPES)
    if book != data["mix"]["book_messages"]:
        failures.append(("book", f"{book} book spans, {data['mix']['book_messages']} counted"))
    if data["empty_before"]["spans"] != 1000 or len(data["empty_before"]["sums"]) != len(events):
        failures.append(("empty", "the empty spans are not recorded"))


def main():
    tool, scratch, session = sys.argv[1:4]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    failures = []

    def run(*args):
        return subprocess.run([tool, *args], capture_output=True, text=True, check=False)

    out = scratch / "counts.json"
    out.unlink(missing_ok=True)
    done = run("--input", session, "--out", str(out), "--empty-spans", "1000",
               *core_arguments())
    if done.returncode not in (0, 3) or not out.is_file():
        failures.append(("run", f"exit {done.returncode}, record {out.is_file()}: {done.stderr}"))
    else:
        data = json.loads(out.read_text(encoding="utf-8"))
        missing = [field for field in HEADER if field not in data["header"]]
        if missing:
            failures.append(("header", f"fields missing: {missing}"))
        if done.returncode == 0:
            check_published(data, failures)
        elif not any("rdpmc" in reason or "counter" in reason for reason in data["refusals"]):
            failures.append(("refusal", f"refused for another reason: {data['refusals']}"))
    for name, args in (("map-aged", ("--input", session, "--out", str(out), "--design",
                                     "map-aged")),
                       ("no-input", ("--out", str(out))),
                       ("zero-empty-spans", ("--input", session, "--out", str(out),
                                             "--empty-spans", "0"))):
        code = run(*args).returncode
        if code != 2:
            failures.append((name, f"exit {code}, not 2"))
    for case, reason in failures:
        print(f"bench counts cli: FAIL {case}: {reason}")
    if failures:
        return 1
    print(f"bench counts cli: ok, exit {done.returncode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
