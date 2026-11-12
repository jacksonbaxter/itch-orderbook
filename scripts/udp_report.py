#!/usr/bin/env python3
"""Writes a results file of the UDP replay's comparison (T-URING-1, T-URING-2) from runs of
scripts/udp_driver.py.

    udp_report.py --run-dir DIR [--run-dir DIR ...] --out results/udp_STEP[_CAPb]_debian_DAY.md
        [--run-log FILE]
    udp_report.py --check FILE

Each DIR is a driver run directory: manifest.json, and each process's STEM.json record and
the STEM.err measure.sh wrote around it. STEP, from the file name, is one of the timed steps
given (ceiling, bisect, feed or latency); the others give the file what it needs beside them:
a ceiling run its sender rows, a smoke run its readings for the header. Every process with a
record is a row of the table of every process, and every one without is listed with the
reason. Only debian files are written: no Mac loopback figure is a result.

The packet size is part of the name. T-URING-2 is judged at 1,400 bytes a datagram (P-0902),
and a file at that size has no _CAPb; a file at any other size, such as T-URING-1's
small-packet arm at 72 bytes, names it, udp_bisect_72b_debian_DAY.md, so that the two sizes
never share a file. Only a bisection at 1,400 bytes carries T-URING-2's verdict; one at
another size says where the verdict is instead, and a confirmation run in it is refused.

Every figure comes from the file's own table of every process, so --check rebuilds the
sender's ceiling, each mode's largest lossless rate, T-URING-1's ratios, T-URING-2's
verdict, the feed's pass and fail, the latency medians, the CPU split and the summary from
that table, and fails on any difference. It also fails on a file without the sentence

    Loopback runs the receive softirq on the sender's CPU, so this is a syscall-and-copy
    benchmark on one host, not a network benchmark (IU-47).

word for word, on one line; on a host label that does not match the file name; on a row
whose sender or receiver was not pinned; on a governor other than performance; on a machine
check missing or failed before or after any run; on a row whose lossless cell is not what
its counters say; on a rate-paced row above the cap, 0.9 x the sender's ceiling, or a cap
that is not 0.9 x the ceiling shown; on a feed speedup that is not the target over the
window's peak; on a T-URING-2 verdict mark missing, doubled, in a file with no bisection or
in one at a packet size other than 1,400 bytes; on a name whose packet size is not the
header's; on a load line that is not measure.sh's; and on anything private.

How a trial counts, as the driver counts it: lossless, verdict PASS and the sender kept pace,
achieving at least 0.99 x the target. The largest lossless rate is the largest such trial's
rate; when that is the cap, the mode is "at least" the cap, sender limited.

The header's prediction and run_id are the file's own step's run, as check_predictions.py
reads them; predictions and runs list every run the file draws on.

Each violation prints as `udp_report: FAIL <code>: <detail>`.

Exit codes: 0 written, or --check found nothing, 1 --check found something, 2 bad usage or
unusable input.
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
import results_table  # noqa: E402
import stamp_load  # noqa: E402
import udp_driver  # noqa: E402

DEBIAN = "debian"
COMPARABILITY = ("Loopback runs the receive softirq on the sender's CPU, so this is a "
                 "syscall-and-copy benchmark on one host, not a network benchmark (IU-47).")
FILE_NAME = re.compile(r"^udp_(ceiling|bisect|feed|latency)(?:_(\d+)b)?_(debian|mac)_(\d{8})"
                       r"\.md$")
CAP_FIELD = re.compile(r"^([0-9,]+) bytes a datagram")
TIMED_STEPS = ("ceiling", "bisect", "feed", "latency")
DESTINATION = "127.0.0.1, unicast, both sockets connect()ed"
CONNECTED = "loopback, unicast, both sockets connect()ed"
NONE = "–"
MODES = udp_driver.MODES
TARGET_1S = udp_driver.TARGET_1S
TARGET_1MS = udp_driver.TARGET_1MS
TURING2_DATAGRAM = udp_driver.TURING2_DATAGRAM
TITLES = {"ceiling": "the sender's ceiling",
          "bisect": "each receive mode's largest lossless rate",
          "feed": "the feed-shaped replay",
          "latency": "latency and the CPU split at fixed rates"}
FEED_HEADS = {"feed-x1": "x1, real time",
              "feed-1s": f"busiest 1 s at {TARGET_1S:,} msg/s",
              "feed-1ms": f"busiest 1 ms at {TARGET_1MS:,} msg/s"}
SPIN_NOTE = {"S4": "the receiver spins: 100% by construction",
             "U4": "the receiver spins: 100% by construction",
             "U5": "the receiver and the SQ thread spin: 100% by construction"}

# The table of every process: (head, key, kind). An int is written with separators, a time
# in µs to two places from ns, a share of CPU in % to one place, a flag yes or no, and a
# value the record does not have as a dash.
COLUMNS = (
    ("Run", "run", "text"), ("Mode", "mode", "text"), ("Role", "role", "text"),
    ("Schedule", "schedule", "text"), ("Target msgs/s", "target", "int"),
    ("Messages", "messages", "int"), ("Achieved msgs/s", "achieved", "int"),
    ("Achieved pkts/s", "packets", "int"), ("Late max µs", "late_max", "us"),
    ("Delivered", "delivered", "flag"), ("Failed sends", "failed_sends", "int"),
    ("Gaps", "tracker_gaps", "int"), ("Lost", "tracker_lost", "int"),
    ("Duplicates", "tracker_duplicates", "int"), ("Truncated", "truncated", "int"),
    ("ENOBUFS", "no_buffers", "int"), ("CQ overflow", "cq_overflow", "int"),
    ("RXQ overflow", "rxq_overflow", "int"), ("Socket drops", "socket_drops", "int"),
    ("RcvbufErrors", "rcvbuf_errors", "int"), ("InErrors", "in_errors", "int"),
    ("Softnet drops", "softnet_dropped", "int"), ("Rearms", "rearms", "int"),
    ("Lossless", "lossless", "flag"), ("p50 µs", "p50", "us"), ("p99 µs", "p99", "us"),
    ("p99.9 µs", "p99_9", "us"), ("max µs", "max", "us"), ("Sender %", "cpu_sender", "pct"),
    ("Receiver %", "cpu_receiver", "pct"), ("SQ thread %", "cpu_sq", "pct"),
    ("Softirq, sender's CPU %", "softirq_sender", "pct"),
    ("Softirq, receiver's CPU %", "softirq_receiver", "pct"), ("Pins", "pins", "text"),
    ("Verdict", "verdict", "text"))
FAILED_COLUMNS = (("Run", "run"), ("Mode", "mode"), ("Role", "role"), ("Exit", "exit"),
                  ("Why", "why"))
BISECTION = ("Each mode's largest lossless rate by bisection on the message rate, one trial a "
             "process, the modes taken in a shuffled order each round. A trial runs min(rate × "
             "the trial's seconds, the frame cap) messages; it counts as lossless only when the "
             "record is lossless on every counter of IU-43, its verdict is PASS and the sender "
             "kept pace, achieving at least 0.99 × the target. From the start rate, halved while "
             "lossy; then the cap; then geometric bisection until high over low is at most 1.02 "
             "or ten midpoints.")
METHOD = {
    "ceiling": "The sender alone, unpaced, down a pair whose receiving socket nobody reads "
               "(`bench_udp --mode sender`): the comparison plan's task 2. No trial runs above "
               "0.9 × its median.",
    "bisect": BISECTION + f" Then the best mode at {TARGET_1MS:,} msg/s, if that is under "
                          "the cap.",
    "feed": "A window of the day around its busiest second, replayed by its ITCH timestamps "
            "three ways: in real time; sped up so its own busiest 1 s runs at "
            f"{TARGET_1S:,} msg/s; and so its own busiest 1 ms runs at {TARGET_1MS:,} msg/s, "
            "the speedup being the target over the peak rate. A packet closes early after the "
            "same replay time at every speed. Pass is lossless.",
    "latency": "Fixed rates per mode: a low rate of packets a second, as messages by the "
               "bisection's messages a packet, where batching does nothing and wake-up "
               "dominates; fractions of the mode's largest lossless rate; and one common rate "
               "for every mode, whose processes give the CPU split. Latency runs from each "
               "packet's due time on the schedule to the receiver's clock read when the wait "
               "that returned it returned, so a sender that falls behind is charged (IU-46); "
               "the first second's packets are left out.",
}


class ReportError(Exception):
    """The input cannot become a results file."""


# --- Packet sizes --------------------------------------------------------------------------

def file_name(step, datagram, host, day):
    """A results file's name: no _CAPb at T-URING-2's 1,400 bytes a datagram, the size named
    at any other, so that the two sizes never share a file."""
    tag = "" if datagram == TURING2_DATAGRAM else f"_{datagram}b"
    return f"udp_{step}{tag}_{host}_{day}.md"


def datagram_of(fields):
    """The packet size the header gives, in bytes a datagram, or None."""
    match = CAP_FIELD.match(fields.get("packet_size_cap", ""))
    return int(match.group(1).replace(",", "")) if match else None


def method_of(step, datagram):
    """What a step ran, in words. T-URING-2 is judged at 1,400 bytes a datagram alone, so a
    bisection at another size ran no confirmation."""
    if step == "bisect" and datagram != TURING2_DATAGRAM:
        return (BISECTION + f" No run at {TARGET_1MS:,} msg/s: T-URING-2 is judged at "
                            f"{TURING2_DATAGRAM:,} bytes a datagram.")
    return METHOD[step]


# --- Rows ----------------------------------------------------------------------------------

def cell(kind, value):
    if value is None:
        return NONE
    if kind == "int":
        return f"{value:,}"
    if kind == "us":
        return f"{value:,.2f}"
    if kind == "pct":
        return f"{value:.1f}"
    if kind == "flag":
        return "yes" if value else "no"
    return str(value)


def uncell(kind, text):
    if text == NONE:
        return None
    if kind == "int":
        return int(text.replace(",", ""))
    if kind in ("us", "pct"):
        return float(text.replace(",", ""))
    if kind == "flag":
        if text not in ("yes", "no"):
            raise ValueError(f"a flag of {text!r}")
        return text == "yes"
    return text


def share(part_ns, window_ns):
    return None if part_ns is None or not window_ns else 100.0 * part_ns / window_ns


def thread_ns(times):
    if not times or times.get("user_us") is None or times.get("system_us") is None:
        return None
    return (times["user_us"] + times["system_us"]) * 1000


def cpu_cells(record, cores):
    """Each thread's and each CPU's softirq share of its window, in %."""
    cpu = record.get("cpu") or {}
    sender = share(thread_ns(cpu.get("sender")), cpu.get("sender_window_ns"))
    if record["kind"] != "receive":
        return {"cpu_sender": sender, "cpu_receiver": None, "cpu_sq": None,
                "softirq_sender": None, "softirq_receiver": None}
    window = cpu.get("window_ns")
    kernel = cpu.get("kernel") or {}
    tick_ns = 1e9 / kernel["ticks_per_second"] if kernel.get("ticks_per_second") else None
    softirq = kernel.get("softirq") or {}

    def ticks_share(ticks):
        return None if ticks is None or tick_ns is None else share(ticks * tick_ns, window)

    sq = None
    if record.get("mode") == udp_driver.SQ_MODE:
        ticks = [one["utime"] + one["stime"] for one in (kernel.get("threads") or {}).values()
                 if one.get("comm", "").startswith("iou-sqp")]
        sq = ticks_share(sum(ticks)) if ticks else None
    return {"cpu_sender": sender,
            "cpu_receiver": share(thread_ns(cpu.get("receiver")), window), "cpu_sq": sq,
            "softirq_sender": ticks_share(softirq.get(f"cpu{cores['sender']}")),
            "softirq_receiver": ticks_share(softirq.get(f"cpu{cores['receiver']}"))}


def pins_cell(record):
    pins = record.get("pins") or {}
    words = []
    for role in ("sender", "receiver"):
        pin = pins.get(role)
        if pin is None:
            continue
        words.append(f"{role} {pin['requested_core']}" if pin.get("pinned") else
                     f"{role} not pinned")
    if record.get("mode") == udp_driver.SQ_MODE:
        threads = ((record.get("cpu") or {}).get("kernel") or {}).get("threads") or {}
        ran = sorted({one["processor"] for one in threads.values()
                      if one.get("comm", "").startswith("iou-sqp")})
        words.append(f"SQ thread asked for {pins.get('sq_core')}, last on "
                     + ("/".join(str(core) for core in ran) if ran else "unseen"))
    return ", ".join(words) if words else "none recorded"


def schedule_cell(record):
    schedule = record.get("schedule")
    if schedule is None:
        return "rate" if record["send"]["paced"] else "unpaced"
    if schedule["kind"] == "feed":
        return f"feed ×{schedule['feed_speedup']:.3f}"
    return schedule["kind"]


def row_of(entry, record, cores):
    """One process as the table shows it, from its manifest entry and its record."""
    send = record["send"]
    drops = record.get("drops") or {}
    receive = record["kind"] == "receive"
    latency = record.get("latency") or {}
    row = {"run": entry["stem"], "mode": record.get("mode", "sender"), "role": entry["role"],
           "schedule": schedule_cell(record), "target": entry.get("target_rate"),
           "messages": record["plan"]["messages"],
           "achieved": round(send["achieved_messages_per_s"]),
           "packets": round(send["achieved_packets_per_s"]),
           "late_max": send["late_max_ns"] / 1000,
           "delivered": record.get("delivered") if receive else None,
           "rearms": record["receive"]["mode"]["rearms"] if receive else None,
           "lossless": record.get("lossless") if receive else None,
           "pins": pins_cell(record), "verdict": record["verdict"]}
    for name in (*udp_driver.LOSS_COUNTERS, *udp_driver.OPTIONAL_COUNTERS):
        row[name] = drops.get(name) if receive else None
    for key, field in (("p50", "p50_ns"), ("p99", "p99_ns"), ("p99_9", "p99_9_ns"),
                       ("max", "max_ns")):
        row[key] = latency[field] / 1000 if field in latency else None
    row.update(cpu_cells(record, cores))
    return row


def row_cells(row):
    return [cell(kind, row[key]) for _, key, kind in COLUMNS]


def runs_table(rows):
    out = ["| " + " | ".join(head for head, _, _ in COLUMNS) + " |",
           "|" + "---|" * len(COLUMNS)]
    out += ["| " + " | ".join(row_cells(row)) + " |" for row in rows]
    return out


def parse_runs_table(text):
    rows = []
    for cells in check_results.table_rows(text, "Every process"):
        if cells[0] == "Run":
            continue
        if len(cells) != len(COLUMNS):
            raise ReportError(f"a row of {len(cells)} cells, wanted {len(COLUMNS)}")
        rows.append({key: uncell(kind, text) for (_, key, kind), text in zip(COLUMNS, cells)})
    return rows


def failed_table(failed):
    out = ["| " + " | ".join(head for head, _ in FAILED_COLUMNS) + " |",
           "|" + "---|" * len(FAILED_COLUMNS)]
    out += ["| " + " | ".join(str(one[key]).replace("|", "/") for _, key in FAILED_COLUMNS)
            + " |" for one in failed]
    return out


def parse_failed_table(text):
    return [dict(zip((key for _, key in FAILED_COLUMNS), cells))
            for cells in check_results.table_rows(text, "Processes with no usable record")
            if cells[0] != "Run" and len(cells) == len(FAILED_COLUMNS)]


# --- What the rows say -------------------------------------------------------------------

def row_lossless(row):
    """Lossless from the row's own cells: delivered, and zero on every counter."""
    if not row["delivered"]:
        return False
    if any(row[name] != 0 for name in udp_driver.LOSS_COUNTERS):
        return False
    return all(row[name] in (0, None) for name in udp_driver.OPTIONAL_COUNTERS)


