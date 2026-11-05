#!/usr/bin/env python3
"""Turns sampled stacks into a flamegraph and its results file, with nothing private in either.

    flame_report.py fold-sample --sample SAMPLE.txt --out STACKS.folded
    flame_report.py render --folded STACKS.folded --flamegraph flamegraph.pl \\
        --record RUN.json --source perf|sample --workload NAME --day DAY --command TEXT \\
        --prediction P-NNNN|none [--run-id ID --run-log results/run_log.md] --notes NOTES.md \\
        --out results/flamegraph_NAME_HOST_DAY.md \\
        [--annotate ANNOTATE.txt --annotate-symbol NAME] [--normal FILE --frame-pointer FILE]
    flame_report.py --check FILE...

fold-sample reads the call graph of macOS's /usr/bin/sample and writes FlameGraph's folded
form, one line per stack with its samples, each frame's self count at its leaf. render scrubs
the stacks of home paths, addresses and the names of this machine and its user, has
FlameGraph's flamegraph.pl draw them with fixed colours, and writes the results file beside
the SVG: where the samples came from, the hottest frames, the perf annotate excerpt for the
hottest function on Linux, what the graph cannot show, and on Linux the frame-pointer
build's throughput against the normal build, read from the two bench_driver files.

--check reads each results file and the SVG beside it: nothing private in either, the
sample count the same in both, the limits stated, and on Linux the build named as the
frame-pointer build with its delta.

Each violation prints as `flame_report: FAIL <code> <file>: <detail>`.

Exit codes: 0 written, or --check found nothing, 1 --check found something, 2 bad usage or
unusable input.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402
import gates  # noqa: E402

SAMPLE_LINE = re.compile(r"^(?P<lead>[ +!:|]*?)(?P<count>\d+) (?P<frame>.*)$")
SAMPLE_FRAME = re.compile(r"^(?P<name>.*?)  \(in (?P<image>[^)]+)\)")
SVG_TOTAL = re.compile(r"<title>all \(([0-9,]+) samples, 100%\)</title>")
MD_SAMPLES = re.compile(r"^- samples: ([0-9,]+)$", re.MULTILINE)
THROUGHPUT = re.compile(r"^\| Throughput, messages/s \| ([0-9,]+) \| ([0-9,]+) \| ([0-9,]+) "
                        r"\| ([0-9.]+)% \|$", re.MULTILINE)
HEX_ADDRESS = re.compile(r"\b0x[0-9a-f]{6,}\b|\[[0-9a-f]{8,}\]")
FRAME_POINTER = ("-fno-omit-frame-pointer", "-mno-omit-leaf-frame-pointer")
TOP_FRAMES = 15

LIMITS = {
    "perf": """\
- **User space only.** At `perf_event_paranoid=2` `cycles:k` is refused, so no kernel frame
  appears and none can: time in page faults, system calls and interrupts shows only as the
  gap it leaves. No system setting was changed to get them.
- **Inlining hides the detail.** The book's operations are inlined into the pass that calls
  them, so a wide frame is several functions, and Zen 1 has no LBR to recover the path
  (`perf record --call-graph lbr` is refused here). The `perf annotate` excerpt below is what
  says where inside the hottest function the samples land.
- **Skid.** `cycles:u` on Zen 1 is not precise: `cycles:pp` needs IBS, which refuses user-only
  sampling. A sample lands on an instruction near the one that stalled, often the next.
- **The frame-pointer build is not the build measured elsewhere.** Its throughput against the
  normal build is below. Stacks are walked by frame pointer; a frame without one (libc's
  hand-written routines) is attributed to its caller.
- **One run.** A flamegraph is a sample of one process, not a measurement with a spread.""",
    "sample": """\
- **macOS, not Linux.** `/usr/bin/sample` on macOS 27, attached to our own process with no
  sudo, every 1 ms. It is a real measurement of this Mac and does not replace the `perf` data
  from `debian`: different CPU, different OS, no pinning, and no hardware events, only time.
