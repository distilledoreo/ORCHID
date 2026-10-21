# Integrated exact lookup: three targeted live checks

The normal `CompactingSession.answer()` pipeline now recovers all three residual failures: **6/6 fields correct**, using the real 0.8B student and Flash-Next. The earlier unchanged-prompt controls reproduced the original failures and scored 3/6 fields.

| Turn | Failure addressed | Integrated answer | Correct fields | Student passes | Exact frame tokens |
|---|---|---|---:|---:|---:|
| 4 | Linked earlier revision | `217379,385680` | 2/2 | 1 | 274 |
| 19 | Wrong revision despite delivered evidence | `320624,731401` | 2/2 | 1 | 246 |
| 21 | Late omitted evidence | `436414,265615` | 2/2 | 2 | 254 |

## Runtime integration

The resolver lives in `memory_gateway/specprefill/exact_lookup.py` and is invoked by `CompactingSession.answer()`, including opt-in gateway sessions that use that class. Exact file/revision/function bindings bypass summary scope; literal ROUTE_KEY addresses are resolved from archived source code without executing it. Each symbol stays paired to its requested source revision. No expected answer is passed to the resolver or selector.

The resulting block is included in the student scoring input and appended before the target question. Explicitly addressed definitions are guaranteed delivery; student selection still governs ordinary retrieved chunks. This is the same delivery strategy exercised by the prior counterfactual, now inside the session controller. It does not require the student to select those explicit definitions.

Exact and ordinary evidence share the existing 2,048-token allowance independently for each model. Full frame size and answer headroom are checked, traversal terminates with visited addresses and a 32-definition limit, and parsing is bounded to 256 KiB per source. Missing/unsupported/oversized lookups defer as a whole to ordinary retrieval with an event reason. Prompt prefixes are appended to rather than rewritten. New answer events carry exact lookup status, token count, and immutable source provenance. The enabled/disabled setting survives checkpoint restore.

## Verification

The three pre-answer snapshots were reconstructed from the previous 41/44 run using its frozen replies, summary, and logged expanded/delivered chunk decisions. Across the complete reconstruction, target prompt lengths and compaction event matched and the final student/target token arrays matched the original checkpoint. Each live case then restored an independent copy, enabled the integrated resolver, and called normal session.answer with a fresh real student cache and the unchanged native engine configuration. No logged chunk decisions or frozen model outputs were used for these three new answers.

Existing student and target prefix token arrays remained identical throughout each live append. The 32K input/output bounds were checked. All three answer events report successful lookup delivery; student pass counts and target input traces were saved. GPU memory returned to zero after the run.

**Offline suite: 565 passed, 5 skipped.** Focused coverage includes delivery with no nomination and no student-selected chunks, exact revision binding, literal route traversal/cycles, shared token budgets, missing revision and budget deferrals, and persisted policy/restore. Ruff passes for the changed production and test modules.

## Limits

These are three live answers on isolated saved histories, not a new 44/44 multi-turn result. A complete rerun is still needed to measure effects on subsequent replies, compaction timing, quality, and cache latency. Freshly restored historical caches make these individual timings unsuitable for comparison with the full-session baseline.

Supported syntax is deliberately narrow: explicit Python file/revision/function requests and literal ROUTE_KEY links. General semantic recall and factual grounding of arbitrary summary claims remain separate work.

Raw checkpoints, actual student/target traces, implementation snapshot, and calls: [/home/distilledoreo/.cache/orchid-three-way-28k/integrated-three-20261017](/home/distilledoreo/.cache/orchid-three-way-28k/integrated-three-20261017).
