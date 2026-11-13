#!/usr/bin/env python3
"""Black-box test of itch_mm on the committed oracle fixture.

Usage: test_mm_cli.py <itch_mm> <oracle fixtures dir>

session.itch is a seeded random session on symbols S1 to S6, in the first milliseconds after
the open. The maker quotes S1 under every run of the evaluation's matrix: Base, Q-off under
each rule, L-off, both off under each rule, and GLFT. Every check the tool prints must pass,
with fills and no allocation after construction; every figure the checks read must be
printed, as an integer; S + A + I must equal the total and the
ledger's total, and the spread capture its gross line less the fees, all as integers; the
mark-path hash must be the same in every run, while L-off changes S1's fills and Q-off S4's.
Bad usage, an unreadable file and a day with no fee schedule exit 2. A failure prints
`mm cli: FAIL <case>: <reason>`.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

CHECKS = ("allocations", "no refused message", "no refused delivery", "no overflow",
          "no fee error", "every fill in the window", "identity")
MODEL = ("--gamma", "0.1", "--sigma", "1", "--k", "1.5", "--a", "1")
SESSION = ("--start", "09:30:00", "--stop", "09:31:00", "--small", "--date", "20191230")
RUNS = (("as", "exact", False), ("as", "conservative", False), ("as", "proportional", False),
        ("as", "exact", True), ("as", "conservative", True), ("as", "proportional", True),
        ("glft", "exact", False))


def field(output, name):
    match = re.search(rf"^{name} (-?\d+|\S+)$", output, re.MULTILINE)
    return match.group(1) if match else None


def main():
    tool, fixtures = sys.argv[1:3]
    session = str(Path(fixtures) / "session.itch")
    failures = []

    def run(*args):
        return subprocess.run([tool, *args], capture_output=True, text=True, check=False)

    def maker(symbol, model, queue, latency_off):
        args = [session, "--symbol", symbol, "--model", model, "--queue", queue, *MODEL, *SESSION]
        return run(*args, *(["--latency-off"] if latency_off else []))

    hashes = set()
    fills = {}
    for model, queue, latency_off in RUNS:
        case = f"S1 {model} {queue}{' L-off' if latency_off else ''}"
        done = maker("S1", model, queue, latency_off)
        out = done.stdout
        if done.returncode != 0:
            failures.append((case, f"exit {done.returncode}: {out}{done.stderr}"))
            continue
        header = f"model {model} queue {queue} latency_off {1 if latency_off else 0} "
        if not out.startswith("mm symbol S1 ") or header not in out.splitlines()[0]:
            failures.append((case, f"the header does not name the run: {out.splitlines()[0]}"))
        for check in CHECKS:
            if f"check {check}: pass" not in out:
                failures.append((case, f"no passing line for {check}"))
        printed = {name: field(out, name) for name in (
            "gross_spread", "fees", "spread_capture", "adverse_selection", "inventory", "total",
            "ledger_total", "fills", "shares", "bought", "sold")}
        unread = [f"{name} {value!r}" for name, value in printed.items()
                  if value is None or not re.fullmatch(r"-?\d+", value)]
        if unread:
            failures.append((case, f"missing or not an integer: {', '.join(unread)}"))
            continue
        values = {name: int(value) for name, value in printed.items()}
        if values["fills"] < 5:
            failures.append((case, f"too few fills: {values['fills']}"))
        if values["spread_capture"] != values["gross_spread"] - values["fees"]:
            failures.append((case, "spread capture is not the gross line less the fees"))
        parts = values["spread_capture"] + values["adverse_selection"] + values["inventory"]
        if not parts == values["total"] == values["ledger_total"]:
            failures.append((case, f"S + A + I {parts}, total {values['total']}, "
                                   f"ledger {values['ledger_total']}"))
        if values["bought"] + values["sold"] != values["shares"]:
            failures.append((case, "bought and sold do not add up to the shares"))
        if field(out, "allocations_after_construction") != "0":
            failures.append((case, "allocations after construction"))
        if len(re.findall(r"^markout_ns \d+ adverse_selection -?\d+ inventory -?\d+$", out,
                          re.MULTILINE)) != 5:
            failures.append((case, "not five markout horizons"))
        hashes.add(field(out, "mark_hash"))
        fills[(model, queue, latency_off)] = field(out, "fills")
    if len(hashes) != 1:
        failures.append(("matrix", f"{len(hashes)} mark-path hashes, not one: {hashes}"))
    base, latency_off = fills.get(("as", "exact", False)), fills.get(("as", "exact", True))
    if base is None or latency_off is None:
        failures.append(("L-off", "a run gave no fill count to compare"))
    elif base == latency_off:
        failures.append(("L-off", f"zero latency changed no fill on S1: {base} fills both ways"))
    queues = {queue: field(maker("S4", "as", queue, False).stdout, "fills")
              for queue in ("exact", "conservative", "proportional")}
    if None in queues.values():
        failures.append(("Q-off", f"a run gave no fill count to compare: {queues}"))
    elif queues["exact"] in (queues["conservative"], queues["proportional"]):
        failures.append(("Q-off", f"the queue rule changed no fill on S4: {queues}"))

    base = (session, "--symbol", "S1", "--model", "as", *MODEL)
    for name, args in (("no model", (session, "--symbol", "S1", *MODEL, *SESSION)),
                       ("bad model", (*base[:4], "ou", *MODEL, *SESSION)),
                       ("no gamma", (session, "--symbol", "S1", "--model", "as", "--sigma", "1",
                                     "--k", "1.5", "--a", "1", *SESSION)),
                       ("zero sigma", (*base, "--sigma", "0", *SESSION)),
                       ("bad queue", (*base, "--queue", "fifo", *SESSION)),
                       ("stop before start", (*base, *SESSION, "--stop", "09:29:00")),
                       ("close before stop", (*base, *SESSION, "--close", "09:30:30")),
                       ("long symbol", (session, "--symbol", "ABCDEFGHI", "--model", "as",
                                        *MODEL, *SESSION)),
                       ("unknown flag", (*base, *SESSION, "--fast")),
                       ("no fee schedule", (*base, *SESSION, "--date", "20200102")),
                       ("missing file", (str(Path(fixtures) / "absent.itch"), *base[1:],
                                         *SESSION))):
        done = run(*args)
        if done.returncode != 2:
            failures.append((name, f"exit {done.returncode}, not 2"))

    # The day after a frame of a type the spec does not define, cut inside its last frame: the
    # replay would end it like a whole day, so the framing check refuses it before the run.
    with tempfile.TemporaryDirectory() as scratch:
        cut = Path(scratch) / "cut.itch"
        cut.write_bytes(b"\x00\x03z\x00\x00" + Path(session).read_bytes()[:-1])
        done = run(str(cut), *base[1:], *SESSION)
        if done.returncode != 1 or done.stdout or \
                "FAIL framing: truncated_payload at offset" not in done.stderr or \
                "skipped frames 1 (unknown_type 1, bad_length 0)" not in done.stderr:
            failures.append(("cut", f"exit {done.returncode}: {done.stdout}{done.stderr}"))

    for case, reason in failures:
        print(f"mm cli: FAIL {case}: {reason}")
    if failures:
        return 1
    print("mm cli: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
