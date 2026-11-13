# Modes

- sessions: 10
- fault_check: FAIL: page faults in the timed passes of pool-index 136, pool-pointer 200, pool-fifo 146
- loss: the day's total -$27.50

| Mode | Largest lossless msgs/s | Trials | Rounds |
|---|---|---|---|
| S1 | 2,735,896 | 9 | 4 |
| S3 | 2,735,896 | 9 | 5 |
| S4 | 2,830,848 | 7 | 4 |

Page faults in each timed pass, one figure a pass.

| Arm | Page faults |
|---|---|
| pool-index | 0 / 136 / 0 |
| pool-pointer | 200 / 0 / 0 |
| pool-fifo | 146 / 0 / 0 |
