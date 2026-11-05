#!/usr/bin/env python3
"""Black-box test of bench_backtest on the committed oracle fixture.

Usage: test_bench_backtest_cli.py <bench_backtest> <scratch dir> <session.itch>

session.itch is a seeded random session on symbols S1 to S5 whose regular hours last 3 ms.
The quoting workload trades S1 and S2 on the whole file. The shadow workload places its
orders over 3 ms with a 1 ms life, so that some trade; on its default schedule, spread over
the day's regular hours, none can, and the run is refused with its record still written.
Each record must hold every header field, a warm-up and a timed pass that agree, and the
counts that say what the workload was. Bad usage, an unreadable file and a day with no fee
schedule exit 2. On Linux, where an unpinned run is refused, each run asks for the first
core the test may use. A failure prints `bench backtest cli: FAIL <case>: <reason>`.
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
AGREED = ("messages", "deliveries", "arrivals", "decisions", "fills", "strategy_events",
          "wakes", "event_hash")
SESSION_MESSAGES = 3330


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

    def record(name, *args, code=0):
        out = scratch / f"{name}.json"
        out.unlink(missing_ok=True)
        done = run("--input", session, "--out", str(out), "--date", "20191230", *args,
                   *core_arguments())
        if done.returncode != code:
            failures.append((name, f"exit {done.returncode}, not {code}: {done.stderr}"))
        if not out.is_file():
            failures.append((name, "no record written"))
            return None
        data = json.loads(out.read_text(encoding="utf-8"))
        missing = [field for field in HEADER if field not in data["header"]]
        if missing:
            failures.append((name, f"header fields missing: {missing}"))
        for field in AGREED:
            if data["warmup"][field] != data["timed"][field]:
                failures.append((name, f"warm-up and timed pass differ on {field}"))
        if data["timed"]["ns"] <= 0:
            failures.append((name, "no time"))
        return data

    symbols = ("--symbol", "S1", "--symbol", "S2")
    quoting = record("quoting", "--workload", "quoting", *symbols)
    if quoting:
        timed = quoting["timed"]
        if timed["messages"] != SESSION_MESSAGES or quoting["input_messages"] != SESSION_MESSAGES:
            failures.append(("quoting", f"{timed['messages']} messages, not the whole file"))
        if timed["fills"] == 0 or timed["sends"] == 0 or timed["replaces"] == 0:
            failures.append(("quoting", "the quoter neither sent, replaced nor traded"))
        if quoting["latency_ns"]["market_data"] == 0 or quoting["extract"] is not None:
            failures.append(("quoting", "not the base latencies on the whole file"))
        if quoting["refusals"]:
            failures.append(("quoting", f"refused: {quoting['refusals']}"))
    again = record("quoting again", "--workload", "quoting", *symbols)
    if quoting and again and again["timed"]["event_hash"] != quoting["timed"]["event_hash"]:
        failures.append(("repeat", "the same run gave another event hash"))

    every = tuple(word for n in range(1, 6) for word in ("--symbol", f"S{n}"))
    shadow = record("shadow", "--workload", "shadow", *every, "--shadow-orders", "200",
                    "--shadow-span-ns", "3000000", "--shadow-life-ns", "1000000")
    if shadow:
        timed = shadow["timed"]
        extract = shadow["extract"]
        if extract is None or extract["messages"] != timed["messages"]:
            failures.append(("shadow", "the engine did not run on the extract alone"))
        elif not 0 < extract["messages"] < shadow["input_messages"] == SESSION_MESSAGES:
            failures.append(("shadow", f"extract of {extract['messages']} messages"))
        if any(shadow["latency_ns"].values()):
            failures.append(("shadow", f"latencies not zero: {shadow['latency_ns']}"))
        if timed["sends"] + timed["skipped"] != 200 or timed["fills"] == 0:
            failures.append(("shadow", f"{timed['sends']} placed, {timed['skipped']} skipped, "
                                       f"{timed['fills']} fills"))
        if timed["cancels"] != timed["sends"]:
            failures.append(("shadow", "not every placed order was cancelled at its life's end"))
        if not str(shadow["input"]).startswith("extract: "):
            failures.append(("shadow", f"input field {shadow['input']!r}"))

    idle = record("shadow day schedule", "--workload", "shadow", *every, code=3)
    if idle and "no simulated order traded" not in idle["refusals"]:
        failures.append(("shadow day schedule", f"refusals {idle['refusals']}"))

    out = str(scratch / "usage.json")
    base = ("--input", session, "--out", out)
    for name, args in (("no workload", (*base, "--date", "20191230")),
                       ("bad workload", (*base, "--date", "20191230", "--workload", "fast")),
                       ("no date", (*base, "--workload", "quoting")),
                       ("short date", (*base, "--workload", "quoting", "--date", "2019123")),
                       ("long symbol", (*base, "--workload", "quoting", "--date", "20191230",
                                        "--symbol", "ABCDEFGHI")),
                       ("zero orders", (*base, "--workload", "shadow", "--date", "20191230",
                                        "--shadow-orders", "0")),
                       ("odd arguments", (*base, "--workload", "quoting", "--date")),
                       ("unknown flag", (*base, "--workload", "quoting", "--date", "20191230",
                                         "--fast", "1")),
                       ("missing file", ("--input", str(scratch / "absent.itch"), "--out", out,
                                         "--workload", "quoting", "--date", "20191230")),
                       ("no fee schedule", (*base, "--workload", "quoting", "--date",
                                            "20200102"))):
        done = run(*args)
        if done.returncode != 2:
            failures.append((name, f"exit {done.returncode}, not 2"))

    for case, reason in failures:
        print(f"bench backtest cli: FAIL {case}: {reason}")
    if failures:
        return 1
    print("bench backtest cli: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