def valid_lossless(row):
    """A trial as the driver counts it: lossless, PASS, the sender kept pace."""
    return bool(row["lossless"]) and row["verdict"] == "PASS" and row["target"] is not None \
        and row["achieved"] >= udp_driver.PACE * row["target"]


def by_mode(rows, role):
    out = {}
    for row in rows:
        if row["role"] == role:
            out.setdefault(row["mode"], []).append(row)
    return {mode: out[mode] for mode in MODES if mode in out}


def largest_lossless(rows, cap):
    """{mode: {result, rate, above, trials}} from the trial rows."""
    out = {}
    for mode, trials in by_mode(rows, "trial").items():
        good = [row["target"] for row in trials if valid_lossless(row)]
        bad = [row["target"] for row in trials if not valid_lossless(row)]
        if not good:
            out[mode] = {"result": "below", "rate": min(bad), "above": min(bad),
                         "trials": len(trials)}
            continue
        best = max(good)
        above = [rate for rate in bad if rate > best]
        out[mode] = {"result": "sender limited" if best >= cap else "max", "rate": best,
                     "above": min(above) if above else None, "trials": len(trials)}
    return out


def rate_text(one):
    if one["result"] == "sender limited":
        return f"at least {one['rate']:,}, sender limited"
    if one["result"] == "below":
        return f"below {one['rate']:,}"
    return f"{one['rate']:,}"


