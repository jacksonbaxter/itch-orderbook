#!/usr/bin/env python3
"""Black-box test of itch_census on hand-built streams.

Usage: test_census_cli.py <itch_census> <scratch dir> <fixtures.hpp>
"""

import struct
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gen_fuzz_corpus  # noqa: E402


def frame(payload):
    return struct.pack(">H", len(payload)) + payload


def patched(payload, *edits):
    """The payload with each (offset, bytes) written over it."""
    out = bytearray(payload)
    for offset, value in edits:
        out[offset:offset + len(value)] = value
    return bytes(out)


def on_locate(payload, locate, *edits):
    return frame(patched(payload, (1, struct.pack(">H", locate)), *edits))


def main():
    census, scratch, fixture_path = sys.argv[1:4]
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    fixtures = gen_fuzz_corpus.parse_fixtures(Path(fixture_path).read_text(encoding="utf-8"))
    system = frame(fixtures["kSystemEvent"])
    add = frame(fixtures["kAddOrder"])
    end = frame(fixtures["kEndOfMessages"])
    unknown = frame(b"z\x01\x02\x03")
    bad = frame(fixtures["kAddOrder"][:-1])

    failures = []

    def directory(locate, category, authenticity, lot):
        return on_locate(fixtures["kStockDirectory"], locate, (19, category),
                         (21, struct.pack(">I", lot)), (29, authenticity))

    def cross(locate, shares, price, cross_type):
        return on_locate(fixtures["kCrossTrade"], locate, (11, struct.pack(">Q", shares)),
                         (27, struct.pack(">I", price)), (39, cross_type))

    def order(name, locate):
        return on_locate(fixtures[name], locate)

    # Locates 5 and 7 are test symbols, and 6 is live with a directory message sent twice.
    omissions = b"".join([
        system,
        directory(5, b"M", b"T", 10),
        directory(6, b"Q", b"P", 100),
        directory(6, b"Q", b"P", 100),
        directory(7, b" ", b"T", 1),
        order("kAddOrder", 5),
        order("kAddOrderAttributed", 5),
        order("kAddOrder", 6),
        order("kOrderCancel", 5),
        cross(5, 0, 0, b"O"),
        cross(6, 0, 0, b"O"),
        cross(6, 0, 500, b"C"),
        cross(6, 0, 700, b"C"),
        cross(6, 100, 0, b"C"),
        cross(6, 100, 500, b"C"),
        frame(patched(fixtures["kSystemEvent"], (11, b"E"))),
        order("kOrderCancel", 6),
        order("kOrderDelete", 5),
        end,
    ])

    def case(name, data, want_exit, *wanted_lines, absent=()):
        path = scratch / f"{name}.bin"
        path.write_bytes(data)
        run = subprocess.run([census, str(path)], capture_output=True, text=True, check=False)
        lines = run.stdout.splitlines()
        if run.returncode != want_exit:
            failures.append(f"{name}: exit {run.returncode}, wanted {want_exit}: {run.stderr}")
        for line in wanted_lines:
            if line not in lines:
                failures.append(f"{name}: no line {line!r} in {lines}")
        for prefix in absent:
            if any(line.startswith(prefix) for line in lines):
                failures.append(f"{name}: a line starts {prefix!r} in {lines}")

    case("complete", system + add + add + end, 0,
         "messages 4", "type A 2", "type S 2", "unknown_type 0", "bad_length 0",
         "end end_of_input", f"consumed {len(system + add + add + end)}")
    case("ends_on_add", system + add, 1, "messages 2", "end end_of_input")
    case("ends_on_system_o", system, 1, "messages 1")
    case("stray_byte_after_end", system + end + b"\x00", 1, "end truncated_prefix")
    case("cut_inside_last_frame", (system + end)[:-1], 1, "end truncated_payload", "messages 1")
    case("unknown_type_counted", system + unknown + end, 0, "unknown_type 1", "type z 1")
    case("bad_length_is_not_the_last_message", system + end + bad, 1, "bad_length 1")
    case("bad_length_counted", system + bad + end, 0, "bad_length 1", "messages 3")
    case("terminator_after_end", system + end + b"\x00\x00", 0, "zero_length 1", "messages 2")
    case("control_byte_type", system + frame(b"\x01\x02") + end, 0, "type 0x01 1")
    case("empty", b"", 1, "messages 0")
    case("omissions", omissions, 0,
         "directory_repeats 1",
         "market_category M 1", "market_category Q 2", "market_category 0x20 1",
         "authenticity P 2", "authenticity T 2",
         "round_lot 1 1", "round_lot 10 1", "round_lot 100 2",
         "test_symbols 2", "test_symbols_with_orders 1",
         "test_type R 2", "test_type A 1", "test_type F 1", "test_type X 1", "test_type Q 1",
         "test_type D 1",
         "cross O count=2 zero_shares=2 zero_price=2 zero_both=2",
         "cross C count=4 zero_shares=2 zero_price=1 zero_both=0",
         "after_e_type X 1", "after_e_type D 1", "after_e_type S 1",
         absent=("after_e_type A", "after_e_type Q", "test_type S"))

    missing = subprocess.run([census, str(scratch / "no-such-file")], capture_output=True,
                             check=False)
    if missing.returncode != 2:
        failures.append(f"missing file: exit {missing.returncode}, wanted 2")
    usage = subprocess.run([census], capture_output=True, check=False)
    if usage.returncode != 2:
        failures.append(f"no arguments: exit {usage.returncode}, wanted 2")

    for failure in failures:
        print(failure, file=sys.stderr)
    print(f"census cli: 14 cases, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
