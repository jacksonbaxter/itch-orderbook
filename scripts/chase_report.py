#!/usr/bin/env python3
"""Says what the pointer chase proves about the counters, and writes it down.

    chase_report.py --record RUN.json... --load STDERR... --machine-check OUT... --sysfs FILE \\
        --prediction P-NNNN --out results/chase_debian.md [--run-log results/run_log.md]
    chase_report.py --check FILE [--record RUN.json... --load STDERR...
        --machine-check OUT... --sysfs FILE --prediction P-NNNN]

bench_chase records raw counts and judges nothing. Here each criterion of the prediction
is applied to every run, not to a median, and the verdict is the AND of them: a verdict
of PASS allows `r0843:u` to stand in for last-level misses, and anything else means no
last-level number is published.

--check reads a published file and judges its own tables again, so that a verdict cannot
say more than the table under it. Given the raw records as well, it rebuilds the file and
compares the two, so that a table cannot say more than the run.

Each violation prints as `chase_report: FAIL <code>: <detail>`.

Exit codes: 0 the verdict is PASS, or --check found nothing wrong, 1 the verdict is FAIL or
--check found something, 2 bad usage or unreadable input.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402
import stamp_load  # noqa: E402

KIB = 1024
MIB = 1024 * KIB
L2_BYTES = 512 * KIB
L3_BYTES = 4 * MIB
EXPECTED_RAW = "0x0843"
EXPECTED_SYSFS = "event=0x64,umask=0x09"
FILLS = "dram_fills"
MISSES = "cache-misses (L2 misses on Zen 1)"
RAW_MISSES = "l2_misses_raw"
UNDER_L2 = (64 * KIB, 128 * KIB)
BETWEEN = (MIB, 2 * MIB, 3 * MIB)
ABOVE_L3 = (64 * MIB, 256 * MIB)
FILLS_UNDER_L2_MAX = 0.01
FILLS_ABOVE_L3 = (0.90, 1.10)
MISSES_BETWEEN = (0.70, 1.30)
MARGIN_MIN = 0.50
GAP_MAX = 0.001
TABLE_ROW = re.compile(r"^\| (\d+ [KM]B) \| (.+) \|$")
SYSFS_LINE = re.compile(r"^cache-misses: (\S+)$", re.MULTILINE)
VERDICT = re.compile(r"^\*\*Verdict: (PASS|FAIL)\*\*", re.MULTILINE)
CRITERION = re.compile(r"^\| (.+?) \| (pass|FAIL) \| .* \|$", re.MULTILINE)
COMMIT_FIELD = re.compile(r"^[0-9a-f]{12} ")
HUGE_KB = re.compile(r"(\d+) kB in transparent hugepages")

DOCUMENT_TEXT = """\
AMD, *Processor Programming Reference (PPR) for AMD Family 17h Model 18h, Revision B1
Processors*, publication 55570-B1, Rev 3.14, Sep 26, 2019, read from the cached PDF.

- PMCx064, p. 170: "Core to L2 Cacheable Request Access Status"
  (`Core::X86::Pmc::Core::L2CacheReqStat`), "L2 Cache Request Outcomes (not including L2
  Prefetch)". Bit 3 is `LsRdBlkC`, "Data Cache Req Miss in L2 (all types)". Bit 0 is
  `IcFillMiss`, "Instruction Cache Req Miss in L2". Bits 3 and 0 make unit mask 0x09, and
  with event 0x64 that is the raw value 0x0964.
- PMCx043, p. 162: "Data Cache Refills from System", "Demand Data Cache Fills by Data
  Source". Bit 3 is `LS_MABRESP_LCL_DRAM`, "From DRAM (home node local)", which makes unit
  mask 0x08 and the raw value 0x0843.

