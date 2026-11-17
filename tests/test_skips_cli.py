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
that its binary's main entry leaves out. Which tests can skip is read from the sources, not
kept by hand: every GTEST_SKIP in tests/test_*.cpp, in the TEST it sits in or in each test
that uses the macro it sits in, on this platform's side of any #ifdef __APPLE__ or __linux__
(and its #elif, #elifdef, #elifndef and #else branches, as the reader's own case checks).
A GTEST_SKIP anywhere else fails, since no test could be named for it. Last, the receive mode
entries skip exactly when CMake built none of the modes they check. The harness-skip faults in
scripts/faults/harness.json and two in udp.json break each.
A failure prints `skips cli: FAIL <case>: <reason>`.
"""

import fnmatch
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
# The tests that can skip are read from the sources (skipping_tests). These are the ones
# there when this was written, so that a reader that finds fewer fails rather than passes.
KNOWN_SKIPS = {
    ("test_bench_platform", "BenchCrossCore.TheLiveClocksAgreeAcrossTwoThreads"),
    ("test_spsc", "SpscRing.PaddingConstantIsTheHardwareLine"),
    ("test_udp", "Modes.ADatagramPastTheSlotIsCountedTruncated"),
    ("test_udp", "Modes.AMultishotModeOutOfBuffersRearmsAndLosesNothing"),
    ("test_udp", "Modes.ARingModeWithABadBufferCountFailsItsSetup"),
    ("test_node_book", "NodePool.EveryNodeIsFaultedInBeforeTheFirstIsTaken"),
    ("test_kernels", "Built/KernelTest.BookOpsMatchScalarOnRandomBytes/*"),
}
TESTS_DIR = Path(__file__).resolve().parent
TEST_MACRO = re.compile(r"^(TEST|TEST_F|TEST_P|TYPED_TEST|TYPED_TEST_P)\(\s*(\w+)\s*,\s*(\w+)\s*\)")
INSTANTIATION = re.compile(r"INSTANTIATE_TEST_SUITE_P\(\s*(\w+)\s*,\s*(\w+)\s*,")
DEFINE = re.compile(r"^\s*#\s*define\s+(\w+)")
CONDITION = re.compile(r"^\s*#\s*(ifdef|ifndef|if|elifdef|elifndef|elif|else|endif)\b(.*)$")
PLATFORM = {"__APPLE__": sys.platform == "darwin", "__linux__": sys.platform.startswith("linux")}


def compiled_here(kind, rest):
    """Whether a condition holds on this platform; one that names no platform is taken to."""
    names = re.findall(r"\b(__APPLE__|__linux__)\b", rest)
    if not names or not set(re.findall(r"\b\w+\b", rest)) <= set(names) | {"defined"}:
        return True
    if kind == "ifdef":
        return PLATFORM[names[0]]
    if kind == "ifndef":
        return not PLATFORM[names[0]]
    return eval(re.sub(r"defined\s*\(\s*(\w+)\s*\)", lambda m: str(PLATFORM[m[1]]),  # noqa: S307
                       rest).replace("||", " or ").replace("&&", " and ").replace("!", " not "))


def skipping_tests(failures):
    """{(binary, gtest name)} for every test that can skip on this platform, from the sources.
    A TEST_P's name is <instantiation>/<suite>.<test>/*, a TYPED_TEST's <suite>/*.<test>."""
    found = set()
    for path in sorted(TESTS_DIR.glob("test_*.cpp")):
        found |= skips_in(path.stem, path.name, path.read_text(encoding="utf-8"), failures)
    return found


def skips_in(stem, file_name, text, failures):
    """skipping_tests' reading of one source. Each #if, #ifdef or #ifndef opens a group whose
    branches (#elif, #elifdef, #elifndef, #else) are each live only if no branch before it was."""
    found = set()
    lines = text.split("\n")
    instances = {suite: name for name, suite in INSTANTIATION.findall(text)}
    stack, current, macro, uses = [], None, None, {}
    skipping_macros = set()
    for number, line in enumerate(lines, start=1):
        condition = CONDITION.match(line)
        if condition:
            kind, rest = condition.groups()
            if kind in ("ifdef", "ifndef", "if"):
                holds = compiled_here(kind, rest)
                stack.append([holds, holds])  # [a branch was taken, this branch is live]
            elif kind in ("elif", "elifdef", "elifndef"):
                holds = not stack[-1][0] and compiled_here(kind.removeprefix("el"), rest)
                stack[-1] = [stack[-1][0] or holds, holds]
            elif kind == "else":
                stack[-1] = [True, not stack[-1][0]]
            else:
                stack.pop()
            continue
        live = all(branch[1] for branch in stack)
        defined = DEFINE.match(line)
        macro = defined[1] if defined else (macro if macro and lines[number - 2].endswith("\\")
                                            else None)
        test = TEST_MACRO.match(line)
        if test:
            kind, suite, name = test.groups()
            if kind == "TEST_P":
                full = f"{instances.get(suite, '*')}/{suite}.{name}/*"
            elif kind.startswith("TYPED_TEST"):
                full = f"{suite}/*.{name}"
            else:
                full = f"{suite}.{name}"
            current = full if live else None
            uses[full] = set()
        elif line.startswith("}"):
            current = None
        if "GTEST_SKIP" not in line or line.lstrip().startswith("//"):
            if current:
                uses[current].update(re.findall(r"\b([A-Z][A-Z0-9_]+)\(", line))
            continue
        if macro:
            skipping_macros.add(macro)
        elif current:
            found.add((stem, current))
        elif live:
            failures.append((f"{file_name}:{number}", "a GTEST_SKIP outside any test and any "
                             "macro, so no test can be named for it"))
    for full, called in uses.items():
        if called & skipping_macros:
            found.add((stem, full))
    return found


# A source the reader must read as the compiler would on this platform: each test skips, and
# only those in the branches compiled here can.
READER_CASE = """#ifdef __linux__
TEST(Reader, OnLinux) {
  GTEST_SKIP() << "x";
}
#elifdef __APPLE__
TEST(Reader, OnTheMac) {
  GTEST_SKIP() << "x";
}
#else
TEST(Reader, Elsewhere) {
  GTEST_SKIP() << "x";
}
#endif
#ifndef __APPLE__
TEST(Reader, NotOnTheMac) {
  GTEST_SKIP() << "x";
}
#elifndef __linux__
TEST(Reader, OnTheMacByElifndef) {
  GTEST_SKIP() << "x";
}
#else
TEST(Reader, OnNeither) {
  GTEST_SKIP() << "x";
}
#endif
#ifdef __APPLE__
TEST(Reader, MacFirst) {
  GTEST_SKIP() << "x";
}
#elifdef __linux__
TEST(Reader, LinuxSecond) {
  GTEST_SKIP() << "x";
}
#else
TEST(Reader, NeitherThird) {
  GTEST_SKIP() << "x";
}
#endif
#ifdef __APPLE__
TEST(Reader, MacOnce) {
  GTEST_SKIP() << "x";
}
#elif defined(__APPLE__) || defined(__linux__)
TEST(Reader, NotAfterTheMac) {
  GTEST_SKIP() << "x";
}
#endif
"""


def reader_case_expected():
    apple, linux = PLATFORM["__APPLE__"], PLATFORM["__linux__"]
    branches = {"OnLinux": linux, "OnTheMac": apple and not linux,
                "Elsewhere": not apple and not linux, "NotOnTheMac": not apple,
                "OnTheMacByElifndef": apple and not linux, "OnNeither": apple and linux,
                "MacFirst": apple, "LinuxSecond": linux and not apple,
                "NeitherThird": not apple and not linux, "MacOnce": apple,
                "NotAfterTheMac": linux and not apple}
    return {("test_reader", f"Reader.{name}") for name, live in branches.items() if live}


def filter_runs(test_filter, name):
    """Whether a --gtest_filter value runs the test of that full name."""
    positive, _, negative = (test_filter or "*").partition("-")
    def matches(patterns):
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns.split(":") if pattern)
    return matches(positive or "*") and not matches(negative)


def sample_names(pattern, filters):
    """Concrete names for a test name with * in it: * taken from each filter's own words."""
    if "*" not in pattern:
        return [pattern]
    words = {word for value in filters for word in re.split(r"[/.:*-]", value or "") if word}
    return [pattern.replace("*", word) for word in sorted(words)] or [pattern.replace("*", "x")]
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

    read = skips_in("test_reader", "the reader's case", READER_CASE, failures)
    if read != reader_case_expected():
        wanted = sorted(name for _, name in reader_case_expected())
        failures.append(("reader", f"reads {sorted(name for _, name in read)} as compiled here "
                                   "from a source of #ifdef, #elifdef, #elifndef and #else "
                                   f"branches, not {wanted}"))
    skipping = skipping_tests(failures)
    for binary, name in sorted(KNOWN_SKIPS - skipping):
        failures.append((f"{binary} {name}", "no longer read as a test that can skip: the "
                                             "reader of the sources misses it"))
    for binary, name in sorted(skipping):
        entries = {entry: gtest_filter(test["command"]) for entry, test in tests.items()
                   if Path(test["command"][0]).name == binary}
        if not entries:
            # Every test source is built as the binary of its own name, in every build that
            # runs this check; a source built under another name would leave its skips unread.
            failures.append((name, f"can skip, and no ctest entry runs a binary named {binary}, "
                                   f"after its source {binary}.cpp, so its entries go unchecked"))
            continue
        names = sample_names(name, entries.values())
        own = [entry for entry, value in entries.items()
               if entry != binary and any(filter_runs(value, sample) for sample in names)]
        if not own:
            failures.append((name, f"can skip, and has no entry of its own: it skips inside "
                                   f"{binary}'s entry"))
        if binary in entries and any(filter_runs(entries[binary], sample) for sample in names):
            failures.append((name, f"{binary}'s own entry runs {name} too"))

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
