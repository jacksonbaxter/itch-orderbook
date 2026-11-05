#!/usr/bin/env python3
"""Each gate in gates.py, shown failing on a planted violation.

Every case plants one violation in a minimal clean tree and requires exactly that gate
to fail, naming the file. The compliant look-alikes beside them stop a gate from
passing these tests by firing on everything. Strings a gate bans are assembled from
pieces here, so that this file does not trip the gates itself.
"""

import contextlib
import getpass
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gates  # noqa: E402

SCRATCH = gates.REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)

HOME = "/" + "Users" + "/someone/"
LINUX_HOME = "/" + "home" + "/someone/"
TAILNET_ADDRESS = "100." + "64.0.1"
LAN_ADDRESS = "192." + "168.1.20"
VENDOR = "CB" + "OE"
TOOLCHAIN = ".tool" + "chain/"
PINNED = "actions/checkout@" + "0123456789abcdef" * 2 + "01234567"

CLEAN_FILES = {
    "include/itch/wire.hpp": "#pragma once\nint clean_header();\n",
    "src/parser.cpp": "int clean_source() { return 0; }\n",
    "tools/tool.cpp": "int main() { return 0; }\n",
    "tests/test.cpp": "int clean_test() { return 0; }\n",
    "scripts/tool.py": "print('clean')\n",
    "include/itch/mm/attribution.hpp": ('#pragma once\n#include "itch/mm/fills.hpp"\n'
                                        "// Never reads the ledger.\nint attribute();\n"),
    "include/itch/mm/fills.hpp": "#pragma once\nint fill();\n",
    "NOTES.md": "Notes.\n",
    "results/run.md": "Host: debian.\n",
    ".github/workflows/ci.yml": f"jobs:\n  a:\n    steps:\n      - uses: {PINNED} # v1.2.3\n",
    "docs/index.html": ("<!doctype html><html><body><h1>Replay <code>09:30</code></h1>\n"
                        "<p>At 09:30:00, 0.01 of a share, 10 of 12 levels.</p>\n"
                        '<button style="border-radius: 2px">Play</button></body></html>\n'),
    "docs/demo.css": (":root { --page: #11151a; }\nbody { background: var(--page); }\n"
                      "button { border-radius: 3px; background: #ffffff; }\n"),
    "docs/demo.js": ('const pad = (v) => String(v).padStart(2, "0");\n'
                     'ctx.fillStyle = "#11151a";\nel.innerHTML = `<h2>Trades</h2>`;\n'),
}

GRADIENT = "linear" + "-gradient"
ITALIC = "ita" + "lic"


