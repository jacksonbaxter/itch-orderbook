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

## P-0012: every book design on debian, 12302019, the file in memory, read in pieces

- Experiment: P-0010 again, from a commit whose in-memory mode no longer holds the file's page cache and its copy whole at once: `bench_replay --input-mode ram` now reads the day in pieces of 64 MiB into an arena that is not prefaulted, so each page is faulted in by the read that fills it, and drops each piece's page cache with `posix_fadvise(POSIX_FADV_DONTNEED)` as soon as it is copied. The driver's hash still leaves the day cached, which `machine_check`'s residency check needs, and the first copy drains that cache a piece at a time. The driver now refuses the run if the kernel's `pswpout` moves between the start of the run and the end of any process, and writes its own run-log row if a signal stops it. As P-0010 otherwise: `bench_replay` through `scripts/bench_driver.py --core 4` on `12302019.NASDAQ_ITCH50`, `--input-mode ram`, top of book not tracked, 3 repetitions, each running `hash-linked`, `hash-flat`, `paged-linked`, `paged-flat`, `map-fresh` and `map-aged` in turn, each in a fresh timed process and a fresh untimed one, on `debian`, pinned to core 4, as `measure.sh taskset -c 4`, from a commit exported by `debian_sync.sh`, with `perf_event_paranoid` at 2.
- What it is for: T-BOOK-1 and T-DESIGN-1 on `debian`, which P-0010 never measured, with T-BOOK-3, T-LAT-1 to T-LAT-5 and the counters for every design, and whether the default arm, chosen from the mapped run, still wins with the day in memory.
- Prediction, fit: `pswpout` does not move from the start of the run to its end, so the driver does not refuse; `MemAvailable` in the 10 s samples never falls below 3.5 GiB; every repetition completes and every page of the copy is resident at both ends of every timed pass.
- Prediction, winner and ranking: P-0011's order holds, and the default arm still wins: `paged-flat`, `hash-flat`, `paged-linked`, `hash-linked`, then `map-fresh`, then `map-aged`.
- Prediction, throughput: each fast arm runs at 1.05 to 1.20 times its P-0011 median, since on the Mac the mapped run with the top tracked ran at 0.87 to 0.93 of the in-memory one: `paged-flat` between 5.3 and 6.1 million messages/s, most likely near 5.8; `hash-flat` near 4.9; `paged-linked` near 2.95; `hash-linked` near 2.7. `map-fresh` between 1.1 and 1.9 million, most likely near 1.4, and `map-aged` below it, between 1.0 and 1.8. CV under 2% on every design.
- Prediction, T-BOOK-1: missed by every arm, the best by more than 10 million messages/s. The reason is the host, as in P-0011: the bar was set on a Ryzen 7 9700X.
- Prediction, T-DESIGN-1: the linked arms fell further behind the flat ones on `debian` than on the Mac, and a map book's tree and lists are more chains of dependent loads, so the map designs lose more to the host than the fast arms do. `paged-flat` over `map-fresh` between 3.3 and 5.0, most likely near 4.1, so the 4× bar is more likely met than missed; over `map-aged` between 3.5 and 5.5, near 4.5, and met. `hash-flat` over `map-fresh` near 3.5, missed. Both linked arms miss both, each ratio under 2.5.
- Prediction, T-BOOK-3: met on every design. Each fast arm's book memory within 2% of its P-0011 figure. `map-fresh` between 0.20 and 0.45 GiB and `map-aged` between 0.30 and 0.60 GiB; glibc's allocator is not macOS's, so those two need not match the Mac.
- Prediction, latency: every all-message p50 resolved above the 40.04 ns floor. On `paged-flat` p50 between 100 and 150 ns, p99 between 650 and 950 ns, p99.9 between 1,150 and 1,700 ns. T-LAT-3's mean met on every fast arm and missed on both map designs, whose mean service time is between 520 and 900 ns; its p99.9 missed on every design.
- Prediction, counters: every event measured at running ratio 1.0000, and `rdpmc` agreeing with `read()` within the harness's limit. Per message, through `rdpmc`, over `apply()`: `r0843:u`, a floor on DRAM demand fills, between 0.5 and 2.5 on `paged-flat`, fewer on each flat arm than on the linked arm with the same index, and more on each map design than on every fast arm. `cache-misses`, which counts L2 misses on Zen 1, between 1.2 and 4 times `r0843:u` on every design. Instructions per message, from `read()` over the pass, on each design between 0.7 and 1.5 times the Mac's in P-0008, and on each map design at least 2.5 times `paged-flat`'s. `branch-misses` per message higher on each map design than on every fast arm.
- Prediction, peak memory: peak RSS from `getrusage`, largest repetition, between 9.0 and 9.8 GiB on every design: the copy, the latency samples and the book, with the file never mapped.

