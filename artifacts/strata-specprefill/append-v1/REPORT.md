# Append-only SpecPrefill with addressable omission

Same Flash-Next Swift IQ3_S weights, Qwen3.5-0.8B student, and P100 16GB. Four conversations × four growing turns. Two reuse the previous frozen incoming texts; two fresh held-out cases test delayed recovery and explicit source revisions. All histories retain the model’s actual replies. No fixed assistant-answer replay is substituted.

## Observed result

No-placeholder append-only: 16/16 correct and 3/3 genuine omitted-span recoveries. Growing-turn mean 4.750s versus 7.102s for ordinary standalone caching (33.1% lower) and 8.284s for the original stable-union control.

Grouped references cost 586 tokens versus 11952 individual-reference tokens (95.1% fewer), with 16/16 versus 16/16 accuracy. The automatic placeholder variants showed no accuracy gain over no placeholders on this set. Grouped references provide cheaper addressability; visible omission markers are not required for the measured automatic recovery.

## Implementation

- Exact source text, bytes/character/token offsets, immutable versions, SHA-256 provenance, stable source/chunk IDs, and original student coordinates are archived. ASCII chunks align with the unchanged 32-token selector grid; UTF-8 boundaries are preserved.
- Target evidence uses append-only chat turns and new sequential positions. Omitted historical content is retrieved from the archive and appended with source/version/offset provenance. Nothing is inserted into an old cached prefix. Chronological union controls retain original coordinates.
- Delivered content and confirmed cache state are separate ledger states. Exact source payload is never semantically appended twice. On cache loss the complete accumulated target ID stream is explicitly replayed; no new duplicate evidence block is appended. Loading an archive never assumes a recurrent cache was restored.
- Frozen adaptive mass=0.80, short-input bypass=1024, floor=160, pooling, and student model are unchanged. Only the existing union30 diagnostic uses its original fixed 30% rate.
- Placeholders are emitted only when a source first arrives. Grouped ranges and compact handles resolve through a private lookup table; no tokenizer or weight changes.

## Measured comparisons

| Arm | Accuracy | Held-out | Growing-turn TTFT | Mean reused tokens | Zero-reuse growing turns | Placeholder tokens | Final context tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| full-alone | 16/16 | 8/8 | 7.102s | 2634 | 0/12 | 0 | 5619 |
| full-matched | 16/16 | 8/8 | 7.272s | 2634 | 0/12 | 0 | 5619 |
| union30 | 16/16 | 8/8 | 8.284s | 0 | 12/12 | 0 | 2059 |
| union-adaptive | 10/16 | 8/8 | 5.016s | 181 | 6/12 | 0 | 959 |
| append-none | 16/16 | 8/8 | 4.750s | 699 | 0/12 | 0 | 1625 |
| append-individual | 16/16 | 8/8 | 7.497s | 2004 | 0/12 | 11952 | 4613 |
| append-grouped | 16/16 | 8/8 | 5.052s | 763 | 0/12 | 586 | 1771 |
| fetch-grouped | 13/16 | 5/8 | 4.561s | 769 | 0/12 | 542 | 1440 |
| fetch-explicit | 16/16 | 8/8 | 5.841s | 1174 | 0/12 | 586 | 2074 |

All TTFT timers start before source archival, student selection and lookup, and end at the first final-answer token. For model-directed recovery, initial fetch generation, decoding, lookup and every extra target request are included. Unresolved fetch loops are failures; their full capped response duration is charged rather than treating a fetch token as an answer.

## Recovery and delivery

| Arm | Previously omitted evidence needed | Recovered | Selected duplicate chunks skipped | Off-task chunks delivered | Tool requests |
|---|---:|---:|---:|---:|---:|
| append-none | 3 | 3 | 91 | 90 | 0 |
| append-individual | 3 | 3 | 91 | 90 | 0 |
| append-grouped | 3 | 3 | 91 | 90 | 0 |
| fetch-grouped | 4 | 1 | 72 | 83 | 1 |
| fetch-explicit | 2 | 2 | 111 | 146 | 2 |

