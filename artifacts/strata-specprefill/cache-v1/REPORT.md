# Multi-turn prefix caching experiment

Same Swift IQ3_S Flash-Next target, Qwen3.5-0.8B student, and P100 16GB. Two frozen scripted recall conversations, four probes each. Greedy decoding, int8 target KV, MTP 4, 16k context, six prefix checkpoints, no parked conversations. These are synthetic fixed histories, not repository edits or a live agent loop.

## Step 1: ordinary target control

| Prompt tokens | Added tokens | Cache off TTFT | Cache on TTFT | Reduction |
|---:|---:|---:|---:|---:|
| 1517 | 1517 | 7.682s | 7.389s | 3.8% |
| 2051 | 534 | 8.931s | 5.131s | 42.6% |
| 3069 | 1018 | 11.551s | 6.697s | 42.0% |
| 5099 | 2030 | 17.019s | 9.250s | 45.6% |

Cache off: 8/8 correct; cache on: 8/8. Output IDs match 8/8. Warmup used a 256-token prefix; initial probes are therefore excluded from the primary growing-turn comparison.

## Steps 2–3: student caching and equivalence

| Turn | Uncached chunked student | Cached student | Reused prefix |
|---:|---:|---:|---:|
| 0 | 0.555s | 0.535s | 0 |
| 1 | 0.749s | 0.290s | 1517 |
| 2 | 0.961s | 0.427s | 2051 |
| 3 | 1.582s | 0.762s | 3069 |

Draft IDs match 8/8. Exact selected positions match 7/8 versus uncached chunking and 6/8 versus original full prefill. Maximum absolute score difference is 0.00012207; minimum selection Jaccard is 0.9331. This is approximate numerical equivalence, not bitwise equivalence. The prompt cache is cloned before lookahead; only prompt state survives between turns.

Target caching was disabled throughout the student isolation arm. Both selector variants answered 8/8 (uncached) and 8/8 (cached) correctly. Growing-turn pipeline means: 7.478s uncached versus 6.762s cached. These include live student inference.

The original full-prefill selections were evaluated directly on the 2 prompts where they differed from the cached student: 2/2 correct. The other 6 selections are identical to cached-student positions already evaluated live.

## Step 4: both caches enabled

Stable selection unions previous retained positions with newly selected positions. Earlier backfill invalidates the affected cached suffix; no old chunk is appended out of order. Token IDs and rotary coordinates must match for every reusable checkpoint. Retention is allowed to grow beyond 30%.

| Prompt tokens | Ordinary cached target, alone | Ordinary cached target, colocated | SpecPrefill, both caches | Spec retention | Target tokens reused |
|---:|---:|---:|---:|---:|---:|
| 1517 | 7.389s | 7.698s | 5.262s | 30.4% | 0 |
| 2051 | 5.131s | 5.494s | 6.252s | 36.8% | 0 |
| 3069 | 6.697s | 7.126s | 6.751s | 36.9% | 0 |
| 5099 | 9.250s | 9.118s | 9.387s | 38.2% | 0 |

Growing-turn mean TTFT: production-relevant ordinary cached target **7.026s**, both-cache SpecPrefill **7.463s** (6.2% slower). The colocated ordinary control separates selection effects from GPU residency pressure.

Ordinary colocated target: 8/8 correct; both-cache SpecPrefill: 8/8 correct. Selected cached responses match exact cold replay **8/8**. Backfill occurred on 6/8 probes. The coordinate-change probe reused 0 tokens, the append probe reused 512, and sparse-to-implicit reused zero.

## Measurement limits

- Timers use the same native Strata transport. Pipeline TTFT runs from before student work to the first target token. No sum of separately measured stages is substituted.
- Models are loaded and warmed before measurements. Loading, HTTP, and chat rendering are excluded. The two pipeline models remain resident throughout each epoch.
- Ordinary standalone control uses a 700 MiB reserve; colocated arms use 4096 MiB. The production baseline benefits from the larger expert cache. Same model weights/hardware/decode controls.
- Only two samples per prompt length, one run per arm, fixed arm ordering, short outputs. This is a bounded feasibility test, not a statistically established general speedup.
- Cached student resets on nonextending or changed prefixes. No partial-prefix/branch cache yet. Lookahead still scores the full prompt; cache copying remains part of measured student time.
- CUDA build and native tests were run. HIP/SYCL compilers were unavailable. Installed production executable and service were not replaced.

Raw prompts, selected IDs, provider outputs, and machine paths remain in ignored `data/strata_specprefill/cache-v1/`. Public summary contains aggregate statistics and hashes only.
