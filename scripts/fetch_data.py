#!/usr/bin/env python3
"""Fetch, verify and pin the ITCH days and external reference files.

    python3 scripts/fetch_data.py --day 12302019       fetch and verify
    python3 scripts/fetch_data.py --day 12302019 --verify-only
    python3 scripts/fetch_data.py --day 12302019 --pin write a new day's values
    python3 scripts/fetch_data.py --ref all            CrossStats and MIDAS

The Nasdaq archive drops connections and throttles, and curl exiting zero proves
nothing about a file from it. So each day is fetched as fixed Range chunks, and
each chunk must prove itself before it is kept. --continue-at is never used: it
has produced files that looked finished and were not. A rerun keeps accepted
chunks and refetches the rest from their first byte.

Nothing gets the final name until every check has passed. All values are read
from data_manifest.json. Once a day is pinned there, any difference is a failure.

Without --dir, files go to data/ at the repo root, wherever the script is run
from. A relative --dir is taken from the working directory, like any other
command-line path, so a copy of the script outside the data tree still works.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import dataclasses
import fcntl
import hashlib
import json
import mmap
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import zipfile
import zlib
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MANIFEST = SCRIPT_DIR / "data_manifest.json"

CHUNK_SIZE = 32 * 1024 * 1024
MAX_ATTEMPTS = 30
MAX_BACKOFF = 300.0
IO_BLOCK = 4 * 1024 * 1024

# A dead socket costs under a minute rather than a whole chunk timeout.
STALL_BYTES_PER_SEC = "24000"
STALL_SECONDS = "45"

# -q must come first: it stops a curlrc adding flags such as --continue-at.
CURL = ["curl", "-q", "--silent", "--show-error", "--connect-timeout", "30"]


class FetchError(Exception):
    """Any failure that must stop the run with a non-zero exit."""


class VerifyError(FetchError):
    """A file failed a check."""


class FramingError(VerifyError):
    """The unpacked stream is not a complete BinaryFILE."""


class ChunkRejected(Exception):
    """A chunk failed acceptance. It is deleted and retried."""


def log(message):
    stamp = time.strftime("%H:%M:%SZ", time.gmtime())
    print(f"[{stamp}] {message}", flush=True)


# Chunk planning and acceptance


@dataclasses.dataclass(frozen=True)
class Chunk:
    index: int
    start: int
    end: int  # inclusive, as in a Range header

    @property
    def size(self):
        return self.end - self.start + 1

    @property
    def filename(self):
        return f"chunk-{self.index:06d}"


def plan_chunks(total, chunk_size=CHUNK_SIZE):
    if total <= 0:
        raise FetchError(f"nothing to fetch: total length {total}")
    starts = range(0, total, chunk_size)
    return [Chunk(i, s, min(s + chunk_size, total) - 1) for i, s in enumerate(starts)]


def parse_headers(raw):
    """Return (status, headers) from the last block of a curl header dump."""
    status = None
    headers = {}
    for line in raw.splitlines():
        line = line.strip()
        match = re.match(r"HTTP/\S+\s+(\d{3})\b", line)
        if match:
            status = int(match.group(1))
            headers = {}
        elif ":" in line and status is not None:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return status, headers


def accept_chunk(chunk, total, first_etag, status, headers, body_length):
    """Raise ChunkRejected unless every condition holds. Curl's exit code is not one."""
    if status != 206:
        raise ChunkRejected(f"status {status}, expected 206")
    want = f"bytes {chunk.start}-{chunk.end}/{total}"
    got = headers.get("content-range")
    if got != want:
        raise ChunkRejected(f"Content-Range {got!r}, expected {want!r}")
    etag = headers.get("etag")
    if etag != first_etag:
        raise ChunkRejected(f"ETag {etag!r}, first seen {first_etag!r}")
    if body_length != chunk.size:
        raise ChunkRejected(f"body is {body_length} bytes, expected {chunk.size}")


# Transport. Everything that touches the network is here, so tests can replace it.


@dataclasses.dataclass(frozen=True)
class Remote:
    total: int
    etag: str
    last_modified: str | None


