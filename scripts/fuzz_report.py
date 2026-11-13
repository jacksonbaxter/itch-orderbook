#!/usr/bin/env python3
"""The fuzzers' long runs: one results file from a run's status file and raw logs.

    fuzz_report.py write --status FILE --logs DIR --found DIR --host LABEL --out FILE
                         [--run-log FILE] [--root DIR]
    fuzz_report.py check FILE...

`write` reads a run of every fuzz target, as the run script records it: a status file whose
first line is `<UTC> commit <sha> tree <path>`, a `build done` line with the build's flags,
the seconds a target, how many ran at once and the sanitizer options, one line a target,
`<UTC start> <UTC end> <target> exit <n> executions <n> crashes <n>`, and a last line
`ALL DONE <UTC>`; beside it each target's raw libFuzzer log, `<target>.log`, and `build.log`,
in the logs folder, and the folder libFuzzer saves a crash, leak, timeout or out-of-memory
input to, as `<target>-*`. It writes the results file only for a run that is clean, and
refuses anything else, naming what it found:

- no-all-done: the status file does not end with ALL DONE (a run stopped or still going);
- target-missing: a target `fuzz/CMakeLists.txt` adds has no status line, or a status line
  names a target it does not add, or names one twice;
- exit: a target's exit is not 0;
- crashes: a target's crash count is above 0;
- short: the run's seconds a target are under SECONDS, a target's span from start to end is
  under them, or its log's `Done <N> runs in <S> second(s)` gives S under them;
- log: a target's log is missing, has no Done line, gives other executions than its status
  line, or holds an `ERROR:`, `FUZZ_CHECK failed` or `SUMMARY:` line;
- found: an input was saved under the target's name;
- symbolizer: ASAN_OPTIONS or UBSAN_OPTIONS do not set `external_symbolizer_path` to the
  toolchain's llvm-symbolizer, the one beside the compiler that `build.log` names, which on
  macOS keeps a fuzzer from waiting on the system symbolizer (docs/NOTES.md, "No check waits
  on the system symbolizer");
- status: a line the status file should have is not there, or does not read as it should.

The file gives the commit fuzzed, the date, the host by its public label, the compiler, the
build's flags and sanitizers, the seconds a target and how many ran at once, each target's
executions, exit and crashes, and what clean means. No path from the status file reaches it.
With --run-log it appends the run's row to results/run_log.md, which only grows; a run
already in the log is refused. The run has no prediction: it is a check, not a measurement,
so its row says so.

`check` reads a published file again: every target its tree's `fuzz/CMakeLists.txt` adds has
a row, and each row's exit and crashes are 0, its seconds at least SECONDS, and the header's
result the count of its rows.

Each refusal prints as `fuzz_report: FAIL <code> <where>: <detail>`.

Exit codes: 0 written, or check found nothing; 1 refused; 2 bad usage.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SECONDS = 1800
RUN_PREFIX = "fuzz-30min"
# cmake/ItchSanitizerTests.cmake names the llvm-symbolizer beside the compiler in the build log.
SYMBOLIZED_BY = re.compile(r"Sanitizer tests: symbolized by (\S+)$", re.MULTILINE)
SYMBOLIZER_OPTIONS = ("ASAN_OPTIONS", "UBSAN_OPTIONS")
BAD_LOG_LINE = re.compile(r"ERROR:|FUZZ_CHECK failed|SUMMARY:")
FIRST = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) commit ([0-9a-f]{40}) tree \S")
BUILD = re.compile(r"^\S+ build done, check lock released; flags (.*?); seconds (\d+) a target, "
                   r"(\d+) at once; (.*)$")
TARGET = re.compile(r"^(\S+Z) (\S+Z) (\S+) exit (-?\d+) executions (\d+) crashes (\d+)$")
ALL_DONE = re.compile(r"^ALL DONE (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$")
DONE = re.compile(r"^Done (\d+) runs in (\d+) second\(s\)$", re.MULTILINE)
FUZZER = re.compile(r"^itch_add_fuzzer\((\w+)\)", re.MULTILINE)
COMPILER = re.compile(r"The CXX compiler identification is (.+)$", re.MULTILINE)
OS_OF_HOST = {"macOS (M4 Max)": "Darwin", "debian": "Linux"}
HEADS = ("Target", "Start", "End", "Seconds", "Executions", "Exit", "Crashes", "Inputs saved")
CLEAN = ("A target is clean when its status line says exit 0 and crashes 0; no input was saved "
         "under its name; its raw log has libFuzzer's line `Done <N> runs in <S> second(s)`, with "
         f"S at least {SECONDS:,} and N the executions its status line gives, and no `ERROR:`, "
         "`FUZZ_CHECK failed` or `SUMMARY:` line; and the run's status file ends with ALL DONE. "
         "scripts/fuzz_report.py writes no file for a run in which any target is not clean.")


class Refused(Exception):
    def __init__(self, code, where, detail):
        super().__init__(f"fuzz_report: FAIL {code} {where}: {detail}")


def when(stamp):
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")


def fuzz_targets(root):
    """The targets fuzz/CMakeLists.txt adds, in its order."""
    path = root / "fuzz" / "CMakeLists.txt"
    names = FUZZER.findall(path.read_text(encoding="utf-8")) if path.is_file() else []
    if not names:
        raise Refused("target-missing", "fuzz/CMakeLists.txt", "it adds no fuzz target")
    return names


def symbolizer_options(options, where, logs):
    """The sanitizer options of the build done line, each held to the toolchain's symbolizer:
    the llvm-symbolizer beside the compiler, which the build log names."""
    path = logs / "build.log"
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    beside = SYMBOLIZED_BY.search(text)
    if beside is None:
        raise Refused("symbolizer", "build.log", "the build names no llvm-symbolizer beside the "
                      "compiler")
    found = dict(re.findall(r"(\w+_OPTIONS)=(\S+)", options))
    for name in SYMBOLIZER_OPTIONS:
        value = dict(part.split("=", 1) for part in found.get(name, "").split(":") if "=" in part)
        if value.get("external_symbolizer_path", "") != beside[1]:
            raise Refused("symbolizer", where, f"{name} does not set external_symbolizer_path to "
                          "the llvm-symbolizer beside the compiler")
    return SYMBOLIZER_OPTIONS


def read_status(status, root, logs):
    lines = [line for line in status.read_text(encoding="utf-8").splitlines() if line.strip()]
    where = status.name
    if not lines or not ALL_DONE.match(lines[-1]):
        raise Refused("no-all-done", where, f"the last line is {lines[-1] if lines else 'none'!r}")
    first = FIRST.match(lines[0])
    if first is None:
        raise Refused("status", where, "line 1 is not `<UTC> commit <sha> tree <path>`")
    builds = [BUILD.match(line) for line in lines if " build done" in line]
    if len(builds) != 1 or builds[0] is None:
        raise Refused("status", where, "no one `build done` line with flags, seconds and options")
    flags, seconds, at_once, options = builds[0].groups()
    if int(seconds) < SECONDS:
        raise Refused("short", where, f"{seconds} s a target, under {SECONDS}")
    symbolizer_options(options, where, logs)
    targets = {}
    for line in lines:
        match = TARGET.match(line)
        if match is None:
            continue
        start, end, name, code, executions, crashes = match.groups()
        if name in targets:
            raise Refused("target-missing", where, f"{name} has two status lines")
        targets[name] = {"start": start, "end": end, "exit": int(code),
                         "executions": int(executions), "crashes": int(crashes)}
    expected = fuzz_targets(root)
    missing = [name for name in expected if name not in targets]
    extra = [name for name in targets if name not in expected]
    if missing or extra:
        raise Refused("target-missing", where, f"no status line for {missing}; "
                      f"lines for targets fuzz/CMakeLists.txt does not add: {extra}")
    return {"started": first[1], "commit": first[2], "flags": flags, "seconds": int(seconds),
            "at_once": int(at_once), "done": ALL_DONE.match(lines[-1])[1],
            "targets": [(name, targets[name]) for name in expected]}


def judge_target(name, row, logs, found, seconds):
    """Refuses a target that is not clean; gives its log's Done seconds."""
    if row["exit"] != 0:
        raise Refused("exit", name, f"exit {row['exit']}")
    if row["crashes"] > 0:
        raise Refused("crashes", name, f"{row['crashes']} crashes")
    span = (when(row["end"]) - when(row["start"])).total_seconds()
    if span < seconds:
        raise Refused("short", name, f"{span:.0f} s from start to end, under {seconds}")
    log = logs / f"{name}.log"
    if not log.is_file():
        raise Refused("log", name, f"no raw log {log.name}")
    text = log.read_text(encoding="utf-8", errors="replace")
    bad = BAD_LOG_LINE.search(text)
    if bad:
        line = text[text.rfind("\n", 0, bad.start()) + 1:].split("\n", 1)[0]
        raise Refused("log", name, f"the log holds {line.strip()[:120]!r}")
    done = DONE.findall(text)
    if len(done) != 1:
        raise Refused("log", name, f"{len(done)} `Done <N> runs in <S> second(s)` lines, not 1")
    runs, ran = int(done[0][0]), int(done[0][1])
    if ran < seconds:
        raise Refused("short", name, f"the log's Done line gives {ran} s, under {seconds}")
    if runs != row["executions"]:
        raise Refused("log", name, f"the log's Done line gives {runs} runs, the status line "
                      f"{row['executions']} executions")
    saved = sorted(path.name for path in found.glob(f"{name}-*")) if found.is_dir() else []
    if saved:
        raise Refused("found", name, f"inputs saved: {saved[:3]}")
    return ran