## P-0013: the fast arms on debian, 12302019, the file mapped and the top of book tracked, with counters

- Experiment: P-0011 again, from the same commit as P-0012 and in its own `measure.sh` run after it, with `perf_event_paranoid` back at 2 so that the counter pass can open its events. The four fast arms, `--input-mode mapped`, `--track-top`, 3 repetitions, on `debian`, pinned to core 4. The mapped mode's code is as in P-0011; what changed is the driver's swap refusal and run-log row, and the names the counters are published under.
- What it is for: the counters P-0011 could not open, after the host's reboot reset `perf_event_paranoid` to 3, so that what the 3.6 to 4.05 times between the Mac and `debian` is made of can be read. P-0011 predicted no counter figure of its own, and P-0010's counter predictions were for the day in memory, so the counter part here is a new prediction, made before any counter of a book design on `debian` has been seen.
- Prediction, reproduction: each arm's median throughput within 3% of its P-0011 median (5,092,461, 4,268,175, 2,732,821 and 2,469,451 messages/s), the same order, CV under 1% on every arm, 124,767,189 moves of the top in both passes of every repetition, `pswpout` still, every page resident at both ends of every timed pass, peak RSS between 9.0 and 9.8 GiB.
- Prediction, counters: every event measured at running ratio 1.0000, and `rdpmc` agreeing with `read()` within the harness's limit. Per message, through `rdpmc`, over `apply()`: `r0843:u` between 0.5 and 2.5 on `paged-flat`, fewer on each flat arm than on the linked arm with the same index, and within 25% of the same arm's P-0012 figure, since both runs replay the same day through the same book and the top's check reads a level the message has just touched. `cache-misses`, L2 misses on Zen 1, between 1.2 and 4 times `r0843:u` on every arm. `branch-misses` between 0.3 and 3 per message on every arm. Cycles per message over `apply()` higher on each linked arm than on the flat arm with the same index.

## P-0014: the fast arms on debian, 12302019, the file in memory, read in pieces

- Experiment: P-0012 with the four fast arms only, `hash-linked`, `hash-flat`, `paged-linked` and `paged-flat`, from the same code, in its own `measure.sh` run after P-0013's. `--input-mode ram`, top of book not tracked, 3 repetitions, on `debian`, pinned to core 4, as `measure.sh taskset -c 4`, from a commit exported by `debian_sync.sh`.
- What it is for: T-BOOK-1 on `debian`. P-0012 was refused at repetition 1, by the driver, after `map-aged`'s timed process: the kernel swapped out one page, 4 KiB, of some process while that process held about 9.8 GB. The day's own page cache was empty at the time (`fincore`: 0 of 7.7 GB), so the fix worked, and the pressure came from the largest design beside other processes' page cache. T-DESIGN-1 needs a map design in the same run, so this run cannot measure it, and it stays unmeasured on `debian`.
- What was seen before this was written: P-0012's status lines, its refusal, its memory samples, that `pswpout` did not move through repetition 1's four fast arms and `map-fresh`, and the per-message counters of `hash-linked`'s first untimed process (`r0843:u` 4.64, `cache-misses` 9.63, `branch-misses` 2.24, instructions 555 and cycles 1,954 a message, over `apply()` through `rdpmc`). No throughput, latency or memory figure from P-0012 was read. So the counter predictions below leave `hash-linked`'s own figures out.
- Prediction, fit: `pswpout` does not move, every repetition completes, every page of the copy is resident at both ends of every timed pass.
- Prediction, throughput and order: as P-0012 for these arms. `paged-flat` wins, then `hash-flat`, `paged-linked`, `hash-linked`; each at 1.05 to 1.20 times its P-0011 median; `paged-flat` between 5.3 and 6.1 million messages/s; CV under 2%. T-BOOK-1 missed by every arm, the best by more than 10 million messages/s, for the host's reason.
- Prediction, T-BOOK-3: met on every arm, each within 2% of its P-0011 figure.
- Prediction, counters: every event measured at running ratio 1.0000, `rdpmc` agreeing with `read()`. `r0843:u` a message on `paged-linked` within 20% of `hash-linked`'s 4.64, since both walk the same linked levels; fewer on each flat arm than on the linked arm with the same index; on `paged-flat` between 1.5 and 4. `cache-misses` between 1.2 and 4 times `r0843:u` on every arm.
- Prediction, peak memory: peak RSS from `getrusage` between 9.0 and 9.8 GiB on every arm.

