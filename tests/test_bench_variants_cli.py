#!/usr/bin/env python3
"""Black-box test of bench_variants on the committed oracle fixture.

Usage: test_bench_variants_cli.py <bench_variants> <scratch dir> <session.itch>

Every variant this build has runs, walks all 3,330 messages in each timed pass and leaves
the book where the switch form leaves it. The lookup passes run in the order check, answer,
answer, check, each reading the whole script with no mismatch. A variant the build or the
arm cannot run exits 2 with its reason and writes no record: the threaded form where the
compiler has no [[clang::musttail]], a look-ahead outside the arm's range, a look-ahead or
a lookup pass with another form, and a map design. On Linux, where an unpinned run is
refused, each run asks for the first core the test may use. A failure prints
`bench variants cli: FAIL <case>: <reason>`.
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
          "residency", "design", "input")
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

    def run(name, *options):
        out = scratch / f"{name}.json"
        out.unlink(missing_ok=True)
        done = subprocess.run([tool, "--input", session, "--out", str(out), *core_arguments(),
                               *options], capture_output=True, text=True, check=False)
        return done, out

    def published(name, *options):
        done, out = run(name, *options)
        if done.returncode != 0 or not out.is_file():
            failures.append(f"{name}: exit {done.returncode}: {done.stderr.strip()}")
            return None
        data = json.loads(out.read_text(encoding="utf-8"))
        missing = [field for field in HEADER if field not in data["header"]]
        if missing:
            failures.append(f"{name}: header lacks {missing}")
        if data["refusals"]:
            failures.append(f"{name}: refused: {data['refusals']}")
        for number, one in enumerate(data["passes"]):
            if one["messages"] != SESSION_MESSAGES or one["rejected"] != 0:
                failures.append(f"{name}: pass {number} walked {one['messages']}, "
                                f"rejected {one['rejected']}")
        return data

    switch = published("switch", "--dispatch", "switch")
    want = None
    if switch is not None:
        want = (switch["passes"][0]["live_orders"], switch["passes"][0]["live_levels"])
        if switch["variant"] != {"pass": "replay", "design": "paged-flat", "dispatch": "switch",
                                 "prefetch": 0, "threaded_built": switch["variant"]["threaded_built"]}:
            failures.append(f"switch: variant {switch['variant']}")
        if len(switch["passes"]) != 1 or switch["warmup_messages"] != SESSION_MESSAGES:
            failures.append("switch: wanted one timed pass after a whole warm-up")
    threaded_built = bool(switch and switch["variant"]["threaded_built"])

    forms = [("table", ("--dispatch", "table")),
             ("prefetch 4", ("--prefetch", "4")),
             ("prefetch 64", ("--prefetch", "64")),
             ("hash prefetch 3", ("--design", "hash-flat", "--prefetch", "3"))]
    if threaded_built:
        forms.append(("threaded", ("--dispatch", "threaded")))
    for name, options in forms:
        data = published(name.replace(" ", "_"), *options)
        if data is not None and want is not None and not name.startswith("hash"):
            got = (data["passes"][0]["live_orders"], data["passes"][0]["live_levels"])
            if got != want:
                failures.append(f"{name}: the book ended at {got}, the switch's at {want}")

    predicate = published("predicate", "--dispatch", "predicate")
    if predicate is not None and predicate["passes"][0]["fold"] == 0:
        failures.append("predicate: no header field was folded")

    for design in ("paged-flat", "hash-flat"):
        data = published(f"lookup_{design}", "--pass", "lookup", "--design", design)
        if data is None:
            continue
        modes = [one["mode"] for one in data["passes"]]
        if modes != ["check", "answer", "answer", "check"]:
            failures.append(f"lookup {design}: passes {modes}")
        entries = data["script"]["entries"]
        if entries == 0 or entries > data["script"]["capacity"]:
            failures.append(f"lookup {design}: {entries} script entries of "
                            f"{data['script']['capacity']}")
        for one in data["passes"]:
            if one["lookups"] != entries or one["mismatches"] != 0 or not one["script_exact"]:
                failures.append(f"lookup {design} {one['mode']}: read {one['lookups']} of "
                                f"{entries}, {one['mismatches']} mismatches")
            if want is not None and design == "paged-flat" and \
                    (one["live_orders"], one["live_levels"]) != want:
                failures.append(f"lookup {design} {one['mode']}: the book ended elsewhere")

    refusals = [("threaded", ("--dispatch", "threaded"), "musttail")] if not threaded_built else []
    refusals += [("prefetch 3", ("--prefetch", "3"), "--prefetch 3"),
                 ("prefetch 65", ("--prefetch", "65"), "--prefetch 65"),
                 ("hash prefetch 2", ("--design", "hash-flat", "--prefetch", "2"), "--prefetch 2"),
                 ("prefetch with table", ("--prefetch", "8", "--dispatch", "table"), "only with"),
                 ("lookup with table", ("--pass", "lookup", "--dispatch", "table"), "only with"),
                 ("lookup with prefetch", ("--pass", "lookup", "--prefetch", "8"), "only with"),
                 ("map design", ("--design", "map-fresh"), "usage"),
                 ("bad form", ("--dispatch", "computed-goto"), "usage"),
                 ("bad pass", ("--pass", "trace"), "usage")]
    for name, options, said in refusals:
        done, out = run(name.replace(" ", "_"), *options)
        if done.returncode != 2 or said not in done.stderr or out.exists():
            failures.append(f"{name}: exit {done.returncode}, record {out.exists()}, "
                            f"stderr {done.stderr.strip()!r}")

    for failure in failures:
        print(f"bench variants cli: FAIL {failure}")
    if failures:
        return 1
    print("bench variants cli: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