def compiler(logs):
    path = logs / "build.log"
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    match = COMPILER.search(text)
    if match is None:
        raise Refused("status", "build.log", "no compiler identification line")
    return match[1].strip()


def hhmmss(stamp):
    return stamp[11:]


def render(run, host, compiler_name, seconds_ran, run_id):
    targets = run["targets"]
    first = min(row["start"] for _, row in targets)
    last = max(row["end"] for _, row in targets)
    day = first[:10]
    flags = run["flags"]
    sanitize = re.search(r"ITCH_SANITIZE=(\S+)", flags)
    extra = re.findall(r"(?<!\S)(-f\S+)", flags)
    build = re.sub(r"\s*ITCH_SANITIZE=\S+", "", flags)
    build = re.sub(r"\s+-f\S+", "", build).strip()
    build = re.sub(r" (?=[A-Z_]+=)", ", ", build)
    lines = [
        f"# The fuzzers, {run['seconds']:,} s each, on {host}",
        "",
        "Generated by `scripts/fuzz_report.py` from the run's status file and each target's raw "
        "libFuzzer log, which are not committed. Do not edit by hand.",
        "",
        f"- Commit: {run['commit'][:12]}, the code fuzzed: every target and all it exercises are "
        f"those of {run['commit']}",
        f"- Run: {run_id}, its row in results/run_log.md; no prediction, since it is a check, not "
        "a measurement",
        f"- Date: {day}, the fuzzing from {hhmmss(first)} to {hhmmss(last)}, the status file "
        f"closed with ALL DONE at {hhmmss(run['done'])}",
        f"- Host: {host}",
        f"- Compiler: {compiler_name}",
        f"- Build: the fuzz preset, {build}",
        f"- Sanitizers: {sanitize[1] if sanitize else 'none named'}, with "
        f"{', '.join(extra) or 'no -f flag'}",
        f"- Symbolizer: {' and '.join(SYMBOLIZER_OPTIONS)} set `external_symbolizer_path` to the "
        "toolchain's `llvm-symbolizer`, the one beside the compiler. On this Mac the system symbolizer waits on an "
        "authorization, and a fuzzer then hangs at its first NEW_FUNC line (docs/NOTES.md, "
        "\"No check waits on the system symbolizer\")",
        f"- Seconds a target: {run['seconds']:,}, libFuzzer's `-max_total_time`",
        f"- Schedule: {run['at_once']} targets at once, each through heavy.sh, which holds a job "
        "off while a measurement has the machine; each from its committed seeds in "
        "`fuzz/corpus/<target>/` into an empty scratch corpus",
        f"- Targets: {len(targets)}, every one `fuzz/CMakeLists.txt` adds",
        f"- Result: {len(targets)} of {len(targets)} clean",
        "",
        "## Each target",
        "",
        "Seconds are from the log's `Done` line; executions are libFuzzer's runs, the same "
        "count in the status line and the log. Inputs saved counts the crash, leak, timeout and "
        "out-of-memory inputs libFuzzer kept under the target's name.",
        "",
        "| " + " | ".join(HEADS) + " |",
        "|" + "---|" * len(HEADS),
    ]
    for name, row in targets:
        lines.append(f"| `{name}` | {hhmmss(row['start'])} | {hhmmss(row['end'])} | "
                     f"{seconds_ran[name]:,} | {row['executions']:,} | {row['exit']} | "
                     f"{row['crashes']} | 0 |")
    lines += [
        "",
        "## What clean means",
        "",
        CLEAN,
        "",
        "## What it does not show",
        "",
        "- A count of executions is not a measure of coverage, and the targets' counts are not "
        "comparable: each input of `fuzz_book` is a stream of order messages replayed through "
        "every arm of the fast book in turn and through the reference book, the two compared in "
        "full after every message, while `fuzz_decode` decodes one message of each type an "
        "input.",
        "- `fuzz_kernels` ran the parsing kernels this host builds. Kernels for another "
        "architecture are not built here, so this run did not fuzz them.",
        f"- Nothing here is timed. The run says only that no target failed in "
        f"{run['seconds']:,} s at the commit above.",
        "",
    ]
    return "\n".join(lines)


