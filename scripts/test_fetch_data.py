#!/usr/bin/env python3
"""Offline tests for fetch_data.py. Run: python3 scripts/test_fetch_data.py"""

import gzip
import hashlib
import io
import os
import random
import sys
import tempfile
import unittest
import contextlib
import struct
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_data as fd  # noqa: E402

SCRATCH = fd.REPO_ROOT / "build-research"
URL = "https://archive.invalid/day.gz"
ETAG = '"64b3281599bfd51:0"'
LAST_MODIFIED = "Tue, 31 Dec 2019 05:13:48 GMT"


def frame(message):
    return struct.pack(">H", len(message)) + message


def system_event(code):
    return b"S" + b"\x00\x00" + b"\x00\x07" + (34_200_000_000_000).to_bytes(6, "big") + code


def stream(*extra, last=b"C"):
    body = frame(system_event(b"O")) + frame(b"A" + bytes(35)) + frame(b"D" + bytes(18))
    return body + b"".join(extra) + frame(system_event(last))


def gz_of(data):
    return gzip.compress(data, compresslevel=6, mtime=0)


def good_headers(chunk, total):
    return {"content-range": f"bytes {chunk.start}-{chunk.end}/{total}", "etag": ETAG}


class Scratch(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self._tmp = tempfile.TemporaryDirectory(dir=SCRATCH, prefix="test-fetch-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self._log = io.StringIO()
        redirect = contextlib.redirect_stdout(self._log)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def write(self, name, data):
        path = self.dir / name
        path.write_bytes(data)
        return path


class ChunkAcceptance(unittest.TestCase):
    chunk = fd.Chunk(1, 32, 63)
    total = 100

    def accept(self, status=206, headers=None, length=32):
        headers = good_headers(self.chunk, self.total) if headers is None else headers
        fd.accept_chunk(self.chunk, self.total, ETAG, status, headers, length)

    def test_good_chunk_is_accepted(self):
        self.accept()

    def test_status_200_is_rejected(self):
        with self.assertRaisesRegex(fd.ChunkRejected, "status 200"):
            self.accept(status=200)

    def test_wrong_content_range_is_rejected(self):
        for value in ("bytes 33-64/100", "bytes 32-63/101", "bytes 32-63/*", "32-63/100", None):
            headers = good_headers(self.chunk, self.total)
            headers["content-range"] = value
            with self.subTest(value), self.assertRaisesRegex(fd.ChunkRejected, "Content-Range"):
                self.accept(headers=headers)

    def test_changed_or_missing_etag_is_rejected(self):
        for value in ('"another:0"', None):
            headers = good_headers(self.chunk, self.total)
            headers["etag"] = value
            with self.subTest(value), self.assertRaisesRegex(fd.ChunkRejected, "ETag"):
                self.accept(headers=headers)

    def test_short_body_is_rejected(self):
        with self.assertRaisesRegex(fd.ChunkRejected, "31 bytes"):
            self.accept(length=31)

    def test_long_body_is_rejected(self):
        with self.assertRaisesRegex(fd.ChunkRejected, "33 bytes"):
            self.accept(length=33)


class HeadersAndPlan(unittest.TestCase):
    def test_last_block_wins(self):
        raw = "HTTP/1.1 100 Continue\r\nX-First: 1\r\n\r\nHTTP/2 206\r\nETag: \"a\"\r\n\r\n"
        status, headers = fd.parse_headers(raw)
        self.assertEqual(status, 206)
        self.assertEqual(headers, {"etag": '"a"'})

    def test_chunks_cover_the_file_exactly(self):
        chunks = fd.plan_chunks(3524013057)
        self.assertEqual(chunks[0].start, 0)
        self.assertEqual(chunks[-1].end, 3524013056)
        self.assertEqual(sum(c.size for c in chunks), 3524013057)
        self.assertTrue(all(a.end + 1 == b.start for a, b in zip(chunks, chunks[1:])))

    def test_range_command_cannot_continue_or_follow(self):
        chunk = fd.Chunk(0, 0, 31)
        command = fd.range_command(URL, chunk, Path("body"), Path("head"))
        self.assertEqual(command[:2], ["curl", "-q"])
        for banned in ("--continue-at", "-C", "--retry", "--location", "-L"):
            self.assertNotIn(banned, command)


class FakeServer:
    """Stands in for curl. `faults` maps (chunk index, attempt) to a fault name."""

    def __init__(self, data, faults=None):
        self.data = data
        self.faults = faults or {}
        self.calls = []

    @property
    def remote(self):
        return fd.Remote(len(self.data), ETAG, LAST_MODIFIED)

    def head(self, url):
        return self.remote

    def __call__(self, url, chunk, body_path, header_path):
        self.calls.append(chunk.index)
        attempt = self.calls.count(chunk.index)
        fault = self.faults.get((chunk.index, attempt))
        body = self.data[chunk.start:chunk.end + 1]
        status = "HTTP/1.1 206 Partial Content"
        content_range = f"bytes {chunk.start}-{chunk.end}/{len(self.data)}"
        etag, code, stderr = ETAG, 0, ""
        if fault == "200":
            status, body = "HTTP/1.1 200 OK", self.data
        elif fault == "short":
            body = body[:-1]
        elif fault == "long":
            body = body + b"x"
        elif fault == "etag":
            etag = '"changed:0"'
        elif fault == "range":
            content_range = f"bytes {chunk.start + 1}-{chunk.end + 1}/{len(self.data)}"
        elif fault == "dropped":
            body, code, stderr = body[:1], 18, "partial file"
        elif fault == "exit":
            code, stderr = 56, "reset"
        elif fault == "disk full":
            raise OSError(28, "No space left on device")
        Path(body_path).write_bytes(body)
        Path(header_path).write_text(
            f"{status}\r\nContent-Range: {content_range}\r\nETag: {etag}\r\n\r\n")
        return code, Path(header_path).read_text(), stderr


class Download(Scratch):
    chunk_size = 16

    def setUp(self):
        super().setUp()
        self.data = bytes(random.Random(1).randrange(256) for _ in range(100))
        self.chunks = fd.plan_chunks(len(self.data), self.chunk_size)
        self.parts = self.dir / ".day.parts"
        self.state = {"url": URL, "total": 100, "etag": ETAG, "chunk_size": self.chunk_size}

    def run_download(self, server, **kwargs):
        fd.prepare_parts(self.parts, self.state)
        fd.download_chunks(URL, server.remote, self.chunks, self.parts, 2, server,
                           sleep=lambda _: None, **kwargs)

    def test_each_fault_is_deleted_and_retried(self):
        faults = {(0, 1): "200", (1, 1): "short", (2, 1): "long", (3, 1): "etag",
                  (4, 1): "range", (5, 1): "dropped", (5, 2): "exit"}
        server = FakeServer(self.data, faults)
        self.run_download(server)
        fd.assemble(self.chunks, self.parts, self.dir / "out")
        self.assertEqual((self.dir / "out").read_bytes(), self.data)
        self.assertEqual(sorted(p.name for p in self.parts.glob("*.tmp")), [])
        self.assertEqual(server.calls.count(5), 3)

    def test_rerun_keeps_accepted_chunks_and_never_continues_a_partial(self):
        server = FakeServer(self.data)
        fd.prepare_parts(self.parts, self.state)
        for chunk in self.chunks[:2]:
            fd.fetch_one(URL, chunk, 100, ETAG, self.parts, server)
        (self.parts / "chunk-000002.tmp").write_bytes(b"partial")
        (self.parts / "chunk-000003").write_bytes(b"wrong size")
        server.calls.clear()
        self.run_download(server)
        self.assertNotIn(0, server.calls)
        self.assertNotIn(1, server.calls)
        self.assertIn(2, server.calls)
        self.assertIn(3, server.calls)
        fd.assemble(self.chunks, self.parts, self.dir / "out")
        self.assertEqual((self.dir / "out").read_bytes(), self.data)

    def test_parts_from_another_object_are_refused(self):
        fd.prepare_parts(self.parts, self.state)
        with self.assertRaisesRegex(fd.FetchError, "different object"):
            fd.prepare_parts(self.parts, dict(self.state, etag='"new:0"'))

    def test_a_chunk_that_never_passes_stops_the_run(self):
        faults = {(0, attempt): "200" for attempt in range(1, 4)}
        with self.assertRaisesRegex(fd.FetchError, "failed 3 attempts"):
            self.run_download(FakeServer(self.data, faults), max_attempts=3)

    def test_an_error_that_is_not_a_rejected_chunk_stops_every_worker(self):
        # One worker, so that what was asked for after the error is exact: nothing.
        server = FakeServer(self.data, {(0, 1): "disk full"})
        fd.prepare_parts(self.parts, self.state)
        with self.assertRaisesRegex(OSError, "No space left on device"):
            fd.download_chunks(URL, server.remote, self.chunks, self.parts, 1, server,
                               sleep=lambda _: None)
        self.assertEqual(server.calls, [0])
        # Two workers: the error still comes out as it came, and the run stops short.
        server = FakeServer(self.data, {(0, 1): "disk full"})
        with self.assertRaisesRegex(OSError, "No space left on device"):
            self.run_download(server)
        self.assertLess(len(server.calls), len(self.chunks))

    def test_nothing_assembles_with_a_chunk_missing(self):
        server = FakeServer(self.data)
        self.run_download(server)
        (self.parts / "chunk-000001").unlink()
        with self.assertRaisesRegex(fd.FetchError, "chunk-000001"):
            fd.assemble(self.chunks, self.parts, self.dir / "out")
        self.assertFalse((self.dir / "out").exists())


def unpinned_day(gz_bytes, raw, **extra):
    day = {"name": "day", "url": URL, "expected": {"compressed_bytes": len(gz_bytes),
                                                   "unpacked_bytes": len(raw)}}
    day.update(extra)
    return day


class Gzip(Scratch):
    def test_single_member(self):
        raw = stream()
        info = fd.inflate(self.write("a.gz", gz_of(raw)))
        self.assertEqual((info.members, info.unpacked_bytes), (1, len(raw)))
        self.assertEqual(info.sha256, hashlib.sha256(raw).hexdigest())

    def test_multi_member_uses_the_last_member_for_the_trailer(self):
        first, second = b"x" * 5000, b"y" * 3000
        info = fd.inflate(self.write("a.gz", gz_of(first) + gz_of(second)))
        self.assertEqual(info.members, 2)
        self.assertEqual(info.unpacked_bytes, 8000)
        self.assertEqual(info.isize, 3000)
        self.assertEqual(info.sha256, hashlib.sha256(first + second).hexdigest())

    def test_isize_is_modulo_2_32(self):
        self.assertEqual(fd.isize_of(8251407909), 3956440613)

    def test_truncated_gzip_fails(self):
        data = gz_of(os.urandom(2000))
        with self.assertRaises(fd.VerifyError):
            fd.inflate(self.write("a.gz", data[:-100]))

    def test_trailing_bytes_fail(self):
        data = gz_of(b"z" * 2000) + b"\x00" * 16
        with self.assertRaises(fd.VerifyError):
            fd.inflate(self.write("a.gz", data))

    def test_flipped_payload_byte_fails(self):
        data = bytearray(gz_of(os.urandom(2000)))
        data[len(data) // 2] ^= 0xFF
        with self.assertRaises(fd.VerifyError):
            fd.inflate(self.write("a.gz", bytes(data)))


class Framing(Scratch):
    def walk(self, data):
        return fd.walk_frames(self.write("raw", data))

    def test_good_stream(self):
        result = self.walk(stream())
        self.assertEqual(result.messages, 4)
        self.assertEqual(result.per_type_counts, {"A": 1, "D": 1, "S": 2})
        self.assertEqual(result.last_message["event_code"], "C")
        self.assertEqual(result.last_message["timestamp_ns"], 34_200_000_000_000)

    def test_truncated_body_is_rejected(self):
        with self.assertRaisesRegex(fd.FramingError, "remain"):
            self.walk(stream()[:-3])

    def test_truncated_length_prefix_is_rejected(self):
        with self.assertRaisesRegex(fd.FramingError, "stray byte"):
            self.walk(stream() + b"\x00")

    def test_clean_prefix_is_rejected_by_the_last_message(self):
        data = stream()
        with self.assertRaisesRegex(fd.FramingError, "last message"):
            self.walk(data[:-len(frame(system_event(b"C")))])

    def test_last_message_of_another_type_is_rejected(self):
        with self.assertRaisesRegex(fd.FramingError, "last message is type b'A'"):
            self.walk(stream() + frame(b"A" + bytes(35)))

    def test_last_system_event_not_c_is_rejected(self):
        with self.assertRaisesRegex(fd.FramingError, "event code 'E'"):
            self.walk(stream(last=b"E"))

    def test_zero_length_frame_is_rejected(self):
        with self.assertRaisesRegex(fd.FramingError, "zero-length"):
            self.walk(stream() + b"\x00\x00")

    def test_no_terminator_is_required(self):
        self.assertFalse(stream().endswith(b"\x00\x00"))
        self.walk(stream())


class VerifyAndPin(Scratch):
    def setUp(self):
        super().setUp()
        self.raw = stream()
        self.gz = gz_of(self.raw)
        self.source = {"etag": ETAG, "last_modified": LAST_MODIFIED}

    def verify(self, day, gz=None):
        gz_path = self.write("day.gz", self.gz if gz is None else gz)
        raw_path = self.dir / "out"
        return fd.verify_day(gz_path, raw_path, day, self.source, unpack=True)

    def pinned(self):
        day = unpinned_day(self.gz, self.raw)
        self.assertTrue(fd.pin_day(day, self.verify(day)))
        return day

    def test_pin_then_repeat_passes(self):
        day = self.pinned()
        self.assertEqual(day["gz_sha256"], hashlib.sha256(self.gz).hexdigest())
        self.assertEqual(day["unpacked_sha256"], hashlib.sha256(self.raw).hexdigest())
        self.verify(day)

    def test_corrupted_assembled_file_fails_the_hash_check(self):
        day = self.pinned()
        bad = bytearray(self.gz)
        bad[20] ^= 0x01
        with self.assertRaisesRegex(fd.VerifyError, "gz_sha256"):
            self.verify(day, bytes(bad))

    def test_length_comes_before_the_hash(self):
        with self.assertRaisesRegex(fd.VerifyError, "compressed_bytes"):
            self.verify(self.pinned(), self.gz + b"x")

    def test_whole_body_appended_fails_on_length(self):
        with self.assertRaisesRegex(fd.VerifyError, "compressed_bytes"):
            self.verify(unpinned_day(self.gz, self.raw), self.gz + self.gz)

    def test_changed_etag_fails_once_pinned(self):
        day = self.pinned()
        self.source = dict(self.source, etag='"changed:0"')
        with self.assertRaisesRegex(fd.VerifyError, "etag"):
            self.verify(day)

    def test_every_pinned_field_is_checked(self):
        for field in ("gzip_crc32", "gzip_isize", "unpacked_bytes", "unpacked_sha256",
                      "messages", "per_type_counts", "last_message"):
            day = self.pinned()
            day[field] = "something else"
            with self.subTest(field), self.assertRaisesRegex(fd.VerifyError, field):
                self.verify(day)

    def test_third_party_disagreement_stops_a_pin(self):
        for field in ("gz_sha256", "unpacked_sha256"):
            day = unpinned_day(self.gz, self.raw, third_party={field: "0" * 64})
            with self.subTest(field), self.assertRaisesRegex(fd.VerifyError, "third party"):
                self.verify(day)
            self.assertIsNone(day.get("gz_sha256"))

    def test_third_party_agreement_is_recorded(self):
        third_party = {"gz_sha256": hashlib.sha256(self.gz).hexdigest()}
        day = unpinned_day(self.gz, self.raw, third_party=third_party)
        fd.pin_day(day, self.verify(day))
        self.assertEqual(day["third_party"]["agrees"], ["gz_sha256"])

    def test_a_pinned_day_is_never_rewritten(self):
        day = self.pinned()
        before = dict(day)
        measured = dict(self.verify(day), gz_sha256="f" * 64)
        self.assertFalse(fd.pin_day(day, measured))
        self.assertEqual(day, before)


class WholeFetch(Scratch):
    def setUp(self):
        super().setUp()
        self.raw = stream(frame(b"E" + bytes(30)))
        self.gz = gz_of(self.raw)
        self.data_dir = self.dir / "data"

    def fetch(self, day, server, jobs=2):
        return fd.fetch_day(day, self.data_dir, jobs, transport=server, head=server.head,
                            sleep=lambda _: None, chunk_size=16)

    def test_fetch_installs_both_files_and_cleans_up(self):
        server = FakeServer(self.gz, {(0, 1): "200", (1, 1): "short"})
        measured, source = self.fetch(unpinned_day(self.gz, self.raw), server)
        self.assertEqual((self.data_dir / "day.gz").read_bytes(), self.gz)
        self.assertEqual((self.data_dir / "day").read_bytes(), self.raw)
        self.assertEqual(sorted(p.name for p in self.data_dir.iterdir()),
                         [".day.lock", ".day.source.json", "day", "day.gz"])
        self.assertEqual(source["etag"], ETAG)
        self.assertEqual(measured["messages"], 5)

    def test_rerun_verifies_instead_of_trusting(self):
        day = unpinned_day(self.gz, self.raw)
        self.fetch(day, FakeServer(self.gz))
        fd.pin_day(day, fd.verify_only(day, self.data_dir)[0])
        with open(self.data_dir / "day", "r+b") as handle:
            handle.write(b"\xff")
        with self.assertRaisesRegex(fd.VerifyError, "unpacked_sha256 on disk"):
            self.fetch(day, FakeServer(self.gz))

    def test_missing_unpacked_file_is_rebuilt_from_the_gz(self):
        day = unpinned_day(self.gz, self.raw)
        self.fetch(day, FakeServer(self.gz))
        (self.data_dir / "day").unlink()
        self.fetch(day, FakeServer(b""))
        self.assertEqual((self.data_dir / "day").read_bytes(), self.raw)

    def test_a_failed_check_leaves_no_final_file(self):
        day = unpinned_day(self.gz, self.raw, gz_sha256="0" * 64)
        with self.assertRaisesRegex(fd.VerifyError, "gz_sha256"):
            self.fetch(day, FakeServer(self.gz))
        names = {p.name for p in self.data_dir.iterdir()}
        self.assertNotIn("day.gz", names)
        self.assertNotIn("day", names)
        self.assertFalse([n for n in names if n.endswith(".tmp")])
        self.assertIn(".day.parts", names)

    def test_a_short_advertised_length_stops_before_any_chunk(self):
        server = FakeServer(self.gz[:-1])
        with self.assertRaisesRegex(fd.VerifyError, "advertised length"):
            self.fetch(unpinned_day(self.gz, self.raw), server)
        self.assertEqual(server.calls, [])

    def test_verify_only_needs_both_files(self):
        with self.assertRaisesRegex(fd.VerifyError, "missing"):
            fd.verify_only(unpinned_day(self.gz, self.raw), self.data_dir)

    def test_a_day_with_an_unpacked_name_is_unpacked_under_it(self):
        day = unpinned_day(self.gz, self.raw, name="S061226-v50.txt",
                           unpacked_name="06122026.NASDAQ_ITCH50")
        self.fetch(day, FakeServer(self.gz))
        self.assertEqual(sorted(p.name for p in self.data_dir.iterdir() if p.name[0] != "."),
                         ["06122026.NASDAQ_ITCH50", "S061226-v50.txt.gz"])
        self.assertEqual((self.data_dir / "S061226-v50.txt.gz").read_bytes(), self.gz)
        self.assertEqual((self.data_dir / "06122026.NASDAQ_ITCH50").read_bytes(), self.raw)
        self.assertFalse((self.data_dir / "S061226-v50.txt").exists())
        self.assertEqual(fd.verify_only(day, self.data_dir)[0]["messages"], 5)
        (self.data_dir / "06122026.NASDAQ_ITCH50").unlink()
        self.fetch(day, FakeServer(b""))
        self.assertEqual((self.data_dir / "06122026.NASDAQ_ITCH50").read_bytes(), self.raw)


class ManifestNames(unittest.TestCase):
    # 10182019 was unpacked under Nasdaq's .txt name before unpacked_name existed, and its
    # published results name that file; its unpacked copy is not kept on this Mac.
    BEFORE_UNPACKED_NAME = {"10182019"}

    def test_no_day_is_unpacked_under_a_name_macos_reads_as_text(self):
        manifest = fd.read_json(fd.REPO_ROOT / "scripts" / "data_manifest.json")
        for key, day in manifest["days"].items():
            if key in self.BEFORE_UNPACKED_NAME:
                continue
            with self.subTest(day=key):
                self.assertFalse(fd.unpacked_name(day).lower().endswith(".txt"),
                                 f"{key} unpacks to {fd.unpacked_name(day)}, which Spotlight "
                                 "imports as plain text; give it an unpacked_name")
        self.assertEqual(fd.unpacked_name(manifest["days"]["06122026"]),
                         "06122026.NASDAQ_ITCH50")


class References(Scratch):
    def zip_bytes(self):
        path = self.dir / "made.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("a.csv", "x,y\n1,2\n")
        return path.read_bytes()

    def test_pinned_length_and_hash_are_checked(self):
        path = self.write("a.txt", b"Symbol|Opening|Closing\n")
        measured = fd.verify_reference(path, {"format": "text", "bytes": None, "sha256": None})
        fd.verify_reference(path, dict(measured, format="text"))
        with self.assertRaisesRegex(fd.VerifyError, "bytes"):
            fd.verify_reference(path, {"format": "text", "bytes": 1, "sha256": None})
        with self.assertRaisesRegex(fd.VerifyError, "sha256"):
            fd.verify_reference(path, dict(measured, format="text", sha256="0" * 64))

    def test_error_page_is_not_taken_for_data(self):
        path = self.write("a.txt", b"  <html>Service unavailable</html>")
        with self.assertRaisesRegex(fd.VerifyError, "HTML"):
            fd.verify_reference(path, {"format": "text"})
        with self.assertRaisesRegex(fd.VerifyError, "zip"):
            fd.verify_reference(path, {"format": "zip"})

    def test_good_zip_passes(self):
        fd.verify_reference(self.write("a.zip", self.zip_bytes()), {"format": "zip"})


class Paths(unittest.TestCase):
    def test_default_is_the_repo_data_folder_from_anywhere(self):
        before = Path.cwd()
        self.addCleanup(os.chdir, before)
        os.chdir(fd.SCRIPT_DIR)
        self.assertEqual(fd.resolve_dir(None), fd.REPO_ROOT / "data")

    def test_relative_dir_follows_the_working_directory(self):
        before = Path.cwd()
        self.addCleanup(os.chdir, before)
        os.chdir(fd.SCRIPT_DIR)
        self.assertEqual(fd.resolve_dir("data"), fd.SCRIPT_DIR / "data")

    def test_absolute_dir_is_unchanged(self):
        self.assertEqual(fd.resolve_dir("/srv/data"), Path("/srv/data").resolve())

    def test_script_holds_no_environment_or_home_path(self):
        source = (fd.SCRIPT_DIR / "fetch_data.py").read_text()
        for banned in ("os.environ", "getenv", "expanduser", "Path.home", "/Users/", "/home/"):
            self.assertNotIn(banned, source)

    def test_no_cboe_in_script_or_manifest(self):
        for name in ("fetch_data.py", "data_manifest.json"):
            self.assertNotIn("cboe", (fd.SCRIPT_DIR / name).read_text().lower())


if __name__ == "__main__":
    unittest.main()