“Off-task” is an oracle-relative proxy: delivered chunks outside the known answer span for the current probe. It does not prove those chunks are semantically unnecessary. Recovery means all source chunks covering the needed exact span became delivered; answer accuracy is measured separately. No oracle labels drive selection or retrieval.

## Placeholder tokenization

Same omission sets from the grouped run, re-rendered with the real target tokenizer. Only individual/grouped verbose formats were quality-tested live; compact alternatives below are token-cost measurements, not established quality equivalents.

| Format | Target tokens |
|---|---:|
| individual | 11952 |
| grouped | 586 |
| compact_individual | 4821 |
| compact_grouped | 275 |
| source_only | 266 |

## Cache and memory validation

Reported cache reuse comes from native engine RESUME counts, not text similarity. Append variants verify immutable prior target ID prefixes and use implicit sequential coordinates on every request. Student prompt reuse is checked against its exact previous input length. Positive reuse smaller than the previous prompt is a checkpoint rewind, not necessarily a full eviction; the zero-reuse column distinguishes complete misses.

Across normal append/fetch turns, 0 previously delivered source chunks fell outside the reused prefix; the small observed checkpoint rewinds affected framing rather than old source payload. Source replay after the explicit eviction is counted separately.

A separate grouped-arm probe deliberately evicts the cache and cold-replays the exact request. It must return identical output with zero newly appended duplicate evidence.

| Arm | Sampled GPU peak MiB |
|---|---:|
| full-alone | 16073 |
| full-matched | 12633 |
| union30 | 13111 |
| union-adaptive | 13111 |
| append-none | 13127 |
| append-individual | 13127 |
| append-grouped | 13127 |
| fetch-grouped | 13127 |
| fetch-explicit | 13111 |

NVML is sampled every 50ms. Standalone sampling includes the remaining initialization window; colocated arm sampling covers their requests. Sampling can miss shorter peaks. Student allocator peaks are retained in private rows. Both models remain resident throughout colocated runs. Standalone target reserve=700 MiB; all matched arms reserve=4096 MiB.

## Limits and failures

- Four synthetic lookup/version conversations, one run per arm, fixed arm order. This does not establish general coding-agent quality or statistical significance.
- Old frozen input content is reused, but provenance framing and actual model replies change the cumulative prompt IDs relative to the earlier fixed-history benchmark.
- Native Strata transport is used consistently; network/HTTP overhead is excluded. Archive persistence before omission is included in append/union latency. No sum of offline stage timings replaces measured request latency.
- Repeated identical file revisions share stable chunk IDs and track each original occurrence. Late historical revisions never become current solely because they arrived later. These archive hardening paths are unit-tested; benchmark streams use unique source encounters.
- Prefix IDs are serialized for native cache matching even when already cached; the reused prefix is not re-prefilled. Duplicate semantic delivery and cache-loss re-prefill are distinct costs.
- The initial fetch-grouped instruction retained an abstention rule that conflicted with retrieval. fetch-explicit clarifies the tool-call exception and requires a lookup before abstention. It is an exploratory repair tested on the same frozen cases after observing failures, not a fresh held-out validation of that revised prompt.
- Model-directed retrieval is a bounded explicit text protocol, not a trained new token. At most two archive lookups are allowed. Failures remain in accuracy and latency aggregates.
- Experimental Python code only; installed production Strata and its routing are unchanged. No compaction, length-dependent thresholds, new student, tokenizer changes or weight changes.

## Verification

Root suite: 443 passed, 12 skipped. Student-runtime focused checks: 43 passed. Ruff passed. Audited 36 archives and 2205 exact delivered source-byte ranges. Native logs confirm 185 completed requests, including tool calls and validation/isolation requests. GPU memory was released after the runs. See `verification.json` for machine-readable checks.


| Arm | Conversation | Failed turns | Failure | Needed source chunks delivered |
|---|---|---|---|---|
| union-adaptive | cache-session-0 | 1, 2, 3 | abstention | yes |
| union-adaptive | cache-session-1 | 1, 2, 3 | abstention | yes |
| fetch-grouped | held-recovery | 1, 2, 3 | abstention | no |
