#!/usr/bin/env python3
"""Black-box test of bench_udp, one T-URING run of the UDP replay on loopback.

Usage: test_bench_udp_cli.py <bench_udp> <scratch dir> <fixture day> <modes built, comma-separated>

Every built mode, paced, passes on the fixture day with its record whole: the book matches
the file's, every message was delivered, latency is from the schedule, and the record says
loopback is not a network. An unpaced run has no latency; a feed-shaped run has its schedule;
the sender alone passes; --peaks and --probe say what they find; and bad usage, a mode the
build lacks and a packing too large for a batching mode's slot exit 2. Nothing here is a
measurement: the figures in these records are never reported.

A failure prints `bench_udp cli: FAIL <case>: <reason>`.
"""

import json
import subprocess
import sys
from pathlib import Path

MODES = ("S1", "S2", "S3", "S4", "U1", "U2", "U3", "U4", "U5")
SMALL = ["--max-orders", "65536", "--max-levels", "65536", "--max-pages", "64",
         "--max-slots", "65536"]
DROP_KEYS = ("failed_sends", "tracker_gaps", "tracker_lost", "tracker_duplicates", "truncated",
             "no_buffers", "cq_overflow", "rxq_overflow", "socket_drops", "rcvbuf_errors",
             "in_errors", "softnet_dropped")


def main():
    tool, scratch, day, built = sys.argv[1:5]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    built = built.split(",")
    failures = []

    def run(*args):
        return subprocess.run([tool, *args], capture_output=True, text=True, check=False,
                              timeout=120)

    def record(name, *args, expect=0):
        out = scratch / f"{name}.json"
        out.unlink(missing_ok=True)
        done = run(day, *args, *SMALL, "--out", str(out))
        if done.returncode != expect:
            failures.append((name, f"exit {done.returncode}, not {expect}: {done.stderr[-500:]}"))
            return None
        try:
            return json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            failures.append((name, f"no record: {error}"))
            return None

    def check_receive(name, got, mode, schedule):
        if got is None:
            return
        want = {"tool": "bench_udp", "kind": "receive", "mode": mode, "verdict": "PASS",
                "delivered": True}
        for key, value in want.items():
            if got.get(key) != value:
                failures.append((name, f"{key} is {got.get(key)!r}, not {value!r}"))
        if "not a network" not in got.get("comparability", ""):
            failures.append((name, "the record does not say loopback is not a network"))
        if not got.get("book", {}).get("match") or \
                got["book"].get("file_end_hash") != got["book"].get("udp_end_hash"):
            failures.append((name, "the book does not match the file's"))
        if got.get("schedule", {}).get("kind") != schedule:
            failures.append((name, f"schedule {got.get('schedule')}, not {schedule}"))
        missing = [key for key in DROP_KEYS if key not in got.get("drops", {})]
        if missing:
            failures.append((name, f"drop counters missing: {missing}"))
        if got.get("send", {}).get("sent_packets") != got.get("plan", {}).get("packets"):
            failures.append((name, "not every packet was sent"))
        latency = got.get("latency")
        if schedule == "unpaced":
            if latency is not None:
                failures.append((name, "an unpaced run reports a latency"))
        elif not latency or latency.get("samples", 0) <= 0 or "p99_ns" not in latency or \
                "due time" not in latency.get("from", ""):
            failures.append((name, f"latency {latency}"))
        if got.get("lossless") and got["drops"].get("rcvbuf_errors") is None:
            failures.append((name, "lossless claimed without the host's counters"))
        for part in ("sender", "receiver", "kernel"):
            if part not in got.get("cpu", {}):
                failures.append((name, f"no {part} CPU"))

    for mode in built:
        got = record(f"{mode}-paced", "--mode", mode, "--rate", "2000000", "--warmup-ms", "0")
        check_receive(f"{mode} paced", got, mode, "rate")

    check_receive("unpaced", record("unpaced", "--mode", "S1"), "S1", "unpaced")
    fed = record("feed", "--mode", "S1", "--feed-speedup", "1000000", "--warmup-ms", "0",
                 "--flush-gap-ns", "1000000")
    check_receive("feed", fed, "S1", "feed")
    if fed is not None and fed.get("schedule", {}).get("span_ns", 0) <= 0:
        failures.append(("feed", "the feed schedule spans no time"))

    sender = record("sender", "--mode", "sender")
    if sender is not None:
        if sender.get("kind") != "sender" or sender.get("verdict") != "PASS":
            failures.append(("sender", f"kind {sender.get('kind')}, verdict {sender.get('verdict')}"))
        if sender.get("send", {}).get("paced") is not False or \
                sender.get("send", {}).get("achieved_messages_per_s", 0) <= 0:
            failures.append(("sender", f"send {sender.get('send')}"))
        if "not a network" not in sender.get("comparability", ""):
            failures.append(("sender", "the record does not say loopback is not a network"))

    peaks = run(day, "--peaks")
    lines = peaks.stdout.splitlines()
    if peaks.returncode != 0 or len(lines) != 3 or not lines[1].startswith("bench_udp_peak_1s ") or \
            not lines[2].startswith("bench_udp_peak_1ms "):
        failures.append(("peaks", f"exit {peaks.returncode}: {peaks.stdout}"))

    probe = run("--probe")
    said = {}
    for line in probe.stdout.splitlines():
        words = line.split(" ", 2)
        if len(words) == 3 and words[0] == "bench_udp_probe":
            said[words[1]] = words[2]
    if probe.returncode != 0 or sorted(said) != sorted(MODES):
        failures.append(("probe", f"exit {probe.returncode}, modes {sorted(said)}"))
    for mode in MODES:
        want = "ok" if mode in built else "not built"
        if not said.get(mode, "").startswith(want):
            failures.append((f"probe {mode}", f"said {said.get(mode)!r}, not {want}"))

    bad = [("no mode", (day,)), ("unknown mode", (day, "--mode", "U6")),
           ("rate and feed", (day, "--mode", "S1", "--rate", "10", "--feed-speedup", "2")),
           ("zero speedup", (day, "--mode", "S1", "--feed-speedup", "0")),
           ("no file", ("--mode", "S1")), ("bad number", (day, "--mode", "S1", "--rate", "x")),
           ("missing file", (str(scratch / "absent.itch"), "--mode", "S1"))]
    bad += [(f"{mode} not built", (day, "--mode", mode)) for mode in MODES if mode not in built]
    bad += [(f"{mode} small slot", (day, "--mode", mode, "--slot", "1000"))
            for mode in ("S3", "S4", "U2", "U3", "U4", "U5") if mode in built]
    for name, args in bad:
        done = run(*args)
        if done.returncode != 2:
            failures.append((name, f"exit {done.returncode}, not 2"))

    for case, reason in failures:
        print(f"bench_udp cli: FAIL {case}: {reason}")
    if failures:
        return 1
    print("bench_udp cli: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
