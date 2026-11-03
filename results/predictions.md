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

## P-0008: every book design on macOS, 12302019, the file in memory

- Experiment: `bench_replay` through `scripts/bench_driver.py` on `12302019.NASDAQ_ITCH50`, `--input-mode ram`, top of book not tracked, 3 repetitions, each running `hash-linked`, `hash-flat`, `paged-linked`, `paged-flat`, `map-fresh` and `map-aged` in turn, each in a fresh timed process and a fresh untimed one, on `macOS (M4 Max)`, not pinned, through `measure.sh` and `/usr/bin/time -l`.
- What it is for: T-BOOK-1, T-BOOK-3, T-LAT-1 to T-LAT-5 and T-DESIGN-1 for every design, in one run, on the Mac. The x86 bars are context here, not pass or fail.
- Prediction, winner: `paged-flat` has the highest median throughput, and `paged-linked` is second. The paged index finds an order by its reference with no probe, and references arrive close to in order.
- Prediction, throughput: every fast arm between 8 and 20 million messages/s, the winner near 14 million. `map-fresh` between 2.5 and 6 million, `map-aged` below it, between 2 and 5 million. CV under 10% on every design, since the Mac is not pinned.
- Prediction, T-DESIGN-1: the winner's ratio over `map-fresh` between 2.5 and 5, most likely near 3.5, so the ≥ 4× bar is missed against the fresh heap. Its ratio over `map-aged` between 3 and 7, and met.
- Prediction, memory: book memory near 0.21 GiB for `hash-linked`, between 0.6 and 0.8 GiB for the paged arms, which hold 418 MiB of slots, and between 0.3 and 0.9 GiB for the map designs. Every design meets T-BOOK-3's 1.4 GB.
- Prediction, latency: on the fast arms the all-message p50 is below the 166.667 ns floor, and so is every per-operation p50. p99.9 is resolved on every design. The mean service time is under 125 ns on every fast arm, so T-LAT-3's mean is met, and the batch mean per book message is within 2% of it.
- Prediction, harness: every repetition completes, every page of the input is resident at both ends of each timed pass, no message is rejected, and every design counts the same mix of 264,478,375 book messages.
- Prediction, peak memory: under 10.5 GiB from `/usr/bin/time -l`: 7.68 GiB of input, 1.25 GiB of latency samples and the book.

## P-0009: the fast arms on macOS, 12302019, the file mapped and the top of book tracked

- Experiment: as P-0008, with `--input-mode mapped` and `--track-top`, for the four fast arms only, in its own `measure.sh` run after P-0008's.
- What it is for: T-BOOK-2, itch-book's `itch-replay --book`, which maps the file and checks the best bid and ask after every book event.
- Prediction: the same arm wins as in P-0008. Each arm runs at between 70% and 95% of its P-0008 throughput, the winner between 7 and 18 million messages/s. Every arm counts the same number of moves of the top, in both passes of every repetition.
- Prediction, peak memory: under 10.5 GiB, with the mapped file counted in resident memory but not in the book's figure.

## P-0010: every book design on debian, 12302019, the file in memory

