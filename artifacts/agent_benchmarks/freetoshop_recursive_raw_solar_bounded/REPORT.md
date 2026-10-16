# Bounded recursive raw → Solar benchmark

Disposable benchmark only; no ORCHID production code or policy was changed.
The frozen replay, 18 raw batches, Solar settings, telemetry, and semantic oracle were reused.
The only per-arm variable was the fixed capsule-budget instruction appended to the existing system prompt.

| Budget | Complete | Retired / planned | Wall s | tok/s | Provider in/out | Timeouts/failures | Semantic P/F | Current fact | Intent/blocker/continuation | Resurrection | Invention | Max capsule | Final capsule | Growth |
|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1000 | yes | 202,761 / 202,761 | 652.0 | 311.0 | 315,396 / 25,470 | 0/0 | 160/18 | 0 | 18 | 0 | 0 | 1985 | 1985 | 4.77x |
| 2000 | yes | 202,761 / 202,761 | 554.1 | 365.9 | 315,093 / 25,076 | 0/0 | 130/48 | 13 | 35 | 0 | 0 | 3250 | 1763 | 1.45x |
| 4000 | no | 139,101 / 202,761 | 954.8 | 145.7 | 228,112 / 39,025 | 1/1 | 90/12 | 0 | 12 | 0 | 0 | 6993 | 6993 | 4.27x |

Capsule sizes by generation are preserved in each arm's `ARM_COMPARISON.json`, `telemetry.jsonl`, and `capsules.jsonl`.
The semantic counts are aggregate applicable frozen checks through the completed prefix; a failed arm is not treated as full-trace evidence.
The final full-trace checkpoints were 18/19 PASS for 1K and 16/19 PASS for 2K; the 4K arm reached only generation 11 and its partial checkpoint was 9/10 PASS.
The prompt instruction was not a literal output cap: 1K and 2K observed maxima were 1,985 and 3,250 estimated tokens, while 4K reached 6,993 before its timeout.

Conclusion: **DIRECT_BOUNDED_PROMISING**