## P-0015: every book design on debian, 12302019, the file in memory, the map designs on throughput only

- Experiment: P-0012 again, with one change: the two map designs run throughput only. `bench_replay` through `scripts/bench_driver.py --core 4` on `12302019.NASDAQ_ITCH50`, `--input-mode ram`, top of book not tracked, 3 repetitions, each running `hash-linked`, `hash-flat`, `paged-linked`, `paged-flat`, `map-fresh` and `map-aged` in turn. Each fast arm runs every pass, as in P-0012: a timed process with the warm-up, the throughput pass and the latency pass, and an untimed process for the counters and memory. `map-fresh` and `map-aged` are named with `--throughput-only`: their timed process runs the warm-up and the throughput pass and no latency pass, so it makes no buffer for the latency samples, 268,744,780 messages at 5 bytes, 1,281.5 MiB, which on `debian` is a prefaulted arena of transparent huge pages; their untimed process is as before. On `debian`, pinned to core 4, as `measure.sh taskset -c 4`, from a commit exported by `debian_sync.sh`, with `perf_event_paranoid` at 2, started at least 30 minutes after the sync and rebuild, with every page of the day resident before it starts, and well away from unattended-upgrades' 04:02 local reboot window.
- What it is for: T-BOOK-1 and T-DESIGN-1 on `debian`, neither ever measured there, with T-BOOK-3 for every design and T-LAT-1 to T-LAT-5 and the counters for the four fast arms; and whether `paged-flat`, the default arm chosen from the mapped run, still wins with the day in memory. T-DESIGN-1 compares throughput alone, so the map designs need no latency pass. Their files publish no latency figure.
- Why the change: P-0012 was refused at repetition 1 because the kernel swapped out one page during `map-aged`'s timed process, which held about 9.3 GiB, and P-0014 never measured (see its block and the run log). The swap refusal stays as it is. Leaving out the map designs' latency pass removes the one large allocation their timed processes make besides the copy of the day, about 1.25 GiB each.
- What was seen before this was written: everything P-0014 lists, P-0013's published results, and the host's state at 03:47 local on the day of this run (`MemAvailable` 14.98 GB, `pswpout` 37,084, 1,788 pages more than at the end of P-0013's run, 1,228 of them during P-0014's one attempt and the rest while nothing of this project ran). Run A's raw records from P-0012 were not opened, so no in-memory throughput, latency or memory figure from `debian` has been read.
- What P-0014 becomes: it is not run on its own. This run measures the same four fast arms, in the same mode with the same passes, so it is P-0014's experiment with the two map designs beside it. Its results files cite P-0015. P-0014's block stays as committed, and its predictions for the fast arms are compared with this run's fast-arm figures in NOTES.md, as a check of what was predicted before, not as a second result.
- Prediction, fit: `pswpout` does not move from the start of the run to its end, so the driver does not refuse. This is the most uncertain line here: the largest timed process is now a fast arm's, at about 9.6 GiB, the size of `paged-flat`'s in P-0012's repetition 1, which did not swap, and the map designs' timed processes fall to about 8.1 GiB. `MemAvailable` in the 10 s samples never falls below 3.5 GiB. Every repetition completes and every page of the copy is resident at both ends of every timed pass.
- Prediction, winner and ranking: as P-0012. `paged-flat` wins, then `hash-flat`, `paged-linked`, `hash-linked`, then `map-fresh`, then `map-aged`.
- Prediction, throughput: as P-0012. Each fast arm at 1.05 to 1.20 times its P-0011 median: `paged-flat` between 5.3 and 6.1 million messages/s, most likely near 5.8; `hash-flat` near 4.9; `paged-linked` near 2.95; `hash-linked` near 2.7. `map-fresh` between 1.1 and 1.9 million, most likely near 1.4, and `map-aged` below it, between 1.0 and 1.8. No latency pass in a map design's process does not change its throughput beyond the CV, since the sample buffer is a mapping of its own that the throughput pass never touches. CV under 2% on every design.
- Prediction, T-BOOK-1: missed by every arm, the best by more than 10 million messages/s, for the host's reason given in P-0011: the bar was set on a Ryzen 7 9700X.
- Prediction, T-DESIGN-1: as P-0012. `paged-flat` over `map-fresh` between 3.3 and 5.0, most likely near 4.1, so the 4× bar is more likely met than missed; over `map-aged` between 3.5 and 5.5, near 4.5, and met. `hash-flat` over `map-fresh` near 3.5, missed. Both linked arms miss both, each ratio under 2.5.
- Prediction, T-BOOK-3: met on every design. Each fast arm's book memory within 2% of its P-0013 figure (662.8, 225.5, 648.3 and 211.0 MiB). `map-fresh` between 0.20 and 0.45 GiB and `map-aged` between 0.30 and 0.60 GiB.
- Prediction, latency, the fast arms only: every all-message p50 resolved above the 40.04 ns floor. On `paged-flat` p50 between 100 and 150 ns, p99 between 650 and 950 ns, p99.9 between 1,150 and 1,700 ns. T-LAT-3's mean met on every fast arm, and its p99.9 missed on every fast arm.
- Prediction, counters: as P-0014 for the fast arms: every event measured at running ratio 1.0000 and `rdpmc` agreeing with `read()`; `r0843:u` a message on each fast arm within 20% of its P-0013 figure (1.95, 2.84, 3.73 and 4.65); fewer on each flat arm than on the linked arm with the same index; `cache-misses` between 1.2 and 4 times `r0843:u` on every design. On each map design `r0843:u` and `branch-misses` a message higher than on every fast arm, and instructions a message at least 2.5 times `paged-flat`'s.
- Prediction, peak memory: peak RSS from `getrusage`, largest repetition, between 9.0 and 9.8 GiB on every fast arm, and between 7.8 and 8.6 GiB on each map design.