def write(args):
    root = args.root.resolve()
    if args.host not in OS_OF_HOST:
        raise Refused("status", "--host", f"{args.host!r} is not one of {sorted(OS_OF_HOST)}")
    run = read_status(args.status, root, args.logs)
    seconds_ran = {name: judge_target(name, row, args.logs, args.found, run["seconds"])
                   for name, row in run["targets"]}
    run_id = f"{RUN_PREFIX}-{run['started'].replace('-', '').replace(':', '')}"
    text = render(run, args.host, compiler(args.logs), seconds_ran, run_id)
    for code, where, detail in check_text(text, root, args.out.name):
        raise Refused(code, where, detail)
    if args.run_log is not None:
        log = args.run_log.read_text(encoding="utf-8") if args.run_log.is_file() else ""
        if f"| {run_id} |" in log:
            raise Refused("status", args.run_log.name, f"{run_id} is already logged")
    args.out.write_text(text, encoding="utf-8")
    if args.run_log is not None:
        with open(args.run_log, "a", encoding="utf-8") as log:
            log.write(f"| {run_id} | none: a check, not a measurement | {run['commit'][:12]} | "
                      f"{OS_OF_HOST[args.host]} | published: results/{args.out.name}; "
                      f"{len(seconds_ran)} of {len(seconds_ran)} targets clean, "
                      f"{run['seconds']:,} s each |\n")
    print(f"fuzz_report: wrote {args.out.name}, run {run_id}")
    return 0


