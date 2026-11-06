#!/usr/bin/env python3
"""Black-box test of bench_kernels on the committed oracle fixture.

Usage: test_bench_kernels_cli.py <bench_kernels> <scratch dir> <session.itch>

--list-kernels names every kernel, says which run here and why the others do not. Every
experiment runs on scalar and on every vector kernel that runs here, walks all 3,330 messages
in each timed pass, in the order the experiment sets, and computes what scalar computes: the
same fold and items, mode by mode. verify finds no message where a vector kernel differs from
scalar. auto runs the widest kernel and says it came from auto. A kernel this build lacks, a
kernel this CPU cannot run, an unknown kernel and a vector kernel asked of an experiment that
uses none each exit 2 with the reason and write no record: never another kernel in its place.
On Linux, where an unpinned run is refused, each run asks for the first core the test may
use. A failure prints `bench kernels cli: FAIL <case>: <reason>`.
"""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

HEADER = ("host", "cpu", "kernel", "compiler", "flags", "commit", "dataset", "dataset_sha256",
          "timer", "pinning", "counters", "residency", "design", "input")
KERNELS = ("scalar", "neon", "ssse3", "avx2", "avx512vbmi", "wasm")
SESSION_MESSAGES = 3330
PAIRED = ("walk", "work", "work", "walk")
MODES = {"field": PAIRED, "decode": PAIRED, "consume": PAIRED, "batch": PAIRED,
         "filter": PAIRED, "timestamp": PAIRED, "framing": ("work", "work"),
         "book": ("work",), "verify": (),
         "parse": ("walk", "all", "A", "F", "E", "C", "X", "D", "U", "all", "walk")}


def core_arguments():
    if platform.system() == "Linux":
        return ["--core", str(min(os.sched_getaffinity(0)))]
    return []


