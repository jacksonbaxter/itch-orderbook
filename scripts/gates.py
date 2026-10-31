#!/usr/bin/env python3
"""Rules no compiler or test enforces, checked by scanning the tree.

    gates.py [--root DIR] [--only NAME] [--list]

Each gate prints every violation as path:line. A gate that scanned no files fails,
because a gate looking at nothing would pass forever. scripts/test_gates.py and the
gates area of scripts/fault_matrix.py show each one failing on a planted violation.

Adding a gate: write a function taking (root, files) and returning violations, and
add it to GATES with the paths it applies to.

Exit codes: 0 every gate passed, 1 at least one failed, 2 bad usage.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SKIPPED_DIRS = {".git", ".venv", ".claude", "__pycache__", "data"}
CXX_SUFFIXES = {".hpp", ".cpp", ".h", ".cc"}
WIRE_CODE = ("include", "src", "tools")
PRODUCTION_CODE = ("include", "src")
# Fed untrusted reference files, so they are held to the parser's rule on aborting.
REFERENCE_PARSERS = ("tests/trades/reference_text.hpp", "tests/trades/cross_stats.hpp",
                     "tests/trades/midas.hpp")
ALL_CODE = ("include", "src", "tools", "tests", "fuzz", "bench")
# Prose may discuss what the project holds back and cite where it came from. Code,
# scripts, configuration and results may not touch it.
PROSE = {".md"}

BYTE_TYPES = {"char", "unsigned char", "signed char", "std::byte", "byte", "std::uint8_t",
              "uint8_t", "std::int8_t", "int8_t", "void"}

COMMENT_OR_LITERAL = re.compile(
    r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", re.DOTALL)
NAMED_POINTER_CAST = re.compile(
    r"\b(reinterpret_cast|static_cast|const_cast)\s*<\s*((?:const\s+|volatile\s+)*)"
    r"([\w:]+(?:\s+[\w:]+)*)\s*(?:const\s*)?[*&]")
C_STYLE_POINTER_CAST = re.compile(
    r"\(\s*(?:const\s+|volatile\s+)*"
    r"((?:std::)?u?int(?:16|32|64)_t|(?:unsigned\s+)?(?:short|int|long)(?:\s+long)?|float|double|T)"
    r"\s*(?:const\s*)?\*\s*\)")
LIFETIME_START = re.compile(r"\bstart_lifetime_as(?:_array)?\b")
BRANCH_HINT = re.compile(
    r"\[\[\s*(?:likely|unlikely)\s*\]\]|__builtin_expect\w*|__builtin_prefetch|_mm_prefetch"
    r"|\b(?:LIKELY|UNLIKELY)\s*\(|\bprefetch[rw]?\s*\(")
MEASURED = re.compile(r"//.*\bmeasured:\s*\S")
HOME_PATH = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+")
PRIVATE_ADDRESS = re.compile(
    r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"
    r"|\b192\.168\.\d{1,3}\.\d{1,3}\b|\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
HELD_VENDOR = re.compile(r"c[b]oe", re.IGNORECASE)
PREPROCESSOR_CONDITION = re.compile(r"^\s*#\s*(?:if|ifdef|ifndef|elif|elifdef|elifndef)\b")
ABORTING_CALL = re.compile(
    r"\bassert\s*\(|\bthrow\b|\babort\s*\(|\b__builtin_trap\b|\bstd::terminate\b"
    r"|\bstd::get\s*<|\.value\s*\(\s*\)|\bstd::unreachable\b")

# scripts/test_fetch_data.py asserts that the data scripts do not name the held vendor,
# so it has to name it.
HELD_VENDOR_EXEMPT = {"scripts/test_fetch_data.py"}


def code_only(text):
    """Blanks comments and literals, keeping every newline so that line numbers hold."""
    return COMMENT_OR_LITERAL.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def read_text(path):
    """The text of a file, or None for a binary one."""
    data = path.read_bytes()
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def walk(root, top=None):
    """Every file under root, skipping build output and everything else not in the repo."""
    base = root / top if top else root
    if base.is_file():
        yield base
        return
    if not base.is_dir():
        return
    for path in sorted(base.iterdir()):
        if path.is_dir():
            if path.name in SKIPPED_DIRS or (path.parent == root and path.name.startswith("build")):
                continue
            yield from walk(root, path.relative_to(root))
        else:
            yield path


def cxx_files(root, tops):
    return [path for top in tops for path in walk(root, top) if path.suffix in CXX_SUFFIXES]


def relative(root, path):
    return path.relative_to(root).as_posix()


def line_of(text, position):
    return text.count("\n", 0, position) + 1


def wire_pointer_cast(root, files):
    violations = []
    for path in files:
        code = code_only(read_text(path) or "")
        for match in NAMED_POINTER_CAST.finditer(code):
            pointee = " ".join(match[3].split())
            if pointee not in BYTE_TYPES:
                violations.append((path, line_of(code, match.start()),
                                   f"{match[1]} to {pointee}"))
        for pattern, what in ((C_STYLE_POINTER_CAST, "C-style cast to a multi-byte pointer"),
                              (LIFETIME_START, "start_lifetime_as")):
            for match in pattern.finditer(code):
                violations.append((path, line_of(code, match.start()), what))
    return violations


def bare_branch_hint(root, files):
    violations = []
    for path in files:
        raw = (read_text(path) or "").splitlines()
        code = code_only("\n".join(raw)).splitlines()
        for number, line in enumerate(code, start=1):
            match = BRANCH_HINT.search(line)
            if match is None:
                continue
            context = raw[number - 1] + "\n" + (raw[number - 2] if number > 1 else "")
            if not MEASURED.search(context):
                violations.append((path, number, f"{match[0]} with no // measured: reason"))
    return violations


def scan_lines(pattern, what):
    def scan(root, files):
        violations = []
        for path in files:
            for number, line in enumerate((read_text(path) or "").splitlines(), start=1):
                match = pattern.search(line)
                if match is not None:
                    violations.append((path, number, what.format(match[0])))
        return violations
    return scan


def held_vendor(root, files):
    kept = [path for path in files
            if path.suffix not in PROSE and relative(root, path) not in HELD_VENDOR_EXEMPT]
    return scan_lines(HELD_VENDOR, "held vendor named: {}")(root, kept)


def preprocessor_condition(root, files):
    return scan_lines(PREPROCESSOR_CONDITION, "conditional compilation: {}")(root, files)


def aborting_call(root, files):
    violations = []
    for path in files:
        code = code_only(read_text(path) or "")
        for number, line in enumerate(code.splitlines(), start=1):
            match = ABORTING_CALL.search(line)
            if match is not None:
                violations.append((path, number, f"can abort on wire content: {match[0]}"))
    return violations


def toolchain_path(root, files):
    build_files = [path for path in files if path.suffix in {".cmake", ".sh", ".py", ".yml"}
                   or path.name in {"CMakeLists.txt", "CMakePresets.json"}]
    return scan_lines(re.compile(r"\.tool" r"chain/"), "hardcoded toolchain path: {}")(
        root, build_files)


@dataclass(frozen=True)
class Gate:
    name: str
    rule: str
    scan: object
    files: object


def gates():
    return (
        Gate("wire-pointer-cast", "no pointer cast to a multi-byte type over wire bytes",
             wire_pointer_cast, lambda root: cxx_files(root, WIRE_CODE)),
        Gate("bare-branch-hint", "every branch hint or prefetch states a // measured: reason",
             bare_branch_hint, lambda root: cxx_files(root, ALL_CODE)),
        Gate("home-path", "no absolute path that names a user",
             scan_lines(HOME_PATH, "home path: {}"), lambda root: list(walk(root))),
        Gate("private-address", "no private or tailnet IP address",
             scan_lines(PRIVATE_ADDRESS, "private address: {}"), lambda root: list(walk(root))),
        Gate("held-vendor", "the vendor held in NOTES.md appears in no code, script or result",
             held_vendor, lambda root: list(walk(root))),
        Gate("no-conditional-compilation", "no hooks or test-only #if in parser code",
             preprocessor_condition, lambda root: cxx_files(root, PRODUCTION_CODE)),
        Gate("no-abort-on-wire", "no assert, throw or abort in parser code",
             aborting_call, lambda root: cxx_files(root, PRODUCTION_CODE + REFERENCE_PARSERS)),
        Gate("toolchain-path", "no hardcoded toolchain path in build files or scripts",
             toolchain_path, lambda root: list(walk(root))),
    )


def run_gate(gate, root):
    """Prints a gate's verdict and returns True if it passed."""
    files = gate.files(root)
    if not files:
        print(f"gates: FAIL {gate.name}: scanned no files")
        return False
    violations = gate.scan(root, files)
    for path, line, what in violations:
        print(f"gates: FAIL {gate.name} {relative(root, path)}:{line}: {what}")
    if violations:
        return False
    print(f"gates: ok {gate.name}: {len(files)} files, rule: {gate.rule}")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--only", action="append", default=[], metavar="NAME")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    chosen = [gate for gate in gates() if not args.only or gate.name in args.only]
    if not chosen:
        print(f"gates: no gate named {args.only}", file=sys.stderr)
        return 2
    if args.list:
        for gate in chosen:
            print(f"{gate.name}\t{gate.rule}")
        return 0
    root = args.root.resolve()
    failed = [gate.name for gate in chosen if not run_gate(gate, root)]
    print(f"gates: {len(chosen) - len(failed)} of {len(chosen)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
