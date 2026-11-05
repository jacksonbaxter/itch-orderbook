#!/usr/bin/env python3
"""Black-box test of bench_spsc and bench_pipeline on the committed oracle fixture.

Usage: test_bench_spsc_cli.py <bench_spsc> <bench_pipeline> <scratch dir> <session.itch>

session.itch's regular hours last 3 ms from 09:30, so bench_spsc's default burst, two
seconds from 09:30 at the day's own pace, replays all of them in 3 ms. Each suite runs with
small counts. Every record must hold every header field and no refusal, and every row must
move its items with none wrong: every queue this build has in the comparison, every
configuration in the ablation, and batch publish against a store per item. bench_pipeline
must leave the same book three ways, warm-up and timed, and hold the same book at its
checkpoint, op 1000, in each; a checkpoint past the file's last op is refused with exit 3.
Bad usage and an unreadable file exit 2. On Linux, where an unpinned run is refused, each run asks for the first two cores
the test may use. A failure prints `bench spsc cli: FAIL <case>: <reason>`.
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
PROTOCOLS = ("rtt", "saturated", "paced-1000ns", "paced-10000ns", "burst")
VARIANTS = ("shipped", "uncached", "modulo", "uncached-modulo", "padding-8", "padding-256")
SESSION_MESSAGES = 3330
REGULAR_HOURS_MESSAGES = 3313
CHECKPOINT = 1000
SMALL = ("--rtt-trips", "2000", "--saturated-items", "300000", "--paced-items", "500")


def core_arguments():
    if platform.system() == "Linux":
        cores = sorted(os.sched_getaffinity(0))
        return ["--core", str(cores[0]), "--peer-core", str(cores[1 % len(cores)])]
    return []


def main():
    spsc, pipeline, scratch, session = sys.argv[1:5]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    failures = []

    def record(name, tool, *args, code=0):
        out = scratch / f"{name}.json"
        out.unlink(missing_ok=True)
        done = subprocess.run([tool, "--input", session, "--out", str(out), *args,
                               *core_arguments()], capture_output=True, text=True, check=False)
        if done.returncode != code:
            failures.append((name, f"exit {done.returncode}, not {code}: {done.stderr}"))
        if not out.is_file():
            failures.append((name, "no record written"))
            return None
        data = json.loads(out.read_text(encoding="utf-8"))
        missing = [field for field in HEADER if field not in data["header"]]
        if missing:
            failures.append((name, f"header fields missing: {missing}"))
        if data["refusals"]:
            failures.append((name, f"refused: {data['refusals']}"))
        return data

    def rows_of(name, data):
        rows = {}
        for row in data["rows"]:
            rows[(row["queue"], row["protocol"])] = row
            if row["errors"] != 0 or row["items"] <= 0 or row["ns"] <= 0:
                failures.append((name, f"{row['queue']} {row['protocol']}: {row['errors']} "
                                       f"wrong of {row['items']} in {row['ns']} ns"))
            if row["protocol"].startswith(("paced", "burst")) and \
                    row["latency"]["samples"] != row["items"]:
                failures.append((name, f"{row['queue']} {row['protocol']}: "
                                       f"{row['latency']['samples']} samples"))
        return rows

    queues = record("queues", spsc, "--suite", "queues", *SMALL)
    if queues:
        rows = rows_of("queues", queues)
        names = ["project", "mutex"] + (
            ["rigtorp", "boost", "moodycamel", "folly", "atomic_queue", "xenium_vyukov"]
            if queues["comparison_queues"] else [])
        for queue in names:
            for protocol in PROTOCOLS:
                if (queue, protocol) not in rows:
                    failures.append(("queues", f"no {queue} {protocol} row"))
        if len(rows) != len(names) * len(PROTOCOLS):
            failures.append(("queues", f"{len(rows)} rows for {len(names)} queues"))
        burst = queues["burst"]
        if burst["messages"] != REGULAR_HOURS_MESSAGES or burst["speedup"] != 1:
            failures.append(("queues", f"burst of {burst['messages']} at {burst['speedup']}"))
        if queues["capacity"] != 131072 or queues["element_bytes"] != 8:
            failures.append(("queues", "not the stated capacity and element"))

    one = record("one queue", spsc, "--suite", "queues", "--queue", "mutex", *SMALL)
    if one and {row["queue"] for row in one["rows"]} != {"mutex"}:
        failures.append(("one queue", "--queue mutex ran another"))

    ablation = record("ablation", spsc, "--suite", "ablation", *SMALL)
    if ablation:
        rows = rows_of("ablation", ablation)
        for variant in VARIANTS:
            for protocol in ("rtt", "saturated", "burst"):
                if (variant, protocol) not in rows:
                    failures.append(("ablation", f"no {variant} {protocol} row"))
        for variant in ("batch-publish", "publish-each"):
            if (variant, "saturated-batch-32") not in rows:
                failures.append(("ablation", f"no {variant} row"))

    replay = record("pipeline", pipeline, "--checkpoint-ops", str(CHECKPOINT))
    if replay:
        passes = replay["warmup"] + replay["timed"]
        if sorted(p["mode"] for p in replay["timed"]) != ["decoded", "pipelined", "single"]:
            failures.append(("pipeline", "not one timed pass of each mode"))
        if len({p["book_hash"] for p in passes}) != 1:
            failures.append(("pipeline", "the passes left different books"))
        checkpoints = {(p["checkpoint_hash"], p.get("checkpoint_orders")) for p in passes}
        if replay["checkpoint_ops"] != CHECKPOINT or len(checkpoints) != 1 or \
                None in checkpoints.pop():
            failures.append(("pipeline", "the passes held different books at the checkpoint"))
        if any(p["messages"] != SESSION_MESSAGES for p in passes):
            failures.append(("pipeline", "a pass did not read the whole file"))
        ops = {p["ops"] for p in passes if p["mode"] != "single"}
        if len(ops) != 1 or not 0 < ops.pop() < SESSION_MESSAGES:
            failures.append(("pipeline", "the pipelined and decoded passes moved other ops"))

    past = scratch / "past.json"
    done = subprocess.run([pipeline, "--input", session, "--out", str(past), "--checkpoint-ops",
                           "100000", *core_arguments()], capture_output=True, text=True,
                          check=False)
    if done.returncode != 3 or "never reached op 100000" not in done.stderr:
        failures.append(("checkpoint past the end", f"exit {done.returncode}: {done.stderr}"))

    for name, args in (("no input", ["--out", str(scratch / "x.json")]),
                       ("bad suite", ["--suite", "other", "--input", session, "--out",
                                      str(scratch / "x.json")]),
                       ("unreadable", ["--suite", "queues", "--input",
                                       str(scratch / "absent.itch"), "--out",
                                       str(scratch / "x.json")])):
        done = subprocess.run([spsc, *args], capture_output=True, text=True, check=False)
        if done.returncode != 2:
            failures.append((name, f"bench_spsc exit {done.returncode}, not 2"))
    done = subprocess.run([pipeline, "--input", str(scratch / "absent.itch"), "--out",
                           str(scratch / "x.json")], capture_output=True, text=True, check=False)
    if done.returncode != 2:
        failures.append(("unreadable", f"bench_pipeline exit {done.returncode}, not 2"))

    for name, reason in failures:
        print(f"bench spsc cli: FAIL {name}: {reason}")
    if failures:
        return 1
    print("bench spsc cli: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