def rate_words(one):
    if one["result"] == "sender limited":
        return f"at least {one['rate']:,} msg/s, sender limited"
    return f"{rate_text(one)} msg/s"


def median_or_none(values):
    values = [value for value in values if value is not None]
    return statistics.median(values) if values else None


def ceiling_lines(rows):
    senders = [row for row in rows if row["role"] == "ceiling"]
    if not senders:
        return None, None
    messages = [row["achieved"] for row in senders]
    packets = [row["packets"] for row in senders]
    lines = ["| | Median | Min | Max | Runs |", "|---|---|---|---|---|",
             f"| Messages/s | {udp_driver.ceiling_of(messages):,} | {min(messages):,} "
             f"| {max(messages):,} | {len(senders)} |",
             f"| Packets/s | {udp_driver.ceiling_of(packets):,} | {min(packets):,} "
             f"| {max(packets):,} | {len(senders)} |", "",
             f"- The cap: {udp_driver.cap_of(udp_driver.ceiling_of(messages)):,} msg/s, 0.9 × "
             "the median. No trial runs above it."]
    return lines, udp_driver.ceiling_of(messages)


def lossless_lines(found):
    lines = ["| Mode | Largest lossless msgs/s | Lowest lossy above it | Trials |",
             "|---|---|---|---|"]
    for mode, one in found.items():
        above = f"{one['above']:,}" if one["above"] is not None and one["result"] == "max" \
            else NONE
        lines.append(f"| {mode} | {rate_text(one)} | {above} | {one['trials']} |")
    return lines


def ratio_text(one, base):
    if one["result"] == "below" or base["result"] == "below":
        return NONE
    if one["result"] == "sender limited" and base["result"] == "sender limited":
        return "both at the cap"
    ratio = f"{one['rate'] / base['rate']:.3f}"
    if one["result"] == "sender limited":
        return f"≥ {ratio}"
    if base["result"] == "sender limited":
        return f"≤ {ratio}"
    return ratio


