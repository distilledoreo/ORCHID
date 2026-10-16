# ORCHID canonicalizer throughput phase

## Recommendation

`NOT_READY_FOR_FULL_AB_RERUN`

## Measured facts

1. Selector share of completed captured compaction wall time: 64.89%.
2. Canonicalizer share: 35.03%.
3. Consolidator share: unavailable; both completed captured jobs failed before consolidation.
4. Prior timeout cause: a non-streaming local canonicalizer request produced no response before the 180-second request deadline; the old client did not provide a reliable total-operation bound. The new explicit async deadline reproduces and records this deterministically with a delayed stub, and the next request succeeds.
5. Best complete corrected window: 12K target, 71.14 source tokens/sec, 2 batches, zero timeouts and valid exact source references.
6. Smaller batches: 4K completed at 51.44 tok/s; 8K at 47.89 tok/s. They did not improve total throughput in this window, though they split individual requests.
7. Larger batches: 12K improved amortization to 71.14 tok/s; 16K fell to 62.05 tok/s. The full replay still timed out at 12K.
8. Canonicalizer input amplification: 1.242x in the corrected 12K window.
9. Fixed overhead: approximately 420 system-prompt characters, 48 wrapper characters, and 340–746 response-schema characters per call; the exact-ID enum is correctness-constraining.
10. Safe prompt overhead removal: none accepted in this phase. Output caps caused invalid truncated JSON and are rejected.
11. Concurrency 2: exploratory single-batch test increased aggregate throughput to about 132.02 tok/s versus 76.42 tok/s at concurrency 1, with no failures in that small test. It was not promoted to production because the loaded endpoint was configured parallel=1 and the full replay remained timeout-unstable.
12. Concurrency timeout risk: not established on a sufficiently large corpus; therefore no production concurrency change was made.
13. Timeout policy: explicit total request deadline, bounded failure telemetry, cleanup, and retry/recovery tests; do not blindly increase the deadline.
14. Final frozen-replay throughput: no valid full-replay throughput exists. The run failed after two valid batches. Diagnostic partial successful-batch rate was 74.62 tok/s, excluding the failed and unattempted work.
15. Does it exceed 75 tok/s? No completed full replay did; the best small complete window was below target.
16. Margin over 59.148 tok/s arrival: best small complete window was 11.99 tok/s (1.20x), but this margin is not operationally valid because the full replay timed out.
17. Semantic correctness: corrected local canonicalizer batches remained structured-output and exact-source-reference valid; full semantic consolidation/current-fact correctness was not measurable after timeout.
18. Provenance: exact source-reference validation passed for completed corrected batches; full replay provenance/promotion was not reached.
19. Live preflight promotions: 0 in this phase; it was not run because the frozen replay gate failed.
20. Live retirement versus live arrival: not measured in this phase; no live preflight was authorized.
21. Ready for another full A/B? No.

## Interpretation

The stage profile confirms canonicalizer behavior is the immediate reliability risk, but the historical provider-backed profile also contains substantial selector work. The measured 12K window is close to the 75 tok/s target but is not a sustainable result: the corrected full replay timed out on its third request and therefore cannot support a claim that ORCHID keeps up with 59.148 tok/s arrival.

The dynamic exact source-ID schema is a narrow correctness hardening change. It prevented the previously observed truncated/parent-ID citation failures on corrected batches. The explicit deadline makes stalled non-streaming calls fail open to scheduler recovery rather than hang indefinitely. Neither change weakens provenance or semantic policy.

## Limitations

- Local Qwen latency varied materially across repeated equivalent inputs.
- The full replay harness invokes the canonicalizer against the frozen source trace, not the entire selector→canonicalizer→Solar consolidator→promotion pipeline after the first canonicalizer failure.
- No new production batch size or concurrency setting was selected from the incomplete replay.
- The known unrelated selector-schema expectation failure remains separate and was not weakened.

## Validation

- Focused and relevant broader tests passed: `tests/test_model_telemetry.py`, `tests/test_pipeline.py`, `tests/test_operability_hardening.py`, `tests/test_gateway_runtime.py`, and `tests/test_invariants.py`.
- `python -m compileall -q memory_gateway tools tests`: passed.
- `git diff --check`: passed; Git emitted only line-ending normalization warnings.
- Full `python -m pytest -q`: one known unrelated failure in `tests/test_openai_adapter.py::test_selector_and_canonicalizer_send_json_schema_response_formats`; it expects the old static selector schema and was not used to weaken dynamic selector hardening.

## Next recommendation

Do not rerun the six-hour A/B. First isolate the third-batch canonicalizer stall with endpoint-level progress/queue diagnostics or a reproducible local request fixture, then repeat the frozen full replay until it completes with zero unresolved timeouts and a measured rate above 75 source tokens/sec.
