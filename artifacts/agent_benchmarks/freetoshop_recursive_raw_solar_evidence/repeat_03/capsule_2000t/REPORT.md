# Disposable recursive raw → Solar benchmark

This is an isolated benchmark harness. It made no ORCHID production changes and used the frozen Phase 3.3 replay, raw batch plan, and semantic oracle.

## Measured facts

- Replay: 317 source items, 202,761 planned source tokens; replay SHA `6feb697961f22654d813159d07e2e519492b9ec431cdf8df08ae549508c26b16`.
- Recursive raw→Solar status: **FAILED**; 3/18 generations completed.
- Retirement: 34,799 tokens in 511.27s = **68.063 tok/s**; margin versus arrival 8.915 tok/s.
- Solar: 4 calls, 49,049 input tokens, 7,749 output tokens, estimated cost $0.002401.
- Failures/timeouts: TimeoutError / 1.
- Semantic retention: 8 PASS / 4 FAIL across 12 applicable frozen checks.
- Final checkpoint retention: 2 PASS / 2 FAIL.
- Capsule size: final 4593 estimated tokens; maximum 4593.
- Capsule growth: 3.97x from first to final valid generation.

## Comparison with existing Selector → Solar

- Selector→Solar: 198,390 selected source tokens in 2884.91s = **68.768 tok/s**.
- Selector→Solar provider use: 405,044 input / 26,374 output tokens; estimated cost $0.015316.
- Selector→Solar semantic retention under the same frozen rubric: 131 PASS / 54 FAIL across 185 checks.
- Selector→Solar final capsule: 291 estimated tokens; maximum 2885.
- Selector→Solar first-to-final capsule ratio: 0.86x.

## Interpretation

Plain-text output removes the structured-output failure mode and makes the recursive memory mechanism directly testable, but it also removes software-enforced source provenance. Semantic checks are the frozen deterministic coverage rubric, not a complete human semantic judge. The Selector→Solar numbers are historical Phase 3.3 results and were not rerun or tuned for this disposable experiment.

## Decision

This benchmark does not change ORCHID and does not authorize a production architecture. Direct recursive raw→Solar is useful only if it completes the trace with adequate semantic retention and acceptable capsule growth; the recorded comparison is the evidence for that decision.
