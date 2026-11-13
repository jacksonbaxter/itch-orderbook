#!/usr/bin/env python3
"""Black-box test of itch_oracle on the committed fixtures.

Usage: test_oracle_cli.py <itch_oracle> <scratch dir> <fixtures dir> <itch_alloc>
                          [with-mimalloc|without-mimalloc]

Each expected line was worked out by hand from scripts/gen_oracle_fixtures.py, not
copied from the tool's output. Every arm the layout results publish runs through the
oracle, the look-ahead and itch_alloc (r8-1), and the fast book's allocation check runs on
lifecycle.itch, whose executions and cancels empty orders, as well as session.itch (r8-2).
itch_alloc's mimalloc arm may say the build has no mimalloc only in a build CMake says has
none, and then the test prints that it skipped it; with no fifth argument it says so
whenever it skips (r8-11). A failure prints `oracle cli: FAIL <case>: <reason>`.
"""

import resource
import signal
import subprocess
import sys
from pathlib import Path

SMALL = ("--max-orders", "4096", "--max-levels", "4096")
# Every arm of the fast book the layout results publish, with the paged index's pool and
# reach small enough to fall back on the fixtures, and the flat arena small enough to move
# blocks.
ARMS = (("hash", "linked"), ("hash", "flat"), ("paged", "linked"), ("paged", "flat"),
        ("identity", "flat"), ("chained", "flat"), ("paged", "flat-scan16"),
        ("paged", "flat-bisect"))
NO_MIMALLOC = "this build has no mimalloc"
ARM_SMALL = SMALL + ("--max-pages", "8", "--index-bits", "16", "--max-slots", "8192")
TYPES = "SRHYLVWKJhAFECXDUPQBINO"


def alloc_arm_failures(alloc_tool, session, fixtures, mimalloc_built=None, skipped=None):
    """T-ARENA-1's node book on each allocator arm through itch_alloc --allocator.

    A pool arm passes on no allocation at all. A heap arm's verdict is REPORT, with its
    calls: malloc one for each node made, std::pmr's resources fewer, a chunk at a time, and
    mimalloc none the counter sees, counted by the book instead. The aged heap's ballast is
    taken off, so that malloc-aged counts what malloc-fresh does. mimalloc_built is what
    CMake says of the build, or None if it was not told; an arm skipped goes into skipped.
    """
    failures = []
    made = set()
    for arm in ("malloc-fresh", "malloc-aged", "mimalloc", "pmr-pool", "pmr-monotonic",
                "pool-index", "pool-pointer", "pool-fifo"):
        done = subprocess.run([alloc_tool, str(session), *SMALL, "--allocator", arm],
                              capture_output=True, text=True, check=False)
        if done.returncode == 2 and arm == "mimalloc" and NO_MIMALLOC in done.stderr:
            if mimalloc_built:
                failures.append(f"alloc {arm}: the build has mimalloc, and the tool says not")
            if skipped is not None:
                skipped.add(arm)
            continue
        values = dict(line.split(" ", 1) for line in done.stdout.splitlines() if " " in line)
        counted = int(values.get("node_allocations", "-1"))
        book = int(values.get("node_book_allocations", "-1"))
        nodes = int(values.get("node_nodes_made", "-1"))
        made.add(nodes)
        pool = arm.startswith("pool-")
        verdict = "PASS" if pool else "REPORT"
        if done.returncode != 0 or \
                not values.get("verdict", "").startswith(f"T-ARENA-1-ALLOC {verdict} "):
            failures.append(f"alloc {arm}: exit {done.returncode}, verdict "
                            f"{values.get('verdict')}, wanted {verdict}")
        if values.get("node_refused") != "0" or values.get("node_allocator") != arm:
            failures.append(f"alloc {arm}: refused {values.get('node_refused')}")
        if pool and (counted != 0 or "node_pages" not in values):
            failures.append(f"alloc {arm}: {counted} allocations on a pool")
        if arm in ("malloc-fresh", "malloc-aged") and book != nodes:
            failures.append(f"alloc {arm}: {book} calls for {nodes} nodes")
        if arm == "malloc-aged" and counted != book + int(values.get("node_ager_allocations", 0)):
            failures.append(f"alloc {arm}: the ager's calls were not taken off")
        if arm.startswith("pmr-") and not 0 < book < nodes:
            failures.append(f"alloc {arm}: {book} calls for {nodes} nodes")
        if arm == "mimalloc" and (counted != 0 or book != nodes):
            failures.append(f"alloc {arm}: counted {counted}, book {book}, nodes {nodes}")
    if len(made) != 1 or min(made) <= 0:
        failures.append(f"alloc: the arms made other numbers of nodes: {made}")
    # A refused message fails a pool arm as it fails the fast book.
    done = subprocess.run([alloc_tool, str(fixtures / "dead_reference.itch"), *SMALL,
                           "--allocator", "pool-index"], capture_output=True, text=True,
                          check=False)
    if done.returncode != 1:
        failures.append(f"alloc pool-index: a refused message exited {done.returncode}, wanted 1")
    for name, options in (("bad allocator", ("--allocator", "arena")),
                          ("bad pages", ("--allocator", "pool-index", "--pages", "64k")),
                          ("look-ahead on the node book",
                           ("--allocator", "pool-index", "--prefetch", "16"))):
        done = subprocess.run([alloc_tool, str(session), *options], capture_output=True,
                              text=True, check=False)
        if done.returncode != 2:
            failures.append(f"alloc usage {name}: exit {done.returncode}, wanted 2")
    return failures