def turing1_lines(found, packets):
    lines = ["Target: `UNCONFIRMED`. No published head-to-head of io_uring and `recvmmsg` for "
             "UDP receive was found, so there is no bar and no verdict: this says which mode "
             f"won in this regime, {packets}, rate-paced, on loopback.", "",
             "| Mode | Largest lossless msgs/s | Over S3 |", "|---|---|---|"]
    base = found.get("S3")
    for mode, one in found.items():
        lines.append(f"| {mode} | {rate_text(one)} | "
                     f"{ratio_text(one, base) if base else NONE} |")
    lines.append("")
    if base and "U2" in found:
        lines.append(f"- U2 over S3, the headline pair: {ratio_text(found['U2'], base)}.")
    else:
        lines.append("- U2 over S3, the headline pair: not both run.")
    ranked = [(one["rate"], mode) for mode, one in found.items()
              if one["result"] in ("max", "sender limited")]
    if not ranked:
        lines.append("- No mode was lossless at any rate tried, so none won.")
        return lines
    top = max(rate for rate, _ in ranked)
    winners = [mode for rate, mode in ranked if rate == top]
    if len(winners) == 1:
        lines.append(f"- Won: {winners[0]}, lossless up to {rate_words(found[winners[0]])}.")
    elif found[winners[0]]["result"] == "sender limited":
        lines.append(f"- No winner: {', '.join(winners)} each reached the cap, {top:,} msg/s, "
                     "so the sender, not the receiver, set the limit.")
    else:
        lines.append(f"- No winner: {', '.join(winners)} tie at {top:,} msg/s.")
    return lines


def turing2_lines(found, rows, failed, cap, ceiling):
    """The T-URING-2 verdict, from the bisection's best mode and its confirmation runs."""
    lines = [f"Target: zero loss at ≥ {TARGET_1MS:,} msg/s on debian, for the winning mode "
             "(BENCHMARKS.md, Nasdaq's 1 ms peak).", ""]
    results = {mode: {"result": one["result"], "rate": one["rate"]}
               for mode, one in found.items()}
    best = udp_driver.best_mode(results)
    if best is None:
        lines.append("Verdict: **FAIL**, no mode was lossless at any rate tried; the target "
                     "was not tested.")
        return lines, "FAIL", None
    one = found[best]
    gap = one["rate"] - TARGET_1MS
    measured = (f"{best}'s largest lossless rate in the bisection is "
                + (f"at least {one['rate']:,} msg/s, sender limited" if
                   one["result"] == "sender limited" else f"{one['rate']:,} msg/s")
                + f"; gap {'at least ' if one['result'] == 'sender limited' else ''}"
                f"{gap:+,} msg/s ({100 * gap / TARGET_1MS:+.1f}%) against the target")
    confirms = [row for row in rows if row["role"] == "confirm"]
    lost = [one for one in failed if one["role"] == "confirm"]
    if TARGET_1MS > cap:
        lines.append(f"Verdict: **FAIL**, not shown: {TARGET_1MS:,} msg/s is above the cap "
                     f"{cap:,} msg/s, 0.9 × the sender's ceiling {ceiling:,}, so no trial may "
                     f"run there; {measured}.")
        return lines, "FAIL", best
    if any(row["mode"] != best for row in confirms) or any(o["mode"] != best for o in lost):
        raise ReportError(f"a confirmation run is not of the best mode, {best}")
    good = sum(1 for row in confirms if valid_lossless(row) and row["target"] == TARGET_1MS)
    total = len(confirms) + len(lost)
    verdict = "PASS" if total and good == total else "FAIL"
    lines.append(f"Verdict: **{verdict}**, {best} lossless at {TARGET_1MS:,} msg/s in {good} of "
                 f"{total} repetitions; {measured}.")
    return lines, verdict, best


def feed_lines(rows):
    modes = {}
    for row in rows:
        if row["role"] in FEED_HEADS:
            modes.setdefault(row["mode"], {}).setdefault(row["role"], []).append(row)
    if not modes:
        return None
    lines = ["Pass is lossless in every repetition.", "",
             "| Mode | " + " | ".join(FEED_HEADS.values()) + " |",
             "|---|" + "---|" * len(FEED_HEADS)]
    for mode in (m for m in MODES if m in modes):
        cells = []
        for role in FEED_HEADS:
            runs = modes[mode].get(role, [])
            good = sum(1 for row in runs if row["lossless"] and row["verdict"] == "PASS")
            cells.append(NONE if not runs else
                         f"{'pass' if good == len(runs) else 'fail'}, {good} of {len(runs)} "
                         "lossless")
        lines.append(f"| {mode} | " + " | ".join(cells) + " |")
    return lines


def role_order(role):
    if role == "latency-low":
        return (0, 0.0)
    return (1, float(role.split("-", 1)[1]))


def latency_lines(rows):
    groups = {}
    for row in rows:
        if row["role"].startswith("latency-"):
            groups.setdefault((row["mode"], row["role"]), []).append(row)
    if not groups:
        return None
    lines = ["Medians over the repetitions, in µs. `latency-low` is the low packet rate; "
             "`latency-F` is F × the mode's largest lossless rate.", "",
             "| Mode | Role | Rate msgs/s | Repetitions | p50 µs | p99 µs | p99.9 µs | max µs |",
             "|---|---|---|---|---|---|---|---|"]
    for mode, role in sorted(groups, key=lambda pair: (MODES.index(pair[0]),
                                                       role_order(pair[1]))):
        runs = groups[(mode, role)]
        rates = sorted({row["target"] for row in runs})
        values = [cell("us", median_or_none([row[key] for row in runs]))
                  for key in ("p50", "p99", "p99_9", "max")]
        lines.append(f"| {mode} | {role} | {', '.join(f'{r:,}' for r in rates)} | {len(runs)} | "
                     + " | ".join(values) + " |")
    return lines


CPU_KEYS = ("cpu_sender", "cpu_receiver", "cpu_sq", "softirq_sender", "softirq_receiver")