## P-0101: T-BT-1 on macOS, 12302019, the backtester with a quoting strategy

- Experiment: `bench_backtest --workload quoting` through `scripts/backtest_driver.py` on `12302019.NASDAQ_ITCH50`, mapped, 3 repetitions, each a fresh process with one warm-up pass and one timed pass, on `macOS (M4 Max)`, not pinned, through `measure.sh` and `/usr/bin/time -l`. The quoter of `bench/backtest_workloads.hpp` on AAPL, MSFT, SPY and TSLA, base latencies (25 µs each way, 5 µs compute), the queue model with this branch's late-add and printable-`C` rules.
- What it is for: T-BT-1, the replay rate with the queue model on and a strategy running, on a real day. The bar, 2.0 M events/s, was set on an Intel Core i9-13900H on a synthetic feed, so a macOS figure is context and the verdict is "host differs".
- What was seen before this was written: the 12302019 smoke of `itch_backtest` with the joiner strategy, 29.5 s of wall time for construction, the engine's whole day and a second plain replay for the identity check, and the Mac's `paged-flat` book at 19.3 M messages/s mapped with the top tracked (P-0009). No time of the engine alone has been measured.
- Prediction, rate: the median between 12 and 22 M ITCH messages/s, most likely near 16 M, so 6 to 11 times the bar's 2.0 M on a different event; at most 1.3 times slower than the plain `paged-flat` book, since 98% of messages are on symbols the strategy does not trade and cost the exchange-side book and a check of the locate. Engine events/s between 1.02 and 1.06 times the message rate. CV under 3%.
- Prediction, counts: every repetition gives the same counts and event hash; 268,744,780 messages; 5,425,845 deliveries; 0 queue anomalies; late adds ahead of a simulated order under 200; fills between 100,000 and 2,000,000; no refused message, ring overflow or fee error.
- Prediction, peak memory: peak RSS from `getrusage` between 8.8 and 9.8 GiB in every repetition, the mapped day counted.

