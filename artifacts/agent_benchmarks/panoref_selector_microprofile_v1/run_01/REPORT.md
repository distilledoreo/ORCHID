# PanoRef selector microprofile

Measured 32 of 32 serial calls from the frozen 1.2K selector chunk plan. Failed calls are retained in `telemetry.jsonl`; no retries were issued.

| Field | p50 | p95 | Count |
|---|---:|---:|---:|
| Total wall (ms) | 10689.1 | 16755.8 | 32 |
| TTFT (ms) | 8015.1 | 13351.4 | 32 |
| Decode after first content (ms) | 1984.7 | 4274.3 | 32 |
| Prompt eval (backend ms) | n/a | n/a | 0 |
| Decode (backend ms) | n/a | n/a | 0 |
| Queue wait (backend ms) | n/a | n/a | 0 |
| Input tok/s (TTFT proxy) | 188.5 | 192.8 | 32 |
| Output tok/s (client observed) | 13.1 | 13.5 | 32 |

## Frozen identity

- Source commit: `297b80e692777ad7c1dbe7b4198cc44bf3dc9f74`
- Replay SHA-256: `aa0e7326c2514501a8c53a5a2c4669b1512383507989e71403d4e6f00f7eaf07`
- Profile config SHA-256: `4c4519e2833867c47715b0899696720f5246863468ad2554ed4488cd432c225d`
- Chunk target: `1200` estimated source tokens
- Sample count: `32`
- Sample selection: deterministic evenly spaced indices across the 148 frozen chunks
- Model: `qwen3.5-4b@q6_k`

## Availability and interpretation

LM Studio returned no prompt-eval, decode, or queue timing fields. TTFT and post-first-token duration are client-observed proxies; prompt eval and queue wait remain unavailable.

The OpenAI-compatible SSE response exposed `usage` and token arrival only. Its timing fields/headers were absent. There is therefore no measured backend prompt-eval time or queue-wait time in this run, and `input tok/s` is explicitly a TTFT-based lower-bound proxy rather than a backend prefill rate: any unexposed queue or transport time makes the denominator larger.

The measured comparison is between the time to first content and the post-first-content interval. TTFT was larger than the post-first-content interval in 31 of 32 successful calls, and its median share of total wall was 0.774 (fraction). This is consistent with a pre-first-token/prefill-dominated request, but prompt evaluation cannot be isolated from any unexposed scheduling or transport delay. A materially larger post-first-content interval would instead indicate decode/output handling dominates. This is a transport microprofile, not a semantic selector benchmark.

No prompt, schema, model, chunk size, selector policy, concurrency, or Solar setting was changed. The only profile-specific setting is streaming with usage included, required to observe TTFT.