def cpu_lines(rows, irq_accounting):
    groups = by_mode(rows, "cpu")
    if not groups:
        return None
    lines = ["Each share is of its own window: the sender's thread from its first send to its "
             "last, the receiver's thread, the SQ thread and each CPU's softirq from when both "
             "threads were ready to the end of session. Softirq and the SQ thread are counted "
             "in USER_HZ ticks. Medians over the repetitions.", "",
             "| Mode | Rate msgs/s | Sender % | Receiver % | SQ thread % | "
             "Softirq, sender's CPU % | Softirq, receiver's CPU % | Sum % | Note |",
             "|---|---|---|---|---|---|---|---|---|"]
    for mode, runs in groups.items():
        medians = [median_or_none([row[key] for row in runs]) for key in CPU_KEYS]
        total = sum(value for value in medians if value is not None)
        rates = sorted({row["target"] for row in runs})
        lines.append(f"| {mode} | {', '.join(f'{r:,}' for r in rates)} | "
                     + " | ".join(cell("pct", value) for value in medians)
                     + f" | {total:.1f} | {SPIN_NOTE.get(mode, '')} |")
    lines += ["", "- On loopback the receive softirq runs on the sender's CPU (IU-39), so the "
                  "sender's CPU softirq share is the kernel's receive work for every mode.",
              "- Without IRQ time accounting, softirq ticks may also appear in the sender "
              "thread's stime, so the sum can count that work twice. "
              f"CONFIG_IRQ_TIME_ACCOUNTING on this host: {irq_accounting}.",
              "- A spinning mode's CPU is 100% by construction; compare it by its largest "
              "lossless rate instead (IU-46)."]
    return lines


PEAK_FIELD = re.compile(r"^([0-9,]+) messages from frame ([0-9,]+)")


def feed_speedups(fields):
    """{role: speedup text} from the header's window peaks, as the driver works them out."""
    out = {"feed-x1": f"feed ×{1.0:.3f}"}
    for role, label in (("feed-1s", "1s"), ("feed-1ms", "1ms")):
        match = PEAK_FIELD.match(fields.get(f"window_peak_{label}", ""))
        if match is None:
            raise ReportError(f"no window_peak_{label} field")
        peak = int(match.group(1).replace(",", ""))
        target = TARGET_1S if label == "1s" else TARGET_1MS
        speedup = udp_driver.speedup_for(target, peak, udp_driver.PEAK_SECONDS[role])
        out[role] = f"feed ×{speedup:.3f}"
    return out


def derived(fields, rows, failed):
    """Every section worked out from the table, by title, and the summary."""
    sections = {}
    summary = []
    lines, ceiling = ceiling_lines(rows)
    if lines is not None:
        sections["Sender ceiling"] = lines
        summary.append(f"sender ceiling {ceiling:,} msg/s")
    cap = check_results.number(fields.get("rate_cap", "")) if fields.get("rate_cap") else None
    cap = None if cap is None else int(cap)
    shown = check_results.number(fields.get("sender_ceiling", "")) \
        if fields.get("sender_ceiling", "").split(" ")[0][:1].isdigit() else None
    found = largest_lossless(rows, cap) if cap is not None else {}
    datagram = datagram_of(fields)
    judged = datagram == TURING2_DATAGRAM
    if not judged and any(row["role"] == "confirm" for row in rows):
        raise ReportError(f"T-URING-2 confirmation runs at {datagram} bytes a datagram; "
                          f"T-URING-2 is judged at {TURING2_DATAGRAM:,}")
    if any(row["role"] == "trial" for row in rows):
        if cap is None:
            raise ReportError("trials with no rate_cap field")
        sections["Largest lossless rate"] = lossless_lines(found)
        lines = turing1_lines(found, fields.get("packet_size_cap", "packet size not recorded"))
        if fields.get("step") == "bisect" and not judged:
            elsewhere = file_name("bisect", TURING2_DATAGRAM, fields.get("host"),
                                  fields.get("dataset", "").split(".", 1)[0])
            lines.append(f"- No T-URING-2 verdict here: T-URING-2 is judged at "
                         f"{TURING2_DATAGRAM:,} bytes a datagram (P-0902), in `{elsewhere}`.")
        sections["T-URING-1: io_uring against sockets"] = lines
        if "S3" in found and "U2" in found:
            summary.append(f"U2 {rate_text(found['U2'])} against S3 {rate_text(found['S3'])} "
                           "msg/s")
    if fields.get("step") == "bisect" and not judged:
        summary.append(f"no T-URING-2 verdict at {datagram} bytes a datagram")
    elif fields.get("step") == "bisect":
        if cap is None or shown is None:
            raise ReportError("a bisect file with no sender_ceiling or rate_cap")
        lines, verdict, best = turing2_lines(found, rows, failed, cap, int(shown))
        sections["T-URING-2: lossless at Nasdaq's peak message rate"] = lines
        summary.append(f"T-URING-2 {verdict}" + (f" for {best}" if best else ""))
    lines = feed_lines(rows)
    if lines is not None:
        sections["Feed-shaped replay"] = lines
        passed = sum(line.count("pass,") for line in lines)
        cells = sum(line.count(" lossless |") for line in lines)
        summary.append(f"feed-shaped replay {passed} of {cells} mode-schedules lossless")
    lines = latency_lines(rows)
    if lines is not None:
        sections["Latency"] = lines
        summary.append(f"latency at {len(lines) - 4} mode-rates")
    lines = cpu_lines(rows, fields.get("irq_time_accounting", "not read: no smoke run given"))
    if lines is not None:
        sections["CPU split at the common rate"] = lines
        summary.append("CPU split at the common rate")
    return sections, "; ".join(summary) if summary else "no derived figure"


# --- Reading runs ---------------------------------------------------------------------------

def read_runs(run_dirs):
    """(timed runs, smoke): each timed run (manifest, [(entry, record, stderr)], [failed])."""
    timed, smoke = [], None
    for run_dir in run_dirs:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        if manifest["step"] == "smoke":
            smoke = (manifest, json.loads((run_dir / "smoke.json").read_text(encoding="utf-8")))
            continue
        done, failed = [], []
        for entry in manifest["processes"]:
            if entry["outcome"] != "result":
                failed.append({"run": entry["stem"], "mode": entry["mode"], "role": entry["role"],
                               "exit": entry["exit"], "why": entry["why"]})
                continue
            record = json.loads((run_dir / f"{entry['stem']}.json").read_text(encoding="utf-8"))
            err = (run_dir / f"{entry['stem']}.err").read_text(encoding="utf-8",
                                                               errors="replace")
            done.append((entry, record, err))
        timed.append((manifest, done, failed))
    if not timed:
        raise ReportError("no timed run among the run directories")
    return timed, smoke


def load_pair(err):
    start = stamp_load.one_line(stamp_load.START, err, "start")
    end = stamp_load.one_line(stamp_load.END, err, "end")
    return tuple(stamp_load.HOST.sub(f"host={DEBIAN}", line) for line in (start, end))


def stamp_of(line):
    match = check_results.TIMESTAMP.search(line)
    if match is None:
        raise ReportError(f"no timestamp in {line!r}")
    return match.group(1)


