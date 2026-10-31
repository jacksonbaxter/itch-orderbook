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

    def case(name, data, want_exit, *wanted_lines):
        path = scratch / f"{name}.bin"
        path.write_bytes(data)
        run = subprocess.run([census, str(path)], capture_output=True, text=True, check=False)
        lines = run.stdout.splitlines()
        if run.returncode != want_exit:
            failures.append(f"{name}: exit {run.returncode}, wanted {want_exit}: {run.stderr}")
        for line in wanted_lines:
            if line not in lines:
                failures.append(f"{name}: no line {line!r} in {lines}")

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

    missing = subprocess.run([census, str(scratch / "no-such-file")], capture_output=True,
                             check=False)
    if missing.returncode != 2:
        failures.append(f"missing file: exit {missing.returncode}, wanted 2")
    usage = subprocess.run([census], capture_output=True, check=False)
    if usage.returncode != 2:
        failures.append(f"no arguments: exit {usage.returncode}, wanted 2")

    for failure in failures:
        print(failure, file=sys.stderr)
    print(f"census cli: 13 cases, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
