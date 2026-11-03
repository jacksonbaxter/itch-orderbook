#!/usr/bin/env python3
"""Checks a real-day itch_trades log against the data manifest and writes the results file.

    trades_report.py --day 12302019 --log RAW [--out results/x.md] [--no-cross-file]
                     [--manifest scripts/data_manifest.json]

The tool's own verdicts are not enough here. A tool that read half the day could pass, so
this recounts what it can from the census pinned in the manifest. The figures in
PUBLISHED are claims to set ours beside, never expected values.

--no-cross-file says that no CrossStats file exists for this day, so T-TRADE-2 is reported
as not available. A verdict of NOT_AVAILABLE without it is a failure.

Exit codes: 0 every hard check passed. 1 a hard check failed. 2 the inputs could not be
used. A failed T-TRADE-3 would exit 1 like any other: it is reported as failing, never as
a gap that passes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS = ("T-TRADE-1", "T-TRADE-2", "T-TRADE-3", "T-TRADE-5", "PRINTS")
PINNED_WINDOW = "0935_1600"
# The first date the SEC's stated round-lot rule does not cover. The tool holds the same
# date, and this is a second reading of it: a late day compared on the old rule fails here.
SEC_RULE_END = 20251103
ODD_LOTS_COMPARED = "compared"
ODD_LOTS_NOT_COMPARED = "not_compared_rule_ended"
CROSS_CLASSES = ("Q_O", "Q_C", "Q_H", "Q_I")

# Claims from BENCHMARKS.md and the research, set beside ours and not used as expected
# values. (label, source, value, how to read ours)
PUBLISHED = {
    "12302019": [
        ("Adds, A + F", "KareemJandali census", 118631456, "adds"),
        ("Deletes", "KareemJandali census", 114360997, "t1_orders_deleted"),
        ("Orders run out on an execution", "KareemJandali census", 4270459,
         "t1_orders_ran_out_on_execute"),
        ("E, market open and state T", "Ironbook", 5683178, "t5_population"),
        ("Of which at the best price", "Ironbook", 5683178, "t5_at_best"),
        ("Of which at the queue front", "Ironbook", 5672613, "t5_at_front"),
        ("CrossStats opening shares", "research, the file's TOTAL row", 19851021,
         "t2_file_opening"),
        ("CrossStats closing shares", "research, the file's TOTAL row", 93930336,
         "t2_file_closing"),
        ("Halt crosses", "research", 24, "halt_crosses"),
        ("MIDAS Nasdaq rows", "research", 5772, "t3_rows"),
        ("MIDAS TradeVol, shares", "research", 620148444, "t3_midas_trade_shares"),
        ("MIDAS HiddenVol, shares", "research", 105453046, "t3_midas_hidden_shares"),
    ],
}

# What each comparison sets against what, and why the two sides could disagree. A sum
# compared with itself proves nothing, so a row with no second party does not belong here.
COMPARISONS = [
    ("T-TRADE-1, per order", "`added` from A, F or U, less E, C and X, all from the feed, "
     "held in a ledger outside the book", "The remaining shares the book holds, read after "
     "every order message and at D and U", "The book loses, doubles or misapplies a "
     "deduction, drops an order early, or keeps one past its last share. The feed's silence "
     "at zero is the witness: Nasdaq sends no D for an order that ran out"),
    ("T-TRADE-1, day", "Shares added", "Executed + cancelled + the book's remainders at D and "
     "U + live. Orders opened against orders closed", "As above, summed. `D` carries no "
     "shares, so its term can only come from the book"),
    ("`E`, `C` hit an order", "Reference, locate and size printed in the feed", "The live "
     "order the book holds under that reference", "A lost add, a wrong locate, a print "
     "larger than the order, or an order already gone"),
    ("T-TRADE-5", "The price of the order `E` names, from the book", "The best price on its "
     "side, from the same book's level list", "Levels out of order, a stale top of book, or "
     "a feed where E can trade through. Not independent of the book: it tests its order"),
    ("`C` keeps the price", "The order's price before the print", "Its price after", "A book "
     "that reprices an order to the print. The print's own price is free to differ"),
    ("`P`", "Reference, side and symbol in the print", "The spec, the directory, and the "
     "book's own write counter and top of book", "Spec drift, or a book that deducts on a "
     "print that names no order"),
    ("T-TRADE-2", "`Q` shares per symbol and type, from the feed", "Nasdaq's CrossStats "
     "file, opening and closing columns", "A different source: the file is Nasdaq's, parsed "
     "here apart from the feed. A misread share count, a wrong type or a wrong symbol shows "
     "as a difference"),
    ("T-TRADE-2, outside the file", "`Q` shares less the file's sum, test symbols left out",
     "Type `H` shares", "A cross of a type the file does not hold, or a row missing"),
    ("`C_N` far leg", "A `C_N` sharing a match number with a print", "That print's time, "
     "symbol, shares, price and side", "Two prints sharing a number, or a `C_N` that does "
     "not describe the same trade"),
    ("`C_N` cross leg", "The `C_N` of one side of a cross, summed", "That `Q`'s shares and "
     "price", "A leg priced apart from its cross, or legs filling more than the cross did"),
    ("T-TRADE-3", "`E + C_Y + P` inside the window, per symbol, from the feed", "MIDAS "
     "TradeVol, HiddenVol, Trades, Hidden, OddLots and OddLotVol", "The SEC built these from "
     "the same direct feed with its own code. A wrong window, a wrong class, a misread size "
     "or a misattributed symbol shows as a difference. A fault in the feed itself would "
     "agree with itself here"),
    ("`B`", "The match number in a `B`", "The print log of the day so far", "A break of "
     "nothing, of another symbol, or of the same match twice"),
]


class ReportError(Exception):
    """The inputs cannot be used."""


KEY_VALUE = re.compile(r"(\w+)=(\S*)")


def parse_log(text):
    log = {"values": {}, "types": {}, "classes": {}, "checks": {}, "verdicts": {}, "fits": {},
           "outside": [], "c_price": {}, "far_legs": {}, "cross_legs": {}, "time": {}, "run": {},
           "violations": [], "edges": {}, "breaks": [], "errors": [], "round_lots": [],
           "sec_lots": []}
    for line in text.splitlines():
        stripped = line.strip()
        word, _, rest = stripped.partition(" ")
        pairs = dict(KEY_VALUE.findall(rest))
        if word.startswith("run_"):
            log["run"][word] = rest
        elif word == "type":
            kind, count = rest.split()
            log["types"][kind] = int(count)
        elif word == "class":
            log["classes"][rest.split()[0]] = {k: int(v) for k, v in pairs.items()}
        elif word == "check":
            log["checks"][rest.split()[0]] = (pairs["group"], int(pairs["violations"]))
        elif word == "verdict":
            fields = rest.split()
            log["verdicts"][fields[0]] = (fields[1], int(pairs["violations"]))
        elif word == "t3_fit":
            log["fits"][pairs["window"]] = {k: int(v) for k, v in pairs.items() if k != "window"}
        elif word == "t5_outside":
            log["outside"].append((pairs["market_open"], pairs["state"], int(pairs["count"])))
        elif word == "c_price":
            log["c_price"][pairs["printable"]] = {k: int(v) for k, v in pairs.items()
                                                  if k != "printable"}
        elif word == "cn_far_leg":
            log["far_legs"][pairs["print"]] = (int(pairs["count"]), int(pairs["shares"]))
        elif word == "cn_cross_leg":
            log["cross_legs"][pairs["cross"]] = (int(pairs["count"]), int(pairs["shares"]),
                                                 int(pairs["longest_lag_ns"]))
        elif word in ("t3_edge_start", "t3_edge_end"):
            log["edges"].update({k: int(v) for k, v in pairs.items()})
        elif word == "t3_round_lot":
            log["round_lots"].append(pairs)
        elif word == "t3_sec_lot":
            log["sec_lots"].append(pairs)
        elif word == "violation":
            log["violations"].append(rest)
        elif word == "broken_trade":
            log["breaks"].append(pairs)
        elif word == "reference_error":
            log["errors"].append(rest)
        elif stripped.endswith("maximum resident set size"):
            log["time"]["max_rss"] = int(word)
        elif stripped.endswith("peak memory footprint"):
            log["time"]["peak_footprint"] = int(word)
        elif re.fullmatch(r"[\d.]+ real\s+[\d.]+ user\s+[\d.]+ sys", stripped):
            fields = stripped.split()
            log["time"].update(real=float(fields[0]), user=float(fields[2]), sys=float(fields[4]))
        elif rest and " " not in rest:
            log["values"][word] = rest
    return log


def number(log, key):
    if key not in log["values"]:
        raise ReportError(f"the log has no {key}")
    return int(log["values"][key])


def class_of(log, name):
    if name not in log["classes"]:
        raise ReportError(f"the log has no class {name}")
    return log["classes"][name]


def hard_failures(log, entry, midas_rows, no_cross_file, date):
    """Every reason the run does not meet the trade-print checks on this day.

    midas_rows is the manifest's Nasdaq row count for the day, or None if none is pinned.
    date is the day as YYYYMMDD, which says whether the SEC's round-lot rule covers it.
    """
    failures = []

    def require(condition, text):
        if not condition:
            failures.append(text)

    messages = entry["messages"]
    counts = entry["per_type_counts"]
    require(not log["errors"], f"a reference file failed to parse: {log['errors']}")
    require(number(log, "frames") == messages, f"frames {number(log, 'frames')}, manifest {messages}")
    require(number(log, "messages_checked") == messages,
            f"{number(log, 'messages_checked')} messages checked, manifest {messages}")
    require(log["types"] == counts, "per-type counts differ from the manifest")

    # Each class against the census. The tool counted one, and fetch_data.py the other.
    require(class_of(log, "E")["count"] == counts.get("E", 0), "E count differs from the census")
    require(class_of(log, "C_Y")["count"] + class_of(log, "C_N")["count"] == counts.get("C", 0),
            "C_Y + C_N differs from the census")
    require(class_of(log, "P")["count"] == counts.get("P", 0), "P count differs from the census")
    crosses = sum(class_of(log, name)["count"] for name in CROSS_CLASSES)
    require(crosses == counts.get("Q", 0), "Q count differs from the census")
    require(number(log, "b_messages") == counts.get("B", 0), "B count differs from the census")
    require(number(log, "b_matched") == counts.get("B", 0), "a B matched no print")

    # T-TRADE-1, recomputed here from the census and the tool's sums.
    opened = counts.get("A", 0) + counts.get("F", 0) + counts.get("U", 0)
    require(number(log, "t1_orders_opened") == opened, "orders opened differ from A + F + U")
    require(number(log, "t1_orders_deleted") == counts.get("D", 0), "deletes differ from D")
    require(number(log, "t1_orders_replaced") == counts.get("U", 0), "replaces differ from U")
    closed = sum(number(log, key) for key in ("t1_orders_ran_out_on_execute",
                                              "t1_orders_ran_out_on_cancel", "t1_orders_deleted",
                                              "t1_orders_replaced", "t1_orders_live_end"))
    require(closed == opened, f"{opened} orders opened, {closed} accounted for")
    left = sum(number(log, key) for key in ("t1_shares_executed", "t1_shares_cancelled",
                                            "t1_shares_deleted", "t1_shares_replaced_out"))
    require(number(log, "t1_shares_added") == left,
            f"{number(log, 't1_shares_added')} shares added, {left} accounted for")
    require(number(log, "t1_orders_live_end") == 0, "orders live at the end of the day")
    require(number(log, "t1_book_checked_empty") == 1, "the book was never checked at S/C")

    # T-TRADE-5 has to account for every E.
    outside = sum(count for _, _, count in log["outside"])
    require(number(log, "t5_population") + outside == counts.get("E", 0),
            "T-TRADE-5 does not account for every E")
    require(number(log, "t5_at_best") == number(log, "t5_population"),
            "an E in the population was not at the best price")

    for name, (group, violations) in log["checks"].items():
        require(violations == 0, f"check {name} ({group}): {violations} violations")
    for name in RESULTS:
        verdict, _ = log["verdicts"].get(name, ("missing", 0))
        if name == "T-TRADE-2" and no_cross_file:
            require(verdict == "NOT_AVAILABLE", f"T-TRADE-2 ran with --no-cross-file: {verdict}")
        else:
            require(verdict == "PASS", f"{name} verdict is {verdict}")

    require(midas_rows is None or number(log, "t3_rows") == midas_rows,
            f"MIDAS rows {number(log, 't3_rows')}, manifest {midas_rows}")
    require(log["values"].get("t3_window") == PINNED_WINDOW,
            f"T-TRADE-3 compared on {log['values'].get('t3_window')}, not {PINNED_WINDOW}")
    odd_lots = log["values"].get("t3_odd_lot_columns")
    wanted = ODD_LOTS_COMPARED if date < SEC_RULE_END else ODD_LOTS_NOT_COMPARED
    require(odd_lots == wanted, f"odd-lot columns {odd_lots} on {date}, wanted {wanted}")
    fit = log["fits"].get(PINNED_WINDOW)
    require(fit is not None, f"no fit for the window {PINNED_WINDOW}")
    if fit is not None:
        symbols = number(log, "t3_symbols_compared")
        keys = ("equal_shares", "equal_hidden", "equal_prints", "equal_hidden_prints")
        if odd_lots == ODD_LOTS_COMPARED:
            keys += ("equal_odd_lots", "equal_odd_shares")
        for key in keys:
            require(fit[key] == symbols, f"{key} {fit[key]} of {symbols} symbols")
    require(number(log, "t3_symbols_compared") + number(log, "t3_second_rows")
            == number(log, "t3_rows"), "MIDAS rows are not all compared")
    return failures


def format_table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines


def count(value):
    return f"{value:,}"


def clock(ns):
    seconds = ns // 1_000_000_000
    return (f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"
            f".{ns % 1_000_000_000:09}")


def slash(pair):
    prints, shares = pair.split("/")
    return f"{count(int(prints))} / {count(int(shares))}"


def slash_or_none(pair):
    return "not compared" if pair == "none" else slash(pair)


def lot_rule_that_fits(row):
    rules = (("SEC", "feed_below_sec"), ("directory", "feed_below_lot"), ("100", "feed_below_100"))
    fits = [name for name, key in rules if row[key] == row["midas"]]
    return ", ".join(fits) or "none"


def our_figure(log, key):
    if key == "adds":
        return log["types"].get("A", 0) + log["types"].get("F", 0)
    if key == "halt_crosses":
        return class_of(log, "Q_H")["count"]
    return number(log, key)


def group_violations(log, *names):
    return sum(violations for name, (_, violations) in log["checks"].items() if name in names)


def per_type_rows(log):
    """Each class, what the user's sentence asks of it, and what was checked instead."""
    far = log["far_legs"]
    cross = log["cross_legs"]
    far_count = sum(row[0] for row in far.values())
    far_shares = sum(row[1] for row in far.values())
    cross_count = sum(row[0] for row in cross.values())
    cross_shares = sum(row[1] for row in cross.values())
    tally = log["classes"]
    zero_q = sum(tally[name]["zero_share"] for name in CROSS_CLASSES)
    nonzero_q = sum(tally[name]["count"] for name in CROSS_CLASSES) - zero_q
    q_shares = sum(tally[name]["shares"] for name in CROSS_CLASSES)
    return [
        ("`E`", count(tally["E"]["count"]), count(tally["E"]["shares"]),
         "Live, on this locate, size within the order, deducted at the order's price. Best "
         "price with the market open and state `T`.",
         "Hard", count(group_violations(log, "e_not_live", "e_wrong_locate", "e_over_remaining",
                                        "e_zero_shares", "e_not_at_best")),
         "The sentence holds as written. `E` carries no price, so the printed price is the "
         "order's."),
        ("`C`, printable", count(tally["C_Y"]["count"]), count(tally["C_Y"]["shares"]),
         "As `E`, and the order's price stays. The print's price may differ.",
         "Hard", count(group_violations(log, "c_not_live", "c_wrong_locate", "c_over_remaining",
                                        "c_zero_shares", "c_bad_printable", "c_price_changed")),
         "Size holds. Price is refined: it hits the order, not always at the order's price."),
        ("`C`, not printable, far leg of a print", count(far_count), count(far_shares),
         "As `C`, and it shares its match number with one print: same time, symbol, shares "
         "and price, on the other side.",
         "Hard", count(group_violations(log, "match_shared", "match_leg_differs")),
         "It hits a resting order, but it is one trade with the print beside it, so it is not "
         "added to volume."),
        ("`C`, not printable, cross leg", count(cross_count), count(cross_shares),
         "As `C`, at its cross's price, and the legs of a side sum to no more than the cross.",
         "Hard", count(group_violations(log, "cn_over_cross", "cn_price", "cn_no_cross")),
         "It hits a resting order, inside a `Q`, so adding it to `Q` would double-count."),
        ("`P`", count(tally["P"]["count"]), count(tally["P"]["shares"]),
         "Reference 0, side `B`, symbol as listed, no book change.",
         "Hard", count(group_violations(log, "p_reference_nonzero", "p_side_not_b",
                                        "p_book_changed", "p_symbol_mismatch")),
         "**Excluded from \"hits a resting order\"**: it names no visible order. It enters "
         "volume, and `P` against HiddenVol is hard."),
        ("`Q`, shares traded", count(nonzero_q), count(q_shares),
         "No book change, symbol as listed. Per symbol against CrossStats.",
         "Hard", count(group_violations(log, "q_book_changed", "q_symbol_mismatch",
                                        "q_unknown_cross_type", "t2_opening", "t2_closing",
                                        "t2_intraday", "t2_outside_file", "t2_total_row")),
         "**A bulk print, not an execution against one order.** Checked per symbol, not per "
         "order."),
        ("`Q`, zero shares", count(zero_q), "0", "Counted. The feed announces a cross that "
         "traded nothing.", "Count", "n/a", "Not an execution."),
        ("`X`", count(log["types"].get("X", 0)), count(number(log, "t1_shares_cancelled")),
         "Live, within the order. Part of the per-order identity.", "Hard",
         count(group_violations(log, "order_refused", "order_mismatch")), "Not a print."),
        ("`D`", count(log["types"].get("D", 0)), count(number(log, "t1_shares_deleted")),
         "Live. Its remainder, which it does not state, comes from the book.", "Hard",
         count(group_violations(log, "remainder_mismatch")), "Not a print."),
        ("`U`", count(log["types"].get("U", 0)), count(number(log, "t1_shares_replaced_out")),
         "Old order live, new reference unused. The old remainder leaves, and the new order "
         "opens with its own size.", "Hard",
         count(group_violations(log, "order_refused", "order_alive_at_zero")), "Not a print."),
        ("`B`", count(number(log, "b_messages")), count(number(log, "b_shares")),
         "Its match was printed, on this locate, and is broken once. No book change.", "Hard",
         count(group_violations(log, "b_unknown_match", "b_wrong_locate", "b_broken_twice",
                                "b_book_changed")),
         "Reverses a print after the fact. The book never restores shares."),
    ]