- Experiment: as P-0008, on `debian`, pinned to core 4: `bench_replay` through `scripts/bench_driver.py --core 4` on `12302019.NASDAQ_ITCH50`, `--input-mode ram`, top of book not tracked, 3 repetitions, each running `hash-linked`, `hash-flat`, `paged-linked`, `paged-flat`, `map-fresh` and `map-aged` in turn, each in a fresh timed process and a fresh untimed one, as `measure.sh taskset -c 4`, from a commit exported by `debian_sync.sh`.
- What it is for: T-BOOK-1, T-BOOK-3, T-LAT-1 to T-LAT-5 and T-DESIGN-1 for every design on the headline host, with the hardware counters, and the choice of the default arm. `debian` is a Ryzen Embedded V1605B, Zen 1 at up to 3.6 GHz with 512 KB of L2 and 4 MB of L3, so every book is far larger than its last level, as on the Mac, but with a quarter of the Mac's cache in front of DRAM.
- Prediction, fit: the day fits in memory. Before the run `MemAvailable` is above 11 GiB, and the largest design needs about 9.4 GiB of anonymous memory: 7.68 GiB of input, 1.00 GiB of latency samples and at most 0.65 GiB of book. Every repetition completes, every page of the input is resident at both ends of every timed pass, and the kernel's `pswpout` count does not move during the run.
- Prediction, winner and ranking: the Mac's order holds, `paged-flat`, `hash-flat`, `paged-linked`, `hash-linked`, `map-fresh`, `map-aged`. Flat levels beat linked levels on either index by more than they did on the Mac, because a walk down a linked side is a chain of dependent loads, and each one costs more on `debian`.
- Prediction, throughput: every design between 2.5 and 3.5 times slower than on the Mac. `paged-flat` between 5 and 10 million messages/s, most likely near 7; `hash-flat` near 5.5; `paged-linked` near 4.5; `hash-linked` near 3.8, every fast arm above 2.5 million. `map-fresh` between 1.2 and 3 million, near 2; `map-aged` below it. CV under 3% on every design, since the core is pinned.
- Prediction, T-DESIGN-1: `paged-flat` over `map-fresh` between 3 and 5, most likely near 3.8, so the ≥ 4× bar is likely missed against the fresh heap; over `map-aged` between 3.3 and 5.5, near 4.2. Every other arm misses both.
- Prediction, T-BOOK-1: missed by every arm, the best by 6 million messages/s or more. The reason is the host: the bar was set on a Ryzen 7 9700X, Zen 5 at up to 5.5 GHz with 32 MB of L3.
- Prediction, T-BOOK-3: met on every arm, each book's memory within 10% of its Mac figure: near 0.21 GiB on the hash arms and 0.63 to 0.65 GiB on the paged arms.
- Prediction, latency: the floor is 40.04 ns and the empty pair near 19 ns, so the all-message p50 is resolved on every design. On `paged-flat`: p50 between 60 and 120 ns raw, p99 between 400 and 1,000 ns, p99.9 between 800 and 2,000 ns. T-LAT-2's p50 bar of 121 ns is met on the flat arms and its p99 and p99.9 are missed; T-LAT-5 is missed at p99 and p99.9; T-LAT-4's p50 is met on the flat arms and its p99 is missed, and its batch mean is between 100 and 200 ns a book message on `paged-flat`, against the bar of 142.36. T-LAT-3's mean of 393 ns is met on every fast arm and its p99.9 of 750 ns is missed on every fast arm. T-LAT-1 is missed on `replace` at p99 and p99.9 on every arm, and on the linked arms by more than 2 µs.
- Prediction, counters: every event measured at running ratio 1.0000 and `rdpmc` agreeing with `read()` within the harness's limit. Per message, through `rdpmc`: `r0843:u`, a floor on demand fills from DRAM, between 0.2 and 1.5 on the flat arms, fewer on each flat arm than on the linked arm with the same index, and more on each map design than on every fast arm. `cache-misses`, which counts L2 misses on Zen 1, between 1.5 and 5 times `r0843:u` on every design. Instructions per message on the map designs at least 2.5 times those on `paged-flat`, as on the Mac. Branch misses per message higher on each map design than on every fast arm.
- Prediction, peak memory: peak RSS from `getrusage`, largest repetition, between 9.0 and 9.8 GiB on every design.

## P-0011: the fast arms on debian, 12302019, the file mapped and the top of book tracked

- Experiment: as P-0010, with `--input-mode mapped` and `--track-top`, for the four fast arms only, in its own `measure.sh` run after P-0010's.
- What it is for: T-BOOK-2 on the headline host, itch-book's `itch-replay --book` method.
- Prediction, fit: the mapped file stays in the page cache. Every repetition completes, every page is resident at both ends of every timed pass, and `pswpout` does not move.
- Prediction: the same arm wins as in P-0010, and the arms keep P-0010's order. Each arm runs at between 75% and 95% of its P-0010 throughput, and the winner between 4 and 9 million messages/s, so T-BOOK-2's 12.5 million is missed by every arm, for the host's reason given in P-0010. Every arm counts the same moves of the top as on the Mac, 124,767,189, in both passes of every repetition.
- Prediction, peak memory: peak RSS between 9.0 and 9.8 GiB, with the mapped file counted in resident memory but not in the book's figure.
