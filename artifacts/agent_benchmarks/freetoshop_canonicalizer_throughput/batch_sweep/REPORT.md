# Batch-size sweep

The corrected full-window runs used the same first 12 frozen replay events, exact per-batch source-ID enums, no output cap, and a 180-second per-request deadline. A result is counted as correct only when every batch completed with valid structured output and zero timeout failures.

Measured complete runs:

| Target | Source tok/s | Wall seconds | Batches | Timeouts | Structured failures |
|---:|---:|---:|---:|---:|---:|
| 4K | 51.44 | 314.05 | 5 | 0 | 0 |
| 8K | 47.89 | 337.36 | 3 | 0 | 0 |
| 12K | 71.14 | 227.10 | 2 | 0 | 0 |
| 16K | 62.05 | 260.38 | 2 | 0 | 0 |

The 12K target is the measured winner in this small window, but it remains below the 75 tok/s engineering gate. Output caps are not accepted because the 512 and 1,024 experiments ended in invalid JSON truncation.
