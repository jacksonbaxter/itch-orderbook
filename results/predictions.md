# Predictions

Each experiment's prediction, committed before it runs and never edited (criterion 31).
`scripts/check_predictions.py` holds every `results/bench_*.md` to this file and to the
order of the commits. What became of each run is in `results/run_log.md`, failed runs
included.

## P-0001: stub-book harness proof on macOS, 12302019

- Experiment: `bench_replay` on `12302019.NASDAQ_ITCH50`, fresh process per repetition, 3 repetitions, on `macOS (M4 Max)`, through `measure.sh` and `/usr/bin/time -l`.
- What it is for: showing that the harness works on a whole day. The book is the Phase 2 stub, so this says nothing about the book Phase 3 builds.
- Prediction, harness: every page of the input is resident before each timed pass, all 3 repetitions complete, and no pin is claimed.
- Prediction, timer: the quantum is 41.67 ns and the floor 166.67 ns, so p50 and p90 are reported as unresolved.
- Prediction, throughput: between 3 and 15 million messages/s, CV under 5%.
- Prediction, instrumentation: the latency pass takes between 1.0 and 1.6 times the throughput pass.
- Prediction, memory: peak RSS near 9.6 GiB, from the mapped file, 1.0 GiB of samples and the book, and it fits in 64 GB.

## P-0002: pointer chase against the Zen 1 counters on debian

- Experiment: `bench_chase`, 3 fresh processes, 20,000,000 dependent loads per size, one random cycle over every line, 4 KB pages, at 64 KB, 128 KB, 256 KB, 1 MB, 2 MB, 3 MB, 8 MB, 16 MB, 64 MB and 256 MB, on `debian`, through `measure.sh` and `taskset -c 4`.
- What it is for: deciding whether raw `r0843:u` may stand in for last-level misses, and what `cache-misses` counts. `debian` has 512 KB of L2 per core and 4 MB of L3.
- Prediction, well under L2: `r0843:u` gives at most 0.01 fills per load at 64 KB and 128 KB.
- Prediction, far above L3: `r0843:u` gives between 0.90 and 1.10 fills per load at 64 MB and 256 MB.
- Prediction, between L2 and L3: `cache-misses` gives between 0.70 and 1.30 per load at 1 MB, 2 MB and 3 MB, and at each of those sizes it stands at least 0.50 per load above `r0843:u`, so it counts L2 misses and not last-level misses.
- Prediction, the read paths: in every row, for `r0843:u` and `cache-misses`, `rdpmc` and `read()` differ by at most 0.001 per load, and each event's running ratio is 1.0000.
- Prediction, the kernel's mapping: sysfs gives `cache-misses` as `event=0x64,umask=0x09`, and raw `r0964:u`, counted in the same group, stays within 0.001 per load of `cache-misses` in every row.
- All of this holds in each of the 3 runs, not in their median alone. If any of it fails, no last-level number is published, the gap is stated, and the failed run stays in `results/run_log.md`.

## P-0003: the chase with a deliberately wrong event

- Experiment: `bench_chase --raw-event 0x0143`, one process, the same sizes and loads as P-0002, on `debian`, through `measure.sh` and `taskset -c 4`, judged by the same `scripts/chase_report.py`.
- What it is for: showing on real hardware that the verdict can fail. `0x0143` is PMCx043 bit 0, fills from local L2, so it counts the opposite end of the hierarchy. The file stays in the research cache and nothing from it is published.
- Prediction: the verdict is FAIL. The encoding criterion fails, the under-L2 criterion fails with close to one fill per load at 64 KB and 128 KB, and the above-L3 criterion fails with close to none at 64 MB and 256 MB.

## P-0004: stub-book harness proof on debian, 12302019

- Experiment: `bench_replay` on `12302019.NASDAQ_ITCH50`, fresh process per repetition, 3 repetitions, on `debian`, through `measure.sh`, `taskset -c 4` and `scripts/bench_driver.py`.
- What it is for: showing that the harness works on a whole day on Linux, pinned, with hardware counters. The book is the Phase 2 stub, so this says nothing about the book Phase 3 builds.
- Prediction, harness: every page of the input is resident before each timed pass, all 3 repetitions complete, the pin reads back as core 4, and no migration is seen.
- Prediction, timer: the quantum is 10.01 ns and the floor 40.04 ns, the calibrated rate is near 1998 MHz, and p50 is resolved, because `apply()` on this CPU costs well over the floor.
- Prediction, throughput: between 2 and 10 million messages/s, CV under 5%.
- Prediction, instrumentation: the latency pass takes between 1.05 and 1.60 times the throughput pass.
- Prediction, counters: every event is measured at running ratio 1.0000, `rdpmc` and `read()` agree within the harness's limit of 1% or 10,000 events over the pass, and per-message counts are published.
- Prediction, memory: peak RSS near 8.9 GiB: 7.68 GiB of mapped file, 1.00 GiB of samples and the book. It fits in 15 GB.

## P-0005: flamegraph proof on debian

- Experiment: `perf record -g --user-callchains -e cycles:u` on `bench_replay` on `12302019.NASDAQ_ITCH50`, one process, on a build with frame pointers, through `measure.sh` and `taskset -c 4`, folded with the FlameGraph scripts.
- What it is for: showing that a profile can be taken at `perf_event_paranoid=2`. The book is the Phase 2 stub, so what the graph shows says nothing about the real book.
- Prediction: `perf record` succeeds, the folded stacks run from `main` down to `FastBook` `apply`, and no kernel frame appears.

## P-0006: stub-book harness proof on debian, 12302019, after the residency fix

- Experiment: as P-0004, on a harness that asks about residency after the pass's allocations, brings back any page they pushed out, and asks again when the pass ends. P-0004 was refused at its first timed pass, with 155 of 2,014,504 pages gone, so none of its other predictions was tested. They are repeated here and it stands as written.
- Prediction, harness: every page of the input is resident at the start and at the end of each timed pass, all 3 repetitions complete, the pin reads back as core 4, and no migration is seen.
- Prediction, timer: the quantum is 10.01 ns and the floor 40.04 ns, the calibrated rate is near 1998 MHz, and p50 is resolved, because `apply()` on this CPU costs well over the floor.
- Prediction, throughput: between 2 and 10 million messages/s, CV under 5%.
- Prediction, instrumentation: the latency pass takes between 1.05 and 1.60 times the throughput pass.
- Prediction, counters: every event is measured at running ratio 1.0000, `rdpmc` and `read()` agree within the harness's limit of 1% or 10,000 events over the pass, and per-message counts are published.
- Prediction, memory: peak RSS near 8.9 GiB: 7.68 GiB of mapped file, 1.00 GiB of samples and the book. It fits in 15 GB.

## P-0007: T-INV-3 and T-TRADE-3 on both days

- Experiment: `scripts/check_real_day.sh --only oracle`, then `--only trades`, on `12302019` and `10302019`, on `macOS (M4 Max)`, through `heavy.sh` and `/usr/bin/time -l`, on the Phase 2 stub book.
- What it is for: the user adopted T-INV-3 on the condition that it holds on 10302019, and chose the SEC's round-lot rule after T-TRADE-3 failed on its odd-lot columns. The oracle has not run on 10302019 before.
- Prediction: T-INV-3 has 0 violations on both days, and T-TRADE-3 passes on both days.
