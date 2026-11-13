#!/usr/bin/env python3
"""The demo page's local steps: build its engine, cut its extract, serve it, and write the
numbers it shows.

    demo.py build   [--build-dir DIR]
    demo.py extract --day FILE --symbol SYMBOL [--tool PATH] [--root DIR]
    demo.py serve   [--port N]
    demo.py numbers [--check] [--root DIR]

build    builds the wasm preset's itch_demo with em++ (EMSDK must be set), -O3 -msimd128 in a
         fixed 1 GiB memory, and copies itch_demo.mjs and itch_demo.wasm into docs/local/,
         with engine.json naming the commit they came from and the .wasm's SHA-256.
extract  runs tools/itch_extract, from the release preset, on a day already fetched by
         scripts/fetch_data.py, and writes docs/local/extract.itch and extract.json. The
         extract is Nasdaq's data, cut from its sample file: docs/local/ is gitignored, and
         nothing in it is committed or deployed. extract.json's day_sha256 is hashed from the
         day file itch_extract reads, never copied from scripts/data_manifest.json, and a day
         that hashes to anything but the manifest's pin for it is refused (r5-6);
         day_sha256_of says so. --root writes under DIR's docs/local/ instead.
serve    serves docs/ on 127.0.0.1 with the standard library alone, adding the headers that
         make the page cross-origin isolated. The page needs a server: file:// cannot load
         its module.
numbers  writes docs/numbers.json from results/: the native Linux numbers from `debian`'s
         results file for the arm that ships, and T-WASM-1 from this Mac's, each marked
         missing until its file exists. A Linux slot takes only a file whose header says
         debian on Linux. --check fails if docs/numbers.json differs from a fresh run.

Exit codes: 0 done, 1 a step failed or --check found a difference, 2 bad usage.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = "docs"
LOCAL = "docs/local"
NUMBERS = "docs/numbers.json"
# The arm that ships, with the day read into memory: the debian run behind T-BOOK-1.
LINUX_SOURCE = "bench_book_paged-flat_ram_debian_*.md"
WASM_SOURCE = "wasm_replay_mac_*.md"
LINUX_HOST = "debian"
MAC_HOST = "macOS (M4 Max)"
HEADER_LINE = re.compile(r"^- ([a-z0-9_]+): (.*)$")
ISOLATION_HEADERS = (("Cross-Origin-Opener-Policy", "same-origin"),
                     ("Cross-Origin-Embedder-Policy", "require-corp"),
                     ("Cross-Origin-Resource-Policy", "same-origin"),
                     ("Cache-Control", "no-store"))


class DemoError(Exception):
    """A step that cannot go on."""


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_commit(root):
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
                          capture_output=True, text=True, check=False)
    if head.returncode != 0:
        return "unknown"
    dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, check=False)
    return head.stdout.strip() + (" (dirty)" if dirty.stdout.strip() else " (clean)")


# build

def build(args):
    if not os.environ.get("EMSDK"):
        raise DemoError("EMSDK is not set: source emsdk_env.sh first")
    build_dir = args.build_dir or REPO_ROOT / "build" / "wasm"
    for command in (["cmake", "--preset", "wasm"],
                    ["cmake", "--build", str(build_dir), "--target", "itch_demo",
                     "--parallel", str(args.jobs)]):
        if subprocess.run(command, cwd=REPO_ROOT, check=False).returncode != 0:
            raise DemoError(f"{' '.join(command)} failed")
    local = REPO_ROOT / LOCAL
    local.mkdir(parents=True, exist_ok=True)
    for name in ("itch_demo.mjs", "itch_demo.wasm"):
        shutil.copyfile(build_dir / "src" / name, local / name)
    engine = json.loads((build_dir / "src" / "itch_demo_build.json").read_text(encoding="utf-8"))
    engine.update({"commit": git_commit(REPO_ROOT),
                   "wasm_sha256": sha256_of(local / "itch_demo.wasm"),
                   "made_by": "scripts/demo.py build"})
    (local / "engine.json").write_text(json.dumps(engine, indent=2) + "\n", encoding="utf-8")
    print(f"demo: engine in {LOCAL}/, built from {engine['commit']}")


# extract

def day_manifest(day):
    """What scripts/data_manifest.json pins for this day's file, or nothing."""
    manifest = json.loads((REPO_ROOT / "scripts" / "data_manifest.json").read_text(encoding="utf-8"))
    for entry in manifest.get("days", {}).values():
        if day.name in (entry.get("name"), entry.get("unpacked_name")):
            return entry
    return {}


