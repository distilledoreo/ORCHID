# PanoRef selector TTFT / prefix-reuse microbenchmark

All calls were serial, benchmark-only, streamed for TTFT observation, and issued without retries. The exact request body, selector policy, model, 1.2K chunk plan, dynamic exact-ID schema, and evidence were frozen before execution.

## Result

| Arm | Calls | TTFT p50 | TTFT p95 | Wall p50 | Decode p50 | Decision consistency |
|---|---:|---:|---:|---:|---:|---|
| A exact repeat | 12 | 182.4 ms | 253.8 ms | 3189.3 ms | 3006.5 ms | 12/12 |
| B stable prefix | 24 | 1565.3 ms | 12292.4 ms | 4610.1 ms | 1979.9 ms | 24/24 |
| C invalidated prefix | 24 | 8214.6 ms | 13183.4 ms | 11395.4 ms | 2001.5 ms | 22/24 |

## Exact-repeat warm behavior

The first exact-repeat call had TTFT p50 327.9 ms. The remaining 11 repeated calls had TTFT p50 182.3 ms. The measured delta is 145.5 ms, first minus warmed. This was not a cold-server reset: chunk 76 also appeared in the preceding 32-call microprofile, so the first call here was already conditioned by prior benchmark activity.

## Stable versus invalidated prefix

Stable-prefix p50 TTFT was 1565.3 ms; invalidated-prefix p50 TTFT was 8214.6 ms; invalidated minus stable was 6649.3 ms. The stable arm was bimodal: its first 12 calls had TTFT p50 7179.8 ms, while its last 12 had p50 288.8 ms. The arms used identical frozen evidence and dynamic exact-ID schemas, with only the benchmark prefix nonce changed in Arm C. This ordering is conditioned by Arm A preceding Arm B and by chunk 76 overlapping the exact-repeat request; it is evidence of a warm-prefix transition, not a cold-start estimate.

## Explicit backend cache evidence

No explicit backend cache/prefix fields were present in 0 of 60 responses. All 60 responses carried the ordinary HTTP `Cache-Control: no-cache` header; that is transport metadata, not evidence of an LM Studio prompt-cache hit. The OpenAI-compatible SSE responses exposed usage and token arrival, but no cache-hit counter, prompt-cache counter, prompt-eval timing, decode timing, or queue-wait timing.

Timing alone is not treated as proof of a cache hit. Arm A/ B/ C timing differences are observations conditioned on the recorded serial call order and backend state; backend cache identity remains unreported by this endpoint.

## Decision validation

Exact repeats matched the first decision in 12/12. Every successful stable-prefix response was a subset of the historical selector decision set in 24/24; invalidated-prefix responses were a subset in 22/24. Paired stable/invalidated decisions matched in 22/24. The two invalidated-prefix mismatches were retained: chunk 90 changed from `[panoref_evt_001064]` to `[panoref_evt_001063, panoref_evt_001064, panoref_evt_001065, panoref_evt_001066, panoref_evt_001067, panoref_evt_001068]`; chunk 133 changed from `[panoref_evt_001167, panoref_evt_001168]` to `[panoref_evt_001167, panoref_evt_001168, panoref_evt_001169, panoref_evt_001170, panoref_evt_001171]`.

## TTFT sensitivity model

This is a model, not a measured speedup. It applies a 25/50/75% reduction to the observed 0.7744 TTFT share of the 1,805.1 s selector wall, holds the 229.3 s non-selector wall and 138,149 retired source tokens constant, and does not assume cache counters.

| Assumed TTFT reduction | Modeled selector wall | Modeled end-to-end wall | Modeled retired tok/s |
|---:|---:|---:|---:|
| 25% | 1455.6 s | 1684.9 s | 82.0 |
| 50% | 1106.1 s | 1335.4 s | 103.4 |
| 75% | 756.7 s | 986.0 s | 140.1 |

## Frozen identity

- Source commit: `1925fdbb198936e39554d5f7f22ec82f0777adbf`
- Plan SHA-256: `a09265b11b1e573ed904982a5b770c58fdaed24bdabf130510eb4f5f31061c43`
- Replay SHA-256: `aa0e7326c2514501a8c53a5a2c4669b1512383507989e71403d4e6f00f7eaf07`
- Prior microprofile config SHA-256: `19157d0b9e59859182b512d24f2b26e9bfbba17aea76f49e8cca0246e1d7d4d2`
- Model: `qwen3.5-4b@q6_k`
- Chunk target: `1200`
- Arm order: `A exact repeat, B stable prefix, C invalidated prefix`

No production architecture, selector prompt policy, chunk size, concurrency, schema semantics, or Solar path was changed.
