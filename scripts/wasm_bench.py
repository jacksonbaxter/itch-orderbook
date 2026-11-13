#!/usr/bin/env python3
"""Runs one side of T-WASM-1, the demo's replay natively or in a browser, and keeps its record.

    wasm_bench.py native  --bench BENCH_DEMO --probe BENCH_PROBE --prediction P-NNNN \\
        --raw-dir DIR [--repetitions N] [--allow-dirty]
    wasm_bench.py browser --browser PATH --probe BENCH_PROBE --prediction P-NNNN \\
        --raw-dir DIR [--repetitions N] [--timeout S] [--allow-dirty]

Each side is its own reported run, measure.sh's direct child, with /usr/bin/time -l around
the driver:

    measure.sh /usr/bin/time -l python3 -B scripts/wasm_bench.py native ... 2> STDERR

Both replay docs/local/extract.itch, whose SHA-256 must be the one extract.json gives, so
that the two sides replay the same bytes. Both confirm the prediction is on record and run
machine_check.sh first.

native runs bench_demo: a warm-up and N timed replays, each on a fresh book.

browser checks that docs/local/ holds the engine built from this commit, serves docs/ on
127.0.0.1 with the cross-origin isolation headers, starts the browser headless on the page's
bench mode with a profile of its own under the raw directory, and waits for the page to post
its record: the same warm-up and N timed replays, timed by performance.now() around each whole
call. Then it stops the browser. The browser's name and version come from the binary itself.
A page that fails, a browser that exits, or no record by --timeout refuses the run.

Other work shares this Mac and does not always wait for measure.sh, so a CPU sampler runs
beside each side, from before the run starts to after it ends: every 2 s a `sample` line, and a
`FOREIGN` line for each process above 20% CPU that is not this driver, a process under it (the
browser's helpers included) or the measure.sh and /usr/bin/time above it. Its lines go into the
record and into <side>.sampler.txt. A side whose span the sampler did not cover with no gap over
5 s, or in which it saw a foreign process, is withdrawn: its record is kept but its row says
why, and scripts/wasm_report.py refuses to publish it.

Each writes <side>.json to its own folder under the raw directory, named by its run id and
refused if another run wrote in it, with run.json beside it: the commit and tree state, each
binary's sha256, the extract hashed, the machine check and the sampler log
(scripts/run_record.py). Every run gets a row in results/run_log.md, whatever became of it. scripts/wasm_report.py turns the two records into the results file.

Exit codes: 0 record written, 1 refused or failed, 2 bad usage, 4 withdrawn under the sampler
rule, 128 + N stopped by signal N.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import json
import platform
import resource
import shlex
import signal
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_driver  # noqa: E402
import demo  # noqa: E402
import process_kinds  # noqa: E402
import run_record  # noqa: E402

REPO_ROOT = bench_driver.REPO_ROOT
Refused = bench_driver.Refused
Stopped = bench_driver.Stopped
MOST_POST_BYTES = 1 << 20
SAMPLE_EVERY_S = 2.0
FOREIGN_CPU = 20.0
LONGEST_GAP_S = 5
WITHDRAWN = 4
# The measured process's own ancestors, which use no CPU of their own while it runs.
OWN_WRAPPERS = ("measure.sh", "/usr/bin/time")
# Each flag keeps the headless browser from doing work of its own while the page runs.
BROWSER_FLAGS = ("--headless=new", "--no-first-run", "--no-default-browser-check",
                 "--disable-extensions", "--disable-background-networking",
                 "--disable-component-update", "--disable-sync", "--disable-default-apps",
                 "--mute-audio", "--disable-background-timer-throttling",
                 "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows")


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def the_extract(local):
    """extract.json, after checking that extract.itch is the file it describes."""
    manifest_path = local / "extract.json"
    if not manifest_path.is_file():
        raise Refused("extract", "no docs/local/extract.json: run scripts/demo.py extract")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = local / manifest["extract_file"]
    if not path.is_file() or sha256_of(path) != manifest["extract_sha256"]:
        raise Refused("extract", f"docs/local/{manifest['extract_file']} is not the extract "
                                 "extract.json describes")
    return manifest, path


def check_prediction(prediction, root):
    found = subprocess.run([sys.executable, "-B", str(REPO_ROOT / "scripts" / "check_predictions.py"),
                            "--root", str(root), "--require", prediction],
                           capture_output=True, text=True, check=False)
    if found.returncode != 0:
        raise Refused("prediction", found.stdout.strip())


def machine_check(script, probe, extract_path):
    result = subprocess.run(["bash", str(script), "--input", str(extract_path), "--probe", str(probe)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise Refused("machine_check", result.stdout.strip().splitlines()[-1]
                      if result.stdout.strip() else f"exited {result.returncode}")
    return result.stdout.strip()


def children_peak_rss():
    """The largest resident set of any process this one has waited for, in bytes. macOS gives
    ru_maxrss in bytes and Linux in KiB."""
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return peak if platform.system() == "Darwin" else peak * 1024


def run_child(command, log_path, timeout=None):
    with open(log_path, "w", encoding="utf-8") as log:
        bench_driver.current_child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            code = bench_driver.current_child.wait(timeout=timeout)
        finally:
            bench_driver.current_child = None
    return code


# the sampler

def processes():
    """(pid, parent pid, %CPU, command) for every process, as ps gives them."""
    listed = subprocess.run(["ps", "-Ao", "pid=,ppid=,pcpu=,args="], capture_output=True,
                            text=True, check=True).stdout
    rows = []
    for line in listed.splitlines():
        words = line.split(None, 3)
        if len(words) >= 3:
            rows.append((int(words[0]), int(words[1]), float(words[2]),
                         words[3] if len(words) == 4 else ""))
    return rows


def own_processes(rows, me):
    """This driver, every process under it, and the measure.sh and /usr/bin/time above it."""
    children = {}
    parent = {}
    command = {}
    for pid, ppid, _, args in rows:
        children.setdefault(ppid, []).append(pid)
        parent[pid] = ppid
        command[pid] = args
    own = set()
    stack = [me]
    while stack:
        pid = stack.pop()
        if pid not in own:
            own.add(pid)
            stack.extend(children.get(pid, ()))
    above = parent.get(me)
    while above and any(wrapper in command.get(above, "") for wrapper in OWN_WRAPPERS):
        own.add(above)
        above = parent.get(above)
    return own


def sample_lines(rows, me, at):
    """One sample: its line, then a FOREIGN line for each other process above the bar."""
    own = own_processes(rows, me)
    lines = [f"{at} sample"]
    for pid, _, cpu, args in sorted(rows, key=lambda row: -row[2]):
        if cpu > FOREIGN_CPU and pid not in own:
            lines.append(f"{at} FOREIGN {cpu:.1f} {args[:150]}")
    return lines


class Sampler:
    """Samples every SAMPLE_EVERY_S on a thread of its own, from start() to stop()."""

    def __init__(self, path):
        self.path = path
        self.lines = []
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def sample(self):
        try:
            lines = sample_lines(processes(), os.getpid(), bench_driver.utc_now())
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            lines = [f"{bench_driver.utc_now()} failed {error!r}"]
        self.lines += lines
        with open(self.path, "a", encoding="utf-8") as out:
            out.write("".join(line + "\n" for line in lines))

    def loop(self):
        while not self.done.wait(SAMPLE_EVERY_S):
            self.sample()

    def start(self):
        self.path.unlink(missing_ok=True)
        self.sample()
        self.thread.start()

    def stop(self):
        self.done.set()
        self.thread.join()
        self.sample()


def at(stamp):
    return datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")


def foreign_kind(command):
    """What kind of process a FOREIGN line's command is (process_kinds.kind_of): one of the
    fixed kinds through the name-to-kind map outside the repository, or another process with
    no map. The raw log keeps the command; anything committed gives the kind."""
    return process_kinds.kind_of(command)


def unquiet(lines, started, finished):
    """Why the sampler does not show the span from started to finished quiet, or None. The
    span is covered if a sample falls at or before started and one at or after finished, with
    no gap over LONGEST_GAP_S between any two of the samples given; it is quiet if no FOREIGN or
    failed line is stamped inside [started, finished]. Lines outside it, the edge samples
    included, are not judged (r5-10). A foreign process is given by its kind (foreign_kind),
    in a run and in a render of any record, old or new."""
    samples = [at(line.split()[0]) for line in lines if line.split()[1:2] == ["sample"]]
    begin, end = at(started), at(finished)
    if not samples or samples[0] > begin or samples[-1] < end:
        return "the CPU sampler did not cover the whole span"
    for before, after in zip(samples, samples[1:]):
        if (after - before).total_seconds() > LONGEST_GAP_S:
            return (f"the CPU sampler went {(after - before).total_seconds():g} s without a "
                    f"sample after {before:%H:%M:%S}")
    for line in lines:
        words = line.split(None, 3)
        if len(words) > 1 and words[1] == "failed" and begin <= at(words[0]) <= end:
            return f"the CPU sampler failed at {words[0]}"
        if len(words) == 4 and words[1] == "FOREIGN" and begin <= at(words[0]) <= end:
            return f"the CPU sampler saw {foreign_kind(words[3])} at {words[2]}% CPU at {words[0]}"
    return None


# native

def native(args, manifest, extract_path, commit):
    bench_driver.set_stage("bench_demo")
    record_path = args.raw_dir / "bench_demo.json"
    record_path.unlink(missing_ok=True)
    code = run_child([str(args.bench), "--extract", str(extract_path), "--out", str(record_path),
                      "--repetitions", str(args.repetitions), "--commit", commit,
                      "--dataset", f"{manifest['symbol']} of {manifest['day']}",
                      "--dataset-sha256", manifest["extract_sha256"]],
                     args.raw_dir / "bench_demo.log")
    if not record_path.is_file():
        raise Refused("bench_demo", f"exited {code} with no record")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if code != 0 or record["refusals"]:
        raise Refused("bench_demo", f"exited {code}: {'; '.join(record['refusals']) or 'no reason'}")
    if record["digest"] != manifest["digest"]:
        raise Refused("bench_demo", f"digest {record['digest']} is not the extract's "
                                    f"{manifest['digest']}")
    return record


# browser

class BenchHandler(demo.DemoHandler):
    """The demo's server, with one more route: the page posts its record to /result."""

    def do_POST(self):  # noqa: N802 - the base class's name
        length = int(self.headers.get("Content-Length", "0"))
        if self.path != "/result" or not 0 < length <= MOST_POST_BYTES:
            self.send_error(400)
            return
        body = self.rfile.read(length)
        self.send_response(204)
        self.end_headers()
        self.server.result = body
        self.server.posted.set()


