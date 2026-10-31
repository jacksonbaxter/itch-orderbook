#!/usr/bin/env python3
"""Runs a benchmark in fresh processes and writes one results file.

    bench_driver.py --bench BENCH_REPLAY --probe BENCH_PROBE --input FILE \\
        --prediction P-NNNN --raw-dir DIR --out results/bench_NAME.md \\
        [--repetitions N] [--core N] [--sibling N] [--title TEXT] [--note TEXT] \\
        [--orders N] [--levels N] [--wrapped-by TEXT] [--allow-dirty] [--bench-arg ARG]...

On a reported run this is measure.sh's direct child, and it runs one repetition at a
time, so that signalling it stops everything:

    measure.sh [taskset -c 4] python3 -B scripts/bench_driver.py ... 2> STDERR
    python3 -B scripts/stamp_load.py --results FILE --stderr STDERR

The order is: hash the input, which also faults it in; confirm the prediction is on
record; run machine_check.sh, refusing on a failure; run the repetitions; refuse if any
one of them refused; write the results file with its load lines pending. Figures are the
median of the repetitions with min, max and CV beside it, never the best of them. A
percentile is shown only when every repetition resolved it.

Every run gets a row in results/run_log.md, whatever became of it. Raw output stays in
--raw-dir, which is not committed.

Exit codes: 0 results written, 1 refused or failed, 2 bad usage.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import mmap
import os
import platform
import shlex
import signal
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
HEADER_ORDER = ("host", "cpu", "kernel", "compiler", "flags", "commit", "dataset",
                "dataset_sha256", "timer", "quantum", "pair", "floor", "pinning", "migrations",
                "core", "governor", "frequency", "turbo", "smt", "isolation", "counters", "rdpmc",
                "page_size", "residency")
METRICS = (("Throughput, messages/s", "messages_per_s", ",.0f"),
           ("Latency-pass rate, messages/s", "latency_messages_per_s", ",.0f"),
           ("Mean span of apply(), ns", "mean_ns", ".2f"),
           ("Instrumentation ratio, latency pass over throughput pass", "ratio", ".4f"))


class Refused(Exception):
    """The run must not publish. The message is the reason on record."""

    def __init__(self, stage, reason):
        super().__init__(reason)
        self.stage = stage


current_child = None


def forward_signal(signum, _frame):
    """measure.sh signals only its direct child, so this stops the repetition too."""
    if current_child is not None and current_child.poll() is None:
        current_child.send_signal(signum)
    sys.exit(128 + signum)


def utc_now():
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_commit(root, allow_dirty):
    """The commit under test: from git, or the COMMIT file a debian sync leaves."""
    if (root / ".git").exists():
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "bench",
                                 "include", "src", "tools", "scripts", "cmake", "CMakeLists.txt"],
                                capture_output=True, text=True, check=True).stdout
        if status and not allow_dirty:
            raise Refused("commit", "the tree has uncommitted changes")
        return f"{head} ({'with uncommitted changes' if status else 'clean'})"
    stamp = root / "COMMIT"
    if stamp.is_file():
        return f"{stamp.read_text(encoding='utf-8').strip()[:12]} (exported by debian_sync.sh)"
    return "unavailable: neither git nor a COMMIT file"


def hash_and_fault_in(path):
    """SHA-256 through a mapping, so that the pages stay in the cache for the run."""
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        if os.fstat(file.fileno()).st_size == 0:
            raise Refused("input", "the input is empty")
        with mmap.mmap(file.fileno(), 0, prot=mmap.PROT_READ) as mapped:
            digest.update(mapped)
    return digest.hexdigest()


def machine_check(args):
    command = ["bash", str(REPO_ROOT / "scripts" / "machine_check.sh"), "--input", str(args.input),
               "--probe", str(args.probe)]
    if args.core is not None:
        command += ["--core", str(args.core)]
    if args.sibling is not None:
        command += ["--sibling", str(args.sibling)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise Refused("machine_check", result.stdout.strip().splitlines()[-1]
                      if result.stdout.strip() else f"exited {result.returncode}")
    return result.stdout.strip()


def run_repetition(args, index, commit, digest):
    global current_child
    record_path = args.raw_dir / f"repetition-{index}.json"
    log_path = args.raw_dir / f"repetition-{index}.log"
    record_path.unlink(missing_ok=True)
    command = [str(args.bench), "--input", str(args.input), "--out", str(record_path),
               "--commit", commit, "--dataset", args.input.name, "--dataset-sha256", digest]
    if args.core is not None:
        command += ["--core", str(args.core)]
    if args.orders is not None:
        command += ["--orders", str(args.orders)]
    if args.levels is not None:
        command += ["--levels", str(args.levels)]
    command += args.bench_arg
    with open(log_path, "w", encoding="utf-8") as log:
        current_child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        code = current_child.wait()
        current_child = None
    if not record_path.is_file():
        raise Refused(f"repetition {index}", f"exited {code} with no record")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if code != 0 or record["refusals"]:
        raise Refused(f"repetition {index}",
                      f"exited {code}: {'; '.join(record['refusals']) or 'no reason given'}")
    return record


def validate_run(record):
    """What a record must show for its numbers to mean anything."""
    warmup = record["warmup"]["messages"]
    for name in ("throughput", "latency_pass"):
        if record[name]["messages"] != warmup:
            raise Refused("record", f"{name} timed {record[name]['messages']} messages, "
                                    f"one pass is {warmup}: the warm-up leaked in")
    if record["latency"]["samples"] != warmup:
        raise Refused("record", f"{record['latency']['samples']} latency samples for {warmup} "
                                "messages")
    floor = record["timer"]["floor_ns"]
    for entry in record["latency"]["percentiles"]:
        if entry["value_ns"] is not None and entry["value_ns"] < floor:
            raise Refused("record", f"p{entry['percentile']} is below the {floor} ns floor")
    for status in record["counters"]["status"]:
        if status["state"] == "measured" and status["ratio"] < 1.0:
            raise Refused("record", f"{status['name']} is called measured at ratio "
                                    f"{status['ratio']}")


def spread(values):
    """Median, min, max and coefficient of variation."""
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    return (statistics.median(values), min(values), max(values),
            deviation / mean if mean else 0.0)


def metric_values(records, key):
    if key == "messages_per_s":
        return [record["throughput"]["messages_per_s"] for record in records]
    if key == "latency_messages_per_s":
        return [record["latency_pass"]["messages_per_s"] for record in records]
    if key == "mean_ns":
        return [record["latency"]["mean_ns"] for record in records]
    return [record["instrumentation_ratio"] for record in records]


def summary_rows(records):
    rows = []
    for label, key, shape in METRICS:
        median, low, high, variation = spread(metric_values(records, key))
        rows.append(f"| {label} | {median:{shape}} | {low:{shape}} | {high:{shape}} "
                    f"| {100 * variation:.2f}% |")
    return rows


def latency_rows(records):
    rows = []
    positions = [entry["percentile"] for entry in records[0]["latency"]["percentiles"]]
    for position, percentile in enumerate(positions):
        values = [record["latency"]["percentiles"][position]["value_ns"] for record in records]
        rows.append(percentile_row(f"p{percentile:g}", values))
    rows.append(percentile_row("max", [record["latency"]["max_ns"] for record in records]))
    return rows


def percentile_row(name, values):
    resolved = [value for value in values if value is not None]
    if len(resolved) != len(values):
        return (f"| {name} | unresolved | | | | resolved in {len(resolved)} of {len(values)} "
                "repetitions |")
    median, low, high, variation = spread(resolved)
    return f"| {name} | {median:.1f} | {low:.1f} | {high:.1f} | {100 * variation:.2f}% | resolved |"


def counter_rows(records):
    rows = []
    for position, status in enumerate(records[0]["counters"]["status"]):
        entries = [record["counters"]["status"][position] for record in records]
        worst = min(entries, key=lambda entry: entry["ratio"])
        counts = [entry["value"] for entry in entries]
        state = next((entry["state"] for entry in entries if entry["state"] != "measured"),
                     "measured")
        detail = "; ".join(sorted({entry["reason"] for entry in entries if entry["reason"]}))
        rows.append(f"| {status['name']} | {state} | {worst['ratio']:.4f} "
                    f"| {statistics.median(counts):,.0f} | {detail} |")
    return rows


def per_message_rows(records):
    rows = []
    published = [record for record in records if record["counters"]["per_message_published"]]
    if len(published) != len(records) or not records[0]["counters"]["per_message"]:
        return rows
    for position, entry in enumerate(records[0]["counters"]["per_message"]):
        means = [record["counters"]["per_message"][position]["mean"] for record in records]
        median, low, high, variation = spread(means)
        rows.append(f"| {entry['name']} | {median:.3f} | {low:.3f} | {high:.3f} "
                    f"| {100 * variation:.2f}% |")
    return rows


def agreement_rows(records):
    """rdpmc against read() over the whole pass, event by event, with the widest gap seen."""
    rows = []
    for position, entry in enumerate(records[0]["counters"].get("agreement_events", [])):
        entries = [record["counters"]["agreement_events"][position] for record in records]
        gaps = [abs(item["read"] - item["rdpmc"]) for item in entries]
        widest = max(gaps)
        base = entries[gaps.index(widest)]["read"]
        share = 100 * widest / base if base else 0.0
        summed = [next((item["sum"] for item in record["counters"]["per_message"]
                        if item["name"] == entry["name"]), None) for record in records]
        apply_sum = "" if None in summed else f"{statistics.median(summed):,.0f}"
        rows.append(f"| {entry['name']} | {statistics.median(e['read'] for e in entries):,.0f} "
                    f"| {statistics.median(e['rdpmc'] for e in entries):,.0f} "
                    f"| {widest:,} | {share:.4f}% | {apply_sum} |")
    return rows


def peak_lines(records):
    """Linux has no /usr/bin/time here, and a wrapper would break measure.sh's signal path."""
    if not records[0]["header"]["kernel"].startswith("Linux"):
        return ["- peak_rss: unavailable: /usr/bin/time did not wrap the run",
                "- peak_footprint: unavailable: /usr/bin/time did not wrap the run"]
    peak = max(record["peak_rss_bytes"] for record in records) / 1024 ** 3
    return [f"- peak_rss: {peak:.2f} GiB, getrusage of the benchmark, largest repetition",
            "- peak_footprint: unavailable: a macOS figure, with no Linux equivalent"]


