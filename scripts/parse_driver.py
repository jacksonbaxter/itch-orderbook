#!/usr/bin/env python3
"""Runs bench_parse in fresh processes and writes the parse rate's results file (T-PARSE-1 to
T-PARSE-3).

    parse_driver.py --bench BENCH_PARSE --probe BENCH_PROBE --input FILE --prediction P-NNNN \\
        --raw-dir DIR --out results/parse_rate_debian_DAY.md [--repetitions N] [--rounds N] \\
        [--core N] [--sibling N] [--sampler-cpu N] [--wrapped-by TEXT] [--allow-dirty]
    parse_driver.py --check FILE...
    parse_driver.py --inline-check BENCH_PARSE [--asm-out FILE]

On a reported run this is measure.sh's direct child, as scripts/backtest_driver.py is, with the
same order, signal handling and Linux sampler rule:

    measure.sh taskset -c 4 python3 -B scripts/parse_driver.py ... --core 4 --sibling 5 2> STDERR
    python3 -B scripts/stamp_load.py --results FILE --stderr STDERR --raw-dir RAW/RUN_ID

Refuse an input scripts/data_manifest.json does not pin, and a bench_parse whose timed loops
make a call (--inline-check, below); hash the input, which also faults it in; confirm the
prediction is on record; run machine_check.sh, refusing on a failure; start scripts/linux_sampler.py; run the repetitions,
one bench_parse process each, the file read back into the page cache before each, outside every
process's span, since bench_parse drops the cache as it copies the day into memory. A process is
published only if the Linux sampler rule passes every second of its span (docs/NOTES.md, "The
Linux sampler rule"); otherwise it is withdrawn, its raw files set aside, with its own row in the
run log, and run again, never for its numbers, at most --most-in-a-row times running and
--most-withdrawn in all. Refuse if a process refused, if the processes disagree on a count or a
fold, if a count is not the one scripts/data_manifest.json pins for the day, or if the kernel
swapped a page out; write the results file with its load lines pending.

The figures. Each pass's rate is every framed message over the pass's seconds, and its GB/s is
the file's bytes, length prefixes included, over the same seconds, in 10^9 bytes: so the
sources count (itch-book's 709 M msg/s at ~21.8 GB/s is 8,251,407,909 bytes over 268,744,780
messages; Lunary's 93.9 M at 2.89 GB/s is 9,035,016,843 over 293,989,079). The summary is the
median over every timed pass of the run, with min, max and CV; the best pass is given apart,
labelled, since both of itch-book's figures are the best of 3. Only debian is measured: a run
anywhere else is refused before anything is timed.

--check FILE... recomputes every figure of a results file from its table of timed passes, the
summary, the best passes and each verdict, holds every pass's messages and the file's bytes to
the frame count and to the manifest's day, works the caveats' table again from the manifest,
and exits 1 on any that differ.

--inline-check BENCH_PARSE disassembles the binary with objdump and refuses a call, or a jump
to another function, in any of the three pass functions (bench/parse_passes.hpp), each of which
is the whole of a timed span's work: a timed loop holds no call. A jump through a table, as a
switch makes, is not a call. --asm-out keeps the three functions' code.

Every run gets a row in results/run_log.md, whatever became of it. Raw output stays in
--raw-dir/<run id> with run.json (scripts/run_record.py). None of it is committed.

Exit codes: 0 results written (or checked), 1 refused or failed, 2 bad usage, 128 + N stopped
by signal N.
"""

from __future__ import annotations

import argparse
import json
import mmap
import os
import platform
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_driver  # noqa: E402
import check_results  # noqa: E402
import linux_sampler  # noqa: E402
import process_kinds  # noqa: E402
import run_record  # noqa: E402

REPO_ROOT = bench_driver.REPO_ROOT
Refused = bench_driver.Refused
Stopped = bench_driver.Stopped
MEGA = 1e6
GIGA = 1e9
MOST_IN_A_ROW = 3
MOST_WITHDRAWN = 20
QUIET_BEFORE_S = 10
QUIET_WAIT_S = 600
PAGE = 4096

# Each pass, its target and the bars BENCHMARKS.md sets: (msg/s in millions, GB/s).
PASSES = (
    ("framing", "T-PARSE-1", "the framing walk", 709.0, 21.8),
    ("decode", "T-PARSE-2", "itch-book's decode of its ten types", 199.0, 6.1),
    ("strict", "T-PARSE-3", "the strict decode of every field", 93.9, 2.89),
)
TEN_TYPES = "SRAFECXDUP"
BAR_DAY = {"T-PARSE-3": "10302019"}  # A bar set on another day than the one measured.
PARSE_FIELD = "parse_passes"
PASS_ROW = re.compile(r"^\| (\d+) \| (\d+) \| (framing|decode|strict) \| ([\d.]+) \| ([\d,]+) \| "
                      r"[\d,]+ \| [\d.]+ \| ([\d,]+) \|$")

last_pid = None


def utc_now():
    return bench_driver.utc_now()


# figures

def rate(messages, seconds):
    return messages / seconds


def gb_rate(file_bytes, seconds):
    return file_bytes / seconds / GIGA


def spread(values):
    """median, min, max and CV in percent, as bench_driver.py works them."""
    median, low, high, cv = bench_driver.spread(values)
    return median, low, high, cv * 100


def figures(passes, file_bytes):
    """For each pass name, its per-pass msg/s and GB/s, in the order run."""
    out = {name: {"msgs": [], "gbs": []} for name, *_ in PASSES}
    for p in passes:
        out[p["pass"]]["msgs"].append(rate(p["messages"], p["seconds"]))
        out[p["pass"]]["gbs"].append(gb_rate(file_bytes, p["seconds"]))
    return out


