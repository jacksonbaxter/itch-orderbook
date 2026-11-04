#!/usr/bin/env python3
"""fetch_tool.py on a local archive: the right hash unpacks, any other unpacks nothing.

A .zst archive is written here as a frame of raw blocks whose header asks for a 1 GiB
window, as LLVM's Linux archive does, which zstd refuses unless told it may.
"""

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_tool  # noqa: E402

SCRATCH = fetch_tool.REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
HOST = ("Testing", "arch")
ZSTD_MAGIC = bytes.fromhex("28b52ffd")
RAW_BLOCK = 1 << 17


def long_window_zstd(data):
    """A zstd frame of raw blocks, no content size and no checksum, with a 1 GiB window."""
    window_1_gib = (30 - 10) << 3
    frame = bytearray(ZSTD_MAGIC + bytes([0, window_1_gib]))
    for start in range(0, len(data), RAW_BLOCK):
        block = data[start:start + RAW_BLOCK]
        last = start + RAW_BLOCK >= len(data)
        frame += ((len(block) << 3) | last).to_bytes(3, "little") + block
    return bytes(frame)


class FetchTool(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        source = self.root / "source"
        (source / "bin").mkdir(parents=True)
        (source / "bin" / "tool").write_text("not run\n", encoding="utf-8")
        self.archive = self.root / "tool.tar.gz"
        subprocess.run(["tar", "-czf", str(self.archive), "-C", str(source), "."], check=True)
        self.tools_dir = self.root / "tools"
        self.source = source

    def use_long_window_zstd(self):
        plain = self.root / "tool.tar"
        subprocess.run(["tar", "-cf", str(plain), "-C", str(self.source), "."], check=True)
        self.archive = self.root / "tool.tar.zst"
        self.archive.write_bytes(long_window_zstd(plain.read_bytes()))

    def tools(self, sha256):
        return {"tool": fetch_tool.Tool("1.0", 0, "bin", {
            HOST: (self.archive.as_uri(), sha256)})}

    def right_hash(self):
        return hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def test_right_hash_unpacks_and_leaves_no_archive(self):
        bin_dir = fetch_tool.fetch("tool", self.tools_dir, self.tools(self.right_hash()), HOST)
        self.assertTrue((bin_dir / "tool").is_file())
        self.assertEqual(sorted(path.name for path in self.tools_dir.iterdir()), ["tool-1.0"])

    def test_wrong_hash_unpacks_nothing(self):
        with self.assertRaisesRegex(fetch_tool.FetchError, "pinned"):
            fetch_tool.fetch("tool", self.tools_dir, self.tools("0" * 64), HOST)
        self.assertEqual(list(self.tools_dir.iterdir()), [])

    def test_a_finished_tool_is_not_fetched_again(self):
        tools = self.tools(self.right_hash())
        fetch_tool.fetch("tool", self.tools_dir, tools, HOST)
        self.archive.unlink()
        bin_dir = fetch_tool.fetch("tool", self.tools_dir, tools, HOST)
        self.assertTrue((bin_dir / "tool").is_file())

    def test_a_host_with_no_pin_is_refused(self):
        with self.assertRaisesRegex(fetch_tool.FetchError, "not pinned"):
            fetch_tool.fetch("tool", self.tools_dir, self.tools("0" * 64), ("Other", "arch"))

    def test_a_missing_archive_fails_and_leaves_nothing(self):
        tools = self.tools(self.right_hash())
        self.archive.unlink()
        with self.assertRaisesRegex(fetch_tool.FetchError, "curl exited"):
            fetch_tool.fetch("tool", self.tools_dir, tools, HOST)
        self.assertEqual(list(self.tools_dir.iterdir()), [])

    def test_the_zstd_frame_here_is_one_zstd_refuses_by_default(self):
        self.use_long_window_zstd()
        plain = subprocess.run(["zstd", "-d", "-c", str(self.archive)], capture_output=True,
                               check=False)
        self.assertNotEqual(plain.returncode, 0)
        self.assertIn(b"Window size larger than maximum", plain.stderr)

    def test_a_zstd_archive_with_a_1_gib_window_unpacks(self):
        self.use_long_window_zstd()
        bin_dir = fetch_tool.fetch("tool", self.tools_dir, self.tools(self.right_hash()), HOST)
        self.assertTrue((bin_dir / "tool").is_file())
        self.assertEqual(sorted(path.name for path in self.tools_dir.iterdir()), ["tool-1.0"])

    def test_a_truncated_zstd_archive_fails_and_leaves_nothing(self):
        self.use_long_window_zstd()
        self.archive.write_bytes(self.archive.read_bytes()[:-100])
        with self.assertRaisesRegex(fetch_tool.FetchError, "zstd exited"):
            fetch_tool.fetch("tool", self.tools_dir, self.tools(self.right_hash()), HOST)
        self.assertEqual(list(self.tools_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