def repetition_rows(records):
    rows = []
    for index, record in enumerate(records, start=1):
        peak = record["peak_rss_bytes"] / 1024 ** 3
        rows.append(f"| {index} | {record['throughput']['messages_per_s']:,.0f} "
                    f"| {record['instrumentation_ratio']:.4f} "
                    f"| {record['timer']['quantum_ns']:.3f} | {record['timer']['pair_ns']:.3f} "
                    f"| {record['migrations']} | {peak:.2f} |")
    return rows


def header_lines(records, args, run_id, started, finished):
    header = dict(records[0]["header"])
    lines = [f"- {name}: {header[name]}" for name in HEADER_ORDER]
    warmup = records[0]["warmup"]["messages"]
    lines += [f"- measure_load_start: {check_results.PENDING}",
              f"- measure_load_end: {check_results.PENDING}",
              *peak_lines(records),
              f"- prediction: {args.prediction}",
              f"- run_id: {run_id}",
              f"- repetitions: {len(records)}",
              f"- warmup_messages: {warmup:,}",
              f"- timed_messages: {records[0]['throughput']['messages']:,}",
              f"- run_started: {started}",
              f"- run_finished: {finished}"]
    return lines


def render(records, args, run_id, started, finished, machine_output, command):
    title = args.title or f"bench_replay on {args.input.name}"
    host = records[0]["header"]["host"]
    lines = [f"# {title}", "",
             "Generated by `scripts/bench_driver.py` from raw records that are not committed. "
             "Do not edit by hand.", "", f"Host: **{host}**. Numbers from the two hosts are "
             "never mixed.", ""]
    if args.note:
        lines += [f"**{args.note}**", ""]
    lines += ["## Header", ""]
    lines += header_lines(records, args, run_id, started, finished)
    lines += ["", "## Summary", "",
              f"Each repetition is a fresh process: one warm-up pass, excluded from every "
              f"figure here, then a throughput pass and a separate latency pass, each on a "
              f"fresh book. {len(records)} repetitions.", "",
              "| Figure | Median | Min | Max | CV |", "|---|---|---|---|---|"]
    lines += summary_rows(records)
    lines += ["", "## Latency", "",
              "One span is one `apply()`, with the empty pair inside it, and the frame walk "
              "outside. HdrHistogram saw the samples after the pass. A percentile less than "
              "the floor above the pair cost is not a measurement, so it has no value here.",
              "", "| Percentile | Median ns | Min ns | Max ns | CV | Status |",
              "|---|---|---|---|---|---|"]
    lines += latency_rows(records)
    lines += ["", "## Counters", "",
              "From a separate pass that is not timed, median over the repetitions. `rdpmc` "
              "in the header is the agreement with `read()` on the descriptors.", "",
              "| Event | State | Running ratio | Median count | Reason |",
              "|---|---|---|---|---|"]
    lines += counter_rows(records)
    per_message = per_message_rows(records)
    if per_message:
        lines += ["", "Per-message means, through `rdpmc`:", "",
                  "| Event | Median | Min | Max | CV |", "|---|---|---|---|---|"]
        lines += per_message
    agreement = agreement_rows(records)
    if agreement:
        lines += ["", "`rdpmc` against `read()` over the whole pass, median counts with the "
                  "widest gap of any repetition, and the per-message deltas summed, which leave "
                  "out the frame walk:", "",
                  "| Event | read() | rdpmc | Widest gap | Gap | Sum over apply() |",
                  "|---|---|---|---|---|---|"]
        lines += agreement
    lines += ["", "## Repetitions", "",
              "| Repetition | Messages/s | Instrumentation ratio | Quantum ns | Empty pair ns "
              "| Migrations | Peak RSS GiB |", "|---|---|---|---|---|---|---|"]
    lines += repetition_rows(records)
    lines += ["", "## machine_check output", "", "```", machine_output, "```", "",
              "## Commands", "", "```", command, "```", ""]
    return "\n".join(lines)


