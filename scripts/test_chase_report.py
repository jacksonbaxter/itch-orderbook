#!/usr/bin/env python3
"""chase_report.py on fixture records, whose numbers are invented in the shape of a good run.

Each case breaks one thing and requires that criterion or code. What the event does on a
real CPU can only be shown on debian: results/chase_debian.md is that run.
"""

import contextlib
import copy
import io
import json
import re
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import chase_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "chase"
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
RUNS = (1, 2, 3)
RECORDS = [json.loads((FIXTURES / f"run-{run}.json").read_text(encoding="utf-8")) for run in RUNS]
STDERRS = [(FIXTURES / f"run-{run}.err").read_text(encoding="utf-8") for run in RUNS]
CHECKS = [(FIXTURES / f"check-{run}.out").read_text(encoding="utf-8") for run in RUNS]
SYSFS = (FIXTURES / "sysfs_events.txt").read_text(encoding="utf-8")
PUBLISHED = (FIXTURES / "published.md").read_text(encoding="utf-8")
PREDICTION = "P-0002"


def build(records=None, sysfs=SYSFS):
    return chase_report.render(records or RECORDS, STDERRS, CHECKS, sysfs, PREDICTION)


def failed_criteria(records=None, sysfs=SYSFS):
    text, passed, _ = build(records, sysfs)
    failed = {line.split(" | ")[0].lstrip("| ") for line in text.splitlines()
              if " | FAIL | " in line}
    assert passed == (not failed), failed
    return failed


def with_events(change, runs=RUNS):
    """Records with `change(row, event)` applied, in the given runs."""
    records = copy.deepcopy(RECORDS)
    for position in runs:
        for row in records[position - 1]["rows"]:
            for entry in row["events"]:
                change(row, entry)
    return records


def set_count(name, size, value, runs=RUNS):
    def change(row, entry):
        if entry["name"] == name and row["bytes"] == size:
            entry["read"] = entry["rdpmc"] = value
    return with_events(change, runs)


def codes(text):
    return {code for code, _ in chase_report.check_published(text)}


def replace(text, old, new):
    assert old in text, old
    return text.replace(old, new)


