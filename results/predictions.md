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