## P-0102: T-BT-3 on macOS, 12302019, queuetruth's shadow orders

- Experiment: `bench_backtest --workload shadow` through `scripts/backtest_driver.py` on `12302019.NASDAQ_ITCH50`, 3 repetitions, each a fresh process that first copies the messages of AAPL, MSFT, SPY and TSLA and every System Event into memory, untimed, then runs one warm-up and one timed pass over that extract, on `macOS (M4 Max)`, not pinned, through `measure.sh` and `/usr/bin/time -l`. 100,000 shadow orders of 100 shares, placed evenly over the regular session at the best price of their side, round-robin over the symbols and sides, each cancelled after 60 s; every latency zero; no strategy.
- What it is for: T-BT-3, ≥ 4.61 M ITCH messages/s, set on an Apple M1 Pro under macOS, so a macOS figure is held to it.
- Prediction, denominator: the extract holds 5,425,845 messages, the smoke's deliveries, 4 fewer than queuetruth's 5,425,849.
- Prediction, rate: the median between 4 and 12 M extract messages/s, most likely near 7 M, so T-BT-3 is met; each message is applied to the exchange-side book and to the view book, with the queue model and a call to the placer, about two plain book applies and the rings. CV under 5%. The untimed extract takes between 1 and 10 s a process.
- Prediction, counts: every repetition gives the same counts and event hash; 0 queue anomalies; 100,000 placements less fewer than 100 skipped, each placed order cancelled once; at least 10,000 fills.
- Prediction, peak memory: peak RSS from `getrusage` between 8.5 and 10 GiB, since the extract reads the whole mapped day.

## P-0016: which counters are real hardware events on debian, with `:u`

- Experiment: `perf stat -x,` from `.toolchain/perf` on `debian`, `perf_event_paranoid` at 2, every event with `:u`, in two parts, each `perf stat` as `measure.sh taskset -c 4`'s direct child. Part A, whether the event opens and counts: one `perf stat` per event around `bench_replay --passes throughput` on `tests/fixtures/oracle/session.itch`, 3,330 messages, plus `cycles:k`, `cycles:pp` and `perf record --call-graph lbr`. Part B, what the events that open count on the real workload: the events Part A found counting, four to a `perf stat` so that none is multiplexed beside the NMI watchdog's counter, each around `bench_replay --design paged-flat --input-mode mapped --passes throughput --core 4` on `12302019.NASDAQ_ITCH50`: one warm-up replay, one timed replay and the warm-up's walk of the frames, in one process.
- What it is for: the Zen 1 counter table TASKS.md asks for, measured rather than read from `io-uring-flamegraph.md`. What each event counts comes from the kernel's `amd_zen1_perfmon_event_map`, AMD's PPR 55570-B1 and, for `cache-misses` and `r0843:u`, from P-0002's chase.
- Prediction, Part A, real and counting: `cycles`, `instructions`, `branches`, `branch-misses`, `cache-references`, `cache-misses`, `stalled-cycles-frontend`, `stalled-cycles-backend`, `L1-dcache-loads`, `L1-dcache-load-misses`, `L1-icache-loads`, `L1-icache-load-misses`, `dTLB-loads`, `dTLB-load-misses`, `iTLB-loads`, `iTLB-load-misses`, `branch-loads`, `branch-load-misses`, raw `r0843`, `r0143`, `r0243`, `r0964`, and the named `ls_dc_accesses`, `ex_ret_brn_ind_misp`, `l2_pf_miss_l2_l3`: each opens and gives a non-zero count, except that `r0843:u` may be zero on so small a fixture.
- Prediction, Part A, not available: `LLC-loads`, `LLC-load-misses` and `ref-cycles` print `<not supported>`. The L3 PMU's `l3_request_g1.caching_l3_cache_accesses:u` and `l3_comb_clstr_state.request_miss:u` fail to open, as a per-task event cannot be scheduled on that PMU and `:u` is rejected. `cycles:k` is refused at paranoid 2, `cycles:pp` is refused because it needs IBS, and `perf record --call-graph lbr` is refused, since Zen 1 has no LBR.
- Prediction, Part B: every event at a running ratio of 100%, no multiplexing. `cache-misses`, which counts L2 misses on Zen 1, between 1.2 and 4 times `r0843:u`, a floor on DRAM demand fills; `r0964:u` equal to `cache-misses` within 0.1%; `r0843:u` between 1.5 and 2.6 per replayed message, two replays a process; `branch-misses` between 1.6 and 2.6 per replayed message; `branches` between 60 and 160 per replayed message; `dTLB-load-misses` between 0.1 and 4 per replayed message.