def day_hash(day):
    """The day file's sha256, hashed from the file, and what it was checked against: refused if
    scripts/data_manifest.json pins another (r5-6)."""
    digest = sha256_of(day)
    pin = day_manifest(day).get("unpacked_sha256")
    if pin is not None and pin != digest:
        raise DemoError(f"{day.name} hashes to {digest}, not the {pin} that "
                        "scripts/data_manifest.json pins")
    return digest, ("the day file itch_extract read, hashed by scripts/demo.py extract; " +
                    ("it matches its pin in scripts/data_manifest.json" if pin else
                     "not pinned in scripts/data_manifest.json"))


def extract(args):
    tool = args.tool or REPO_ROOT / "build" / "release" / "tools" / "itch_extract"
    if not tool.is_file():
        raise DemoError(f"no {tool}: build it with cmake --preset release && "
                        "cmake --build --preset release --target itch_extract")
    if not args.day.is_file():
        raise DemoError(f"no {args.day}: fetch the day with scripts/fetch_data.py")
    day_sha256, day_sha256_of = day_hash(args.day)
    local = args.root / LOCAL
    local.mkdir(parents=True, exist_ok=True)
    partial = local / "extract.itch.partial"
    result = subprocess.run([str(tool), str(args.day), args.symbol, str(partial)],
                            capture_output=True, text=True, check=False)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        partial.unlink(missing_ok=True)
        raise DemoError(f"itch_extract exited {result.returncode}")
    summary = json.loads(result.stdout)
    summary.update({
        "day": args.day.name,
        "day_sha256": day_sha256,
        "day_sha256_of": day_sha256_of,
        "extract_file": "extract.itch",
        "extract_sha256": sha256_of(partial),
        "made_by": "scripts/demo.py extract",
        "terms": ("Cut from Nasdaq's TotalView-ITCH sample file for local use. Not committed, "
                  "and not for public deployment without Nasdaq's written approval."),
    })
    partial.replace(local / "extract.itch")
    (local / "extract.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"demo: {summary['symbol']} on {args.day.name}, {summary['messages']:,} messages, "
          f"{summary['bytes']:,} bytes, in {LOCAL}/extract.itch")


# serve

class DemoHandler(http.server.SimpleHTTPRequestHandler):
    """docs/ as a static site, cross-origin isolated, with the module types set."""

    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".mjs": "text/javascript", ".js": "text/javascript",
                      ".wasm": "application/wasm", ".json": "application/json"}

    def end_headers(self):
        for name, value in ISOLATION_HEADERS:
            self.send_header(name, value)
        super().end_headers()

    def log_message(self, format, *args):  # noqa: A002 - the base class's name
        if getattr(self.server, "quiet", False):
            return
        super().log_message(format, *args)


def make_server(port, handler=DemoHandler, root=None):
    directory = str(root or REPO_ROOT / DOCS)

    def bound(*args, **kwargs):
        return handler(*args, directory=directory, **kwargs)

    return http.server.ThreadingHTTPServer(("127.0.0.1", port), bound)


