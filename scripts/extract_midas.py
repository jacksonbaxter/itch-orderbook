#!/usr/bin/env python3
"""Extracts one month's CSV from its quarter's pinned MIDAS zip, into the gitignored build tree.

    python3 -B -I scripts/extract_midas.py --member dec19.csv [--out-dir build/reference]

Each member belongs to one quarter's zip, named in MEMBERS by its key in
scripts/data_manifest.json. The zip is checked against the SHA-256 there first. The file is
untrusted data, so run this with -I. Only the member named here is read, and its name
never reaches a path unless it is one of the names listed in MEMBERS.

Exit codes: 0 extracted, or already there with the right size and CRC. 1 the zip or the
member is not what the manifest says. 2 bad usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# The months of the sessions in the manifest, each with the quarter's zip that holds it.
MEMBERS = {
    "jan19.csv": "midas_2019q1",
    "mar19.csv": "midas_2019q1",
    "may19.csv": "midas_2019q2",
    "jul19.csv": "midas_2019q3",
    "aug19.csv": "midas_2019q3",
    "oct19.csv": "midas_2019q4",
    "dec19.csv": "midas_2019q4",
    "jan20.csv": "midas_2020q1",
}
CHUNK = 1 << 20


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def crc_of(path):
    crc = 0
    with open(path, "rb") as stream:
        while chunk := stream.read(CHUNK):
            crc = zlib.crc32(chunk, crc)
    return crc


def already_extracted(path, info):
    return (path.is_file() and path.stat().st_size == info.file_size
            and crc_of(path) == info.CRC)


def extract(archive, member, out_dir):
    with zipfile.ZipFile(archive) as bundle:
        try:
            info = bundle.getinfo(member)
        except KeyError:
            print(f"midas: {archive} has no {member}", file=sys.stderr)
            return 1
        target = out_dir / member
        if already_extracted(target, info):
            print(f"midas: {member} is already extracted, {info.file_size} bytes")
            return 0
        out_dir.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".partial")
        with bundle.open(info) as source, open(partial, "wb") as sink:
            while chunk := source.read(CHUNK):
                sink.write(chunk)
        os.replace(partial, target)
    print(f"midas: extracted {member}, {info.file_size} bytes")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--member", required=True, choices=sorted(MEMBERS))
    parser.add_argument("--zip", type=Path, help="default data/<the manifest's file name>")
    parser.add_argument("--manifest", type=Path,
                        default=REPO_ROOT / "scripts" / "data_manifest.json")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "build" / "reference")
    args = parser.parse_args(argv)

    key = MEMBERS[args.member]
    reference = json.loads(args.manifest.read_text(encoding="utf-8"))["references"][key]
    archive = args.zip or REPO_ROOT / "data" / reference["filename"]
    if not archive.is_file():
        print(f"midas: no {archive}. Run: python3 scripts/fetch_data.py --ref {key}",
              file=sys.stderr)
        return 1
    found = sha256_of(archive)
    if found != reference["sha256"]:
        print(f"midas: {archive.name} has SHA-256 {found}, manifest {reference['sha256']}",
              file=sys.stderr)
        return 1
    return extract(archive, args.member, args.out_dir)


if __name__ == "__main__":
    sys.exit(main())
