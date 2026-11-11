#!/usr/bin/env python3
"""Runs the backtester's benchmark in fresh processes and writes one results file.

    backtest_driver.py --bench BENCH_BACKTEST --probe BENCH_PROBE --input FILE \\
        --workload quoting|shadow --date YYYYMMDD --prediction P-NNNN --raw-dir DIR \\
        --out results/backtest_NAME.md [--repetitions N] [--core N] [--sibling N] \\
        [--wrapped-by TEXT] [--allow-dirty] [--bench-arg ARG]...

On a reported run this is measure.sh's direct child, as scripts/bench_driver.py is, and it
shares that driver's order and its signal handling:

    measure.sh /usr/bin/time -l python3 -B scripts/backtest_driver.py ... 2> STDERR
    python3 -B scripts/stamp_load.py --results FILE --stderr STDERR

Hash the input, which also faults it in; confirm the prediction is on record; run
machine_check.sh, refusing on a failure; run the repetitions, one bench_backtest process
each; refuse if any refused, if the repetitions disagree on a single count or the event
hash, or if the kernel swapped a page out; write the results file with its load lines
pending. Figures are the median of the repetitions with min, max and CV beside it.

The quoting workload is T-BT-1 and the shadow workload T-BT-3. The file says what one event
is, what the rate's denominator is beside the source's, and gives the target's verdict in
words from check_results.py's fixed set. T-BT-1's bar was set on x86, so a macOS figure
neither meets nor misses it; T-BT-3's was set on a Mac.

Every run gets a row in results/run_log.md, whatever became of it. Raw output stays in
--raw-dir, which is not committed.

Exit codes: 0 results written, 1 refused or failed, 2 bad usage, 128 + N stopped by
signal N.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_driver  # noqa: E402
import check_results  # noqa: E402

REPO_ROOT = bench_driver.REPO_ROOT
Refused = bench_driver.Refused
Stopped = bench_driver.Stopped
MEGA = 1e6
# Counts that every repetition must give exactly: the same day through the same engine.
AGREED = ("messages", "deliveries", "arrivals", "decisions", "reports", "wakes", "fills",
          "filled_shares", "maker_fills", "taker_fills", "crossing_fills", "queue_anomalies",
          "late_adds", "late_adds_ahead", "hidden_prints_at_sim_price", "strategy_events",
          "sends", "replaces", "cancels", "skipped", "event_hash")
COUNT_ROWS = (("ITCH messages through the engine", "messages"),
              ("Deliveries to the strategy", "deliveries"),
              ("Simulated orders, cancels and replaces arriving", "arrivals"),
              ("Reports to the strategy", "reports"),
              ("Wakes", "wakes"),
              ("Orders sent or placed", "sends"),
              ("Replaces sent", "replaces"),
              ("Cancels sent", "cancels"),
              ("Placements skipped, side empty", "skipped"),
              ("Fills", "fills"),
              ("Filled shares", "filled_shares"),
              ("Maker fills", "maker_fills"),
              ("Taker fills", "taker_fills"),
              ("Maker fills against a crossing add", "crossing_fills"),
              ("Queue anomalies", "queue_anomalies"),
              ("Late adds on the traded symbols", "late_adds"),
              ("Late adds ahead of a simulated order", "late_adds_ahead"),
              ("Hidden prints at a simulated order's price, a bound on missed fills",
               "hidden_prints_at_sim_price"),
              ("Event hash", "event_hash"))

WORKLOADS = {
    "quoting": {
        "target": "T-BT-1",
        "bar": 2.0,
        "x86": True,
        "title": "T-BT-1: the backtester with a quoting strategy",
        "host": ("T-BT-1's bar was set on an Intel Core i9-13900H, so a macOS figure neither "
                 "meets nor misses it."),
        "what": (
            "The whole day through the backtester: every ITCH message of the day applied to "
            "the exchange-side book, the queue model on for every simulated order, the base "
            "latencies, and the quoting strategy of `bench/backtest_workloads.hpp` on {symbols}. "
            "It joins the view book's best bid and ask with 100 shares, follows each with a "
            "replace, and stops bidding or offering at 1,000 shares of inventory. It is not the "
            "Avellaneda-Stoikov maker."),
        "event": (
            "One event here is one ITCH message of the day through the engine: the frame walk, "
            "the exchange-side book, the queue model, and for a traded symbol the delivery to the "
            "strategy L_md later, the strategy's decision, and every arrival, fill and report it "
            "causes, all inside the one timed span. The denominator is the day's {messages} "
            "messages, every symbol's. The strategy sees only its {symbols} and the System "
            "Events, {deliveries} deliveries; every other message is replayed through the "
            "exchange-side book and nothing else. The engine-events rate counts messages, "
            "deliveries, arrivals, reports and wakes together over the same span.\n\n"
            "LobSim's op, T-BT-1's source, is one event of a synthetic L3 feed through replay "
            "plus a market-making strategy, at 507.6 ns an op; its README does not say whether "
            "the queue model was on in that row, and its strategy may see every event. So the "
            "two rates count a different event on a different feed: ours is a real day on "
            "which most messages never reach a strategy."),
    },
    "shadow": {
        "target": "T-BT-3",
        "bar": 4.61,
        "x86": False,
        "title": "T-BT-3: the backtester with queuetruth's shadow orders",
        "host": ("T-BT-3's bar was set on an Apple M1 Pro under macOS, in Low Power Mode, which "
                 "its author calls a lower bound for that machine, so a macOS figure is held "
                 "to it, on a faster Apple part."),
        "what": (
            "queuetruth's workload: {orders} shadow orders of 100 shares in real FIFO queues on "
            "{symbols}, no strategy, every latency zero. The orders are placed at evenly spaced "
            "times over the regular session, cycling through the symbols and the two sides, "
            "each at the view book's best price on its side then, and each is cancelled 60 s "
            "after it was placed (`ShadowPlacer` in `bench/backtest_workloads.hpp`)."),
        "event": (
            "One event here is one ITCH message of the extract through the engine. Before any "
            "timing, each process walks the whole day once to count and once to copy the "
            "messages of {symbols} and every System Event into anonymous memory: {extract} of "
            "the day's {day} messages. Only the extract is replayed and timed; the full-day "
            "parse and filter is excluded, and its time is in the repetitions table. The "
            "denominator is the {extract} extract messages.\n\n"
            "queuetruth's 5.19 M records/s is over 6,103,262 records decoded from 5,425,849 ITCH "
            "messages of the same four symbols, read from Parquet with a decode thread, so its "
            "timed span also excludes the full day. T-BT-3's 4.61 M messages/s is that rate "
            "over messages: 5.19 M × 5,425,849 / 6,103,262. The extract here holds {extract} "
            "messages against queuetruth's 5,425,849; the rate is compared on messages, and "
            "the two counts differ by {difference}."),
    },
}


def run_process(args, index, commit, digest):
    """One bench_backtest process: a warm-up pass and a timed pass."""
    stem = f"{args.workload}-repetition-{index}"
    record_path = args.raw_dir / f"{stem}.json"
    log_path = args.raw_dir / f"{stem}.log"
    record_path.unlink(missing_ok=True)
    command = [str(args.bench), "--input", str(args.input), "--out", str(record_path),
               "--workload", args.workload, "--date", args.date, "--commit", commit,
               "--dataset", args.input.name, "--dataset-sha256", digest]
    if args.core is not None:
        command += ["--core", str(args.core)]
    command += args.bench_arg
    where = f"repetition {index}"
    bench_driver.set_stage(where)
    with open(log_path, "w", encoding="utf-8") as log:
        bench_driver.current_child = subprocess.Popen(command, stdout=log,
                                                      stderr=subprocess.STDOUT)
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


def validate_run(record, workload):
    """What one record must show for its numbers to mean anything."""
    if record.get("workload") != workload:
        raise Refused("record", f"the record is of workload {record.get('workload')!r}")
    for name in AGREED:
        if record["warmup"][name] != record["timed"][name]:
            raise Refused("record", f"the warm-up and the timed pass differ on {name}")
    if record["timed"]["ns"] <= 0:
        raise Refused("record", "the timed pass took no time")
    if workload == "shadow" and (record.get("extract") is None or
                                 record["extract"]["messages"] != record["timed"]["messages"]):
        raise Refused("record", "the shadow workload did not run on its extract alone")


def validate_together(records):
    first = records[0]["timed"]
    for index, record in enumerate(records[1:], start=2):
        for name in AGREED:
            if record["timed"][name] != first[name]:
                raise Refused("record", f"repetition {index} differs from repetition 1 on {name}")


def seconds(record):
    return record["timed"]["ns"] / 1e9


def message_rate(record):
    return record["timed"]["messages"] / seconds(record)


def engine_events(record):
    timed = record["timed"]
    return sum(timed[name] for name in ("messages", "deliveries", "arrivals", "reports", "wakes"))


def engine_rate(record):
    return engine_events(record) / seconds(record)


def ns_per_message(record):
    return record["timed"]["ns"] / record["timed"]["messages"]


SUMMARY = (("Replay rate, messages/s", message_rate, ",.0f"),
           ("Engine events/s", engine_rate, ",.0f"),
           ("Mean time per message, ns", ns_per_message, ".2f"),
           ("Timed pass, s", seconds, ".3f"))


def summary_rows(records):
    rows = []
    for label, measure, shown in SUMMARY:
        median, low, high, cv = bench_driver.spread([measure(record) for record in records])
        rows.append(f"| {label} | {median:{shown}} | {low:{shown}} | {high:{shown}} "
                    f"| {cv * 100:.2f}% |")
    return rows


def verdict(workload, host, median_rate):
    """T-BT-1's bar was set on x86; T-BT-3's on a Mac. Rates in millions of messages a second."""
    spec = WORKLOADS[workload]
    bar = spec["bar"]
    rate = median_rate / MEGA
    gap = rate - bar
    if spec["x86"] and host == check_results.MAC_HOST:
        return (f"host differs: a macOS figure against a bar set on an x86 laptop, neither a pass "
                f"nor a fail; against the bar: {gap:+.2f} M messages/s")
    if rate >= bar:
        return "met"
    return f"missed: {-gap:.2f} M messages/s under the bar"


def target_rows(records):
    workload = records[0]["workload"]
    spec = WORKLOADS[workload]
    host = records[0]["header"]["host"]
    median = statistics.median(message_rate(record) for record in records)
    return [f"| {spec['target']} | ≥ {spec['bar']:.2f} M messages/s | "
            f"{median / MEGA:.2f} M messages/s | {verdict(workload, host, median)} |"]


def count_rows(record):
    rows = []
    for label, name in COUNT_ROWS:
        value = record["timed"][name]
        rows.append(f"| {label} | {value if name == 'event_hash' else f'{value:,}'} |")
    return rows


def repetition_rows(records):
    rows = []
    for index, record in enumerate(records, start=1):
        extract = record.get("extract")
        cut = f"{extract['ns'] / 1e9:.3f}" if extract else "none"
        rows.append(f"| {index} | {message_rate(record):,.0f} | {engine_rate(record):,.0f} "
                    f"| {seconds(record):.3f} | {cut} | {record['migrations']} "
                    f"| {record['peak_rss_bytes'] / 1024 ** 3:.2f} |")
    return rows


def describe(records):
    """The workload's paragraph and the event's, with this run's counts."""
    first = records[0]
    spec = WORKLOADS[first["workload"]]
    timed = first["timed"]
    extract = first.get("extract") or {"messages": 0}
    values = {"symbols": first["symbols"].replace(" ", ", "), "orders": f"{first['shadow_orders']:,}",
              "messages": f"{timed['messages']:,}", "deliveries": f"{timed['deliveries']:,}",
              "extract": f"{extract['messages']:,}", "day": f"{first['input_messages']:,}",
              "difference": f"{abs(extract['messages'] - 5_425_849):,}"}
    return spec["what"].format(**values), spec["event"].format(**values)


