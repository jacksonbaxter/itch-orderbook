#!/usr/bin/env python3
"""T-MM-2: runs itch_as_tables, writes its results file, and checks a results file against a rerun.

    as_tables_report.py run --tool TOOL --raw RAW --prediction P-NNNN [--out FILE] [--root DIR]
    as_tables_report.py check --tool TOOL [FILE]

`run` runs the tool with its defaults, the paper's parameters, keeps the tool's output in RAW,
which is not committed, writes the results file (results/mm_as_tables.md by default), and
appends the run to results/run_log.md. The prediction must already be committed.

`check` reruns the tool and requires the results file's body, every figure and verdict in it,
to be what the rerun renders now. The simulation is seeded and deterministic, so on the host
and compiler that wrote the file a difference is an edit or a change to the model. It also
requires the header to name a prediction, a run and a raw log.

What "matches" means, here and in the file:
  - The average spread is the same on every path, since eq. 30 does not depend on q. It
    matches when it rounds to the paper's figure at the paper's two decimals. No tolerance.
  - Profit, final q and their standard deviations are estimates, and so are the paper's: two
    independent sets of 1000 paths. Each is consistent when it lies within two standard
    errors of the difference of the two estimates, plus half a unit of the paper's last
    printed digit, the rounding the paper applied. The standard error of a mean is
    sqrt(s_ours^2 + s_paper^2) / sqrt(n); of a standard deviation, sqrt(s_ours^2 +
    s_paper^2) / sqrt(2(n - 1)), its large-sample form. Both are fixed by prediction P-0201.

Each violation prints as `as_tables_report: FAIL <code> <where>: <detail>`.

Exit codes: 0 done or the file holds, 1 a violation, 2 bad usage or the tool failed.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import platform
import re
import shlex
import subprocess
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = "results/mm_as_tables.md"
RUN_LOG = "results/run_log.md"
BODY_START = "## What matches means"

# Avellaneda and Stoikov 2008, Tables 1 to 3, pp. 222-223 (market-maker.md, MK-8 to MK-10), as
# printed: average spread, then for each strategy profit, std (profit), final q, std (final q).
PAPER = (
    ("0.1", "Table 1", "1.49", {"inventory": ("65.0", "6.6", "0.08", "2.9"),
                                "symmetric": ("68.4", "12.7", "0.26", "8.4")}),
    ("0.01", "Table 2", "1.35", {"inventory": ("68.6", "8.7", "0.12", "5.1"),
                                 "symmetric": ("68.8", "12.8", "0.09", "8.7")}),
    ("1", "Table 3", "3.02", {"inventory": ("31.4", "5.0", "0.02", "1.7"),
                              "symmetric": ("44.0", "11.0", "0.00", "5.1")}),
)
COLUMNS = ("Profit", "Std (Profit)", "Final q", "Std (Final q)")
STRATEGIES = ("inventory", "symmetric")
SE_MULTIPLE = 2


class ReportError(Exception):
    """The tool's output or the results file cannot be used."""


def parse_raw(text):
    """{'paths': n, 'header': line, 'tables': {gamma_text: {...}}} from the tool's output."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("as_tables "):
        raise ReportError("the output does not start with the tool's header line")
    header = lines[0]
    paths = int(re.search(r"\bpaths (\d+)\b", header)[1])
    tables = {}
    current = None
    for line in lines[1:]:
        words = line.split()
        if words[:2] == ["table", "gamma"]:
            current = tables.setdefault(gamma_key(words[2]), {"strategies": {}})
        elif current is None:
            raise ReportError(f"a line before any table: {line}")
        elif words and words[0].startswith("spread_"):
            current[words[0]] = float(words[1])
        elif words[:1] == ["strategy"]:
            values = dict(zip(words[2::2], (float(word) for word in words[3::2])))
            current["strategies"][words[1]] = values
        else:
            raise ReportError(f"a line the report does not know: {line}")
    for gamma, _, _, _ in PAPER:
        table = tables.get(gamma)
        if table is None or set(table["strategies"]) != set(STRATEGIES):
            raise ReportError(f"no complete table for gamma {gamma}")
    return {"paths": paths, "header": header, "tables": tables}


def gamma_key(text):
    """The paper's spelling of a gamma the tool printed: 1 for 1.0."""
    value = Decimal(text).normalize()
    return format(value, "f")


