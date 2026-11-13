#!/usr/bin/env python3
"""The Mac's sampler rule inside a driver, for bench_driver.py and backtest_driver.py.

spsc_driver.py's CPU sampler (wasm_bench.py's, reading the process table through `ps`)
runs beside the whole run. Each process the driver judges runs once the sampler has seen
--quiet-before seconds with no other process above 20% CPU, and is published only if the
sampler then covered its whole span with no gap over 5 s and saw no such process in it.
Otherwise it is withdrawn, its raw files set aside, with its own row in the run log, and run
again; never for its numbers. A process withdrawn --most-in-a-row times running, or more
than --most-withdrawn in one run, ends the run, refused, so that a busy Mac is given back and
the run is tried again later. The file says so in its interference field and its Sampler
section, which check_results.py holds (check_driver_sampler).

A library, not a command.
"""

from __future__ import annotations

import argparse
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_driver  # noqa: E402
import check_results  # noqa: E402
import spsc_driver  # noqa: E402
import wasm_bench  # noqa: E402

QUIET_WAIT_S = 600
MOST_IN_A_ROW = 3
MOST_WITHDRAWN = 20


def add_arguments(parser, quiet_before=0.0):
    """The rule's options, for a driver that does not have them already."""
    parser.add_argument("--quiet-before", type=float, default=quiet_before, metavar="S",
                        help="macOS: wait for S seconds with no other busy process before each "
                             "process")
    parser.add_argument("--quiet-wait", type=float, default=QUIET_WAIT_S, metavar="S")
    parser.add_argument("--most-in-a-row", type=int, default=MOST_IN_A_ROW, metavar="N")
    parser.add_argument("--most-withdrawn", type=int, default=MOST_WITHDRAWN, metavar="N")


def add_sampler_arguments(parser):
    """For tests: the `ps` the sampler reads and how often."""
    parser.add_argument("--ps", default="ps", help=argparse.SUPPRESS)
    parser.add_argument("--sample-every", type=float, default=wasm_bench.SAMPLE_EVERY_S,
                        help=argparse.SUPPRESS)


def start(args):
    """The sampler beside a run on macOS, writing raw-dir/sampler.txt, or None elsewhere."""
    if platform.system() != "Darwin":
        return None
    sampler = spsc_driver.Sampler(args.raw_dir / "sampler.txt", args.ps, args.sample_every)
    sampler.start()
    return sampler


def judged(args, sampler, sampled, what, run, set_aside, commit):
    """run()'s result once the rule publishes it. what names the process in the file and the
    run log; set_aside(count) keeps a withdrawn one's raw files under a name of their own."""
    in_a_row = 0
    while True:
        spsc_driver.wait_for_quiet(sampler, args.quiet_before, args.quiet_wait)
        begin = bench_driver.utc_now()
        result = run()
        time.sleep(1)  # So that the span covers all of the process.
        end = bench_driver.utc_now()
        sampler.wait_past(end)
        lines = list(sampler.lines)
        why = spsc_driver.span_unquiet(lines, begin, end)
        if why is None:
            sampled["published"].append((what, begin, end,
                                         spsc_driver.span_summary(lines, begin, end)))
            return result
        sampled["withdrawn"].append((what, begin, end, why))
        count = len(sampled["withdrawn"])
        set_aside(count)
        bench_driver.append_log(
            args.run_log, f"{args.prediction}-{begin.replace(':', '').replace('-', '')}",
            args.prediction, commit,
            f"withdrawn: {what} on the Mac, exit 0; {why}, inside its span, so the sampler rule "
            f"does not publish it; nothing from it is published, and it is rerun under "
            f"{args.prediction}; the row was written by the driver")
        print(f"{Path(sys.argv[0]).name}: {what} withdrawn: {why}", file=sys.stderr)
        if count > args.most_withdrawn:
            raise bench_driver.Refused(what, f"{count} processes withdrawn under the sampler "
                                             f"rule, over the {args.most_withdrawn} allowed")
        in_a_row += 1
        if in_a_row >= args.most_in_a_row:
            raise bench_driver.Refused(what, f"withdrawn {in_a_row} times in a row under the "
                                             "sampler rule, so the Mac is given back and the "
                                             "run is tried again later")


def interference_line(sampled, mine=lambda what: True):
    withdrawn = [entry for entry in sampled["withdrawn"] if mine(entry[0])]
    return (f"- interference: {check_results.driver_interference(sampled['every'])}; "
            f"{len(withdrawn)} withdrawn under the rule")


def sampler_section(sampled, mine=lambda what: True):
    """The Sampler section: the rule, then each process the file publishes and each one
    withdrawn, of those mine() picks."""
    rule = check_results.sampler_rule(check_results.DRIVER_SAMPLER_RULE, sampled)
    lines = ["## Sampler", "", rule, "",
             "| Process | Started | Finished | Outcome |", "|---|---|---|---|"]
    for what, begin, end, said in sampled["published"]:
        if mine(what):
            lines.append(f"| {what} | {begin} | {end} | {said} |")
    for index, (what, begin, end, why) in enumerate(sampled["withdrawn"], start=1):
        if mine(what):
            lines.append(f"| withdrawn {index}: {what} | {begin} | {end} | withdrawn: {why} |")
    return lines
