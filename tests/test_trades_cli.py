#!/usr/bin/env python3
"""Black-box test of itch_trades on the committed fixtures, and on each way to break them.

Usage: test_trades_cli.py <itch_trades> <scratch dir> <fixtures dir>

The expected lines were worked out by hand from scripts/gen_trade_fixtures.py, not copied
from the tool's output. Each case after the clean run breaks one thing in a copy of a
fixture and names the check that must fire. A failure prints
`trades cli: FAIL <case>: <reason>`.
"""

import json
import struct
import subprocess
import sys
from pathlib import Path

SMALL = ("--max-orders", "4096", "--max-levels", "4096")
# Every arm of the fast book the layout results publish, with the paged index's pool and
# reach small enough to fall back on the fixture, and the flat arena small enough to move
# blocks.
ARMS = (("hash", "linked"), ("hash", "flat"), ("paged", "linked"), ("paged", "flat"),
        ("identity", "flat"), ("chained", "flat"), ("paged", "flat-scan16"),
        ("paged", "flat-bisect"))
ARM_SMALL = ("--max-pages", "8", "--index-bits", "16", "--max-slots", "8192")
DATE = ("--date", "20200106")
PINNED_ROWS = ("--midas-rows", "4")

CLEAN_TYPES = {"A": 15, "B": 1, "C": 12, "D": 7, "E": 12, "F": 1, "H": 8, "P": 5, "Q": 13, "R": 6,
               "S": 6, "U": 1, "X": 2}

CLEAN_LINES = (
    "trades_midas_exchange Nasdaq",
    "messages_checked 89",
    "class E count=12 shares=1570 zero_share=0 test_count=1 test_shares=100",
    "class C_Y count=3 shares=160 zero_share=0 test_count=0 test_shares=0",
    "class C_N count=9 shares=1210 zero_share=0 test_count=0 test_shares=0",
    "class P count=5 shares=1429 zero_share=0 test_count=0 test_shares=0",
    "class Q_O count=6 shares=660 zero_share=2 test_count=1 test_shares=40",
    "class Q_C count=6 shares=660 zero_share=3 test_count=1 test_shares=0",
    "class Q_H count=1 shares=150 zero_share=0 test_count=0 test_shares=0",
    "class Q_I count=0 shares=0 zero_share=0 test_count=0 test_shares=0",
    # 17 orders: 8 run out on an execution, 1 on a cancel, 7 deleted, 1 replaced.
    "t1_orders_opened 17",
    "t1_orders_ran_out_on_execute 8",
    "t1_orders_ran_out_on_cancel 1",
    "t1_orders_deleted 7",
    "t1_orders_replaced 1",
    "t1_orders_live_end 0",
    # 4,700 added = 2,940 executed + 70 cancelled + 1,540 deleted + 150 replaced out.
    "t1_shares_added 4700",
    "t1_shares_executed 2940",
    "t1_shares_cancelled 70",
    "t1_shares_deleted 1540",
    "t1_shares_replaced_out 150",
    "t1_book_checked_empty 1",
    # 9 E with the market open and in T, one of them behind another order. Before the
    # open and after hours make 2, both at the best. Halted and not at the best makes 1.
    "t5_population 9",
    "t5_at_best 9",
    "t5_at_front 8",
    "t5_outside_at_best 2",
    "t5_outside market_open=0 state=T count=2",
    "t5_outside market_open=1 state=H count=1",
    "c_price printable=Y equal=2 different=1 equal_shares=110 different_shares=50",
    "c_price printable=N equal=1 different=8 equal_shares=150 different_shares=1060",
    "match_records 42",
    "match_shared_numbers 0",
    "cn_far_leg print=E count=1 shares=100",
    "cn_far_leg print=C_Y count=1 shares=60",
    "cn_far_leg print=P count=1 shares=100",
    "cn_cross_leg cross=Q_O count=2 shares=300 longest_lag_ns=0",
    "cn_cross_leg cross=Q_C count=2 shares=400 longest_lag_ns=0",
    "cn_cross_leg cross=Q_H count=2 shares=250 longest_lag_ns=6000000",
    "cn_cross_sides_filled 6",
    "cn_cross_sides_exact 1",
    "b_messages 1",
    "b_matched 1",
    "b_shares 100",
    # E 1,570 + C_Y 160 + P 429, less the 100 broken.
    "b_net_printed_shares 3059",
    "t2_file_rows 5",
    "t2_rows_compared 5",
    "t2_file_opening 620",
    "t2_file_closing 660",
    "t2_outside_file 150",
    "t3_window 0935_1600",
    "t3_odd_lot_columns compared",
    # BRVO alone. The directory says 10, so the P of 99 would not be odd. The SEC says 100.
    "t3_other_round_lots 1",
    "t3_round_lot symbol=BRVO lot=10 sec_lot=100 feed_below_sec=1/99 midas=1/99 "
    "feed_below_lot=0/0 "
    "feed_below_100=1/99 feed_below_10=0/0",
    "t3_sec_lot symbol=BRK.A lot=1 listed=0 compared=0",
    "t3_sec_lot symbol=SEB lot=1 listed=0 compared=0",
    "t3_sec_lot symbol=BH lot=10 listed=0 compared=0",
    "t3_rows 4",
    "t3_rows_in_file 7",
    "t3_second_rows 0",
    "t3_symbols_compared 4",
    # ALFA 790 + BRVO 499 + HLTD 1,200 + NONE 0, of which 330 + 99 + 1,000 hidden.
    "t3_midas_trade_shares 2489",
    "t3_midas_hidden_shares 1429",
    "t3_midas_trades 13",
    "t3_midas_hidden_trades 5",
    "t3_outside_symbols 1",
    "t3_outside_shares 200",
    "t3_outside_test_symbols 1",
    # One nanosecond apart at each bound, by construction of the fixture.
    "t3_edge_start last_before=34499999999999 first_inside=34500000000000",
    "t3_edge_end last_inside=57599999999999 first_after=57600000000000",
    "verdict T-TRADE-1 PASS violations=0",
    "verdict T-TRADE-2 PASS violations=0",
    "verdict T-TRADE-3 PASS violations=0",
    "verdict T-TRADE-5 PASS violations=0",
    "verdict PRINTS PASS violations=0",
)

