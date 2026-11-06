#!/usr/bin/env python3
"""Runs one SIMD experiment (simd.md): every variant, in fresh processes, under measure.sh.

    simd_driver.py --experiment NAME --prediction P-NNNN --raw-dir DIR --input DAY \\
        --dataset NAME --dataset-sha256 HEX --bench BENCH_KERNELS --probe BENCH_PROBE \\
        --measure MEASURE_SH [--node-module ITCH_E11_MJS] [--repetitions N] [--kernels K,...] \\
        [--allow-dirty]

NAME is a bench_kernels experiment (field, decode, consume, parse, book, batch, framing,
filter, timestamp); e11, which runs scripts/simd_wasm_e11.mjs in node on the Emscripten
build's itch_e11 module; or asm, E12, which runs scripts/simd_asm_audit.py collect once
(compiler output does not vary, so --repetitions 1). Each process is

    MEASURE_SH /usr/bin/time -l <bench_kernels or node> ... > STEM.out 2> STEM.err

with its record in STEM.json: MEASURE_SH is the host's measure.sh. On debian there is no
/usr/bin/time -l, and bench_kernels runs on core 4. The variants of each repetition run one
after another, in an order turned by one place each repetition, so that a drift over the run
falls on all of them.

The vector kernels are every kernel `bench_kernels --list-kernels` says runs here other than
scalar, or the --kernels given: on this Mac neon, on debian ssse3 and avx2. Each kernel's
variant is named after it, so the results file says which instruction set every figure used.

On macOS, where nothing pins a process and other projects share the machine, a CPU sampler
runs beside the whole run (scripts/wasm_bench.py's): every 2 s a `sample` line, and a `FOREIGN`
line for any process above 20% CPU outside this driver and what it runs. A process is published
only if the sampler covered its whole span, from measure.sh's start line to its end line, with
no gap over 5 s and no foreign process inside it. Otherwise it is withdrawn, with its own row in
results/run_log.md giving the reason, and run again as a further repetition, at most
MOST_WITHDRAWN times in a run. On Linux the machine check runs before and after.

The run directory, DIR/<run id>/, holds every process's files, the sampler's log and
manifest.json, which lists every process and what became of it. scripts/simd_report.py
turns it into the results file. results/run_log.md gets a row for the run and for each
process that was not published.

Exit codes: 0 every variant has its repetitions published, 1 refused or incomplete, 2 bad
usage.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_driver  # noqa: E402
import wasm_bench  # noqa: E402

REPO_ROOT = bench_driver.REPO_ROOT
AUDIT = REPO_ROOT / "scripts" / "simd_asm_audit.py"
MOST_WITHDRAWN = 12
RETRY_BUSY_S = 60
SAMPLE_WAIT_S = 15
MEASURE_LINE = re.compile(r"^measure\.sh: (start|end) .*\bat=(\S+)")
EXPERIMENTS = ("field", "decode", "consume", "parse", "book", "batch", "framing", "filter",
               "timestamp", "e11", "asm")
FORMS = {"framing": ("length", "byte", "table", "constant"),
         "timestamp": ("exact", "split", "overread", "underread")}
WIDTHS = (2, 4, 6, 8)
WATCHLISTS = (8, 64)


class Refused(Exception):
    """The run cannot go on."""


def vector_kernels(bench, given):
    if given:
        return given.split(",")
    listed = subprocess.run([str(bench), "--list-kernels"], capture_output=True, text=True,
                            check=True).stdout
    return [line.split(":")[0] for line in listed.splitlines()
            if line.endswith(": runs") and not line.startswith("scalar:")]


def variants(experiment, kernels):
    """(name, bench_kernels arguments) for every variant of the experiment."""
    every = ["scalar", *kernels]
    if experiment == "field":
        return [(f"{k}-w{w}", ["--kernel", k, "--width", str(w)]) for w in WIDTHS for k in every]
    if experiment in FORMS:
        return [(form, ["--form", form]) for form in FORMS[experiment]]
    if experiment == "filter":
        out = []
        for n in WATCHLISTS:
            out += [(f"compare-{k}-n{n}",
                     ["--form", "compare", "--kernel", k, "--watchlist", str(n)]) for k in every]
            out.append((f"bitset-n{n}", ["--form", "bitset", "--watchlist", str(n)]))
        return out
    if experiment == "e11":
        return [("scalar", ["--kernel", "scalar"]), ("wasm", ["--kernel", "wasm"])]
    if experiment == "asm":
        return [("audit", [])]
    return [(k, ["--kernel", k]) for k in every]


def command(args, name, extra, stem, commit):
    if args.experiment == "asm":
        return [sys.executable, "-B", str(AUDIT), "collect", "--out", f"{stem}.json",
                "--commit", commit]
    if args.experiment == "e11":
        return ["node", str(REPO_ROOT / "scripts" / "simd_wasm_e11.mjs"), "--module",
                str(args.node_module), "--input", str(args.input), "--out", f"{stem}.json",
                "--commit", commit, "--dataset", args.dataset, "--dataset-sha256",
                args.dataset_sha256, *extra]
    return [str(args.bench), "--input", str(args.input), "--out", f"{stem}.json", "--experiment",
            args.experiment, "--commit", commit, "--dataset", args.dataset, "--dataset-sha256",
            args.dataset_sha256, *extra] + (["--core", "4"] if platform.system() == "Linux" else [])


def span_of(err_text):
    """measure.sh's start and end times for the process, or None if either is missing."""
    found = {}
    for line in err_text.splitlines():
        match = MEASURE_LINE.match(line)
        if match:
            found[match[1]] = match[2]
    return (found["start"], found["end"]) if "start" in found and "end" in found else None


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_one(args, run_dir, name, extra, repetition, commit):
    """One process, retried while measure.sh is busy. (stem, exit code)."""
    stem = run_dir / f"{name}-{repetition}"
    words = [str(args.measure), *(["/usr/bin/time", "-l"] if platform.system() == "Darwin" else []),
             *command(args, name, extra, stem, commit)]
    while True:
        with open(f"{stem}.out", "w", encoding="utf-8") as out, \
                open(f"{stem}.err", "w", encoding="utf-8") as err:
            code = subprocess.run(words, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                  check=False).returncode
        if code != 75:
            return stem, code
        time.sleep(RETRY_BUSY_S)


