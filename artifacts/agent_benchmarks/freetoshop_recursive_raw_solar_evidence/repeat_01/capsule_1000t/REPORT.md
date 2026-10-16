# Disposable recursive raw → Solar benchmark

This is an isolated benchmark harness. It made no ORCHID production changes and used the frozen Phase 3.3 replay, raw batch plan, and semantic oracle.

## Measured facts

- Replay: 317 source items, 202,761 planned source tokens; replay SHA `6feb697961f22654d813159d07e2e519492b9ec431cdf8df08ae549508c26b16`.
- Recursive raw→Solar status: **FAILED**; 13/18 generations completed.
- Retirement: 150,621 tokens in 857.18s = **175.717 tok/s**; margin versus arrival 116.569 tok/s.
- Solar: 14 calls, 241,402 input tokens, 37,913 output tokens, estimated cost $0.011792.
- Failures/timeouts: TimeoutError / 1.
- Semantic retention: 90 PASS / 22 FAIL across 112 applicable frozen checks.
- Final checkpoint retention: 8 PASS / 2 FAIL.
- Capsule size: final 8663 estimated tokens; maximum 8663.
- Capsule growth: 10.76x from first to final valid generation.

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
