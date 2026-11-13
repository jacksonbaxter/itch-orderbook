#!/usr/bin/env python3
"""Black-box test of itch_queue_jumps on streams built here.

Usage: test_queue_jumps_cli.py <itch_queue_jumps> <scratch dir>

The clean stream has one jump of the kind found on the real days: an order shown late, at
the open, below a reference the symbol had shown before it, executes while the earlier
shown order still rests. It also has a printable C above the bid it executes, as every one
in regular trading on the real days was. Every expected line was worked out by hand from the stream below.
Each later stream breaks one condition of --strict. A failure prints
`queue jumps cli: FAIL <case>: <reason>`.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gen_oracle_fixtures import OPEN_NS, Stream  # noqa: E402

LOCATE = 1
SYMBOL = "AAA"
PRICE = 100_000


def opening(stream):
    stream.system("O")
    stream.directory(LOCATE, SYMBOL)
    stream.trading(LOCATE, SYMBOL, "T")
    stream.system("S")


def opened(stream):
    stream.at(OPEN_NS)
    stream.system("Q")
    stream.cross(LOCATE, 0, PRICE, SYMBOL)


def closing(stream):
    stream.system("M")
    stream.system("E")
    stream.system("C")


def clean():
    """R10 shown before the open; R5, received before it, shown at the open; R5 executes."""
    s = Stream()
    opening(s)
    s.add(LOCATE, 10, "B", 200, PRICE, SYMBOL)
    s.execute(LOCATE, 10, 1)  # Before the open: outside the population, at the lowest.
    opened(s)
    s.add(LOCATE, 5, "B", 50, PRICE, SYMBOL)  # Late: 5 < 10.
    s.add(LOCATE, 20, "B", 30, PRICE, SYMBOL)
    s.execute(LOCATE, 5, 50)  # The jump: R10 is ahead of it in feed order.
    s.execute(LOCATE, 10, 199)  # The front, executed at a later nanosecond.
    s.replace(LOCATE, 20, 21, 30, PRICE)
    s.execute(LOCATE, 21, 30)
    s.add(LOCATE, 30, "B", 100, PRICE, SYMBOL)
    s.execute_price(LOCATE, 30, 40, PRICE + 100)  # The seller sold above the bid shown.
    closing(s)
    return s.data


def c_at_shown_price():
    s = Stream()
    opening(s)
    opened(s)
    s.add(LOCATE, 30, "B", 100, PRICE, SYMBOL)
    s.execute_price(LOCATE, 30, 40, PRICE)
    closing(s)
    return s.data


def c_resting_improved(behind):
    """A printable C below the bid it executes: the resting order sold for less than it showed.
    Behind, it executes R40 while R30 rests ahead of it."""
    s = Stream()
    opening(s)
    opened(s)
    s.add(LOCATE, 30, "B", 100, PRICE, SYMBOL)
    if behind:
        s.add(LOCATE, 40, "B", 100, PRICE, SYMBOL)
    s.execute_price(LOCATE, 40 if behind else 30, 40, PRICE - 100)
    closing(s)
    return s.data


def out_of_order_execution():
    """R30 then R40, and R40 executes first: behind in both orders."""
    s = Stream()
    opening(s)
    opened(s)
    s.add(LOCATE, 30, "B", 100, PRICE, SYMBOL)
    s.add(LOCATE, 40, "B", 100, PRICE, SYMBOL)
    s.execute(LOCATE, 40, 10)
    closing(s)
    return s.data


def late_replace():
    s = Stream()
    opening(s)
    opened(s)
    s.add(LOCATE, 30, "B", 100, PRICE, SYMBOL)
    s.add(LOCATE, 40, "B", 100, PRICE, SYMBOL)
    s.replace(LOCATE, 40, 35, 100, PRICE)
    closing(s)
    return s.data


def late_before_open():
    s = Stream()
    opening(s)
    s.add(LOCATE, 30, "B", 100, PRICE, SYMBOL)
    s.add(LOCATE, 25, "B", 100, PRICE, SYMBOL)
    opened(s)
    closing(s)
    return s.data


CLEAN_LINES = (
    "e_messages 4",
    "population 3",
    "at_fifo_front 2",
    "jumps 1",
    "at_lowest_reference 3",
    "population_not_at_lowest_reference 0",
    "outside_population 1",
    "outside_at_lowest_reference 1",
    "jumps_reference_below_every_order_ahead 1",
    "jumps_order_added_late 1",
    "jumps_front_added_before_open 1",
    "jumps_only_odd_lots_ahead 0",
    "jumps_front_executed_same_ns 0",
    "jumps_front_executed_later 1",
    "jumps_front_removed_first 0",
    "jumps_front_never_named 0",
    "jump_symbols 1",
    "jump_symbol AAA 1",
    "adds 4",
    "replaces 1",
    "late_adds 1",
    "late_adds_before_open 0",
    "late_add_symbols 1",
    "late_replaces 0",
    "c_printable 1",
    "c_printable_at_lowest_reference 1",
    "c_printable_aggressor_improved front 1 behind 0",
    "c_printable_at_shown_price front 0 behind 0",
    "c_printable_resting_improved front 0 behind 0",
    "strict pass",
)


def main():
    tool, scratch = sys.argv[1:3]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    failures = []

    def write(name, data):
        path = scratch / name
        path.write_bytes(bytes(data))
        return str(path)

    def run(*args):
        return subprocess.run([tool, *args], capture_output=True, text=True, check=False)

    details = scratch / "details.tsv"
    good = write("clean.itch", clean())
    done = run(good, "--strict", "--details", str(details))
    if done.returncode != 0:
        failures.append(("clean", f"exit {done.returncode}: {done.stdout}{done.stderr}"))
    lines = done.stdout.splitlines()
    for line in CLEAN_LINES:
        if line not in lines:
            failures.append(("clean", f"no line {line!r}"))
    rows = details.read_text().splitlines() if details.exists() else []
    if len(rows) != 2 or not rows[1].split("\t")[1:5] == ["AAA", "B", str(PRICE), "5"]:
        failures.append(("details", f"wanted a header and one row for reference 5: {rows}"))

    for name, data, wanted in (
            ("out of order", out_of_order_execution(), "population_not_at_lowest_reference 1"),
            ("late replace", late_replace(), "late_replaces 1"),
            ("late before open", late_before_open(), "late_adds_before_open 1"),
            ("c at shown price", c_at_shown_price(),
             "c_printable_at_shown_price front 1 behind 0"),
            ("c resting improved", c_resting_improved(False),
             "c_printable_resting_improved front 1 behind 0"),
            ("c resting improved behind", c_resting_improved(True),
             "c_printable_resting_improved front 0 behind 1")):
        path = write(name.replace(" ", "_") + ".itch", data)
        strict = run(path, "--strict")
        if strict.returncode != 1 or "strict FAIL" not in strict.stdout:
            failures.append((name, f"--strict exit {strict.returncode}, not 1"))
        if wanted not in strict.stdout.splitlines():
            failures.append((name, f"no line {wanted!r}"))
        loose = run(path)
        if loose.returncode != 0:
            failures.append((name, f"without --strict exit {loose.returncode}, not 0"))

    for name, args in (("no file", ()),
                       ("unknown flag", (good, "--fast")),
                       ("two files", (good, good)),
                       ("missing file", (str(scratch / "absent.itch"),)),
                       ("unwritable details", (good, "--details", str(scratch / "no" / "x")))):
        done = run(*args)
        if done.returncode != 2:
            failures.append((name, f"exit {done.returncode}, not 2"))

    # The day after a frame of a type the spec does not define, cut inside its last frame: the
    # replay would end it like a whole day, so the framing check refuses it before the run.
    cut = scratch / "cut.itch"
    cut.write_bytes(b"\x00\x03z\x00\x00" + Path(good).read_bytes()[:-1])
    done = run(str(cut), "--strict")
    if done.returncode != 1 or done.stdout or \
            "FAIL framing: truncated_payload at offset" not in done.stderr or \
            "skipped frames 1 (unknown_type 1, bad_length 0)" not in done.stderr:
        failures.append(("cut", f"exit {done.returncode}: {done.stdout}{done.stderr}"))

    for case, reason in failures:
        print(f"queue jumps cli: FAIL {case}: {reason}")
    if failures:
        return 1
    print("queue jumps cli: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