def judge(args, stem, code, sampler):
    """None if the process is published, else why not."""
    err = Path(f"{stem}.err").read_text(encoding="utf-8", errors="replace")
    if code != 0:
        refusals = [line for line in err.splitlines() if "refused:" in line]
        return f"exited {code}" + (f": {'; '.join(refusals)}" if refusals else "")
    if not Path(f"{stem}.json").is_file():
        return "exited 0 with no record"
    span = span_of(err)
    if span is None:
        return "measure.sh's start or end line is missing"
    if sampler is not None:
        wait_for_sample(sampler, span[1])
        return wasm_bench.unquiet(sampler.lines, *span)
    return None


def wait_for_sample(sampler, finished):
    """Waits, at most SAMPLE_WAIT_S, for the sampler's first sample at or after the process's
    end, without which no span is covered: the process has just exited, and the sampler
    samples every 2 s."""
    deadline = time.monotonic() + SAMPLE_WAIT_S
    end = wasm_bench.at(finished)
    while time.monotonic() < deadline:
        if any(line.split()[1:2] == ["sample"] and wasm_bench.at(line.split()[0]) >= end
               for line in list(sampler.lines)):
            return
        time.sleep(0.5)


def run(args, run_id, commit):
    run_dir = args.raw_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    kernels = [] if args.experiment in (*FORMS, "asm", "e11") else \
        vector_kernels(args.bench, args.kernels)
    plan = variants(args.experiment, kernels)
    if args.experiment == "e11" and args.node_module is None:
        raise Refused("usage", "--experiment e11 needs --node-module")
    binary = {"e11": args.node_module, "asm": AUDIT}.get(args.experiment, args.bench)
    manifest = {"run_id": run_id, "prediction": args.prediction, "experiment": args.experiment,
                "commit": commit, "host_os": platform.system(), "binary": str(binary.name),
                "binary_sha256": sha256_of(binary), "dataset": args.dataset,
                "dataset_sha256": args.dataset_sha256, "repetitions": args.repetitions,
                "variants": [name for name, _ in plan], "processes": [],
                "sampler": "sampler.txt" if platform.system() == "Darwin" else None,
                "started": bench_driver.utc_now()}
    if platform.system() == "Linux":
        manifest["machine_check_before"] = bench_driver.machine_check(args)
    sampler = None
    if platform.system() == "Darwin":
        sampler = wasm_bench.Sampler(run_dir / "sampler.txt")
        sampler.start()
    published = {name: 0 for name, _ in plan}
    withdrawn = 0
    repetition = 0
    try:
        while min(published.values()) < args.repetitions:
            repetition += 1
            turn = (repetition - 1) % len(plan)
            for name, extra in plan[turn:] + plan[:turn]:
                if published[name] >= args.repetitions:
                    continue
                stem, code = run_one(args, run_dir, name, extra, repetition, commit)
                if code == 2:
                    raise Refused(name, f"bench exited 2: "
                                        f"{Path(f'{stem}.err').read_text(encoding='utf-8')[-400:]}")
                why = judge(args, stem, code, sampler)
                entry = {"variant": name, "repetition": repetition, "stem": stem.name,
                         "exit": code, "published": why is None, "why": why}
                manifest["processes"].append(entry)
                (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                                        encoding="utf-8")
                if why is None:
                    published[name] += 1
                    continue
                withdrawn += 1
                bench_driver.append_log(
                    args.run_log, f"{run_id}_{name}-{repetition}", args.prediction, commit,
                    f"withdrawn: {args.experiment}, {name}, repetition {repetition}: {why}; "
                    f"nothing from it is published, and it is rerun under {args.prediction}")
                if withdrawn > MOST_WITHDRAWN:
                    raise Refused(name, f"{withdrawn} processes withdrawn, more than "
                                        f"{MOST_WITHDRAWN}: the Mac is not quiet enough")
    finally:
        if sampler is not None:
            sampler.stop()
        manifest["finished"] = bench_driver.utc_now()
        if platform.system() == "Linux":
            try:
                manifest["machine_check_after"] = bench_driver.machine_check(args)
            except bench_driver.Refused as error:
                manifest["machine_check_after"] = f"failed: {error}"
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                                encoding="utf-8")
    return withdrawn


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--experiment", required=True, choices=EXPERIMENTS)
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--dataset-sha256", required=True)
    parser.add_argument("--bench", required=True, type=Path)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--measure", required=True, type=Path)
    parser.add_argument("--node-module", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--kernels")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--run-log", type=Path, default=REPO_ROOT / "results" / "run_log.md")
    args = parser.parse_args(argv)
    args.core = 4 if platform.system() == "Linux" else None
    args.sibling = None
    return args


def main(argv=None):
    args = parse_args(argv)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{args.prediction}-{stamp}"
    try:
        commit = bench_driver.git_commit(REPO_ROOT, args.allow_dirty)
        checker = REPO_ROOT / "scripts" / "check_predictions.py"
        if subprocess.run([sys.executable, "-B", str(checker), "--require", args.prediction],
                          check=False).returncode != 0:
            raise Refused("prediction", f"{args.prediction} is not on record")
        withdrawn = run(args, run_id, commit)
    except (Refused, bench_driver.Refused) as error:
        bench_driver.append_log(args.run_log, run_id, args.prediction,
                                locals().get("commit", "unknown"), f"refused: {error}")
        print(f"simd_driver: refused: {error}", file=sys.stderr)
        return 1
    outcome = "recorded: every variant's repetitions published under the sampler rule"
    if withdrawn:
        outcome += f", after {withdrawn} withdrawn and rerun"
    bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                            f"{outcome}; for scripts/simd_report.py")
    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
