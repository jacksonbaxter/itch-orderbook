#!/usr/bin/env python3
"""Black-box test of bench_parse on the committed oracle fixture.

Usage: test_bench_parse_cli.py <bench_parse> <scratch dir> <session.itch>

A run of two rounds must record every header field, the file's size, the frame reader's
counts, a warm-up of each pass, and six timed passes, framing, decode and strict in the first
round and decode, strict and framing in the second, each over every frame of the file and each
equal to its warm-up. A file cut inside its last frame is refused with its record still
written, and bad usage and an unreadable file exit 2. On Linux, where an unpinned run is
refused, each run asks for the first core the test may use. A failure prints
`bench parse cli: FAIL <case>: <reason>`.
"""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

HEADER = ("host", "cpu", "kernel", "compiler", "flags", "commit", "dataset", "dataset_sha256",
          "timer", "quantum", "pair", "floor", "pinning", "migrations", "core", "governor",
          "frequency", "turbo", "smt", "isolation", "counters", "rdpmc", "page_size",
          "residency")
SESSION_MESSAGES = 3330
TARGETS = {"framing": "T-PARSE-1", "decode": "T-PARSE-2", "strict": "T-PARSE-3"}


def core_arguments():
    if platform.system() == "Linux":
        return ["--core", str(min(os.sched_getaffinity(0)))]
    return []


def main():
    tool, scratch, session = sys.argv[1:4]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    failures = []

    def run(*args):
        return subprocess.run([tool, *args], capture_output=True, text=True, check=False)

    def record(name, source, *args, code=0):
        out = scratch / f"{name}.json"
        out.unlink(missing_ok=True)
        done = run("--input", str(source), "--out", str(out), *args, *core_arguments())
        if done.returncode != code:
            failures.append((name, f"exit {done.returncode}, not {code}: {done.stderr}"))
        if not out.is_file():
            failures.append((name, "no record written"))
            return None
        data = json.loads(out.read_text(encoding="utf-8"))
        missing = [field for field in HEADER if field not in data["header"]]
        if missing:
            failures.append((name, f"header fields missing: {missing}"))
        return data

    size = Path(session).stat().st_size
    whole = record("two rounds", session, "--rounds", "2")
    if whole:
        reference = whole["reference"]
        if whole["bytes"] != size or reference["consumed"] != size:
            failures.append(("two rounds", f"{whole['bytes']} bytes, not the file's {size}"))
        if reference["frames"] != SESSION_MESSAGES or reference["messages"] != SESSION_MESSAGES:
            failures.append(("two rounds", f"the reader counted {reference['frames']} frames"))
        order = [(p["round"], p["pass"]) for p in whole["passes"]]
        wanted = [(1, "framing"), (1, "decode"), (1, "strict"),
                  (2, "decode"), (2, "strict"), (2, "framing")]
        if order != wanted:
            failures.append(("two rounds", f"passes ran as {order}, not {wanted}"))
        for timed in whole["passes"]:
            if timed["target"] != TARGETS[timed["pass"]]:
                failures.append(("two rounds", f"{timed['pass']} names {timed['target']}"))
            if timed["totals"] != whole["warmup"][timed["pass"]]:
                failures.append(("two rounds", f"a {timed['pass']} pass differs from its warm-up"))
            if timed["totals"]["messages"] != SESSION_MESSAGES or timed["ticks"] <= 0:
                failures.append(("two rounds", f"a {timed['pass']} pass walked "
                                               f"{timed['totals']['messages']} frames"))
        strict = whole["warmup"]["strict"]
        if strict["decoded"] != SESSION_MESSAGES or strict["rejected"] != 0:
            failures.append(("two rounds", f"the strict decode decoded {strict['decoded']}"))
        decode = whole["warmup"]["decode"]
        if decode["decoded"] != reference["decode_types"] or decode["fold"] == 0:
            failures.append(("two rounds", f"the decode decoded {decode['decoded']} of "
                                           f"{reference['decode_types']}"))
        if whole["refusals"]:
            failures.append(("two rounds", f"refused: {whole['refusals']}"))

    cut = scratch / "cut.itch"
    cut.write_bytes(Path(session).read_bytes()[:-3])
    refused = record("cut", cut, "--rounds", "1", code=3)
    if refused and not any("frame reader stopped" in why for why in refused["refusals"]):
        failures.append(("cut", f"refused for {refused['refusals']}, not the cut"))

    for name, args in (("no output", ["--input", session]),
                       ("no rounds", ["--input", session, "--out", str(scratch / "x.json"),
                                      "--rounds", "0"]),
                       ("unreadable", ["--input", str(scratch / "missing.itch"),
                                       "--out", str(scratch / "y.json")])):
        done = run(*args)
        if done.returncode != 2:
            failures.append((name, f"exit {done.returncode}, not 2"))

    for name, why in failures:
        print(f"bench parse cli: FAIL {name}: {why}")
    if not failures:
        print("bench parse cli: ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