def relative_command(args):
    def shown(path):
        path = Path(path).resolve()
        try:
            return str(path.relative_to(REPO_ROOT))
        except ValueError:
            return f"<raw>/{path.name}"
    words = ["python3", "-B", "scripts/bench_driver.py", "--bench", shown(args.bench),
             "--probe", shown(args.probe), "--input", shown(args.input),
             "--prediction", args.prediction, "--repetitions", str(args.repetitions),
             "--raw-dir", "<raw>", "--out", shown(args.out)]
    if args.core is not None:
        words += ["--core", str(args.core)]
    text = " ".join(shlex.quote(word) if "<" not in word else word for word in words)
    return f"{args.wrapped_by} {text}" if args.wrapped_by else text


def append_log(path, run_id, prediction, commit, outcome):
    new = not path.is_file()
    with open(path, "a", encoding="utf-8") as log:
        if new:
            log.write("# Run log\n\nOne row for every run of `scripts/bench_driver.py`, failed "
                      "ones included. Rows are never changed or removed.\n\n"
                      "| Run | Prediction | Commit | OS | Outcome |\n|---|---|---|---|---|\n")
        outcome_text = outcome.replace("|", "/").replace("\n", " ")
        log.write(f"| {run_id} | {prediction} | {commit} | {platform.system()} | {outcome_text} |\n")


