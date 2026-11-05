#!/usr/bin/env python3
"""Writes the Zen 1 counter table: which events are real hardware events on debian with `:u`.

    zen1_events.py --part-a DIR --part-b DIR --record RUN.json --machine-check BEFORE AFTER \\
        --prediction P-NNNN --commit TEXT --dataset NAME --command TEXT \\
        --out results/counters_zen1_events_debian.md
    zen1_events.py --check FILE

Part A is one `perf stat -x,` per event around a replay of the oracle fixture: each event's
CSV, its stderr and its exit code in exits.txt. Part B is the events that counted, four to a
`perf stat`, around a replay of the day: group*.csv. What each event is programmed as comes
from the kernel's Zen 1 tables (arch/x86/events/amd/core.c, Linux 6.12) and sysfs, and what it
counts from AMD's PPR for family 17h; those columns are fixed here, the state and the counts
are read from the run.

On Zen 1, `cache-misses` counts L2 misses and `r0843:u` is a floor on DRAM demand fills, and
every line that names either says so.

--check reads a published file again: the host, nothing private, both machine checks
passed, the names said, and every state one of the four this script writes.

Each violation prints as `zen1_events: FAIL <code>: <detail>`.

Exit codes: 0 written, or --check found nothing, 1 --check found something, 2 bad usage or
unusable input.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402
import counts_report  # noqa: E402
import flame_report  # noqa: E402

# Event, how the kernel programs it on Zen 1, what that counts. Sources in SOURCES.
TABLE = (
    ("cycles", "PMCx076", "Core cycles not halted. Varies with boost clock."),
    ("instructions", "PMCx0C0", "Retired instructions."),
    ("branches", "PMCx0C2", "Retired branch instructions."),
    ("branch-misses", "PMCx0C3", "Retired mispredicted branch instructions."),
    ("cache-references", "PMCx060 umask 0xFF",
     "Every L2 request, prefetches and instruction fetches included. Not LLC references."),
    ("cache-misses", "PMCx064 umask 0x09",
     "L2 misses: data cache requests that miss in L2 and instruction cache requests that miss "
     "in L2, L2 prefetches not included. Not LLC misses."),
    ("stalled-cycles-frontend", "PMCx087 umask 0x02",
     "Cycles the instruction fetch stalls with the decode queue empty. Not a front-end stall "
     "count in Intel's sense."),
    ("stalled-cycles-backend", "PMCx087 umask 0x01",
     "Cycles the instruction fetch stalls on back-pressure from decode. A fetch-side event, "
     "not a back-end stall count."),
    ("ref-cycles", "none", "No entry in the Zen 1 map."),
    ("bus-cycles", "none", "No entry in the Zen 1 map."),
    ("L1-dcache-loads", "PMCx040",
     "Data cache accesses, loads and stores, speculative, in 8-byte units. Not loads only."),
    ("L1-dcache-load-misses", "PMCx060 umask 0xC8",
     "L2 requests from the data cache, stores and change-to-writable included. Not load misses "
     "only."),
    ("L1-dcache-prefetches", "PMCx05A umask 0xFF",
     "Data cache fills from the hardware prefetcher."),
    ("L1-icache-loads", "PMCx080", "32-byte instruction fetch windows, not instructions."),
    ("L1-icache-load-misses", "PMCx081", "32-byte fetch windows that miss the instruction cache."),
    ("LLC-loads", "none", "Zero in the Zen 1 cache table: the kernel refuses it."),
    ("LLC-load-misses", "none", "Zero in the Zen 1 cache table: the kernel refuses it."),
    ("dTLB-loads", "PMCx045 umask 0xFF",
     "L1 DTLB misses or reloads, so L2 DTLB lookups. Not every load."),
    ("dTLB-load-misses", "PMCx045 umask 0xF0", "L2 DTLB misses: page table walks, all page sizes."),
    ("iTLB-loads", "PMCx084", "L1 ITLB misses that hit the L2 ITLB. Not every fetch."),
    ("iTLB-load-misses", "PMCx085 umask 0xFF", "Fetches that miss both ITLB levels."),
    ("branch-loads", "PMCx0C2", "The same event as `branches`."),
    ("branch-load-misses", "PMCx0C3", "The same event as `branch-misses`."),
    ("r0843", "PMCx043 umask 0x08",
     "Demand data cache fills from local DRAM, prefetch fills not included: a floor on DRAM "
     "demand fills (results/chase_debian.md)."),
    ("r0143", "PMCx043 umask 0x01", "Demand data cache fills from the core's own L2."),
    ("r0243", "PMCx043 umask 0x02",
     "Demand data cache fills from another cache in the same complex: L3, or another core's L2."),
    ("r0443", "PMCx043 umask 0x04", "A reserved bit of the unit mask in AMD's PPR."),
    ("r0964", "PMCx064 umask 0x09", "The same encoding as `cache-misses`: L2 misses."),
    ("ls_dc_accesses", "PMCx040", "The same event as `L1-dcache-loads`."),
    ("ex_ret_brn_ind_misp", "PMCx0CA", "Retired indirect branches mispredicted."),
    ("l2_pf_miss_l2_l3", "PMCx072 umask 0xFF",
     "L2 prefetches that miss both L2 and L3: the prefetch side the demand fills leave out."),
    ("l2_request_g1.all_no_prefetch", "PMCx060 umask 0xF9",
     "L2 requests other than prefetches."),
    ("l3_request_g1.caching_l3_cache_accesses", "L3 PMU event 0x01 umask 0x80",
     "L3 accesses, counted per core complex by the L3 PMU, which has no per-task mode."),
    ("l3_comb_clstr_state.request_miss", "L3 PMU event 0x06 umask 0x01",
     "L3 misses, on the L3 PMU, as above."),
)
EXTRA = (("cycles:k", "PMCx076, kernel only"), ("cycles:upp", "IBS, which `:pp` asks for"),
         ("lbr", "`perf record --call-graph lbr`"))
SOURCES = """\
- What each generic event is programmed as: the kernel's `amd_zen1_perfmon_event_map` and
  `amd_hw_cache_event_ids_f17h` in `arch/x86/events/amd/core.c` at Linux 6.12, and
  `/sys/bus/event_source/devices/cpu/events/` on `debian`; for the named events, `perf list
  --details` from the same `perf`.