class Verdict(unittest.TestCase):
    def test_the_fixture_passes(self):
        text, passed, run_id = build()
        self.assertTrue(passed)
        self.assertEqual(run_id, "P-0002-20261031T100200Z")
        self.assertIn("**Verdict: PASS**", text)
        self.assertEqual(text, PUBLISHED)

    def test_the_wrong_event_fails_on_its_encoding_alone(self):
        records = copy.deepcopy(RECORDS)
        records[0]["raw_event"] = "0x0943"
        self.assertEqual(failed_criteria(records), {"fill event is PMCx043 unit mask 0x08"})

    def test_fills_under_l2(self):
        self.assertEqual(failed_criteria(set_count("dram_fills", 64 * 1024, 20_000)),
                         {"under L2: fills at most 0.01 per load"})

    def test_too_few_fills_above_l3(self):
        self.assertEqual(failed_criteria(set_count("dram_fills", 256 * 1024 ** 2, 500_000)),
                         {"above L3: fills from 0.90 to 1.10 per load"})

    def test_one_bad_run_fails_even_when_the_median_is_good(self):
        records = set_count("dram_fills", 64 * 1024 ** 2, 500_000, runs=(2,))
        self.assertEqual(failed_criteria(records),
                         {"above L3: fills from 0.90 to 1.10 per load"})

    def test_cache_misses_that_do_not_rise_past_l2(self):
        records = set_count("cache-misses (L2 misses on Zen 1)", 1024 ** 2, 100_000)
        records = copy.deepcopy(records)
        for record in records:
            for entry in record["rows"][3]["events"]:
                if entry["name"] == "l2_misses_raw":
                    entry["read"] = entry["rdpmc"] = 100_000
        self.assertEqual(failed_criteria(records), {
            "between L2 and L3: cache-misses from 0.70 to 1.30 per load",
            "between L2 and L3: cache-misses at least 0.50 above fills"})

    def test_cache_misses_that_track_fills_are_last_level_misses(self):
        records = set_count("dram_fills", 2 * 1024 ** 2, 850_000)
        self.assertEqual(failed_criteria(records),
                         {"between L2 and L3: cache-misses at least 0.50 above fills"})

    def test_rdpmc_and_read_disagreeing(self):
        def change(row, entry):
            if entry["name"] == "dram_fills" and row["bytes"] == 8 * 1024 ** 2:
                entry["rdpmc"] += 2000
        self.assertEqual(failed_criteria(with_events(change, runs=(3,))),
                         {"rdpmc and read() within 0.001 per load, both events"})

    def test_a_multiplexed_event(self):
        def change(row, entry):
            if entry["name"] == "cycles" and row["bytes"] == 1024 ** 2:
                entry["ratio"] = 0.9
        self.assertEqual(failed_criteria(with_events(change)),
                         {"every event ran for the whole span"})

    def test_a_buffer_on_hugepages(self):
        records = copy.deepcopy(RECORDS)
        records[1]["rows"][-1]["pages"] = "4096 bytes per page, 2048 kB in transparent hugepages"
        self.assertEqual(failed_criteria(records), {"every buffer on 4 KB pages"})

    def test_no_account_of_hugepages_is_no_pass(self):
        records = copy.deepcopy(RECORDS)
        records[1]["rows"][-1]["pages"] = "unavailable: no account of hugepages"
        self.assertEqual(failed_criteria(records), {"every buffer on 4 KB pages"})

    def test_the_kernel_mapping_something_else(self):
        sysfs = replace(SYSFS, "umask=0x09", "umask=0x08")
        self.assertEqual(failed_criteria(sysfs=sysfs),
                         {"kernel maps cache-misses to event=0x64,umask=0x09"})
        self.assertIn("do not agree", build(sysfs=sysfs)[0])

    def test_no_sysfs_line_is_no_pass(self):
        self.assertIn("kernel maps cache-misses to event=0x64,umask=0x09",
                      failed_criteria(sysfs="kernel: Linux\n"))

    def test_the_raw_form_differing_from_the_named_event(self):
        records = set_count("l2_misses_raw", 3 * 1024 ** 2, 100_000)
        self.assertEqual(failed_criteria(records),
                         {"r0964:u within 0.001 per load of cache-misses"})

    def test_a_missing_size(self):
        records = copy.deepcopy(RECORDS)
        for record in records:
            del record["rows"][3]
        self.assertEqual(failed_criteria(records), {"every size was run"})

    def test_a_failed_verdict_says_no_number_may_be_published(self):
        self.assertIn("no last-level number may be published",
                      build(set_count("dram_fills", 64 * 1024, 20_000))[0])

    def test_the_machine_hostname_never_reaches_the_file(self):
        self.assertNotIn("some-machine", build()[0])

    def test_runs_that_differ_in_their_header_are_refused(self):
        for field in chase_report.SAME_FIELDS:
            records = copy.deepcopy(RECORDS)
            records[1]["header"][field] = "something else"
            with self.assertRaisesRegex(ValueError, f"the records differ in {field}:"):
                build(records)

    def test_a_failed_machine_check_refuses_the_run(self):
        for broken in ("machine_check: record host: debian\n"
                       "machine_check: FAIL governor: core 4 runs schedutil\n"
                       "machine_check: refused, 1 failed\n",
                       CHECKS[1].replace("machine_check: pass pin-readback",
                                         "machine_check: FAIL pin-readback"),
                       CHECKS[1].replace("machine_check: passed", "")):
            checks = [CHECKS[0], broken, CHECKS[2]]
            with self.assertRaisesRegex(ValueError, "run 2's machine check did not pass"):
                chase_report.render(RECORDS, STDERRS, checks, SYSFS, PREDICTION)