def header_lines(records, args, run_id, started, finished):
    first = records[0]
    header = dict(first["header"])
    latency = first["latency_ns"]
    lines = [f"- {name}: {header[name]}" for name in bench_driver.HEADER_ORDER if name in header]
    lines += [f"- workload: {first['workload']}",
              f"- symbols: {first['symbols']}",
              f"- input: {first['input']}",
              f"- latency: L_md {latency['market_data']:,} ns, L_oe {latency['order_entry']:,} "
              f"ns, L_resp {latency['response']:,} ns, L_compute {latency['compute']:,} ns",
              f"- measure_load_start: {check_results.PENDING}",
              f"- measure_load_end: {check_results.PENDING}",
              *bench_driver.peak_lines(records),
              f"- prediction: {args.prediction}",
              f"- run_id: {run_id}",
              f"- repetitions: {len(records)}",
              f"- warmup_messages: {first['warmup']['messages']:,}",
              f"- timed_messages: {first['timed']['messages']:,}",
              f"- run_started: {started}",
              f"- run_finished: {finished}"]
    return lines


def render(records, args, run_id, started, finished, machine_output, command):
    first = records[0]
    spec = WORKLOADS[first["workload"]]
    host = first["header"]["host"]
    day = first["header"]["dataset"].split(".")[0]
    what, event = describe(records)
    lines = [f"# {spec['title']}, on {host}, {day}", "",
             "Generated by `scripts/backtest_driver.py` from raw records that are not committed. "
             "Do not edit by hand.", "",
             f"Host: **{host}**. Numbers from the two hosts are never mixed.", ""]
    if host == check_results.MAC_HOST:
        lines += [f"**{host} is not a pinned host.** macOS offers no way to pin a thread, so each "
                  "process ran wherever the scheduler put it, beside whatever else the machine "
                  "was doing under `measure.sh`'s lock. Every figure here is a macOS figure. "
                  + spec["host"], ""]
    lines += [what, "", "## Header", ""]
    lines += header_lines(records, args, run_id, started, finished)
    lines += ["", "## Summary", "",
              "Each repetition is a fresh process: one warm-up pass, excluded from every figure "
              "here, then one timed pass on a fresh engine and strategy, which must agree with "
              "the warm-up on every count and the event hash. The span is the engine's `run()` "
              f"over the whole input, and nothing before or after it. {len(records)} "
              "repetitions.", "",
              "| Figure | Median | Min | Max | CV |", "|---|---|---|---|---|"]
    lines += summary_rows(records)
    lines += ["", "## What an event is", "", event, "",
              "## Targets", "",
              "| Target | Bar | Measured | Verdict |", "|---|---|---|---|"]
    lines += target_rows(records)
    lines += ["", "## Counts", "",
              "From the timed pass. Every repetition gave the same, and the driver refuses a run "
              "where one differs.", "", "| Count | Value |", "|---|---|"]
    lines += count_rows(first)
    lines += ["", "## Repetitions", "",
              "| Repetition | Messages/s | Engine events/s | Timed pass s | Extract s, untimed "
              "| Migrations | Peak RSS GiB |", "|---|---|---|---|---|---|---|"]
    lines += repetition_rows(records)
    if bench_driver.swap_watch is not None:
        lines += ["", bench_driver.swap_watch.line()]
    lines += ["", "## machine_check output", "", "```", machine_output, "```", "",
              "## Commands", "", "```", command, "```", ""]
    return "\n".join(lines)


