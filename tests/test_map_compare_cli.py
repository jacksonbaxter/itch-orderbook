#!/usr/bin/env python3
"""Black-box test of itch_map_compare on the committed fixtures.

Usage: test_map_compare_cli.py <itch_map_compare> <scratch dir> <oracle fixtures dir>
                               <trade fixtures dir> [with-mimalloc|without-mimalloc]

Every fixture runs on every arm of the fast book against the map book, on a fresh heap and
on an aged one, with the whole of both books compared after every message. Every fixture
also runs the node book of T-ARENA-1 on each allocator arm against the fast book, with the
same checks, and must end on the same book as the map book did. The counts
named here come from the fixture builders and the frame counts, not from the tool's own
output. A book that goes wrong is shown failing by the faults in scripts/faults/mapbook.json.
The mimalloc arm may say the build has no mimalloc only in a build CMake says has none, and
then the test prints that it skipped it; with no fifth argument it says so whenever it skips.
A failure prints `map compare cli: FAIL <case>: <reason>`.
"""

import platform
import struct
import subprocess
import sys
from pathlib import Path

SMALL = ("--max-orders", "4096", "--max-levels", "4096")
ARMS = (("hash", "linked"), ("hash", "flat"), ("paged", "linked"), ("paged", "flat"),
        ("identity", "flat"), ("chained", "flat"), ("paged", "flat-scan16"),
        ("paged", "flat-bisect"))
ARM_SMALL = SMALL + ("--max-pages", "8", "--index-bits", "16", "--max-slots", "8192")
AGED = ("--heap", "aged", "--seed", "7", "--warm-blocks", "4096", "--ring-blocks", "64")
# Each message the fixture builder marks as refused: 5 unknown, 1 on another locate, 2 too
# large, 2 duplicates, 1 bad side and 1 delete of an order already gone.
DEAD_REFERENCE_REFUSED = 12
ALLOCATORS = ("malloc-fresh", "malloc-aged", "mimalloc", "pmr-pool", "pmr-monotonic",
              "pool-index", "pool-pointer", "pool-fifo")
POOLS = ("pool-index", "pool-pointer", "pool-fifo")
NO_MIMALLOC = "this build has no mimalloc"


def frame_types(data):
    """The type of each frame of a BinaryFILE stream."""
    types = []
    at = 0
    while at + 2 <= len(data):
        (length,) = struct.unpack_from(">H", data, at)
        if length:
            types.append(chr(data[at + 2]))
        at += 2 + length
    return types