def main():
    tool, scratch, fixtures, alloc_tool = sys.argv[1:5]
    mimalloc_built = {"with-mimalloc": True, "without-mimalloc": False}.get(
        sys.argv[5] if len(sys.argv) > 5 else "", None)
    skipped = set()
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
    #
    # The slots: 13 orders taken (10 adds, 3 U) and 13 given back, 3 partial deductions,
    # 2 appends behind an order (2 behind 1, 5 behind 1), and 15 uses in the 13 unlinks: each
    # order itself, and 1's newer and 2's older. 46 order slots. 51 level writes, and 11 levels
    # opened and closed, 73 level slots. Each of the 23 one-order messages names a reference,
    # and each U two, 29 index checks.
    lifecycle = fixtures / "lifecycle.itch"
    lines = case("lifecycle", lifecycle, SMALL + ("--exhaustive",), 0,
                 "frames 51", "messages_diffed 51", "status_checks 51", "touched_checks 17",
                 "empty_touched_checks 11", "bbo_checks 102", "write_set_checks 51",
                 "write_set_violations 0", "order_slot_checks 46", "level_slot_checks 73",
                 "slot_violations 0", "index_checks 29",
                 "final_hash_checks 1", "exhaustive_checks 51",
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
    # A pool of no slots, or one past what a slot index can name, is refused before any run:
    # 2^32 would otherwise be taken as 0 and 2^32 + 1 as 1.
    for name, options in (("no file", None), ("bad option", ("--nonsense", "1")),
                          ("bad number", ("--hash-every", "x")),
                          ("no orders", ("--index", "hash", "--max-orders", "0")),
                          ("2^32 orders", ("--index", "hash", "--max-orders", "4294967296")),
                          ("2^32 + 1 orders", ("--max-orders", "4294967297")),
                          ("no levels", ("--max-levels", "0")),
                          ("2^32 levels", ("--max-levels", "4294967296"))):
        if options is None:
            done = subprocess.run([tool], capture_output=True, text=True, check=False)
        else:
            done = run(lifecycle, *options)
        if done.returncode != 2:
            failures.append(f"usage {name}: exit {done.returncode}")
    if run(scratch / "no-such-file").returncode != 2:
        failures.append("missing file: not exit 2")

    # An observations file that cannot be written in full fails the run: here the file may
    # grow to 64 bytes, and crossed.itch logs 9 lines.
    def small_files():
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (64, 64))

    short = scratch / "short_observations.txt"
    done = subprocess.run([tool, str(crossed), *SMALL, "--observations", str(short)],
                          capture_output=True, text=True, check=False, preexec_fn=small_files)
    if done.returncode != 2 or "cannot finish writing" not in done.stderr:
        failures.append(f"short observations: exit {done.returncode}, wanted 2: {done.stderr}")

    # Each arm on the fixtures, with the same verdicts as the default arm. The whole-book
    # hash is a canonical walk, so equal books hash equal and every arm ends on the same
    # last_hash. What T(m) and the BBO compare cover comes from the reference, so their
    # counts agree as well. What moves depends on the arm.
    shared = ("touched_checks", "empty_touched_checks", "bbo_checks", "write_set_writes",
              "order_slot_checks", "level_slot_checks", "slot_violations", "index_checks",
              "periodic_hash_checks", "exhaustive_checks", "last_hash", "live_orders_end")
    for name, path, options, want_exit in (
            ("lifecycle", lifecycle, ("--exhaustive",), 0),
            ("session", session, ("--hash-every", "100", "--exhaustive"), 0),
            ("crossed", crossed, ("--exhaustive",), 1),
            ("leftover", fixtures / "leftover.itch", ("--exhaustive",), 3),
            ("dead reference", fixtures / "dead_reference.itch", ("--exhaustive",), 1)):
        seen = {}
        for index, levels in ARMS:
            arm = ("--index", index, "--levels", levels)
            lines = case(f"{name} {index} {levels}", path, ARM_SMALL + options + arm, want_exit,
                         f"oracle_index {index}", f"oracle_levels {levels}", "divergences 0",
                         "verdict T-ORACLE-1 PASS", "write_set_violations 0")
            values = dict(line.split(" ", 1) for line in lines if " " in line)
            seen[(index, levels)] = tuple(values.get(key) for key in shared)
            if index == "paged" and "oracle_max_pages 8" not in lines:
                failures.append(f"{name} {index} {levels}: the page pool is not reported")
            if levels != "linked" and "oracle_max_slots 8192" not in lines:
                failures.append(f"{name} {index} {levels}: the arena is not reported")
        if len(set(seen.values())) != 1:
            failures.append(f"{name}: the arms disagree on {shared}: {seen}")

    # The fast book looking ahead (itch/lookahead.hpp) changes nothing the oracle can see:
    # on every arm, at the shortest and longest look-ahead, the same verdicts and the same
    # shared counts as without it, the whole book compared after every message. A distance
    # the arm cannot stage is bad usage.
    for name, path, options, want_exit in (
            ("lifecycle", lifecycle, ("--exhaustive",), 0),
            ("session", session, ("--hash-every", "100", "--exhaustive"), 0),
            ("crossed", crossed, ("--exhaustive",), 1)):
        for index, levels in ARMS:
            arm = ("--index", index, "--levels", levels)
            plain = case(f"{name} {index} {levels} no look-ahead", path,
                         ARM_SMALL + options + arm, want_exit, "oracle_prefetch 0")
            want = {key: value for key, value in (line.split(" ", 1) for line in plain
                                                 if " " in line) if key in shared}
            for distance in ("4", "64"):
                lines = case(f"{name} {index} {levels} look-ahead {distance}", path,
                             ARM_SMALL + options + arm + ("--prefetch", distance), want_exit,
                             f"oracle_prefetch {distance}", "divergences 0",
                             "verdict T-ORACLE-1 PASS", "write_set_violations 0")
                got = {key: value for key, value in (line.split(" ", 1) for line in lines
                                                    if " " in line) if key in shared}
                if got != want:
                    failures.append(f"{name} {index} {levels} look-ahead {distance}: "
                                    f"{got} against {want}")
    for distance, index in (("3", "paged"), ("2", "hash"), ("65", "hash"), ("4294967300", "paged")):
        done = run(lifecycle, "--index", index, "--prefetch", distance)
        if done.returncode != 2 or "--prefetch" not in done.stderr:
            failures.append(f"usage look-ahead {distance} on {index}: exit {done.returncode}")

    # lifecycle's 13 new references are 1 to 11, 0 and 2^64 - 1. The last is past any
    # reach, so it falls back and is counted. With a reach of 2^0 only reference 0 indexes
    # directly, the other 12 fall back, and the diff is still clean.
    case("lifecycle paged reach", lifecycle, ARM_SMALL + ("--index", "paged"), 0,
         "index_fallback_inserts 1", "index_fallback_live_end 0", "verdict T-ORACLE-1 PASS")
    case("lifecycle paged no reach", lifecycle,
         SMALL + ("--index", "paged", "--max-pages", "8", "--index-bits", "0", "--exhaustive"), 0,
         "index_fallback_inserts 12", "index_fallback_live_end 0", "index_peak_live_pages 1",
         "verdict T-ORACLE-1 PASS")
    # session is deep enough that its flat arrays move to bigger blocks.
    lines = case("session flat moves", session, ARM_SMALL + ("--levels", "flat"), 0,
                 "verdict T-ORACLE-1 PASS")
    values = dict(line.split(" ", 1) for line in lines if " " in line)
    if int(values.get("level_relocations", "0")) == 0 or int(values.get("moved_checks", "0")) == 0:
        failures.append("session flat moves: no block moved, or no moved level was compared")
    # Each side's first block and each move to a bigger one is checked against the arena.
    if int(values.get("arena_checks", "0")) <= int(values.get("level_relocations", "0")):
        failures.append(f"session flat moves: {values.get('arena_checks')} arena checks for "
                        f"{values.get('level_relocations')} relocations")
    # Every flat arm reads the arena's size, so every one reports it.
    for levels in ("flat-scan16", "flat-bisect"):
        case(f"session {levels}", session, ARM_SMALL + ("--levels", levels), 0,
             "oracle_max_slots 8192", "verdict T-ORACLE-1 PASS", "slot_violations 0")
    # An arena too small for the session refuses, the reference does not, and the diff
    # says so at once.
    lines = case("flat capacity", session,
                 SMALL + ("--levels", "flat", "--max-slots", "64"), 1, "verdict T-ORACLE-1 FAIL")
    if not any("kind=status" in line and "capacity_exhausted" in line for line in lines):
        failures.append("flat capacity: no status divergence")
    for name, options in (("bad index", ("--index", "tree")), ("bad levels", ("--levels", "map"))):
        if run(lifecycle, *options).returncode != 2:
            failures.append(f"usage {name}: not exit 2")

    # T-ALLOC-1 on two fixtures: the fast pass is clean and the control pass is seen. session
    # leaves deep orders resting; lifecycle empties orders by E, C and X, the path where an
    # execution or cancel removes the order (r8-2).
    alloc_files = (("session", session), ("lifecycle", lifecycle))
    for name, path in alloc_files:
        done = subprocess.run([alloc_tool, str(path), *SMALL], capture_output=True, text=True,
                              check=False)
        if done.returncode != 0 or "fast_allocations 0" not in done.stdout.splitlines():
            failures.append(f"alloc {name}: exit {done.returncode}: {done.stdout}")
        if "control_allocations 0" in done.stdout.splitlines():
            failures.append(f"alloc {name}: the control book made no allocation")
        if "verdict T-ALLOC-1 PASS" not in done.stdout:
            failures.append(f"alloc {name}: no pass verdict")
    done = subprocess.run([alloc_tool, str(fixtures / "dead_reference.itch"), *SMALL],
                          capture_output=True, text=True, check=False)
    if done.returncode != 1:
        failures.append(f"alloc: a refused message exited {done.returncode}, wanted 1")
    for (name, path), (index, levels) in ((f, a) for f in alloc_files for a in ARMS):
        done = subprocess.run([alloc_tool, str(path), *ARM_SMALL, "--index", index, "--levels",
                               levels], capture_output=True, text=True, check=False)
        lines = done.stdout.splitlines()
        for line in (f"book_index {index}", f"book_levels {levels}", "fast_allocations 0",
                     "fast_refused 0"):
            if line not in lines:
                failures.append(f"alloc {name} {index} {levels}: no line {line!r}")
        if done.returncode != 0 or "control_allocations 0" in lines:
            failures.append(f"alloc {name} {index} {levels}: exit {done.returncode}")
    for (name, path), (index, levels) in ((f, a) for f in alloc_files for a in ARMS):
        done = subprocess.run([alloc_tool, str(path), *ARM_SMALL, "--index", index, "--levels",
                               levels, "--prefetch", "16"], capture_output=True, text=True,
                              check=False)
        lines = done.stdout.splitlines()
        for line in ("book_prefetch 16", "fast_allocations 0", "fast_refused 0"):
            if line not in lines:
                failures.append(f"alloc {name} {index} {levels} look-ahead 16: no line {line!r}")
        if done.returncode != 0 or not any(x.startswith("verdict T-ALLOC-1 PASS") for x in lines):
            failures.append(f"alloc {name} {index} {levels} look-ahead 16: exit {done.returncode}")
    done = subprocess.run([alloc_tool, str(session), "--prefetch", "3"], capture_output=True,
                          text=True, check=False)
    if done.returncode != 2:
        failures.append(f"alloc: look-ahead 3 on the paged index exited {done.returncode}, wanted 2")
    done = subprocess.run([alloc_tool, str(session), "--levels", "map"], capture_output=True,
                          text=True, check=False)
    if done.returncode != 2:
        failures.append(f"alloc: a bad arm exited {done.returncode}, wanted 2")
    failures += alloc_arm_failures(alloc_tool, session, fixtures, mimalloc_built, skipped)

    for arm in sorted(skipped):
        print(f"oracle cli: skipped the alloc {arm} arm: {NO_MIMALLOC}")
    for failure in failures:
        print(f"oracle cli: FAIL {failure}")
    if failures:
        return 1
    print("oracle cli: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
