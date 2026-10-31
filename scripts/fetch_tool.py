#!/usr/bin/env python3
"""Fetches a pinned tool into the gitignored build tree, and prints its bin directory.

    fetch_tool.py NAME [--dir DIR]

NAME is actionlint or llvm. The default DIR is build/_tools at the repo root. Each archive
is pinned by version and SHA-256, for each platform that uses it. An archive that does not
match is deleted and nothing is unpacked. A finished tool is kept and not fetched again.
The hash of an archive is checked before anything reads it, and nothing inside it runs here.

CI fetches LLVM through this, so that it builds with the compiler the repo was tested on.

Exit codes: 0 the tool is there, 1 it could not be fetched or did not match, 2 bad usage.
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = REPO_ROOT / "build" / "_tools"
CURL_RETRIES = "3"

ACTIONLINT_URL = "https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_"
LLVM_URL = "https://github.com/llvm/llvm-project/releases/download/llvmorg-23.1.4/LLVM-23.1.4-"


@dataclass(frozen=True)
class Tool:
    version: str
    strip: int  # Leading directories in the archive to drop.
    bin_dir: str
    assets: dict  # (system, machine) to (url, sha256).


TOOLS = {
    "actionlint": Tool("1.7.12", 0, ".", {
        ("Darwin", "arm64"): (
            ACTIONLINT_URL + "darwin_arm64.tar.gz",
            "aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f"),
        ("Linux", "x86_64"): (
            ACTIONLINT_URL + "linux_amd64.tar.gz",
            "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"),
        ("Linux", "aarch64"): (
            ACTIONLINT_URL + "linux_arm64.tar.gz",
            "325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6"),
    }),
    "llvm": Tool("23.1.4", 1, "bin", {
        ("Darwin", "arm64"): (
            LLVM_URL + "macOS-ARM64.tar.xz",
            "5d3179db39adeca5b17a34d82708fe976bc60e055b05fe557878fb3fb64d39ef"),
        ("Linux", "x86_64"): (
            LLVM_URL + "Linux-X64.tar.zst",
            "efdd1dbd089b99bfcfcde7b8eb1236163f2e88df5a981cca4f783ec9cb5e0378"),
    }),
}


class FetchError(Exception):
    """The tool could not be fetched, or was not the one pinned."""


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(name, tools_dir, tools=None, host=None):
    """The bin directory of the tool, fetched and unpacked if it is not there yet."""
    tool = (tools or TOOLS)[name]
    home = tools_dir / f"{name}-{tool.version}"
    bin_path = home / tool.bin_dir
    if home.is_dir():
        return bin_path.resolve()
    host = host or (platform.system(), platform.machine())
    if host not in tool.assets:
        raise FetchError(f"{name} is not pinned for {host[0]} {host[1]}")
    url, sha256 = tool.assets[host]

    tools_dir.mkdir(parents=True, exist_ok=True)
    archive = tools_dir / url.rsplit("/", 1)[1]
    fetched = subprocess.run(["curl", "-fsSL", "--retry", CURL_RETRIES, "-o", str(archive), url],
                             check=False)
    if fetched.returncode != 0:
        archive.unlink(missing_ok=True)
        raise FetchError(f"curl exited {fetched.returncode} on {url}")
    seen = sha256_of(archive)
    if seen != sha256:
        archive.unlink()
        raise FetchError(f"{archive.name} has SHA-256 {seen}, pinned {sha256}")

    staging = tools_dir / f"{home.name}.new"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    unpacked = subprocess.run(["tar", "-xf", str(archive), "-C", str(staging),
                               f"--strip-components={tool.strip}"], check=False)
    archive.unlink()
    if unpacked.returncode != 0:
        shutil.rmtree(staging, ignore_errors=True)
        raise FetchError(f"tar exited {unpacked.returncode} on {archive.name}")
    staging.rename(home)
    return bin_path.resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", choices=sorted(TOOLS))
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args(argv)
    try:
        print(fetch(args.name, args.dir.resolve()))
    except FetchError as error:
        print(f"fetch_tool: FAIL {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