def main():
    tool, scratch, session = sys.argv[1:4]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    failures = []

    listed = subprocess.run([tool, "--list-kernels"], capture_output=True, text=True,
                            check=False)
    lines = dict(line.split(": ", 1) for line in listed.stdout.splitlines())
    if listed.returncode != 0 or list(lines) != [*KERNELS, "auto"]:
        failures.append(f"list: exit {listed.returncode}, {listed.stdout!r}")
    runs = [name for name in KERNELS if lines.get(name) == "runs"]
    cannot = {name: lines.get(name, "") for name in KERNELS if name not in runs}
    if "scalar" not in runs or lines.get("auto") not in runs:
        failures.append(f"list: scalar must run and auto must name a kernel that runs: {lines}")
    for name, why in cannot.items():
        if "is not built" not in why and "cannot run here" not in why:
            failures.append(f"list: {name} gives no reason: {why!r}")

    def run(name, *options):
        out = scratch / f"{name}.json"
        out.unlink(missing_ok=True)
        done = subprocess.run([tool, "--input", session, "--out", str(out), *core_arguments(),
                               *options], capture_output=True, text=True, check=False)
        return done, out

    def published(name, experiment, kernel, *options):
        done, out = run(name, "--experiment", experiment, "--kernel", kernel, *options)
        if done.returncode != 0 or not out.is_file():
            failures.append(f"{name}: exit {done.returncode}: {done.stderr.strip()}")
            return None
        data = json.loads(out.read_text(encoding="utf-8"))
        missing = [field for field in HEADER if field not in data["header"]]
        if missing:
            failures.append(f"{name}: header lacks {missing}")
        if data["refusals"]:
            failures.append(f"{name}: refused: {data['refusals']}")
        variant = data["variant"]
        want = lines.get("auto") if kernel == "auto" else kernel
        if variant["kernel"] != want or variant["from_auto"] != (kernel == "auto"):
            failures.append(f"{name}: ran {variant['kernel']}, from auto {variant['from_auto']}")
        modes = tuple(one["mode"] for one in data["passes"])
        if modes != MODES[experiment]:
            failures.append(f"{name}: passes {modes}, wanted {MODES[experiment]}")
        for number, one in enumerate(data["passes"]):
            if one["messages"] != SESSION_MESSAGES:
                failures.append(f"{name}: pass {number} walked {one['messages']}")
        return data

    def answers(data):
        return [(one["mode"], one["fold"], one["items"], one["matches"])
                for one in data["passes"]]

    def same_as_scalar(case, experiment, *options):
        base = published(f"{case}-scalar", experiment, "scalar", *options)
        for kernel in runs[1:]:
            data = published(f"{case}-{kernel}", experiment, kernel, *options)
            if base is not None and data is not None and answers(data) != answers(base):
                failures.append(f"{case}-{kernel}: computed {answers(data)}, scalar "
                                f"{answers(base)}")
        return base

    for width in ("2", "4", "6", "8"):
        same_as_scalar(f"field-w{width}", "field", "--width", width)
    for experiment in ("decode", "consume", "parse"):
        same_as_scalar(experiment, experiment)
    book = same_as_scalar("book", "book")
    if book is not None and book["book"]["live_orders"] == 0:
        failures.append("book: the fixture leaves orders on the book, and this one has none")
    batch = same_as_scalar("batch", "batch")
    if batch is not None and sum(batch["runs"]["any_type"]) != SESSION_MESSAGES:
        failures.append(f"batch: run lengths cover {sum(batch['runs']['any_type'])} messages")
    compare = same_as_scalar("filter-compare", "filter", "--form", "compare", "--watchlist", "2")
    bitset = published("filter-bitset", "filter", "scalar", "--form", "bitset", "--watchlist",
                       "2")
    if compare is not None and bitset is not None:
        if answers(compare) != answers(bitset):
            failures.append(f"filter: compare found {answers(compare)}, bitset "
                            f"{answers(bitset)}")
        if compare["passes"][1]["matches"] == 0:
            failures.append("filter: the two most active stocks matched no Add Order")
    stamps = [published(f"timestamp-{form}", "timestamp", "scalar", "--form", form)
              for form in ("exact", "split", "overread", "underread")]
    if all(stamps) and any(answers(one) != answers(stamps[0]) for one in stamps):
        failures.append(f"timestamp: the forms differ: {[answers(one) for one in stamps]}")
    walks = [published(f"framing-{form}", "framing", "scalar", "--form", form)
             for form in ("length", "byte", "table", "constant")]
    if all(walks) and any(answers(one) != answers(walks[0]) for one in walks):
        failures.append(f"framing: the forms differ: {[answers(one) for one in walks]}")
    for kernel in runs:
        data = published(f"verify-{kernel}", "verify", kernel)
        if data is not None and (data["verify"]["compared"] == 0 or
                                 data["verify"]["mismatches"] != 0):
            failures.append(f"verify-{kernel}: {data['verify']}")
    published("auto", "decode", "auto")

    refused = [("unknown kernel", ("--experiment", "decode", "--kernel", "sse9"),
                "unknown kernel"),
               ("vector kernel for framing", ("--experiment", "framing", "--form", "length",
                                              "--kernel", runs[-1]),
                "uses no kernel" if len(runs) > 1 else None),
               ("width outside field", ("--experiment", "decode", "--width", "4"), "--width")]
    refused += [(f"{name} here", ("--experiment", "decode", "--kernel", name), why)
                for name, why in cannot.items()]
    for case, options, said in refused:
        if said is None:
            continue
        done, out = run(case.replace(" ", "-"), *options)
        if done.returncode != 2 or out.exists() or said not in done.stderr:
            failures.append(f"{case}: exit {done.returncode}, record {out.exists()}, "
                            f"stderr {done.stderr.strip()!r}")

    for failure in failures:
        print(f"bench kernels cli: FAIL {failure}")
    if failures:
        return 1
    print(f"bench kernels cli: ok, kernels that run here: {', '.join(runs)}; refused with "
          f"their reason: {', '.join(cannot)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
