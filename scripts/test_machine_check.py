#!/usr/bin/env python3
"""machine_check.sh on a fake machine, breaking one condition at a time (criterion 29).

Each case changes one thing in a copy of a machine that passes, and requires exactly that
condition to fail, with every other one still passing.
"""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "machine_check.sh"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "machine_check"
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)

GOOD_PROBE = (FIXTURE / "probe_output.txt").read_text(encoding="utf-8")
GOVERNOR = "linux/sys/devices/system/cpu/cpu4/cpufreq/scaling_governor"


def failed(output):
    return re.findall(r"^machine_check: FAIL ([a-z-]+):", output, re.MULTILINE)


class Machine:
    def __init__(self, directory):
        self.root = Path(directory) / "machine"
        shutil.copytree(FIXTURE, self.root)

    def write(self, relative, text):
        (self.root / relative).write_text(text, encoding="utf-8")

    def run(self, os_name="linux", core=4):
        command = ["bash", str(SCRIPT), "--input", str(self.root / "input.bin"),
                   "--probe", str(self.root / "fake_probe.sh"), "--core", str(core),
                   "--sibling", "5", "--os", os_name, "--root", str(self.root / os_name),
                   "--idle-window", "0"]
        return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


class LinuxConditions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.directory.cleanup)
        self.machine = Machine(self.directory.name)

    def assert_only_fails(self, condition):
        result = self.machine.run()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(failed(result.stdout), [condition], result.stdout)
        self.assertIn("machine_check: refused, 1 failed", result.stdout)

    def test_a_good_machine_passes(self):
        result = self.machine.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(failed(result.stdout), [])
        for line in ("pass pin-readback", "pass governor", "pass sibling-idle",
                     "pass input-resident", "pass timer-calibration", "record host: debian"):
            self.assertIn(f"machine_check: {line}", result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], "machine_check: passed")

    def test_no_isolated_core_is_recorded_not_failed(self):
        result = self.machine.run()
        self.assertIn("machine_check: record isolation: none", result.stdout)

    def test_turbo_is_recorded(self):
        self.assertIn("machine_check: record turbo: boost=1", self.machine.run().stdout)

    def test_pin_wider_than_the_core(self):
        self.machine.write("linux/proc/self/status", "Cpus_allowed_list:\t0-7\n")
        self.assert_only_fails("pin-readback")

    def test_pin_on_another_core(self):
        self.machine.write("linux/proc/self/status", "Cpus_allowed_list:\t6\n")
        self.assert_only_fails("pin-readback")

    def test_pin_unreadable(self):
        (self.machine.root / "linux/proc/self/status").unlink()
        self.assert_only_fails("pin-readback")

    def test_wrong_governor(self):
        self.machine.write(GOVERNOR, "schedutil\n")
        self.assert_only_fails("governor")

    def test_governor_unreadable(self):
        (self.machine.root / GOVERNOR).unlink()
        self.assert_only_fails("governor")

    def test_sibling_busy(self):
        self.machine.write("linux/proc/stat.after", "cpu5 150 0 150 850 0 0 0 0 0 0\n")
        self.assert_only_fails("sibling-idle")

    def test_sibling_missing_from_stat(self):
        self.machine.write("linux/proc/stat", "cpu4 1 0 1 8 0 0 0 0 0 0\n")
        self.assert_only_fails("sibling-idle")

    def test_input_not_resident(self):
        self.machine.write("probe_output.txt",
                           GOOD_PROBE.replace("4 of 4 pages", "3 of 4 pages"))
        self.assert_only_fails("input-resident")

    def test_no_quantum(self):
        self.machine.write("probe_output.txt",
                           GOOD_PROBE.replace("timer_quantum_ns: 10.000", "timer_quantum_ns: 0"))
        self.assert_only_fails("timer-calibration")

    def test_input_missing_fails_closed(self):
        (self.machine.root / "input.bin").unlink()
        self.assertIn("input-resident", failed(self.machine.run().stdout))

    def test_probe_missing_fails_closed(self):
        (self.machine.root / "fake_probe.sh").unlink()
        self.assertIn("input-resident", failed(self.machine.run().stdout))

    def test_an_unknown_cpu_is_named_unknown(self):
        self.machine.write("linux/proc/cpuinfo", "model name\t: Some Other CPU\n")
        self.assertIn("machine_check: record host: unknown", self.machine.run().stdout)


class MacConditions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.directory.cleanup)
        self.machine = Machine(self.directory.name)

    def test_records_what_cannot_be_enforced(self):
        result = self.machine.run("darwin")
        self.assertEqual(result.returncode, 0, result.stdout)
        for condition in ("pin", "governor", "turbo", "isolation", "sibling-idle"):
            self.assertIn(f"machine_check: record {condition}: cannot be enforced", result.stdout)
        self.assertIn("machine_check: record host: macOS (M4 Max)", result.stdout)
        self.assertNotIn("pass pin", result.stdout)

    def test_still_enforces_residency(self):
        self.machine.write("probe_output.txt",
                           GOOD_PROBE.replace("4 of 4 pages", "0 of 4 pages"))
        result = self.machine.run("darwin")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(failed(result.stdout), ["input-resident"])

    def test_still_enforces_the_timer(self):
        self.machine.write("probe_output.txt", "residency: 4 of 4 pages resident\n")
        result = self.machine.run("darwin")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(failed(result.stdout), ["timer-calibration"])


if __name__ == "__main__":
    unittest.main()
