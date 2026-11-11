#!/usr/bin/env python3
"""Black-box test of bench_walks on the committed oracle fixture.

Usage: test_bench_walks_cli.py <bench_walks> <scratch dir> <session.itch>

session.itch is a seeded random session on symbols S1 to S5 whose regular hours last 3 ms.
The shadow load places 200 orders over 3 ms with a 1 ms life, as bench_backtest's CLI test
does, so that orders rest, trade and leave. The record must say the counted engine agreed
with the engine, that the counts are consistent and every side empty at the end, and hold
every walk site with histograms that sum to its walks. A longer life holds more orders at
once, and so deeper sides. Bad usage, an unreadable file and a day with no fee schedule exit
2. A failure prints `bench walks cli: FAIL <case>: <reason>`.
"""

import json
import subprocess
import sys
from pathlib import Path

SITES = ("execution", "cross", "depletion", "late_add", "hidden_print", "rest")
SYMBOLS = tuple(word for n in range(1, 6) for word in ("--symbol", f"S{n}"))
SESSION_MESSAGES = 3330


def main():
    tool, scratch, session = sys.argv[1:4]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    failures = []

    def run(*args):
        return subprocess.run([tool, *args], capture_output=True, text=True, check=False)

    def record(name, life, code=0):
        out = scratch / f"{name}.json"
        out.unlink(missing_ok=True)
        done = run("--input", session, "--out", str(out), "--date", "20191230", *SYMBOLS,
                   "--shadow-orders", "200", "--shadow-span-ns", "3000000",
                   "--shadow-life-ns", str(life))
        if done.returncode != code:
            failures.append((name, f"exit {done.returncode}, not {code}: {done.stderr}"))
        if not out.is_file():
            failures.append((name, "no record written"))
            return None
        return json.loads(out.read_text(encoding="utf-8"))

    def mean_depth(data):
        walks = sum(data["sites"][site]["walks"] for site in SITES)
        levels = sum(depth * count for site in SITES for depth, count in data["sites"][site]["depth"])
        return levels / walks

    short = record("short life", 1_000_000)
    if short:
        if not (short["identical"] and short["consistent"] and short["empty_at_end"]):
            failures.append(("short life", "the counted engine disagreed, or the counts did not "
                                           "close"))
        if short["refusals"]:
            failures.append(("short life", f"refused: {short['refusals']}"))
        if short["input_messages"] != SESSION_MESSAGES:
            failures.append(("short life", f"{short['input_messages']} messages in the day"))
        engine = short["engine"]
        if engine["placed"] + engine["skipped"] != 200 or engine["fills"] == 0:
            failures.append(("short life", f"engine counts {engine}"))
        for site in SITES:
            data = short["sites"].get(site)
            if data is None:
                failures.append(("short life", f"no site {site}"))
                continue
            if sum(count for _, count in data["depth"]) != data["walks"]:
                failures.append((site, "the depth histogram does not sum to the walks"))
            if sum(count for _, count in data["steps"]) != data["walks"]:
                failures.append((site, "the steps histogram does not sum to the walks"))
            if sum(steps * count for steps, count in data["steps"]) != data["level_steps"]:
                failures.append((site, "the steps histogram does not sum to the level steps"))
        for site in ("execution", "cross", "depletion", "rest"):
            if short["sites"][site]["level_steps"] == 0:
                failures.append((site, "no level step on the fixture"))

    long = record("long life", 3_000_000)
    if short and long and not mean_depth(long) > mean_depth(short):
        failures.append(("long life", f"mean depth {mean_depth(long):.3f}, not above "
                                      f"{mean_depth(short):.3f}"))

    out = str(scratch / "usage.json")
    base = ("--input", session, "--out", out)
    for name, args in (("no date", base),
                       ("short date", (*base, "--date", "2019123")),
                       ("no fee schedule", (*base, "--date", "19990101")),
                       ("zero orders", (*base, "--date", "20191230", "--shadow-orders", "0")),
                       ("zero life", (*base, "--date", "20191230", "--shadow-life-ns", "0")),
                       ("long symbol", (*base, "--date", "20191230", "--symbol", "ABCDEFGHI")),
                       ("odd arguments", (*base, "--date")),
                       ("unknown flag", (*base, "--date", "20191230", "--fast", "1")),
                       ("no file", ("--input", str(scratch / "missing.itch"), "--out", out,
                                    "--date", "20191230"))):
        done = run(*args)
        if done.returncode != 2:
            failures.append((name, f"exit {done.returncode}, not 2"))

    for case, reason in failures:
        print(f"bench walks cli: FAIL {case}: {reason}")
    if failures:
        return 1
    print("bench walks cli: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
