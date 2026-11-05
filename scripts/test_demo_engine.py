#!/usr/bin/env python3
"""The demo's engine and page, end to end, on an extract cut at test time.

    test_demo_engine.py --release DIR --wasm DIR [--browser PATH]

Writes gen_oracle_fixtures.session(), a seeded random day on six symbols, into a scratch
folder, cuts S1 from it with the release build's itch_extract, then:

  - runs the wasm build's itch_demo.mjs in node on that extract, through the same calls the
    page makes, and holds it to itch_extract's digest, by replay() and by step(), in a linear
    memory of exactly 1 GiB that did not grow;
  - with --browser, puts the page, the engine and the extract in a scratch copy of docs/,
    serves it as scripts/demo.py serves it, and opens the page headless twice: in its bench
    mode, which must post a record with that digest and no refusal, and playing, after which
    it posts the DOM it leaves, which must show the symbol, the book and the tape.

Without --browser the page is not opened, and the output says so.

Exit codes: 0 passed, 1 a check failed, 2 bad usage or a missing build.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo  # noqa: E402
import gen_oracle_fixtures  # noqa: E402
import wasm_bench  # noqa: E402

REPO_ROOT = demo.REPO_ROOT
PAGE_FILES = ("index.html", "demo.css", "demo.js", "numbers.json")

NODE_CHECK = """\
import { readFileSync } from "node:fs";
const [module, extract] = process.argv.slice(2);
const { default: create } = await import(module);
const m = await create();
const bytes = readFileSync(extract);
const memory = m.HEAPU8.length;
const at = m._demo_input(bytes.length);
m.HEAPU8.set(bytes, at);
const digest = () => (m._demo_digest_high() >>> 0).toString(16).padStart(8, "0") +
  (m._demo_digest_low() >>> 0).toString(16).padStart(8, "0");
const status = m._demo_load();
const replayed = m._demo_replay();
const whole = digest();
m._demo_load();
let stepped = 0;
while (m._demo_applied() < m._demo_messages()) stepped += m._demo_step(7);
console.log(JSON.stringify({ status, replayed, stepped, whole, step: digest(),
  prints: m._demo_prints(), memory, memory_after: m.HEAPU8.length,
  too_big: m._demo_input(m._demo_max_input_bytes() + 1) }));