- **Wall-clock stacks, user and kernel together.** `sample` suspends the thread and walks its
  stack, so a frame's count is time on the stack, not cycles, and time in the kernel shows
  as the user frame that entered it.
- **Inlining hides the detail,** as on Linux: Apple's arm64 ABI keeps frame records, so stacks
  are whole, but an inlined function has no frame of its own. No annotate excerpt is taken.
- **One run, 25 seconds** of a process that ran longer. Not a measurement with a spread.""",
}


class ReportError(ValueError):
    """The inputs cannot make a report."""


def private_patterns():
    names = gates.private_names()
    patterns = [(gates.HOME_PATH, "<home>/"), (gates.PRIVATE_ADDRESS, "<address>"),
                (HEX_ADDRESS, "")]
    if names:
        patterns.append((re.compile(r"(?<![\w-])(?:" + "|".join(map(re.escape, names))
                                    + r")(?![\w-])", re.IGNORECASE), "<host>"))
    return patterns


def scrub(text):
    """The text with every home path, address and private name taken out."""
    for pattern, replacement in private_patterns():
        text = pattern.sub(replacement, text)
    return text


def private_found(text):
    """The first private thing in text, or None."""
    patterns = private_patterns()[:2] + private_patterns()[3:]
    for pattern, _ in patterns:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def shorten(frame):
    """A frame's name without its parameter list, which perf and sample both print."""
    frame = frame.strip()
    for suffix in (" const", " &&", " &"):
        if frame.endswith(")" + suffix):
            frame = frame[: -len(suffix)]
    if not frame.endswith(")"):
        return frame
    depth = 0
    for index in range(len(frame) - 1, -1, -1):
        depth += {")": 1, "(": -1}.get(frame[index], 0)
        if depth == 0:
            head = frame[:index]
            return head if head and not head.endswith("operator") else frame
    return frame


def brief(frame):
    """A frame's name with its template arguments folded, for the table; the SVG keeps them."""
    out, depth = [], 0
    for char in frame:
        if char == "<":
            if depth == 0:
                out.append("<…>")
            depth += 1
        elif char == ">" and depth:
            depth -= 1
        elif depth == 0:
            out.append(char)
    return "".join(out)


def fold_sample(text):
    """FlameGraph's folded stacks from a `sample` call graph, as {stack: self samples}.

    Each line of the graph is a frame with the samples it was on the stack for, indented one
    step deeper than its caller. A frame's own samples are its count less its callees'."""
    body = text.split("Call graph:", 1)
    if len(body) != 2:
        raise ReportError("no call graph in the sample output")
    graph = body[1].split("\nTotal number in stack", 1)[0]
    stacks = Counter()
    path = []  # [column, name, count, callees' count]

    def pop_to(column):
        while path and path[-1][0] >= column:
            names = ";".join(node[1] for node in path)
            _, _, count, callees = path.pop()
            if count > callees:
                stacks[names] += count - callees

    for line in graph.splitlines():
        match = SAMPLE_LINE.match(line)
        if match is None:
            continue
        column = len(match.group("lead"))
        frame = SAMPLE_FRAME.match(match.group("frame"))
        name = shorten(frame.group("name")) if frame else match.group("frame").split("  ")[0]
        count = int(match.group("count"))
        pop_to(column)
        if path:
            path[-1][3] += count
        path.append([column, name, count, 0])
    pop_to(0)
    if not stacks:
        raise ReportError("an empty call graph")
    return stacks


def fold_lines(stacks):
    return [f"{stack} {count}" for stack, count in sorted(stacks.items())]


def read_folded(text):
    stacks = Counter()
    for line in text.splitlines():
        if not line.strip():
            continue
        stack, _, count = line.rpartition(" ")
        stacks[";".join(shorten(frame) for frame in scrub(stack).split(";"))] += int(count)
    return stacks