def run(args, run_id, started, commit):
    digest = hash_and_fault_in(args.input)
    code = subprocess.run([sys.executable, "-B", str(REPO_ROOT / "scripts" / "check_predictions.py"),
                           "--root", str(args.predictions_root), "--require", args.prediction],
                          capture_output=True, text=True, check=False)
    if code.returncode != 0:
        raise Refused("prediction", code.stdout.strip())
    machine_output = machine_check(args)
    records = []
    for index in range(1, args.repetitions + 1):
        record = run_repetition(args, index, commit, digest)
        validate_run(record)
        records.append(record)
    text = render(records, args, run_id, started, utc_now(), machine_output,
                  relative_command(args))
    violations = [found for found in check_results.check_text(text)
                  if found[0] != "load-not-stamped"]
    if violations:
        raise Refused("results", "; ".join(f"{code}: {detail}" for code, detail in violations))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bench", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--core", type=int)
    parser.add_argument("--sibling", type=int)
    parser.add_argument("--orders", type=int)
    parser.add_argument("--levels", type=int)
    parser.add_argument("--title")
    parser.add_argument("--note")
    parser.add_argument("--wrapped-by", help="what the run was started under, for the record")
    parser.add_argument("--allow-dirty", action="store_true", help="for tests, not for a report")
    parser.add_argument("--bench-arg", action="append", default=[], metavar="ARG")
    parser.add_argument("--predictions-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--run-log", type=Path, default=REPO_ROOT / "results" / "run_log.md",
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.repetitions < 3:
        parser.error("median, min, max and CV need at least 3 repetitions")
    return args


def main(argv=None):
    args = parse_args(argv)
    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    run_id = f"{args.prediction}-{started.replace(':', '').replace('-', '')}"
    commit = "unknown"
    try:
        commit = git_commit(REPO_ROOT, args.allow_dirty)
        run(args, run_id, started, commit)
    except Refused as refusal:
        print(f"bench_driver: refused at {refusal.stage}: {refusal}", file=sys.stderr)
        append_log(args.run_log, run_id, args.prediction, commit,
                   f"refused at {refusal.stage}: {refusal}")
        return 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"bench_driver: failed: {error!r}", file=sys.stderr)
        append_log(args.run_log, run_id, args.prediction, commit, f"failed: {error!r}")
        return 1
    append_log(args.run_log, run_id, args.prediction, commit, "published")
    print(f"bench_driver: wrote {args.out}, load lines pending. Now run scripts/stamp_load.py.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