def browser_version(binary):
    found = subprocess.run([str(binary), "--version"], capture_output=True, text=True,
                           check=False, timeout=60)
    version = found.stdout.strip()
    if found.returncode != 0 or not version:
        raise Refused("browser", f"{binary.name} --version exited {found.returncode}")
    return version


def the_engine(local, commit, allow_dirty):
    """engine.json, after checking the engine in docs/local/ came from this commit, and the
    page from it too."""
    info_path = local / "engine.json"
    if not info_path.is_file() or not (local / "itch_demo.mjs").is_file():
        raise Refused("engine", "no engine in docs/local/: run scripts/demo.py build")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    clean = "(clean)" in info.get("commit", "") or allow_dirty
    if info.get("commit", "").split()[0] != commit.split()[0] or not clean:
        raise Refused("engine", f"the engine was built from {info.get('commit')}, not from "
                                f"{commit}: run scripts/demo.py build")
    if sha256_of(local / "itch_demo.wasm") != info.get("wasm_sha256"):
        raise Refused("engine", "itch_demo.wasm is not the file engine.json describes")
    page = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--", "docs"],
                          capture_output=True, text=True, check=False).stdout
    if page.strip() and not allow_dirty:
        raise Refused("engine", "the page in docs/ has uncommitted changes")
    return info


