#!/usr/bin/env python3
"""Runs one step of the UDP replay's comparison on debian (T-URING-1, T-URING-2; the
comparison plan of build-research/evidence/io-uring-flamegraph.md): every timed process a
fresh bench_udp under measure.sh, pinned by taskset.

    udp_driver.py --step STEP --prediction P-NNNN --raw-dir DIR --input DAY --dataset NAME \\
        --dataset-sha256 HEX --bench BENCH_UDP --probe BENCH_PROBE --measure MEASURE_SH \\
        [--modes ID,...] [--repetitions N] [--seed N] [--core S] [--receiver-core R] \\
        [--sq-core Q] [--first F] [--frames-cap N] [--trial-seconds S] [--datagram BYTES] \\
        [--allow-dirty] [--run-log FILE] STEP'S OPTIONS

    smoke     --heavy HEAVY_SH --loopback ITCH_LOOPBACK [--smoke-frames N] [--smoke-rate R]
    ceiling   (no more)
    bisect    (--ceiling MSGS_PER_S | --ceiling-from CEILING_RUN_DIR) [--start-rate R]
              [--floor-rate R] [--most-steps N]
    feed      --heavy HEAVY_SH [--feed-lead-frames N] [--flush-us U]
    latency   --bisect-from BISECT_RUN_DIR [--latency-fractions F,...]
              [--low-packets-per-s N] [--cpu-rate MSGS_PER_S]

Every timed process is

    MEASURE_SH taskset -c S,R[,Q] BENCH_UDP DAY --mode M ... --core S --receiver-core R
        [--sq-core Q] --commit C --dataset D --dataset-sha256 H --out STEM.json
        > STEM.out 2> STEM.err

the benchmark measure.sh's direct child through taskset's exec, its output to files, never
streamed. Q is U5's SQ thread and is in the mask for U5 only. The sender alone (ceiling) runs
with the same mask and only --core. Exit 75 from measure.sh is retried after a wait. The
default cores are 4 for the sender, 6 for the receiver and 2 for the SQ thread. Each
repetition, or for bisect each round, runs the modes in an order shuffled by a seeded
generator, the seed in the manifest, so that a drift over the run falls on every mode.

The steps:
- smoke: no measure.sh and no figure. Records the host's readings into smoke.json and
  smoke.txt (kernel, io_uring_disabled and io_uring_group, rmem_max and rmem_default,
  ulimit -l, every vulnerabilities line, lo's MTU, the clocksource, the three cores'
  governors and SMT siblings, nmi_watchdog, perf_event_paranoid, ip_early_demux,
  CONFIG_IRQ_TIME_ACCOUNTING, lscpu), `bench_udp --probe`, and one functional itch_loopback
  per mode under heavy.sh, paced at --smoke-rate msg/s, each verdict. Paced, because an
  unpaced sender outruns a receiver that applies every message to the book, and the check
  is that each mode delivers, not how fast. A reading that cannot be had is recorded as
  `unreadable: <why>`, never filled in, and no limit is changed.
- ceiling: the sender alone, unpaced, --repetitions times (the comparison plan's task 2).
  The manifest gets the median achieved messages/s and packets/s. A sender run that failed or
  did not PASS refuses the step, with why in the run log, never left out of the median.
- bisect: each mode's largest lossless message rate. A trial runs min(rate x
  --trial-seconds, --frames-cap) frames from --first. No trial runs above the cap, 0.9 x the
  sender's ceiling: a mode lossless at the cap is "at least the cap, sender limited". A trial
  is lossless only if its record says lossless, every loss counter in it is zero, its verdict
  is PASS and the sender kept pace, achieving at least 0.99 x the target. From --start-rate,
  halved while lossy down to --floor-rate ("below" the last rate tried), then the cap, then
  geometric bisection between the lossless low and the lossy high until high/low <= 1.02 or
  --most-steps midpoints. Then T-URING-2: the best mode at 2,547,190 msg/s, --repetitions
  times, if that is under the cap; otherwise the manifest says it cannot be tested there.
  T-URING-2 is judged at 1,400 bytes a datagram (P-0902), so a bisection at any other
  --datagram, such as T-URING-1's small-packet arm, runs no confirmation and says why.
- feed: the day's busiest 1 s and 1 ms (bench_udp --peaks, under heavy.sh), then a window of
  --frames-cap frames from --feed-lead-frames before the busiest second, replayed by its ITCH
  timestamps three ways: x1; sped up so its own busiest 1 s runs at 1,305,440 msg/s; and so
  its own busiest 1 ms runs at 2,547,190 msg/s (speedup = target / peak rate). Each packs
  with --flush-gap-ns = --flush-us x 1000 x speedup, so a packet closes after the same
  replay time whatever the speed, and --timeout-ms above the window's longest gap at that
  speed. Pass or fail per mode and schedule is lossless.
- latency: per mode, fixed rates: --low-packets-per-s packets/s in messages/s, by the bisect
  run's messages a packet; each of --latency-fractions of the mode's largest lossless rate
  from --bisect-from; and --cpu-rate, one rate for every mode, whose records give the CPU
  split.

Before and after every step but smoke, the day is read through and its hash checked against
--dataset-sha256 (bench_udp drops the day's pages after packing, and machine_check.sh refuses
an input not resident), and the machine check runs on every core of the step's mask, the
sender's, the receiver's and, where the step runs U5, the SQ thread's, each with its SMT
sibling read from sysfs: `MEASURE_SH taskset -c C bash scripts/machine_check.sh ... --core C
--sibling S`, one after another, and the manifest lists the cores and siblings checked
(r5-11; before, one check covered core 4 and its sibling only). A failed check before
refuses the run. Before and after every step the manifest also gets perf_event_paranoid, the uptime
and pswpout from /proc/vmstat, and a warning at its top if perf_event_paranoid reads 3.

The run directory, DIR/<run id>/, refused if another run wrote in it, holds every process's
files, manifest.json, which lists every process and what became of it, and run.json
(scripts/run_record.py): the commit and tree state, each binary's sha256, the day hashed, and
each machine check untouched. scripts/udp_report.py turns run directories into a
results file. results/run_log.md gets a row for the run and one for every process that
failed, and the prediction must be on record before anything runs (check_predictions.py
--require). No Mac loopback figure is a result, so the driver runs on Linux only.

Exit codes: 0 the step ran to its end with every process usable, 1 refused or incomplete, 2
bad usage or not Linux.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import mmap
import platform
import random
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_driver  # noqa: E402
import run_record  # noqa: E402
import simd_driver  # noqa: E402

REPO_ROOT = bench_driver.REPO_ROOT
Refused = bench_driver.Refused
STEPS = ("smoke", "ceiling", "bisect", "feed", "latency")
MODES = ("S1", "S2", "S3", "S4", "U1", "U2", "U3", "U4", "U5")
SQ_MODE = "U5"
SPIN_MODES = ("S4", "U4", "U5")
TARGET_1S = 1_305_440  # BENCHMARKS.md, DS-78: Nasdaq's 1 s peak, msg/s.
TARGET_1MS = 2_547_190  # T-URING-2: Nasdaq's 1 ms peak, msg/s.
TURING2_DATAGRAM = 1400  # T-URING-2's packet size, bytes a datagram (P-0902).
CAP_FRACTION = 0.9
PACE = 0.99
TOLERANCE = 1.02
RETRY_BUSY_S = 60
MIN_TIMEOUT_MS = 2000
MOST_INT = 2**31 - 1
# The counters a lossless record has at zero; rxq_overflow is null for a mode without
# SO_RXQ_OVFL, and any other null is a counter that could not be read, which is not zero.
LOSS_COUNTERS = ("failed_sends", "tracker_gaps", "tracker_lost", "tracker_duplicates",
                 "truncated", "no_buffers", "cq_overflow", "socket_drops", "rcvbuf_errors",
                 "in_errors", "softnet_dropped")
OPTIONAL_COUNTERS = ("rxq_overflow",)
PEAK_LINE = re.compile(r"^bench_udp_peak_(1s|1ms) (\d+) messages from frame (\d+) at (\d+) ns$",
                       re.MULTILINE)
VERDICT_LINE = re.compile(r"^loopback_verdict (\S+)", re.MULTILINE)
FEED_SCHEDULES = (("feed-x1", None), ("feed-1s", TARGET_1S), ("feed-1ms", TARGET_1MS))
PEAK_SECONDS = {"feed-1s": 1.0, "feed-1ms": 0.001}


# --- What a record says ------------------------------------------------------------------

def lossless_of(record):
    """Lossless from the record's own counters: delivered, and zero on every layer."""
    drops = record.get("drops") or {}
    if not record.get("delivered"):
        return False
    for name in LOSS_COUNTERS:
        if drops.get(name) != 0:
            return False
    return all(drops.get(name) in (0, None) for name in OPTIONAL_COUNTERS)


