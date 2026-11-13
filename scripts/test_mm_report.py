#!/usr/bin/env python3
"""mm_report.py on tool output written here, every identity worked by hand: what it must render,
and what it must refuse. The committed evaluation is the matrix throughout."""

import contextlib
import io
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mm_report as report  # noqa: E402

REPO_ROOT = report.REPO_ROOT
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
EVALUATION = report.load_evaluation(REPO_ROOT)
OUT = report.default_out(EVALUATION)
TICK = 10**10
HEADER = ["# The market maker", "", "- prediction: P-0202", "- run_id: P-0202-20261105T000000Z",
          "- Raw folder: `mm2/`, one `.out` and one `.err` a run"]


def figures(symbol, run):
    """A run's amounts in picodollars, different in every run and consistent by construction."""
    index = list(EVALUATION["symbols"]).index(symbol) * 10 + [
        r["name"] for r in EVALUATION["runs"]].index(run["name"])
    fills = 100 + index
    shares = fills * 100
    gross = shares * 3 * TICK
    fees = -shares * TICK // 20
    adverse = -shares * (4 * TICK + index * TICK // 100)
    inventory = (index - 7) * 50 * TICK
    return {"fills": fills, "shares": shares, "gross": gross, "fees": fees, "adverse": adverse,
            "inventory": inventory}


def output(symbol, run, **changes):
    """itch_mm's output for one committed run, then the two lines of time -l, as the report
    keeps them. changes replaces a figure, or a line by its first word with a string."""
    fit = EVALUATION["symbols"][symbol]
    f = figures(symbol, run) | {k: v for k, v in changes.items() if k in figures(symbol, run)}
    capture = f["gross"] - f["fees"]
    total = capture + f["adverse"] + f["inventory"]
    held = f["adverse"] + f["inventory"]
    bought = f["shares"] // 2 + 50
    lines = {
        "mm": f"mm symbol {symbol} model {run['model']} queue {run['queue']} latency_off "
              f"{1 if run['latency_off'] else 0} gamma {fit['gamma']} sigma {fit['sigma']} k "
              f"{fit['k']} a {fit['a']} lot 100 max_lots 5 start_ns 35100000000000 stop_ns "
              "56700000000000 close_ns 57600000000000 date 20191230",
        "market": "market maker attribution, amounts in picodollars",
        "window_open_ns": "window_open_ns 35100000000000",
        "window_close_ns": "window_close_ns 57600000000000",
        "opening_position": "opening_position 0",
        "fills": f"fills {f['fills']}", "shares": f"shares {f['shares']}",
        "closing_position": "closing_position 100",
        "mark_points": "mark_points 52846",
        "mark_hash": f"mark_hash {'1aa1e2ecc0364496' if symbol == 'AMD' else '00c0ffee00c0ffee'}",
        "mark_held_ns": "mark_held_ns 3174399903362", "mark_overflows": "mark_overflows 0",
        "horizon_ns": "horizon_ns 15000000000",
        "gross_spread": f"gross_spread {f['gross']}", "fees": f"fees {f['fees']}",
        "spread_capture": f"spread_capture {capture}",
        "adverse_selection": f"adverse_selection {f['adverse']}",
        "inventory": f"inventory {f['inventory']}", "total": f"total {total}",
        "ledger_total": f"ledger_total {total}", "identity": "identity holds",
    }
    body = list(lines.values())
    for horizon, shift in zip(report.MARKOUT_GRID, (3, 2, 0, -1, 1), strict=True):
        moved = f["adverse"] + shift * f["shares"] * TICK // 1000
        body.append(f"markout_ns {horizon} adverse_selection {moved} inventory {held - moved}")
    body += [f"bought {bought}", f"sold {f['shares'] - bought}", "largest_position 500",
             "notional 12038639240000000000",
             "messages 268744780 deliveries 342596 out_of_order 0",
             f"engine fills {f['fills']} shares {f['shares']} maker {f['fills'] - 2} taker 2 "
             "crossing 9",
             "queue_anomalies 0 late_adds_ahead 0 hidden_prints_at_sim_price 736",
             "strategy sends 4218 replaces 51230 cancels 3056 rejects 0 unpriced 0",
             "allocations_after_construction 0"]
    body += [f"check {name}: pass" for name in report.CHECK_LINES]
    body += ["16.60 real        16.09 user         0.50 sys",
             "10194993152  maximum resident set size"]
    for key, value in changes.items():
        if isinstance(value, str):
            body = [value if line.split()[0] == key else line for line in body]
    return "\n".join(body) + "\n"


def outputs(**changes_by_run):
    """Every run's output; changes_by_run maps 'SYMBOL/run name' to output()'s changes."""
    return {(symbol, run["name"]): output(symbol, run,
                                          **changes_by_run.get(f"{symbol}/{run['name']}", {}))
            for symbol, run in report.matrix(EVALUATION)}


def violations(text):
    return report.check_text(text, EVALUATION, OUT)


def codes(text):
    return {code for code, _, _ in violations(text)}


class Render(unittest.TestCase):
    def setUp(self):
        self.text = report.render(EVALUATION, outputs(), HEADER)

    def test_a_good_file_holds(self):
        self.assertEqual(violations(self.text), [])

    def test_amounts_in_dollars_per_share_and_ticks(self):
        # AMD, AS Base: 100 fills, 10,000 shares; S gross 3 ticks a share = $300.00, fees
        # −0.05 ticks a share = −$5.00, so S = $305.00 and 3.05 ticks; A −4 ticks = −$400.00;
        # I (0 − 7)·50 ticks = −$3.50; total −$98.50, −0.0099 $ a share, −0.985 ticks.
        self.assertIn("| AS Base | +300.00 | -5.00 | +305.00 | -400.00 | -3.50 | -98.50 |",
                      self.text)
        self.assertIn("| AS Base | +0.0300 | -0.0005 | +0.0305 | -0.0400 | -0.0004 | -0.0099 |",
                      self.text)
        self.assertIn("| AS Base | +3.000 | -0.050 | +3.050 | -4.000 | -0.035 | -0.985 |",
                      self.text)

    def test_the_markout_curve_has_five_horizons(self):
        self.assertIn("| Run | 50 ms | 1 s | 15 s | 1 min | 5 min |", self.text)
        # A(h) = A + shift ticks over 10,000 shares: shifts 3, 2, 0, −1, 1.
        self.assertIn("| AS Base | -3.997 | -3.998 | -4.000 | -4.001 | -3.999 |", self.text)

    def test_each_switch_is_one_row(self):
        # Q-off conservative is run index 1 on AMD: 101 fills, 10,100 shares.
        self.assertIn("| Q-off conservative, latency on | 100 → 101 | 10,000 → 10,100 |",
                      self.text)
        self.assertIn("| GLFT against AS, both Base | 100 → 106 |", self.text)

    def test_the_fixed_sentences_and_labels(self):
        self.assertIn("One symbol-day. No edge is claimed. 2 symbols, AMD and BYND, are traded, "
                      "each on one day, 12302019", self.text)
        self.assertIn("Tuned on 10302019, an FOMC day; reported on 12302019, a late-December "
                      "session: out of sample by day.", self.text)
        self.assertIn(report.NO_IMPACT, self.text)
        self.assertIn("the base tier of the 2019-12-30 schedule", self.text)

    def test_the_command_carries_every_committed_figure_exactly(self):
        self.assertIn("--sigma 2.592559 --k 0.243074 --a 0.170163", self.text)
        self.assertIn("--queue proportional --latency-off`", self.text)

    def test_peak_memory_is_shown(self):
        self.assertIn("| 10,194,993,152 | 9.49 | 16.60 |", self.text)

    def test_sigma_is_in_ticks_per_root_second(self):
        # itch_calibrate fits sigma in ticks and seconds, and the gamma rule only works out in
        # ticks: 1 / (0.727768^2 * 21,600 s) = 8.741e-5, AMD's committed gamma.
        self.assertIn("| Symbol | Group | σ, ticks/√s | k, 1/tick | A, 1/s | γ |", self.text)
        self.assertNotIn("$/√s", self.text)


    def test_a_switch_that_moves_nothing_says_so(self):
        same = outputs()
        same[("AMD", "AS both off, conservative")] = same[("AMD", "AS L-off")].replace(
            "queue exact", "queue conservative")
        text = report.render(EVALUATION, same, HEADER)
        self.assertIn("- Q-off conservative, latency off (AS L-off to AS both off, conservative): "
                      "the total moves by $0.00, from -$102.94 to -$102.94; none of S, A and I "
                      "moves.", text)
        self.assertIn("- L-off, exact queue (AS Base to AS L-off): the total moves by -$4.44, "
                      "from -$98.50 to -$102.94; of S, A and I, A moves most.", text)


class Refuse(unittest.TestCase):
    def test_a_figure_typed_over_in_the_body(self):
        text = report.render(EVALUATION, outputs(), HEADER)
        text = text.replace("| AS Base | +300.00 |", "| AS Base | +301.00 |", 1)
        self.assertEqual(codes(text), {"differs-from-output"})

    def test_a_total_that_is_not_s_plus_a_plus_i(self):
        changed = outputs(**{"BYND/AS L-off": {"total": "total 5"}})
        self.assertIn("components-not-total", codes(report.render(EVALUATION, changed, HEADER)))

    def test_a_ledger_that_differs(self):
        changed = outputs(**{"AMD/AS Base": {"ledger_total": "ledger_total 7"}})
        self.assertIn("total-not-ledger", codes(report.render(EVALUATION, changed, HEADER)))

    def test_s_that_is_not_gross_less_fees(self):
        changed = outputs(**{"AMD/AS Base": {"spread_capture": "spread_capture 7"}})
        self.assertIn("s-not-gross-less-fees", codes(report.render(EVALUATION, changed, HEADER)))

    def test_a_markout_that_moves_the_total(self):
        changed = outputs(**{"AMD/GLFT Base": {
            "markout_ns": "markout_ns 50000000 adverse_selection 1 inventory 1"}})
        found = codes(report.render(EVALUATION, changed, HEADER))
        self.assertIn("markout-not-total", found)
        self.assertIn("markout-grid", found)

    def test_a_second_mark_path(self):
        changed = outputs(**{"BYND/AS Q-off proportional": {"mark_hash": "mark_hash 1"}})
        self.assertIn("mark-path-differs", codes(report.render(EVALUATION, changed, HEADER)))

    def test_a_parameter_not_committed(self):
        line = output("AMD", EVALUATION["runs"][0]).splitlines()[0].replace(
            "sigma 0.727768", "sigma 0.72777")
        changed = outputs(**{"AMD/AS Base": {"mm": line}})
        found = violations(report.render(EVALUATION, changed, HEADER))
        self.assertIn(("parameter-not-committed", "AMD, AS Base", "sigma is 0.72777, not 0.727768"),
                      found)

    def test_a_run_on_another_day(self):
        line = output("AMD", EVALUATION["runs"][0]).splitlines()[0].replace(
            "date 20191230", "date 20191030")
        changed = outputs(**{"AMD/AS Base": {"mm": line}})
        self.assertIn("parameter-not-committed", codes(report.render(EVALUATION, changed, HEADER)))

    def test_a_failed_tool_check(self):
        changed = outputs()
        key = ("AMD", "AS L-off")
        changed[key] = changed[key].replace("check allocations: pass", "check allocations: FAIL")
        found = violations(report.render(EVALUATION, changed, HEADER))
        self.assertEqual(found, [("tool-check-failed", "AMD, AS L-off",
                                  "check allocations: FAIL")])

    def test_a_failed_check_the_report_does_not_know(self):
        changed = outputs()
        key = ("AMD", "AS Base")
        changed[key] = changed[key].replace("check identity: pass",
                                            "check identity: pass\ncheck queue position sane: FAIL")
        found = violations(report.render(EVALUATION, changed, HEADER))
        self.assertIn(("tool-check-failed", "AMD, AS Base", "check queue position sane: FAIL"),
                      found)
        self.assertIn(("tool-checks-not-committed", "AMD, AS Base",
                       "no check -; a check not committed: queue position sane"), found)

    def test_a_passing_check_the_report_does_not_know(self):
        changed = outputs()
        key = ("BYND", "GLFT Base")
        changed[key] = changed[key].replace("check identity: pass",
                                            "check identity: pass\ncheck something new: pass")
        self.assertEqual(violations(report.render(EVALUATION, changed, HEADER)),
                         [("tool-checks-not-committed", "BYND, GLFT Base",
                           "no check -; a check not committed: something new")])

    def test_a_committed_check_missing(self):
        changed = outputs()
        key = ("AMD", "AS L-off")
        changed[key] = changed[key].replace("check no overflow: pass\n", "")
        self.assertEqual(violations(report.render(EVALUATION, changed, HEADER)),
                         [("tool-checks-not-committed", "AMD, AS L-off",
                           "no check no overflow; a check not committed: -")])

    def test_two_lines_for_one_check_are_unusable(self):
        text = output("AMD", EVALUATION["runs"][0]).replace(
            "check allocations: pass", "check allocations: FAIL\ncheck allocations: pass")
        with self.assertRaises(report.ReportError):
            report.parse_output(text)

    def test_an_identity_the_tool_says_fails(self):
        changed = outputs(**{"AMD/AS L-off": {"identity": "identity FAILS"}})
        self.assertIn("identity-fails", codes(report.render(EVALUATION, changed, HEADER)))

    def test_a_missing_run(self):
        text = report.render(EVALUATION, outputs(), HEADER)
        start = text.index("### BYND, GLFT Base\n\n```text")
        text = text[:start] + "### BYND, GLFT Bas\n" + text[start + len("### BYND, GLFT Base"):]
        self.assertEqual(codes(text), {"matrix-not-committed"})

    def test_the_fixed_sentence_removed(self):
        text = report.render(EVALUATION, outputs(), HEADER).replace(report.FIXED, "One day.")
        self.assertIn("sentence-missing", codes(text))

    def test_a_sharpe_ratio(self):
        text = report.render(EVALUATION, outputs(), HEADER).replace(
            "## Runs\n", "## Runs\n\nSharpe 3.1.\n")
        self.assertIn("return-or-sharpe", codes(text))

    def test_a_return(self):
        text = report.render(EVALUATION, outputs(), HEADER).replace(
            "## Runs\n", "## Runs\n\nThe return on capital.\n")
        self.assertIn("return-or-sharpe", codes(text))

    def test_no_prediction(self):
        text = report.render(EVALUATION, outputs(), HEADER).replace("- prediction: P-0202\n", "")
        self.assertEqual(codes(text), {"header-field-missing"})

    def test_a_refused_attribution_is_unusable(self):
        with self.assertRaises(report.ReportError):
            report.parse_output("mm symbol AMD\nattribution refused: no mark at the open\n")


class RenderAgain(unittest.TestCase):
    def test_render_mends_a_typed_over_body_from_the_file_alone(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        path = Path(scratch.name) / "mm_pnl.md"
        good = report.render(EVALUATION, outputs(), HEADER)
        path.write_text(good.replace("| AS Base | +300.00 |", "| AS Base | +301.00 |", 1),
                        encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            code = report.main(["render", str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(path.read_text(encoding="utf-8"), good)


class RenderFromRaw(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.raw = self.root / "mm2"
        self.raw.mkdir()
        self.outputs = outputs()
        for symbol, run in report.matrix(EVALUATION):
            lines = self.outputs[(symbol, run["name"])].splitlines()
            name = report.raw_name(EVALUATION, symbol, run)
            (self.raw / f"{name}.out").write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")
            (self.raw / f"{name}.err").write_text(
                "heavy.sh: start\n       " + lines[-2] + "\n         " + lines[-1] +
                "\n                   0  average shared memory size\n", encoding="utf-8")
        self.path = self.root / "mm_pnl.md"

    def render(self, text, raw):
        self.path.write_text(text, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            return report.main(["render", str(self.path), "--raw-dir", str(raw)])

    def test_the_tool_output_comes_from_the_raw_records(self):
        good = report.render(EVALUATION, self.outputs, HEADER)
        stale = report.render(EVALUATION, outputs(**{"AMD/AS Base": {"fills": 7}}), HEADER)
        self.assertEqual(self.render(stale, self.raw), 0)
        self.assertEqual(self.path.read_text(encoding="utf-8"), good)

    def test_a_file_rendered_again_says_so_once(self):
        good = report.render(EVALUATION, self.outputs, ["# The market maker", "",
                                                         "Generated by mm_report.", *HEADER[1:]])
        self.path.write_text(good, encoding="utf-8")
        for reason in ("first.", "second."):
            with contextlib.redirect_stdout(io.StringIO()):
                code = report.main(["render", str(self.path), "--raw-dir", str(self.raw),
                                    "--rendered-again", reason])
            self.assertEqual(code, 0)
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text, good.replace("Generated by mm_report.\n",
                                            f"Generated by mm_report.\n\n{report.AGAIN}second.\n"))

    def test_a_raw_folder_the_header_does_not_name(self):
        other = self.root / "mm1"
        self.raw.rename(other)
        good = report.render(EVALUATION, self.outputs, HEADER)
        self.assertEqual(self.render(good, other), 2)
        self.assertEqual(self.path.read_text(encoding="utf-8"), good)


class TimeLines(unittest.TestCase):
    def test_the_wall_time_and_peak_are_kept(self):
        stderr = ("heavy.sh: start\n       16.60 real        16.09 user         0.50 sys\n"
                  "         10194993152  maximum resident set size\n"
                  "                   0  average shared memory size\n")
        self.assertEqual(report.time_lines(stderr),
                         ["16.60 real        16.09 user         0.50 sys",
                          "10194993152  maximum resident set size"])

    def test_no_peak_is_refused(self):
        with self.assertRaises(report.ReportError):
            report.time_lines("       16.60 real        16.09 user         0.50 sys\n")


FAKE_TOOL = """#!/usr/bin/env python3
import sys
sys.path.insert(0, {scripts!r})
import test_mm_report as t
args = sys.argv[1:]
value = dict(zip(args[1::2], args[2::2]))
run = {{"model": value["--model"], "queue": value["--queue"],
       "latency_off": "--latency-off" in args}}
run["name"] = next(r["name"] for r in t.EVALUATION["runs"]
                   if all(r[key] == run[key] for key in ("model", "queue", "latency_off")))
text = t.output(value["--symbol"], run)
print("\\n".join(text.splitlines()[:-2]))
"""

FAKE_TIME = """#!/bin/sh
shift
"$@"
code=$?
echo "       16.60 real        16.09 user         0.50 sys" >&2
echo "         10194993152  maximum resident set size" >&2
exit $code
"""


class Run(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        shutil.copytree(REPO_ROOT / "results", self.root / "results")
        (self.root / OUT).unlink(missing_ok=True)  # Each test's run writes its own.
        (self.root / "scripts").mkdir()
        for name in ("mm_evaluation.json", "check_predictions.py", "oracle_report.py"):
            shutil.copy(REPO_ROOT / "scripts" / name, self.root / "scripts" / name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.tool = self.write("itch_mm", FAKE_TOOL.format(scripts=str(Path(__file__).parent)))
        self.time = self.write("time", FAKE_TIME)

    def write(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def run_report(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = report.main(list(args))
        return code, out.getvalue()

    def test_the_matrix_runs_and_its_file_holds(self):
        code, said = self.run_report(
            "run", "--tool", str(self.tool), "--data", "day.itch", "--raw-dir", str(self.raw),
            "--prediction", "P-0202", "--time", str(self.time), "--root", str(self.root))
        self.assertEqual(code, 0, said)
        self.assertEqual(len(list(self.raw.glob("*.out"))), 14)
        self.assertTrue((self.raw / "mm_12302019_BYND_as_both_off_proportional.err").is_file())
        code, said = self.run_report("check", str(self.root / OUT), "--root", str(self.root))
        self.assertEqual(code, 0, said)
        log = (self.root / "results" / "run_log.md").read_text(encoding="utf-8")
        self.assertRegex(log, r"\| P-0202-\d{8}T\d{6}Z \| P-0202 \| .* \| published \|\n$")

    def test_an_unknown_prediction_runs_nothing(self):
        code, said = self.run_report(
            "run", "--tool", str(self.tool), "--data", "day.itch", "--raw-dir", str(self.raw),
            "--prediction", "P-9999", "--time", str(self.time), "--root", str(self.root))
        self.assertEqual(code, 1, said)
        self.assertEqual(list(self.raw.iterdir()), [])

    def test_an_exit_status_the_check_lines_do_not_explain(self):
        # itch_mm exits 1 when a check failed; an exit 1 with every check passing, or an
        # exit 0 with a failed check, is refused before anything is published.
        for wrapped, code in (("exit 1", 1), ("sed 's/check no overflow: pass/check no "
                                               "overflow: FAIL/'", 0)):
            with self.subTest(wrapped=wrapped):
                inner = self.write("itch_mm_inner", FAKE_TOOL.format(
                    scripts=str(Path(__file__).parent)))
                pipe = f" | {wrapped}" if code == 0 else f"; {wrapped}"
                self.write("itch_mm", f"#!/bin/sh\n{inner} \"$@\"{pipe}\n")
                said = self.run_report(
                    "run", "--tool", str(self.tool), "--data", "day.itch", "--raw-dir",
                    str(self.raw), "--prediction", "P-0202", "--time", str(self.time), "--root",
                    str(self.root))
                self.assertEqual(said[0], 2, said[1])
                self.assertIn(f"exited {code}, but", said[1])
                self.assertFalse((self.root / OUT).exists())

    def test_a_tool_that_fails_is_logged_and_refused(self):
        self.write("itch_mm", "#!/bin/sh\necho 'itch_mm: cannot read day.itch' >&2\nexit 2\n")
        code, said = self.run_report(
            "run", "--tool", str(self.tool), "--data", "day.itch", "--raw-dir", str(self.raw),
            "--prediction", "P-0202", "--time", str(self.time), "--root", str(self.root))
        self.assertEqual(code, 2, said)
        log = (self.root / "results" / "run_log.md").read_text(encoding="utf-8")
        self.assertIn("refused at AMD, AS Base: exited 2", log)
        self.assertFalse((self.root / OUT).exists())


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