def browser(args, manifest, commit):
    bench_driver.set_stage("checking the engine")
    engine = the_engine(args.docs / "local", commit, args.allow_dirty)
    bench_driver.set_stage("reading the browser's version")
    version = browser_version(args.browser)
    server = demo.make_server(0, BenchHandler, args.docs)
    server.quiet = True
    server.result = None
    server.posted = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/index.html?bench={args.repetitions}"
    profile = args.raw_dir / "browser-profile"
    command = [str(args.browser), *BROWSER_FLAGS, f"--user-data-dir={profile}", url]
    bench_driver.set_stage("the page's replays")
    try:
        with open(args.raw_dir / "browser.log", "w", encoding="utf-8") as log:
            bench_driver.current_child = subprocess.Popen(command, stdout=log,
                                                          stderr=subprocess.STDOUT)
            child = bench_driver.current_child
            posted = False
            waited = 0.0
            while waited < args.timeout and child.poll() is None:
                if server.posted.wait(timeout=1.0):
                    posted = True
                    break
                waited += 1.0
            exited = child.poll()
            child.terminate()
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            bench_driver.current_child = None
    finally:
        server.shutdown()
        server.server_close()
    if not posted:
        raise Refused("browser", f"the browser exited {exited} before the page posted"
                      if exited is not None else f"no record from the page in {args.timeout:g} s")
    record = json.loads(server.result)
    record.update({"browser": version, "browser_binary": args.browser.name,
                   "browser_flags": list(BROWSER_FLAGS), "engine_built": engine,
                   "browser_tree_peak_rss_bytes": children_peak_rss()})
    if record.get("refusals"):
        raise_after = "; ".join(record["refusals"])
        (args.raw_dir / "browser.json").write_text(json.dumps(record, indent=2) + "\n",
                                                   encoding="utf-8")
        raise Refused("page", raise_after)
    if record["extract"]["sha256"] != manifest["extract_sha256"]:
        raise Refused("page", "the page replayed another extract")
    if record["wasm_sha256"] != engine["wasm_sha256"]:
        raise Refused("page", "the page loaded another engine")
    if record["memory_bytes"] != record["memory_bytes_after"]:
        raise Refused("page", "the engine's memory grew")
    if len(record.get("repetitions", [])) != args.repetitions:
        raise Refused("page", f"{len(record.get('repetitions', []))} repetitions, not "
                              f"{args.repetitions}")
    return record