def hottest(stacks):
    """Each frame's self and total share, the most self first."""
    total = sum(stacks.values())
    own, inclusive = Counter(), Counter()
    for stack, count in stacks.items():
        frames = stack.split(";")
        own[frames[-1]] += count
        for frame in set(frames):
            inclusive[frame] += count
    ranked = sorted(own, key=lambda frame: (-own[frame], frame))[:TOP_FRAMES]
    return [(frame, own[frame] / total, inclusive[frame] / total) for frame in ranked], total


ANNOTATE_LINE = re.compile(r"^\s*([0-9]+\.[0-9]+)\s*:")


def annotate_excerpt(text, hottest=6, context=4):
    """The lines of a `perf annotate --stdio` listing with the most samples, and the code
    around each, in the listing's order; gaps marked with an ellipsis."""
    lines = text.splitlines()
    shares = []
    for index, line in enumerate(lines):
        match = ANNOTATE_LINE.match(line)
        if match and float(match.group(1)) > 0:
            shares.append((float(match.group(1)), index))
    if not shares:
        raise ReportError("the annotate listing has no line with samples")
    keep = set()
    for _, index in sorted(shares, reverse=True)[:hottest]:
        keep.update(range(max(0, index - context), min(len(lines), index + context + 1)))
    out, last = [], None
    for index in sorted(keep):
        if last is not None and index != last + 1:
            out.append("        ...")
        out.append(lines[index].rstrip())
        last = index
    return "\n".join(out)


def throughput(path):
    text = path.read_text(encoding="utf-8")
    match = THROUGHPUT.search(text)
    if match is None:
        raise ReportError(f"{path.name}: no throughput row")
    fields = check_results.parse_header(text)
    median, low, high = (int(match.group(i).replace(",", "")) for i in (1, 2, 3))
    return {"median": median, "min": low, "max": high, "cv": float(match.group(4)),
            "flags": fields.get("flags", ""), "commit": fields.get("commit", ""),
            "repetitions": fields.get("repetitions", ""), "name": path.name}


def delta_lines(normal, frame_pointer):
    ratio = frame_pointer["median"] / normal["median"]
    rows = ["| Build | Flags | Median messages/s | Min | Max | CV | Commit | File |",
            "|---|---|---|---|---|---|---|---|"]
    for label, entry in (("normal", normal), ("frame pointers", frame_pointer)):
        rows.append(f"| {label} | `{entry['flags']}` | {entry['median']:,} | {entry['min']:,} "
                    f"| {entry['max']:,} | {entry['cv']:.2f}% | `{entry['commit'].split()[0]}` "
                    f"| [results/{entry['name']}]({entry['name']}) |")
    return [f"- frame_pointer_ratio: {ratio:.4f}", ""], rows, ratio


def mentions_frame_pointers(flags):
    return all(flag in flags for flag in FRAME_POINTER)