def distinct(values):
    seen = list(dict.fromkeys(str(value) for value in values))
    return "; ".join(seen)


def mitigations(readings):
    value = readings.get("vulnerabilities")
    if isinstance(value, str):
        return value
    return "; ".join(f"{name}: {text}" for name, text in value.items())


def smoke_fields(smoke):
    if smoke is None:
        return []
    manifest, data = smoke
    readings = data["readings"]
    loopback = data.get("loopback", {})
    return [("smoke_run_id", manifest["run_id"]),
            ("io_uring_disabled", readings["io_uring_disabled"]),
            ("io_uring_group", readings["io_uring_group"]),
            ("rmem_max", readings["rmem_max"]), ("rmem_default", readings["rmem_default"]),
            ("ulimit_l", readings["ulimit_l"]), ("mitigations", mitigations(readings)),
            ("lo_mtu", readings["lo_mtu"]), ("irq_time_accounting",
                                             readings["irq_time_accounting"]),
            ("ip_early_demux", readings["ip_early_demux"]),
            ("clocksource", readings["clocksource"]),
            ("smoke_loopback", "; ".join(f"{mode} {one['verdict'] or 'no verdict'}"
                                         for mode, one in loopback.items()))]


def ceiling_field(manifests, rows):
    """The sender's ceiling, from the file's own sender rows if it has them."""
    _, from_rows = ceiling_lines(rows)
    given = [m["ceiling"] for m in manifests if "ceiling" in m]
    if from_rows is not None:
        for one in given:
            if one["messages_per_s"] != from_rows:
                raise ReportError(f"a run used a ceiling of {one['messages_per_s']:,}, the "
                                  f"sender rows give {from_rows:,}")
        return from_rows, "the median of the sender rows below"
    if not given:
        return None, None
    if len({one["messages_per_s"] for one in given}) != 1:
        raise ReportError("the runs used different ceilings")
    return given[0]["messages_per_s"], given[0]["source"]


def check_bisections(manifests, found):
    """The driver's own results agree with what the table gives."""
    for manifest in manifests:
        for mode, one in (manifest.get("bisect") or {}).items():
            if one["result"] == "failed":
                continue
            mine = found.get(mode)
            if mine is None or (mine["result"], mine["rate"]) != (one["result"], one["rate"]):
                raise ReportError(f"{mode}: the driver found {one['result']} {one['rate']}, "
                                  f"the table gives {mine}")


def feed_fields(manifests):
    feeds = [m["feed"] for m in manifests if "feed" in m]
    if not feeds:
        return []
    if len(feeds) != 1:
        raise ReportError("more than one feed run")
    feed = feeds[0]
    window = feed["window"]

    def peak(value):
        return f"{value[0]:,} messages from frame {value[1]:,}"

    return [("feed_window", f"frames {window['first']:,} to "
                            f"{window['first'] + window['frames']:,}, from "
                            f"{window['lead_frames']:,} frames before the day's busiest second"),
            ("day_peak_1s", peak(feed["day_peaks"]["1s"])),
            ("day_peak_1ms", peak(feed["day_peaks"]["1ms"])),
            ("window_peak_1s", peak(feed["window_peaks"]["1s"])),
            ("window_peak_1ms", peak(feed["window_peaks"]["1ms"])),
            ("flush", f"{feed['flush_us']:g} µs of replay time at every speed"),
            ("longest_gap", f"{window['longest_gap_ns'] / 1e6:,.3f} ms of ITCH time in the "
                            "window, each run's idle timeout above it at its speed")]


