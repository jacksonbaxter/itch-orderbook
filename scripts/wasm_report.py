#!/usr/bin/env python3
"""T-WASM-1's results file: the demo's replay in a browser against native, on one Mac.

    wasm_report.py render --native native.json --native-stderr FILE \\
        --browser browser.json --browser-stderr FILE --out results/wasm_replay_mac_DAY.md
    wasm_report.py check [FILE...]

render reads the two records scripts/wasm_bench.py wrote and the stderr each run left, which
holds measure.sh's load lines and /usr/bin/time -l's memory, and writes the file. It refuses
records that do not replay the same extract, reach the same digest, or come from one commit
and one host.

check holds a written file to itself, with no raw record: every header field present; the
host macOS on Darwin and the browser named; each summary figure the median, min, max and CV
of the repetitions table; the ratio the browser's median over native's; the verdict in a word
from the fixed set, and the one those figures and the browser give; the load lines stamped;
both runs in results/run_log.md; and no per-message figure. With no FILE it checks every
results/wasm_*.md.

T-WASM-1's bar, 1.55 times native, is Jangda et al.'s geomean in Google Chrome. A run in any
other browser neither meets nor misses it: its verdict is `browser differs: <browser>, not
Google Chrome`, with the figure against the bar beside it.

Each violation prints as `wasm_report: FAIL <code> <file>: <detail>`.

Exit codes: 0 written or passed, 1 refused or a violation, 2 bad usage.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAC_HOST = "macOS (M4 Max)"
BAR = 1.55
CHROME = "Google Chrome "
VERDICTS = ("met", "missed: ", "browser differs: ")
FIELDS = ("host", "cpu", "kernel", "commit", "extract", "extract_sha256", "day_sha256",
          "native_compiler", "native_flags", "native_timer", "wasm_compiler", "wasm_flags",
          "wasm_sha256", "wasm_memory", "browser", "browser_flags", "user_agent",
          "cross_origin_isolated", "page_clock_step", "pinning", "native_load_start",
          "native_load_end", "browser_load_start", "browser_load_end", "native_peak_rss",
          "browser_peak_rss", "prediction", "run_id", "native_run_id", "repetitions",
          "messages", "digest", "run_started", "run_finished")
SUMMARY = (("Native, messages/s", "native", "rate"),
           ("WebAssembly in the browser, messages/s", "wasm", "rate"),
           ("Native, ms a replay", "native", "ms"),
           ("WebAssembly in the browser, ms a replay", "wasm", "ms"))
NO_LATENCY = ("No per-message latency is shown: a replay is the only span either side "
              "times, and the browser's clock cannot resolve one message.")
HEADER_LINE = re.compile(r"^- ([a-z0-9_]+): (.*)$")
LOAD = re.compile(r"^measure\.sh: (start|end) load1=(\S+).*?(?: exit=(\d+))? at=(\S+)")
TIME_LINE = re.compile(r"^\s*(\d+)\s+(maximum resident set size|peak memory footprint)$")
PRIVATE = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/")


class ReportError(Exception):
    """The records cannot make a results file."""


# Figures, shared by render and check so that both compute them one way.

def spread(values):
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    return statistics.median(values), min(values), max(values), deviation / mean


def shown(value, kind):
    return f"{value:,.0f}" if kind == "rate" else f"{value:.3f}"


def summary_cells(values, kind):
    median, low, high, cv = spread(values)
    return [shown(median, kind), shown(low, kind), shown(high, kind), f"{cv * 100:.2f}%"]


def ratio_of(native_ms, wasm_ms):
    return statistics.median(wasm_ms) / statistics.median(native_ms)


def is_chrome(browser):
    """Google Chrome itself: not Chromium and not Chrome for Testing."""
    return browser.startswith(CHROME) and not browser.startswith(CHROME + "for Testing")


def verdict(ratio, browser):
    against = (f"{ratio:.2f}× native, {'within' if ratio <= BAR else 'over'} the {BAR:.2f}× bar "
               f"by {abs(BAR - ratio):.2f}×")
    if not is_chrome(browser):
        return (f"browser differs: {browser}, not Google Chrome; neither a pass nor a fail; "
                f"against the bar: {against}")
    return "met" if ratio <= BAR else f"missed: {against}"


# render

def load_lines(stderr_text, name):
    found = {}
    peak = {}
    for line in stderr_text.splitlines():
        match = LOAD.match(line)
        if match:
            kind, load, code, at = match.groups()
            found[kind] = (f"measure.sh: {kind} load1={load}" + (f" exit={code}" if code else "")
                           + f" at={at}")
        timed = TIME_LINE.match(line)
        if timed:
            peak[timed.group(2)] = int(timed.group(1))
    if set(found) != {"start", "end"}:
        raise ReportError(f"{name}: measure.sh's start and end lines are not both there")
    if "exit=0" not in found["end"]:
        raise ReportError(f"{name}: measure.sh's end line is not exit=0")
    if "maximum resident set size" not in peak:
        raise ReportError(f"{name}: no /usr/bin/time -l output")
    return found, peak


def gib(value):
    return f"{value / 1024 ** 3:.2f} GiB"


def render(native, browser, native_stderr, browser_stderr):
    for record, side in ((native, "native"), (browser, "browser")):
        if record.get("side") != side:
            raise ReportError(f"the {side} record is of side {record.get('side')!r}")
    if native["extract_manifest"]["extract_sha256"] != browser["extract"]["sha256"]:
        raise ReportError("the two sides replayed different extracts")
    if native["digest"] != browser["digest"] or native["digest"] != native["extract_manifest"]["digest"]:
        raise ReportError("the two sides left different books")
    if native["commit"] != browser["commit"] or "(clean)" not in native["commit"]:
        raise ReportError(f"commits {native['commit']} and {browser['commit']}: not one clean commit")
    if native["prediction"] != browser["prediction"]:
        raise ReportError("the two runs cite different predictions")
    header = native["header"]
    if header["host"] != MAC_HOST:
        raise ReportError(f"host {header['host']!r}: T-WASM-1 runs on the Mac")
    manifest = native["extract_manifest"]
    messages = manifest["messages"]
    native_ms = [repetition["ns"] / 1e6 for repetition in native["repetitions"]]
    wasm_ms = [repetition["ms"] for repetition in browser["repetitions"]]
    if len(native_ms) != len(wasm_ms):
        raise ReportError("the two sides ran different numbers of repetitions")
    if any(r["messages"] != messages for r in native["repetitions"] + browser["repetitions"]):
        raise ReportError("a repetition did not replay every message of the extract")
    native_load, native_peak = load_lines(native_stderr, "native stderr")
    browser_load, browser_peak = load_lines(browser_stderr, "browser stderr")
    engine = browser["engine_built"]
    day = manifest["day"].split(".")[0]
    fields = {
        "host": header["host"], "cpu": header["cpu"], "kernel": header["kernel"],
        "commit": native["commit"],
        "extract": (f"{manifest['symbol']} on {manifest['day']}, {messages:,} messages, "
                    f"{manifest['bytes']:,} bytes, made by scripts/demo.py extract"),
        "extract_sha256": manifest["extract_sha256"], "day_sha256": manifest["day_sha256"],
        "native_compiler": header["compiler"], "native_flags": header["flags"],
        "native_timer": f"{header['timer']}; quantum {header['quantum']}",
        "wasm_compiler": engine.get("compiler", "unavailable: engine.json does not name it"),
        "wasm_flags": engine.get("flags", "unavailable: engine.json does not give them"),
        "wasm_sha256": browser["wasm_sha256"],
        "wasm_memory": (f"{browser['memory_bytes']:,} bytes of linear memory, fixed: the same "
                        "size after the run"),
        "browser": f"{browser['browser']}, headless, from the binary's --version",
        "browser_flags": " ".join(browser["browser_flags"]),
        "user_agent": browser["user_agent"],
        "cross_origin_isolated": str(browser["cross_origin_isolated"]).lower(),
        "page_clock_step": (f"{browser['clock_step_ms'] * 1000:.1f} µs, the smallest step of "
                            "performance.now() the page saw in 200,000 reads"),
        "pinning": header["pinning"],
        "native_load_start": native_load["start"], "native_load_end": native_load["end"],
        "browser_load_start": browser_load["start"], "browser_load_end": browser_load["end"],
        "native_peak_rss": (f"{gib(native_peak['maximum resident set size'])}, /usr/bin/time -l "
                            "of the driver and bench_demo"),
        "browser_peak_rss": (f"{gib(browser_peak['maximum resident set size'])}, /usr/bin/time -l "
                             "of the driver, the largest process of the browser's tree it waited "
                             "for"),
        "prediction": native["prediction"], "run_id": browser["run_id"],
        "native_run_id": native["run_id"], "repetitions": str(len(native_ms)),
        "messages": f"{messages:,}", "digest": native["digest"],
        "run_started": native["run_started"], "run_finished": browser["run_finished"],
    }
    ratio = ratio_of(native_ms, wasm_ms)
    lines = [f"# T-WASM-1: the demo's replay in a browser against native, on {MAC_HOST}, "
             f"{manifest['symbol']} on {day}", "",
             "Generated by `scripts/wasm_report.py` from raw records that are not committed. "
             "Do not edit by hand.", "",
             f"Host: **{MAC_HOST}**. A macOS figure, never a Linux one: `debian` has no browser. "
             "macOS offers no way to pin a thread, so neither side was pinned.", "",
             "## Header", ""]
    lines += [f"- {name}: {fields[name]}" for name in FIELDS]
    lines += ["", "## What is timed", "",
              f"Both sides replay the same extract, the {messages:,} messages of "
              f"{manifest['symbol']} and every System Event of {manifest['day']}, through the "
              "same C++, `include/itch/demo_replay.hpp` on `FastBook<>` at its default capacity, "
              "the arm the project ships. Each side runs one warm-up and then "
              f"{len(native_ms)} timed replays, each on a fresh book built before its span "
              "opens. The span is one call of `Replay::replay()`: the frame walk and "
              "`FastBook::apply()` for every message, with each status counted. Natively the "
              "clock is `mach_absolute_time` around the call, in `bench_demo`; in the browser "
              "it is `performance.now()` around the call into the engine, in the page's bench "
              "mode, since WebAssembly has no clock of its own. Each side's rate is the "
              "extract's messages over its span, and the ratio is the browser's median span "
              "over native's. Both left the book with digest "
              f"`{native['digest']}`, the one `itch_extract` reached when it cut the extract.",
              "",
              "The two builds differ in more than the target: native is "
              f"{header['compiler'].split(' (')[0]} for arm64, the browser's engine is em++ for "
              "wasm32 with 128-bit SIMD, and a 64-bit pointer becomes a 32-bit one.", "",
              NO_LATENCY, "",
              "## Summary", "",
              "| Figure | Median | Min | Max | CV |", "|---|---|---|---|---|"]
    for label, side, kind in SUMMARY:
        values = native_ms if side == "native" else wasm_ms
        if kind == "rate":
            values = [messages / (ms / 1000) for ms in values]
        lines.append(f"| {label} | " + " | ".join(summary_cells(values, kind)) + " |")
    lines += ["", "## Target", "",
              "| Target | Bar | Measured | Verdict |", "|---|---|---|---|",
              f"| T-WASM-1 | ≤ {BAR:.2f}× native, in Google Chrome | {ratio:.2f}× | "
              f"{verdict(ratio, browser['browser'])} |", "",
              f"The bar is Jangda et al.'s geomean slowdown over SPEC CPU in Chrome, {BAR:.2f}×, "
              "not a book replay (BENCHMARKS.md, T-WASM-1).", "",
              "## Repetitions", "",
              "| Repetition | Native ms | Browser ms |", "|---|---|---|"]
    for index, (n, w) in enumerate(zip(native_ms, wasm_ms), start=1):
        lines.append(f"| {index} | {n:.3f} | {w:.3f} |")
    lines += ["", f"Warm-ups, excluded: native {native['warmup']['ns'] / 1e6:.3f} ms, browser "
                  f"{browser['warmup']['ms']:.3f} ms.", "",
              "## machine_check output", "", "Native run:", "", "```", native["machine_check"],
              "```", "", "Browser run:", "", "```", browser["machine_check"], "```", "",
              "## Commands", "", "```",
              "python3 -B scripts/demo.py extract --day data/" + manifest["day"]
              + f" --symbol {manifest['symbol']}",
              "python3 -B scripts/demo.py build",
              f"measure.sh /usr/bin/time -l {native['command']}",
              f"measure.sh /usr/bin/time -l {browser['command']}",
              "python3 -B scripts/wasm_report.py render --native <raw>/native.json "
              "--native-stderr <raw>/native.stderr --browser <raw>/browser.json "
              f"--browser-stderr <raw>/browser.stderr --out results/wasm_replay_mac_{day}.md",
              "```", ""]
    text = "\n".join(lines)
    if PRIVATE.search(text):
        raise ReportError("the file would carry a home path")
    return text


# check

def section(text, title):
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else None


def table_rows(body):
    return [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in (body or "").splitlines()
            if line.startswith("|") and not line.startswith("|---")]


def check_text(text, run_log):
    """(code, detail) for every violation."""
    fields = {}
    for line in (section(text, "Header") or "").splitlines():
        match = HEADER_LINE.match(line)
        if match:
            fields[match.group(1)] = match.group(2)
    for name in FIELDS:
        if not fields.get(name, "").strip():
            yield "header-field-missing", name
    if fields.get("host") != MAC_HOST or not fields.get("kernel", "").startswith("Darwin"):
        yield "host-label", f"{fields.get('host')!r} on {fields.get('kernel')!r}"
    for name in ("native_load_start", "native_load_end", "browser_load_start", "browser_load_end"):
        if not fields.get(name, "").startswith("measure.sh: "):
            yield "load-not-stamped", name
    for name in ("run_id", "native_run_id"):
        if fields.get(name) and fields[name] not in run_log:
            yield "run-missing-from-log", f"{fields[name]} has no row in results/run_log.md"
    if NO_LATENCY not in text:
        yield "latency-statement-missing", "the file must say it shows no per-message latency"
    if re.search(r"^\| (?:p\d|max\b)", text, re.MULTILINE):
        yield "per-message-figure", "a percentile row: neither side measured one message"

    rows = table_rows(section(text, "Repetitions"))[1:]
    try:
        native_ms = [float(row[1]) for row in rows]
        wasm_ms = [float(row[2]) for row in rows]
        messages = int(fields.get("messages", "0").replace(",", ""))
    except (ValueError, IndexError):
        yield "repetitions-unreadable", "the repetitions table cannot be read"
        return
    if len(rows) < 3 or str(len(rows)) != fields.get("repetitions"):
        yield "repetitions-count", f"{len(rows)} rows against repetitions {fields.get('repetitions')}"
        return
    summary = {row[0]: row[1:] for row in table_rows(section(text, "Summary"))[1:]}
    for label, side, kind in SUMMARY:
        values = native_ms if side == "native" else wasm_ms
        if kind == "rate":
            values = [messages / (ms / 1000) for ms in values]
        if summary.get(label) != summary_cells(values, kind):
            yield "summary-differs", f"{label}: {summary.get(label)} against the repetitions"
    targets = table_rows(section(text, "Target"))[1:]
    if len(targets) != 1 or targets[0][0] != "T-WASM-1":
        yield "target-missing", "no single T-WASM-1 row"
        return
    _, bar, measured, said = targets[0]
    ratio = ratio_of(native_ms, wasm_ms)
    if measured != f"{ratio:.2f}×":
        yield "ratio-differs", f"{measured} against {ratio:.2f}× from the repetitions"
    if bar != f"≤ {BAR:.2f}× native, in Google Chrome":
        yield "bar-differs", bar
    if not said.startswith(VERDICTS):
        yield "verdict-word", said
    browser = fields.get("browser", "").split(", headless")[0]
    if said != verdict(ratio, browser):
        yield "verdict-differs", f"{said!r}, not {verdict(ratio, browser)!r}"


def check(paths):
    log_path = REPO_ROOT / "results" / "run_log.md"
    run_log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    failed = False
    for path in paths:
        for code, detail in check_text(path.read_text(encoding="utf-8"), run_log):
            print(f"wasm_report: FAIL {code} {path.name}: {detail}")
            failed = True
    if not failed:
        print(f"wasm_report: ok, {len(paths)} files")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="step", required=True)
    made = sub.add_parser("render")
    for name in ("native", "native-stderr", "browser", "browser-stderr", "out"):
        made.add_argument(f"--{name}", type=Path, required=True)
    checked = sub.add_parser("check")
    checked.add_argument("files", type=Path, nargs="*")
    args = parser.parse_args(argv)
    if args.step == "check":
        return check(args.files or sorted((REPO_ROOT / "results").glob("wasm_*.md")))
    try:
        text = render(json.loads(args.native.read_text(encoding="utf-8")),
                      json.loads(args.browser.read_text(encoding="utf-8")),
                      args.native_stderr.read_text(encoding="utf-8"),
                      args.browser_stderr.read_text(encoding="utf-8"))
    except (ReportError, KeyError, ValueError) as error:
        print(f"wasm_report: FAIL refused: {error!r}")
        return 1
    log_path = REPO_ROOT / "results" / "run_log.md"
    violations = list(check_text(text, log_path.read_text(encoding="utf-8")))
    if violations:
        for code, detail in violations:
            print(f"wasm_report: FAIL {code} {args.out.name}: {detail}")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wasm_report: wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