def round_half_up(value, places):
    return Decimal(repr(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def decimals(printed):
    return len(printed.split(".")[1]) if "." in printed else 0


def compare(measured, measured_spread, paper_text, paper_spread, paths, is_mean):
    """(difference, standard error, z, allowance, consistent) for one column."""
    paper = float(paper_text)
    spreads = math.hypot(measured_spread, paper_spread)
    error = spreads / math.sqrt(paths) if is_mean else spreads / math.sqrt(2 * (paths - 1))
    half_unit = 0.5 * 10 ** -decimals(paper_text)
    allowance = SE_MULTIPLE * error + half_unit
    difference = measured - paper
    z = difference / error if error > 0 else math.inf
    return difference, error, z, allowance, abs(difference) <= allowance


def render_body(parsed):
    paths = parsed["paths"]
    lines = [BODY_START, "",
             "- **Average spread.** Eq. 30 does not depend on q, so the average spread is the "
             "same on every path. It matches when it rounds to the paper's figure at the "
             "paper's two decimals. No tolerance.",
             "- **Every other column.** Profit, final q and their standard deviations are "
             f"estimates, and so are the paper's: two independent sets of {paths:,} paths. Each "
             f"is consistent when it lies within {SE_MULTIPLE} standard errors of the "
             "difference of the two estimates, plus half a unit of the paper's last printed "
             "digit. The standard error of a mean is sqrt(s_ours² + s_paper²)/sqrt(n), and of a "
             "standard deviation sqrt(s_ours² + s_paper²)/sqrt(2(n − 1)). With 24 such "
             "comparisons, about one outside is expected by chance.",
             "- Both rules were committed in prediction P-0201 before any path ran.", ""]
    spreads = []
    matched = 0
    for gamma, _, paper_spread, _ in PAPER:
        table = parsed["tables"][gamma]
        measured = table["spread_average"]
        shown = round_half_up(measured, 2)
        ok = shown == Decimal(paper_spread)
        matched += ok
        spreads.append((gamma, paper_spread, measured, shown, ok, table))
    verdict = "PASS" if matched == len(PAPER) else "FAIL"
    lines += ["## T-MM-2: the average spread", "",
              f"**Verdict: {verdict}**", "",
              f"{matched} of {len(PAPER)} average spreads match the paper at two decimals.", "",
              "| γ | Paper | Measured | At two decimals | Verdict |", "|---|---|---|---|---|"]
    for gamma, paper_spread, measured, shown, ok, _ in spreads:
        lines.append(f"| {gamma} | {paper_spread} | {measured:.4f} | {shown} | "
                     f"{'matches' if ok else 'does not match'} |")
    lines += ["", "The simulated grid quotes at t = n·dt for n = 0 to 199, so T − t averages "
                  "1 − 199·dt/2 = 0.5025 over the steps. Eq. 30 averaged over t in [0, 1], "
                  "where T − t averages exactly 1/2, is the closed form the evidence works by "
                  "hand (MK-11). Two other grids, shown as a sensitivity and not as the "
                  "measurement:", "",
              "| γ | Simulated grid | Grid with t = T, 201 points | Grid from t = dt to T |",
              "|---|---|---|---|"]
    inclusive = 0
    for gamma, paper_spread, _, _, _, table in spreads:
        inclusive += round_half_up(table["spread_grid_with_t_equal_T"], 2) == Decimal(paper_spread)
        lines.append(f"| {gamma} | {table['spread_grid_simulated']:.4f} | "
                     f"{table['spread_grid_with_t_equal_T']:.4f} | "
                     f"{table['spread_grid_from_dt']:.4f} |")
    shifts = ", ".join(f"{table['spread_grid_simulated'] - table['spread_grid_with_t_equal_T']:.4f}"
                       for _, _, _, _, _, table in spreads)
    lines += ["", "Neighbouring grids differ by γσ²·dt/2, here "
                  f"{shifts}: the spread's slope in T − t times the half step. On the 201-point "
                  f"grid, {inclusive} of {len(PAPER)} round to the paper's figures. The paper "
                  "does not state its grid.", ""]
    consistent = 0
    total = 0
    outside = []
    for gamma, name, _, paper_rows in PAPER:
        table = parsed["tables"][gamma]
        lines += [f"## {name}, γ = {gamma}", "",
                  "| Strategy | Column | Paper | Measured | Difference | Standard error | z | "
                  "Allowed | Within |", "|---|---|---|---|---|---|---|---|---|"]
        for strategy in STRATEGIES:
            ours = table["strategies"][strategy]
            paper = paper_rows[strategy]
            columns = (
                (COLUMNS[0], ours["profit_mean"], ours["profit_std"], paper[0], paper[1], True),
                (COLUMNS[1], ours["profit_std"], ours["profit_std"], paper[1], paper[1], False),
                (COLUMNS[2], ours["final_q_mean"], ours["final_q_std"], paper[2], paper[3], True),
                (COLUMNS[3], ours["final_q_std"], ours["final_q_std"], paper[3], paper[3], False),
            )
            for column, measured, spread, paper_text, paper_spread_text, is_mean in columns:
                difference, error, z, allowance, ok = compare(
                    measured, spread, paper_text, float(paper_spread_text), parsed["paths"],
                    is_mean)
                total += 1
                consistent += ok
                if not ok:
                    outside.append(f"{name} {strategy} {column}")
                lines.append(f"| {strategy} | {column} | {paper_text} | {measured:.3f} | "
                             f"{difference:+.3f} | {error:.3f} | {z:+.2f} | {allowance:.3f} | "
                             f"{'yes' if ok else 'no'} |")
        lines.append("")
        lines.append("Fills per path: inventory "
                     f"{table['strategies']['inventory']['fills_per_path']:.3f}, symmetric "
                     f"{table['strategies']['symmetric']['fills_per_path']:.3f}.")
        lines.append("")
    lines += ["## Summary", "",
              f"- Average spread: {matched} of {len(PAPER)} match at the paper's two decimals.",
              f"- Other columns: {consistent} of {total} within {SE_MULTIPLE} standard errors"
              + (f"; outside: {', '.join(outside)}." if outside else "."),
              "- Synthetic Monte Carlo of the paper's own model. It tests this implementation of "
              "the quoting model, and says nothing about real data."]
    return lines


def render(parsed, header):
    return "\n".join(header + [""] + render_body(parsed)) + "\n"


def run_tool(tool):
    result = subprocess.run([str(tool)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ReportError(f"{tool} exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                            check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def host_line():
    system, machine = platform.system(), platform.machine()
    cpu = ""
    if system == "Darwin":
        cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                             text=True, check=False).stdout.strip()
    label = "Mac numbers stay labelled macOS." if system == "Darwin" else "Linux host."
    return f"{system} {machine} {cpu}".strip() + f". {label}"


def compiler_and_flags(root, tool):
    """The compiler's first line and the tool's flags, from the build's compile commands."""
    sys.path.insert(0, str(root / "scripts"))
    from oracle_report import tool_flags  # noqa: PLC0415

    commands = Path(tool).resolve().parent.parent / "compile_commands.json"
    flags = (tool_flags(str(commands), "tools/itch_as_tables.cpp") if commands.is_file()
             else "unavailable")
    compiler = "unavailable"
    entries = json.loads(commands.read_text(encoding="utf-8")) if commands.is_file() else []
    for entry in entries:
        if entry["file"].endswith("tools/itch_as_tables.cpp"):
            program = entry["arguments"][0] if "arguments" in entry else shlex.split(
                entry["command"])[0]
            version = subprocess.run([program, "--version"], capture_output=True, text=True,
                                     check=False).stdout.splitlines()
            compiler = version[0].split(" (")[0] if version else "unavailable"
            break
    return compiler, flags


def command_run(args):
    root = args.root.resolve()
    committed = subprocess.run([sys.executable, "-B", str(root / "scripts/check_predictions.py"),
                                "--root", str(root), "--require", args.prediction],
                               capture_output=True, text=True, check=False)
    if committed.returncode != 0:
        print(committed.stdout.strip())
        return 1
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{args.prediction}-{stamp}"
    status = git(root, "status", "--porcelain", "--untracked-files=no")
    commit = git(root, "rev-parse", "--short=12", "HEAD") or "unavailable"
    commit_cell = f"{commit} ({'clean' if status == '' else 'modified'})"
    log_path = root / RUN_LOG
    try:
        raw = run_tool(args.tool)
        args.raw.write_text(raw, encoding="utf-8")
        parsed = parse_raw(raw)
    except (ReportError, OSError) as error:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"| {run_id} | {args.prediction} | {commit_cell} | {platform.system()} | "
                      f"refused: {error} |\n")
        print(f"as_tables_report: FAIL run {error}")
        return 2
    compiler, flags = compiler_and_flags(root, args.tool)
    header = ["# T-MM-2: Avellaneda and Stoikov's tables by Monte Carlo", "",
              "Generated by `scripts/as_tables_report.py` from a raw log, which is not "
              "committed. Do not edit by hand. `as_tables_report.py check` reruns the tool and "
              "holds this file to it.", "",
              f"- prediction: {args.prediction}",
              f"- run_id: {run_id}",
              f"- Commit: {commit_cell}",
              f"- Compiler: {compiler}",
              f"- Flags: `{flags}`",
              f"- Host: {host_line()}",
              "- Command: `itch_as_tables`, its defaults: s = 100, T = 1, σ = 2, dt = 0.005, "
              "q = 0, k = 1.5, A = 140, 1000 paths a table, γ = 0.1, 0.01 and 1, seed 20080401",
              f"- Tool header: `{parsed['header']}`",
              f"- Raw log: `{args.raw.name}`",
              "- Synthetic: no market data. The model, the fill rule and the grid are in the "
              "header comment of `tools/itch_as_tables.cpp`."]
    out = root / args.out
    out.write_text(render(parsed, header), encoding="utf-8")
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"| {run_id} | {args.prediction} | {commit_cell} | {platform.system()} | "
                  "published |\n")
    print(f"as_tables_report: wrote {out.relative_to(root)}, run {run_id}")
    return 0


def check_text(text, raw, where):
    """The violations of a results file against the tool's output now."""
    violations = []
    for field in ("prediction", "run_id"):
        if re.search(rf"^- {field}: \S+", text, re.MULTILINE) is None:
            violations.append(("header-field-missing", where, f"no {field}"))
    if re.search(r"^- Raw log: `[^`]+`", text, re.MULTILINE) is None:
        violations.append(("header-field-missing", where, "no raw log"))
    if BODY_START not in text:
        violations.append(("body-missing", where, f"no '{BODY_START}'"))
        return violations
    body = text[text.index(BODY_START):].rstrip("\n").split("\n")
    fresh = render_body(parse_raw(raw))
    for index in range(max(len(body), len(fresh))):
        have = body[index] if index < len(body) else "<no line>"
        want = fresh[index] if index < len(fresh) else "<no line>"
        if have != want:
            violations.append(("differs-from-rerun", where,
                               f"line {index + 1} of the body: have {have!r}, want {want!r}"))
            break
    return violations


def command_check(args):
    path = Path(args.file)
    try:
        text = path.read_text(encoding="utf-8")
        violations = check_text(text, run_tool(args.tool), path.name)
    except (ReportError, OSError) as error:
        print(f"as_tables_report: FAIL unusable {path.name}: {error}")
        return 2
    for code, where, detail in violations:
        print(f"as_tables_report: FAIL {code} {where}: {detail}")
    if violations:
        return 1
    print(f"as_tables_report: ok {path.name}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--tool", type=Path, required=True)
    run.add_argument("--raw", type=Path, required=True)
    run.add_argument("--prediction", required=True)
    run.add_argument("--out", default=DEFAULT_OUT)
    run.add_argument("--root", type=Path, default=REPO_ROOT)
    check = commands.add_parser("check")
    check.add_argument("--tool", type=Path, required=True)
    check.add_argument("file", nargs="?", default=str(REPO_ROOT / DEFAULT_OUT))
    args = parser.parse_args(argv)
    return command_run(args) if args.command == "run" else command_check(args)


if __name__ == "__main__":
    sys.exit(main())
