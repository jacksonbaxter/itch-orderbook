#!/usr/bin/env python3
"""The one skip mechanism of the GoogleTest entries (tests/CMakeLists.txt, support/gtest_main.cpp).

Usage: test_skips_cli.py <skip_probe> <build dir> <udp modes built, comma-separated>

Three parts. The probe's tests pass, skip and fail on purpose, and run here a filter at a
time through the main every GoogleTest binary has: under ctest's ITCH_CTEST_SKIPS, a binary
that passed with a skip for a stated reason exits 77; a failure fails beside a skip; a skip
with no reason fails; a filter that matches nothing fails; run by hand, a skip passes. Then
the build's ctest entries, read from its CTestTestfile.cmake files (`ctest --show-only` leaves out
the command of a binary not built, as in a fault's build of a few targets): no entry has a skip regex,
which would take a failing binary that printed a skip as Skipped; every GoogleTest entry has
SKIP_RETURN_CODE 77 and ITCH_CTEST_SKIPS; each test that can skip has an entry of its own
that its binary's main entry leaves out. Last, the receive mode entries skip exactly when
CMake built none of the modes they check. The harness-skip faults in scripts/faults/harness.json
and two in udp.json break each.
A failure prints `skips cli: FAIL <case>: <reason>`.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_CODE = 77
REASON = "the probe's reason"
# (filter, ITCH_CTEST_SKIPS set, exit code, text the output must hold)
PROBE_CASES = (
    ("Probe.Passes", True, 0, ""),
    ("Probe.Passes:Probe.SkipsWithAReason", True, SKIP_CODE,
     f"ctest entry: skipped Probe.SkipsWithAReason: {REASON}"),
    ("Probe.SkipsWithNoReason", True, 1, "skipped without a reason"),
    ("Probe.SkipsWithAReason:Probe.SkipsWithNoReason", True, 1, "skipped without a reason"),
    ("Probe.SkipsWithAReason:Probe.Fails", True, 1, "the probe fails"),
    ("Probe.Passes:Probe.Fails", True, 1, "the probe fails"),
    ("Nothing.Matches", True, 1, "no test matched the filter"),
    ("Probe.SkipsWithAReason", False, 0, REASON),
    ("Probe.Fails", False, 1, "the probe fails"),
)
# The tests that can skip, each in an entry of its own: entry -> (binary, its tests).
APART = {
    "test_bench_platform_cross_core": (
        "test_bench_platform", ("BenchCrossCore.TheLiveClocksAgreeAcrossTwoThreads",)),
    "test_spsc_cache_line": ("test_spsc", ("SpscRing.PaddingConstantIsTheHardwareLine",)),
    "test_udp_slot_modes": ("test_udp", ("Modes.ADatagramPastTheSlotIsCountedTruncated",)),
    "test_udp_ring_modes": ("test_udp", ("Modes.AMultishotModeOutOfBuffersRearmsAndLosesNothing",
                                         "Modes.ARingModeWithABadBufferCountFailsItsSetup")),
}
# The modes each receive mode entry checks; with none of them built, it skips.
MODES_CHECKED = {
    "test_udp_slot_modes": {"S3", "S4", "U2", "U3", "U4", "U5"},
    "test_udp_ring_modes": {"U2", "U3", "U4", "U5"},
}


def run(command, under_ctest):
    env = dict(os.environ)
    env.pop("ITCH_CTEST_SKIPS", None)
    if under_ctest:
        env["ITCH_CTEST_SKIPS"] = "1"
    done = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    return done.returncode, done.stdout + done.stderr


# A CMake argument as CTestTestfile.cmake writes it: "quoted", with backslash escapes,
# [=[bracketed]=], or a bare word such as a property's name.
ARGUMENT = re.compile(r'"((?:[^"\\]|\\.)*)"|\[(=*)\[(.*?)\]\2\]|([^\s"()\[]+)')


def arguments(text):
    found = []
    for quoted, equals, bracketed, bare in ARGUMENT.findall(text):
        if bare:
            found.append(bare)
        elif equals or bracketed:
            found.append(bracketed)
        else:
            found.append(re.sub(r"\\(.)", r"\1", quoted))
    return found


def ctest_entries(build):
    """Each entry's command and properties, from every CTestTestfile.cmake under the build."""
    tests = {}
    for path in sorted(Path(build).rglob("CTestTestfile.cmake")):
        if "_deps" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("add_test("):
                name, *command = arguments(line.removeprefix("add_test("))
                tests[name] = {"command": command, "properties": {}}
            elif line.startswith("set_tests_properties("):
                head, _, rest = line.removeprefix("set_tests_properties(").partition(" PROPERTIES ")
                words = arguments(rest)
                for name in arguments(head):
                    props = tests[name]["properties"]
                    for key, value in zip(words[0::2], words[1::2]):
                        props[key] = value
    return tests