def check_text(text, root, where):
    """Each finding as (code, where, detail)."""
    if check_results.HOME_PATH.search(text) or check_results.ADDRESS.search(text):
        yield "status", where, "a home path or an address"
    rows = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("| `") and len(cells) == len(HEADS):
            rows[cells[0].strip("`")] = cells
    try:
        expected = fuzz_targets(root)
    except Refused as refused:
        yield "target-missing", where, str(refused)
        return
    missing = [name for name in expected if name not in rows]
    if missing or len(rows) != len(expected):
        yield "target-missing", where, f"no row for {missing}, {len(rows)} rows"
    for name, cells in rows.items():
        seconds, exit_code, crashes, saved = (int(cells[i].replace(",", "")) for i in (3, 5, 6, 7))
        if exit_code != 0:
            yield "exit", where, f"{name} exit {exit_code}"
        if crashes > 0 or saved > 0:
            yield "crashes", where, f"{name}: {crashes} crashes, {saved} inputs saved"
        if seconds < SECONDS:
            yield "short", where, f"{name}: {seconds} s"
    result = re.search(r"^- Result: (\d+) of (\d+) clean$", text, re.MULTILINE)
    if result is None or int(result[1]) != len(rows) or int(result[2]) != len(rows):
        yield "status", where, "the Result line is not the count of its rows"
    if CLEAN not in text:
        yield "status", where, "it does not say what clean means"


def check(args):
    failed = False
    for path in args.files:
        for code, where, detail in check_text(path.read_text(encoding="utf-8"),
                                              args.root.resolve(), path.name):
            print(f"fuzz_report: FAIL {code} {where}: {detail}")
            failed = True
    if not failed:
        print(f"fuzz_report: ok, {len(args.files)} file(s)")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    written = sub.add_parser("write")
    written.add_argument("--status", type=Path, required=True)
    written.add_argument("--logs", type=Path, required=True)
    written.add_argument("--found", type=Path, required=True)
    written.add_argument("--host", required=True)
    written.add_argument("--out", type=Path, required=True)
    written.add_argument("--run-log", type=Path)
    written.add_argument("--root", type=Path, default=REPO_ROOT)
    checked = sub.add_parser("check")
    checked.add_argument("files", type=Path, nargs="+")
    checked.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    try:
        return write(args) if args.command == "write" else check(args)
    except Refused as refused:
        print(refused)
        return 1
    except OSError as error:
        print(f"fuzz_report: FAIL status {getattr(error, 'filename', '')}: {error.strerror}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
