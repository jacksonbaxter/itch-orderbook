#!/usr/bin/env python3
"""What every driver keeps of a run, beside its raw records, so that the run can be told apart
from any other and its file rendered again from what it saved.

Each run writes into a raw folder named by its run id, under the --raw-dir it is given, and
refuses one that exists and is not empty, so that no run writes over another's evidence
(r5-13). Into it, run.json, of RECORD_VERSION, written by the driver:

- run_id;
- commit: the checkout's HEAD and whether the tree was clean, CMakePresets.json included;
- binaries: the sha256 of each binary the driver runs, read from the file it runs (r5-9);
- dataset: the input's name and sha256, hashed from the file itself, and, where
  scripts/data_manifest.json pins that day, the pin it was checked against (r5-6): never
  copied from the manifest;
- outputs: the name and sha256 of every tool output the results file quotes, each kept
  untouched in outputs/: the machine checks, the cross-core check, the sampler log, and
  measure.sh's stderr, which stamp_load.py --raw-dir adds once the run is stamped.

A render refuses a record of this kind that lacks any of them (missing()). A raw folder with
no run.json is of the kind made before, and renders as it did: the kind is told by the
version the driver wrote, never by a date.

A library, not a command.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST = SCRIPT_DIR / "data_manifest.json"
RECORD_NAME = "run.json"
RECORD_VERSION = 1
FIELDS = ("version", "driver", "run_id", "commit", "binaries", "dataset", "outputs")
MEASURE_STDERR = "measure.stderr"


class Incomplete(Exception):
    """A raw folder that cannot be the record of a run of the new kind. stage, as a driver's
    Refused has, so that the driver's run-log row can name it."""

    stage = "the raw record"


def run_folder(parent, run_id):
    """The run's own raw folder, parent/run_id, made empty, or refused."""
    folder = Path(parent) / run_id
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise Incomplete(f"the raw folder {folder} exists and is not empty, and a run does not "
                         "write over another's record")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def pinned(name, manifest=MANIFEST):
    """The sha256 scripts/data_manifest.json pins for a day's file, or None."""
    try:
        days = json.loads(Path(manifest).read_text(encoding="utf-8")).get("days", {})
    except (OSError, ValueError):
        return None
    for entry in days.values():
        if name in (entry.get("name"), entry.get("unpacked_name")):
            return entry.get("unpacked_sha256")
    return None


class Record:
    """run.json as the driver builds it up; write() saves it."""

    def __init__(self, folder, driver, run_id, commit):
        self.folder = Path(folder)
        self.data = {"version": RECORD_VERSION, "driver": driver, "run_id": run_id,
                     "commit": commit, "binaries": {}, "dataset": None, "outputs": {}}

    def binary(self, path):
        """Records the sha256 of a binary the driver runs, by the path it runs it at."""
        if not Path(path).is_file():
            raise Incomplete(f"{path}, a binary the run needs, is not there to hash")
        self.data["binaries"][str(path)] = sha256_of(path)
        self.write()

    def dataset(self, path, digest, manifest=MANIFEST):
        """The input's name and the sha256 the driver hashed from it, checked against the
        manifest's pin where there is one."""
        name = Path(path).name
        pin = pinned(name, manifest)
        if pin is not None and pin != digest:
            raise Incomplete(f"{name} hashes to {digest}, not the {pin} that "
                             "scripts/data_manifest.json pins")
        self.data["dataset"] = {"name": name, "sha256": digest,
                                "manifest": "matches its pin" if pin else
                                "not pinned in scripts/data_manifest.json"}
        self.write()

    def output(self, name, text):
        """Keeps a tool's output untouched in outputs/name."""
        outputs = self.folder / "outputs"
        outputs.mkdir(exist_ok=True)
        path = outputs / name
        if path.exists():
            raise Incomplete(f"outputs/{name} is kept already")
        path.write_text(text, encoding="utf-8")
        self.data["outputs"][name] = sha256_of(path)
        self.write()

    def kept_file(self, name):
        """Records a file the driver wrote into the folder itself, such as sampler.txt."""
        self.data["outputs"][name] = sha256_of(self.folder / name)
        self.write()

    def write(self):
        (self.folder / RECORD_NAME).write_text(json.dumps(self.data, indent=1) + "\n",
                                               encoding="utf-8")