def serve(args):
    server = make_server(args.port)
    print(f"demo: serving {DOCS}/ at http://127.0.0.1:{server.server_address[1]}/ "
          "(Ctrl-C stops it)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


# numbers

def header_of(text):
    fields = {}
    match = re.search(r"^## Header\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    for line in (match.group(1) if match else "").splitlines():
        found = HEADER_LINE.match(line)
        if found:
            fields[found.group(1)] = found.group(2)
    return fields


def row_cells(text, label, name):
    match = re.search(rf"^\| {re.escape(label)} \|(.*)\|$", text, re.MULTILINE)
    if match is None:
        raise DemoError(f"results/{name}: no row {label!r}")
    return [cell.strip() for cell in match.group(1).split("|")]


def newest(root, pattern):
    found = sorted((root / "results").glob(pattern))
    return found[-1] if found else None


def linux_slot(root):
    path = newest(root, LINUX_SOURCE)
    if path is None:
        return {"missing": f"no results/{LINUX_SOURCE} yet: the native Linux numbers come from a "
                           "debian run of the arm that ships, and none is on record"}
    name = path.name
    text = path.read_text(encoding="utf-8")
    header = header_of(text)
    if header.get("host") != LINUX_HOST or not header.get("kernel", "").startswith("Linux"):
        raise DemoError(f"results/{name}: host {header.get('host')!r} on "
                        f"{header.get('kernel')!r} is not debian on Linux, so it cannot fill the "
                        "Linux slot")
    throughput = row_cells(text, "Throughput, messages/s", name)
    percentiles = {}
    for label in ("p50", "p99", "p99.9"):
        cells = row_cells(text, label, name)
        percentiles[label] = {"median_ns": cells[0], "status": cells[4]}
    return {
        "file": f"results/{name}",
        "host": header["host"], "cpu": header.get("cpu", ""), "kernel": header["kernel"],
        "compiler": header.get("compiler", ""), "flags": header.get("flags", ""),
        "commit": header.get("commit", ""), "dataset": header.get("dataset", ""),
        "pinning": header.get("pinning", ""), "design": header.get("design", "").split(":")[0],
        "prediction": header.get("prediction", ""), "run_id": header.get("run_id", ""),
        "throughput_messages_per_s": throughput[0], "throughput_cv": throughput[3],
        "latency": percentiles,
        "book_memory_mib": row_cells(text, "Book memory, MiB", name)[0],
    }


def wasm_slot(root):
    path = newest(root, WASM_SOURCE)
    if path is None:
        return {"missing": f"no results/{WASM_SOURCE} yet: T-WASM-1 has not run"}
    name = path.name
    text = path.read_text(encoding="utf-8")
    header = header_of(text)
    if header.get("host") != MAC_HOST:
        raise DemoError(f"results/{name}: host {header.get('host')!r} is not {MAC_HOST}")
    target = row_cells(text, "T-WASM-1", name)
    return {
        "file": f"results/{name}", "host": header["host"], "cpu": header.get("cpu", ""),
        "browser": header.get("browser", ""), "commit": header.get("commit", ""),
        "extract": header.get("extract", ""),
        "native_messages_per_s": row_cells(text, "Native, messages/s", name)[0],
        "wasm_messages_per_s": row_cells(text, "WebAssembly in the browser, messages/s", name)[0],
        "ratio": target[1], "bar": target[0], "verdict": target[2],
    }


def numbers(root):
    return {"generated_by": "scripts/demo.py numbers", "linux": linux_slot(root),
            "wasm_macos": wasm_slot(root)}


def numbers_command(args):
    root = args.root.resolve()
    text = json.dumps(numbers(root), indent=2, ensure_ascii=False) + "\n"
    target = root / NUMBERS
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current != text:
            print(f"demo: FAIL numbers-stale {NUMBERS}: differs from a fresh run over results/; "
                  "run scripts/demo.py numbers")
            return 1
        print(f"demo: ok {NUMBERS} matches results/")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"demo: wrote {NUMBERS}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="step", required=True)
    built = sub.add_parser("build")
    built.add_argument("--build-dir", type=Path)
    built.add_argument("--jobs", type=int, default=3)
    cut = sub.add_parser("extract")
    cut.add_argument("--day", type=Path, required=True)
    cut.add_argument("--symbol", required=True)
    cut.add_argument("--tool", type=Path)
    cut.add_argument("--root", type=Path, default=REPO_ROOT)
    served = sub.add_parser("serve")
    served.add_argument("--port", type=int, default=8000)
    shown = sub.add_parser("numbers")
    shown.add_argument("--check", action="store_true")
    shown.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    try:
        if args.step == "build":
            build(args)
        elif args.step == "extract":
            extract(args)
        elif args.step == "serve":
            serve(args)
        else:
            return numbers_command(args)
    except DemoError as error:
        print(f"demo: FAIL {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
