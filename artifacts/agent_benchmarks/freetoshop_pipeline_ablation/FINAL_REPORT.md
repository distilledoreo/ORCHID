# ORCHID Phase 3.2 — Semantic pipeline ablation

## Recommendation

`NOT_READY_FOR_FULL_AB_RERUN`

## Measured facts

The frozen replay contains 199 events, 317 expanded source items, and 202,761 planned source tokens. The prior full current-pipeline replay completed two canonicalizer batches, then timed out on batch 2 at 180s; it did not produce a full-trace retirement rate.

- ARM_A_CURRENT: scope `12_EVENT_PILOT`, status `SUCCEEDED`, rate `65.91` source tok/s.
- ARM_B_NO_CANONICALIZER: scope `12_EVENT_PILOT`, status `SUCCEEDED`, rate `133.67` source tok/s.
- ARM_C_DIRECT: scope `FULL_REPLAY`, status `FAILED`, rate `not available` source tok/s.
- ARM_D_ROLE_AFFINITY: scope `NOT_RUN`, status `NOT_RUN`, rate `not available` source tok/s.
- ARM_E_PIPELINED: scope `NOT_RUN`, status `NOT_RUN`, rate `not available` source tok/s.

The completed 12-event pilots are not full-replay claims:

- ARM A current: 13,740 source tokens in 208.48s, 65.91 source tok/s, selector 42.29s, canonicalizer 158.00s, consolidator 8.16s, zero timeouts, exact-reference checks and temporary promotion passed.
- ARM B selector → direct Solar: 13,740 source tokens in 102.79s, 133.67 source tok/s, selector 41.79s, direct Solar 61.00s, zero timeouts, exact-reference checks and temporary promotion passed.
- ARM C bounded raw → direct Solar: the preserved 12-event pilot measured 115.87 source tok/s with zero timeouts and promotion; the full 199-event attempt failed after one 11,463-token direct batch when the second batch hit 180s.

The exact stall matrix recorded 24/24 successes and 0 timeouts: idle, after-selector, after-canonicalizer, three alternation cycles, and a concurrent pair. LM Studio showed real generation. This does not erase the prior full-replay timeout; it shows that the input is not a deterministic failure trigger.

Role-switch testing used a 4K fixture and two iterations per configuration. Shared-client role switching had 1 timeout in 4 requests; two persistent role clients had 0 timeouts in 4. Successful canonicalizer calls remained about 155–158s. TTFT and KV/prefix-cache hits were unavailable.

Concurrency testing used a 2K fixture. At the original `parallel=1`, one request was 63.00s / 27.43 tok/s and two overlapping requests were 105.70s aggregate / 32.70 tok/s. With a separately loaded `parallel=2` model identifier, one request was 145.98s / 11.84 tok/s and two were 171.11s aggregate / 20.20 tok/s. Both configurations had zero timeouts; parallelism did not improve throughput here.

## Interpretation

The current 12-event waterfall makes the canonicalizer the dominant measured stage at 75.7% of current-pipeline pilot wall time. The full-trace failure and role/concurrency probes point to a long-tail local inference/runtime issue, but do not identify a deadlock, deterministic malformed prompt, or cache-affinity mechanism. Persistent role clients reduced observed failure count in this small probe but did not improve successful canonicalizer latency enough to justify a production change.

The simplest promising ablation is bounded raw → Solar direct consolidation, followed by selector → direct Solar. Both pilots are faster than the current pipeline, but neither has a full-trace semantic evaluation. ARM C is therefore an experimental candidate only; no production semantic stage was removed.

## Final questions

1. Selector share: 20.3% of the 12-event current-pipeline pilot; full-trace share unavailable.
2. Canonicalizer share: 75.7% of that pilot; full-trace share unavailable because the replay stopped in canonicalization.
3. Consolidator share: 3.9% of that pilot; full-trace share unavailable.
4. Prior timeouts: an unresolved long-tail local inference/runtime event. Exact isolated/context requests later succeeded; queue depth, KV hits, and first-token progress were not exposed.
5. Best corrected canonicalizer batch size: 12K in the prior sweep at 71.14 source tok/s with zero timeouts on the 12-event fixture; it did not meet the 75 tok/s target on the full trace.
6. Smaller batches: 4K and 8K were slower in the prior corrected sweep (51.44 and 47.89 tok/s).
7. Larger batches: 12K improved amortization over 4K/8K; 16K was slower at 62.05 tok/s and did not establish a safe advantage.
8. Canonicalizer input amplification: 1.242x in the prior corrected 12K local replay (20,060 model-input tokens / 16,155 source tokens). Total semantic amplification was not available from one unified full trace.
9. Fixed prompt/schema overhead: approximately 420 system-prompt characters + 48 wrapper characters + 340–746 schema characters per canonicalizer call in the prior profile; the exact-ID schema component is correctness-constraining.
10. Safe prompt removal: none demonstrated; no schema/provenance weakening was attempted.
11. Concurrency 2: no improvement; the explicit `parallel=2` run was slower than the original `parallel=1` comparison.
12. Concurrency timeout risk: no timeout in the small paired runs, but long latency increased substantially; shared role switching did produce one timeout.
13. Timeout policy: retain a bounded 180s deadline until progress/queue telemetry exists; do not blindly increase it. Treat a timeout as a failed compaction that can be retried/recovered by existing scheduling.
14. Final frozen-replay rate: unavailable; ARM C failed on batch 2 and current ARM A had already failed on canonicalizer batch 2.
15. Above 75 tok/s: no completed full replay, so no.
16. Margin over 59.148 arrival: full-trace margin is unknown; pilot margins were +6.76 tok/s for ARM A, +74.52 for ARM B, and +56.72 for ARM C.
17. Semantic correctness: not independently established; no deterministic frozen semantic judge exists for this trace.
18. Provenance: exact source-reference subset and temporary promotion checks passed on completed pilots; the failed full ARM C did not reach promotion.
19. Live preflight with three promotions: not run because no full candidate passed the gate.
20. Live retirement versus arrival: not measured in this phase; no live preflight was authorized after the failed full replay.
21. Ready for another full A/B: no.

## Selected pipeline

No production pipeline is selected. The next experiment should be a full-trace ARM C/ARM B comparison with a semantic judge, not a production removal of canonicalization. The data supports testing bounded raw → Solar as the simplest candidate, but it does not prove semantic sufficiency or sustainable full-trace throughput.

## Limitations

- This phase did not add or change production memory semantics, retrieval, schemas, provenance validation, or scheduler behavior.
- Full ARM C is a real failure, not an omitted measurement; no full ARM B was launched after that failure.
- Pilot structural promotion is not a substitute for current-state, supersession, or invention scoring.
- The known unrelated selector-schema test expectation remains separate; the dynamic exact-ID selector schema was not weakened.
