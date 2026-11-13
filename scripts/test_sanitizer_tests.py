#!/usr/bin/env python3
"""No sanitizer or fuzz test may wait on the system symbolizer, and no test may hang.

On macOS a sanitizer report, and each NEW_FUNC line of libFuzzer, is symbolized by default by
the system symbolizer, which attaches to the process and may wait on a system authorization
dialog. Four things keep a check from stalling on it, and this holds each:

- cmake/ItchSanitizerTests.cmake, called at the end of CMakeLists.txt in a sanitizer build on
  macOS, appends external_symbolizer_path, the llvm-symbolizer beside the compiler or empty
  where there is none, to ASAN_OPTIONS, UBSAN_OPTIONS and TSAN_OPTIONS of every test in every
  directory, after what the test already modifies. Held by configuring a small project of
  NONE language that includes the module (no compiler, a second each), and by the text of
  CMakeLists.txt; configuring a sanitizer preset here would take minutes.
- The test presets' execution block sets a default timeout of 1800 s, so a hang fails; tests
  with a TIMEOUT of their own keep it.
- scripts/fault_matrix.py runs every command, fuzz targets run directly among them, with the
  same options on macOS, and leaves other systems' environment as it is.
- scripts/check.sh sources scripts/symbolizer_env.sh, which sets the same options once in the
  check's own environment on macOS, for every command it runs, inside ctest or not.

The faults tooling-symbolizer-* and tooling-test-timeout-dropped in scripts/faults/tooling.json
break each.
"""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPTIONS = ("ASAN_OPTIONS", "UBSAN_OPTIONS", "TSAN_OPTIONS")
TIMEOUT = 1800