"""


def run(command, **kwargs):
    return subprocess.run([str(word) for word in command], capture_output=True, text=True,
                          check=False, **kwargs)


def cut_extract(release, scratch):
    day = scratch / "day.itch"
    day.write_bytes(gen_oracle_fixtures.session())
    extract = scratch / "extract.itch"
    result = run([release / "tools" / "itch_extract", day, "S1", extract])
    if result.returncode != 0:
        raise SystemExit(f"itch_extract exited {result.returncode}: {result.stderr}")
    summary = json.loads(result.stdout)
    summary.update({"day": "synthetic.itch", "day_sha256": "none, generated at test time",
                    "extract_file": "extract.itch", "extract_sha256": demo.sha256_of(extract)})
    return extract, summary


def check_node(wasm, extract, summary, failures):
    module = wasm / "src" / "itch_demo.mjs"
    script = extract.parent / "check.mjs"
    script.write_text(NODE_CHECK, encoding="utf-8")
    result = run(["node", script, module.resolve().as_uri(), extract])
    if result.returncode != 0:
        failures.append(f"node exited {result.returncode}: {result.stderr.strip()}")
        return
    got = json.loads(result.stdout)
    print(f"node: {json.dumps(got)}")
    want = summary["digest"]
    for name, condition in (
            ("status 0", got["status"] == 0),
            ("every message by replay()", got["replayed"] == summary["messages"]),
            ("every message by step()", got["stepped"] == summary["messages"]),
            (f"replay() digest {got['whole']} is itch_extract's {want}", got["whole"] == want),
            (f"step() digest {got['step']} is itch_extract's {want}", got["step"] == want),
            ("prints read", got["prints"] > 0),
            ("1 GiB of memory", got["memory"] == 1 << 30),
            ("memory did not grow", got["memory_after"] == got["memory"]),
            ("an input past the limit refused", got["too_big"] == 0)):
        if not condition:
            failures.append(f"node: not {name}")


def page_tree(scratch, wasm, extract, summary):
    docs = scratch / "docs"
    local = docs / "local"
    local.mkdir(parents=True)
    for name in PAGE_FILES:
        shutil.copyfile(REPO_ROOT / "docs" / name, docs / name)
    for name in ("itch_demo.mjs", "itch_demo.wasm"):
        shutil.copyfile(wasm / "src" / name, local / name)
    shutil.copyfile(extract, local / "extract.itch")
    (local / "extract.json").write_text(json.dumps(summary), encoding="utf-8")
    return docs


def serving(docs):
    server = demo.make_server(0, wasm_bench.BenchHandler, docs)
    server.quiet = True
    server.result = None
    server.posted = threading.Event()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/index.html"


def posted(browser, docs, query, profile, timeout):
    """What the page posts to /result, or None. The browser is stopped once it has."""
    server, url = serving(docs)
    command = [str(browser), *wasm_bench.BROWSER_FLAGS, f"--user-data-dir={docs.parent / profile}",
               f"{url}{query}"]
    try:
        child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        server.posted.wait(timeout=timeout)
        child.terminate()
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    finally:
        server.shutdown()
        server.server_close()
    return json.loads(server.result) if server.result else None


def check_page(browser, docs, summary, failures):
    record = posted(browser, docs, "?bench=3", "profile-bench", 300)
    if record is None:
        failures.append("page: no record posted")
    else:
        print(f"page bench: {len(record.get('repetitions', []))} replays, digest "
              f"{record.get('digest')}, refusals {record.get('refusals')}")
        if record.get("refusals"):
            failures.append(f"page: refused: {record['refusals']}")
        if record.get("digest") != summary["digest"]:
            failures.append(f"page: digest {record.get('digest')}, not {summary['digest']}")
        if len(record.get("repetitions", [])) != 3:
            failures.append("page: not 3 repetitions")
    played = posted(browser, docs, "?play=1&post=/result", "profile-play", 300)
    dom = played["dom"] if played else ""
    if not played:
        failures.append("page playing: nothing posted")
    for name, pattern in (("the symbol", r'id="symbol">S1<'),
                          ("every message applied",
                           rf'id="progress">{summary["messages"]:,} of {summary["messages"]:,}<'),
                          ("a depth row", r'<td class="bid">\d+\.\d+</td>'),
                          ("a tape row", r'<td class="side-[BS]">\d+\.\d+</td>'),
                          ("the native digest", rf'{summary["digest"]}, the same as the native'),
                          ("the Linux numbers", r'id="linux-numbers"><dl'),
                          ("the macOS numbers or their absence",
                           r'id="wasm-numbers">(?:<dl|<p class="missing">)')):
        if re.search(pattern, dom) is None:
            failures.append(f"page playing: no {name} in the DOM")
    if re.search(r'<section id="setup"[^>]*hidden', dom) is None:
        failures.append("page playing: the set-up instructions are showing")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--wasm", type=Path, required=True)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--scratch", type=Path, default=REPO_ROOT / "build" / "demo-engine")
    args = parser.parse_args(argv)
    for needed in (args.release / "tools" / "itch_extract", args.wasm / "src" / "itch_demo.mjs"):
        if not needed.is_file():
            print(f"test_demo_engine: no {needed}: build the release and wasm presets first")
            return 2
    shutil.rmtree(args.scratch, ignore_errors=True)
    args.scratch.mkdir(parents=True)
    extract, summary = cut_extract(args.release, args.scratch)
    failures = []
    check_node(args.wasm, extract, summary, failures)
    if args.browser is None:
        print("test_demo_engine: the page was not opened: no --browser given")
    else:
        check_page(args.browser, page_tree(args.scratch, args.wasm, extract, summary), summary,
                   failures)
    for failure in failures:
        print(f"test_demo_engine: FAIL {failure}")
    print(f"test_demo_engine: {'FAILED' if failures else 'ok'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
