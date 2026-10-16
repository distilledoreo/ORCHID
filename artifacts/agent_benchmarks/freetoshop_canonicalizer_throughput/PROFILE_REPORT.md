# Canonicalizer throughput phase profile

## Measured facts

The captured provider-backed ORCHID database contains two completed failed compaction jobs and one unfinished job. No completed capture reached consolidator or promotion, so those stages are explicitly unavailable in the captured failure profile.

- Completed-job wall time: 1728.451 seconds
- Selector model wall time: 1121.615 seconds (64.89132230535685)%
- Canonicalizer model wall time: 605.405 seconds (35.025848976916265)%
- Consolidator model wall time: unavailable because canonicalizer failed first
- Software/unobserved remainder: 1.432 seconds
- Captured canonicalizer failures: 2

The corrected local canonicalizer replay used the frozen real FreetoShop event trace with exact per-batch source-ID enums. The best complete zero-timeout result in the measured window was:

- target: 12000 estimated source tokens
- source throughput: 71.13510137720677 source tokens/sec
- wall time: 227.10307129999273 seconds

## Interpretation

Canonicalizer time is a material stage-level risk, but the captured provider-backed failures also show selector work is substantial when the snapshot is split into many small source-span chunks. The stage profile cannot claim a consolidator percentage because no captured job reached it.

The exact-ID schema is a correctness hardening change, not a semantic policy change. It eliminated the observed truncated-span citation on the corrected local runs. The 75 source-tokens/sec target was not met by the complete corrected local window; no live preflight is authorized from this result.

## Limitations

- The frozen local replay measures canonicalizer behavior and does not invoke selector, Solar consolidator, or DB promotion for every frozen event.
- The old capture predates request-shape telemetry, so its timeout rows have no actual prompt-token or progress signal.
- Local model latency is highly variable across repeated identical inputs; p50/p95 should be calculated from a larger stable run before production tuning.