def render(timed, smoke, name):
    match = FILE_NAME.match(name)
    if match is None:
        raise ReportError(f"{name} is not udp_STEP_debian_DAY.md")
    step, _, host_file, day = match.groups()
    manifests = [manifest for manifest, _, _ in timed]
    steps = [manifest["step"] for manifest in manifests]
    if steps.count(step) != 1:
        raise ReportError(f"{name} is a {step} file, and the runs are {steps}")
    own = manifests[steps.index(step)]
    records = [record for _, done, _ in timed for _, record, _ in done]
    if not records:
        raise ReportError("no process has a record")
    header = records[0]["header"]
    for field in ("host", "cpu", "kernel", "compiler", "flags", "commit", "dataset",
                  "dataset_sha256"):
        values = {record["header"].get(field) for record in records}
        if len(values) != 1:
            raise ReportError(f"the records differ in {field}: {sorted(map(str, values))}")
    if header["host"] != DEBIAN or host_file != "debian":
        raise ReportError(f"a {header['host']} run: only debian files are written")
    if header["dataset"].split(".", 1)[0] != day:
        raise ReportError(f"{name} names {day}, the records {header['dataset']}")
    datagrams = sorted({record["plan"]["max_datagram"] for record in records})
    if len(datagrams) != 1:
        raise ReportError(f"the records differ in max_datagram: {datagrams}")
    datagram = datagrams[0]
    if name != file_name(step, datagram, host_file, day):
        raise ReportError(f"{name} is not the name for {datagram:,} bytes a datagram: "
                          f"{file_name(step, datagram, host_file, day)}")
    cores = manifests[0]["cores"]
    if any(manifest["cores"] != cores for manifest in manifests):
        raise ReportError("the runs used different cores")
    rows, failed, loads = [], [], []
    for manifest, done, lost in timed:
        for entry, record, err in done:
            if record.get("plan", {}).get("destination") != DESTINATION:
                raise ReportError(f"{entry['stem']}: sent to {record['plan'].get('destination')}")
            rows.append(row_of(entry, record, cores))
            loads.append(load_pair(err))
        failed += lost
    if len({row["run"] for row in rows}) != len(rows):
        raise ReportError("two processes share a name: two runs of one step")
    ceiling, source = ceiling_field(manifests, rows)
    cap = udp_driver.cap_of(ceiling) if ceiling is not None else None
    for manifest in manifests:
        if "rate_cap" in manifest and manifest["rate_cap"] != cap:
            raise ReportError(f"run {manifest['run_id']} capped at {manifest['rate_cap']}")
    check_bisections(manifests, largest_lossless(rows, cap) if cap is not None else {})
    pinned = sum(1 for row in rows if "not pinned" not in row["pins"])
    before = manifests[0]["readings_before"]
    after = manifests[-1]["readings_after"]
    paranoid = [m[f"readings_{when}"]["perf_event_paranoid"] for m in manifests
                for when in ("before", "after")]
    checks = [m.get(f"machine_check_{when}") or "missing" for m in manifests
              for when in ("before", "after")]
    passed = sum(1 for text in checks if text.strip().endswith("machine_check: passed"))
    loads.sort(key=lambda pair: stamp_of(pair[0]))
    first_load = loads[0][0]
    last_load = max((pair[1] for pair in loads), key=stamp_of)
    fields = [(key, header[key]) for key in ("host", "cpu", "kernel", "compiler", "flags",
                                             "commit", "dataset", "dataset_sha256")]
    fields += [("step", step), ("steps", ", ".join(steps)),
               ("prediction", own["prediction"]), ("run_id", own["run_id"]),
               ("predictions", ", ".join(dict.fromkeys(m["prediction"] for m in manifests))),
               ("runs", ", ".join(m["run_id"] for m in manifests)),
               ("cores", f"sender {cores['sender']}, receiver {cores['receiver']}, SQ thread "
                         f"{cores['sq']} (U5 only)"),
               ("pin_outcomes", f"{pinned} of {len(rows)} processes with every thread asked "
                                "for pinned and read back"),
               ("governor", distinct(r["header"]["governor"] for r in records)),
               ("turbo", distinct(r["header"]["turbo"] for r in records)),
               ("smt", distinct(r["header"]["smt"] for r in records)),
               ("packet_size_cap", distinct(f"{r['plan']['max_datagram']:,} bytes a datagram, "
                                            "header included" for r in records)),
               ("connected", CONNECTED),
               ("receive_buffer", distinct(
                   f"requested {r['receive_buffer']['requested']:,} bytes, read back "
                   f"{r['receive_buffer']['read_back']:,}" for r in records)),
               ("first_frame", distinct(f"{r['plan']['first_frame']:,}" for r in records
                                        if r.get("schedule", {}).get("kind") != "feed")
                or NONE),
               ("sender_ceiling", f"{ceiling:,} msg/s, {source}" if ceiling is not None
                else "not in this file"),
               ("rate_cap", f"{cap:,} msg/s, 0.9 × the sender's ceiling" if cap is not None
                else "not in this file")]
    fields += smoke_fields(smoke)
    fields += feed_fields(manifests)
    fields += [("machine_checks", f"{passed} of {len(checks)} passed, before and after each "
                                  "run"),
               ("perf_event_paranoid", f"{before['perf_event_paranoid']} at the start, "
                                       f"{after['perf_event_paranoid']} at the end"),
               ("pswpout", f"{before['pswpout']} at the start, {after['pswpout']} at the end"),
               ("uptime_s", f"{before['uptime_s']} at the start, {after['uptime_s']} at the "
                            "end")]
    if "3" in paranoid:
        fields.append(("warning", "perf_event_paranoid read 3 during a run: perf could not "
                                  "profile even user space"))
    fields += [("mode_order_seed", ", ".join(f"{m['run_id']}: {m['seed']}" for m in manifests)),
               ("processes", f"{len(rows)} with a record, {len(failed)} without"),
               ("measure_load_start", first_load), ("measure_load_end", last_load),
               ("run_started", stamp_of(first_load)), ("run_finished", stamp_of(last_load))]
    as_dict = dict(fields)
    parsed = parse_runs_table("## Every process\n\n" + "\n".join(runs_table(rows)) + "\n")
    sections, summary = derived(as_dict, parsed, failed)
    fields.append(("summary", summary))
    title = f"T-URING-1 and T-URING-2, {TITLES[step]}" if datagram == TURING2_DATAGRAM else \
        f"T-URING-1 at {datagram:,} bytes a datagram, {TITLES[step]}"
    out = [f"# {title}, {host_file}, {day}", "",
           "Generated by `scripts/udp_report.py` from raw records that are not committed. "
           "Do not edit by hand.", "",
           f"Host: **{DEBIAN}**. Numbers from the two hosts are never mixed.", "",
           COMPARABILITY, ""]
    if "warning" in as_dict:
        out += [f"**Warning: {as_dict['warning']}.**", ""]
    out += ["## Header", ""]
    out += [f"- {key}: {value}" for key, value in fields]
    out += ["", "## What was run", ""]
    out += [f"- {s}: {method_of(s, datagram)}" for s in dict.fromkeys(steps)]
    for title, lines in sections.items():
        out += ["", f"## {title}", ""] + lines
    out += ["", "## Every process", "",
            "One row a process with a record, in the order run. A count is the run's; a dash "
            "is a value the record does not have, and a dash in a loss counter other than RXQ "
            "overflow is one that could not be read, which is not zero. Every figure above is "
            "worked from these rows.", ""]
    out += runs_table(rows)
    out += ["", "## Processes with no usable record", ""]
    out += failed_table(failed) if failed else ["None."]
    out += ["", "## machine_check output", "", "Before and after each run, in order.", "",
            "```"]
    out += [text.strip() for text in checks]
    out += ["```", "", "## measure.sh load lines", "", "```"]
    out += [line for pair in loads for line in pair]
    out += ["```", "", "## Commands", "", "```"]
    out += command_lines(timed, header)
    out += ["```", ""]
    return "\n".join(out), summary


def command_lines(timed, header):
    """One command a role, as the driver built it, with the raw paths left out."""
    lines = []
    for manifest, done, lost in timed:
        cores = manifest["cores"]
        args = argparse.Namespace(measure="measure.sh", bench="build/release/bench/bench_udp",
                                  input=f"<raw>/{header['dataset']}", core=cores["sender"],
                                  receiver_core=cores["receiver"], sq_core=cores["sq"],
                                  dataset=header["dataset"], dataset_sha256="<sha256>")
        seen = set()
        for entry in manifest["processes"]:
            if entry["role"] in seen:
                continue
            seen.add(entry["role"])
            words = udp_driver.bench_words(args, entry["mode"], entry["extra"],
                                           f"<raw>/{entry['stem']}", "<commit>")
            lines.append(" ".join(words) + f"  (udp_driver.py --step {manifest['step']})")
    return lines


# --- Checking a published file ----------------------------------------------------------------

