# Notes

Working notes, kept as we go. Includes what didn't help.

## Research, 2026-10-30

Eleven subagents ran, all on `claude-opus-5-5` at high effort, confirmed by `verify_models.py` from the API request records. The account usage limit stopped the second round at about 13:00. Those five subagents returned no final message, but all five evidence files had already reached disk with their end sections, so none of that work needed repeating.

Evidence is in `build-research/evidence/` (gitignored). Third-party clones and page snapshots are in `.orchestrator/research-cache/itch-orderbook/`.

### Decisions

- **Reference day is `12302019`, not `S061226`.** The data subagent recommended the newest file, a 41.7 GB day. Headline numbers must come from `debian`, which has about 11 GB free, and the replay input must be resident before timing. `12302019` is 8.25 GB, and the strongest public results were measured on it. Its volume also ties to outside data. `S061226` stays as a Mac-only stretch, because its mix is genuinely different: `U` is 27.7% of messages there against 8.7% on `10302019`.
- **Tune on `10302019`, report on `12302019`.** Parameters are fitted from the market tape, never from a run's own fills. `10302019` is an FOMC statement day, so it is not a typical tuning day. That is stated, not hidden.
- **Cboe figures are not in the repo yet.** Its terms bar use "to verify or correct other data or information" without consent, subject to fair use. Research compared published ITCH sums with Cboe in gitignored evidence only. The repo-side check waits for the user's decision.
- **Avellaneda-Stoikov stays the named strategy.** The evidence recommends GLFT stationary quotes on one-tick stocks, but the user asked for Avellaneda-Stoikov. GLFT runs as a labelled variant.
- **Fees sit inside spread capture,** so the user's three components remain the complete sum. They are always shown as a sub-line, since fees can decide the sign on a one-tick stock.
- **Inventory is computed, never a residual.** A residual would make the required test pass by construction.

### Book and throughput

- "Full book" often means no queue order. parseritch, charles-cooper and CppTrader's fast variants aggregate price levels. They are not L3, and they beat the L3 numbers by a wide margin. Every rate needs FIFO yes or no beside it.
- "Parse" covers at least four jobs. A length-prefix walk and a full decode differ by 3.6× on one machine.
- Prefer the most honest published number over the highest. Excluded after reading the method: bsinek (8 symbols), Apex-Wire (all symbols in one book), LobIQ (per-symbol store), Mohith's 124 M and 2.9 GB/s (two different runs).
- Absolute numbers don't travel. Same itch-book code: 16.4 M msg/s on a Ryzen 9700X, 4.6 M on a 2.0 GHz EPYC VM.
- Mapping the file and tracking BBO costs itch-book 24%. Report the realistic pipeline as well as the headline.

### Data

- Nasdaq publishes no usable checksum. All listed `.md5sum` and `.done` files return 404. Integrity rests on the gzip trailer, third-party census byte counts, and our own pinned hash.
- Real files lack BinaryFILE's zero-length terminator. Completeness means ending on System Event `C`.
- The archive isn't stable: 8 listed `.md5sum` entries have no data file beside them. Keep the `.gz`.
- One published count is provably mislabelled (`PIYUSH-KUMAR1809`, DS-16). A count is a claim until our harness reproduces it.
- Sparse price fields are real: stub quotes reach $199,999.99. A flat array per symbol can't work.
- `X` messages can arrive after End of System Hours, which the spec text doesn't list. The spec has at least nine internal errors (DS-76).

### Terms

No document found names the sample files or `emi.nasdaq.com`. No text grants or denies rights. The nearest text points away from a public page built on real extracts: display on the public internet doesn't qualify as a publication "unless specifically approved by Nasdaq". The demo therefore replays a locally generated, gitignored extract, and public deployment needs written approval from Nasdaq. This is a reading of documents, not legal advice.

### Scalar against SIMD

Nothing published settles this. The only native NEON measurement ranges from 0.58× to 1.51× over 5 runs. No M4 instruction table is public, and Apple's guide needs a developer login. Firestorm data is labelled as M1 wherever used. Framing is a serial dependency, so it can't be vectorised directly. Prefetch and dispatch likely matter more than decode.

WASM may reverse the result, because its core instruction set has no byte-reverse.

### Trade prints

- `P` and `Q` cannot satisfy "hits a resting order": that holds for a whole class of message, and it stays surfaced as a count.
- The independent internal pair is `Add` shares against the sum of deductions for orders that die with no `D`. Nasdaq's silence is the witness.
- Published per-symbol ITCH sums tie to Cboe's Nasdaq-venue totals exactly on 2019-12-30 and 2019-08-30, across all three tapes, after removing test symbols. On 2026-06-12 the residual is 28 times larger and unexplained, which is one more reason not to use that day as the reference.
- Queue-front rate cannot be a hard check. Published 99.82% and 99.914%, and nobody has attributed the remainder.

### Backtester and market maker

- ITCH gives the real queue ahead of a simulated order, so `ahead` is known rather than estimated. That is what separates this from L2 backtesters.
- A replay still can't contain races lost by others, so a low-latency row flatters a strategy.
- No published equity backtest replays real L3 data with strategy PnL both with and without queue and latency modelling. No published AS replay on TotalView was found either.
- Attribution identity was checked by `identity_check.py` in the research cache: 4,000 random fill sequences at six horizons, exact.

### Components

- SPSC: only ratios can carry across hardware, so the target is a ratio. `debian` has one CCX, so rigtorp's 20× cross-CCX gain can't be reproduced there.
- Allocators: the strongest published result is a locality result, not an allocator-speed result. N4468 also shows a pool losing where locality isn't the issue.
- A monotonic arena can't hold a 2026-class day on `debian`. Use a recycling pool.

### io_uring, counters and flamegraphs

- No published head-to-head with `recvmmsg` exists. It's not known that io_uring wins, and the two nearest published figures disagree with each other.
- Loopback runs UDP receive softirq on the sender's CPU unless root configures RPS. So it's a syscall-and-copy benchmark, not a network one.
- At `perf_event_paranoid=2` a flamegraph has no kernel frames, so it cannot show the io_uring against socket difference at all.
- On Zen 1, `LLC-load-misses` is unsupported, `cache-misses` appears to count L2 misses, and L3 counters are denied.

### This machine (macOS)

From probes run in the measurement track (`probes-measurement/` in the research cache):

- `mach_absolute_time` costs about 4 ns, at a 41.67 ns quantum. `CNTFRQ_EL0` reads 1 GHz, but the step is still about 41.67 ns, so the harness must measure the step rather than read the register.
- `thread_policy_set` returns 46, and threads migrated about 8 times per second under QoS.
- Cycles and instructions are available without root, through a private syscall per thread or `proc_pid_rusage` per process. Branch and cache misses are not.
- About 600 scheduling gaps per second at or above 1 µs put p99.9 near the OS jitter floor here.
- `CLOCK_MONOTONIC` steps 1 µs here. Use `CLOCK_UPTIME_RAW`.
- Xcode is absent, so `xctrace` and CPU Counters are untested.

### Process notes

- Two subagents briefly wrote scratch files to `/tmp` against their brief, and moved them under the project area. No project data was involved.
- Third-party clones reached the working tree in the first round, some carrying their own `CLAUDE.md` and `AGENTS.md`. All were moved to the research cache when the rule arrived. A `sed` loop for the path rewrite failed the first time because zsh doesn't split unquoted variables on newlines; re-run with `while read`.
- The SIMD subagent reported `reasoning_effort 25` from its runtime and couldn't map it to a level. The API request records show `effort=high` for that agent, so it was not a violation.