- What it counts: AMD's Processor Programming Reference for family 17h (55570-B1), PMCx040 to
  PMCx0CA, and the kernel's comments beside each entry. PMCx043 is not in perf's Zen 1 event
  list; its unit mask bits are from the PPR.
- Against real DRAM traffic: P-0002's chase, results/chase_debian.md. `r0843:u` is a floor on DRAM demand fills:
  it counts demand fills only, never the prefetches, so never the whole DRAM traffic."""
STATES = ("counts", "counts zero", "not supported", "refused", "not run")
PART_B = {  # event: (low, high) per replayed message, from the prediction
    "r0843:u": (1.5, 2.6), "branch-misses:u": (1.6, 2.6), "branches:u": (60, 160),
    "dTLB-load-misses:u": (0.1, 4)}
CHECK_CODES = ("host-label", "private-text", "machine-check-failed", "name-unsaid",
               "state-unknown")


class ReportError(ValueError):
    """The inputs cannot make a report."""


def read_csv(path):
    """{event: (value or None, raw value, running %)} from a `perf stat -x,` file."""
    rows = {}
    if not path.is_file():
        return rows
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    for cells in csv.reader(lines):
        if len(cells) < 5:
            continue
        raw = cells[0].strip()
        value = int(raw) if raw.isdigit() else None
        event = cells[2].strip()
        # perf prints the generic cache events without the `:u` they were asked with; every
        # event here was asked for with it, and `perf stat -vv` shows exclude_kernel set.
        rows[event if ":" in event else event + ":u"] = (value, raw, cells[4].strip())
    return rows


def first_error(path):
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and line != "Error:" and not line.startswith(("measure.sh:", "#")):
            return flame_report.scrub(line)[:160]
    return ""


def part_a(root):
    exits = {}
    for line in (root / "exits.txt").read_text(encoding="utf-8").splitlines():
        spec, _, code = line.rpartition(" exit ")
        exits[spec] = int(code)
    states = {}
    for spec, code in exits.items():
        name = spec.replace(":", "_").replace("/", "_")
        if spec == "lbr":
            states[spec] = ("refused" if code else "counts", code, None,
                            first_error(root / "lbr.err"))
            continue
        rows = read_csv(root / f"{name}.csv")
        value, raw, _ = rows.get(spec, (None, "", ""))
        if code != 0:
            state = "refused"
        elif value is None:
            state = "not supported" if "not supported" in raw else "refused"
        else:
            state = "counts" if value > 0 else "counts zero"
        detail = {"refused": first_error(root / f"{name}.err"),
                  "not supported": "perf stat printed `<not supported>`"}.get(state, raw)
        states[spec] = (state, code, value, detail)
    return states


def part_b(root, messages):
    """{event: (count, running %, group)} from every group of Part B."""
    counts = {}
    for path in sorted(root.glob("group*.csv"), key=lambda p: int(re.sub(r"\D", "", p.stem))):
        for event, (value, raw, running) in read_csv(path).items():
            counts[event] = (value, raw, running, path.stem)
    return counts


def figure(value):
    """Four decimals, or three significant figures for a count too rare for four."""
    return f"{value:.4f}" if value == 0 or value >= 0.001 else f"{value:.2e}"


def render(args):
    record = json.loads(args.record.read_text(encoding="utf-8"))
    header = dict(record["header"], commit=args.commit, dataset=args.dataset)
    if header.get("host") != "debian":
        raise ReportError(f"host {header.get('host')!r}")
    messages = record["mix"]["messages"]
    replayed = 2 * messages
    states = part_a(args.part_a)
    counts = part_b(args.part_b, messages)
    real = [spec for spec, entry in states.items() if entry[0] in ("counts", "counts zero")]
    out = ["# The Zen 1 counter table on debian", "",
           "Generated by `scripts/zen1_events.py` from `perf stat` output that is not "
           "committed. Do not edit by hand.", "", "## Header", ""]
    for name in ("host", "cpu", "kernel", "compiler", "flags", "commit", "pinning", "governor"):
        out.append(f"- {name}: {flame_report.scrub(header.get(name, 'unknown'))}")
    out += ["- perf_event_paranoid: 2", f"- prediction: {args.prediction}",
            f"- part_a_input: tests/fixtures/oracle/session.itch, "
            f"{len(states)} `perf stat` and `perf record` runs",
            f"- part_b_input: {header.get('dataset', 'unknown')}, paged-flat, mapped, "
            f"{messages:,} messages replayed twice a process",
            f"- summary: {len(real)} of {len(states)} events count with `:u`; "
            f"{sum(1 for e in states.values() if e[0] == 'not supported')} not supported, "
            f"{sum(1 for e in states.values() if e[0] == 'refused')} refused", "",
            "## The table", "",
            "State is what `perf stat -e EVENT:u` did around a replay of the fixture: counts, "
            "counts zero, not supported (perf opened nothing and said so), or refused (perf "
            "exited non-zero). Per message is from Part B, the whole process around a replay "
            "of the day, divided by the two replays' messages: the warm-up count, page walk "
            "and setup are in it, so it is an upper bound on the replay's own figure. "
            "Running is the share of the run the event was on a counter.", "",
            "| Event | Programmed as | State with `:u` | Fixture count | Day, per replayed "
            "message | Running | What it counts on Zen 1 |", "|---|---|---|---|---|---|---|"]
    for event, encoding, meaning in TABLE:
        spec = f"{event}:u"
        state, _, value, detail = states.get(spec, ("not run", None, None, ""))
        fixture = f"{value:,}" if value is not None else "n/a"
        if state in ("refused", "not supported") and detail:
            fixture = f"n/a: {detail}"
        day = counts.get(spec)
        per = figure(day[0] / replayed) if day and day[0] is not None else "n/a"
        running = f"{day[2]}%" if day and day[0] is not None else "n/a"
        out.append(f"| `{event}` | {encoding} | {state} | {fixture} | {per} | {running} "
                   f"| {meaning} |")
    for spec, what in EXTRA:
        state, code, _, detail = states.get(spec, ("not run", None, None, ""))
        out.append(f"| `{spec}` | {what} | {state}, exit {code} | n/a: {detail or 'none'} "
                   "| n/a | n/a | "
                   + {"cycles:k": "Kernel cycles: refused to an unprivileged user at "
                                  "`perf_event_paranoid=2`, so no kernel frame in any graph.",
                      "cycles:upp": "Precise sampling goes to IBS, which does not take `:u`.",
                      "lbr": "perf refused it with its permission message, which by itself "
                             "does not say why; the kernel source gives Zen 1 no LBR at all "
                             "(`brs.c` and `lbr.c`: family 19h and LBR v2 only)."}[spec]
                   + " |")
    out += ["", "## Against the prediction", "",
            "| Event | Per replayed message | Predicted | Held |", "|---|---|---|---|"]
    for spec, (low, high) in PART_B.items():
        day = counts.get(spec)
        if not day or day[0] is None:
            out.append(f"| `{spec}` | n/a | {low} to {high} | not measured |")
            continue
        per = day[0] / replayed
        label = spec + (" (a floor on DRAM demand fills)" if spec.startswith("r0843") else "")
        out.append(f"| `{label}` | {per:.4f} | {low} to {high} | "
                   f"{'yes' if low <= per <= high else 'no'} |")
    l2 = counts.get("cache-misses:u")
    fills = counts.get("r0843:u")
    raw964 = counts.get("r0964:u")
    if l2 and fills and l2[0] and fills[0]:
        ratio = l2[0] / fills[0]
        out.append(f"| `cache-misses:u` (L2 misses) over `r0843:u` (a floor on DRAM demand "
                   f"fills) | {ratio:.3f} | 1.2 to 4 | {'yes' if 1.2 <= ratio <= 4 else 'no'} |")
    if l2 and raw964 and l2[0] and raw964[0]:
        gap = abs(raw964[0] - l2[0]) / l2[0]
        out.append(f"| `r0964:u` against `cache-misses:u`, both L2 misses, relative gap "
                   f"| {100 * gap:.3f}% | under 0.1% | {'yes' if gap < 0.001 else 'no'} |")
    multiplexed = [spec for spec, day in counts.items()
                   if day[0] is not None and day[2] not in ("100.00", "100.0", "100")]
    out.append(f"| every Part B event at 100% running | "
               f"{'all' if not multiplexed else ', '.join(multiplexed)} | all | "
               f"{'yes' if not multiplexed else 'no'} |")
    out += ["", "## Sources", "", SOURCES, "", "## What this does not show", "",
            "- Nothing at the kernel's side: every count is `:u`, user mode only, the only mode "
            "`perf_event_paranoid=2` allows.",
            "- No L3 or memory-controller figure. The L3 PMU counts per core complex and "
            "has no per-task mode, so a user cannot open it for a process; a CPU-wide count "
            "needs a lower `perf_event_paranoid`, and no system setting was changed.",
            "- Part B's figures are whole-process counts, not per message type. Those are in "
            "the `counters_by_type_*` files.", "",
            "## machine_check output", "", "```"]
    for path in args.machine_check:
        out.append(flame_report.scrub(path.read_text(encoding="utf-8")).rstrip())
    out += ["```", "", "## Command", "", "```", flame_report.scrub(args.command), "```", ""]
    text = "\n".join(out)
    found = check_published(text)
    if found:
        raise ReportError(f"the file would not pass its own check: {found}")
    args.out.write_text(text, encoding="utf-8")
    print(f"zen1_events: wrote {args.out.name}, {len(real)} of {len(states)} counting")
    return 0


def check_published(text):
    fields = check_results.parse_header(text)
    found = []
    if fields.get("host") != "debian":
        found.append(("host-label", repr(fields.get("host"))))
    if flame_report.private_found(text):
        found.append(("private-text", flame_report.private_found(text)))
    output = check_results.section(text, "machine_check output") or ""
    if output.count("machine_check: passed") != 2:
        found.append(("machine-check-failed", "wanted a passing check before and after"))
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern, said in counts_report.NAME_RULES:
            if pattern.search(line) and said not in line:
                found.append(("name-unsaid", f"line {number} names {pattern.pattern} "
                                             f"without '{said}'"))
    table = check_results.section(text, "The table") or ""
    for line in table.splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if line.startswith("| `") and len(cells) == 7:
            if cells[2].split(",")[0] not in STATES:
                found.append(("state-unknown", f"{cells[0]}: {cells[2]}"))
    return found


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--check"]:
        if len(argv) != 2:
            print("zen1_events: --check needs one file", file=sys.stderr)
            return 2
        found = check_published(Path(argv[1]).read_text(encoding="utf-8"))
        for code, detail in found:
            print(f"zen1_events: FAIL {code}: {detail}")
        if not found:
            print(f"zen1_events: ok {Path(argv[1]).name}")
        return 1 if found else 0
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--part-a", type=Path, required=True)
    parser.add_argument("--part-b", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--machine-check", type=Path, nargs=2, required=True)
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--commit", required=True, help="the commit measured, as the run knew it")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return render(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"zen1_events: unusable input: {error!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