def range_command(url, chunk, body_path, header_path):
    """Build the curl call for one chunk.

    There is no --location, --retry or --continue-at. --max-filesize refuses a
    200 whole-body reply before it is downloaded.
    """
    return CURL + [
        "--speed-limit", STALL_BYTES_PER_SEC,
        "--speed-time", STALL_SECONDS,
        "--max-time", "3600",
        "--max-filesize", str(chunk.size),
        "--header", "Accept-Encoding: identity",
        "--range", f"{chunk.start}-{chunk.end}",
        "--dump-header", str(header_path),
        "--output", str(body_path),
        url,
    ]


def curl_range(url, chunk, body_path, header_path):
    """Fetch one range. Return (curl exit code, raw headers, stderr)."""
    result = subprocess.run(
        range_command(url, chunk, body_path, header_path),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    raw = header_path.read_text(errors="replace") if header_path.exists() else ""
    return result.returncode, raw, result.stderr.strip()


def curl_head(url, attempts=8, sleep=time.sleep):
    """HEAD with no Range header: the archive answers HEAD with Range as 416."""
    problem = "no attempt made"
    for attempt in range(1, attempts + 1):
        result = subprocess.run(
            CURL + ["--head", "--max-time", "60", url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        status, headers = parse_headers(result.stdout)
        length, etag = headers.get("content-length"), headers.get("etag")
        if result.returncode != 0 or status != 200:
            problem = f"curl exit {result.returncode}, status {status}: {result.stderr.strip()}"
        elif not (length and length.isdigit()) or not etag:
            problem = f"no usable Content-Length or ETag: {headers}"
        elif headers.get("accept-ranges", "").lower() != "bytes":
            problem = f"server does not advertise byte ranges: {headers}"
        else:
            return Remote(int(length), etag, headers.get("last-modified"))
        log(f"HEAD attempt {attempt} failed: {problem}")
        sleep(backoff(attempt))
    raise FetchError(f"HEAD failed after {attempts} attempts: {problem}")


def backoff(attempt):
    return min(MAX_BACKOFF, 2.0 ** min(attempt, 10)) + random.uniform(0.0, 1.0)


# Download


def write_json(path, value):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as out:
        json.dump(value, out, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)


def read_json(path):
    with open(path) as handle:
        return json.load(handle)


def prepare_parts(parts_dir, state):
    """Keep accepted chunks only if they came from this exact object."""
    state_path = parts_dir / "state.json"
    parts_dir.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        found = read_json(state_path)
        if found != state:
            raise FetchError(
                f"{parts_dir} holds chunks of a different object: {found} against "
                f"{state}. Refusing to mix them. Delete that folder to start again."
            )
    else:
        write_json(state_path, state)
    # A partial chunk is never continued, so anything unaccepted goes.
    for leftover in list(parts_dir.glob("*.tmp")) + list(parts_dir.glob("*.hdr")):
        leftover.unlink()


def accepted_on_disk(parts_dir, chunks):
    kept = set()
    for chunk in chunks:
        path = parts_dir / chunk.filename
        if path.exists():
            if path.stat().st_size == chunk.size:
                kept.add(chunk.index)
            else:
                log(f"{chunk.filename}: wrong size on disk, deleted")
                path.unlink()
    return kept


def fetch_one(url, chunk, total, etag, parts_dir, transport):
    """One attempt at one chunk. The body only gets its final name if accepted."""
    final = parts_dir / chunk.filename
    body = parts_dir / (chunk.filename + ".tmp")
    header = parts_dir / (chunk.filename + ".hdr")
    body.unlink(missing_ok=True)
    header.unlink(missing_ok=True)
    try:
        code, raw, stderr = transport(url, chunk, body, header)
        status, headers = parse_headers(raw)
        length = body.stat().st_size if body.exists() else 0
        accept_chunk(chunk, total, etag, status, headers, length)
        if code != 0:
            raise ChunkRejected(f"curl exit {code}: {stderr}")
        with open(body, "rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(body, final)
    finally:
        body.unlink(missing_ok=True)
        header.unlink(missing_ok=True)


def download_chunks(url, remote, chunks, parts_dir, jobs, transport, sleep=time.sleep,
                    max_attempts=MAX_ATTEMPTS):
    kept = accepted_on_disk(parts_dir, chunks)
    missing = [c for c in chunks if c.index not in kept]
    log(f"{len(kept)} of {len(chunks)} chunks already accepted, {len(missing)} to fetch, "
        f"{jobs} workers")
    done_lock = threading.Lock()
    done = {"chunks": len(kept), "bytes": sum(c.size for c in chunks if c.index in kept), "new": 0}
    began = time.monotonic()
    stop = threading.Event()

    def work(chunk):
        for attempt in range(1, max_attempts + 1):
            if stop.is_set():
                return
            started = time.monotonic()
            try:
                fetch_one(url, chunk, remote.total, remote.etag, parts_dir, transport)
            except ChunkRejected as reason:
                log(f"{chunk.filename} attempt {attempt} rejected: {reason}")
                if attempt < max_attempts:
                    sleep(backoff(attempt))
                continue
            except BaseException:
                # Anything else, a full disk or a failed fsync or rename, is fatal: the other
                # workers stop before their next chunk, and it is raised as it came.
                stop.set()
                raise
            now = time.monotonic()
            with done_lock:
                done["chunks"] += 1
                done["bytes"] += chunk.size
                done["new"] += chunk.size
                rate = done["new"] / max(now - began, 1e-3)
                left = (remote.total - done["bytes"]) / max(rate, 1.0)
                log(f"{chunk.filename} accepted in {now - started:.1f}s; "
                    f"{done['chunks']}/{len(chunks)} chunks, "
                    f"{100.0 * done['bytes'] / remote.total:.1f}%; "
                    f"session {rate / 1e6:.2f} MB/s, about {left / 60:.0f} min left")
            return
        stop.set()
        raise FetchError(f"{chunk.filename} failed {max_attempts} attempts")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [pool.submit(work, chunk) for chunk in missing]
        for future in concurrent.futures.as_completed(futures):
            future.result()


def assemble(chunks, parts_dir, out_path):
    for chunk in chunks:
        part = parts_dir / chunk.filename
        if not part.exists() or part.stat().st_size != chunk.size:
            raise FetchError(f"{chunk.filename} is missing or the wrong size, not assembling")
    with open(out_path, "wb") as out:
        for chunk in chunks:
            with open(parts_dir / chunk.filename, "rb") as part:
                shutil.copyfileobj(part, out, IO_BLOCK)
        out.flush()
        os.fsync(out.fileno())


# Verification


def require(label, measured, *wanted):
    """Compare a measured value with each (source, value). None means not known yet."""
    for source, value in wanted:
        if value is not None and value != measured:
            raise VerifyError(f"{label}: measured {measured!r}, {source} says {value!r}")
    log(f"ok {label}: {measured}")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(IO_BLOCK):
            digest.update(block)
    return digest.hexdigest()


@dataclasses.dataclass(frozen=True)
class Inflated:
    members: int
    crc32: int  # from the trailer, so of the last member
    isize: int
    unpacked_bytes: int
    sha256: str


def isize_of(length):
    return length & 0xFFFFFFFF


def inflate(gz_path, out_path=None):
    """Decompress every member, checking the file's trailer against what was computed.

    The last 8 bytes belong to the last member only, so in a multi-member file they
    are compared with that member's CRC and length, not the whole stream's. zlib
    also checks each member's own trailer. Anything after the last member fails.
    """
    size = gz_path.stat().st_size
    if size < 18:
        raise VerifyError(f"{gz_path.name}: {size} bytes is too short to be gzip")
    with open(gz_path, "rb") as handle:
        handle.seek(-8, os.SEEK_END)
        trailer_crc, trailer_isize = struct.unpack("<II", handle.read(8))

    digest = hashlib.sha256()
    total = members = member_crc = member_size = last_crc = last_size = 0
    in_member = False
    inflater = zlib.decompressobj(31)
    out = open(out_path, "wb") if out_path is not None else None
    try:
        with open(gz_path, "rb") as handle:
            while block := handle.read(IO_BLOCK):
                pending = block
                while pending:
                    in_member = True
                    try:
                        data = inflater.decompress(pending)
                    except zlib.error as error:
                        raise VerifyError(f"{gz_path.name}: gzip stream is bad: {error}") from error
                    digest.update(data)
                    member_crc = zlib.crc32(data, member_crc)
                    member_size += len(data)
                    total += len(data)
                    if out is not None:
                        out.write(data)
                    pending = b""
                    if inflater.eof:
                        members += 1
                        last_crc, last_size = member_crc, member_size
                        member_crc = member_size = 0
                        pending = inflater.unused_data
                        inflater = zlib.decompressobj(31)
                        in_member = False
        if out is not None:
            out.flush()
            os.fsync(out.fileno())
    finally:
        if out is not None:
            out.close()
    if in_member or members == 0:
        raise VerifyError(f"{gz_path.name}: gzip stream ends inside a member")
    if trailer_crc != last_crc:
        raise VerifyError(f"{gz_path.name}: trailer CRC {trailer_crc:08x}, computed {last_crc:08x}")
    if trailer_isize != isize_of(last_size):
        raise VerifyError(f"{gz_path.name}: trailer ISIZE {trailer_isize}, "
                          f"computed {isize_of(last_size)}")
    return Inflated(members, trailer_crc, trailer_isize, total, digest.hexdigest())


@dataclasses.dataclass(frozen=True)
class Framing:
    messages: int
    per_type_counts: dict
    last_message: dict


def describe_last(message, offset):
    """System Event layout: type, locate 2, tracking 2, timestamp 6, event code."""
    if message[0:1] != b"S" or len(message) != 12:
        raise FramingError(f"last message is type {message[0:1]!r} and {len(message)} bytes, "
                           f"expected a 12-byte S")
    event = chr(message[11])
    if event != "C":
        raise FramingError(f"last message is S with event code {event!r}, expected 'C'")
    return {
        "type": "S",
        "event_code": event,
        "stock_locate": int.from_bytes(message[1:3], "big"),
        "tracking_number": int.from_bytes(message[3:5], "big"),
        "timestamp_ns": int.from_bytes(message[5:11], "big"),
        "offset": offset,
    }


def walk_frames(path):
    """Walk 2-byte big-endian lengths. The frames must consume the file exactly.

    Real files have no zero-length terminator, so none is required, and a truncated
    file is a well-formed prefix: ending on S/C is what shows it is complete.
    """
    size = path.stat().st_size
    if size == 0:
        raise FramingError(f"{path.name} is empty")
    counts = [0] * 256
    position = last = 0
    with open(path, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as view:
        while position < size:
            if position + 2 > size:
                raise FramingError(f"one stray byte at offset {position}")
            length = view[position] << 8 | view[position + 1]
            if length == 0:
                raise FramingError(f"zero-length frame at offset {position}")
            following = position + 2 + length
            if following > size:
                raise FramingError(f"frame at offset {position} needs {length} bytes, "
                                   f"{size - position - 2} remain")
            counts[view[position + 2]] += 1
            last = position
            position = following
        last_message = describe_last(bytes(view[last + 2:position]), last)
    per_type = {chr(code): n for code, n in enumerate(counts) if n}
    return Framing(sum(counts), dict(sorted(per_type.items())), last_message)


def wanted(day, field):
    """Every source that holds a value for this field, in the order they are checked."""
    third_party = day.get("third_party", {})
    return (
        ("the pinned manifest", day.get(field)),
        ("the published claim", day.get("expected", {}).get(field)),
        ("the third party", third_party.get(field)),
    )


def verify_day(gz_path, raw_path, day, source, unpack):
    """Run every check in order, stopping at the first failure."""
    measured = {"compressed_bytes": gz_path.stat().st_size}
    require("compressed_bytes", measured["compressed_bytes"], *wanted(day, "compressed_bytes"))

    if source is None:
        log("etag and last_modified not recorded on disk, so not compared")
    else:
        measured["etag"] = source["etag"]
        measured["last_modified"] = source["last_modified"]
        require("etag", source["etag"], *wanted(day, "etag"))
        require("last_modified", source["last_modified"], *wanted(day, "last_modified"))

    measured["gz_sha256"] = sha256_file(gz_path)
    require("gz_sha256", measured["gz_sha256"], *wanted(day, "gz_sha256"))

    inflated = inflate(gz_path, raw_path if unpack else None)
    measured["gzip_crc32"] = f"{inflated.crc32:08x}"
    measured["gzip_isize"] = inflated.isize
    measured["gzip_members"] = inflated.members
    require("gzip_crc32", measured["gzip_crc32"], *wanted(day, "gzip_crc32"))
    require("gzip_isize", measured["gzip_isize"], *wanted(day, "gzip_isize"))
    require("gzip_members", measured["gzip_members"], *wanted(day, "gzip_members"))

    measured["unpacked_bytes"] = inflated.unpacked_bytes
    require("unpacked_bytes", inflated.unpacked_bytes, *wanted(day, "unpacked_bytes"))
    if not raw_path.exists():
        raise VerifyError(f"{raw_path.name} is missing")
    require("unpacked_bytes on disk", raw_path.stat().st_size, ("the gzip stream", inflated.unpacked_bytes))

    measured["unpacked_sha256"] = inflated.sha256
    require("unpacked_sha256", inflated.sha256, *wanted(day, "unpacked_sha256"))
    require("unpacked_sha256 on disk", sha256_file(raw_path), ("the gzip stream", inflated.sha256))

    framing = walk_frames(raw_path)
    measured["messages"] = framing.messages
    measured["per_type_counts"] = framing.per_type_counts
    measured["last_message"] = framing.last_message
    require("messages", framing.messages, *wanted(day, "messages"))
    require("per_type_counts", framing.per_type_counts, *wanted(day, "per_type_counts"))
    require("last_message", framing.last_message, *wanted(day, "last_message"))
    return measured


# Whole-day operations


@contextlib.contextmanager
def run_lock(path):
    with open(path, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise FetchError(f"another run holds {path.name}") from error
        yield


def check_free_space(data_dir, compressed, unpacked):
    # Chunks and the assembled copy coexist, and the unpacked file joins them.
    need = 2 * compressed + (unpacked if unpacked else 3 * compressed)
    free = shutil.disk_usage(data_dir).free
    if free < need:
        raise FetchError(f"{free} bytes free in {data_dir}, {need} needed")


def unpacked_name(day):
    """The unpacked file's name: the manifest's unpacked_name if it gives one, else the name the
    gzip file has without its .gz. Nasdaq names some days S<MMDDYY>-v50.txt, and macOS types a
    .txt file as plain text, so Spotlight's text importer reads the whole binary day; such a
    day is unpacked under a name in the other days' MMDDYYYY.NASDAQ_ITCH50 form instead."""
    return day.get("unpacked_name", day["name"])


def fetch_day(day, data_dir, jobs, transport=curl_range, head=curl_head, sleep=time.sleep,
              chunk_size=CHUNK_SIZE):
    """Fetch and verify one day. Return (measured values, source of the bytes)."""
    name = day["name"]
    gz = data_dir / f"{name}.gz"
    raw = data_dir / unpacked_name(day)
    gz_tmp = data_dir / f".{name}.gz.tmp"
    raw_tmp = data_dir / f".{name}.unpack.tmp"
    source_path = data_dir / f".{name}.source.json"
    parts = data_dir / f".{name}.parts"
    data_dir.mkdir(parents=True, exist_ok=True)

    with run_lock(data_dir / f".{name}.lock"):
        gz_tmp.unlink(missing_ok=True)
        raw_tmp.unlink(missing_ok=True)
        try:
            if gz.exists():
                # A kept file gets the same checks as a new one.
                log(f"{gz.name} already here, verifying it")
                source = read_json(source_path) if source_path.exists() else None
                if raw.exists():
                    return verify_day(gz, raw, day, source, unpack=False), source
                measured = verify_day(gz, raw_tmp, day, source, unpack=True)
                os.replace(raw_tmp, raw)
                return measured, source

            remote = head(day["url"])
            log(f"remote: {remote.total} bytes, etag {remote.etag}, "
                f"last-modified {remote.last_modified}")
            require("advertised length", remote.total, *wanted(day, "compressed_bytes"))
            require("advertised etag", remote.etag, *wanted(day, "etag"))
            require("advertised last_modified", remote.last_modified, *wanted(day, "last_modified"))
            unpacked = day.get("unpacked_bytes") or day.get("expected", {}).get("unpacked_bytes")
            check_free_space(data_dir, remote.total, unpacked)

            chunks = plan_chunks(remote.total, chunk_size)
            prepare_parts(parts, {
                "url": day["url"],
                "total": remote.total,
                "etag": remote.etag,
                "last_modified": remote.last_modified,
                "chunk_size": chunk_size,
            })
            download_chunks(day["url"], remote, chunks, parts, jobs, transport, sleep)
            log("all chunks accepted, assembling")
            assemble(chunks, parts, gz_tmp)

            source = {"etag": remote.etag, "last_modified": remote.last_modified}
            try:
                measured = verify_day(gz_tmp, raw_tmp, day, source, unpack=True)
            except VerifyError:
                log(f"verification failed. Accepted chunks are kept in {parts.name}. "
                    "Delete that folder to fetch again from zero")
                raise
            write_json(source_path, source)
            os.replace(gz_tmp, gz)
            os.replace(raw_tmp, raw)
            shutil.rmtree(parts)
            return measured, source
        finally:
            gz_tmp.unlink(missing_ok=True)
            raw_tmp.unlink(missing_ok=True)


def verify_only(day, data_dir):
    """Recheck what is on disk. Touches no network and writes nothing."""
    name = day["name"]
    gz = data_dir / f"{name}.gz"
    raw = data_dir / unpacked_name(day)
    for path in (gz, raw):
        if not path.exists():
            raise VerifyError(f"{path} is missing")
    source_path = data_dir / f".{name}.source.json"
    source = read_json(source_path) if source_path.exists() else None
    return verify_day(gz, raw, day, source, unpack=False), source


PINNED_FIELDS = (
    "compressed_bytes", "etag", "last_modified", "gz_sha256", "gzip_crc32", "gzip_isize",
    "gzip_members", "unpacked_bytes", "unpacked_sha256", "messages", "per_type_counts",
    "last_message",
)


def pin_day(day, measured):
    """Write measured values into a day, once. Return False if it was already pinned."""
    if day.get("gz_sha256") is not None:
        return False
    missing = [field for field in PINNED_FIELDS if measured.get(field) is None]
    if missing:
        raise FetchError(f"cannot pin without {missing}")
    for field in PINNED_FIELDS:
        day[field] = measured[field]
    third_party = day.get("third_party", {})
    agreed = [f for f in ("gz_sha256", "unpacked_sha256") if third_party.get(f) is not None]
    third_party["agrees"] = agreed  # verify_day has already failed on any difference
    return True


# External reference files


def advertised_ftp_length(url):
    result = subprocess.run(CURL + ["--head", "--max-time", "120", url],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    match = re.search(r"^Content-Length:\s*(\d+)", result.stdout, re.M | re.I)
    return int(match.group(1)) if match else None


def plain_get(url, body_path, header_path):
    """Fetch a small file. Raise FetchError unless the transfer shows it arrived whole."""
    advertised = advertised_ftp_length(url) if url.startswith("ftp://") else None
    result = subprocess.run(
        CURL + ["--location", "--speed-limit", "1024", "--speed-time", "60", "--max-time", "1800",
                "--dump-header", str(header_path), "--output", str(body_path), url],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    if result.returncode != 0:
        raise FetchError(f"curl exit {result.returncode}: {result.stderr.strip()}")
    if not url.startswith("ftp://"):
        status, headers = parse_headers(header_path.read_text(errors="replace"))
        if status != 200:
            raise FetchError(f"status {status}, expected 200")
        length = headers.get("content-length")
        advertised = int(length) if length and length.isdigit() else None
    got = body_path.stat().st_size
    if advertised is not None and got != advertised:
        raise FetchError(f"received {got} bytes, advertised {advertised}")
    if advertised is None:
        log(f"no advertised length for {url}")


def verify_reference(path, ref):
    length = path.stat().st_size
    require("bytes", length, ("the pinned manifest", ref.get("bytes")))
    digest = sha256_file(path)
    require("sha256", digest, ("the pinned manifest", ref.get("sha256")))
    if length == 0:
        raise VerifyError(f"{path.name} is empty")
    if ref["format"] == "zip":
        try:
            with zipfile.ZipFile(path) as archive:  # read only, never extracted
                bad = archive.testzip()
        except zipfile.BadZipFile as error:
            raise VerifyError(f"{path.name} is not a zip: {error}") from error
        if bad is not None:
            raise VerifyError(f"{path.name}: bad CRC in member {bad}")
    elif path.read_bytes()[:64].lstrip().startswith(b"<"):
        raise VerifyError(f"{path.name} looks like an HTML page, not a data file")
    return {"bytes": length, "sha256": digest}


def fetch_reference(ref, data_dir, sleep=time.sleep, attempts=5):
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / ref["filename"]
    if path.exists():
        log(f"{path.name} already here, verifying it")
        return verify_reference(path, ref)
    body = data_dir / f".{ref['filename']}.tmp"
    header = data_dir / f".{ref['filename']}.hdr"
    try:
        for attempt in range(1, attempts + 1):
            body.unlink(missing_ok=True)
            try:
                plain_get(ref["url"], body, header)
                measured = verify_reference(body, ref)
                os.replace(body, path)
                return measured
            except VerifyError:
                raise
            except FetchError as reason:
                log(f"{path.name} attempt {attempt} failed: {reason}")
                if attempt == attempts:
                    raise
                sleep(backoff(attempt))
    finally:
        body.unlink(missing_ok=True)
        header.unlink(missing_ok=True)


# Command line


def resolve_dir(value):
    """Default: data/ at the repo root. An explicit path follows normal CLI rules."""
    return REPO_ROOT / "data" if value is None else Path(value).resolve()


def run_day(key, manifest, args):
    day = manifest["days"][key]
    data_dir = resolve_dir(args.dir)
    log(f"day {key}: {day['name']}, unpacked as {unpacked_name(day)}, in {data_dir.name}/, "
        f"mode "
        f"{'verify-only' if args.verify_only else 'fetch'}")
    if args.verify_only:
        measured, _ = verify_only(day, data_dir)
    else:
        measured, _ = fetch_day(day, data_dir, args.jobs)
    print(json.dumps(measured, indent=2), flush=True)
    log(f"day {key}: every check passed")
    if args.pin:
        if pin_day(day, measured):
            write_json(MANIFEST, manifest)
            log(f"day {key}: pinned in {MANIFEST.name}")
        else:
            log(f"day {key}: already pinned, and nothing differs")
    elif day.get("gz_sha256") is None:
        log(f"day {key}: not pinned. Rerun with --pin to write these values")


def run_reference(key, manifest, args):
    ref = manifest["references"][key]
    data_dir = resolve_dir(args.dir)
    if args.verify_only:
        path = data_dir / ref["filename"]
        if not path.exists():
            raise VerifyError(f"{path} is missing")
        measured = verify_reference(path, ref)
    else:
        measured = fetch_reference(ref, data_dir)
    log(f"reference {key}: {measured}")
    if args.pin:
        if ref.get("sha256") is None:
            ref.update(measured)
            write_json(MANIFEST, manifest)
            log(f"reference {key}: pinned in {MANIFEST.name}")
        else:
            log(f"reference {key}: already pinned, and nothing differs")


def parse_args(argv, manifest):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--day", choices=sorted(manifest["days"]), help="ITCH day to fetch")
    parser.add_argument("--ref", choices=sorted(manifest["references"]) + ["all"],
                        help="external reference file to fetch, or all of them")
    parser.add_argument("--verify-only", action="store_true", help="recheck what is on disk")
    parser.add_argument("--pin", action="store_true", help="write measured values for an unpinned item")
    parser.add_argument("--dir", help="data folder. Default: data/ at the repo root. "
                                      "A relative path is taken from the working directory")
    parser.add_argument("--jobs", type=int, default=4, help="parallel chunk workers (default 4)")
    args = parser.parse_args(argv)
    if not args.day and not args.ref:
        parser.error("give --day or --ref")
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    return args


def main(argv=None):
    manifest = read_json(MANIFEST)
    args = parse_args(argv, manifest)
    try:
        if args.day:
            run_day(args.day, manifest, args)
        if args.ref:
            keys = sorted(manifest["references"]) if args.ref == "all" else [args.ref]
            for key in keys:
                run_reference(key, manifest, args)
    except FetchError as error:
        print(f"FAILED: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
