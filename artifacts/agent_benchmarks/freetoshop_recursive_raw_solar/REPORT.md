# Disposable recursive raw → Solar benchmark

This is an isolated benchmark harness. It made no ORCHID production changes and used the frozen Phase 3.3 replay, raw batch plan, and semantic oracle.

## Measured facts

- Replay: 317 source items, 202,761 planned source tokens; replay SHA `6feb697961f22654d813159d07e2e519492b9ec431cdf8df08ae549508c26b16`.
- Recursive raw→Solar status: **FAILED**; 5/18 generations completed.
- Retirement: 58,024 tokens in 629.23s = **92.214 tok/s**; margin versus arrival 33.066 tok/s.
- Solar: 6 calls, 92,114 input tokens, 17,041 output tokens, estimated cost $0.004808.
- Failures/timeouts: TimeoutError after 180s / 1.
- Semantic retention: 27 PASS / 5 FAIL across 32 applicable frozen checks.
- Final checkpoint retention: 9 PASS / 1 FAIL.
- Capsule size: final 5580 estimated tokens; maximum 5580.
- Capsule growth: 4.49x from first to final valid generation.

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