def odd_lot_text(log):
    """The paragraph on the odd-lot columns, whichever way they went on this day."""
    other = count(number(log, "t3_other_round_lots"))
    if log["values"].get("t3_odd_lot_columns") != ODD_LOTS_COMPARED:
        return (f"**The odd-lot columns were not compared on this day.** The SEC's method page "
                "gives its round-lot rule only until Nov 3, 2025. After that it uses tiers set "
                "from each security's closing price, which nothing here reads, and the old rule "
                f"is never applied in their place. {other} listed symbols have a directory round "
                "lot other than 100 this day. The shares and trades of T-TRADE-3 are still "
                "compared. What the directory's lot would give is below, for information.")
    return ("An odd lot is a print below the round lot of the SEC's rule, from its method page "
            "for the Market Activity Report: \"Until Nov 3, 2025, a round lot is defined as 100 "
            "shares for all but three tickers. BRK.A and SEB have a round lot of one. BH has a "
            "round lot of 10.\" T-TRADE-3 first failed on odd lots with a fixed 100, on BH and "
            "SEB, and then with the directory's lot, on MKL and NVR, which it lists at 10. This "
            "rule was chosen after those failures, from the SEC's published text, and nothing "
            "was fitted. The page goes on: \"All trades involving nine or fewer shares are "
            "considered odd lot trades.\" That adds nothing at a lot of 100 or 10. At a lot of "
            "one it is not what MIDAS does, as the last column below shows for SEB, so the check "
            f"takes a print below the lot. {other} listed symbols have a directory round lot "
            "other than 100 this day. Those with a MIDAS row are below, as prints / shares, "
            "with what the directory's lot gives kept for information. Every other compared "
            "symbol is 100 on both rules.")


