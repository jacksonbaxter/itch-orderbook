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