def check_published(text, name=None):
    """What is wrong with a published file, read alone, as (code, detail) pairs."""
    found = []
    if COMPARABILITY not in text.splitlines():
        found.append(("comparability-unsaid", "the file does not say loopback is not a network"))
    fields = check_results.parse_header(text)
    if not fields:
        return found + [("header-missing", "no Header section")]
    host = fields.get("host")
    if host != DEBIAN:
        found.append(("host-label", f"{host!r}: only debian files are results"))
    match = FILE_NAME.match(name) if name is not None else None
    if name is not None:
        day = fields.get("dataset", "").split(".", 1)[0]
        if match is None or match.group(3) != ("debian" if host == DEBIAN else "mac") or \
                match.group(1) != fields.get("step") or match.group(4) != day:
            found.append(("host-label", f"{name} holds {fields.get('step')} on {host}, {day}"))
        datagram = datagram_of(fields)
        if match is not None and (datagram is None or match.group(2) != (
                None if datagram == TURING2_DATAGRAM else str(datagram))):
            found.append(("cap-label", f"{name} for {fields.get('packet_size_cap')!r}"))
    if not re.match(r"^[0-9a-f]{12} ", fields.get("commit", "")):
        found.append(("commit-missing", repr(fields.get("commit"))))
    found += list(check_results.check_load(fields))
    found += list(check_results.check_private(text, fields))
    if fields.get("governor") != "performance":
        found.append(("governor", fields.get("governor", "no field")))
    runs = [one for one in fields.get("runs", "").split(", ") if one]
    if fields.get("run_id") not in runs or \
            not re.fullmatch(r"P-\d{4}", fields.get("prediction", "")):
        found.append(("prediction-missing", f"run_id {fields.get('run_id')!r}, prediction "
                                            f"{fields.get('prediction')!r}"))
    output = check_results.section(text, "machine_check output") or ""
    wanted = 2 * len(runs)
    if not runs or output.count("machine_check: passed") != wanted or \
            "machine_check: FAIL" in output or \
            fields.get("machine_checks") != f"{wanted} of {wanted} passed, before and after " \
                                            "each run":
        found.append(("machine-check-failed", f"wanted {wanted} passing checks, before and "
                                              "after each run"))
    try:
        rows = parse_runs_table(text)
        failed = parse_failed_table(text)
    except (ReportError, ValueError) as error:
        return found + [("table-unusable", repr(error))]
    if not rows:
        return found + [("table-missing", "no process in the table")]
    found += check_rows(rows, fields)
    try:
        sections, summary = derived(fields, rows, failed)
    except (ReportError, KeyError, ValueError, ZeroDivisionError, statistics.StatisticsError) \
            as error:
        return found + [("table-unusable", repr(error))]
    for title, lines in sections.items():
        if check_results.section(text, title) != "\n" + "\n".join(lines) + "\n\n":
            found.append(("derived-differs", f"{title} is not what the table gives"))
    if fields.get("summary") != summary:
        found.append(("summary-differs", f"{fields.get('summary')!r} against {summary!r}"))
    marks = len(results_table.VERDICT_MARK.findall(text))
    expected = 1 if fields.get("step") == "bisect" and \
        datagram_of(fields) == TURING2_DATAGRAM else 0
    if marks != expected:
        found.append(("verdict-marks", f"{marks} verdict marks, wanted {expected}"))
    return found


def check_rows(rows, fields):
    found = []
    pinned = 0
    for row in rows:
        receive = row["role"] != "ceiling"
        roles = ("sender", "receiver") if receive else ("sender",)
        if "not pinned" in row["pins"] or \
                any(not row["pins"].startswith(role) and f", {role} " not in row["pins"]
                    for role in roles):
            found.append(("unpinned", f"{row['run']}: {row['pins']}"))
        else:
            pinned += 1
        if receive and row["lossless"] != row_lossless(row):
            found.append(("lossless-differs", f"{row['run']} says lossless {row['lossless']}, "
                                              "its counters say otherwise"))
    if fields.get("pin_outcomes") != f"{pinned} of {len(rows)} processes with every thread " \
                                     "asked for pinned and read back":
        found.append(("unpinned", f"pin_outcomes {fields.get('pin_outcomes')!r}"))
    ceiling = fields.get("sender_ceiling", "")
    cap_text = fields.get("rate_cap", "")
    if ceiling[:1].isdigit():
        shown = int(check_results.number(ceiling))
        if not cap_text[:1].isdigit() or int(check_results.number(cap_text)) != \
                udp_driver.cap_of(shown):
            found.append(("above-ceiling", f"cap {cap_text!r} is not 0.9 × {shown:,}"))
        _, from_rows = ceiling_lines(rows)
        if from_rows is not None and from_rows != shown:
            found.append(("ceiling-differs", f"{shown:,} against the rows' {from_rows:,}"))
    if cap_text[:1].isdigit():
        cap = int(check_results.number(cap_text))
        for row in rows:
            if row["schedule"] == "rate" and row["target"] is not None and row["target"] > cap:
                found.append(("above-ceiling", f"{row['run']} ran at {row['target']:,} msg/s, "
                                               f"above the cap {cap:,}"))
    elif any(row["schedule"] == "rate" for row in rows):
        found.append(("above-ceiling", "rate-paced rows with no cap to hold them to"))
    feeds = [row for row in rows if row["role"] in FEED_HEADS]
    if feeds:
        try:
            speedups = feed_speedups(fields)
        except ReportError as error:
            return found + [("speedup-differs", str(error))]
        for row in feeds:
            if row["schedule"] != speedups[row["role"]]:
                found.append(("speedup-differs", f"{row['run']} ran {row['schedule']}, the "
                                                 f"window's peak gives {speedups[row['role']]}"))
    return found


def report_violations(violations):
    for code, detail in violations:
        print(f"udp_report: FAIL {code}: {detail}")
    return 1 if violations else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--run-log", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args(argv)
    if args.check is None and (not args.run_dir or args.out is None):
        parser.error("wanted --check FILE, or --run-dir and --out")
    try:
        if args.check is not None:
            violations = check_published(args.check.read_text(encoding="utf-8"),
                                         args.check.name)
            if violations:
                return report_violations(violations)
            print(f"udp_report: ok {args.check.name}")
            return 0
        timed, smoke = read_runs(args.run_dir)
        text, summary = render(timed, smoke, args.out.name)
        violations = check_published(text, args.out.name)
        if violations:
            return report_violations(violations)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        if args.run_log:
            fields = check_results.parse_header(text)
            manifest = timed[-1][0]
            with open(args.run_log, "a", encoding="utf-8") as log:
                log.write(f"| {manifest['run_id']}_report | {fields['prediction']} | "
                          f"{fields['commit']} | {manifest['host_os']} | published: "
                          f"{summary.replace('|', '/')} |\n")
        print(f"udp_report: wrote {args.out.name}")
        return 0
    except (ReportError, OSError, ValueError, KeyError, IndexError, TypeError,
            ZeroDivisionError, statistics.StatisticsError) as error:
        print(f"udp_report: unusable input: {error!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