def render(log, entry, day, no_cross_file, failures):
    run = log["run"]
    values = log["values"]
    time = log["time"]
    gib = 1024.0 ** 3
    verdict = {name: log["verdicts"].get(name, ("missing", 0))[0] for name in RESULTS}
    lines = [
        f"# Trade-print verification of {day}",
        "",
        "Generated by `scripts/trades_report.py` from a raw log, which is not committed. Do "
        "not edit by hand.",
        "",
        f"- Commit: {run.get('run_commit', 'unavailable')}",
        f"- Compiler: {run.get('run_compiler', 'unavailable')}",
        f"- Flags: `{run.get('run_flags', 'unavailable')}`",
        f"- Host: {run.get('run_host', 'unavailable')}. Mac numbers stay labelled macOS.",
        f"- Book: the Phase 2 stub, `FastBook`. This run repeats on the real book.",
        f"- Dataset: `{entry['name']}`, SHA-256 of the unpacked file `{entry['unpacked_sha256']}`",
        f"- Command: `{run.get('run_command', 'unavailable')}`",
        f"- Elapsed: {values.get('elapsed_seconds', '?')} s in the replay, "
        f"{time.get('real', 0.0):.1f} s real including any wait for the machine, "
        f"{time.get('user', 0.0):.1f} s user",
        f"- Maximum RSS: {time.get('max_rss', 0) / gib:.2f} GiB, which counts resident pages of "
        "the mapped file",
        f"- Peak memory footprint: {time.get('peak_footprint', 0) / gib:.2f} GiB",
        "",
        "## Results",
        "",
        "Any mismatch fails. No tolerance applies anywhere.",
        "",
    ]
    lines += format_table(("Result", "Verdict", "Violations"), [
        (name, f"**{verdict[name]}**", count(log["verdicts"].get(name, ("", 0))[1]))
        for name in RESULTS])
    odd_lots_compared = values.get("t3_odd_lot_columns") == ODD_LOTS_COMPARED
    if not odd_lots_compared:
        lines += ["", f"T-TRADE-3's odd-lot columns were not compared on {day}: the SEC's "
                  "round-lot rule stops at Nov 3, 2025. Shares and trades were compared."]
    if no_cross_file:
        lines += ["", f"T-TRADE-2 is not available on {day}: no CrossStats file for that day is "
                  "in hand, so the crosses of this day were compared with nothing. The "
                  "per-message checks on `Q` still ran."]
    if failures:
        lines += ["", "## What failed", ""] + [f"- {failure}" for failure in failures]
    if log["violations"]:
        lines += ["", "The violations the tool printed in full. It prints the first of each "
                  "check, so the counts above can be larger.", ""]
        lines += [f"- `{violation}`" for violation in log["violations"]]
    lines += ["", "## Per message type", "",
              "The user's sentence is: every execution hits a resting order at the printed "
              "price and size, and executed volume per symbol matches. This is where it holds "
              "as written, and where a whole class refines it.", ""]
    lines += format_table(("Class", "Messages", "Shares", "Checked", "Level", "Violations",
                           "Against the sentence"), per_type_rows(log))
    tally = log["classes"]
    test_count = sum(row["test_count"] for row in tally.values())
    test_shares = sum(row["test_shares"] for row in tally.values())
    lines += ["", f"Test symbols: {count(test_count)} prints and {count(test_shares)} shares. "
              "They are checked per message, and left out of both external comparisons.", ""]
    lines += ["### `C` price against the resting order", ""]
    prices = log["c_price"]
    lines += format_table(
        ("Printable", "Price equal", "Price different", "Shares equal", "Shares different"),
        [(name, count(prices[key]["equal"]), count(prices[key]["different"]),
          count(prices[key]["equal_shares"]), count(prices[key]["different_shares"]))
         for name, key in (("Y", "Y"), ("N", "N"))])

    lines += ["", "## What is compared with what", ""]
    lines += format_table(("Check", "One side", "Other side", "Why they could disagree"),
                          COMPARISONS)

    lines += ["", "## T-TRADE-1: the order lifecycle", "",
              "For every order: added = E + C + X + closing remainder. An order is one "
              "reference. U ends one and opens another, and the old remainder does not carry "
              "over. The closing remainder comes from the book, at D or U, and is 0 for an "
              "order that ran out.", ""]
    lines += format_table(("Orders", "Count"), [
        ("Opened: A + F + U", count(number(log, "t1_orders_opened"))),
        ("Ran out on E or C", count(number(log, "t1_orders_ran_out_on_execute"))),
        ("Ran out on X", count(number(log, "t1_orders_ran_out_on_cancel"))),
        ("Ended by D", count(number(log, "t1_orders_deleted"))),
        ("Ended by U", count(number(log, "t1_orders_replaced"))),
        ("Live at S/C", count(number(log, "t1_orders_live_end"))),
    ])
    lines += [""]
    lines += format_table(("Shares", "Count"), [
        ("Added", count(number(log, "t1_shares_added"))),
        ("Executed, E + C", count(number(log, "t1_shares_executed"))),
        ("Cancelled, X", count(number(log, "t1_shares_cancelled"))),
        ("Book's remainder at D", count(number(log, "t1_shares_deleted"))),
        ("Book's remainder at U", count(number(log, "t1_shares_replaced_out"))),
    ])

    population = number(log, "t5_population")
    front = number(log, "t5_at_front")
    lines += ["", "## T-TRADE-5: `E` hits the best price", "",
              f"With the market open and the symbol in state `T`: {count(population)} `E`, "
              f"{count(number(log, 't5_at_best'))} at the best price. Hard. At the queue front, "
              f"reported and not gated: {count(front)}, or "
              f"{100.0 * front / population if population else 0.0:.4f}%.", "",
              "The rest, by reason. They are not in the population, so they are not excused: "
              "they are counted here.", ""]
    lines += format_table(("Market open", "State", "E"),
                          [("yes" if is_open == "1" else "no", f"`{state}`", count(rows))
                           for is_open, state, rows in log["outside"]])
    lines += ["", f"Of those, {count(number(log, 't5_outside_at_best'))} were at the best price."]

    if no_cross_file:
        lines += ["", "## T-TRADE-2: `Q` against CrossStats", "", "Not available on this day."]
    else:
        lines += ["", "## T-TRADE-2: `Q` against CrossStats", ""]
        q_o, q_c, q_h = (class_of(log, name) for name in ("Q_O", "Q_C", "Q_H"))
        lines += format_table(("", "Rows or crosses", "Shares"), [
            ("File rows compared, per symbol, exact", count(number(log, "t2_rows_compared")), ""),
            ("File, Opening column", "", count(number(log, "t2_file_opening"))),
            ("File, Closing column", "", count(number(log, "t2_file_closing"))),
            ("Feed `Q` type `O`, test symbols apart", count(q_o["count"]),
             f"{count(q_o['shares'] - q_o['test_shares'])} + {count(q_o['test_shares'])}"),
            ("Feed `Q` type `C`, test symbols apart", count(q_c["count"]),
             f"{count(q_c['shares'] - q_c['test_shares'])} + {count(q_c['test_shares'])}"),
            ("Feed `Q` type `H`, test symbols apart", count(q_h["count"]),
             f"{count(q_h['shares'] - q_h['test_shares'])} + {count(q_h['test_shares'])}"),
            ("`Q` outside the file, which has to equal type `H`", "",
             count(number(log, "t2_outside_file"))),
        ])

    lines += ["", "## `C_N`: what a non-printable execution is", "",
              "Measured first, because no one had confirmed this on real data. The expected "
              "picture, `C_N` as the visible part of a cross, covers only part of the class. "
              "Every `C_N` is one of two things, and the two are told apart by match number.",
              ""]
    lines += format_table(("Kind", "Beside", "Count", "Shares", "Longest lag after the cross"),
                          [("Far leg", f"`{name}`", count(row[0]), count(row[1]), "same instant")
                           for name, row in log["far_legs"].items()]
                          + [("Cross leg", f"`{name}`", count(row[0]), count(row[1]),
                              f"{row[2] / 1e6:.3f} ms")
                             for name, row in log["cross_legs"].items()])
    lines += ["", f"Sides of a cross holding any `C_N`: "
              f"{count(number(log, 'cn_cross_sides_filled'))}, of which "
              f"{count(number(log, 'cn_cross_sides_exact'))} filled the whole cross. On a far "
              "leg the match number is shared with the print, and on a cross leg it is not "
              "shared with the `Q`. Print classes never share a number with each other."]

    lines += ["", "## T-TRADE-3: `E + C_Y + P` against SEC MIDAS", "",
              "MIDAS gives thousands of shares to three decimals, as text written from doubles, "
              "so a value resolves to one share. ITCH gives whole shares. The parser turns the "
              "text into whole shares with no floating point, and refuses a value more than a "
              "millionth of a share from a whole number. Every comparison is then integer "
              "equality, per symbol.", "",
              f"Pinned window: `{log['values'].get('t3_window', '?')}`, half open, "
              "09:35:00 to 16:00:00 in the feed's own timestamps. It is pinned by measurement: "
              "the lead was the SEC's method page for its Market Activity Report, which says "
              "its sample runs from 9:35 and does not say that of `TradeVol`. Each cell counts "
              f"symbols of {count(number(log, 't3_symbols_compared'))} that agree exactly.", "",
              odd_lot_text(log), ""]
    lines += format_table(
        ("Symbol", "Directory lot", "SEC lot", "MIDAS odd lots", "Feed below SEC lot",
         "Feed below directory lot", "Feed below 100", "Feed below 10", "Equal to MIDAS"),
        [(row["symbol"], row["lot"], row["sec_lot"], slash(row["midas"]),
          slash_or_none(row["feed_below_sec"]), slash(row["feed_below_lot"]),
          slash(row["feed_below_100"]), slash(row["feed_below_10"]), lot_rule_that_fits(row))
         for row in log["round_lots"]])
    lines += ["", "The SEC's three entries, and whether this day could exercise them.", ""]
    lines += format_table(
        ("Ticker", "SEC lot", "In the Nasdaq directory", "Has a Nasdaq row in MIDAS"),
        [(row["symbol"], row["lot"], "yes" if row["listed"] == "1" else "no",
          "yes" if row["compared"] == "1" else "no") for row in log["sec_lots"]])
    lines += [""]
    lines += format_table(
        ("Window", "Shares", "Hidden shares", "Trades", "Hidden trades", "Odd lots",
         "Odd-lot shares", "Feed below MIDAS", "Feed shares"),
        [(f"`{name}`", count(fit["equal_shares"]), count(fit["equal_hidden"]),
          count(fit["equal_prints"]), count(fit["equal_hidden_prints"]),
          count(fit["equal_odd_lots"]) if odd_lots_compared else "not compared",
          count(fit["equal_odd_shares"]) if odd_lots_compared else "not compared",
          count(fit["feed_below"]),
          count(fit["feed_shares"]))
         for name, fit in log["fits"].items()])
    edges = log["edges"]
    lines += ["", "How tightly the prints pin it. Equality can say no more than this about "
              "either bound, on symbols MIDAS covers.", ""]
    lines += format_table(("Bound", "Last print before", "First print after"), [
        ("Start", clock(edges.get("last_before", 0)), clock(edges.get("first_inside", 0))),
        ("End", clock(edges.get("last_inside", 0)), clock(edges.get("first_after", 0))),
    ])
    lines += [""]
    lines += format_table(("MIDAS, Nasdaq rows", "Count"), [
        ("Rows that day, of which a second row for a symbol", f"{count(number(log, 't3_rows'))}, "
         f"{count(number(log, 't3_second_rows'))}"),
        ("Symbols compared", count(number(log, "t3_symbols_compared"))),
        ("TradeVol, shares", count(number(log, "t3_midas_trade_shares"))),
        ("HiddenVol, shares", count(number(log, "t3_midas_hidden_shares"))),
        ("Trades", count(number(log, "t3_midas_trades"))),
        ("Hidden trades", count(number(log, "t3_midas_hidden_trades"))),
        ("Listed symbols outside MIDAS, not test symbols", count(number(log, "t3_outside_symbols"))),
        ("Shares of those inside the window", count(number(log, "t3_outside_shares"))),
        ("Test symbols outside MIDAS", count(number(log, "t3_outside_test_symbols"))),
    ])
    lines += ["", "MIDAS covers Stock and ETF names drawn from CRSP, so whether a symbol has a "
              "row cannot be told from the feed. The Nasdaq row count is pinned in the manifest "
              "for that reason: a row lost from the file fails on that pin, and its symbol "
              "would otherwise pass as outside the universe. MIDAS is independent of this code, "
              "not of the feed."]

    lines += ["", "## `B`: broken trades", "",
              f"`B` messages: {count(number(log, 'b_messages'))}, matched to a stored print: "
              f"{count(number(log, 'b_matched'))}, shares reversed: "
              f"{count(number(log, 'b_shares'))}. `E + C_Y + P` net of those over the whole day: "
              f"{count(number(log, 'b_net_printed_shares'))}.", ""]
    for broken in log["breaks"]:
        lines.append(f"- Match {broken['match']}: `{broken['class']}` on {broken['symbol']}, "
                     f"{broken['shares']} shares.")
    if log["breaks"]:
        lines.append("")
    lines += ["Whether MIDAS nets a break out cannot be told from these days unless a break "
              "falls on a symbol it covers. T-TRADE-3 compares before any break."]

    published = PUBLISHED.get(day)
    if published:
        lines += ["", "## Against published figures", "",
                  "Claims, set beside ours and not used as expected values.", ""]
        lines += format_table(
            ("Figure", "Source", "Published", "Ours", "Difference"),
            [(label, source, count(claim), count(our_figure(log, key)),
              f"{our_figure(log, key) - claim:+,}") for label, source, claim, key in published])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--day", required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--no-cross-file", action="store_true")
    parser.add_argument("--manifest", type=Path,
                        default=REPO_ROOT / "scripts" / "data_manifest.json")
    args = parser.parse_args(argv)
    try:
        days = json.loads(args.manifest.read_text(encoding="utf-8"))["days"]
        if args.day not in days:
            raise ReportError(f"no day {args.day} in the manifest")
        entry = days[args.day]
        date = args.day[4:] + args.day[:4]
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        references = manifest.get("references", {})
        midas_rows = references.get("midas_2019q4", {}).get("nasdaq_rows", {}).get(date)
        log = parse_log(args.log.read_text(encoding="utf-8", errors="replace"))
        failures = hard_failures(log, entry, midas_rows, args.no_cross_file, int(date))
        text = render(log, entry, args.day, args.no_cross_file, failures)
    except (OSError, ValueError, KeyError, ReportError) as error:
        print(f"trades report: {error!r}", file=sys.stderr)
        return 2

    for failure in failures:
        print(f"trades report: FAIL {failure}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    if failures:
        return 1
    print(f"trades report: {args.day} every hard check passed, "
          f"{number(log, 'messages_checked')} messages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
