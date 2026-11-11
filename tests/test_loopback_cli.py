#!/usr/bin/env python3
"""Black-box test of itch_loopback, the UDP replay's functional check on loopback.

Usage: test_loopback_cli.py <itch_loopback> <scratch dir> <oracle fixtures> <trade fixtures>

Every fixture day passes whole, packed at the default 1,400 bytes, at the least cap a day
packs under, one message a packet, and paced; so does a slice. Each pass prints the line
that says this is no measurement, and no rate. The negative control loses one packet and
must fail, with the gap seen and the book's digests apart. A truncated day, a cap no
message fits and bad usage exit 2.

A failure prints `loopback cli: FAIL <case>: <reason>`.
"""

import subprocess
import sys
from pathlib import Path

NOT_A_MEASUREMENT = ("itch_loopback: a functional check on loopback, not a measurement. It "
                     "reports no rate or latency, and no figure it prints is a result.")
# Small books: the fixtures hold a few thousand orders.
SMALL = ["--max-orders", "65536", "--max-levels", "65536", "--max-pages", "64",
         "--max-slots", "65536"]


def fields(stdout):
    out = {}
    for line in stdout.splitlines():
        key, _, value = line.partition(" ")
        out[key] = value
    return out


def check_pass(name, done, failures):
    if done.returncode != 0:
        failures.append((name, f"exit {done.returncode}: {done.stdout[-2000:]}{done.stderr}"))
        return None
    lines = done.stdout.splitlines()
    if not lines or lines[0] != NOT_A_MEASUREMENT:
        failures.append((name, "the first line does not say this is no measurement"))
    rest = "\n".join(lines[1:]).lower()
    if any(word in rest for word in ("per second", "msg/s", "latency", "rate ")):
        failures.append((name, "the output names a rate or latency"))
    got = fields(done.stdout)
    if got.get("loopback_verdict") != "PASS":
        failures.append((name, f"verdict {got.get('loopback_verdict')}"))
    for path in ("file", "udp"):
        if got.get(f"loopback_{path}_checkpoint") != "reached":
            failures.append((name, f"the {path} path never reached its checkpoint"))
    for key in ("frames", "frame_hash", "checkpoint_hash", "end_hash"):
        if got.get(f"loopback_file_{key}") != got.get(f"loopback_udp_{key}"):
            failures.append((name, f"{key} differs and the verdict passed"))
    if got.get("loopback_lost") != "0" or got.get("loopback_gaps") != "0":
        failures.append((name, "loss was counted and the verdict passed"))
    if got.get("loopback_messages") != got.get("loopback_admitted"):
        failures.append((name, "not every message was admitted"))
    return got


def main():
    tool, scratch, oracle, trades = sys.argv[1:5]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    failures = []

    def run(*args):
        return subprocess.run([tool, *args, *SMALL], capture_output=True, text=True,
                              check=False, timeout=120)

    days = sorted(Path(oracle).glob("*.itch")) + sorted(Path(trades).glob("*.itch"))
    if len(days) < 8:
        failures.append(("fixtures", f"{len(days)} fixture days, not at least 8"))
    for day in days:
        for label, extra in (("default", ()),
                             ("least cap", ("--datagram", "72")),
                             ("one a packet", ("--max-messages", "1")),
                             ("paced", ("--rate", "2000000"))):
            got = check_pass(f"{day.name} {label}", run(str(day), *extra), failures)
            if got is not None and label == "one a packet":
                # One message a packet, plus a heartbeat a quiet second and the close.
                packets = int(got["loopback_packets"])
                messages = int(got["loopback_messages"])
                beats = int(got["loopback_heartbeats"])
                if packets != messages + beats + 1:
                    failures.append((f"{day.name} {label}",
                                     f"{packets} packets for {messages} messages"))

    session = str(Path(oracle) / "session.itch")
    sliced = check_pass("slice", run(session, "--first", "100", "--frames", "500"), failures)
    if sliced is not None and sliced.get("loopback_messages") != "500":
        failures.append(("slice", f"{sliced.get('loopback_messages')} messages, not 500"))
    if sliced is not None and sliced.get("loopback_next_expected") != "501":
        failures.append(("slice", f"next expected {sliced.get('loopback_next_expected')}"))

    lost = run(session, "--drop-packet", "5")
    got = fields(lost.stdout)
    if lost.returncode != 1 or got.get("loopback_verdict") != "FAIL":
        failures.append(("lost packet", f"exit {lost.returncode}, verdict "
                         f"{got.get('loopback_verdict')}, not 1 and FAIL"))
    if got.get("loopback_gaps") != "1" or got.get("loopback_lost", "0") == "0":
        failures.append(("lost packet", f"gaps {got.get('loopback_gaps')}, lost "
                         f"{got.get('loopback_lost')}"))
    if got.get("loopback_file_frame_hash") == got.get("loopback_udp_frame_hash"):
        failures.append(("lost packet", "the two paths' frames agree"))

    truncated = scratch / "truncated.itch"
    truncated.write_bytes(Path(session).read_bytes()[:-3])
    for name, args in (("truncated day", (str(truncated),)),
                       ("cap under every message", (session, "--datagram", "30")),
                       ("zero count limit", (session, "--max-messages", "0")),
                       ("no file", ("--rate", "10")),
                       ("missing file", (str(scratch / "absent.itch"),)),
                       ("unknown flag", (session, "--fast", "1")),
                       ("bad number", (session, "--rate", "fast")),
                       ("flag without value", (session, "--rate"))):
        done = run(*args)
        if done.returncode != 2:
            failures.append((name, f"exit {done.returncode}, not 2"))

    for case, reason in failures:
        print(f"loopback cli: FAIL {case}: {reason}")
    if failures:
        return 1
    print("loopback cli: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