def trial_lossless(record, target):
    """(lossless, why not): a rate trial counts only if the record says lossless, its
    counters agree, its verdict is PASS and the sender kept pace with the target."""
    if record.get("kind") != "receive":
        return False, f"a {record.get('kind')} record, not a receive run"
    if not record.get("lossless"):
        return False, "the record is not lossless"
    if not lossless_of(record):
        return False, f"the record calls itself lossless with counters {record.get('drops')}"
    if record.get("verdict") != "PASS":
        return False, f"verdict {record.get('verdict')}"
    achieved = record["send"]["achieved_messages_per_s"]
    if achieved < PACE * target:
        return False, f"the sender fell behind: {achieved:,.0f} of {target:,} msg/s"
    return True, None


def ceiling_of(values):
    """The sender's ceiling: the median of the runs' achieved rates, unrounded. Before
    2026-11-13 each rate was rounded to whole messages/s first, as the results file shows it."""
    return statistics.median(values)


def cap_of(ceiling):
    """The highest rate a trial may run at: 0.9 x the sender's ceiling, rounded down."""
    return int(ceiling * 9 // 10)


def frames_for(rate, seconds, frames_cap):
    return max(1, min(int(rate * seconds), frames_cap))


# --- The bisection -----------------------------------------------------------------------

class Bisection:
    """One mode's search for its largest lossless rate. next_rate() is the rate to try, None
    once done; record() takes each trial's outcome. result() says what was found."""

    def __init__(self, cap, start, floor, most_steps=10, tolerance=TOLERANCE):
        self.cap = cap
        self.floor = floor
        self.most_steps = most_steps
        self.tolerance = tolerance
        self.lo = None
        self.hi = None
        self.steps = 0
        self.kind = None
        self.why = None
        self.tried = []
        self.rate = min(start, cap)

    def next_rate(self):
        return None if self.kind is not None else self.rate

    def fail(self, why):
        self.kind, self.why = "failed", why

    def record(self, rate, lossless):
        self.tried.append([rate, lossless])
        if self.lo is None and not lossless:
            self.hi = rate
            if rate // 2 < self.floor:
                self.kind = "below"
                return
            self.rate = rate // 2
            return
        if self.lo is None:
            self.lo = rate
            if self.hi is None:
                if rate >= self.cap:
                    self.kind = "sender limited"
                else:
                    self.rate = self.cap
                return
        elif self.hi is None:
            if lossless:
                self.lo = rate
                self.kind = "sender limited"
                return
            self.hi = rate
        else:
            self.steps += 1
            if lossless:
                self.lo = rate
            else:
                self.hi = rate
        mid = int(round(math.sqrt(self.lo * self.hi)))
        if self.hi / self.lo <= self.tolerance or self.steps >= self.most_steps or \
                mid in (self.lo, self.hi):
            self.kind = "max"
            return
        self.rate = mid

    def result(self):
        """kind is max, sender limited, below or failed; rate is the largest lossless rate,
        or for below the smallest rate tried, none of them lossless."""
        rate = self.hi if self.kind == "below" else self.lo
        return {"result": self.kind, "rate": rate, "lossy_above": self.hi, "why": self.why,
                "steps": self.steps, "tried": self.tried}


def best_mode(results):
    """The mode with the largest lossless rate, a mode at the cap counting as the cap; ties go
    to the earlier mode in MODES."""
    found = [(mode, one["rate"]) for mode, one in results.items()
             if one["result"] in ("max", "sender limited")]
    if not found:
        return None
    return max(found, key=lambda pair: (pair[1], -MODES.index(pair[0])))[0]


# --- Feed and latency ---------------------------------------------------------------------

def parse_peaks(text):
    """{"1s": (messages, frame, ns), "1ms": ...} from bench_udp --peaks."""
    found = {label: (int(count), int(frame), int(at))
             for label, count, frame, at in PEAK_LINE.findall(text)}
    if set(found) != {"1s", "1ms"}:
        raise Refused("peaks", f"bench_udp --peaks gave {sorted(found)}, not 1s and 1ms")
    return found


def speedup_for(target, peak_messages, seconds):
    """How much faster than real time the window must run for its busiest span of `seconds`
    to reach `target` msg/s."""
    if peak_messages <= 0:
        raise Refused("peaks", "the window has no messages")
    return target / (peak_messages / seconds)


def flush_gap_ns(flush_us, speedup):
    """The flush gap in ITCH time: flush_us of replay time at this speedup."""
    return int(round(flush_us * 1000 * speedup))


def timeout_ms(longest_gap_ns, speedup):
    """An idle timeout above the window's longest gap as replayed, with room to spare."""
    replayed_ms = longest_gap_ns / speedup / 1e6
    return min(MOST_INT, max(MIN_TIMEOUT_MS, math.ceil(2 * replayed_ms) + 1000))


def window_gaps(path, first, frames):
    """(span, longest gap) in ITCH ns over frames first to first + frames of a BinaryFILE
    day, walked as bench_udp --peaks walks it: a frame is a 2-byte length and its payload,
    and a payload of 11 bytes or more has a timestamp at bytes 5 to 11."""
    longest = 0
    start = last = None
    with open(path, "rb") as file, mmap.mmap(file.fileno(), 0, prot=mmap.PROT_READ) as data:
        size = len(data)
        offset = index = 0
        end = first + frames
        while index < end and offset + 2 <= size:
            length = int.from_bytes(data[offset:offset + 2], "big")
            payload = offset + 2
            if payload + length > size:
                break
            if index >= first and length >= 11:
                stamp = int.from_bytes(data[payload + 5:payload + 11], "big")
                if last is not None:
                    longest = max(longest, stamp - last)
                start = stamp if start is None else start
                last = stamp
            offset = payload + length
            index += 1
    return (0 if start is None else last - start), longest


def messages_per_packet(records):
    """The median of the records' messages over data packets, heartbeats and the end of
    session left out."""
    values = [record["plan"]["messages"] / (record["plan"]["packets"]
                                            - record["plan"]["heartbeats"] - 1)
              for record in records
              if record["plan"]["packets"] - record["plan"]["heartbeats"] - 1 > 0]
    if not values:
        raise Refused("latency", "no record to take messages a packet from")
    return statistics.median(values)


def latency_rates(results, fractions, low_packets, per_packet, cpu_rate, cap):
    """{mode: [(role, rate)]}: the low rate in messages/s, each fraction of the mode's largest
    lossless rate, and the common CPU rate. A rate above the cap is refused."""
    low = int(round(low_packets * per_packet))
    out = {}
    for mode, one in results.items():
        rates = [("latency-low", low)]
        if one["result"] in ("max", "sender limited"):
            rates += [(f"latency-{fraction:g}", int(fraction * one["rate"]))
                      for fraction in fractions]
        if cpu_rate is not None:
            rates.append(("cpu", cpu_rate))
        for role, rate in rates:
            if rate > cap:
                raise Refused("latency", f"{mode} {role} at {rate:,} msg/s is above the cap "
                                         f"{cap:,}, 0.9 x the sender's ceiling")
            if rate <= 0:
                raise Refused("latency", f"{mode} {role} has no rate")
        out[mode] = rates
    return out


# --- Readings ------------------------------------------------------------------------------

def read_text(path):
    """A file's text, stripped, or `unreadable: <why>`."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError as error:
        return f"unreadable: {error.strerror or error}"


def command_text(words):
    try:
        result = subprocess.run(words, capture_output=True, text=True, check=False,
                                stdin=subprocess.DEVNULL)
    except OSError as error:
        return f"unreadable: {words[0]}: {error.strerror or error}"
    if result.returncode != 0:
        return f"unreadable: {' '.join(words)} exited {result.returncode}: " \
               f"{result.stderr.strip()[-200:]}"
    return result.stdout.strip()


def vmstat_value(text, name):
    if text.startswith("unreadable"):
        return text
    for line in text.splitlines():
        words = line.split()
        if len(words) == 2 and words[0] == name:
            return words[1]
    return f"unreadable: no {name} line"


def state_readings(root="/"):
    """perf_event_paranoid, the uptime in seconds and pswpout, before or after a step."""
    root = Path(root)
    uptime = read_text(root / "proc/uptime")
    return {"perf_event_paranoid": read_text(root / "proc/sys/kernel/perf_event_paranoid"),
            "uptime_s": uptime if uptime.startswith("unreadable") else uptime.split()[0],
            "pswpout": vmstat_value(read_text(root / "proc/vmstat"), "pswpout")}


def irq_time_accounting(config_text):
    if config_text.startswith("unreadable"):
        return "unreadable"
    for line in config_text.splitlines():
        if line.startswith("CONFIG_IRQ_TIME_ACCOUNTING="):
            return line.split("=", 1)[1]
        if line.strip() == "# CONFIG_IRQ_TIME_ACCOUNTING is not set":
            return "not set"
    return "not in the config"


def smoke_readings(cores, root="/", release=None):
    """Every reading the smoke step records, each as its text or `unreadable: <why>`."""
    root = Path(root)
    release = release if release is not None else command_text(["uname", "-r"])
    out = {"uname_r": release,
           "proc_version": read_text(root / "proc/version")}
    for name in ("io_uring_disabled", "io_uring_group", "nmi_watchdog", "perf_event_paranoid"):
        out[name] = read_text(root / f"proc/sys/kernel/{name}")
    for name in ("rmem_max", "rmem_default"):
        out[name] = read_text(root / f"proc/sys/net/core/{name}")
    out["ip_early_demux"] = read_text(root / "proc/sys/net/ipv4/ip_early_demux")
    out["ulimit_l"] = command_text(["bash", "-c", "ulimit -l"])
    folder = root / "sys/devices/system/cpu/vulnerabilities"
    try:
        out["vulnerabilities"] = {path.name: read_text(path) for path in sorted(folder.iterdir())}
    except OSError as error:
        out["vulnerabilities"] = f"unreadable: {error.strerror or error}"
    out["lo_mtu"] = read_text(root / "sys/class/net/lo/mtu")
    out["clocksource"] = read_text(
        root / "sys/devices/system/clocksource/clocksource0/current_clocksource")
    for role, core in cores.items():
        base = root / f"sys/devices/system/cpu/cpu{core}"
        out[f"governor_cpu{core}"] = read_text(base / "cpufreq/scaling_governor")
        out[f"siblings_cpu{core}"] = read_text(base / "topology/thread_siblings_list")
    config = "unreadable" if release.startswith("unreadable") else \
        read_text(root / f"boot/config-{release}")
    out["irq_time_accounting"] = irq_time_accounting(config)
    out["lscpu"] = command_text(["lscpu"])
    return out


# --- The processes -------------------------------------------------------------------------

def mode_cores(args, mode):
    return [args.core, args.receiver_core] + ([args.sq_core] if mode == SQ_MODE else [])


# --- The machine checks, one a core in the mask (r5-11) -------------------------------------

def mask_cores(step, modes, cores):
    """Every core a timed process of the step is pinned to: the sender's and the receiver's
    (the sender alone runs with the same mask), and U5's SQ thread's where the step runs U5."""
    found = [cores["sender"], cores["receiver"]]
    if step != "ceiling" and SQ_MODE in modes:
        found.append(cores["sq"])
    return found


def sibling_of(core, root="/"):
    """The core's SMT sibling, from sysfs; Refused if it cannot be read, since the check of
    a core is a check of its sibling's idleness too."""
    text = read_text(Path(root) / f"sys/devices/system/cpu/cpu{core}/topology/"
                                  "thread_siblings_list")
    listed = set()
    for part in text.split(","):
        ends = part.split("-")
        if not all(end.strip().isdigit() for end in ends) or len(ends) > 2:
            raise Refused("machine_check", f"cpu{core}'s SMT siblings read {text!r}")
        listed.update(range(int(ends[0]), int(ends[-1]) + 1))
    others = sorted(listed - {core})
    if len(others) != 1:
        raise Refused("machine_check", f"cpu{core}'s SMT siblings read {text!r}, not one other")
    return others[0]


def core_check_words(args, core, sibling):
    """machine_check.sh on one core of the mask, pinned there as measure.sh's child."""
    return [str(args.measure), "taskset", "-c", str(core), "bash",
            str(REPO_ROOT / "scripts" / "machine_check.sh"), "--input", str(args.input),
            "--probe", str(args.probe), "--core", str(core), "--sibling", str(sibling)]


def machine_checks(args, root="/"):
    """(every core's machine check output, in order, [[core, sibling], ...]): one check a
    core in the step's mask, each retried while measure.sh is busy; Refused at the first that
    fails."""
    outputs, covered = [], []
    for core in mask_cores(args.step, args.modes, {"sender": args.core,
                                                   "receiver": args.receiver_core,
                                                   "sq": args.sq_core}):
        sibling = sibling_of(core, root)
        while True:
            result = subprocess.run(core_check_words(args, core, sibling), capture_output=True,
                                    text=True, stdin=subprocess.DEVNULL, check=False)
            if result.returncode != 75:
                break
            time.sleep(RETRY_BUSY_S)
        if result.returncode != 0:
            lines = result.stdout.strip().splitlines()
            raise simd_driver.Refused("machine_check", f"core {core}: " + (
                lines[-1] if lines else
                f"exited {result.returncode}: {result.stderr.strip()[-300:]}"))
        outputs.append(result.stdout.strip())
        covered.append([core, sibling])
    return "\n".join(outputs), covered


def bench_words(args, mode, extra, stem, commit):
    """One timed process, whole: measure.sh, taskset's mask, bench_udp and its arguments."""
    mask = ",".join(str(core) for core in mode_cores(args, mode))
    words = [str(args.measure), "taskset", "-c", mask, str(args.bench), str(args.input),
             "--mode", mode, *extra, "--core", str(args.core)]
    if mode != "sender":
        words += ["--receiver-core", str(args.receiver_core)]
    if mode == SQ_MODE:
        words += ["--sq-core", str(args.sq_core)]
    return words + ["--commit", commit, "--dataset", args.dataset, "--dataset-sha256",
                    args.dataset_sha256, "--out", f"{stem}.json"]


def run_words(words, stem):
    """Runs one process to STEM.out and STEM.err, retried while measure.sh is busy."""
    while True:
        with open(f"{stem}.out", "w", encoding="utf-8") as out, \
                open(f"{stem}.err", "w", encoding="utf-8") as err:
            code = subprocess.run(words, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                  check=False).returncode
        if code != 75:
            return code
        time.sleep(RETRY_BUSY_S)


def outcome_of(stem, code):
    """(record, None) for a usable process, (None, why) for one that failed. Exit 1 with a
    record is a run that lost or differed: a result, not a failure."""
    err = Path(f"{stem}.err").read_text(encoding="utf-8", errors="replace")
    if code not in (0, 1):
        lines = [line for line in err.strip().splitlines() if not line.startswith("measure.sh")]
        return None, f"exited {code}" + (f": {lines[-1][-300:]}" if lines else "")
    if not Path(f"{stem}.json").is_file():
        return None, f"exited {code} with no record"
    if simd_driver.span_of(err) is None:
        return None, "measure.sh's start or end line is missing"
    try:
        return json.loads(Path(f"{stem}.json").read_text(encoding="utf-8")), None
    except ValueError as error:
        return None, f"the record does not parse: {error}"


class Run:
    """One step's run directory, manifest and processes."""

    def __init__(self, args, run_id, commit):
        self.args = args
        self.run_id = run_id
        self.commit = commit
        self.dir = run_record.run_folder(args.raw_dir, run_id)
        # The run's own record (gate item 8), beside the manifest.
        self.record = run_record.Record(self.dir, "udp_driver", run_id, commit)
        for path in (args.bench, args.probe, args.measure, args.heavy, args.loopback):
            if path is not None:
                self.record.binary(path)
        self.count = 0
        self.failed = 0
        self.rng = random.Random(args.seed)
        self.manifest = {
            "run_id": run_id, "prediction": args.prediction, "step": args.step,
            "commit": commit, "host_os": platform.system(), "binary": args.bench.name,
            "binary_sha256": simd_driver.sha256_of(args.bench), "dataset": args.dataset,
            "dataset_sha256": args.dataset_sha256,
            "cores": {"sender": args.core, "receiver": args.receiver_core,
                      "sq": args.sq_core},
            "modes": list(args.modes), "repetitions": args.repetitions, "seed": args.seed,
            "first_frame": args.first, "frames_cap": args.frames_cap,
            "trial_seconds": args.trial_seconds, "datagram": args.datagram,
            "processes": [], "started": bench_driver.utc_now()}

    def save(self):
        manifest = dict(self.manifest)
        if "warning" in manifest:
            manifest = {"warning": manifest.pop("warning"), **manifest}
        (self.dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                                encoding="utf-8")

    def shuffled(self, modes):
        modes = list(modes)
        self.rng.shuffle(modes)
        return modes

    def timed(self, mode, role, extra, repetition, target=None, speedup=None):
        """One bench_udp process: its record, or None with the failure on record."""
        self.count += 1
        stem = self.dir / f"{self.args.step}-{mode}-{self.count:04d}"
        words = bench_words(self.args, mode, extra, stem, self.commit)
        code = run_words(words, stem)
        record, why = outcome_of(stem, code)
        entry = {"stem": stem.name, "mode": mode, "role": role, "repetition": repetition,
                 "target_rate": target, "feed_speedup": speedup, "extra": extra, "exit": code,
                 "outcome": "result" if record is not None else "failed", "why": why}
        if record is not None and record.get("kind") == "receive":
            entry["lossless"] = lossless_of(record) and bool(record.get("lossless"))
            entry["verdict"] = record.get("verdict")
            if target is not None:
                ok, judged = trial_lossless(record, target)
                entry["trial_lossless"], entry["trial_why"] = ok, judged
        self.manifest["processes"].append(entry)
        self.save()
        if record is None:
            self.failed += 1
            bench_driver.append_log(
                self.args.run_log, f"{self.run_id}_{stem.name}", self.args.prediction,
                self.commit, f"failed: udp {self.args.step}, {mode} {role}, repetition "
                             f"{repetition}: {why}; nothing from it is published")
        return record

    def check_machine(self, when):
        """The day read through and checked, then the machine check and the readings."""
        digest = bench_driver.hash_and_fault_in(self.args.input)
        if digest != self.args.dataset_sha256:
            raise Refused("input", f"the day's sha256 is {digest}, not {self.args.dataset_sha256}")
        self.record.dataset(self.args.input, digest)
        try:
            self.manifest[f"machine_check_{when}"], covered = machine_checks(self.args)
            self.manifest[f"machine_check_cores_{when}"] = covered
            self.record.output(f"machine_check_{when}.txt", self.manifest[f"machine_check_{when}"])
        except simd_driver.Refused as error:
            if when == "before":
                raise Refused("machine_check", f"the check before failed: {error}") from error
            self.manifest[f"machine_check_{when}"] = f"failed: {error}"

    def read_state(self, when):
        readings = state_readings()
        self.manifest[f"readings_{when}"] = readings
        if readings["perf_event_paranoid"] == "3":
            self.manifest["warning"] = (f"perf_event_paranoid read 3 {when} the step: perf "
                                        "cannot profile even user space; the user approved 2")


# --- The steps -------------------------------------------------------------------------------

def slice_args(args, frames, first=None):
    return ["--first", str(args.first if first is None else first), "--frames", str(frames),
            "--datagram", str(args.datagram)]


def smoke_words(args, mode):
    """One functional itch_loopback run of the smoke step, under heavy.sh, paced."""
    words = [str(args.heavy), str(args.loopback), str(args.input), "--mode", mode,
             "--first", str(args.first), "--frames", str(args.smoke_frames),
             "--datagram", str(args.datagram), "--rate", str(args.smoke_rate),
             "--core", str(args.core), "--receiver-core", str(args.receiver_core)]
    if mode == SQ_MODE:
        words += ["--sq-core", str(args.sq_core)]
    return words


def step_smoke(run):
    args = run.args
    if args.heavy is None or args.loopback is None:
        raise Refused("usage", "--step smoke needs --heavy and --loopback")
    cores = {"sender": args.core, "receiver": args.receiver_core, "sq": args.sq_core}
    smoke = {"readings": smoke_readings(cores)}
    probe = command_text([str(args.bench), "--probe", "--sq-core", str(args.sq_core)])
    smoke["probe"] = probe.splitlines()
    smoke["loopback"] = {}
    for mode in args.modes:
        stem = run.dir / f"smoke-loopback-{mode}"
        words = smoke_words(args, mode)
        with open(f"{stem}.out", "w", encoding="utf-8") as out, \
                open(f"{stem}.err", "w", encoding="utf-8") as err:
            code = subprocess.run(words, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                  check=False).returncode
        verdict = VERDICT_LINE.search(Path(f"{stem}.out").read_text(encoding="utf-8",
                                                                     errors="replace"))
        smoke["loopback"][mode] = {"exit": code, "verdict": verdict[1] if verdict else None,
                                   "stem": stem.name}
        if code != 0 or verdict is None or verdict[1] != "PASS":
            run.failed += 1
            bench_driver.append_log(
                args.run_log, f"{run.run_id}_{stem.name}", args.prediction, run.commit,
                f"failed: udp smoke, itch_loopback --mode {mode} exited {code}, verdict "
                f"{verdict[1] if verdict else 'none'}")
    (run.dir / "smoke.json").write_text(json.dumps(smoke, indent=2) + "\n", encoding="utf-8")
    lines = []
    for name, value in smoke["readings"].items():
        if isinstance(value, dict):
            lines += [f"{name}/{key}: {text}" for key, text in value.items()]
        else:
            lines += [f"{name}: {line}" for line in str(value).splitlines() or [""]]
    lines += [f"probe: {line}" for line in smoke["probe"]]
    lines += [f"loopback {mode}: exit {one['exit']}, verdict {one['verdict']}"
              for mode, one in smoke["loopback"].items()]
    (run.dir / "smoke.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    run.manifest["smoke"] = {"json": "smoke.json", "text": "smoke.txt",
                             "loopback": smoke["loopback"]}


def refuse_unpassed_sender(run, record, repetition):
    """r5-14: the ceiling is the median of every sender run, so a run that failed or did not
    PASS refuses the step, with why in the run log's row, and is never left out of it."""
    if record is None:
        entry = run.manifest["processes"][-1]
        raise Refused("ceiling", f"sender run {repetition} ({entry['stem']}) failed: "
                                 f"{entry['why']}; the ceiling is the median of every run, so "
                                 "the step is refused, not taken from the rest")
    if record.get("verdict") != "PASS":
        stem = run.manifest["processes"][-1]["stem"]
        raise Refused("ceiling", f"sender run {repetition} ({stem}) gave the verdict "
                                 f"{record.get('verdict')!r}, not PASS; the ceiling is the "
                                 "median of every run, so the step is refused, not taken from "
                                 "the rest")


def step_ceiling(run):
    args = run.args
    achieved, packets = [], []
    for repetition in range(1, args.repetitions + 1):
        record = run.timed("sender", "ceiling", slice_args(args, args.frames_cap), repetition)
        refuse_unpassed_sender(run, record, repetition)
        achieved.append(record["send"]["achieved_messages_per_s"])
        packets.append(record["send"]["achieved_packets_per_s"])
    run.manifest["ceiling"] = {"messages_per_s": ceiling_of(achieved),
                               "packets_per_s": ceiling_of(packets), "runs": len(achieved),
                               "source": f"run {run.run_id}, the median of {len(achieved)}"}
    run.save()


def given_ceiling(args):
    if args.ceiling is not None and args.ceiling_from is not None:
        raise Refused("usage", "give --ceiling or --ceiling-from, not both")
    if args.ceiling is not None:
        return {"messages_per_s": args.ceiling, "source": "given on the command line"}
    if args.ceiling_from is None:
        raise Refused("usage", "--step bisect needs --ceiling or --ceiling-from")
    manifest = json.loads((args.ceiling_from / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("step") != "ceiling" or "ceiling" not in manifest:
        raise Refused("ceiling", f"{args.ceiling_from.name} is not a finished ceiling run")
    return dict(manifest["ceiling"], source=f"run {manifest['run_id']}")


def step_bisect(run):
    try:
        search_and_confirm(run)
    finally:
        run.save()


def search_and_confirm(run):
    args = run.args
    ceiling = given_ceiling(args)
    cap = cap_of(ceiling["messages_per_s"])
    run.manifest["ceiling"] = ceiling
    run.manifest["rate_cap"] = cap
    searches = {mode: Bisection(cap, args.start_rate, args.floor_rate, args.most_steps)
                for mode in args.modes}
    round_number = 0
    while any(search.next_rate() is not None for search in searches.values()):
        round_number += 1
        for mode in run.shuffled(m for m, s in searches.items() if s.next_rate() is not None):
            rate = searches[mode].next_rate()
            frames = frames_for(rate, args.trial_seconds, args.frames_cap)
            record = run.timed(mode, "trial", ["--rate", str(rate), *slice_args(args, frames)],
                               round_number, target=rate)
            if record is None:
                searches[mode].fail(run.manifest["processes"][-1]["why"])
                continue
            searches[mode].record(rate, trial_lossless(record, rate)[0])
        run.manifest["bisect"] = {mode: search.result() for mode, search in searches.items()}
        run.save()
    results = run.manifest["bisect"]
    best = best_mode(results)
    run.manifest["best_mode"] = best
    if best is None:
        run.manifest["t_uring_2"] = {"tested": False, "why": "no mode was lossless at any rate"}
        return
    if args.datagram != TURING2_DATAGRAM:
        run.manifest["t_uring_2"] = {
            "tested": False, "why": f"T-URING-2 is judged at {TURING2_DATAGRAM:,} bytes a "
                                    f"datagram, and this bisection ran at {args.datagram:,}"}
        return
    if TARGET_1MS > cap:
        run.manifest["t_uring_2"] = {
            "tested": False, "why": f"{TARGET_1MS:,} msg/s is above the cap {cap:,}, 0.9 x "
                                    f"the sender's ceiling {ceiling['messages_per_s']:,}"}
        return
    frames = frames_for(TARGET_1MS, args.trial_seconds, args.frames_cap)
    lossless = 0
    for repetition in range(1, args.repetitions + 1):
        record = run.timed(best, "confirm", ["--rate", str(TARGET_1MS),
                                             *slice_args(args, frames)],
                           repetition, target=TARGET_1MS)
        lossless += record is not None and trial_lossless(record, TARGET_1MS)[0]
    run.manifest["t_uring_2"] = {"tested": True, "mode": best, "rate": TARGET_1MS,
                                 "lossless": lossless, "repetitions": args.repetitions}


def heavy_text(args, words):
    """A whole-day job that is not timed, under heavy.sh, its stdout."""
    result = subprocess.run([str(args.heavy), *words], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        raise Refused("peaks", f"{Path(words[0]).name} exited {result.returncode}: "
                               f"{result.stderr.strip()[-300:]}")
    return result.stdout


def step_feed(run):
    args = run.args
    if args.heavy is None:
        raise Refused("usage", "--step feed needs --heavy")
    day = parse_peaks(heavy_text(args, [str(args.bench), str(args.input), "--peaks"]))
    first = max(0, day["1s"][1] - args.feed_lead_frames)
    frames = args.frames_cap
    window = parse_peaks(heavy_text(args, [str(args.bench), str(args.input), "--peaks",
                                           "--first", str(first), "--frames", str(frames)]))
    span, longest = window_gaps(args.input, first, frames)
    schedules = {}
    for role, target in FEED_SCHEDULES:
        speedup = 1.0 if target is None else \
            speedup_for(target, window[role.split("-")[1]][0], PEAK_SECONDS[role])
        schedules[role] = {"target": target, "speedup": speedup,
                           "flush_gap_ns": flush_gap_ns(args.flush_us, speedup),
                           "timeout_ms": timeout_ms(longest, speedup)}
    run.manifest["feed"] = {
        "day_peaks": {label: list(value) for label, value in day.items()},
        "window": {"first": first, "frames": frames, "lead_frames": args.feed_lead_frames,
                   "span_ns": span, "longest_gap_ns": longest},
        "window_peaks": {label: list(value) for label, value in window.items()},
        "flush_us": args.flush_us, "schedules": schedules}
    run.save()
    for repetition in range(1, args.repetitions + 1):
        for mode in run.shuffled(args.modes):
            for role, _ in FEED_SCHEDULES:
                one = schedules[role]
                extra = ["--feed-speedup", repr(one["speedup"]), *slice_args(args, frames, first),
                         "--flush-gap-ns", str(one["flush_gap_ns"]),
                         "--timeout-ms", str(one["timeout_ms"])]
                run.timed(mode, role, extra, repetition, speedup=one["speedup"])


def step_latency(run):
    args = run.args
    if args.bisect_from is None:
        raise Refused("usage", "--step latency needs --bisect-from")
    source = args.bisect_from
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("step") != "bisect" or "bisect" not in manifest:
        raise Refused("latency", f"{source.name} is not a finished bisect run")
    cap = manifest["rate_cap"]
    records = [json.loads((source / f"{entry['stem']}.json").read_text(encoding="utf-8"))
               for entry in manifest["processes"] if entry["outcome"] == "result"]
    per_packet = messages_per_packet(records)
    results = {mode: manifest["bisect"][mode] for mode in args.modes
               if mode in manifest["bisect"]}
    missing = [mode for mode in args.modes if mode not in results]
    if missing:
        raise Refused("latency", f"{source.name} has no bisection for {missing}")
    rates = latency_rates(results, args.latency_fractions, args.low_packets_per_s, per_packet,
                          args.cpu_rate, cap)
    run.manifest["ceiling"] = manifest["ceiling"]
    run.manifest["rate_cap"] = cap
    run.manifest["latency"] = {"bisect_run": manifest["run_id"],
                               "messages_per_packet": per_packet,
                               "low_packets_per_s": args.low_packets_per_s,
                               "fractions": args.latency_fractions, "cpu_rate": args.cpu_rate,
                               "rates": rates}
    run.save()
    for repetition in range(1, args.repetitions + 1):
        for mode in run.shuffled(args.modes):
            for role, rate in rates[mode]:
                frames = frames_for(rate, args.trial_seconds, args.frames_cap)
                run.timed(mode, role, ["--rate", str(rate), *slice_args(args, frames)],
                          repetition, target=rate)


STEP_BODIES = {"smoke": step_smoke, "ceiling": step_ceiling, "bisect": step_bisect,
               "feed": step_feed, "latency": step_latency}


def run_step(args, run_id, commit):
    run = Run(args, run_id, commit)
    timed = args.step != "smoke"
    run.read_state("before")
    try:
        if timed:
            run.check_machine("before")
        run.save()
        STEP_BODIES[args.step](run)
    finally:
        run.manifest["finished"] = bench_driver.utc_now()
        if timed and "machine_check_before" in run.manifest:
            try:
                run.check_machine("after")
            except (Refused, simd_driver.Refused, OSError) as error:
                run.manifest["machine_check_after"] = f"failed: {error}"
        run.read_state("after")
        run.save()
    if timed and not str(run.manifest.get("machine_check_after", "")).endswith(
            "machine_check: passed"):
        raise Refused("machine_check", "the machine check after the step did not pass")
    return run


def fractions(text):
    values = [float(word) for word in text.split(",")]
    if not values or any(not 0 < value < 1 for value in values):
        raise argparse.ArgumentTypeError("each fraction must be between 0 and 1")
    return values


def mode_list(text):
    modes = text.split(",")
    if not modes or any(mode not in MODES for mode in modes) or len(set(modes)) != len(modes):
        raise argparse.ArgumentTypeError(f"modes are among {', '.join(MODES)}, each once")
    return modes


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--step", required=True, choices=STEPS)
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--dataset-sha256", required=True)
    parser.add_argument("--bench", required=True, type=Path)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--measure", required=True, type=Path)
    parser.add_argument("--heavy", type=Path)
    parser.add_argument("--loopback", type=Path)
    parser.add_argument("--modes", type=mode_list, default=list(MODES))
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--core", type=int, default=4)
    parser.add_argument("--receiver-core", type=int, default=6)
    parser.add_argument("--sq-core", type=int, default=2)
    parser.add_argument("--first", type=int, default=0)
    parser.add_argument("--frames-cap", type=int, default=50_000_000)
    parser.add_argument("--trial-seconds", type=float, default=60.0)
    parser.add_argument("--datagram", type=int, default=1400)
    parser.add_argument("--smoke-frames", type=int, default=1_000_000)
    parser.add_argument("--smoke-rate", type=int, default=1_000_000)
    parser.add_argument("--ceiling", type=int)
    parser.add_argument("--ceiling-from", type=Path)
    parser.add_argument("--start-rate", type=int, default=100_000)
    parser.add_argument("--floor-rate", type=int, default=1_000)
    parser.add_argument("--most-steps", type=int, default=10)
    parser.add_argument("--feed-lead-frames", type=int, default=1_000_000)
    parser.add_argument("--flush-us", type=float, default=100.0)
    parser.add_argument("--bisect-from", type=Path)
    parser.add_argument("--latency-fractions", type=fractions, default=[0.1, 0.5, 0.8])
    parser.add_argument("--low-packets-per-s", type=int, default=1000)
    parser.add_argument("--cpu-rate", type=int)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--run-log", type=Path, default=REPO_ROOT / "results" / "run_log.md")
    args = parser.parse_args(argv)
    if len({args.core, args.receiver_core, args.sq_core}) != 3:
        parser.error("the sender, receiver and SQ thread need three different cores")
    if args.repetitions < 1 or args.frames_cap < 1 or args.trial_seconds <= 0:
        parser.error("--repetitions, --frames-cap and --trial-seconds must be positive")
    if args.seed is None:
        args.seed = random.SystemRandom().randrange(2**32)
    return args


def main(argv=None):
    args = parse_args(argv)
    if platform.system() != "Linux":
        print("udp_driver: refused: no Mac loopback figure is a result; run on debian",
              file=sys.stderr)
        return 2
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{args.prediction}-{stamp}"
    try:
        commit = bench_driver.git_commit(REPO_ROOT, args.allow_dirty)
        checker = REPO_ROOT / "scripts" / "check_predictions.py"
        # Its lines go to stderr: stdout is the run id alone, for a launcher to read.
        checked = subprocess.run([sys.executable, "-B", str(checker), "--require",
                                  args.prediction], capture_output=True, text=True, check=False)
        print((checked.stdout + checked.stderr).strip(), file=sys.stderr)
        if checked.returncode != 0:
            raise Refused("prediction", f"{args.prediction} is not on record")
        run = run_step(args, run_id, commit)
    except (Refused, run_record.Incomplete, OSError, ValueError, KeyError) as error:
        bench_driver.append_log(args.run_log, run_id, args.prediction,
                                locals().get("commit", "unknown"),
                                f"refused: udp {args.step}: {error}")
        print(f"udp_driver: refused: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # Last, so that no run ends without its row.
        return bench_driver.log_failure("udp_driver", args.run_log, run_id, args.prediction,
                                        locals().get("commit", "unknown"), error,
                                        f"udp {args.step}: ")
    outcome = (f"recorded: udp {args.step}, {run.count} processes"
               + (", each under measure.sh taskset, the machine checks before and after in "
                  "the manifest" if args.step != "smoke" else ", no figure")
               + (f", {run.failed} failed" if run.failed else "")
               + "; for scripts/udp_report.py")
    bench_driver.append_log(args.run_log, run_id, args.prediction, commit, outcome)
    print(run_id)
    return 1 if run.failed else 0


if __name__ == "__main__":
    sys.exit(main())