def open_run(args, run_id):
    """args.raw_dir, from here on, is the run's own folder under the one given."""
    args.raw_dir = run_folder(args.raw_dir, run_id)


def start_record(args, driver, run_id, commit, binaries):
    """run.json begun, as args.record, with the sha256 of each binary the driver runs."""
    args.record = Record(args.raw_dir, driver, run_id, commit)
    for binary in binaries:
        args.record.binary(binary)
    return args.record


def close_sampler(args, sampler):
    """Stops the sampler and records the log it kept in the run's folder."""
    if sampler is None:
        return
    sampler.done.set()
    sampler.thread.join(timeout=30)
    record = getattr(args, "record", None)
    if record is not None and (record.folder / "sampler.txt").is_file():
        record.kept_file("sampler.txt")


def add_measure_stderr(folder, run_id, stderr_path):
    """stamp_load.py's part: measure.sh's stderr, byte for byte, into the run's record."""
    folder = Path(folder)
    try:
        record = json.loads((folder / RECORD_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Incomplete(f"{folder} holds no run.json to add measure.sh's stderr to") from error
    if record.get("run_id") != run_id:
        raise Incomplete(f"{folder} is the record of {record.get('run_id')}, not {run_id}")
    body = Path(stderr_path).read_bytes()
    path = folder / "outputs" / MEASURE_STDERR
    if path.exists() and path.read_bytes() != body:
        raise Incomplete(f"{folder} holds another measure.sh stderr already")
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(body)
    record["outputs"][MEASURE_STDERR] = sha256_of(path)
    (folder / RECORD_NAME).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")


def missing(folder, needs=(), run_id=None, measure=True):
    """What a raw folder of the new kind lacks, [] for a complete one or one of the old kind
    (no run.json). needs names the outputs its results file quotes; measure, that measure.sh
    wrapped the driver, so its stderr is one (simd_driver and udp_driver run measure.sh on
    each process, and each process's .err in the folder holds its lines)."""
    folder = Path(folder)
    path = folder / RECORD_NAME
    if not path.is_file():
        return []
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return ["run.json, which is not JSON"]
    gaps = [f"the {name} field" for name in FIELDS if not record.get(name)]
    if record.get("version") != RECORD_VERSION:
        gaps.append(f"version {RECORD_VERSION}, not {record.get('version')!r}")
    if run_id is not None and record.get("run_id") != run_id:
        gaps.append(f"the run id {run_id}, not {record.get('run_id')!r}")
    if folder.name != record.get("run_id"):
        gaps.append(f"a folder named by its run id, not {folder.name}")
    commit = record.get("commit") or ""
    if commit and not commit.endswith(("(clean)", "(with uncommitted changes)",
                                       "(exported by debian_sync.sh)")):
        gaps.append("whether the tree was clean")
    dataset = record.get("dataset") or {}
    if dataset and not (dataset.get("name") and len(dataset.get("sha256") or "") == 64):
        gaps.append("the dataset's name and sha256")
    for name, digest in (record.get("binaries") or {}).items():
        if len(digest or "") != 64:
            gaps.append(f"the sha256 of {name}")
    outputs = record.get("outputs") or {}
    for name in (*needs, *((MEASURE_STDERR,) if measure else ())):
        if name not in outputs:
            gaps.append(f"{name}, untouched")
    for name, digest in outputs.items():
        kept = folder / "outputs" / name if (folder / "outputs" / name).is_file() else folder / name
        if not kept.is_file() or sha256_of(kept) != digest:
            gaps.append(f"{name} as it was kept")
    return gaps


def refuse_incomplete(folder, needs=(), run_id=None, measure=True):
    gaps = missing(folder, needs, run_id, measure)
    if gaps:
        raise Incomplete(f"the raw record {Path(folder).name} lacks " + "; ".join(gaps))


def is_new_kind(folder):
    return (Path(folder) / RECORD_NAME).is_file()


def kept_output(folder, name):
    """A tool's output as the run kept it."""
    return (Path(folder) / "outputs" / name).read_text(encoding="utf-8")