def verdict(target, msgs_median, gbs_median, bar_msgs, bar_gbs, day):
    """met only if both medians reach their bars; a bar set on another day says so."""
    short = []
    if msgs_median / MEGA < bar_msgs:
        short.append(f"{shortfall(bar_msgs, msgs_median / MEGA)} M msg/s")
    if gbs_median < bar_gbs:
        short.append(f"{shortfall(bar_gbs, gbs_median)} GB/s")
    text = "met" if not short else f"missed: {' and '.join(short)} under the bar"
    other = BAR_DAY.get(target)
    if other and other != day:
        text += (f"; measured on {day}, not the bar's {other}, which is not on this host: the "
                 "two days are compared under Caveats")
    return text


def shortfall(bar, value):
    """How far under its bar a median is, never printed as 0.00: a miss is a miss."""
    text = f"{bar - value:,.2f}"
    return text if text != "0.00" else f"{bar - value:.2g}"


# the timed loops' code

PASS_SYMBOLS = {"frame_walk": "5parse10frame_walkE", "book_decode": "5parse11book_decodeE",
                "strict_decode": "5parse13strict_decodeE"}
FUNCTION_LINE = re.compile(r"^([0-9a-f]+) <(\S+)>:$")
INSTRUCTION = re.compile(r"^\s*[0-9a-f]+:\s+(.*)$")
PREFIXES = {"notrack", "bnd", "lock", "rep", "repz", "repnz", "repe", "repne", "data16", "cs",
            "ds"}
CALLS = re.compile(r"^(call[lqw]?|bl|blr|blraaz?|blrabz?)$")
BRANCHES = re.compile(r"^(j[a-z]+|b|b\.[a-z]+|cbn?z|tbn?z)$")
TARGET = re.compile(r"<([^>+]+)(\+0x[0-9a-f]+)?>")


def pass_functions(disassembly):
    """Each pass's own code in objdump -d's text: {pass: [(symbol, [instruction lines])]}, the
    function and any clone the compiler made of it (.isra, .constprop, .part), each checked as
    the pass itself. A .cold part is not taken in: a branch into it is a branch to another
    function, refused. GCC 14.2 and Clang 23.1.4 on debian, and Clang on the Mac, made neither
    (P4 parse asm/)."""
    found, current = {}, None
    for line in disassembly.splitlines():
        header = FUNCTION_LINE.match(line)
        if header:
            symbol = header.group(2)
            name = next((n for n, mangled in PASS_SYMBOLS.items() if mangled in symbol
                         and ".cold" not in symbol), None)
            current = None
            if name is not None:
                current = (symbol, [])
                found.setdefault(name, []).append(current)
            continue
        if current is not None and INSTRUCTION.match(line):
            current[1].append(line)
    return found


def loop_calls(symbol, lines):
    """The instructions of one pass's code that leave it: every call, and any branch to
    another function."""
    out = []
    for line in lines:
        words = INSTRUCTION.match(line).group(1).replace("\t", " ").split()
        while words and words[0] in PREFIXES:
            words = words[1:]
        if not words:
            continue
        mnemonic = words[0]
        target = TARGET.search(line)
        if CALLS.match(mnemonic):
            out.append(line.strip())
        elif BRANCHES.match(mnemonic) and target and target.group(1) != symbol:
            out.append(line.strip())
    return out


def inline_verdicts(disassembly):
    """(lines to print, problems) for the three passes' code."""
    found = pass_functions(disassembly)
    lines, problems = [], []
    for name in PASS_SYMBOLS:
        if name not in found:
            problems.append(f"{name}: not in the binary as a function of its own")
            continue
        calls = [call for symbol, code in found[name] for call in loop_calls(symbol, code)]
        size = sum(len(code) for _, code in found[name])
        if calls:
            problems.append(f"{name}: {len(calls)} instructions leave the pass: "
                            + "; ".join(calls[:5]))
        elif len(found[name]) == 1:
            lines.append(f"{name}: {size} instructions, no call and no jump to another function")
        else:
            lines.append(f"{name}: {size} instructions in {len(found[name])} functions, the pass "
                         "and its clones, no call and no jump to another function")
    return lines, problems


