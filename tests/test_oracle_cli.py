#!/usr/bin/env python3
"""Black-box test of itch_oracle on the committed fixtures.

Usage: test_oracle_cli.py <itch_oracle> <scratch dir> <fixtures dir> <itch_alloc>

Each expected line was worked out by hand from scripts/gen_oracle_fixtures.py, not
copied from the tool's output. A failure prints `oracle cli: FAIL <case>: <reason>`.
"""

import subprocess
import sys
from pathlib import Path

SMALL = ("--max-orders", "4096", "--max-levels", "4096")
TYPES = "SRHYLVWKJhAFECXDUPQBINO"


def main():
    tool, scratch, fixtures, alloc_tool = sys.argv[1:5]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    fixtures = Path(fixtures)
    failures = []

    def run(path, *options):
        return subprocess.run([tool, str(path), *options], capture_output=True, text=True,
                              check=False)

    def case(name, path, options, want_exit, *wanted):
        done = run(path, *options)
        lines = done.stdout.splitlines()
        if done.returncode != want_exit:
            failures.append(f"{name}: exit {done.returncode}, wanted {want_exit}")
        for line in wanted:
            if line not in lines:
                failures.append(f"{name}: no line {line!r}")
        return lines

    # Every message type, every order path, a clean diff and an empty book. 28 touched
    # levels: 9 A, 1 F, 2 E, 1 C, 1 X, 9 D, and 5 from the 3 U, one of which keeps its
    # price. 11 of them are levels that message emptied.
    lifecycle = fixtures / "lifecycle.itch"
    lines = case("lifecycle", lifecycle, SMALL + ("--exhaustive",), 0,
                 "frames 51", "messages_diffed 51", "status_checks 51", "touched_checks 17",
                 "empty_touched_checks 11", "bbo_checks 102", "write_set_checks 51",
                 "write_set_violations 0", "final_hash_checks 1", "exhaustive_checks 51",
                 "live_orders_end 0", "live_levels_end 0", "divergences 0",
                 "t_inv1_violations 0", "t_inv2_crossed 0", "t_inv2_locked 0",
                 "operational_halt_messages 1", "verdict T-ORACLE-1 PASS",
                 "verdict T-INV-1 PASS violations=0", "verdict T-INV-2 PASS crossed=0 locked=0")
    seen = {line.split()[1] for line in lines if line.startswith("type ")}
    if seen != set(TYPES):
        failures.append(f"lifecycle: types {sorted(seen)}, wanted all 23")

    # A session with deep orders left resting. The hash runs every 100 messages, and the
    # same stream hashes the same on a second run.
    session = fixtures / "session.itch"
    first = case("session", session, SMALL + ("--hash-every", "100", "--exhaustive"), 0,
                 "verdict T-ORACLE-1 PASS", "verdict T-INV-1 PASS violations=0",
                 "verdict T-INV-2 PASS crossed=0 locked=0", "divergences 0")
    values = dict(line.split(" ", 1) for line in first if " " in line)
    diffed = int(values["messages_diffed"])
    if int(values["periodic_hash_checks"]) != diffed // 100 or diffed < 3000:
        failures.append(f"session: {values['periodic_hash_checks']} hashes in {diffed} messages")
    if int(values["exhaustive_checks"]) != diffed:
        failures.append(f"session: exhaustive ran {values['exhaustive_checks']} times")
    if int(values["live_orders_end"]) < 12:
        failures.append("session: the deep orders are gone")
    again = case("session again", session, SMALL + ("--hash-every", "100"), 0)
    if [line for line in first if line.startswith("last_hash")] != \
            [line for line in again if line.startswith("last_hash")]:
        failures.append("session: last_hash differs between runs")

    # Each observation below is marked in the generator.
    crossed = fixtures / "crossed.itch"
    log = scratch / "observations.txt"
    case("crossed", crossed, SMALL + ("--exhaustive", "--observations", str(log)), 1,
         "t_inv2_crossed 7", "t_inv2_locked 2", "t_inv2_deepest_crossing 10000",
         "t_inv2_crossed_symbols 3", "t_inv2_locked_symbols 1",
         "t_inv2_not_in_t crossed=3 locked=1", "t_inv2_no_action_yet crossed=1 locked=0",
         "t_inv2_in_reopening_window crossed=2 locked=1",
         "t_inv2_in_t_outside_window crossed=2 locked=0",
         "t_inv2_market_open crossed=6 locked=2", "t_inv2_market_open_and_t crossed=4 locked=1",
         "t_inv2_clock_0930_1600 crossed=6 locked=2",
         "t_inv2_in_t_within_50ms_of_resumption crossed=3 locked=1",
         "t_inv2_in_t_later_than_50ms crossed=0 locked=0",
         "t_inv2_in_t_never_resumed crossed=1 locked=0", "t_inv2_reopening_windows 2",
         "t_inv2_by_state - crossed=1 locked=0", "t_inv2_by_state H crossed=2 locked=0",
         "t_inv2_by_state P crossed=0 locked=1", "t_inv2_by_state T crossed=4 locked=1",
         "t_inv2_cause type=A cause=not_t crossed=3 locked=1",
         "t_inv2_cause type=A cause=window crossed=1 locked=0",
         "t_inv2_cause type=A cause=continuous crossed=1 locked=0",
         "t_inv2_cause type=Q cause=continuous crossed=1 locked=0",
         "t_inv2_cause type=H cause=window crossed=1 locked=1",
         "t_inv2_cause type=A cause=clock crossed=4 locked=1",
         "t_inv2_tally type=H scope=all checks=7 two_sided=2 crossed=1 locked=1",
         "t_inv2_tally type=A scope=open_t checks=3 two_sided=2 crossed=2 locked=0",
         "t_inv2_tally type=A scope=all checks=10 two_sided=6 crossed=5 locked=1",
         "verdict T-ORACLE-1 PASS", "verdict T-INV-1 PASS violations=0",
         "verdict T-INV-2 FAIL crossed=7 locked=2",
         # TRD's cross, and HLT's after a cross message ended its leftover.
         "t_inv3_new crossed=2 locked=0", "t_inv3_leftover crossed=2 locked=1",
         "verdict T-INV-3 FAIL violations=2")
    observed = log.read_text(encoding="utf-8").splitlines()
    if len(observed) != 9:
        failures.append(f"crossed: {len(observed)} observations logged, wanted 9")
    wanted = [("crossed", "symbol=HLT", "state=H", "market_open=1", "in_window=0", "depth=10000"),
              ("crossed", "symbol=HLT", "state=H", "time=10:00:00", "type=A"),
              ("crossed", "symbol=HLT", "state=T", "in_window=1", "type=H"),
              ("crossed", "symbol=HLT", "state=T", "in_window=1", "type=A"),
              ("crossed", "symbol=HLT", "state=T", "in_window=0", "type=Q"),
              ("locked", "symbol=PSD", "state=P", "depth=0"),
              ("locked", "symbol=PSD", "state=T", "in_window=1"),
              ("crossed", "symbol=TRD", "state=T", "in_window=0", "depth=100"),
              ("crossed", "symbol=NOH", "state=-", "market_open=0", "time=16:00:00")]
    for index, parts in enumerate(wanted[:len(observed)]):
        for part in parts:
            if part not in observed[index]:
                failures.append(f"crossed: observation {index} has no {part!r}: {observed[index]}")

    # T-INV-2 fails and T-INV-3 passes, which is the one way to exit 3. Crossed 5: PRE
    # before the open, LFT in its halt, at the resumption and five hours on, and CLR in its
    # halt. Locked 4: LCK in its halt, at the resumption and after it, and PSD in its pause.
    # Two of each are in the population, and all four have a leftover in force. 4
    # resumptions, of which 2 find the book crossed or locked, since CLR and PSD cleared
    # first. LFT's leftover runs from 09:30:00.000005 to 14:30:00.000002, and nothing ends
    # it but the book: that span is 18,000 s less 3,000 ns.
    allowed = case("leftover", fixtures / "leftover.itch", SMALL + ("--exhaustive",), 3,
                   "t_inv2_crossed 5", "t_inv2_locked 4", "t_inv2_reopening_windows 4",
                   "t_inv3_new crossed=0 locked=0", "t_inv3_leftover crossed=2 locked=2",
                   "t_inv3_leftovers_opened 2", "t_inv3_ended_by_book 2",
                   "t_inv3_ended_by_cross 0", "t_inv3_longest_leftover_ns 17999999997000",
                   "t_inv3_still_in_force 0",
                   "verdict T-ORACLE-1 PASS", "verdict T-INV-1 PASS violations=0",
                   "verdict T-INV-2 FAIL crossed=5 locked=4",
                   "verdict T-INV-3 PASS violations=0")
    for symbol in ("LFT", "LCK", "CLR", "PSD", "PRE"):
        if any(line.startswith("t_inv3_violation") and f"symbol={symbol} " in line
               for line in allowed):
            failures.append(f"leftover: violation on {symbol}")

    # Each new cross in normal trading, located by hand from the generator: 7 in all, 2 of
    # them on BYCROSS, and 4 allowed observations at the resumptions before them.
    bad = case("new cross", fixtures / "new_cross.itch", SMALL + ("--exhaustive",), 1,
               "t_inv3_new crossed=4 locked=3", "t_inv3_leftover crossed=2 locked=2",
               "t_inv3_leftovers_opened 4", "t_inv3_ended_by_book 2", "t_inv3_ended_by_cross 2",
               "verdict T-ORACLE-1 PASS", "verdict T-INV-1 PASS violations=0",
               "verdict T-INV-3 FAIL violations=7")
    wanted_new = [("ENDED", "crossed", "type=A", "depth=100"),
                  ("FRESH", "crossed", "type=A", "depth=200"),
                  ("BYCROSS", "crossed", "type=Q", "depth=400"),
                  ("BYCROSS", "crossed", "type=A", "depth=400"),
                  ("ENDLK", "locked", "type=A", "depth=0"),
                  ("FRSHLK", "locked", "type=A", "depth=0"),
                  ("BYCRSLK", "locked", "type=Q", "depth=0")]
    reported = [line for line in bad if line.startswith("t_inv3_violation")]
    for symbol, kind, *parts in wanted_new:
        hits = [line for line in reported if f"symbol={symbol} " in line and f"kind={kind} " in line
                and all(part in line for part in parts) and " time=09:30:" in line
                and " message=" in line]
        if not hits:
            failures.append(f"new cross: no violation on {symbol} {parts}")
    if len(reported) != len(wanted_new):
        failures.append(f"new cross: {len(reported)} violations reported, wanted {len(wanted_new)}")

    # Both books refuse each of these, so they stay equal and only T-INV-1 fails.
    case("dead reference", fixtures / "dead_reference.itch", SMALL + ("--exhaustive",), 1,
         "messages_diffed 26", "t_inv1_violations 12", "t_inv1_by_status unknown_order 6",
         "t_inv1_by_status locate_mismatch 1", "t_inv1_by_status over_deduction 2",
         "t_inv1_by_status duplicate_reference 2", "t_inv1_by_status bad_side 1",
         "t_inv1_violation message=9 type=E locate=1 reference=999 status=unknown_order",
         "divergences 0", "verdict T-ORACLE-1 PASS", "verdict T-INV-1 FAIL violations=12",
         "verdict T-INV-2 PASS crossed=0 locked=0")

    # A truncated download is a well-formed prefix: the diff is clean and it must fail.
    data = lifecycle.read_bytes()
    prefix = scratch / "prefix.itch"
    prefix.write_bytes(data[:-14])
    case("ends before S/C", prefix, SMALL, 1, "verdict T-ORACLE-1 FAIL",
         "oracle_incomplete the stream did not end cleanly on S/C")
    prefix.write_bytes(data[:-1])
    case("cut inside a frame", prefix, SMALL, 1, "end truncated_payload",
         "verdict T-ORACLE-1 FAIL")
    prefix.write_bytes(data[:-14] + b"\x00\x02z\x00" + data[-14:])
    case("unknown type", prefix, SMALL, 1, "malformed_frames 1", "verdict T-ORACLE-1 FAIL",
         "oracle_incomplete diffed 51 of 52 frames")

    # Full pools refuse, the reference does not, and the diff says so at once.
    lines = case("capacity", lifecycle, ("--max-orders", "2", "--max-levels", "2"), 1,
                 "verdict T-ORACLE-1 FAIL")
    if not any("kind=status" in line and "capacity_exhausted" in line for line in lines):
        failures.append("capacity: no status divergence")

    # Bad usage is not a pass.
    for name, options in (("no file", None), ("bad option", ("--nonsense", "1")),
                          ("bad number", ("--hash-every", "x"))):
        if options is None:
            done = subprocess.run([tool], capture_output=True, text=True, check=False)
        else:
            done = run(lifecycle, *options)
        if done.returncode != 2:
            failures.append(f"usage {name}: exit {done.returncode}")
    if run(scratch / "no-such-file").returncode != 2:
        failures.append("missing file: not exit 2")

    # T-ALLOC-1 on a fixture: the fast pass is clean and the control pass is seen.
    done = subprocess.run([alloc_tool, str(session), *SMALL], capture_output=True, text=True,
                          check=False)
    if done.returncode != 0 or "fast_allocations 0" not in done.stdout.splitlines():
        failures.append(f"alloc: exit {done.returncode}: {done.stdout}")
    if "control_allocations 0" in done.stdout.splitlines():
        failures.append("alloc: the control book made no allocation")
    if "verdict T-ALLOC-1 PASS" not in done.stdout:
        failures.append("alloc: no pass verdict")
    done = subprocess.run([alloc_tool, str(fixtures / "dead_reference.itch"), *SMALL],
                          capture_output=True, text=True, check=False)
    if done.returncode != 1:
        failures.append(f"alloc: a refused message exited {done.returncode}, wanted 1")

    for failure in failures:
        print(f"oracle cli: FAIL {failure}")
    if failures:
        return 1
    print("oracle cli: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
