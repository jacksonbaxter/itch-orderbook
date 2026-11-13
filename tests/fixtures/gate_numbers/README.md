# A fixture README

An ITCH 5.0 parser in C++23, with 3 hosts and 2 passes. See phase 2 of the plan.

| Host | Messages/s | p99 | Source |
|---|---|---|---|
| debian | 20,677,835 | 291.7 ns | results/bench_one.md |
| macOS | 12.5 M | CV 3.06% | results/bench_two.md |

The day, 12302019, holds 268,744,780 messages, dated 2019-12-30 ([results/bench_one.md](results/bench_one.md)).

Over 10 sessions on a Zen 1 with 4 cores, S4 carries 2,830,848 messages/s; S1 and S3 tie at 2,735,896 messages/s in 9 trials ([results/modes.md](results/modes.md)).

In the fault check, `pool-index`, `pool-pointer` and `pool-fifo` take 136, 200 and 146 page faults, and the day's total loss is -$27.50 ([results/modes.md](results/modes.md)).

Run `bench_replay --orders 4194304` to repeat it, as on [this page](https://example.org/9999.5), or read [V8 on 4 GB of memory](https://example.org/v8).

```
sample output 123456 that is not a figure
```

The chip costs $400 at 10 GHz. <!-- figures: not measured -->