def render(args):
    stacks = read_folded(args.folded.read_text(encoding="utf-8"))
    if not stacks:
        raise ReportError("no stacks")
    record = json.loads(args.record.read_text(encoding="utf-8"))
    header = record["header"]
    host = header["host"]
    if (args.source == "perf") != (host == "debian"):
        raise ReportError(f"a {args.source} graph from host {host!r}")
    if args.source == "perf" and not mentions_frame_pointers(header.get("flags", "")):
        raise ReportError("a perf graph from a build without frame pointers")
    day = args.day
    svg = args.out.with_suffix(".svg")
    folded = "\n".join(fold_lines(stacks)) + "\n"
    title = f"{args.workload} on {host}, {day}, {args.event}"
    drawn = subprocess.run(
        ["perl", str(args.flamegraph), "--hash", "--title", title, "--subtitle",
         "user frames only" if args.source == "perf" else "macOS /usr/bin/sample",
         "--countname", "samples", "--width", "1600"],
        input=folded, capture_output=True, text=True, check=False)
    if drawn.returncode != 0:
        raise ReportError(f"flamegraph.pl failed: {drawn.stderr.strip()}")
    image = scrub(drawn.stdout)
    frames, total = hottest(stacks)
    notes = scrub(args.notes.read_text(encoding="utf-8")).strip()
    out = [f"# Flamegraph: {args.workload} on {host}, {day}", "",
           "Generated by `scripts/flame_report.py` from sampled stacks that are not committed. "
           "Do not edit by hand.", "", f"Host: **{host}**. "
           + ("Numbers from the two hosts are never mixed." if host == "debian" else
              "A macOS profile: it does not replace the `perf` data from `debian`."), "",
           f"![{args.workload}]({svg.name})", "", f"[{svg.name}]({svg.name}), interactive in a "
           "browser: hover for a frame's samples, click to zoom.", "", "## Header", ""]
    for name in ("host", "cpu", "kernel", "compiler", "flags", "commit", "pinning", "governor",
                 "frequency", "turbo"):
        if name in header:
            out.append(f"- {name}: {scrub(header[name])}")
    out += [f"- build: {'frame pointers' if mentions_frame_pointers(header.get('flags', '')) else 'normal'}",
            f"- workload: {args.workload}", f"- event: {args.event}",
            f"- samples: {total:,}", f"- stacks: {len(stacks):,}",
            f"- prediction: {args.prediction}"]
    if args.run_id:
        out.append(f"- run_id: {args.run_id}")
    ratio_lines, delta_rows, ratio = [], [], None
    if args.normal and args.frame_pointer:
        ratio_lines, delta_rows, ratio = delta_lines(throughput(args.normal),
                                                     throughput(args.frame_pointer))
        out += ratio_lines[:1]
    top, top_own, _ = frames[0]
    summary = (f"{total:,} samples, {args.event}, the hottest frame `{brief(top)}` at "
               f"{100 * top_own:.2f}% self")
    if ratio is not None:
        summary += f"; the frame-pointer build at {ratio:.4f} of the normal build's throughput"
    out.append(f"- summary: {summary}")
    out += ["", "## What it shows", "", notes, "", "## Hottest frames", "",
            f"The {len(frames)} frames with the most samples at the top of the stack. Self is "
            "the share of samples in the frame itself; total adds every frame it called.", "",
            "| Frame | Self | Total |", "|---|---|---|"]
    out += [f"| `{brief(frame)}` | {100 * own:.2f}% | {100 * inclusive:.2f}% |"
            for frame, own, inclusive in frames]
    if args.annotate:
        excerpt = annotate_excerpt(scrub(args.annotate.read_text(encoding="utf-8",
                                                                 errors="replace")))
        out += ["", "## perf annotate, the hottest function", "",
                f"`perf annotate --stdio` on `{args.annotate_symbol}`, the lines with the most "
                "samples and the code around them. The percentages are of that symbol's samples.",
                "", "```", excerpt, "```"]
    if delta_rows:
        out += ["", "## The frame-pointer build", "",
                f"The graph is taken on a build with `{' '.join(FRAME_POINTER)}`, which keeps "
                "a frame pointer in every function so that `perf` can walk the stack. Both "
                "builds ran `paged-flat` mapped, top of book not tracked, throughput only, in "
                "the same sitting through `scripts/bench_driver.py`. The frame-pointer build "
                f"ran at {ratio:.4f} times the normal build's median throughput, "
                f"{100 * (ratio - 1):+.2f}%.", ""]
        out += delta_rows
    out += ["", "## What it cannot show", "", LIMITS[args.source], "", "## Command", "", "```",
            scrub(args.command), "```", ""]
    text = "\n".join(out)
    found = private_found(text) or private_found(image)
    if found:
        raise ReportError(f"private text survived the scrub: {found}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    svg.write_text(image, encoding="utf-8")
    args.out.write_text(text, encoding="utf-8")
    if args.run_log:
        if not args.run_id:
            raise ReportError("--run-log needs --run-id")
        commit = scrub(header["commit"])
        with open(args.run_log, "a", encoding="utf-8") as log:
            log.write(f"| {args.run_id} | {args.prediction} | {commit} | Linux | published: "
                      f"a flamegraph of {args.workload}, {total:,} samples |\n")
    print(f"flame_report: wrote {args.out.name} and {svg.name}, {total:,} samples")
    return 0


def check_file(path):
    found = []
    text = path.read_text(encoding="utf-8")
    svg = path.with_suffix(".svg")
    if not svg.is_file():
        return [("svg-missing", f"no {svg.name} beside it")]
    image = svg.read_text(encoding="utf-8")
    for label, body in (("results file", text), ("svg", image)):
        leaked = private_found(body)
        if leaked:
            found.append(("private-text", f"{label}: {leaked}"))
    drawn = SVG_TOTAL.search(image)
    stated = MD_SAMPLES.search(text)
    if drawn is None or stated is None or drawn.group(1) != stated.group(1):
        found.append(("samples-differ", f"svg {drawn and drawn.group(1)}, file "
                                        f"{stated and stated.group(1)}"))
    if "## What it cannot show" not in text:
        found.append(("limits-unstated", "no What it cannot show section"))
    fields = check_results.parse_header(text)
    if fields.get("host") == "debian":
        if not mentions_frame_pointers(fields.get("flags", "")) or \
                fields.get("build") != "frame pointers":
            found.append(("build-unnamed", "a debian graph not said to be the frame-pointer "
                                           "build"))
        if "frame_pointer_ratio" not in fields or "## The frame-pointer build" not in text:
            found.append(("delta-missing", "the frame-pointer build's delta is not there"))
        if not fields.get("run_id"):
            found.append(("run-id-missing", "a debian graph with no run_id"))
        if "## perf annotate, the hottest function" not in text:
            found.append(("annotate-missing", "no perf annotate excerpt"))
    elif "does not replace the `perf` data" not in text:
        found.append(("host-unsaid", "a macOS graph that does not say it is no substitute"))
    return found


def run_check(paths):
    failed = False
    for path in paths:
        for code, detail in check_file(path):
            print(f"flame_report: FAIL {code} {path.name}: {detail}")
            failed = True
        if not failed:
            print(f"flame_report: ok {path.name}")
    return 1 if failed else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--check"]:
        if len(argv) < 2:
            print("flame_report: --check needs a file", file=sys.stderr)
            return 2
        return run_check([Path(arg) for arg in argv[1:]])
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    fold = sub.add_parser("fold-sample")
    fold.add_argument("--sample", type=Path, required=True)
    fold.add_argument("--out", type=Path, required=True)
    draw = sub.add_parser("render")
    draw.add_argument("--folded", type=Path, required=True)
    draw.add_argument("--flamegraph", type=Path, required=True)
    draw.add_argument("--record", type=Path, required=True)
    draw.add_argument("--source", choices=sorted(LIMITS), required=True)
    draw.add_argument("--workload", required=True)
    draw.add_argument("--day", required=True)
    draw.add_argument("--event", default="cycles:u")
    draw.add_argument("--command", required=True)
    draw.add_argument("--prediction", required=True)
    draw.add_argument("--run-id")
    draw.add_argument("--run-log", type=Path)
    draw.add_argument("--notes", type=Path, required=True)
    draw.add_argument("--out", type=Path, required=True)
    draw.add_argument("--annotate", type=Path)
    draw.add_argument("--annotate-symbol")
    draw.add_argument("--normal", type=Path)
    draw.add_argument("--frame-pointer", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.mode == "fold-sample":
            stacks = fold_sample(args.sample.read_text(encoding="utf-8", errors="replace"))
            args.out.write_text("\n".join(fold_lines(stacks)) + "\n", encoding="utf-8")
            print(f"flame_report: folded {sum(stacks.values()):,} samples, {len(stacks)} stacks")
            return 0
        if args.source == "perf" and not (args.annotate and args.normal and args.frame_pointer):
            parser.error("a perf graph needs --annotate and the two builds' files")
        return render(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"flame_report: unusable input: {error!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