def relative(path):
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return f"<{Path(path).name}>"


def command_line(args):
    words = ["python3", "-B", "scripts/wasm_bench.py", args.side]
    if args.side == "native":
        words += ["--bench", relative(args.bench)]
    else:
        words += ["--browser", f"<{args.browser.name}>", "--timeout", f"{args.timeout:g}"]
    words += ["--probe", relative(args.probe), "--prediction", args.prediction,
              "--repetitions", str(args.repetitions), "--raw-dir", "<raw>"]
    return " ".join(shlex.quote(word) if "<" not in word else word for word in words)


def run(args, run_id, started, commit, sampler):
    bench_driver.set_stage("checking the extract")
    manifest, extract_path = the_extract(args.docs / "local")
    # Hashed from the file the run reads, never copied from a manifest (r5-6).
    args.record.dataset(extract_path, run_record.sha256_of(extract_path))
    bench_driver.set_stage("checking the prediction")
    check_prediction(args.prediction, args.predictions_root)
    bench_driver.set_stage("machine_check")
    machine_output = machine_check(args.machine_check, args.probe, extract_path)
    args.record.output(bench_driver.MACHINE_CHECK, machine_output)
    if args.side == "native":
        record = native(args, manifest, extract_path, commit)
    else:
        record = browser(args, manifest, commit)
    bench_driver.hold_signals()
    finished = bench_driver.utc_now()
    sampler.stop()
    args.record.kept_file(sampler.path.name)
    record.update({"side": args.side, "prediction": args.prediction, "run_id": run_id,
                   "commit": commit, "run_started": started, "run_finished": finished,
                   "machine_check": machine_output, "command": command_line(args),
                   "extract_manifest": manifest,
                   "sampler": {"every_s": SAMPLE_EVERY_S, "foreign_cpu": FOREIGN_CPU,
                               "longest_gap_s": LONGEST_GAP_S, "lines": sampler.lines}})
    out = args.raw_dir / f"{args.side}.json"
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return out, unquiet(sampler.lines, started, finished)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("side", choices=("native", "browser"))
    parser.add_argument("--bench", type=Path)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=21)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--allow-dirty", action="store_true", help="for tests, not for a report")
    parser.add_argument("--run-log", type=Path, default=REPO_ROOT / "results" / "run_log.md",
                        help=argparse.SUPPRESS)
    parser.add_argument("--docs", type=Path, default=REPO_ROOT / demo.DOCS, help=argparse.SUPPRESS)
    parser.add_argument("--machine-check", type=Path,
                        default=REPO_ROOT / "scripts" / "machine_check.sh", help=argparse.SUPPRESS)
    parser.add_argument("--predictions-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.repetitions < 3:
        parser.error("median, min, max and CV need at least 3 repetitions")
    if args.side == "native" and args.bench is None:
        parser.error("native needs --bench")
    if args.side == "browser" and args.browser is None:
        parser.error("browser needs --browser")
    return args


def main(argv=None):
    args = parse_args(argv)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, bench_driver.forward_signal)
    started = bench_driver.utc_now()
    # One word after the prediction, as results_table.py reads the log's rows.
    run_id = f"{args.prediction}-{started.replace(':', '').replace('-', '')}_{args.side}"
    commit = "unknown"
    sampler = None
    try:
        run_record.open_run(args, run_id)
        sampler = Sampler(args.raw_dir / f"{args.side}.sampler.txt")
        sampler.start()
        commit = bench_driver.git_commit(REPO_ROOT, args.allow_dirty)
        run_record.start_record(args, "wasm_bench", run_id, commit,
                                (args.bench if args.side == "native" else args.browser,
                                 args.probe))
        written, withdrawn = run(args, run_id, started, commit, sampler)
    except Stopped as stop:
        bench_driver.stop_child()
        name = signal.Signals(stop.signum).name
        print(f"wasm_bench: stopped by {name} at {bench_driver.stage}", file=sys.stderr)
        bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                                f"T-WASM-1 {args.side}: stopped by {name} at "
                                f"{bench_driver.stage}; nothing from it is published")
        return 128 + stop.signum
    except (Refused, run_record.Incomplete) as refusal:
        bench_driver.hold_signals()
        print(f"wasm_bench: refused at {refusal.stage}: {refusal}", file=sys.stderr)
        bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                                f"T-WASM-1 {args.side}: refused at {refusal.stage}: {refusal}")
        return 1
    except Exception as error:  # Last, so that no run ends without its row.
        return bench_driver.log_failure("wasm_bench", args.run_log, run_id, args.prediction,
                                        commit, error, f"T-WASM-1 {args.side}: ")
    finally:
        if sampler is not None:
            sampler.done.set()
    bench_driver.hold_signals()
    if withdrawn:
        bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                                f"withdrawn: T-WASM-1's {args.side} side; {withdrawn}; nothing from "
                                f"it is published, and it is rerun under {args.prediction}")
        print(f"wasm_bench: withdrawn: {withdrawn}; the record is in {written}", file=sys.stderr)
        return WITHDRAWN
    bench_driver.append_log(args.run_log, run_id, args.prediction, commit,
                            f"recorded: T-WASM-1's {args.side} side, for scripts/wasm_report.py; "
                            "the CPU sampler saw no other process above 20% CPU")
    print(f"wasm_bench: wrote {written}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