# Worked out from LOT_PRINTS in the generator. Prints / shares below each lot.
LOT_LINES = (
    "t3_other_round_lots 5",
    "t3_round_lot symbol=BH lot=10 sec_lot=10 feed_below_sec=1/9 midas=1/9 feed_below_lot=1/9 "
    "feed_below_100=3/118 feed_below_10=1/9",
    "t3_round_lot symbol=BH.A lot=10 sec_lot=100 feed_below_sec=1/50 midas=1/50 "
    "feed_below_lot=0/0 "
    "feed_below_100=1/50 feed_below_10=0/0",
    "t3_round_lot symbol=SEB lot=1 sec_lot=1 feed_below_sec=0/0 midas=0/0 feed_below_lot=0/0 "
    "feed_below_100=4/114 feed_below_10=3/15",
    "t3_round_lot symbol=BRK.A lot=1 sec_lot=1 feed_below_sec=0/0 midas=0/0 feed_below_lot=0/0 "
    "feed_below_100=2/8 feed_below_10=2/8",
    "t3_round_lot symbol=MKL lot=10 sec_lot=100 feed_below_sec=3/118 midas=3/118 "
    "feed_below_lot=1/9 "
    "feed_below_100=3/118 feed_below_10=1/9",
    "t3_sec_lot symbol=BRK.A lot=1 listed=1 compared=1",
    "t3_sec_lot symbol=SEB lot=1 listed=1 compared=1",
    "t3_sec_lot symbol=BH lot=10 listed=1 compared=1",
)

T1, T2, T3, T5, PRINTS = 1, 2, 4, 8, 16
ALFA_LOCATE, BRVO_LOCATE = 1, 2


def frames_of(data):
    """The payloads of a BinaryFILE stream."""
    frames = []
    at = 0
    while at < len(data):
        length = struct.unpack_from(">H", data, at)[0]
        frames.append(bytearray(data[at + 2:at + 2 + length]))
        at += 2 + length
    return frames


def stream_of(frames):
    return b"".join(struct.pack(">H", len(f)) + bytes(f) for f in frames)


def reference(frame):
    return struct.unpack_from(">Q", frame, 11)[0]


def locate(frame):
    return struct.unpack_from(">H", frame, 1)[0]


def shares(frame):
    return struct.unpack_from(">I", frame, 19)[0]