def disassemble(bench):
    tool = shutil.which("objdump")
    if tool is None:
        raise Refused("inline", "no objdump on this host, so the timed loops' code is not read")
    done = subprocess.run([tool, "-d", "--no-show-raw-insn", str(bench)], capture_output=True,
                          text=True, check=False)
    if done.returncode != 0:
        raise Refused("inline", f"objdump exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def pass_code(disassembly):
    """The three passes' code, as objdump gave it."""
    found = pass_functions(disassembly)
    out = []
    for name in PASS_SYMBOLS:
        for symbol, code in found.get(name, []):
            out += [f"<{symbol}>:", *code, ""]
    return "\n".join(out)


def inline_check(bench):
    """The timed loops' code: lines for the results file, or Refused."""
    disassembly = disassemble(bench)
    lines, problems = inline_verdicts(disassembly)
    if problems:
        raise Refused("inline", "; ".join(problems))
    return lines, disassembly


def inline_main(argv):
    if not argv or len(argv) not in (1, 3) or (len(argv) == 3 and argv[1] != "--asm-out"):
        print("usage: parse_driver.py --inline-check BENCH_PARSE [--asm-out FILE]",
              file=sys.stderr)
        return 2
    try:
        disassembly = disassemble(argv[0])
    except Refused as refusal:
        print(f"parse_driver: FAIL inline {refusal}")
        return 1
    if len(argv) == 3:
        Path(argv[2]).write_text(pass_code(disassembly), encoding="utf-8")
    lines, problems = inline_verdicts(disassembly)
    for line in lines:
        print(f"parse_driver: ok inline {line}")
    for problem in problems:
        print(f"parse_driver: FAIL inline {problem}")
    return 1 if problems else 0


# the run

def fault_in(path):
    """Reads every page of the file, so that the next process copies it from the page cache."""
    with open(path, "rb") as file, mmap.mmap(file.fileno(), 0, prot=mmap.PROT_READ) as mapped:
        total = 0
        for at in range(0, len(mapped), PAGE):
            total += mapped[at]
    return total


def run_process(args, index, commit, digest):
    """One bench_parse process."""
    global last_pid
    stem = f"parse-repetition-{index}"
    record_path = args.raw_dir / f"{stem}.json"
    log_path = args.raw_dir / f"{stem}.log"
    record_path.unlink(missing_ok=True)
    command = [str(args.bench), "--input", str(args.input), "--out", str(record_path),
               "--rounds", str(args.rounds), "--commit", commit, "--dataset", args.input.name,
               "--dataset-sha256", digest]
    if args.core is not None:
        command += ["--core", str(args.core)]
    where = f"repetition {index}"
    bench_driver.set_stage(where)
    with open(log_path, "w", encoding="utf-8") as log:
        bench_driver.current_child = subprocess.Popen(command, stdout=log,
                                                      stderr=subprocess.STDOUT)
        last_pid = bench_driver.current_child.pid
        code = bench_driver.current_child.wait()
        bench_driver.current_child = None
    if bench_driver.swap_watch is not None:
        bench_driver.swap_watch.check(where)
    if not record_path.is_file():
        raise Refused(where, f"exited {code} with no record")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if code != 0 or record["refusals"]:
        raise Refused(where, f"exited {code}: {'; '.join(record['refusals']) or 'no reason given'}")
    return record


def validate_run(record, args, manifest_day):
    """What one record must show for its numbers to mean anything."""
    ref = record["reference"]
    passes = record["passes"]
    if len(passes) != args.rounds * len(PASSES):
        raise Refused("record", f"{len(passes)} timed passes, not {args.rounds * len(PASSES)}")
    if record["bytes"] != ref["consumed"]:
        raise Refused("record", "the frame reader did not walk the whole input")
    for p in passes:
        if p["totals"] != record["warmup"][p["pass"]] or p["totals"]["messages"] != ref["frames"]:
            raise Refused("record", f"a {p['pass']} pass is not its warm-up's")
        if p["seconds"] <= 0:
            raise Refused("record", f"a {p['pass']} pass took no time")
    pinned = {"bytes": manifest_day["unpacked_bytes"], "frames": manifest_day["messages"],
              "decode_types": sum(manifest_day["per_type_counts"].get(t, 0) for t in TEN_TYPES)}
    found = {"bytes": record["bytes"], "frames": ref["frames"],
             "decode_types": ref["decode_types"]}
    for name, value in pinned.items():
        if found[name] != value:
            raise Refused("record", f"{name} {found[name]:,}, not the manifest's {value:,}")


def validate_together(records):
    first = records[0]
    for index, record in enumerate(records[1:], start=2):
        if record["warmup"] != first["warmup"] or record["reference"] != first["reference"]:
            raise Refused("record", f"repetition {index} differs from repetition 1")


def wait_for_quiet(watch, args):
    if args.quiet_before <= 0:
        return
    bench_driver.set_stage("waiting for the host to be quiet")
    give_up = time.monotonic() + args.quiet_wait
    while not watch.quiet_for(args.quiet_before):
        if time.monotonic() >= give_up:
            raise Refused("waiting for the host to be quiet",
                          f"the sampler never saw {args.quiet_before:g} s that pass the Linux "
                          f"sampler rule in {args.quiet_wait:g} s")
        time.sleep(watch.every)


MOST_OF_IT = re.compile(r" at (\S+Z), the most of it (.+)$")
KINDS_UNREADABLE = (f"${process_kinds.MAP_ENV} names a map of process kinds that cannot be read, "
                    "or that gives a kind not in process_kinds.KINDS")
# The program names the sampler has logged in this run: never in a row (scrub()).
seen_names = set()


def check_kinds_map():
    """The map of process kinds loaded and held to process_kinds.KINDS before anything is
    timed, so that a bad map is refused at the start and never met at a withdrawal."""
    try:
        process_kinds.default_map()
    except process_kinds.MapError:
        raise Refused("kinds", KINDS_UNREADABLE) from None


def note_names(lines):
    """Keeps the program of every busy line, so that scrub() can keep it out of a row."""
    for words in (line.split(None, 4) for line in lines):
        if len(words) == 5 and words[1] == "busy":
            command = words[4].partition(": ")[2].rsplit(" [", 1)[0]
            for name in (command.split()[:1] + [process_kinds.program_of(command)]):
                if len(name) > 1:
                    seen_names.add(name)


def scrub(text):
    """A row's text with the map's path, and every program name the map or the sampler gave in
    this run, taken out: whatever failed, a row says no more than a kind."""
    path = os.environ.get(process_kinds.MAP_ENV)
    if path:
        for form in {path, os.path.abspath(path), os.path.realpath(path)}:
            text = text.replace(form, f"${process_kinds.MAP_ENV}")
    names = set(seen_names)
    try:
        kinds = process_kinds.default_map()
    except process_kinds.MapError:
        kinds = None
    if kinds:
        names |= {name for name in kinds["programs"] if len(name) > 1}
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", process_kinds.OTHER, text)
    return text


def log_row(args, run_id, commit, outcome):
    bench_driver.append_log(args.run_log, run_id, args.prediction, commit, scrub(outcome))


def public_why(why, lines):
    """A withdrawal's reason as the run log and the results file carry it: what the sampler saw,
    with its share and time, and the busiest other process by its kind (scripts/process_kinds.py)
    at its own share of a CPU, never by its name. The kind is the map's ($ITCH_PROCESS_KINDS)
    for the command the sampler logged, or, where the map gives none, the one the sampler told
    from /proc. sampler.txt, in the raw folder, keeps the name."""
    match = MOST_OF_IT.search(why)
    if match is None:
        return why
    stamp = match.group(1)
    busy = next((words for words in (line.split(None, 4) for line in lines)
                 if len(words) == 5 and words[0] == stamp and words[1] == "busy"), None)
    if busy is None:  # No process above the sampler's floor: kernel or interrupt work.
        return why
    told, _, rest = busy[4].partition(": ")
    command = rest.rsplit(" [", 1)[0]
    try:
        kind = process_kinds.kind_of(command)
    except process_kinds.MapError:  # check_kinds_map() refused a bad map before this.
        kind = process_kinds.OTHER
    if kind == process_kinds.OTHER:
        kind = linux_sampler.EARLIER_WORDS.get(told, told)
    if kind not in process_kinds.KINDS:
        kind = process_kinds.OTHER
    return f"{why[:match.start(2)]}{kind}, at {busy[2]} of a CPU"


def withdraw(args, index, begin, why, sampled, commit):
    """A process the rule does not publish: its files set aside and its own row."""
    sampled["withdrawn"].append((f"repetition {index}", begin, why))
    count = len(sampled["withdrawn"])
    for suffix in (".json", ".log"):
        path = args.raw_dir / f"parse-repetition-{index}{suffix}"
        if path.is_file():
            path.rename(args.raw_dir / f"parse-repetition-{index}-withdrawn-{count}{suffix}")
    run_id = f"{args.prediction}-{begin[:19].replace(':', '').replace('-', '')}Z"
    log_row(
        args, run_id, commit,
        f"withdrawn: T-PARSE-1 to T-PARSE-3, repetition {index}, exit 0; {why}, inside its span, "
        f"so the Linux sampler rule does not publish it; nothing from it is published, and it "
        f"is rerun under {args.prediction}; the row was written by the driver")
    print(f"parse_driver: repetition {index} withdrawn: {why}", file=sys.stderr)
    if count > args.most_withdrawn:
        raise Refused(f"repetition {index}", f"{count} processes withdrawn under the Linux "
                                             f"sampler rule, over the {args.most_withdrawn} allowed")


def judged_process(args, index, commit, digest, watch, sampled, manifest_day):
    """One repetition, run again until the Linux sampler rule publishes it."""
    in_a_row = 0
    while True:
        bench_driver.set_stage(f"reading the input back into the page cache, repetition {index}")
        fault_in(args.input)
        wait_for_quiet(watch, args)
        begin = linux_sampler.utc_now()
        record = run_process(args, index, commit, digest)
        validate_run(record, args, manifest_day)
        end = linux_sampler.utc_now()
        watch.wait_past(end)
        lines = watch.lines()
        note_names(lines)
        why = linux_sampler.judge(lines, begin, end, last_pid)
        if why is not None:
            why = public_why(why, lines)
        if why is None:
            sampled["published"].append((index, begin, end, linux_sampler.summary(
                lines, begin, end, args.core, args.sibling)))
            return record
        withdraw(args, index, begin, why, sampled, commit)
        in_a_row += 1
        if in_a_row >= args.most_in_a_row:
            raise Refused(f"repetition {index}",
                          f"withdrawn {in_a_row} times in a row under the Linux sampler rule, "
                          "so the host is given back and the run is tried again later")


def manifest_days():
    return json.loads(run_record.MANIFEST.read_text(encoding="utf-8")).get("days", {})


def manifest_entry(name):
    return next((entry for entry in manifest_days().values() if entry.get("name") == name), None)


def run(args, run_id, started, commit):
    if platform.system() != "Linux":
        raise Refused("host", "the parse rate is measured on debian only; this host is not "
                              "measured")
    if args.core is None or args.sibling is None:
        raise Refused("sampler", "the Linux sampler rule needs --core and --sibling")
    bench_driver.set_stage("reading the map of process kinds")
    check_kinds_map()
    manifest_day = manifest_entry(args.input.name)
    if manifest_day is None:
        raise Refused("manifest", f"{args.input.name} is not a day scripts/data_manifest.json "
                                  "pins, so its counts could not be held to anything")
    bench_driver.set_stage("reading the timed loops' code")
    loops, disassembly = inline_check(args.bench)
    args.record.output("passes.asm", pass_code(disassembly))
    bench_driver.swap_watch = bench_driver.SwapWatch(args.vmstat)
    bench_driver.set_stage("hashing the input")
    digest = bench_driver.hash_and_fault_in(args.input)
    args.record.dataset(args.input, digest)
    bench_driver.set_stage("checking the prediction")
    code = subprocess.run([sys.executable, "-B", str(REPO_ROOT / "scripts" / "check_predictions.py"),
                           "--root", str(args.predictions_root), "--require", args.prediction],
                          capture_output=True, text=True, check=False)
    if code.returncode != 0:
        raise Refused("prediction", code.stdout.strip())
    bench_driver.set_stage("machine_check")
    machine_output = bench_driver.machine_check(args)
    args.record.output(bench_driver.MACHINE_CHECK, machine_output)
    bench_driver.set_stage("starting the sampler")
    watch, cpu = linux_sampler.Watch.start(args.raw_dir / "sampler.txt", args.core, args.sibling,
                                           args.sampler_cpu)
    sampled = {"every": watch.every, "cpu": cpu, "published": [], "withdrawn": []}
    try:
        records = [judged_process(args, index, commit, digest, watch, sampled, manifest_day)
                   for index in range(1, args.repetitions + 1)]
    finally:
        watch.stop()
        args.record.kept_file("sampler.txt")
    validate_together(records)
    bench_driver.set_stage("rendering the results file")
    text = render(records, args, run_id, started, utc_now(), machine_output,
                  relative_command(args), sampled, loops)
    violations = [found for found in check_results.check_text(text)
                  if found[0] not in check_results.LEFT_TO_STAMP]
    if violations:
        raise Refused("results", f"{args.out.name}: " + "; ".join(
            f"{code}: {detail}" for code, detail in violations))
    bench_driver.hold_signals()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    return args.out


# the file

def timed_passes(records):
    """Every timed pass of the run, in the order run, with its repetition."""
    return [{"repetition": index, "round": p["round"], "pass": p["pass"],
             "seconds": p["seconds"], "messages": p["totals"]["messages"],
             "fold": p["totals"]["fold"]}
            for index, record in enumerate(records, start=1) for p in record["passes"]]


def summary_rows(passes, file_bytes):
    rows = []
    found = figures(passes, file_bytes)
    for name, target, what, _, _ in PASSES:
        for key, unit, form in (("msgs", "messages/s", ",.0f"), ("gbs", "GB/s", ".3f")):
            median, low, high, cv = spread(found[name][key])
            rows.append(f"| {target}, {what}, {unit} | {median:{form}} | {low:{form}} | "
                        f"{high:{form}} | {cv:.2f}% |")
    return rows


def best_heading(passes):
    each = len(passes) // len(PASSES)
    return f"| Target | Best of {each} passes, messages/s | Best of {each} passes, GB/s |"


def best_rows(passes, file_bytes):
    found = figures(passes, file_bytes)
    return [f"| {target} | {max(found[name]['msgs']):,.0f} | {max(found[name]['gbs']):.3f} |"
            for name, target, *_ in PASSES]


def target_rows(passes, file_bytes, day):
    found = figures(passes, file_bytes)
    rows = []
    for name, target, _, bar_msgs, bar_gbs in PASSES:
        msgs = statistics.median(found[name]["msgs"])
        gbs = statistics.median(found[name]["gbs"])
        bar = f"≥ {bar_msgs:g} M msg/s and {bar_gbs:g} GB/s"
        if target in BAR_DAY:
            bar += f", on {BAR_DAY[target]}"
        rows.append(f"| {target} | {bar} | {msgs / MEGA:,.2f} M msg/s, {gbs:.3f} GB/s, the "
                    f"median | {verdict(target, msgs, gbs, bar_msgs, bar_gbs, day)} |")
    return rows


def pass_rows(passes, file_bytes):
    return [f"| {p['repetition']} | {p['round']} | {p['pass']} | {p['seconds']:.9f} | "
            f"{p['messages']:,} | {rate(p['messages'], p['seconds']):,.0f} | "
            f"{gb_rate(file_bytes, p['seconds']):.3f} | {p['fold']:,} |"
            for p in passes]


def caveat_rows(day):
    """The measured day beside each other day a bar was set on, from the manifest's raw
    counts: bytes, messages, bytes a message, and each type's share of the messages."""
    days = manifest_days()
    others = sorted({other for other in BAR_DAY.values() if other != day})
    if not others or day not in days or any(other not in days for other in others):
        return None
    shown = [days[other] for other in others] + [days[day]]
    heading = ("| | " + " | ".join(f"{other}, the bar's day" for other in others)
               + f" | {day}, measured |")
    rows = [heading, "|---|" + "---|" * len(shown),
            "| Bytes | " + " | ".join(f"{d['unpacked_bytes']:,}" for d in shown) + " |",
            "| Messages | " + " | ".join(f"{d['messages']:,}" for d in shown) + " |",
            "| Bytes a message | " + " | ".join(f"{d['unpacked_bytes'] / d['messages']:.3f}"
                                               for d in shown) + " |"]
    types = sorted({t for d in shown for t in d["per_type_counts"]},
                   key=lambda t: (-max(d["per_type_counts"].get(t, 0) / d["messages"]
                                       for d in shown), t))
    for t in types:
        cells = []
        for d in shown:
            count = d["per_type_counts"].get(t, 0)
            cells.append(f"{count:,}, {count / d['messages'] * 100:.3f}%")
        rows.append(f"| {t} | " + " | ".join(cells) + " |")
    return rows


def caveat_lines(day, loops):
    lines = ["## Caveats", "",
             "- T-PARSE-1 is a transcription of the work of itch-book's `scan()`, its `parse()` "
             "loop with a handler that takes nothing: per frame the length prefix, a stop at a "
             "zero-length frame, the type byte, the type's length looked up and compared, and "
             "one of three counters. The work is the same; what differs is the compiler and the "
             "host, the clock (rdtsc, one span a pass) and the figure the verdict is on (the "
             "median, not the best of 3).",
             "- T-PARSE-2 is the same loop with the ten types itch-book's Checksum handler takes "
             "decoded and the fields it adds folded, and one add a message more than the source "
             "makes: the count of messages decoded, which bench_parse holds to the frame "
             "reader's count.",
             "- T-PARSE-3: the strict decode reads the variant `itch::decode()` returns by a "
             "switch on its index, not `std::visit`, whose table of functions would put an "
             "indirect call in the loop. Lunary decoded the day memory-mapped; bench_parse, as "
             "itch-book does, reads a copy into anonymous memory before the warm-up, and every "
             "page of it was found resident before and after each timed pass; bench_parse "
             "counts no page faults. Lunary's bar is on "
             f"{BAR_DAY['T-PARSE-3']}, which is not on this host"]
    table = caveat_rows(day)
    if table:
        lines[-1] += ("; the days' sizes and each type's share of their messages, from "
                      "scripts/data_manifest.json:")
        lines += [""] + table
    else:
        lines[-1] += "."
    lines += ["", "The timed loops, as objdump read them from the bench_parse that ran "
              "(`parse_driver.py --inline-check`): each pass is one function, and none makes a "
              "call or jumps to another function.", ""]
    lines += [f"- {line}" for line in loops]
    return lines


def count_rows(record):
    ref = record["reference"]
    warm = record["warmup"]
    return [f"| Bytes, the whole file | {record['bytes']:,} |",
            f"| Frames, every one a message | {ref['frames']:,} |",
            f"| Messages of the decode's ten types ({', '.join(TEN_TYPES)}) | "
            f"{ref['decode_types']:,} |",
            f"| Decode: decoded, rejected, fold | {warm['decode']['decoded']:,}, "
            f"{warm['decode']['rejected']:,}, {warm['decode']['fold']:,} |",
            f"| Strict decode: decoded, rejected, fold | {warm['strict']['decoded']:,}, "
            f"{warm['strict']['rejected']:,}, {warm['strict']['fold']:,} |"]


def load_rule_line(sampled):
    return (f"- load_rule: the Linux sampler rule was applied: sampled every "
            f"{sampled['every']:g} s from /proc by scripts/linux_sampler.py on cpu"
            f"{sampled['cpu']}, over each process's whole span; every published process quiet; "
            f"{len(sampled['withdrawn'])} withdrawn under the rule")


def sampler_section(sampled):
    lines = ["## Sampler", "",
             "The Linux sampler rule was applied (docs/NOTES.md, \"The Linux sampler rule\"): "
             f"`scripts/linux_sampler.py` ran beside the driver on cpu{sampled['cpu']}, reading "
             f"/proc every {sampled['every']:g} s, and a process is published only if the sampler "
             f"covered its whole span with no gap over {linux_sampler.LONGEST_GAP_S:g} s and saw, "
             "in every second of it, the measured process wait on the run queue for at most "
             f"{linux_sampler.CORE_WAIT_PCT:g}% of the second, the core's SMT sibling at most "
             f"{linux_sampler.SIBLING_PCT:g}% busy, and the other cores together at most "
             f"{linux_sampler.OTHERS_PCT:g}% of a CPU busy. A process that failed the rule was "
             "withdrawn, with its own row in results/run_log.md, and run again; none was "
             "withdrawn for its numbers.", "",
             "| Process | From | To | The sampler |", "|---|---|---|---|"]
    for index, begin, end, text in sampled["published"]:
        lines.append(f"| {index} | {begin} | {end} | {text} |")
    for count, (what, begin, why) in enumerate(sampled["withdrawn"], start=1):
        lines.append(f"| withdrawn {count}, {what} | {begin} | | {why} |")
    return lines


def header_lines(records, args, run_id, started, finished, sampled):
    first = records[0]
    lines = [f"- {name}: {first['header'][name]}" for name in bench_driver.HEADER_ORDER
             if name in first["header"]]
    lines += [f"- input: {first['input']}",
              f"- {PARSE_FIELD}: framing (T-PARSE-1), decode (T-PARSE-2), strict (T-PARSE-3)",
              f"- bytes: {first['bytes']:,}, the whole file, length prefixes included",
              f"- measure_load_start: {check_results.PENDING}",
              f"- measure_load_end: {check_results.PENDING}",
              load_rule_line(sampled),
              *bench_driver.peak_lines(records),
              f"- prediction: {args.prediction}",
              f"- run_id: {run_id}",
              f"- repetitions: {len(records)}",
              f"- rounds: {args.rounds}",
              f"- warmup_messages: {first['reference']['frames']:,}",
              f"- timed_messages: {first['reference']['frames']:,}",
              f"- run_started: {started}",
              f"- run_finished: {finished}"]
    return lines


def render(records, args, run_id, started, finished, machine_output, command, sampled, loops):
    first = records[0]
    host = first["header"]["host"]
    day = first["header"]["dataset"].split(".")[0]
    passes = timed_passes(records)
    file_bytes = first["bytes"]
    lines = [f"# T-PARSE-1 to T-PARSE-3: the parse rate, on {host}, {day}", "",
             "Generated by `scripts/parse_driver.py` from raw records that are not committed. "
             "Do not edit by hand.", "",
             f"Host: **{host}**. Numbers from the two hosts are never mixed. The Mac is not "
             "measured.", "",
             "Three passes over the whole day, held in memory, on one pinned thread, each as its "
             "target's source defines it (`bench/parse_passes.hpp`): the framing walk is "
             "itch-book's `scan()`, which reads each frame's length and type byte and counts it; "
             "the decode is the same loop with itch-book's ten types (S, R, A, F, E, C, X, D, U, "
             "P) decoded, adding the fields its Checksum handler adds; the strict decode runs "
             "`itch::decode()` on every frame and adds every field of every one of the 23 types. "
             "Each pass's rate is every framed message over its seconds; its GB/s is the whole "
             "file's bytes, length prefixes included, over the same seconds, in 10^9 bytes, as "
             "both sources count.", "",
             "## Header", ""]
    lines += header_lines(records, args, run_id, started, finished, sampled)
    lines += ["", "## Summary", "",
              f"Each repetition is a fresh process: the day read into memory, an untimed "
              f"warm-up of each pass, then {args.rounds} rounds of one timed pass of each, in an "
              f"order turned by one place each round. Each span is one whole pass. "
              f"{len(records)} repetitions, {len(records) * args.rounds} timed passes of each; "
              "the figures are over all of them.", "",
              "| Figure | Median | Min | Max | CV |", "|---|---|---|---|---|"]
    lines += summary_rows(passes, file_bytes)
    lines += ["", "## Best pass", "",
              "Both itch-book figures are the best of 3 passes (its `parse_throughput.cpp`); "
              "Lunary's is a median of 10. The best timed pass of each, of all this run's, for "
              "comparison only; every verdict is on the median.", "",
              best_heading(passes), "|---|---|---|"]
    lines += best_rows(passes, file_bytes)
    lines += ["", "## Targets", "", "| Target | Bar | Measured | Verdict |", "|---|---|---|---|"]
    lines += target_rows(passes, file_bytes, day)
    lines += [""] + caveat_lines(day, loops)
    lines += ["", "## Counts", "",
              "From the warm-up. Every timed pass gave the same, and every repetition, or the run "
              "is refused.", "", "| Count | Value |", "|---|---|"]
    lines += count_rows(first)
    lines += ["", "## Every timed pass", "",
              "One row a timed pass, in the order run. Every figure above is worked from these "
              "seconds, as `bench_parse` records them, to the nanosecond.", "",
              "| Repetition | Round | Pass | Seconds | Messages | Messages/s | GB/s | Fold |",
              "|---|---|---|---|---|---|---|---|"]
    lines += pass_rows(passes, file_bytes)
    lines += ["", bench_driver.swap_watch.line() if bench_driver.swap_watch else
              "Swap-outs are not read on this host: it has no /proc/vmstat."]
    lines += [""] + sampler_section(sampled)
    lines += ["", "## machine_check output", "", "```", machine_output, "```", "",
              "## Commands", "", "```", command, "```", ""]
    return "\n".join(lines)


def relative_command(args):
    def shown(path):
        path = Path(os.path.abspath(path))
        try:
            return str(path.relative_to(REPO_ROOT))
        except ValueError:
            return f"<raw>/{path.name}"
    words = ["python3", "-B", "scripts/parse_driver.py", "--bench", shown(args.bench),
             "--probe", shown(args.probe), "--input", shown(args.input),
             "--prediction", args.prediction, "--repetitions", str(args.repetitions),
             "--rounds", str(args.rounds), "--raw-dir", "<raw>", "--out", shown(args.out)]
    for flag, value in (("--core", args.core), ("--sibling", args.sibling),
                        ("--sampler-cpu", args.sampler_cpu)):
        if value is not None:
            words += [flag, str(value)]
    for flag, value, default in (("--quiet-before", args.quiet_before, QUIET_BEFORE_S),
                                 ("--quiet-wait", args.quiet_wait, QUIET_WAIT_S),
                                 ("--most-in-a-row", args.most_in_a_row, MOST_IN_A_ROW),
                                 ("--most-withdrawn", args.most_withdrawn, MOST_WITHDRAWN)):
        if value != default:
            words += [flag, f"{value:g}"]
    text = " ".join(shlex.quote(word) if "<" not in word else word for word in words)
    return f"{args.wrapped_by} {text}" if args.wrapped_by else text


# --check

LOOP_LINE = re.compile(r"^- (frame_walk|book_decode|strict_decode): \d+ instructions"
                       r"( in \d+ functions, the pass and its clones)?, no call and no jump to "
                       r"another function$")


def caveat_problems(text, day):
    """The caveats' prose as the driver writes it, and one inline-check line for each pass."""
    printed = (check_results.section(text, "Caveats") or "").splitlines()
    problems = [("parse-figure-differs", f"Caveats: {line}")
                for line in caveat_lines(day, []) if line and not line.startswith("#")
                and not line.startswith("|") and line not in printed]
    found = [match.group(1) for match in map(LOOP_LINE.match, printed) if match]
    for name in PASS_SYMBOLS:
        if found.count(name) != 1:
            problems.append(("parse-loops-missing", f"Caveats: {found.count(name)} lines say "
                                                    f"{name} makes no call, not 1"))
    return problems


def check_text(text):
    """Every figure of a results file against its own table of timed passes: (code, detail)."""
    fields = check_results.parse_header(text)
    passes = []
    for line in (check_results.section(text, "Every timed pass") or "").splitlines():
        match = PASS_ROW.match(line.strip())
        if match:
            passes.append({"repetition": int(match.group(1)), "round": int(match.group(2)),
                           "pass": match.group(3), "seconds": float(match.group(4)),
                           "messages": int(match.group(5).replace(",", "")),
                           "fold": int(match.group(6).replace(",", ""))})
    if not passes:
        return [("parse-passes-missing", "no table of timed passes")]
    problems = []
    size = re.match(r"[\d,]+", fields.get("bytes", ""))
    if size is None:
        return [("parse-bytes-missing", "no bytes field")]
    file_bytes = int(size.group(0).replace(",", ""))
    want = int(fields.get("repetitions", "0")) * int(fields.get("rounds", "0"))
    for name, *_ in PASSES:
        of = [p for p in passes if p["pass"] == name]
        if len(of) != want:
            problems.append(("parse-passes-count", f"{len(of)} {name} passes, not {want}"))
        if len({(p["messages"], p["fold"]) for p in of}) > 1:
            problems.append(("parse-passes-disagree", f"the {name} passes disagree"))
    day = fields.get("dataset", "").split(".")[0]
    pinned = manifest_entry(fields.get("dataset", ""))
    frames = re.match(r"[\d,]+", fields.get("timed_messages", ""))
    if pinned is None:
        problems.append(("parse-day-unpinned", f"{fields.get('dataset')!r} is not a day "
                                               "scripts/data_manifest.json pins"))
    if frames is None:
        problems.append(("parse-messages-missing", "no timed_messages field"))
    else:
        frames = int(frames.group(0).replace(",", ""))
        wrong = sorted({p["messages"] for p in passes} - {frames})
        if wrong:
            problems.append(("parse-messages-not-frames",
                             f"passes over {', '.join(f'{w:,}' for w in wrong)} messages, not "
                             f"every framed message, {frames:,}"))
        counted = f"| Frames, every one a message | {frames:,} |"
        if counted not in (check_results.section(text, "Counts") or "").splitlines():
            problems.append(("parse-messages-not-frames", f"Counts: no row {counted}"))
        if pinned is not None and frames != pinned["messages"]:
            problems.append(("parse-not-the-manifest", f"{frames:,} frames, not the manifest's "
                                                       f"{pinned['messages']:,}"))
    if pinned is not None and file_bytes != pinned["unpacked_bytes"]:
        problems.append(("parse-not-the-manifest", f"{file_bytes:,} bytes, not the manifest's "
                                                   f"{pinned['unpacked_bytes']:,}"))
    for title, rows in (("Every timed pass", pass_rows(passes, file_bytes)),
                        ("Summary", summary_rows(passes, file_bytes)),
                        ("Best pass", [best_heading(passes), *best_rows(passes, file_bytes)]),
                        ("Targets", target_rows(passes, file_bytes, day)),
                        ("Caveats", caveat_rows(day) or [])):
        printed = check_results.section(text, title) or ""
        for row in rows:
            if row not in printed.splitlines():
                problems.append(("parse-figure-differs", f"{title}: {row}"))
    problems.extend(caveat_problems(text, day))
    return problems


def check_main(paths):
    failed = 0
    for path in paths:
        problems = check_text(Path(path).read_text(encoding="utf-8"))
        for code, detail in problems:
            print(f"parse_driver: FAIL {code} {Path(path).name}: {detail}")
        failed += bool(problems)
        if not problems:
            print(f"parse_driver: ok {Path(path).name}")
    return 1 if failed else 0


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bench", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--core", type=int)
    parser.add_argument("--sibling", type=int)
    parser.add_argument("--sampler-cpu", type=int, help="the CPU the sampler runs on")
    parser.add_argument("--wrapped-by", help="what the run was started under, for the record")
    parser.add_argument("--allow-dirty", action="store_true", help="for tests, not for a report")
    parser.add_argument("--quiet-before", type=float, default=QUIET_BEFORE_S, metavar="S")
    parser.add_argument("--quiet-wait", type=float, default=QUIET_WAIT_S, metavar="S")
    parser.add_argument("--most-in-a-row", type=int, default=MOST_IN_A_ROW)
    parser.add_argument("--most-withdrawn", type=int, default=MOST_WITHDRAWN)
    parser.add_argument("--predictions-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--run-log", type=Path, default=REPO_ROOT / "results" / "run_log.md",
                        help=argparse.SUPPRESS)
    parser.add_argument("--vmstat", type=Path, default=Path("/proc/vmstat"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.repetitions < 3:
        parser.error("median, min, max and CV need at least 3 repetitions")
    if args.rounds < 1:
        parser.error("--rounds is at least 1")
    return args


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--inline-check"]:
        return inline_main(argv[1:])
    if argv[:1] == ["--check"]:
        if len(argv) < 2:
            print("usage: parse_driver.py --check FILE...", file=sys.stderr)
            return 2
        return check_main(argv[1:])
    args = parse_args(argv)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, bench_driver.forward_signal)
    started = utc_now()
    run_id = f"{args.prediction}-{started.replace(':', '').replace('-', '')}"
    commit = "unknown"
    try:
        run_record.open_run(args, run_id)
        commit = bench_driver.git_commit(REPO_ROOT, args.allow_dirty)
        run_record.start_record(args, "parse_driver", run_id, commit, (args.bench, args.probe))
        written = run(args, run_id, started, commit)
    except Stopped as stop:
        bench_driver.stop_child()
        name = signal.Signals(stop.signum).name
        print(f"parse_driver: stopped by {name} at {bench_driver.stage}", file=sys.stderr)
        log_row(args, run_id, commit,
                f"stopped by {name} at {bench_driver.stage}; nothing from it is published; the row "
                "was written by the driver as it stopped")
        return 128 + stop.signum
    except (Refused, run_record.Incomplete) as refusal:
        bench_driver.hold_signals()
        print(f"parse_driver: refused at {refusal.stage}: {scrub(str(refusal))}", file=sys.stderr)
        log_row(args, run_id, commit, f"refused at {refusal.stage}: {refusal}")
        return 1
    except Exception as error:  # Last, so that no run ends without its row.
        # As bench_driver.log_failure, with the text scrubbed of the map's path and any name.
        bench_driver.hold_signals()
        print(f"parse_driver: failed: {scrub(repr(error))}", file=sys.stderr)
        log_row(args, run_id, commit, f"failed: {error!r}")
        return 1
    bench_driver.hold_signals()
    bench_driver.append_written(args.run_log, run_id, args.prediction, commit, [written])
    print(f"parse_driver: wrote {written}, load lines pending. Now run scripts/stamp_load.py.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
