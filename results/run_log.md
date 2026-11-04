# Run log

One row for every run of `scripts/bench_driver.py`, failed ones included. Rows are never changed or removed.

| Run | Prediction | Commit | OS | Outcome |
|---|---|---|---|---|
| P-0001-20261031T083536Z | P-0001 | 747d316af3f5 (clean) | Darwin | published |
| P-0002-20261031T090306Z | P-0002 | 9392a9ba8a5b (exported by debian_sync.sh) | Linux | chase verdict PASS |
| P-0003-20261031T091318Z | P-0003 | 20cdcde135b0 (exported by debian_sync.sh) | Linux | chase verdict FAIL |
| P-0004-20261031T091341Z | P-0004 | unknown | Linux | refused at repetition 1: exited 3: input not resident: 2014349 of 2014504 pages resident; input not resident: 2014349 of 2014504 pages resident |
| P-0006-20261031T091906Z | P-0006 | 92e3f74c5766 (exported by debian_sync.sh) | Linux | published |
| P-0008-20261103T082340Z | P-0008 | 3e5b67a8cefe (clean) | Darwin | refused at repetition 1, hash-linked: exited 2 with no record |
| P-0008-20261103T082514Z | P-0008 | 9934df5c3b7a (clean) | Darwin | published |
| P-0009-20261103T090210Z | P-0009 | f3fe97a93cea (clean) | Darwin | published |
| P-0010-20261103T105131Z | P-0010 | bfdd4bb117a7 (exported by debian_sync.sh) | Linux | stopped by hand at repetition 1, hash-linked, before any record: it ran at nice 5, which the login shell gives a background job, and the kernel swapped out 2,290 pages of other processes in its first minute, while the day was hashed and read into memory; the driver writes no row when signalled, so this row is typed, with the time measure.sh started it |
| P-0010-20261103T105404Z | P-0010 | bfdd4bb117a7 (exported by debian_sync.sh) | Linux | stopped at repetition 1, hash-linked, by the host: unattended-upgrades rebooted it at its set time of 04:02 local, after the first timed record and before the untimed one; nothing from it is published; the reboot reset perf_event_paranoid to 3, so later runs have no counters; typed, with the time measure.sh started it, since the driver writes no row when signalled |
| P-0010-20261103T110708Z | P-0010 | bfdd4bb117a7 (exported by debian_sync.sh) | Linux | refused by hand at repetition 1, hash-flat, untimed process: the mode with the day in memory does not fit on debian without swapping; each process copies the 7.68 GiB day into anonymous memory while the file still fills the page cache, and at the four process starts the kernel swapped out 4, 1,590, 31,210 and 2,455 pages of other processes, 35,259 in all (138 MiB), though MemAvailable never fell below 5.24 GiB, no page moved during a timed span and every input page stayed resident; nothing from it is published, and the mapped mode is kept; typed, since the driver writes no row when signalled |
| P-0011-20261103T112023Z | P-0011 | bfdd4bb117a7 (exported by debian_sync.sh) | Linux | published |
| P-0012-20261104T044034Z | P-0012 | 2810183f1d95 (exported by debian_sync.sh) | Linux | refused at repetition 1, map-aged: the kernel's pswpout moved by 1 pages since the run began, by the end of this process: the run does not fit beside the host's other work |
| P-0013-20261104T054149Z | P-0013 | 2810183f1d95 (exported by debian_sync.sh) | Linux | published |
| P-0014-20261104T071049Z | P-0014 | 0ffe738913e4 (exported by debian_sync.sh) | Linux | refused at machine_check: machine_check: refused, 1 failed |
