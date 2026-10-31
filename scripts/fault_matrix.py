#!/usr/bin/env python3
"""Shows every check failing on a fault injected for it.

A check that cannot fail proves nothing. Each fault here breaks one thing on purpose,
in a scratch copy of the tree, and names the check that must catch it. A fault passes
only if that check fails with the expected signature. The verdicts are:

    detected         the check failed and printed the signature. The only pass.
    undetected       the check passed on the broken tree.
    wrong-signature  the check failed some other way, a crash included.
    timeout          the check did not finish.
    stale            a mutation's old text does not occur exactly once, or an input
                     mutation changes nothing, so the fault no longer injects anything.
    build-error      the mutant did not build. This is not a detection. Faults aimed
                     at the compiler build in their command, with empty build targets.
    not-run          a control failed, so no mutant was run.

Before any mutant runs, each distinct command runs on a clean copy and must pass
without printing the signature. A failed control fails the whole run.

Adding an area means adding scripts/faults/<area>.json:

    {
      "area": "<the file's stem>",
      "tree": ["paths to copy"],           optional, default DEFAULT_TREE
      "faults": [{
        "id": "unique-across-all-areas",
        "breaks": "what the fault breaks",
        "check": "the check that must catch it",
        "mutations": [{"file": "...", "old": "exact text", "new": "..."}],
        "inputs": [{"file": "...", "op": "flip_byte", "offset": 11}],
        "build": {"preset": "release", "targets": ["test_wire"], "gtest": true},
        "command": ["{build}/tests/test_wire"],
        "expect": {"signature": "regex", "exit": 1},
        "timeout": 300
      }]
    }

mutations and inputs may be used together, and at least one is required. build is
optional. expect.exit is optional and defaults to any non-zero exit. Commands run in
the tree copy, with {tree}, {build} and {python} substituted. Input ops are flip_byte
(offset, mask), truncate (size, negative counts from the end), delete_bytes (offset,
length), duplicate_bytes (offset, length), json_set (path, value), replace_text (old,
new, which must occur exactly once) and remove_files.

    fault_matrix.py --list
    fault_matrix.py [--only ID] [--area NAME] [--log-dir DIR] [--out FILE] [--keep]

Exit codes: 0 every selected fault detected and every control passed, 1 otherwise,
2 the definitions could not be used.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_JOBS = 6
DEFAULT_TIMEOUT = 300
DEFAULT_TREE = ("CMakeLists.txt", "CMakePresets.json", ".clang-tidy", "cmake", "include", "src",
                "tests", "tools", "fuzz", "scripts")
GTEST_LIBRARY = "lib/libgtest.a"

DETECTED = "detected"
UNDETECTED = "undetected"
WRONG_SIGNATURE = "wrong-signature"
TIMEOUT = "timeout"
STALE = "stale"
BUILD_ERROR = "build-error"
NOT_RUN = "not-run"

ID_FORMAT = re.compile(r"[a-z0-9][a-z0-9-]*")
REQUIRED_KEYS = {"id", "breaks", "check", "command", "expect"}
ALLOWED_KEYS = REQUIRED_KEYS | {"mutations", "inputs", "build", "timeout"}


class DefinitionError(Exception):
    """A fault file cannot be used as written."""


@dataclass
class Fault:
    id: str
    area: str
    breaks: str
    check: str
    command: list
    signature: str
    exit_code: int | None
    mutations: list
    inputs: list
    build: dict | None
    timeout: int
    tree: tuple
    status: str = ""
    detail: str = ""
    exit_seen: int | None = None
    matched: str = ""
    seconds: float = 0.0


@dataclass
class Control:
    command: list
    build: dict | None
    timeout: int
    tree: tuple
    faults: list = field(default_factory=list)


def require(condition, message):
    if not condition:
        raise DefinitionError(message)


def parse_fault(raw, area, tree, source):
    where = f"{source}: fault {raw.get('id', '<no id>')}"
    missing = REQUIRED_KEYS - raw.keys()
    require(not missing, f"{where}: missing {sorted(missing)}")
    unknown = raw.keys() - ALLOWED_KEYS
    require(not unknown, f"{where}: unknown keys {sorted(unknown)}")
    require(ID_FORMAT.fullmatch(raw["id"]), f"{where}: bad id")

    mutations = raw.get("mutations", [])
    inputs = raw.get("inputs", [])
    require(mutations or inputs, f"{where}: injects nothing")
    for mutation in mutations:
        require(mutation.keys() == {"file", "old", "new"}, f"{where}: bad mutation keys")
        require(mutation["old"] != mutation["new"], f"{where}: mutation changes nothing")
    for spec in inputs:
        require({"file", "op"} <= spec.keys(), f"{where}: bad input keys")
        require(spec["op"] in INPUT_OPS or spec["op"] == "remove_files",
                f"{where}: unknown input op {spec['op']}")

    expect = raw["expect"]
    require(isinstance(expect, dict) and expect.get("signature"), f"{where}: no signature")
    require(expect.keys() <= {"signature", "exit"}, f"{where}: bad expect keys")
    try:
        re.compile(expect["signature"])
    except re.error as error:
        raise DefinitionError(f"{where}: bad signature: {error}") from error

    build = raw.get("build")
    if build is not None:
        require(build.keys() <= {"preset", "targets", "gtest"} and "preset" in build,
                f"{where}: bad build keys")
    command = raw["command"]
    require(command and all(isinstance(word, str) for word in command), f"{where}: bad command")
    require(build is not None or not any("{build}" in word for word in command),
            f"{where}: {{build}} needs a build")

    return Fault(id=raw["id"], area=area, breaks=raw["breaks"], check=raw["check"],
                 command=command, signature=expect["signature"], exit_code=expect.get("exit"),
                 mutations=mutations, inputs=inputs, build=build,
                 timeout=raw.get("timeout", DEFAULT_TIMEOUT), tree=tree)


def load_faults(faults_dir):
    faults = []
    seen = set()
    for path in sorted(Path(faults_dir).glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise DefinitionError(f"{path.name}: {error}") from error
        require(document.get("area") == path.stem, f"{path.name}: area must be {path.stem}")
        unknown = document.keys() - {"area", "tree", "faults"}
        require(not unknown, f"{path.name}: unknown keys {sorted(unknown)}")
        tree = tuple(document.get("tree", DEFAULT_TREE))
        for raw in document.get("faults", []):
            fault = parse_fault(raw, path.stem, tree, path.name)
            require(fault.id not in seen, f"{path.name}: duplicate id {fault.id}")
            seen.add(fault.id)
            faults.append(fault)
    return faults


def flip_byte(data, spec):
    offset = spec["offset"]
    require(offset < len(data), f"flip_byte: offset {offset} beyond {len(data)} bytes")
    return data[:offset] + bytes([data[offset] ^ spec.get("mask", 0x01)]) + data[offset + 1:]


def truncate(data, spec):
    return data[:spec["size"]]


def delete_bytes(data, spec):
    return data[:spec["offset"]] + data[spec["offset"] + spec["length"]:]


def json_set(data, spec):
    document = json.loads(data)
    node = document
    for key in spec["path"][:-1]:
        node = node[key]
    require(spec["path"][-1] in node, f"json_set: no key {spec['path'][-1]}")
    node[spec["path"][-1]] = spec["value"]
    return json.dumps(document, indent=2).encode("utf-8") + b"\n"


def duplicate_bytes(data, spec):
    offset, length = spec["offset"], spec["length"]
    return data[:offset + length] + data[offset:offset + length] + data[offset + length:]


def replace_text(data, spec):
    old = spec["old"].encode("utf-8")
    require(data.count(old) == 1, f"replace_text: {spec['old']!r} is not there exactly once")
    return data.replace(old, spec["new"].encode("utf-8"))


INPUT_OPS = {
    "flip_byte": flip_byte,
    "truncate": truncate,
    "delete_bytes": delete_bytes,
    "duplicate_bytes": duplicate_bytes,
    "json_set": json_set,
    "replace_text": replace_text,
}


def find_stale(fault, root):
    """Why this fault no longer injects what it says, or None."""
    for mutation in fault.mutations:
        path = root / mutation["file"]
        if not path.is_file():
            return f"{mutation['file']} does not exist"
        count = path.read_text(encoding="utf-8").count(mutation["old"])
        if count != 1:
            return f"{mutation['file']}: old text occurs {count} times, not once"
    for spec in fault.inputs:
        path = root / spec["file"]
        if not path.exists():
            return f"{spec['file']} does not exist"
        if spec["op"] == "remove_files":
            if not path.is_dir() or not any(path.iterdir()):
                return f"{spec['file']} has nothing to remove"
            continue
        data = path.read_bytes()
        try:
            changed = INPUT_OPS[spec["op"]](data, spec)
        except (DefinitionError, KeyError, ValueError, IndexError, TypeError) as error:
            return f"{spec['file']}: {spec['op']} cannot apply: {error!r}"
        if changed == data:
            return f"{spec['file']}: {spec['op']} changes nothing"
    return None


def copy_tree(root, destination, tree):
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in tree:
        source = root / name
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, ignore=ignore, symlinks=True)
        else:
            shutil.copy2(source, target)


def inject(fault, tree):
    for mutation in fault.mutations:
        path = tree / mutation["file"]
        path.write_text(path.read_text(encoding="utf-8").replace(mutation["old"], mutation["new"]),
                        encoding="utf-8")
    for spec in fault.inputs:
        path = tree / spec["file"]
        if spec["op"] == "remove_files":
            for entry in path.iterdir():
                entry.unlink()
        else:
            path.write_bytes(INPUT_OPS[spec["op"]](path.read_bytes(), spec))


def run_logged(command, cwd, log, timeout):
    """Runs a command with its output going to log. Returns (exit, output), exit None on timeout."""
    with open(log, "a", encoding="utf-8", errors="replace") as out:
        out.write(f"$ {' '.join(command)}\n")
        out.flush()
        start = out.tell()
    with open(log, "ab") as out:
        process = subprocess.Popen(command, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            code = None
    with open(log, "rb") as produced:
        produced.seek(start)
        output = produced.read().decode("utf-8", errors="replace")
    with open(log, "a", encoding="utf-8") as out:
        out.write(f"\n[exit {code}]\n")
    return code, output


def substitute(command, tree, build):
    replaced = []
    for word in command:
        word = word.replace("{tree}", str(tree)).replace("{python}", sys.executable)
        if build:
            word = word.replace("{build}", str(tree / "build" / build["preset"]))
        replaced.append(word)
    return replaced


def ensure_gtest(root, preset, log):
    """Builds the pinned GoogleTest once in build/<preset>, for every scratch build to link."""
    if (root / "build" / preset / GTEST_LIBRARY).is_file():
        return True
    for command in (["cmake", "--preset", preset],
                    ["cmake", "--build", "--preset", preset, "--target", "gtest", "gtest_main",
                     "--parallel", str(MAX_JOBS)]):
        code, _ = run_logged(command, root, log, DEFAULT_TIMEOUT)
        if code != 0:
            return False
    return True


def build_tree(root, tree, build, targets, log):
    """Configures and builds one tree. Returns an error text, or None."""
    if build is None:
        return None
    preset = build["preset"]
    configure = ["cmake", "--preset", preset]
    if build.get("gtest", True):
        if not ensure_gtest(root, preset, log):
            return f"could not build GoogleTest for preset {preset}"
        configure.append(f"-DITCH_PREBUILT_DEPS={root / 'build' / preset}")
    code, _ = run_logged(configure, tree, log, DEFAULT_TIMEOUT)
    if code != 0:
        return f"configure exited {code}"
    if targets:
        command = ["cmake", "--build", "--preset", preset, "--parallel", str(MAX_JOBS),
                   "--target", *targets]
        code, _ = run_logged(command, tree, log, DEFAULT_TIMEOUT)
        if code != 0:
            return f"build exited {code}"
    return None


def group_controls(faults):
    controls = {}
    for fault in faults:
        preset = fault.build["preset"] if fault.build else None
        key = (json.dumps(fault.command), preset)
        if key not in controls:
            controls[key] = Control(command=fault.command, build=fault.build, timeout=fault.timeout,
                                    tree=fault.tree)
        controls[key].faults.append(fault)
    return list(controls.values())


def run_controls(root, build_root, faults, log_dir):
    """Runs each distinct command on a clean tree. Returns the failures as text."""
    controls = group_controls(faults)
    tree = build_root / "_control"
    trees = sorted({name for control in controls for name in control.tree})
    copy_tree(root, tree, trees)
    log = log_dir / "_control.log"
    log.write_text("", encoding="utf-8")

    wanted = {}
    for control in controls:
        if control.build:
            preset = control.build["preset"]
            wanted.setdefault(preset, {"build": control.build, "targets": set()})
            wanted[preset]["targets"].update(
                target for fault in control.faults for target in fault.build.get("targets", []))
    failures = []
    for preset, spec in wanted.items():
        error = build_tree(root, tree, spec["build"], sorted(spec["targets"]), log)
        if error:
            failures.append(f"preset {preset}: {error}")
    if failures:
        return len(controls), failures

    for control in controls:
        command = substitute(control.command, tree, control.build)
        code, output = run_logged(command, tree, log, control.timeout)
        shown = " ".join(control.command)
        if code != 0:
            failures.append(f"{shown}: exited {code} on the clean tree")
        for fault in control.faults:
            if re.search(fault.signature, output, re.MULTILINE):
                failures.append(f"{fault.id}: signature appears on the clean tree")
    print(f"control: {len(controls)} commands, {len(failures)} failures", flush=True)
    return len(controls), failures


def judge(fault, code, output):
    if code is None:
        return TIMEOUT, f"no result in {fault.timeout} s"
    if code == 0:
        return UNDETECTED, "the check passed on the broken tree"
    match = re.search(fault.signature, output, re.MULTILINE)
    if match is None:
        return WRONG_SIGNATURE, f"exited {code} without the signature"
    if fault.exit_code is not None and code != fault.exit_code:
        return WRONG_SIGNATURE, f"exited {code}, wanted {fault.exit_code}"
    fault.matched = match.group(0)
    return DETECTED, ""


def run_fault(fault, root, build_root, log_dir, keep):
    started = time.monotonic()
    tree = build_root / fault.id
    log = log_dir / f"{fault.id}.log"
    log.write_text(f"fault {fault.id}: {fault.breaks}\ncheck: {fault.check}\n", encoding="utf-8")
    copy_tree(root, tree, fault.tree)
    inject(fault, tree)
    targets = fault.build.get("targets", []) if fault.build else []
    error = build_tree(root, tree, fault.build, targets, log)
    if error:
        fault.status, fault.detail = BUILD_ERROR, error
    else:
        command = substitute(fault.command, tree, fault.build)
        code, output = run_logged(command, tree, log, fault.timeout)
        fault.exit_seen = code
        fault.status, fault.detail = judge(fault, code, output)
    fault.matched = fault.matched.replace(str(tree), "<tree>")
    fault.seconds = time.monotonic() - started
    if not keep:
        shutil.rmtree(tree, ignore_errors=True)


def table_text(text):
    return " ".join(text.split()).replace("|", "\\|").replace("`", "'")


def describe_injection(fault):
    places = [mutation["file"] for mutation in fault.mutations]
    places += [f"{spec['file']} ({spec['op']})" for spec in fault.inputs]
    return ", ".join(sorted(set(places)))


def git_description(root):
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, check=True,
                                capture_output=True, text=True).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain", "--", *DEFAULT_TREE], cwd=root,
                                check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"
    return f"{commit}, with uncommitted changes" if status else commit


def compiler_description():
    compiler = os.environ.get("CXX", "c++")
    try:
        text = subprocess.run([compiler, "--version"], check=True, capture_output=True,
                              text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"
    return text.splitlines()[0].split(" (")[0]


def write_report(path, root, faults, selection, control_count, control_failures):
    passed = sum(fault.status == DETECTED for fault in faults)
    lines = [
        "# Fault matrix",
        "",
        "Generated by `scripts/fault_matrix.py`. Do not edit by hand. Raw logs are not committed.",
        "",
        "Each row breaks one thing on purpose in a scratch copy of the tree. A row passes only",
        "if the named check fails with the expected signature. Before any mutant runs, every",
        "command must pass on a clean copy without printing its signature.",
        "",
        f"- Commit: {git_description(root)}",
        f"- Compiler: {compiler_description()}",
        f"- Host: {platform.system()} {platform.machine()}",
        f"- Selection: {selection}",
        f"- Controls: {control_count} commands, {len(control_failures)} failed",
        f"- Result: {passed} of {len(faults)} detected",
        "",
    ]
    for failure in control_failures:
        lines.append(f"- Control failure: {table_text(failure)}")
    if control_failures:
        lines.append("")
    lines += [
        "| Fault | Area | What it breaks | Check that must catch it | Injected in | Result | Exit "
        "| Signature seen |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for fault in faults:
        seen = table_text(fault.matched) if fault.matched else table_text(fault.detail)
        code = "" if fault.exit_seen is None else str(fault.exit_seen)
        lines.append(
            f"| `{fault.id}` | {fault.area} | {table_text(fault.breaks)} "
            f"| {table_text(fault.check)} | `{table_text(describe_injection(fault))}` "
            f"| {fault.status} | {code} | `{seen}` |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def select(faults, only, areas):
    known_ids = {fault.id for fault in faults}
    known_areas = {fault.area for fault in faults}
    require(not set(only) - known_ids, f"no fault {sorted(set(only) - known_ids)}")
    require(not set(areas) - known_areas, f"no area {sorted(set(areas) - known_areas)}")
    chosen = [fault for fault in faults
              if (not only or fault.id in only) and (not areas or fault.area in areas)]
    require(chosen, "no faults selected")
    return chosen


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="list the faults and stop")
    parser.add_argument("--only", action="append", default=[], metavar="ID")
    parser.add_argument("--area", action="append", default=[], metavar="NAME")
    parser.add_argument("--log-dir", type=Path, help="default <build root>/_logs")
    parser.add_argument("--out", type=Path, help="write the Markdown table here")
    parser.add_argument("--keep", action="store_true", help="keep each fault's scratch tree")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--faults-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--build-root", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    root = args.root.resolve()
    faults_dir = args.faults_dir or root / "scripts" / "faults"
    build_root = (args.build_root or root / "build" / "faults").resolve()
    try:
        faults = select(load_faults(faults_dir), args.only, args.area)
    except DefinitionError as error:
        print(f"fault matrix: {error}", file=sys.stderr)
        return 2

    if args.list:
        for fault in faults:
            print(f"{fault.id}\t{fault.area}\t{fault.check}\t{fault.breaks}")
        return 0

    log_dir = (args.log_dir or build_root / "_logs").resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    build_root.mkdir(parents=True, exist_ok=True)

    live = []
    for fault in faults:
        reason = find_stale(fault, root)
        if reason:
            fault.status, fault.detail = STALE, reason
            print(f"{fault.id}: {STALE}: {reason}", flush=True)
        else:
            live.append(fault)

    control_count, control_failures = (run_controls(root, build_root, live, log_dir)
                                       if live else (0, []))
    for failure in control_failures:
        print(f"control FAILED: {failure}", flush=True)
    for position, fault in enumerate(live, start=1):
        if control_failures:
            fault.status, fault.detail = NOT_RUN, "a control failed"
            continue
        run_fault(fault, root, build_root, log_dir, args.keep)
        print(f"[{position}/{len(live)}] {fault.id}: {fault.status} {fault.detail} "
              f"({fault.seconds:.1f} s)", flush=True)

    passed = sum(fault.status == DETECTED for fault in faults)
    selection = "all areas"
    if args.only or args.area:
        selection = "subset: " + ", ".join(args.area + args.only)
    if args.out:
        write_report(args.out, root, faults, selection, control_count, control_failures)
    if not args.keep:
        shutil.rmtree(build_root / "_control", ignore_errors=True)
    print(f"fault matrix: {passed} of {len(faults)} detected, "
          f"{len(control_failures)} control failures", flush=True)
    return 0 if passed == len(faults) and not control_failures else 1


if __name__ == "__main__":
    sys.exit(main())
