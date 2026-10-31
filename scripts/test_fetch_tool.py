#!/usr/bin/env python3
"""fetch_tool.py on a local archive: the right hash unpacks, any other unpacks nothing."""

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


if __name__ == "__main__":
    unittest.main()