def main():
    tool, scratch, oracle_fixtures, trade_fixtures = sys.argv[1:5]
    mimalloc_built = {"with-mimalloc": True, "without-mimalloc": False}.get(
        sys.argv[5] if len(sys.argv) > 5 else "", None)
    skipped = set()
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    fixtures = sorted(Path(oracle_fixtures).glob("*.itch")) + [Path(trade_fixtures) / "day.itch"]
    failures = []

    def run(path, *options):
        return subprocess.run([tool, str(path), *options], capture_output=True, text=True,
                              check=False)

    for path in fixtures:
        types = frame_types(path.read_bytes())
        adds = sum(1 for kind in types if kind in "AFU")
        hashes = set()
        for heap in ((), AGED):
            for index, levels in ARMS:
                name = f"{path.name} {index} {levels} {'aged' if heap else 'fresh'}"
                done = run(path, *ARM_SMALL, "--index", index, "--levels", levels,
                           "--hash-every", "1", *heap)
                lines = done.stdout.splitlines()
                values = dict(line.split(" ", 1) for line in lines if " " in line)
                if done.returncode != 0:
                    failures.append(f"{name}: exit {done.returncode}")
                wanted = {
                    "frames": len(types), "messages_compared": len(types),
                    "status_checks": len(types), "top_checks": 2 * len(types),
                    "periodic_hash_checks": len(types), "final_hash_checks": 1,
                    "divergences": 0, "compare_index": index, "compare_levels": levels,
                    "compare_heap": "aged" if heap else "fresh", "end": "end_of_input",
                }
                if path.name == "dead_reference.itch":
                    wanted["refused_alike"] = DEAD_REFERENCE_REFUSED
                if heap:
                    wanted["ager_allocations"] = 4096 + adds
                for key, value in wanted.items():
                    if values.get(key) != str(value):
                        failures.append(f"{name}: {key} {values.get(key)}, wanted {value}")
                if "verdict MAP-BOOK PASS" not in lines:
                    failures.append(f"{name}: no PASS verdict")
                if any(line.startswith("MAP_DIVERGENCE") for line in lines):
                    failures.append(f"{name}: a divergence was printed")
                if not heap and "ager_allocations" in values:
                    failures.append(f"{name}: a fresh heap was aged")
                hashes.add(values.get("last_hash"))
        for allocator in ALLOCATORS:
            name = f"{path.name} node {allocator}"
            done = run(path, *SMALL, "--allocator", allocator, "--hash-every", "1")
            if done.returncode == 2 and allocator == "mimalloc" and NO_MIMALLOC in done.stderr:
                if mimalloc_built:
                    failures.append(f"{name}: the build has mimalloc, and the tool says not")
                skipped.add(allocator)
                continue
            lines = done.stdout.splitlines()
            values = dict(line.split(" ", 1) for line in lines if " " in line)
            if done.returncode != 0:
                failures.append(f"{name}: exit {done.returncode}")
            wanted = {
                "frames": len(types), "messages_compared": len(types),
                "status_checks": len(types), "top_checks": 2 * len(types),
                "periodic_hash_checks": len(types), "final_hash_checks": 1,
                "divergences": 0, "compare_allocator": allocator, "end": "end_of_input",
            }
            if path.name == "dead_reference.itch":
                wanted["refused_alike"] = DEAD_REFERENCE_REFUSED
            for key, value in wanted.items():
                if values.get(key) != str(value):
                    failures.append(f"{name}: {key} {values.get(key)}, wanted {value}")
            if "verdict NODE-BOOK PASS" not in lines:
                failures.append(f"{name}: no PASS verdict")
            if any(line.startswith(("NODE_DIVERGENCE", "MAP_DIVERGENCE")) for line in lines):
                failures.append(f"{name}: a divergence was printed")
            if ("node_pages" in values) != (allocator in POOLS):
                failures.append(f"{name}: node_pages {values.get('node_pages')}")
            if (allocator == "malloc-aged") != ("ager_allocations" in values):
                failures.append(f"{name}: ager_allocations {values.get('ager_allocations')}")
            hashes.add(values.get("last_hash"))
        if len(hashes) != 1:
            failures.append(f"{path.name}: the arms and heaps end on other books: {hashes}")

    # A stream that stops before S/C is not a whole day, even with no divergence.
    session = Path(oracle_fixtures) / "session.itch"
    cut = scratch / "cut.itch"
    data = session.read_bytes()
    at = 0
    for _ in range(100):
        (length,) = struct.unpack_from(">H", data, at)
        at += 2 + length
    cut.write_bytes(data[:at])
    done = run(cut, *SMALL)
    if done.returncode != 1 or "compare_incomplete the stream did not end cleanly on S/C" \
            not in done.stdout or "verdict MAP-BOOK FAIL" not in done.stdout:
        failures.append(f"a cut stream: exit {done.returncode}")

    for name, args in (("no file", []), ("no such file", [str(scratch / "no-such-file")]),
                       ("bad heap", [str(session), "--heap", "old"]),
                       ("bad allocator", [str(session), "--allocator", "pool"]),
                       ("bad pages", [str(session), "--allocator", "pool-index", "--pages", "4k"]),
                       ("an aged map heap with an allocator",
                        [str(session), "--allocator", "malloc-fresh", "--heap", "aged"]),
                       ("bad arm", [str(session), "--index", "tree"]),
                       ("bad number", [str(session), "--seed", "x"]),
                       ("unknown flag", [str(session), "--nonsense", "1"])):
        code = subprocess.run([tool, *args], capture_output=True, check=False).returncode
        if code != 2:
            failures.append(f"usage {name}: exit {code}, wanted 2")
    # A capacity of 0 or past 32 bits is bad usage, on every index: 0 orders gave the hash
    # indexes one slot and a shift of 64, and 2^32 wrapped to 0. 1 is a capacity.
    for flag, value in (("--max-orders", "0"), ("--max-orders", "4294967296"),
                        ("--max-levels", "0"), ("--max-levels", "4294967297")):
        for book in ([], ["--index", "hash"], ["--allocator", "pool-index"]):
            code = subprocess.run([tool, str(session), *book, flag, value], capture_output=True,
                                  check=False).returncode
            if code != 2:
                failures.append(f"capacity {flag} {value} {' '.join(book)}: exit {code}, "
                                "wanted 2")
    code = run(session, "--index", "hash", "--max-orders", "1", "--max-levels", "1").returncode
    if code != 1:
        failures.append(f"capacity of 1: exit {code}, wanted 1, the books refusing alike")

    # A pool whose pages were refused has no room, so the books part at the first add. Apple
    # silicon refuses every superpage (LC-57), so there an explicit request always is.
    if sys.platform == "darwin" and platform.machine() == "arm64":
        done = run(session, *SMALL, "--allocator", "pool-index", "--pages", "explicit")
        if done.returncode != 1 or "verdict NODE-BOOK FAIL" not in done.stdout or \
                "nothing mapped" not in done.stdout:
            failures.append(f"a pool with no pages: exit {done.returncode}")

    for allocator in sorted(skipped):
        print(f"map compare cli: skipped the {allocator} arm: {NO_MIMALLOC}")
    for failure in failures:
        print(f"map compare cli: FAIL {failure}")
    if failures:
        return 1
    print(f"map compare cli: ok, {len(fixtures)} fixtures on {len(ARMS)} arms and two heaps, "
          f"and the node book on {len(ALLOCATORS) - len(skipped)} allocator arms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