The document is for Model 18h, and this CPU is family 23 model 17, which is Model 11h. No
PPR for Model 11h could be found, so the bit definitions are read from the nearest one and
the measurement below is what holds them to this part."""

CAVEATS = """\
- A random cycle gives the prefetchers nothing to follow, so this says nothing about
  prefetched lines. By its definition `r0843:u` counts demand fills only, so a line a
  prefetcher brought in first is not counted, and such a load is a last-level miss that
  this event does not see.
- `r0843:u` is a stand-in: it counts demand data fills from local DRAM, on this core. Code
  fetches are outside its definition, and whether the page walker's fills are counted is
  not stated.
- `cache-misses` passes one per load above the L3 size, {top_misses:.2f} at {top_size} on 4 KB
  pages, where `r0843:u` is {top_fills:.2f}. The chase has one demand load per step, so the
  excess is not demand data. The page walker is the likely source, but nothing here
  measures that.
- The sizes between L2 and L3 are not free of last-level misses: at {last_between} `r0843:u`
  reaches {last_fills:.4f}, because L3 is shared and 4 KB pages land unevenly in its sets."""


def size_label(size):
    return f"{size // MIB} MB" if size >= MIB else f"{size // KIB} KB"


def label_size(label):
    number, unit = label.split()
    return int(number) * (MIB if unit == "MB" else KIB)


def level_of(size):
    if size < L2_BYTES:
        return "under L2"
    return "between L2 and L3" if size < L3_BYTES else "above L3"


def event(row, name):
    for entry in row["events"]:
        if entry["name"] == name:
            return entry
    raise ValueError(f"the record has no event {name}")


def per_load(record, row, name, path):
    return event(row, name)[path] / record["loads"]


def hugepage_kb(row):
    """kB of the buffer in hugepages, or -1 where the kernel gave no account."""
    match = HUGE_KB.search(row["pages"])
    return int(match.group(1)) if match else -1


def summarise(records):
    """One entry per size: the median of each figure, and the worst of the runs."""
    sizes = [row["bytes"] for row in records[0]["rows"]]
    summary = []
    for position, size in enumerate(sizes):
        rows = [record["rows"][position] for record in records]
        if any(row["bytes"] != size for row in rows):
            raise ValueError("the runs did not chase the same sizes")
        figures = {name: [per_load(record, row, *key) for record, row in zip(records, rows)]
                   for name, key in (("fills", (FILLS, "read")), ("fills_rdpmc", (FILLS, "rdpmc")),
                                     ("misses", (MISSES, "read")),
                                     ("misses_rdpmc", (MISSES, "rdpmc")),
                                     ("raw_misses", (RAW_MISSES, "read")))}
        gaps = [abs(left - right) for pair in (("fills", "fills_rdpmc"),
                                               ("misses", "misses_rdpmc"))
                for left, right in zip(figures[pair[0]], figures[pair[1]])]
        ratios = [entry["ratio"] for row in rows for entry in row["events"]]
        summary.append({
            "size": size,
            "ns": statistics.median(row["ns_per_load"] for row in rows),
            **{name: statistics.median(values) for name, values in figures.items()},
            "fills_min": min(figures["fills"]), "fills_max": max(figures["fills"]),
            "misses_min": min(figures["misses"]), "misses_max": max(figures["misses"]),
            "margin_min": min(miss - fill for miss, fill in zip(figures["misses"],
                                                                figures["fills"])),
            "gap_max": max(gaps),
            "raw_gap_max": max(abs(left - right) for left, right in zip(figures["raw_misses"],
                                                                        figures["misses"])),
            "ratio_min": min(ratios),
            "huge_kb": max(hugepage_kb(row) for row in rows) if min(
                hugepage_kb(row) for row in rows) >= 0 else -1})
    return summary


def judge(summary, raw_event, sysfs):
    """Every criterion of the prediction, as (name, passed, detail), in its order."""
    table = {entry["size"]: entry for entry in summary}
    missing = [size_label(size) for size in (*UNDER_L2, *BETWEEN, *ABOVE_L3) if size not in table]
    results = [("every size was run", not missing, ", ".join(missing) or "all present"),
               ("fill event is PMCx043 unit mask 0x08", raw_event == EXPECTED_RAW,
                f"raw event {raw_event}")]
    if missing:
        return results

    def worst(sizes, key, choose):
        return choose(table[size][key] for size in sizes)

    fills = worst(UNDER_L2, "fills_max", max)
    results.append((f"under L2: fills at most {FILLS_UNDER_L2_MAX} per load",
                    fills <= FILLS_UNDER_L2_MAX, f"worst {fills:.4f}"))
    low, high = worst(ABOVE_L3, "fills_min", min), worst(ABOVE_L3, "fills_max", max)
    results.append((f"above L3: fills from {FILLS_ABOVE_L3[0]:.2f} to {FILLS_ABOVE_L3[1]:.2f} "
                    "per load", FILLS_ABOVE_L3[0] <= low and high <= FILLS_ABOVE_L3[1],
                    f"from {low:.4f} to {high:.4f}"))
    low, high = worst(BETWEEN, "misses_min", min), worst(BETWEEN, "misses_max", max)
    results.append((f"between L2 and L3: cache-misses from {MISSES_BETWEEN[0]:.2f} to "
                    f"{MISSES_BETWEEN[1]:.2f} per load",
                    MISSES_BETWEEN[0] <= low and high <= MISSES_BETWEEN[1],
                    f"from {low:.4f} to {high:.4f}"))
    margin = worst(BETWEEN, "margin_min", min)
    results.append((f"between L2 and L3: cache-misses at least {MARGIN_MIN:.2f} above fills",
                    margin >= MARGIN_MIN, f"smallest {margin:.4f}"))
    gap = max(entry["gap_max"] for entry in summary)
    results.append((f"rdpmc and read() within {GAP_MAX} per load, both events", gap <= GAP_MAX,
                    f"widest {gap:.6f}"))
    ratio = min(entry["ratio_min"] for entry in summary)
    results.append(("every event ran for the whole span", ratio == 1.0, f"lowest {ratio:.4f}"))
    huge = [entry["huge_kb"] for entry in summary]
    results.append(("every buffer on 4 KB pages", all(kb == 0 for kb in huge),
                    f"hugepage kB by size: {' '.join(str(kb) for kb in huge)}"))
    results.append((f"kernel maps cache-misses to {EXPECTED_SYSFS}", sysfs == EXPECTED_SYSFS,
                    f"sysfs says {sysfs}"))
    gap = max(entry["raw_gap_max"] for entry in summary)
    results.append((f"r0964:u within {GAP_MAX} per load of cache-misses", gap <= GAP_MAX,
                    f"widest {gap:.6f}"))
    return results


def read_sysfs(text):
    match = SYSFS_LINE.search(text)
    return match.group(1) if match else "unavailable: no cache-misses line"


def load_lines(stderr_text):
    """The run's two measure.sh lines, with the machine's hostname replaced."""
    start = stamp_load.one_line(stamp_load.START, stderr_text, "start")
    end = stamp_load.one_line(stamp_load.END, stderr_text, "end")
    return tuple(stamp_load.HOST.sub("host=debian", line) for line in (start, end))


def timestamp(line):
    match = check_results.TIMESTAMP.search(line)
    if match is None:
        raise ValueError(f"no timestamp in {line!r}")
    return match.group(1)


def agreement_line(results):
    mapped = [good for name, good, _ in results if "cache-misses" in name and "maps" in name]
    counted = [good for name, good, _ in results if name.startswith("r0964:u")]
    if mapped == [True] and counted == [True]:
        return ("The document, the kernel and the count agree: `r0964:u`, opened as a raw event "
                "in the same group as `cache-misses`, tracks it in every row above.")
    return "**The document, the kernel and the count do not agree. See the criteria above.**"


def caveats(summary):
    between = [entry for entry in summary if entry["size"] in BETWEEN]
    return CAVEATS.format(top_misses=summary[-1]["misses"], top_size=size_label(summary[-1]["size"]),
                          top_fills=summary[-1]["fills"],
                          last_between=size_label(between[-1]["size"]) if between else "none",
                          last_fills=between[-1]["fills_max"] if between else 0.0)


def render(records, stderrs, machine_checks, sysfs_text, prediction):
    summary = summarise(records)
    sysfs = read_sysfs(sysfs_text)
    raw_event = records[0]["raw_event"]
    results = judge(summary, raw_event, sysfs)
    passed = all(result[1] for result in results)
    lines = [load_lines(text) for text in stderrs]
    run_id = f"{prediction}-" + timestamp(lines[0][0]).replace(":", "").replace("-", "")
    header = records[0]["header"]
    peak = max(record["peak_rss_bytes"] for record in records) / 1024 ** 2
    migrations = sum(record["migrations"] for record in records)
    out = ["# Pointer chase against the Zen 1 counters", "",
           "Generated by `scripts/chase_report.py` from raw records that are not committed. Do "
           "not edit by hand.", "", f"Host: **{header['host']}**.", "", "## Header", ""]
    out += [f"- {name}: {value}" for name, value in header.items()
            if name not in ("dataset", "dataset_sha256", "residency")]
    out += [f"- prediction: {prediction}", f"- run_id: {run_id}", f"- runs: {len(records)}",
            f"- loads_per_size: {records[0]['loads']:,}", f"- raw_event: {raw_event}",
            f"- migrations_all_runs: {migrations}", f"- peak_rss: {peak:.0f} MiB, getrusage of "
            "bench_chase, largest run", f"- run_started: {timestamp(lines[0][0])}",
            f"- run_finished: {timestamp(lines[-1][1])}", "",
            "## Per size", "",
            f"Median of {len(records)} fresh processes, per load, on 4 KB pages. `r0843:u` is "
            "demand fills from local DRAM. `cache-misses` is the kernel's name for `r0964:u`.",
            "", "| Buffer | Where it sits | ns/load | r0843:u read() | r0843:u rdpmc | "
            "cache-misses read() | cache-misses rdpmc | r0964:u read() |",
            "|---|---|---|---|---|---|---|---|"]
    for entry in summary:
        out.append(f"| {size_label(entry['size'])} | {level_of(entry['size'])} "
                   f"| {entry['ns']:.2f} | {entry['fills']:.4f} | {entry['fills_rdpmc']:.4f} "
                   f"| {entry['misses']:.4f} | {entry['misses_rdpmc']:.4f} "
                   f"| {entry['raw_misses']:.4f} |")
    out += ["", "## Worst of the runs", "",
            "What the criteria are judged on. Gaps are per load: `rdpmc` against `read()` on "
            "the two events, and `r0964:u` against `cache-misses`.", "",
            "| Buffer | r0843:u min | r0843:u max | cache-misses min | cache-misses max "
            "| Smallest margin | Widest rdpmc gap | Widest r0964 gap | Lowest running ratio "
            "| Hugepage kB |", "|---|---|---|---|---|---|---|---|---|---|"]
    for entry in summary:
        out.append(f"| {size_label(entry['size'])} | {entry['fills_min']:.4f} "
                   f"| {entry['fills_max']:.4f} | {entry['misses_min']:.4f} "
                   f"| {entry['misses_max']:.4f} | {entry['margin_min']:.4f} "
                   f"| {entry['gap_max']:.6f} | {entry['raw_gap_max']:.6f} "
                   f"| {entry['ratio_min']:.4f} | {entry['huge_kb']} |")
    out += ["", "## Verdict", "", f"**Verdict: {'PASS' if passed else 'FAIL'}**", ""]
    if passed:
        out += [f"Every criterion of {prediction} held in every run. `r0843:u` follows the "
                "buffer size as a count of DRAM fills should, so it may stand in for last-level "
                "misses on this host. `cache-misses` reaches nearly one per load while `r0843:u` "
                "stays near zero, so it counts L2 misses and any write-up must call it that.", ""]
    else:
        out += [f"At least one criterion of {prediction} failed, so no last-level number may be "
                "published from this event. The gap stands until a run passes.", ""]
    out += ["| Criterion | Result | Measured |", "|---|---|---|"]
    out += [f"| {name} | {'pass' if good else 'FAIL'} | {detail} |"
            for name, good, detail in results]
    out += ["", "## What `cache-misses` is", "", "The kernel's mapping on this host, from "
            "`/sys/bus/event_source/devices/cpu/events/cache-misses`, which reads the map in the "
            f"running kernel rather than `perf list`'s tables: `{sysfs}`.", "", DOCUMENT_TEXT, "",
            agreement_line(results), "",
            "## What this does not show", "", caveats(summary), "",
            "## machine_check output", "", "One before each run, under `measure.sh` and "
            "`taskset -c 4`. A chase has no input, so a small fixture stands in for the "
            "residency line.", "", "```"]
    out += [text.strip() for text in machine_checks]
    out += ["```", "", "## measure.sh load lines", "", "```"]
    out += [line for pair in lines for line in pair]
    out += ["```", "", "## Command", "", "```",
            "measure.sh taskset -c 4 build/bench/bench_chase --core 4 --commit <commit> "
            "--out <raw>/run-N.json", "```", ""]
    return "\n".join(out) + "", passed, run_id


def published_summary(text):
    """The figures of a published file, as summarise() gives them, rounded as printed."""
    figures = {}
    for line in (check_results.section(text, "Per size") or "").splitlines():
        match = TABLE_ROW.match(line)
        if match:
            cells = [cell.strip() for cell in match.group(2).split("|")]
            figures[label_size(match.group(1))] = {
                "size": label_size(match.group(1)), "ns": float(cells[1]),
                "fills": float(cells[2]), "fills_rdpmc": float(cells[3]),
                "misses": float(cells[4]), "misses_rdpmc": float(cells[5]),
                "raw_misses": float(cells[6])}
    for line in (check_results.section(text, "Worst of the runs") or "").splitlines():
        match = TABLE_ROW.match(line)
        if match and label_size(match.group(1)) in figures:
            cells = [float(cell) for cell in match.group(2).split("|")]
            figures[label_size(match.group(1))].update(
                zip(("fills_min", "fills_max", "misses_min", "misses_max", "margin_min",
                     "gap_max", "raw_gap_max", "ratio_min", "huge_kb"), cells))
    return list(figures.values())


def check_published(text):
    """What is wrong with a published file, read alone, as (code, detail) pairs."""
    fields = check_results.parse_header(text)
    if not fields:
        return [("header-missing", "no Header section")]
    found = []
    if fields.get("host") != "debian":
        found.append(("host-label", repr(fields.get("host"))))
    if not COMMIT_FIELD.match(fields.get("commit", "")):
        found.append(("commit-missing", repr(fields.get("commit"))))
    if not re.match(r"pinned to core 4: .*read back \[4\]$", fields.get("pinning", "")):
        found.append(("pin-failed", fields.get("pinning", "no field")))
    if fields.get("governor") != "performance":
        found.append(("governor", fields.get("governor", "no field")))
    for name, pattern in (("home path", check_results.HOME_PATH),
                          ("address", check_results.ADDRESS)):
        match = pattern.search(text)
        if match:
            found.append(("private-text", f"{name}: {match.group(0)}"))
    for line in (check_results.section(text, "measure.sh load lines") or "").splitlines():
        code = check_results.EXIT_CODE.search(line)
        if code is not None and code.group(1) != "0":
            found.append(("load-line-invalid", line))
        host = check_results.HOST_WORD.search(line)
        if host is not None and host.group(1) != "debian":
            found.append(("load-line-hostname", f"host={host.group(1)}"))
    if "machine_check: passed" not in (check_results.section(text, "machine_check output") or ""):
        found.append(("machine-check-failed", "the embedded output does not pass"))
    summary = published_summary(text)
    if not summary or any(len(entry) < 16 for entry in summary):
        return [*found, ("table-missing", "the per-size tables are not whole")]
    sysfs = re.search(r"`(event=\S+)`", text)
    results = judge(summary, fields.get("raw_event", "none"),
                    sysfs.group(1) if sysfs else "none")
    verdict = VERDICT.search(text)
    expected = "PASS" if all(result[1] for result in results) else "FAIL"
    if verdict is None or verdict.group(1) != expected:
        found.append(("verdict-faked", f"the tables give {expected}, the file says "
                                       f"{verdict.group(1) if verdict else 'nothing'}"))
    printed = dict(CRITERION.findall(check_results.section(text, "Verdict") or ""))
    for name, good, _ in results:
        if printed.get(name) != ("pass" if good else "FAIL"):
            found.append(("criterion-faked", f"{name}: printed {printed.get(name)}"))
    return found


def read_inputs(args):
    records = [json.loads(path.read_text(encoding="utf-8")) for path in args.record]
    stderrs = [path.read_text(encoding="utf-8", errors="replace") for path in args.load]
    checks = [path.read_text(encoding="utf-8") for path in args.machine_check]
    if not records or len(records) != len(stderrs) or len(records) != len(checks):
        raise ValueError("wanted one --record, --load and --machine-check for each run")
    for record in records:
        if record["refusals"]:
            raise ValueError(f"a refused run: {'; '.join(record['refusals'])}")
    return records, stderrs, checks, args.sysfs.read_text(encoding="utf-8")


def append_log(path, run_id, prediction, commit, passed):
    outcome = "chase verdict PASS" if passed else "chase verdict FAIL"
    with open(path, "a", encoding="utf-8") as log:
        log.write(f"| {run_id} | {prediction} | {commit} | Linux | {outcome} |\n")


def report_violations(violations):
    for code, detail in violations:
        print(f"chase_report: FAIL {code}: {detail}")
    return 1 if violations else 0


def run_check(args):
    text = args.check.read_text(encoding="utf-8")
    violations = check_published(text)
    if args.record:
        rebuilt = render(*read_inputs(args), args.prediction)[0]
        if rebuilt != text:
            violations.append(("table-differs", "the file is not what the raw records give"))
    if violations:
        return report_violations(violations)
    print(f"chase_report: ok {args.check.name}")
    return 0


def run_report(args):
    text, passed, run_id = render(*read_inputs(args), args.prediction)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    if args.run_log:
        append_log(args.run_log, run_id, args.prediction,
                   check_results.parse_header(text)["commit"], passed)
    for line in check_results.section(text, "Verdict").splitlines():
        match = CRITERION.match(line)
        if match and match.group(2) == "FAIL":
            print(f"chase_report: FAIL criterion: {match.group(1)}")
    print(f"chase_report: wrote {args.out.name}, verdict {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", type=Path, nargs="+", default=[])
    parser.add_argument("--load", type=Path, nargs="+", default=[])
    parser.add_argument("--machine-check", type=Path, nargs="+", default=[])
    parser.add_argument("--sysfs", type=Path)
    parser.add_argument("--prediction")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--run-log", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args(argv)
    if args.record and (not args.sysfs or not args.prediction):
        parser.error("the raw records need --sysfs and --prediction")
    if args.check is None and (args.out is None or not args.record):
        parser.error("wanted --check FILE, or the raw records and --out")
    try:
        return run_check(args) if args.check else run_report(args)
    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
        print(f"chase_report: unusable input: {error!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