def nth(frames, type_code, wanted=lambda frame: True, count=0):
    """The index of a frame of this type."""
    hits = [i for i, f in enumerate(frames) if chr(f[0]) == type_code and wanted(f)]
    return hits[count]


def order_message(frames, type_code, ref, count=0):
    return nth(frames, type_code, lambda f: reference(f) == ref, count)


def cross(frames, cross_type, symbol):
    field = symbol.ljust(8).encode()
    return nth(frames, "Q", lambda f: f[39] == ord(cross_type) and bytes(f[19:27]) == field)


def set_field(frame, offset, layout, value):
    struct.pack_into(layout, frame, offset, value)


def main():
    tool, scratch, fixtures = sys.argv[1:4]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    fixtures = Path(fixtures)
    day = (fixtures / "day.itch").read_bytes()
    cross_text = (fixtures / "day_cross.txt").read_bytes().decode("ascii")
    midas_text = (fixtures / "day_midas.csv").read_bytes().decode("ascii")
    failures = []

    def run(day_bytes=day, cross_file=cross_text, midas_file=midas_text, extra=DATE + PINNED_ROWS,
            tag="case"):
        path = scratch / f"{tag}.itch"
        path.write_bytes(day_bytes)
        cross_path = scratch / f"{tag}_cross.txt"
        cross_path.write_bytes(cross_file.encode("ascii"))
        midas_path = scratch / f"{tag}_midas.csv"
        midas_path.write_bytes(midas_file.encode("ascii"))
        command = [tool, str(path), "--crossstats", str(cross_path), "--midas", str(midas_path),
                   "--details", "2", *SMALL, *extra]
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def expect(name, done, bits, *checks):
        """The run failed with exactly these result bits and fired each named check."""
        wanted = 64 + bits
        if done.returncode != wanted:
            failures.append(f"{name}: exit {done.returncode}, wanted {wanted}")
        for check in checks:
            if not any(line.startswith(f"violation {check} ") for line in done.stdout.splitlines()):
                failures.append(f"{name}: no violation line for {check}")

    def broken_frames(name, mutate, bits, *checks):
        frames = frames_of(day)
        mutate(frames)
        expect(name, run(stream_of(frames)), bits, *checks)

    def broken_cross(name, old, new, bits, *checks):
        if cross_text.count(old) != 1:
            failures.append(f"{name}: the cross fixture does not hold {old!r} exactly once")
        expect(name, run(cross_file=cross_text.replace(old, new)), bits, *checks)

    def broken_midas(name, old, new, bits, *checks):
        if midas_text.count(old) != 1:
            failures.append(f"{name}: the MIDAS fixture does not hold {old!r} exactly once")
        expect(name, run(midas_file=midas_text.replace(old, new)), bits, *checks)

    # The clean day.
    done = run()
    lines = done.stdout.splitlines()
    if done.returncode != 0:
        failures.append(f"clean: exit {done.returncode}")
    for line in CLEAN_LINES:
        if line not in lines:
            failures.append(f"clean: no line {line!r}")
    if any(line.startswith("violation ") for line in lines):
        failures.append("clean: a violation was printed")
    for name in ("broken_trade match=1060 class=E symbol=ZTST shares=100",):
        if not any(line.startswith(name) for line in lines):
            failures.append(f"clean: no line starting {name!r}")

    # Every arm prints the same lines as the default arm, apart from the lines naming it
    # and the time it took.
    def comparable(output):
        return [line for line in output.splitlines()
                if not line.startswith(("trades_index ", "trades_levels ", "elapsed_seconds "))]

    for index, levels in ARMS:
        arm = run(extra=DATE + PINNED_ROWS + ARM_SMALL + ("--index", index, "--levels", levels),
                  tag=f"arm_{index}_{levels}")
        arm_lines = arm.stdout.splitlines()
        if arm.returncode != 0:
            failures.append(f"arm {index} {levels}: exit {arm.returncode}")
        if f"trades_index {index}" not in arm_lines or f"trades_levels {levels}" not in arm_lines:
            failures.append(f"arm {index} {levels}: the arm is not named")
        if comparable(arm.stdout) != comparable(done.stdout):
            failures.append(f"arm {index} {levels}: prints other lines than the default arm")
    # The arm really runs: a flat arena too small for the day refuses adds, which the
    # linked arm does not have and would not refuse. Both ways round, so that neither arm
    # can pass for the other, whichever is the default.
    tight = subprocess.run([tool, str(fixtures / "day.itch"), *SMALL, "--index", "hash",
                            "--levels", "flat", "--max-slots", "8"],
                           capture_output=True, text=True, check=False)
    if tight.returncode == 0 or "capacity_exhausted" not in tight.stdout:
        failures.append(f"flat capacity: exit {tight.returncode}, no add refused")
    loose = subprocess.run([tool, str(fixtures / "day.itch"), *SMALL, "--index", "hash",
                            "--levels", "linked", "--max-slots", "8"],
                           capture_output=True, text=True, check=False)
    if loose.returncode != 0 or "capacity_exhausted" in loose.stdout:
        failures.append(f"linked capacity: exit {loose.returncode}, an add refused")
    for name, value in (("--index", "hashed"), ("--levels", "tree"), ("--max-pages", "x")):
        code = subprocess.run([tool, str(fixtures / "day.itch"), *SMALL, name, value],
                              capture_output=True, check=False).returncode
        if code != 2:
            failures.append(f"bad arm {name} {value}: exit {code}, wanted 2")
    # A capacity of 0 or past 32 bits is bad usage, on every index: 0 orders gave the hash
    # indexes one slot and a shift of 64, and 2^32 wrapped to 0.
    for flag, value in (("--max-orders", "0"), ("--max-orders", "4294967296"),
                        ("--max-levels", "0"), ("--max-levels", "4294967297")):
        for index in ("hash", "paged"):
            code = subprocess.run([tool, str(fixtures / "day.itch"), "--index", index, flag,
                                   value], capture_output=True, check=False).returncode
            if code != 2:
                failures.append(f"capacity {flag} {value} --index {index}: exit {code}, "
                                "wanted 2")

    # The report reads what the tool really prints, so the two cannot drift apart.
    (scratch / "report.log").write_text(done.stdout, encoding="ascii")

    def report_on(entry):
        (scratch / "report_manifest.json").write_text(json.dumps({
            "days": {"01062020": entry},
            "references": {"midas_2019q4": {"nasdaq_rows": {"20200106": 4}}},
        }), encoding="ascii")
        return subprocess.run(
            [sys.executable, "-B",
             str(Path(__file__).resolve().parent.parent / "scripts/trades_report.py"),
             "--day", "01062020", "--log", str(scratch / "report.log"),
             "--manifest", str(scratch / "report_manifest.json"),
             "--out", str(scratch / "report.md")],
            capture_output=True, text=True, check=False)

    # The manifest pins the fixture day's true length, which the log's bytes has to match.
    entry = {"name": "day.itch", "unpacked_sha256": "fixture", "unpacked_bytes": len(day),
             "messages": 89, "per_type_counts": CLEAN_TYPES}
    report = report_on(entry)
    if report.returncode != 0:
        failures.append(f"report: exit {report.returncode}: {report.stdout}{report.stderr}")
    # A manifest that pins no length for the day is refused, not passed.
    report = report_on({key: value for key, value in entry.items() if key != "unpacked_bytes"})
    if report.returncode != 2 or "the manifest pins no unpacked_bytes" not in report.stderr:
        failures.append(f"report without unpacked_bytes: exit {report.returncode}: {report.stderr.strip()};"
                        " wanted exit 2 refusing a manifest that pins no unpacked_bytes")

    # What the tool says when it was not given a reference.
    bare = subprocess.run([tool, str(fixtures / "day.itch"), *SMALL], capture_output=True,
                          text=True, check=False)
    if bare.returncode != 0 or "verdict T-TRADE-2 NOT_AVAILABLE violations=0" not in bare.stdout \
            or "verdict T-TRADE-3 NOT_RUN violations=0" not in bare.stdout:
        failures.append(f"no references: exit {bare.returncode}")

    # A symbol file names every listing.
    symbols = scratch / "symbols.txt"
    run(extra=DATE + PINNED_ROWS + ("--symbols", str(symbols)))
    text = symbols.read_text(encoding="ascii")
    if "symbol ZTST locate=5 test=1" not in text or "broken=100" not in text:
        failures.append("symbols: ZTST's line is wrong")
    # A symbol file that cannot be written fails the run, before the day is replayed.
    unwritable = run(extra=DATE + PINNED_ROWS + ("--symbols", str(scratch / "no-such-dir/s.txt")))
    if unwritable.returncode != 2 or "cannot write" not in unwritable.stderr \
            or "messages_checked" in unwritable.stdout:
        failures.append(f"symbols unwritable: exit {unwritable.returncode}, wanted 2 before the "
                        "replay")

    # Bad usage and unreadable files.
    for name, args, wanted in (
            ("no file", [], 2),
            ("no such file", ["no-such-file"], 2),
            ("reference with no date", [str(fixtures / "day.itch"), "--crossstats", "x"], 2),
            ("unknown flag", [str(fixtures / "day.itch"), "--nonsense", "1"], 2),
            ("no such cross file", [str(fixtures / "day.itch"), "--crossstats", "no-such-file",
                                    *DATE, *SMALL], 2),
            ("no such MIDAS file", [str(fixtures / "day.itch"), "--midas", "no-such-file",
                                    *DATE, *SMALL], 2)):
        code = subprocess.run([tool, *args], capture_output=True, check=False).returncode
        if code != wanted:
            failures.append(f"{name}: exit {code}, wanted {wanted}")

    # A reference file that does not parse has its own exit status and names its error.
    done = run(cross_file="Date,Symbol\n")
    if done.returncode != 3 or "reference_error crossstats bad_header line=1" not in done.stdout:
        failures.append(f"bad cross file: exit {done.returncode}")
    done = run(midas_file=midas_text.replace("0.499,0.4", "0.4995,0.4"))
    if done.returncode != 3 or "reference_error midas not_whole_shares line=5" not in done.stdout:
        failures.append(f"bad MIDAS file: exit {done.returncode}")
    done = run(midas_file=midas_text, extra=("--date", "20200108"))
    if done.returncode != 3 or "reference_error midas no_rows line=0" not in done.stdout:
        failures.append(f"a date with no MIDAS rows: exit {done.returncode}")

    # --exchange names the venue's rows in MIDAS: a BX day reads Boston's rows, and Boston's
    # rows are not read as Nasdaq's.
    boston = midas_text.replace(",Nasdaq,", ",Boston,")
    done = run(midas_file=boston, extra=DATE + PINNED_ROWS + ("--exchange", "Boston"))
    if done.returncode != 0 or "trades_midas_exchange Boston" not in done.stdout.splitlines():
        failures.append(f"--exchange Boston: exit {done.returncode}")
    done = run(midas_file=boston)
    if done.returncode != 3 or "reference_error midas no_rows line=0" not in done.stdout:
        failures.append(f"Boston's rows read as Nasdaq's: exit {done.returncode}")

    # E: each way a print can fail to hit a resting order.
    def e_of(frames):
        return frames[order_message(frames, "E", 5)]  # 200 shares on ALFA at 09:40.

    broken_frames("E on a dead order", lambda f: set_field(e_of(f), 11, ">Q", 999_999),
                  T1 + T3 + PRINTS, "e_not_live")
    broken_frames("E on another locate", lambda f: set_field(e_of(f), 1, ">H", BRVO_LOCATE),
                  T1 + T3 + PRINTS, "e_wrong_locate")
    broken_frames("E over the remaining shares", lambda f: set_field(e_of(f), 19, ">I", 201),
                  T1 + T3 + PRINTS, "e_over_remaining")
    broken_frames("E of no shares", lambda f: set_field(e_of(f), 19, ">I", 0), T1 + T3 + PRINTS,
                  "e_zero_shares", "book_not_empty")

    # C, the same, and a printable flag the spec does not list.
    def c_of(frames):
        return frames[order_message(frames, "C", 2)]  # 50 shares on ALFA at 09:41.

    broken_frames("C on a dead order", lambda f: set_field(c_of(f), 11, ">Q", 999_999),
                  T1 + T3 + PRINTS, "c_not_live")
    broken_frames("C on another locate", lambda f: set_field(c_of(f), 1, ">H", BRVO_LOCATE),
                  T1 + T3 + PRINTS, "c_wrong_locate")
    broken_frames("C over the remaining shares", lambda f: set_field(c_of(f), 19, ">I", 201),
                  T1 + T3 + PRINTS, "c_over_remaining")
    broken_frames("C of no shares", lambda f: set_field(c_of(f), 19, ">I", 0), T1 + T3 + PRINTS,
                  "c_zero_shares")
    broken_frames("C with a bad printable flag", lambda f: set_field(c_of(f), 31, "c", b"?"),
                  T3 + PRINTS, "c_bad_printable")

    # X, D, U and A, and a lost or doubled deduction.
    def x_of(frames):
        return frames[order_message(frames, "X", 4)]

    broken_frames("X on a dead order", lambda f: set_field(x_of(f), 11, ">Q", 999_999), T1,
                  "order_refused")
    broken_frames("X over the remaining shares", lambda f: set_field(x_of(f), 19, ">I", 181), T1,
                  "order_refused")
    broken_frames("D on a dead order",
                  lambda f: set_field(f[order_message(f, "D", 3)], 11, ">Q", 999_999), T1,
                  "order_refused")

    def u_of(frames):
        return frames[nth(frames, "U")]

    # The rest of each of these orders' lives fail as well, hence the wider exit status.
    broken_frames("U of a dead order", lambda f: set_field(u_of(f), 11, ">Q", 999_999),
                  T1 + T3 + PRINTS, "order_refused", "e_not_live")
    broken_frames("U onto a live reference", lambda f: set_field(u_of(f), 19, ">Q", 5),
                  T1 + T3 + T5 + PRINTS, "order_refused", "e_not_live")
    broken_frames("An add of a live reference",
                  lambda f: set_field(f[order_message(f, "A", 2)], 11, ">Q", 1), T1 + T3 + PRINTS,
                  "add_refused", "c_not_live")
    broken_frames("An add on the wrong symbol",
                  lambda f: set_field(f[order_message(f, "A", 2)], 24, "4s", b"BRVO"), T1,
                  "add_symbol_mismatch")

    def drop(frames, type_code, ref):
        del frames[order_message(frames, type_code, ref)]

    broken_frames("A lost X that would have ended an order", lambda f: drop(f, "X", 8), T1,
                  "book_not_empty", "ledger_not_empty")
    broken_frames("A lost A", lambda f: drop(f, "A", 7), PRINTS, "c_not_live")

    def duplicate(frames, type_code, ref):
        at = order_message(frames, type_code, ref)
        frames.insert(at, bytearray(frames[at]))

    broken_frames("A doubled E", lambda f: duplicate(f, "E", 6), T3 + PRINTS, "match_shared",
                  "t3_trades", "t3_trade_shares")

    # The end of the stream.
    frames = frames_of(day)
    del frames[-1]
    expect("No S/C", run(stream_of(frames)), T1, "stream_incomplete")
    expect("A truncated file", run(day[:-5]), T1, "stream_incomplete")

    # T-TRADE-5: the halt lost, so a resting E below the best is judged.
    def no_halt(frames):
        del frames[nth(frames, "H", lambda f: f[19] == ord("H"))]

    broken_frames("E below the best in T", no_halt, T5, "e_not_at_best")

    # P.
    def p_of(frames):
        return frames[nth(frames, "P")]

    broken_frames("P with a reference", lambda f: set_field(p_of(f), 11, ">Q", 5), PRINTS,
                  "p_reference_nonzero")
    broken_frames("P on side S", lambda f: set_field(p_of(f), 19, "c", b"S"), PRINTS,
                  "p_side_not_b")
    broken_frames("P naming another symbol", lambda f: set_field(p_of(f), 24, "4s", b"BRVO"),
                  PRINTS, "p_symbol_mismatch")

    # Q, each attribution.
    broken_frames("Q naming another symbol",
                  lambda f: set_field(f[cross(f, "O", "BRVO")], 19, "4s", b"ALFA"), PRINTS,
                  "q_symbol_mismatch")
    broken_frames("Q on another locate",
                  lambda f: set_field(f[cross(f, "O", "BRVO")], 1, ">H", ALFA_LOCATE),
                  T2 + PRINTS, "t2_opening", "q_symbol_mismatch")
    broken_frames("Q of an unknown type",
                  lambda f: set_field(f[cross(f, "O", "BRVO")], 39, "c", b"Z"),
                  T2 + PRINTS, "q_unknown_cross_type", "t2_opening")
    broken_frames("An opening Q typed as closing",
                  lambda f: set_field(f[cross(f, "O", "BRVO")], 39, "c", b"C"), T2, "t2_opening",
                  "t2_closing")
    broken_frames("A closing Q typed as a halt cross",
                  lambda f: set_field(f[cross(f, "C", "BRVO")], 39, "c", b"H"), T2, "t2_closing")
    broken_frames("A Q typed as intraday",
                  lambda f: set_field(f[cross(f, "C", "BRVO")], 39, "c", b"I"), T2, "t2_closing",
                  "t2_intraday")
    broken_frames("One more Q share",
                  lambda f: set_field(f[cross(f, "O", "BRVO")], 11, ">Q", 301), T2, "t2_opening",
                  "t2_outside_file")

    # C_N against its cross.
    broken_frames("Q below its C_N",
                  lambda f: set_field(f[cross(f, "O", "ALFA")], 11, ">Q", 100), T2 + PRINTS,
                  "cn_over_cross")

    def c_n_of(frames, match):
        return frames[nth(frames, "C", lambda f: struct.unpack_from(">Q", f, 23)[0] == match)]

    broken_frames("C_N at a price the cross did not print",
                  lambda f: set_field(c_n_of(f, 1002), 32, ">I", 999_400), PRINTS, "cn_price")

    def no_cross(frames):
        del frames[cross(frames, "O", "ALFA")]

    broken_frames("C_N before any cross", no_cross, T2 + PRINTS, "cn_no_cross")

    def far_leg(frames):
        return frames[nth(frames, "C", lambda f: reference(f) == 7)]

    broken_frames("A far leg with its own price",
                  lambda f: set_field(far_leg(f), 32, ">I", 1_000_700), PRINTS, "match_leg_differs")
    broken_frames("A far leg with its own number",
                  lambda f: set_field(far_leg(f), 23, ">Q", 7777), PRINTS, "cn_price")

    # Order 2, the far leg of the E on order 6, moved to the same side as that E. Order 2
    # is then the best bid, so the E below it fails T-TRADE-5 too.
    broken_frames("A far leg on the side of its print",
                  lambda f: set_field(f[order_message(f, "A", 2)], 19, "c", b"B"), T5 + PRINTS,
                  "match_leg_differs", "e_not_at_best")

    # Match numbers.
    broken_frames("Two prints on one match number",
                  lambda f: set_field(e_of(f), 23, ">Q", 1010), PRINTS, "match_shared")

    # B.
    def b_of(frames):
        return frames[nth(frames, "B")]

    broken_frames("B of no print", lambda f: set_field(b_of(f), 11, ">Q", 424_242), PRINTS,
                  "b_unknown_match")
    broken_frames("B on another locate", lambda f: set_field(b_of(f), 1, ">H", BRVO_LOCATE), PRINTS,
                  "b_wrong_locate")

    def twice(frames):
        at = nth(frames, "B")
        frames.insert(at, bytearray(frames[at]))

    broken_frames("B twice", twice, PRINTS, "b_broken_twice")

    # T-TRADE-2, the file's side.
    row = "01/06/2020,BRVO  ,Q            ,         300,         250,            0,"
    broken_cross("One share changed in Opening", row, row.replace("300", "301"), T2, "t2_opening",
                 "t2_total_row", "t2_outside_file")
    broken_cross("One share changed in Closing", row, row.replace("250", "251"), T2, "t2_closing",
                 "t2_total_row", "t2_outside_file")
    broken_cross("Shares in Intraday", row, row[:-2] + "5,", T2, "t2_intraday", "t2_total_row")
    broken_cross("The TOTAL row changed", "         620,         660",
                 "         621,         660", T2, "t2_total_row")
    broken_cross("A row removed", row + "\r\n", "", T2, "t2_outside_file", "t2_total_row")
    broken_cross("Opening and Closing swapped on a row", row,
                 row.replace("300,         250", "250,         300"), T2, "t2_opening", "t2_closing")
    broken_cross("A row for a symbol the day never listed", row, row.replace("BRVO", "XXXX"), T2,
                 "t2_row_not_in_directory")
    expect("Another day's file", run(cross_file=cross_text.replace("01/06/2020", "01/07/2020")), T2,
           "t2_date")

    # T-TRADE-3, the file's side.
    alfa = "8.0,5.0,4.0,3.0,8.0,4.7,0.7900000000000001,0.46,0.19,0.33,0.79"

    broken_midas("One share of TradeVol", "0.7900000000000001", "0.791", T3, "t3_trade_shares")
    broken_midas("One share of HiddenVol", "0.19,0.33,0.79", "0.19,0.331,0.79", T3,
                 "t3_hidden_shares")
    broken_midas("One Trades", alfa, alfa.replace("8.0,5.0,4.0,3.0", "9.0,5.0,4.0,3.0"), T3,
                 "t3_trades")
    broken_midas("One Hidden", alfa, alfa.replace("8.0,5.0,4.0,3.0", "8.0,5.0,4.0,4.0"), T3,
                 "t3_hidden_trades")
    broken_midas("One OddLots", alfa, alfa.replace("8.0,5.0,4.0,3.0", "8.0,5.0,3.0,3.0"), T3,
                 "t3_odd_lots")
    broken_midas("One share of OddLotVol", "0.46,0.19,0.33", "0.46,0.191,0.33", T3,
                 "t3_odd_lot_shares")
    broken_midas("A row for a symbol the day never listed", ",BRVO,Nasdaq", ",XXXX,Nasdaq", T3,
                 "t3_row_not_in_directory")
    lines = midas_text.splitlines(keepends=True)
    missing = "".join(line for line in lines if ",HLTD,Nasdaq" not in line)
    expect("A row removed", run(midas_file=missing), T3, "t3_row_count")

    # The SEC's round-lot rule, and the date it stops at.
    lots_midas = (fixtures / "lots_midas.csv").read_bytes().decode("ascii")

    def lots_run(date, midas=lots_midas):
        midas_path = scratch / "lots_midas.csv"
        midas_path.write_bytes(midas.encode("ascii"))
        return subprocess.run(
            [tool, str(fixtures / "lots.itch"), "--date", date, "--midas", str(midas_path),
             "--midas-rows", "6", "--details", "6", *SMALL],
            capture_output=True, text=True, check=False)

    def lots_case(name, done, want_exit, *wanted):
        lines = done.stdout.splitlines()
        if done.returncode != want_exit:
            failures.append(f"{name}: exit {done.returncode}, wanted {want_exit}")
        for line in wanted:
            if line not in lines:
                failures.append(f"{name}: no line {line!r}")
        return lines

    for date in ("20200106", "20251031"):
        lots_case(f"lots {date}", lots_run(date), 0, "t3_odd_lot_columns compared", *LOT_LINES,
                  "verdict T-TRADE-3 PASS violations=0")

    # From the date the rule ends every odd-lot value differs and nothing is compared, and no
    # line says what the old rule would have given.
    lines = lots_case("lots 20251103", lots_run("20251103"), 0,
                      "t3_odd_lot_columns not_compared_rule_ended",
                      "t3_round_lot symbol=BH lot=10 sec_lot=none feed_below_sec=none midas=7/7 "
                      "feed_below_lot=1/9 feed_below_100=3/118 feed_below_10=1/9",
                      "verdict T-TRADE-3 PASS violations=0")
    fit = [line for line in lines if line.startswith("t3_fit window=0935_1600 ")]
    if len(fit) != 1 or "equal_odd_lots=0 equal_odd_shares=0" not in fit[0]:
        failures.append(f"lots 20251103: odd lots were still fitted: {fit}")
    # The rest of the check goes on after that date.
    expect("One share of TradeVol after the rule ended",
           lots_run("20251103", lots_midas.replace("20251103,Stock,BH,Nasdaq,1.0,1.0,1.0,1.0,5.0,"
                                                   "3.0,0.0,7.0,3.0,3.0,1.0,0.118,",
                                                   "20251103,Stock,BH,Nasdaq,1.0,1.0,1.0,1.0,5.0,"
                                                   "3.0,0.0,7.0,3.0,3.0,1.0,0.119,")),
           T3, "t3_trade_shares")

    # Trades, LitTrades, then OddLots: one odd lot too many on each of the SEC's exceptions.
    for symbol, trades, odd_lots in (("BH", "3.0", "1.0"), ("SEB", "4.0", "0.0"),
                                     ("BRK.A", "2.0", "0.0")):
        row = f"20200106,Stock,{symbol},Nasdaq,1.0,1.0,1.0,1.0,5.0,{trades},0.0,"
        more = f"{float(odd_lots) + 1}"
        expect(f"One OddLots on {symbol}",
               lots_run("20200106", lots_midas.replace(row + odd_lots, row + more)),
               T3, "t3_odd_lots")

    for failure in failures:
        print(f"trades cli: FAIL {failure}")
    if failures:
        return 1
    print("trades cli: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
