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
import lint_workflows  # noqa: E402

SCRATCH = gates.REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)

HOME = "/" + "Users" + "/someone/"
LINUX_HOME = "/" + "home" + "/someone/"
TAILNET_ADDRESS = "100." + "64.0.1"
LAN_ADDRESS = "192." + "168.1.20"
VENDOR = "CB" + "OE"
TOOLCHAIN = ".tool" + "chain/"
PINNED = "actions/checkout@" + "0123456789abcdef" * 2 + "01234567"
DUE_TIME = ("The latencies are from the push. No latency from the due time is measured: it needs "
            "the due time in each item, a change to measured code.\n")

CLEAN_FILES = {
    "include/itch/wire.hpp": "#pragma once\nint clean_header();\n",
    "src/parser.cpp": "int clean_source() { return 0; }\n",
    "tools/tool.cpp": "int main() { return 0; }\n",
    "tests/test.cpp": "int clean_test() { return 0; }\n",
    "scripts/tool.py": "print('clean')\n",
    "include/itch/mm/attribution.hpp": ('#pragma once\n#include "itch/mm/fills.hpp"\n'
                                        "// Never reads the ledger.\nint attribute();\n"),
    "include/itch/mm/fills.hpp": "#pragma once\nint fill();\n",
    "docs/NOTES.md": "Notes.\n",
    "BENCHMARKS.md": DUE_TIME,
    "RESULTS.md": DUE_TIME,
    "WRITEUP.md": DUE_TIME,
    "results/run.md": "Host: debian.\n",
    "results/run_log.md": ("# Run log\n\n| Run | Prediction | Commit | OS | Outcome |\n"
                           "|---|---|---|---|---|\n"
                           "| P-0001-x | P-0001 | abc | Darwin | withdrawn: the sampler saw a "
                           "macOS system daemon at 41.2% CPU at 2026-11-05T15:15:58Z |\n"),
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

    def test_a_cast_to_a_struct_or_through_an_alias(self):
        planted = [
            "auto* h = (const Header*)src;\n",
            "auto* h = (Header *) &buf[0];\n",
            "auto* h = (const itch::wire::Header* const)(src + 4);\n",
            "auto w = reinterpret_cast<WordPtr>(p);\n",
            "auto w = reinterpret_cast<const WordPtr>(p);\n",
        ]
        for code in planted:
            with self.subTest(code):
                self.reset()
                self.assert_only_gate_fails("wire-pointer-cast", "include/itch/wire.hpp", code)

    def test_byte_casts_declarations_and_pointer_integers_are_allowed(self):
        allowed = [
            "auto* b = (const char*)src;\n",
            "auto* b = (std::byte *) p;\n",
            "auto n = reinterpret_cast<std::uintptr_t>(p);\n",
            "void f(Header*) const;\n",
            "auto s = sizeof(Header*);\n",
        ]
        for code in allowed:
            with self.subTest(code):
                self.reset()
                self.assert_passes("include/itch/wire.hpp", code)

    def test_a_yaml_workflow_is_read(self):
        self.assert_only_gate_fails("pinned-actions", ".github/workflows/extra.yaml",
                                    "steps:\n  - uses: actions/checkout@v4\n")
        self.reset()
        self.assert_only_gate_fails("toolchain-path", ".github/workflows/extra.yaml",
                                    "run: ~/x/.tool" "chain/env.sh\n")

    def test_actionlint_reads_a_yaml_workflow_too(self):
        self.plant({".github/workflows/extra.yaml": "on: push\n"})
        self.assertEqual(lint_workflows.workflow_files(self.root),
                         [".github/workflows/ci.yml", ".github/workflows/extra.yaml"])

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
            for name in ("scripts/tool.py", "docs/NOTES.md"):
                with self.subTest(path=path, name=name):
                    self.reset()
                    self.assert_only_gate_fails("home-path", name, f"path = {path}data\n")

    def test_relative_paths_are_allowed(self):
        self.assert_passes("docs/NOTES.md", "See data/12302019 and scripts/tool.py.\n")

    def test_private_address(self):
        for address in (TAILNET_ADDRESS, LAN_ADDRESS):
            with self.subTest(address):
                self.reset()
                self.assert_only_gate_fails("private-address", "docs/NOTES.md", f"host {address}\n")

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
        self.assert_passes("docs/NOTES.md", f"The flamegraph names {planted} nowhere.\n")

    def test_public_numbers_are_allowed(self):
        self.assert_passes("docs/NOTES.md", "version 1.18.0, ratio 100.5, address 8.8.8.8\n")

    def test_held_vendor(self):
        for name in ("scripts/tool.py", "src/parser.cpp", "results/table.md.txt"):
            with self.subTest(name):
                self.reset()
                self.assert_only_gate_fails("held-vendor", name, f"name = '{VENDOR.lower()}'\n")

    def test_prose_may_discuss_the_held_vendor(self):
        self.assert_passes("docs/NOTES.md", f"{VENDOR} stays held until the user decides.\n")

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

    def test_the_linux_sampler_rule_holds_every_kind_of_file(self):
        """Every kind of results file on record, made up as a debian run after the Linux
        sampler rule's moment: refused without the rule's line, accepted with a passing one,
        and accepted with none when dated before the rule."""
        sys.path.insert(0, str(gates.REPO_ROOT / "scripts"))
        import check_results  # noqa: PLC0415

        kinds = sorted({path.name.split("_")[0] for path in (gates.REPO_ROOT / "results").glob(
            "*.md") if path.name not in ("run_log.md", "predictions.md")})
        self.assertGreaterEqual(len(kinds), 15, kinds)
        quiet = (f"- load_rule: {check_results.LINUX_RULE_WHOLE}120 samples from "
                 "2026-11-13T04:00:00.000000Z to 2026-11-13T04:02:00.000000Z, the longest gap "
                 "1.02 s; at most 0.10% run-queue wait on core 4, 1.20% busy on cpu5, 3.40% of "
                 "a CPU on the other cores\n")
        busy = quiet.replace("3.40% of a CPU", "45.00% of a CPU")
        for kind in kinds:
            with self.subTest(kind=kind):
                self.reset()
                name = f"results/{kind}_made_up_debian.md"
                header = ("# A made-up file\n\n## Header\n\n- host: debian\n"
                          "- run_id: P-0999-20261113T040000Z\n")
                self.assert_only_gate_fails("linux-sampler-rule", name, header)
                _, output = self.run_gates()
                self.assertIn("load-rule-missing: a debian run from 2026-11-13T03:00:00Z on has "
                              "no load_rule line", output)
                if not f"{kind}_".startswith(check_results.LINUX_RULE_WIRED):
                    self.assertIn(f"no driver of the {kind} kind writes it yet", output)
                self.assert_only_gate_fails("linux-sampler-rule", name, header + busy)
                self.assert_passes(name, header + quiet)
                self.assert_passes(name, header.replace("20261113T040000Z",
                                                        "20261113T025959Z"))
                started = header + "- run_started: 2026-11-13T02:59:59Z\n"
                self.assert_passes(name, started.replace("20261113T040000Z", "x"))
                self.assert_only_gate_fails("linux-sampler-rule", name,
                                            header.replace("20261113T040000Z", "x"))
                self.assert_passes(name, header.replace("host: debian",
                                                        f"host: {check_results.MAC_HOST}"))

    def run_log(self, *outcomes):
        return ("# Run log\n\n| Run | Prediction | Commit | OS | Outcome |\n|---|---|---|---|---|\n"
                + "".join(f"| P-0001-{index} | P-0001 | abc | Darwin | {outcome} |\n"
                          for index, outcome in enumerate(outcomes)))

    # Each form a row has given a process in, with an invented program's name where a kind
    # belongs; then the same forms with kinds, and the shares that are not a process's.
    NAMED_FORMS = (
        "withdrawn: the CPU sampler saw frobd at 41.2% CPU at 2026-11-05T15:15:58Z",
        "withdrawn: the sampler saw 3 other processes above 20% CPU inside its span, the "
        "first `frobd` at 21.7% at 2026-11-10T10:34:28Z",
        "withdrawn: the sampler saw 3 other processes above 20% CPU inside its span, the "
        "first, frobd, at 21.7% at 2026-11-10T10:34:28Z",
        "withdrawn: which saw 2 other processes above 20% CPU inside the run: a macOS system "
        "daemon at 32.4% (19:26:58 UTC); frobd at 22.1% (19:28:01 UTC)",
        "withdrawn: frobd's unit tests were seen at 92% to 100% CPU from 14:34:49 UTC",
        "withdrawn: the host's backup timer ran frobd at up to 193.1% CPU inside its span",
        "withdrawn: 8 other processes, a browser at 66.1% from 19:21:41 UTC and frobd up to 199.2%",
        "withdrawn: the sampler saw this piece's own read (frobd, 28.4%) at its first second",
        "withdrawn: the sampler saw the other cores: 25.00% of a CPU busy at "
        "2026-11-10T10:34:28.000000Z, the most of it frobd",
        # r14-2: forms the grammar did not know, each passed before it was closed.
        "withdrawn: the sampler saw frobd (a browser) at 41.2% CPU at 2026-11-05T15:15:58Z",
        "withdrawn: the sampler saw 3 other processes, the first `frobd`, a browser, at 21.7% at "
        "2026-11-10T10:34:28Z",
        "withdrawn: the sampler saw frobd (41.2% CPU) at 2026-11-05T15:15:58Z",
        "withdrawn: the sampler saw frobd at 41.2 % CPU at 2026-11-05T15:15:58Z",
        "withdrawn: frobd used 41.2% CPU at 2026-11-05T15:15:58Z",
        "withdrawn: frobd took 41.2% of a core at 2026-11-05T15:15:58Z",
        "withdrawn: frobd: 41.2% CPU at 2026-11-05T15:15:58Z",
        "withdrawn: 25.00% of a CPU busy, most of it frobd",
        "withdrawn: the sampler saw a browser inside its span",
        "withdrawn: the sampler saw the core's SMT sibling: 41.00% busy at "
        "2026-11-13T17:00:02.000000Z, the most of it frobd, at 38.5% of a CPU, inside its span",
    )
    KIND_FORMS = (
        "withdrawn: the CPU sampler saw a macOS system daemon at 41.2% CPU at 2026-11-05T15:15:58Z",
        "withdrawn: the sampler saw 3 other processes above 20% CPU inside its span, the "
        "first, the system indexer, at 21.7% at 2026-11-10T10:34:28Z",
        "withdrawn: the sampler saw 2 other busy processes in the run, the first another "
        "process at 50.0% CPU at 2026-11-10T10:34:28Z",
        "withdrawn: which saw 2 other processes above 20% CPU inside the run: a macOS system "
        "daemon at 32.4% (19:26:58 UTC); another assistant session at 22.1% (19:28:01 UTC)",
        "withdrawn: another project's tests were seen at 92% to 100% CPU from 14:34:49 UTC",
        "withdrawn: the host's backup timer ran a backup tool at up to 193.1% CPU inside its span",
        "withdrawn: a backup tool and its file-sync tool at 22% to 165%",
        "withdrawn: 8 other processes, a browser at 66.1% from 19:21:41 UTC and the system "
        "indexer up to 199.2%",
        "withdrawn: the sampler saw a system tool at 28.4% at its first second, this session's "
        "own page-cache read, and a VPN daemon at 27.5% inside it, serving a status poll",
        "withdrawn: the sampler saw a system tool at 44.7% CPU at 2026-11-05T15:14:35Z, this "
        "piece's own read of the day before it, inside its span",
        "withdrawn: the sampler saw the other cores: 25.00% of a CPU busy at "
        "2026-11-10T10:34:28.000000Z, the most of it a kernel thread",
        "withdrawn: the sampler saw the other cores: 30.00% of a CPU busy at "
        "2026-11-10T10:34:29.000000Z, the most of it no process to name, so kernel or "
        "interrupt work",
        "published: paged-flat lookup 8.95 ns, 19.6% of the pass; best look-ahead D=4 at "
        "-18.01% over D=0; E8 refuted (under 10% at every D)",
        "published: hash policy +3.12% (identity-flat over hash-flat); search window of 16 "
        "-0.18% (paged-flat-scan16 over paged-flat, ranges overlap)",
        "published: adds outside a window of 256 ticks 8.337%, of 2,048 1.493% (carteret 4.90% "
        "and 1.03%); sub-cent 0.468%; add searches past 1 or fewer better levels 57.10%, past 16 "
        "or more 10.79%",
        "published: largest single structure level record at 22.7% of the floor on DRAM demand "
        "fills; no structure reaches the 40% rule; 54.5% of those messages name an order",
        # scripts/parse_driver.py's withdrawal (the Linux sampler's words, the kind at its share).
        "withdrawn: T-PARSE-1 to T-PARSE-3, repetition 1, exit 0; the sampler saw the core's SMT "
        "sibling: 41.00% busy at 2026-11-13T17:00:02.000000Z, the most of it another project's "
        "process, at 38.5% of a CPU, inside its span, so the Linux sampler rule does not publish "
        "it",
        "withdrawn: T-PARSE-1 to T-PARSE-3, repetition 2, exit 0; the sampler saw the measured "
        "core: its process waited 7.50% of a second on the run queue at "
        "2026-11-13T17:00:04.000000Z, the most of it no process to name, so kernel or interrupt "
        "work, inside its span",
        "withdrawn: the sampler saw 2 other busy processes in the run, the first a browser at "
        "31.0% CPU at 2026-11-05T09:00:02Z",
        "published: 12 samples from 2026-11-13T17:00:00.000000Z to 2026-11-13T17:00:22.000000Z, "
        "the longest gap 2.00 s; at most 1.00% run-queue wait on core 4, 2.50% busy on cpu10, "
        "40.00% of a CPU on the other cores",
        "withdrawn: no other process above 20% CPU; cpu5 and cpu7 each 100% busy over 1 s",
    )

    def test_run_log_kinds(self):
        for outcome in self.NAMED_FORMS:
            with self.subTest(outcome=outcome):
                self.reset()
                self.assert_only_gate_fails("run-log-kinds", "results/run_log.md",
                                            self.run_log(*self.KIND_FORMS, outcome))
        self.reset()
        self.assert_passes("results/run_log.md", self.run_log(*self.KIND_FORMS))

    def test_the_run_log_is_required(self):
        (self.root / "results" / "run_log.md").unlink()
        code, output = self.run_gates("--only", "run-log-kinds")
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL run-log-kinds: scanned no files", output)

    def test_spsc_due_time_unmeasured(self):
        for name in gates.DUE_TIME_PAGES:
            with self.subTest(name=name):
                self.reset()
                self.assert_only_gate_fails("spsc-due-time-unmeasured", name,
                                            "The latencies are from the push.\n")
                # The sentence with its reason in another paragraph, or none, is not enough.
                self.reset()
                self.assert_only_gate_fails("spsc-due-time-unmeasured", name,
                                            "No latency from the due time is measured.\n\n"
                                            "That needs a change to measured code.\n")
                self.reset()
                self.assert_passes(name, "No figure from the due time was measured, since that "
                                         "needs a change to measured code.\n")

    def test_a_gate_that_scans_nothing_fails(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.plant({"docs/NOTES.md": "Only prose.\n"})
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