def relative_command(args):
    def shown(path):
        # Absolute without following links, so that a data/ link in a worktree shows as data/.
        path = Path(os.path.abspath(path))
        try:
            return str(path.relative_to(REPO_ROOT))
        except ValueError:
            return f"<raw>/{path.name}"
    words = ["python3", "-B", "scripts/backtest_driver.py", "--bench", shown(args.bench),
             "--probe", shown(args.probe), "--input", shown(args.input),
             "--workload", args.workload, "--date", args.date,
             "--prediction", args.prediction, "--repetitions", str(args.repetitions),
             "--raw-dir", "<raw>", "--out", shown(args.out)]
    for flag, value in (("--core", args.core), ("--sibling", args.sibling)):
        if value is not None:
            words += [flag, str(value)]
    text = " ".join(shlex.quote(word) if "<" not in word else word for word in words)
    return f"{args.wrapped_by} {text}" if args.wrapped_by else text


def run(args, run_id, started, commit):
    bench_driver.swap_watch = bench_driver.SwapWatch(args.vmstat)
    bench_driver.set_stage("hashing the input")
    digest = bench_driver.hash_and_fault_in(args.input)
    bench_driver.set_stage("checking the prediction")
    code = subprocess.run([sys.executable, "-B", str(REPO_ROOT / "scripts" / "check_predictions.py"),
                           "--root", str(args.predictions_root), "--require", args.prediction],
                          capture_output=True, text=True, check=False)
    if code.returncode != 0:
        raise Refused("prediction", code.stdout.strip())
    bench_driver.set_stage("machine_check")
    machine_output = bench_driver.machine_check(args)
    records = []
    for index in range(1, args.repetitions + 1):
        record = run_process(args, index, commit, digest)
        validate_run(record, args.workload)
        records.append(record)
    validate_together(records)
    bench_driver.set_stage("rendering the results file")
    text = render(records, args, run_id, started, bench_driver.utc_now(), machine_output,
                  relative_command(args))
    violations = [found for found in check_results.check_text(text)
                  if found[0] != "load-not-stamped"]
    if violations:
        raise Refused("results", f"{args.out.name}: " + "; ".join(
            f"{code}: {detail}" for code, detail in violations))
    bench_driver.hold_signals()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    return args.out


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bench", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--workload", choices=tuple(WORKLOADS), required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--core", type=int)
    parser.add_argument("--sibling", type=int)
    parser.add_argument("--wrapped-by", help="what the run was started under, for the record")
    parser.add_argument("--allow-dirty", action="store_true", help="for tests, not for a report")
    parser.add_argument("--bench-arg", action="append", default=[], metavar="ARG")
    parser.add_argument("--predictions-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--run-log", type=Path, default=REPO_ROOT / "results" / "run_log.md",
                        help=argparse.SUPPRESS)
    parser.add_argument("--vmstat", type=Path, default=Path("/proc/vmstat"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.repetitions < 3:
        parser.error("median, min, max and CV need at least 3 repetitions")
    if len(args.date) != 8 or not args.date.isdigit():
        parser.error("--date is YYYYMMDD")
    return args


def main(argv=None):
    args = parse_args(argv)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, bench_driver.forward_signal)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    started = bench_driver.utc_now()
    run_id = f"{args.prediction}-{started.replace(':', '').replace('-', '')}"
    commit = "unknown"
    try:
        commit = bench_driver.git_commit(REPO_ROOT, args.allow_dirty)
        written = run(args, run_id, started, commit)
    except Stopped as stop:
        bench_driver.stop_child()
        name = signal.Signals(stop.signum).name
        print(f"backtest_driver: stopped by {name} at {bench_driver.stage}", file=sys.stderr)
        bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                                f"stopped by {name} at {bench_driver.stage}; nothing from it is "
                                "published; the row was written by the driver as it stopped")
        return 128 + stop.signum
    except Refused as refusal:
        bench_driver.hold_signals()
        print(f"backtest_driver: refused at {refusal.stage}: {refusal}", file=sys.stderr)
        bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                                f"refused at {refusal.stage}: {refusal}")
        return 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        bench_driver.hold_signals()
        print(f"backtest_driver: failed: {error!r}", file=sys.stderr)
        bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                                f"failed: {error!r}")
        return 1
    bench_driver.hold_signals()
    bench_driver.append_log(args.run_log, run_id, args.prediction, commit, "published")
    print(f"backtest_driver: wrote {written}, load lines pending. Now run scripts/stamp_load.py.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