class Published(unittest.TestCase):
    def test_the_fixture_passes(self):
        self.assertEqual(chase_report.check_published(PUBLISHED), [])

    def test_a_faked_verdict(self):
        failing = build(set_count("dram_fills", 64 * 1024, 20_000))[0]
        self.assertIn("verdict-faked", codes(replace(failing, "**Verdict: FAIL**",
                                                     "**Verdict: PASS**")))

    def test_a_row_changed_under_the_verdict(self):
        broken = re.sub(r"^\| 256 MB \| 0\.\d+ ", "| 256 MB | 0.5000 ", PUBLISHED, flags=re.MULTILINE)
        self.assertNotEqual(broken, PUBLISHED)
        self.assertIn("verdict-faked", codes(broken))

    def test_a_criterion_printed_as_passing(self):
        failing = build(set_count("dram_fills", 64 * 1024, 20_000))[0]
        self.assertIn("criterion-faked",
                      codes(re.sub(r"(under L2: fills at most 0\.01 per load) \| FAIL", r"\1 | pass",
                                   failing)))

    def test_a_table_missing(self):
        self.assertEqual(codes(replace(PUBLISHED, "## Worst of the runs", "## Gone")),
                         {"table-missing"})

    def test_the_wrong_host(self):
        self.assertIn("host-label", codes(replace(PUBLISHED, "- host: debian", "- host: unknown")))

    def test_no_commit(self):
        self.assertIn("commit-missing",
                      codes(re.sub(r"^- commit: .*$", "- commit: unknown", PUBLISHED,
                                   flags=re.MULTILINE)))

    def test_a_pin_not_read_back(self):
        self.assertIn("pin-failed", codes(replace(PUBLISHED, "read back [4]", "read back [4,5]")))

    def test_the_wrong_governor(self):
        self.assertIn("governor", codes(replace(PUBLISHED, "- governor: performance",
                                                "- governor: schedutil")))

    def test_a_hostname_in_a_load_line(self):
        self.assertIn("load-line-hostname", codes(replace(PUBLISHED, "host=debian at=2026-10-31T10:02",
                                                          "host=other at=2026-10-31T10:02")))

    def test_a_run_that_failed(self):
        self.assertIn("load-line-invalid", codes(replace(PUBLISHED, "exit=0 at=2026-10-31T10:02",
                                                         "exit=1 at=2026-10-31T10:02")))

    def test_machine_check_not_passing(self):
        self.assertIn("machine-check-failed",
                      codes(PUBLISHED.replace("machine_check: passed", "machine_check: refused")))

    def test_one_run_without_a_passing_machine_check(self):
        last = "machine_check: passed\n```\n\n## measure.sh load lines"
        self.assertIn("machine-check-failed",
                      codes(replace(PUBLISHED, last, last.replace("passed", "refused, 1 failed"))))

    def test_a_failed_line_beside_every_pass(self):
        end = "```\n\n## measure.sh load lines"
        self.assertIn("machine-check-failed",
                      codes(replace(PUBLISHED, end, "machine_check: FAIL governor: schedutil\n"
                                    + end)))

    def test_private_text(self):
        address = ".".join(("100", "64", "0", "1"))
        self.assertIn("private-text", codes(PUBLISHED + address))
        self.assertIn("private-text", codes(PUBLISHED + "/" + "home" + "/someone/"))