def gtest_filter(command):
    words = [w for w in command[1:] if w.startswith("--gtest_filter=")]
    return words[-1].removeprefix("--gtest_filter=") if words else None


def main():
    probe, build, modes = sys.argv[1:4]
    modes_built = set(modes.split(",")) if modes else set()
    failures = []

    for wanted, under_ctest, code, text in PROBE_CASES:
        case = f"probe {wanted}{'' if under_ctest else ' by hand'}"
        got, out = run([probe, f"--gtest_filter={wanted}"], under_ctest)
        if got != code:
            failures.append((case, f"exit {got}, not {code}"))
        elif text not in out:
            failures.append((case, f"no {text!r} in its output"))

    tests = ctest_entries(build)
    gtest_entries = 0
    for name, test in sorted(tests.items()):
        props = test["properties"]
        if "SKIP_REGULAR_EXPRESSION" in props:
            failures.append((name, "has a skip regex, which takes a failing binary as Skipped"))
        binary = Path(test["command"][0]).name
        if not binary.startswith("test_"):
            continue
        gtest_entries += 1
        if props.get("SKIP_RETURN_CODE") != str(SKIP_CODE):
            failures.append((name, f"SKIP_RETURN_CODE is {props.get('SKIP_RETURN_CODE')}"))
        if "ITCH_CTEST_SKIPS=1" not in props.get("ENVIRONMENT", "").split(";"):
            failures.append((name, "ctest does not set ITCH_CTEST_SKIPS"))
    if gtest_entries < 20:
        failures.append(("entries", f"only {gtest_entries} GoogleTest entries found"))

    for entry, (binary, names) in APART.items():
        test = tests.get(entry)
        if test is None:
            failures.append((entry, "no such entry: the test skips inside its binary's entry"))
            continue
        if Path(test["command"][0]).name != binary:
            failures.append((entry, f"does not run {binary}"))
        own = (gtest_filter(test["command"]) or "").split(":")
        main_entry = tests.get(binary)
        left_out = (gtest_filter(main_entry["command"]) or "") if main_entry else ""
        left_out = left_out.removeprefix("-").split(":") if left_out.startswith("-") else []
        for name in names:
            if name not in own:
                failures.append((entry, f"does not run {name}"))
            if name not in left_out:
                failures.append((entry, f"{binary}'s own entry runs {name} too"))

    for entry, checked in MODES_CHECKED.items():
        test = tests.get(entry)
        if test is None:
            continue
        code = 0 if checked & modes_built else SKIP_CODE
        got, out = run(test["command"], True)
        if got != code:
            failures.append((entry, f"exit {got}, not {code}, with modes {sorted(modes_built)} "
                                    f"built:\n{out[-2000:]}"))
        elif code == SKIP_CODE and "is built here" not in out:
            failures.append((entry, "skipped without saying which modes it needs"))

    for case, reason in failures:
        print(f"skips cli: FAIL {case}: {reason}")
    if failures:
        return 1
    print(f"skips cli: all cases passed ({gtest_entries} GoogleTest entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