def load_fault_matrix():
    spec = importlib.util.spec_from_file_location("fault_matrix_under_test",
                                                  ROOT / "scripts" / "fault_matrix.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def configure(project, compiler):
    """Configures a project that adds two tests, one in a subdirectory, then calls the module."""
    (project / "sub").mkdir(parents=True)
    module_dir = (ROOT / "cmake").as_posix()
    (project / "CMakeLists.txt").write_text(f"""cmake_minimum_required(VERSION 3.28)
project(symbolizer_probe NONE)
enable_testing()
list(APPEND CMAKE_MODULE_PATH "{module_dir}")
add_test(NAME top COMMAND top_binary)
set_tests_properties(top PROPERTIES ENVIRONMENT_MODIFICATION "PROBE=set:kept")
add_subdirectory(sub)
include(ItchSanitizerTests)
itch_sanitizer_symbolizer(COMPILER "{compiler}")
""", encoding="utf-8")
    (project / "sub" / "CMakeLists.txt").write_text("add_test(NAME below COMMAND below_binary)\n",
                                                   encoding="utf-8")
    build = project / "build"
    subprocess.run(["cmake", "-S", str(project), "-B", str(build)], check=True,
                   capture_output=True, text=True)
    shown = subprocess.run(["ctest", "--test-dir", str(build), "--show-only=json-v1"],
                           check=True, capture_output=True, text=True).stdout
    tests = {}
    for test in json.loads(shown)["tests"]:
        props = {p["name"]: p["value"] for p in test.get("properties", [])}
        tests[test["name"]] = props.get("ENVIRONMENT_MODIFICATION", [])
    return tests


class SymbolizerModule(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="itch-symbolizer-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def expected(self, path):
        return [f"{name}=string_append::external_symbolizer_path={path}" for name in OPTIONS]

    def test_every_test_in_every_directory_names_the_symbolizer_beside_the_compiler(self):
        bin_dir = self.scratch / "toolchain" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "llvm-symbolizer").write_text("", encoding="utf-8")
        tests = configure(self.scratch / "project", (bin_dir / "clang++").as_posix())
        self.assertEqual(sorted(tests), ["below", "top"])
        symbolizer = (bin_dir / "llvm-symbolizer").as_posix()
        self.assertEqual(tests["below"], self.expected(symbolizer))
        self.assertEqual(tests["top"], ["PROBE=set:kept"] + self.expected(symbolizer),
                         "a test's own modifications must stay, before the symbolizer's")

    def test_no_symbolizer_beside_the_compiler_turns_the_external_one_off(self):
        bin_dir = self.scratch / "bare" / "bin"
        bin_dir.mkdir(parents=True)
        tests = configure(self.scratch / "project", (bin_dir / "clang++").as_posix())
        self.assertEqual(tests["below"], self.expected(""))
        self.assertEqual(tests["top"], ["PROBE=set:kept"] + self.expected(""))

    def test_cmakelists_calls_it_last_for_sanitizer_builds_on_macos_only(self):
        text = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        call = re.search(r"\nif\(APPLE AND ITCH_SANITIZE\)\n"
                         r"  itch_sanitizer_symbolizer\(COMPILER \"\$\{CMAKE_CXX_COMPILER\}\"\)\n"
                         r"endif\(\)\n", text)
        self.assertIsNotNone(call, "CMakeLists.txt does not call itch_sanitizer_symbolizer "
                                   "for a sanitizer build on macOS")
        self.assertIn("\ninclude(ItchSanitizerTests)\n", text)
        last_subdirectory = max(m.start() for m in re.finditer(r"add_subdirectory\(", text))
        self.assertGreater(call.start(), last_subdirectory,
                           "the call must come after every test is added")


class TestTimeout(unittest.TestCase):
    def test_every_test_preset_fails_a_test_that_runs_past_the_timeout(self):
        presets = json.loads((ROOT / "CMakePresets.json").read_text(encoding="utf-8"))
        by_name = {p["name"]: p for p in presets["testPresets"]}

        def timeout_of(preset):
            own = preset.get("execution", {}).get("timeout")
            if own is not None:
                return own
            for parent in ([preset["inherits"]] if isinstance(preset.get("inherits"), str)
                           else preset.get("inherits", [])):
                found = timeout_of(by_name[parent])
                if found is not None:
                    return found
            return None

        for name, preset in by_name.items():
            if preset.get("hidden"):
                continue
            with self.subTest(preset=name):
                self.assertEqual(timeout_of(preset), TIMEOUT)


class FaultMatrixEnvironment(unittest.TestCase):
    def setUp(self):
        self.matrix = load_fault_matrix()
        self.scratch = Path(tempfile.mkdtemp(prefix="itch-symbolizer-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.bin_dir = self.scratch / "bin"
        self.bin_dir.mkdir()
        compiler = self.bin_dir / "clang++"
        compiler.write_text("#!/bin/sh\n", encoding="utf-8")
        compiler.chmod(0o755)
        self.compiler = compiler.as_posix()

    def test_on_macos_each_sanitizer_names_the_symbolizer_after_its_own_options(self):
        (self.bin_dir / "llvm-symbolizer").write_text("", encoding="utf-8")
        env = self.matrix.command_environment(
            {"CXX": self.compiler, "ASAN_OPTIONS": "halt_on_error=1"}, "Darwin")
        path = (self.bin_dir / "llvm-symbolizer").as_posix()
        self.assertEqual(env["ASAN_OPTIONS"], f"halt_on_error=1:external_symbolizer_path={path}")
        self.assertEqual(env["UBSAN_OPTIONS"], f":external_symbolizer_path={path}")
        self.assertEqual(env["TSAN_OPTIONS"], f":external_symbolizer_path={path}")

    def test_on_macos_with_no_symbolizer_the_external_one_is_off(self):
        env = self.matrix.command_environment({"CXX": self.compiler}, "Darwin")
        for name in OPTIONS:
            self.assertEqual(env[name], ":external_symbolizer_path=")

    def test_elsewhere_the_environment_is_left_as_it_is(self):
        (self.bin_dir / "llvm-symbolizer").write_text("", encoding="utf-8")
        environ = {"CXX": self.compiler, "ASAN_OPTIONS": "halt_on_error=1"}
        self.assertEqual(self.matrix.command_environment(environ, "Linux"), environ)

    def test_every_command_runs_in_that_environment(self):
        seen = []
        real = self.matrix.command_environment
        self.matrix.command_environment = lambda: (seen.append(1), {**real(), "ITCH_PROBE": "on"})[1]
        log = self.scratch / "log.txt"
        code, output = self.matrix.run_logged(
            [sys.executable, "-c", "import os; print(os.environ.get('ITCH_PROBE'))"],
            self.scratch, log, 60)
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "on")
        self.assertEqual(seen, [1])


class CheckShEnvironment(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="itch-symbolizer-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.bin_dir = self.scratch / "bin"
        self.bin_dir.mkdir()
        compiler = self.bin_dir / "clang++"
        compiler.write_text("#!/bin/sh\n", encoding="utf-8")
        compiler.chmod(0o755)
        self.compiler = compiler.as_posix()

    def options(self, system, asan=None):
        script = (ROOT / "scripts" / "symbolizer_env.sh").as_posix()
        env = {"PATH": "/usr/bin:/bin", "CXX": self.compiler, "ITCH_SYMBOLIZER_SYSTEM": system}
        if asan is not None:
            env["ASAN_OPTIONS"] = asan
        shown = subprocess.run(
            ["bash", "-c", f'source "{script}"; for v in {" ".join(OPTIONS)}; do '
                           'echo "$v=${!v-unset}"; done'],
            env=env, check=True, capture_output=True, text=True).stdout
        return dict(line.split("=", 1) for line in shown.splitlines())

    def test_on_macos_the_check_names_the_symbolizer_after_the_options_set(self):
        (self.bin_dir / "llvm-symbolizer").write_text("", encoding="utf-8")
        path = (self.bin_dir / "llvm-symbolizer").as_posix()
        got = self.options("Darwin", asan="halt_on_error=1")
        self.assertEqual(got["ASAN_OPTIONS"], f"halt_on_error=1:external_symbolizer_path={path}")
        self.assertEqual(got["UBSAN_OPTIONS"], f":external_symbolizer_path={path}")
        self.assertEqual(got["TSAN_OPTIONS"], f":external_symbolizer_path={path}")

    def test_on_macos_with_no_symbolizer_the_check_turns_the_external_one_off(self):
        for name, value in self.options("Darwin").items():
            self.assertEqual(value, ":external_symbolizer_path=", name)

    def test_elsewhere_the_check_leaves_the_options_alone(self):
        (self.bin_dir / "llvm-symbolizer").write_text("", encoding="utf-8")
        got = self.options("Linux", asan="halt_on_error=1")
        self.assertEqual(got, {"ASAN_OPTIONS": "halt_on_error=1", "UBSAN_OPTIONS": "unset",
                               "TSAN_OPTIONS": "unset"})

    def test_check_sh_sources_it_before_any_stage(self):
        text = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
        source = text.find("\nsource scripts/symbolizer_env.sh\n")
        self.assertNotEqual(source, -1, "check.sh does not source scripts/symbolizer_env.sh")
        self.assertLess(source, text.find("\nstage_"), "it must come before the stages")


if __name__ == "__main__":
    unittest.main()