class Cli(unittest.TestCase):
    def arguments(self):
        words = ["--record", *(str(FIXTURES / f"run-{run}.json") for run in RUNS),
                 "--load", *(str(FIXTURES / f"run-{run}.err") for run in RUNS),
                 "--machine-check", *(str(FIXTURES / f"check-{run}.out") for run in RUNS)]
        return words + ["--sysfs", str(FIXTURES / "sysfs_events.txt"), "--prediction", PREDICTION]

    def run_main(self, *words):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = chase_report.main(list(words))
        return code, out.getvalue()

    def test_a_report_writes_its_file_and_a_run_log_row(self):
        target = SCRATCH / "chase_report_test.md"
        log = SCRATCH / "chase_report_test_log.md"
        log.unlink(missing_ok=True)
        code, _ = self.run_main(*self.arguments(), "--out", str(target), "--run-log", str(log))
        self.assertEqual(code, 0)
        self.assertEqual(target.read_text(encoding="utf-8"), PUBLISHED)
        self.assertEqual(log.read_text(encoding="utf-8"),
                         "| P-0002-20261031T100200Z | P-0002 | 0123456789ab (exported by "
                         "debian_sync.sh) | Linux | chase verdict PASS |\n")

    def test_a_check_against_the_raw_records_sees_any_change(self):
        code, _ = self.run_main("--check", str(FIXTURES / "published.md"), *self.arguments())
        self.assertEqual(code, 0)
        target = SCRATCH / "chase_report_test_changed.md"
        target.write_text(replace(PUBLISHED, "| 1 MB | between L2 and L3 | 10.02 |",
                                  "| 1 MB | between L2 and L3 | 10.03 |"), encoding="utf-8")
        self.assertEqual(self.run_main("--check", str(target))[0], 0)
        code, out = self.run_main("--check", str(target), *self.arguments())
        self.assertEqual(code, 1)
        self.assertIn("table-differs", out)

    def test_a_failing_verdict_exits_one(self):
        target = SCRATCH / "chase_report_test.json"
        record = copy.deepcopy(RECORDS[0])
        record["raw_event"] = "0x0143"
        target.write_text(json.dumps(record), encoding="utf-8")
        arguments = self.arguments()
        arguments[1] = str(target)
        code, out = self.run_main(*arguments, "--out", str(SCRATCH / "chase_report_fail.md"))
        self.assertEqual(code, 1)
        self.assertIn("verdict FAIL", out)

    def test_a_run_whose_machine_check_failed_is_unusable(self):
        target = SCRATCH / "chase_report_test_check.out"
        target.write_text(replace(CHECKS[1], "machine_check: pass pin-readback",
                                  "machine_check: FAIL pin-readback"), encoding="utf-8")
        arguments = self.arguments()
        arguments[arguments.index("--machine-check") + 2] = str(target)
        out_file = SCRATCH / "chase_report_failed_check.md"
        out_file.unlink(missing_ok=True)
        code, out = self.run_main(*arguments, "--out", str(out_file))
        self.assertEqual(code, 2)
        self.assertIn("run 2's machine check did not pass", out)
        self.assertFalse(out_file.exists())

    def test_a_file_that_fails_its_own_check_is_not_written(self):
        arguments = self.arguments()
        for run in RUNS:
            record = copy.deepcopy(RECORDS[run - 1])
            record["header"]["governor"] = "schedutil"
            target = SCRATCH / f"chase_report_test_governor-{run}.json"
            target.write_text(json.dumps(record), encoding="utf-8")
            arguments[run] = str(target)
        out_file = SCRATCH / "chase_report_governor.md"
        out_file.unlink(missing_ok=True)
        code, out = self.run_main(*arguments, "--out", str(out_file))
        self.assertEqual(code, 2)
        self.assertIn("chase_report: FAIL governor: schedutil", out)
        self.assertFalse(out_file.exists())

    def test_a_refused_run_is_unusable(self):
        target = SCRATCH / "chase_report_test.json"
        record = copy.deepcopy(RECORDS[0])
        record["refusals"] = ["not pinned"]
        target.write_text(json.dumps(record), encoding="utf-8")
        arguments = self.arguments()
        arguments[1] = str(target)
        code, out = self.run_main(*arguments, "--out", str(SCRATCH / "chase_report_fail.md"))
        self.assertEqual(code, 2)
        self.assertIn("a refused run", out)


if __name__ == "__main__":
    unittest.main()