class Gates(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.plant(CLEAN_FILES)

    def reset(self):
        for entry in self.root.iterdir():
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
        self.plant(CLEAN_FILES)

    def plant(self, files):
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def run_gates(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gates.main(["--root", str(self.root), *extra])
        return code, out.getvalue()

    def assert_only_gate_fails(self, gate, name, text):
        self.plant({name: text})
        code, output = self.run_gates()
        self.assertEqual(code, 1, output)
        failures = [line for line in output.splitlines() if "gates: FAIL" in line]
        self.assertTrue(failures, output)
        for line in failures:
            self.assertIn(f"FAIL {gate} {name}:", line)

    def assert_passes(self, name, text):
        self.plant({name: text})
        code, output = self.run_gates()
        self.assertEqual(code, 0, output)

    def test_clean_tree_passes(self):
        code, output = self.run_gates()
        self.assertEqual(code, 0, output)

    def test_the_repository_itself_passes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gates.main([])
        self.assertEqual(code, 0, out.getvalue())

    def test_pinned_actions(self):
        name = ".github/workflows/ci.yml"
        for planted in ("actions/checkout@v7 # v7", "actions/checkout@v7.0.1", f"{PINNED}",
                        "actions/checkout@0123456 # v7", f"{PINNED[:-1]} # v7",
                        f"{PINNED[:-1]}G # v7", "docker://alpine:3.20 # 3.20"):
            with self.subTest(planted=planted):
                self.assert_only_gate_fails("pinned-actions", name,
                                            f"steps:\n  - uses: {planted}\n")
                self.reset()
        self.assert_only_gate_fails("pinned-actions", name,
                                    "steps:\n  - name: x\n    uses: 'a/b@v1'\n")
        self.reset()
        self.assert_passes(name, f"steps:\n  - name: x\n    uses: {PINNED} # v7.0.1\n"
                                 "    with:\n      uses-nothing: true\n")

    def test_no_workflow_fails_pinned_actions(self):
        (self.root / ".github" / "workflows" / "ci.yml").unlink()
        code, output = self.run_gates()
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL pinned-actions: scanned no files", output)

    def test_wire_pointer_cast(self):
        planted = [
            "auto v = *reinterpret_cast<const std::uint32_t*>(p);\n",
            "auto v = *reinterpret_cast<std::uint64_t const*>(p);\n",
            "auto v = *reinterpret_cast<const std::uint16_t *>(p);\n",
            "auto& v = reinterpret_cast<const Header&>(p);\n",
            "auto v = *reinterpret_cast<const T*>(p);\n",
            "auto v = *static_cast<const std::uint32_t*>(p);\n",
            "auto v = *(const uint32_t*)p;\n",
            "auto v = *(uint64_t *)p;\n",
            "auto v = *(const unsigned long long*)p;\n",
            "auto v = *(const T*)p;\n",
            "auto* v = std::start_lifetime_as<Header>(p);\n",
        ]
        for code in planted:
            with self.subTest(code):
                self.reset()
                self.assert_only_gate_fails("wire-pointer-cast", "include/itch/wire.hpp", code)

    def test_byte_casts_and_comments_are_allowed(self):
        allowed = [
            "auto* v = reinterpret_cast<const char*>(p);\n",
            "auto* v = reinterpret_cast<const std::byte*>(p);\n",
            "auto* v = static_cast<const std::byte*>(base);\n",
            "auto* v = reinterpret_cast<const std::uint8_t*>(p);\n",
            "// Nothing here uses reinterpret_cast<const std::uint32_t*> on wire bytes.\n",
            'const char* s = "reinterpret_cast<const T*>";\n',
        ]
        for code in allowed:
            with self.subTest(code):
                self.assert_passes("include/itch/wire.hpp", code)

    def test_wire_cast_is_gated_in_tools_too(self):
        self.assert_only_gate_fails("wire-pointer-cast", "tools/tool.cpp",
                                    "auto v = *reinterpret_cast<const int*>(p);\n")

    def test_bare_branch_hint(self):
        planted = [
            "if (x) [[likely]] { y(); }\n",
            "if (x) [[unlikely]] { y(); }\n",
            "if (__builtin_expect(x, 0)) { y(); }\n",
            "__builtin_prefetch(p);\n",
            "// Probably helps.\n__builtin_prefetch(p);\n",
            "if (LIKELY(x)) { y(); }\n",
        ]
        for code in planted:
            with self.subTest(code):
                self.reset()
                self.assert_only_gate_fails("bare-branch-hint", "src/parser.cpp", code)

    def test_a_hint_with_a_measured_reason_is_allowed(self):
        self.assert_passes("src/parser.cpp",
                           "// measured: 4% fewer branch misses, results/x.md\n"
                           "if (x) [[likely]] { y(); }\n")
        self.assert_passes("src/parser.cpp", "__builtin_prefetch(p);  // measured: results/y.md\n")

    def test_a_measured_comment_with_no_reason_is_not_enough(self):
        self.assert_only_gate_fails("bare-branch-hint", "src/parser.cpp",
                                    "// measured:\n__builtin_prefetch(p);\n")

    def test_home_path(self):
        for path in (HOME, LINUX_HOME, "C:" + chr(92) + "Users" + chr(92) + "someone"):
            for name in ("scripts/tool.py", "NOTES.md"):
                with self.subTest(path=path, name=name):
                    self.reset()
                    self.assert_only_gate_fails("home-path", name, f"path = {path}data\n")

    def test_relative_paths_are_allowed(self):
        self.assert_passes("NOTES.md", "See data/12302019 and scripts/tool.py.\n")

    def test_private_address(self):
        for address in (TAILNET_ADDRESS, LAN_ADDRESS):
            with self.subTest(address):
                self.reset()
                self.assert_only_gate_fails("private-address", "NOTES.md", f"host {address}\n")

    def test_private_name(self):
        planted = "some" + "host-name"
        os.environ[gates.PRIVATE_NAMES_ENV] = planted
        self.addCleanup(os.environ.pop, gates.PRIVATE_NAMES_ENV, None)
        for name in ("results/flamegraph_x.svg", "results/run.md"):
            with self.subTest(name=name):
                self.reset()
                self.assert_only_gate_fails("private-name", name,
                                            f"<title>main ({planted.upper()})</title>\n")
        login = getpass.getuser()
        if len(login) >= 4 and login.lower() not in gates.PUBLIC_NAMES:
            self.reset()
            self.assert_only_gate_fails("private-name", "results/run.md", f"by {login}\n")

    def test_a_name_inside_a_longer_word_or_outside_results_is_allowed(self):
        planted = "some" + "host-name"
        os.environ[gates.PRIVATE_NAMES_ENV] = planted
        self.addCleanup(os.environ.pop, gates.PRIVATE_NAMES_ENV, None)
        self.assert_passes("results/run.md", f"x{planted}y and {planted}_z, host debian\n")
        self.assert_passes("NOTES.md", f"The flamegraph names {planted} nowhere.\n")

    def test_public_numbers_are_allowed(self):
        self.assert_passes("NOTES.md", "version 1.18.0, ratio 100.5, address 8.8.8.8\n")

    def test_held_vendor(self):
        for name in ("scripts/tool.py", "src/parser.cpp", "results/table.md.txt"):
            with self.subTest(name):
                self.reset()
                self.assert_only_gate_fails("held-vendor", name, f"name = '{VENDOR.lower()}'\n")

    def test_prose_may_discuss_the_held_vendor(self):
        self.assert_passes("NOTES.md", f"{VENDOR} stays held until the user decides.\n")

    def test_no_conditional_compilation(self):
        for directive in ("#ifdef ITCH_FAULT\n", "#if defined(TESTING)\n", "#  ifndef X\n"):
            with self.subTest(directive):
                self.reset()
                self.assert_only_gate_fails("no-conditional-compilation", "include/itch/wire.hpp",
                                            directive + "#endif\n")

    def test_conditional_compilation_is_a_production_rule_only(self):
        self.assert_passes("tests/test.cpp", "#ifdef X\n#endif\n")

    def test_no_abort_on_wire(self):
        planted = ["assert(x);\n", "throw 1;\n", "std::abort();\n", "__builtin_trap();\n",
                   "auto v = result.value();\n", "auto v = std::get<Add>(m);\n"]
        for code in planted:
            with self.subTest(code):
                self.reset()
                self.assert_only_gate_fails("no-abort-on-wire", "src/parser.cpp", code)

    def test_std_get_if_is_allowed(self):
        self.assert_passes("src/parser.cpp", "auto* v = std::get_if<Add>(&m);\n")

    def test_toolchain_path(self):
        for name in ("scripts/tool.py", "cmake/x.cmake", "CMakePresets.json"):
            with self.subTest(name):
                self.reset()
                self.assert_only_gate_fails("toolchain-path", name, f"p = 'x/{TOOLCHAIN}env.sh'\n")

    def test_attribution_without_ledger(self):
        name = "include/itch/mm/attribution.hpp"
        self.assert_only_gate_fails("attribution-without-ledger", name,
                                    '#pragma once\n#include "itch/mm/ledger.hpp"\nint a();\n')
        self.reset()
        self.assert_only_gate_fails("attribution-without-ledger", name,
                                    "#pragma once\nint a(const Ledger& book);\n")
        self.reset()
        # Through a header the attribution includes.
        self.assert_only_gate_fails("attribution-without-ledger", "include/itch/mm/fills.hpp",
                                    "#pragma once\nlong ledger_total();\n")
        self.reset()
        self.assert_passes(name, '#pragma once\n#include "itch/mm/fills.hpp"\n'
                                 '// Not the Ledger.\nconst char* a = "ledger";\n')
        (self.root / name).unlink()
        code, output = self.run_gates()
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL attribution-without-ledger: scanned no files", output)

    def test_demo_background(self):
        self.assert_only_gate_fails("demo-background", "docs/demo.css",
                                    ":root { --page: #fdf6e3; }\nbody { background: var(--page); }\n")
        self.reset()
        self.assert_only_gate_fails("demo-background", "docs/demo.css",
                                    "body { background-color: ivory; }\n")
        self.reset()
        self.assert_only_gate_fails("demo-background", "docs/index.html",
                                    '<body style="background: #f5f5f0">x</body>\n')
        self.reset()
        self.assert_only_gate_fails("demo-background", "docs/demo.js",
                                    'document.body.style.background = "rgb(250, 246, 236)";\n')

    def test_white_and_dark_backgrounds_are_allowed(self):
        self.assert_passes("docs/demo.css", "body { background: #fff; color: #fafafa; }\n"
                                            "div { background: #1d2127; }\n")

    def test_demo_italic_heading(self):
        self.assert_only_gate_fails("demo-italic-heading", "docs/index.html",
                                    "<h1>The <em>fast</em> book</h1>\n")
        self.reset()
        self.assert_only_gate_fails("demo-italic-heading", "docs/demo.css",
                                    f"h2 span {{ font-style: {ITALIC}; }}\n")
        self.reset()
        self.assert_only_gate_fails("demo-italic-heading", "docs/demo.js",
                                    "el.innerHTML = `<h2>Depth, <i>live</i></h2>`;\n")

    def test_demo_numbered_label(self):
        self.assert_only_gate_fails("demo-numbered-label", "docs/index.html",
                                    "<h2>01 / Replay</h2><h2>02 / Depth</h2>\n")
        self.reset()
        self.assert_only_gate_fails("demo-numbered-label", "docs/demo.css",
                                    'h2::before { content: "03"; }\n')
        self.reset()
        self.assert_only_gate_fails("demo-numbered-label", "docs/demo.css",
                                    "ol { list-style: decimal-leading-zero; }\n")

    def test_demo_pill_button(self):
        self.assert_only_gate_fails("demo-pill-button", "docs/demo.css",
                                    "button { border-radius: 999px; }\n")
        self.reset()
        self.assert_only_gate_fails("demo-pill-button", "docs/index.html",
                                    '<button style="border-radius: 50%">Go</button>\n')
        self.reset()
        self.assert_only_gate_fails("demo-pill-button", "docs/demo.js",
                                    'button.style.borderRadius = "1.5rem";\n')

    def test_demo_gradient(self):
        self.assert_only_gate_fails("demo-gradient", "docs/demo.css",
                                    f"header {{ background-image: {GRADIENT}(#000, #333); }}\n")
        self.reset()
        self.assert_only_gate_fails("demo-gradient", "docs/demo.js",
                                    "const g = ctx.create" + "LinearGradient(0, 0, 0, 9);\n")

    def test_the_demo_page_is_required(self):
        for name in ("docs/index.html", "docs/demo.css", "docs/demo.js"):
            (self.root / name).unlink()
        code, output = self.run_gates()
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL demo-gradient: scanned no files", output)

    def test_the_demo_local_folder_is_not_scanned(self):
        self.plant({"docs/local/itch_demo.mjs": f"x = '{GRADIENT}(';\n{HOME}\n"})
        code, output = self.run_gates()
        self.assertEqual(code, 0, output)

    def test_a_gate_that_scans_nothing_fails(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.plant({"NOTES.md": "Only prose.\n"})
        code, output = self.run_gates()
        self.assertEqual(code, 1)
        self.assertIn("FAIL wire-pointer-cast: scanned no files", output)

    def test_build_output_and_data_are_not_scanned(self):
        self.plant({"build/release/x.cpp": f"{HOME}\n", "data/x.txt": f"{VENDOR}\n"})
        code, output = self.run_gates()
        self.assertEqual(code, 0, output)

    def test_a_worktree_git_file_is_not_scanned(self):
        self.plant({".git": f"gitdir: {HOME}repo/.git/worktrees/w\n"})
        code, output = self.run_gates()
        self.assertEqual(code, 0, output)

    def test_binary_files_are_skipped(self):
        (self.root / "fuzz").mkdir()
        (self.root / "fuzz" / "seed").write_bytes(b"\x00" + HOME.encode())
        code, output = self.run_gates()
        self.assertEqual(code, 0, output)

    def test_unknown_gate_name_is_a_usage_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.run_gates("--only", "nothing")[0], 2)


if __name__ == "__main__":
    unittest.main()
