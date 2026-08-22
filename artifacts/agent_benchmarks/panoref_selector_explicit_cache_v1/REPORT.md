# PanoRef selector explicit prefix-cache / slot-affinity validation

This report measures the same frozen 148-call, serial, streamed selector request trace in two fresh dedicated LM Studio llama.cpp instances. Request prompt, evidence, dynamic exact-ID schema, model settings, and decoding are unchanged; only backend instance routing and context-checkpoint capacity differ. No retries were issued, and failed rows remain in each telemetry file.

## Result

| Condition | Calls | TTFT p50 | TTFT p95 | Selector wall | Decision match | Errors |
|---|---:|---:|---:|---:|---:|---:|
| Baseline/current 32 | 148 | 8416.3 ms | 15229.0 ms | 1819.2 s | 148/148 paired | 0 |
| Explicit prefix 148 | 148 | 8418.0 ms | 15211.1 ms | 1820.3 s | 148/148 paired | 0 |

## Cold to warm and durability observations

- Baseline first low-TTFT call: None; warmup wall to that point: n/a ms; low-TTFT calls: 0; >=5 s regressions: 142.
- Explicit first low-TTFT call: None; warmup wall to that point: n/a ms; low-TTFT calls: 0; >=5 s regressions: 142.
- Changing evidence durability is measured by the full 148-call trace. Explicit cache-hit, slot, eviction, and session counters were not emitted by the OpenAI-compatible SSE response; the recorded backend instance identifier is the loaded model instance, not a cache-hit claim.
- The narrow short-idle and post-cleanup check completed 3/3 calls with no errors: after a 2 s idle, TTFT p50 was 6213.0 ms and all 3 calls were >=5 s; after closing the HTTP client and creating a new one, the subsequent selector call also remained in the cold/regression class. Solar-between-selector-calls was not run because it would change the existing apparatus ordering (the selector stage precedes Solar).

## Decision equivalence

148/148 successful paired chunks matched. Planned denominator is 148; unresolved calls therefore cannot count as equivalent. Divergences: []

## Projected end-to-end impact

The following holds the historical 229.3 s downstream Solar/validation wall constant; it is not a full pipeline result.

- Explicit selector wall: 1820.3 s.
- Projected total: 2049.6 s.
- Projected retirement throughput: 67.4 source tok/s.
- Projected speedup vs historical selector-control total: 0.99x.
- Projected margin over 59.148 tok/s FreetoShop arrival rate: 8.3 tok/s.

## Frozen identity

- Source commit: `7ddac7b628ab4303053af16b72dd69b7a9de2437`
- Plan SHA-256: `fd1a2f332ccee1f894f1408cbdbcaa9369bde6d654c81ca1cd7c2c0fcb9d21e8`
- Benchmark config file SHA-256: `8a210240584203c8223b262c77b1d951b904a51b6809bb8460e1bb960070d42f`
- Replay SHA-256: `aa0e7326c2514501a8c53a5a2c4669b1512383507989e71403d4e6f00f7eaf07`
- Chunk plan SHA-256: `74ff93c68bde03cdb0676e6ce478620d6b273f610826dad95959c3615fb65873`
- Prior selector profile config SHA-256: `19157d0b9e59859182b512d24f2b26e9bfbba17aea76f49e8cca0246e1d7d4d2`
- Backend capability note: `C:\Users\disti\orchid_memory_gateway_v1\artifacts\agent_benchmarks\panoref_selector_explicit_cache_v1\BACKEND_CAPABILITIES.md`

No production ORCHID architecture, selector prompt, selector policy, chunk size, concurrency, dynamic exact-ID schema, decode setting, Solar path, or oracle was changed.