## P-0017: the frame-pointer build's throughput against the normal build on debian

- Experiment: `bench_replay` through `scripts/bench_driver.py --core 4`, `paged-flat`, `--input-mode mapped`, top of book not tracked, `--throughput-only paged-flat`, 3 repetitions, twice in one sitting on `debian` from the same commit, each driver as `measure.sh taskset -c 4`'s direct child: first the normal build (`-O3 -DNDEBUG`), then a separate build with `-fno-omit-frame-pointer -mno-omit-leaf-frame-pointer -g` added to `CMAKE_CXX_FLAGS`, configured in its own build directory. `-g` adds debug information for `perf annotate`; clang does not change the code it generates for it.
- What it is for: the delta TASKS.md asks for, so that the flamegraph, which is taken on the frame-pointer build, says how far that build is from the one measured.
- Prediction, the normal build: between 4.9 and 5.6 million messages/s, most likely near 5.2, a little above P-0013's 5.04 M with the top tracked; CV under 1%; every page resident at both ends of each timed pass; `pswpout` still; peak RSS between 7.5 and 9.6 GiB.
- Prediction, the frame-pointer build: between 0.96 and 1.01 times the normal build's median, most likely 0.99: the hot loop is inlined into one function, so frame pointers cost one register and a prologue on the few calls left. CV under 1%.
- Prediction, the untimed counter process each repetition runs: `r0843:u` over `apply()` per message within 10% of P-0015's 1.97 for `paged-flat` on both builds, and instructions over `apply()` within 3% of each other.

## P-0018: flamegraphs of the hot paths on debian, on the frame-pointer build

- Experiment: `perf record -g --user-callchains -e cycles:u -F 999` on `debian`, as `measure.sh taskset -c 4`'s direct child, on the P-0017 frame-pointer build, three workloads on `12302019.NASDAQ_ITCH50`, mapped: `bench_replay --design paged-flat --passes throughput` (a warm-up and a timed replay, parse plus apply); `bench_replay --design map-fresh --passes throughput`, the map baseline for contrast, whose warm-up runs `hash-linked`; and `bench_backtest --workload quoting`, T-BT-1's workload. Then `perf script`, FlameGraph's `stackcollapse-perf.pl` and `flamegraph.pl`, and `perf annotate --stdio` on the hottest symbol of each.
- What it is for: TASKS.md's flamegraph line, beside the counter data.
- Prediction, every graph: user frames only, no kernel frame and no `[unknown]` frame above 1% of samples once frame pointers are on; between 50,000 and 400,000 samples each.
- Prediction, `paged-flat`: at least 85% of samples under the two replay passes. The book's `apply` and the frame walk are inlined into the pass, so the graph shows one wide frame and almost no depth; `perf annotate` is what shows where in it the cycles go, and its hottest instructions are loads that follow the order's index slot and its level.
- Prediction, `map-fresh`: at least 20% of its replay samples in `malloc`, `free` and `operator new` or `delete`, and at least 25% in `std::map`'s and `std::unordered_map`'s own functions, each a separate frame. The warm-up's `hash-linked` pass is a separate tower of 25% to 45% of the samples.
- Prediction, the backtester: at least 60% of samples in the exchange-side book's apply under the engine's run, and under 15% in the quoter and queue model together, since 98% of messages are on symbols the strategy does not trade.
