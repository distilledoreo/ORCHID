# SpecPrefill production punchlist

> **Active goal and recovery plan: [specprefill-goal.md](specprefill-goal.md).** Items below predate it; Phase gates there take priority.

This tracks the indexed, addressable-compaction path separately from ORCHID's
existing summary/observer path. Passing offline tests does not mark live model
accuracy, latency, or operational readiness complete.

- [x] Immutable source revisions, compact external manifests, and generation-aware recovery.
- [x] Indexed identifier/text retrieval before student rescoring; explicit finite-scan fallback.
- [x] Automatic token-counted compaction admission before source ingestion and answers.
- [x] Installable runtime package and opt-in gateway text-chat route with JSON and native token SSE.
- [x] Serialized model/cache ownership, bounded resident sessions, checkpoint restart, and request replay ledger.
- [x] Offline integration coverage for headroom, staged long histories, streaming/disconnect, retries, restart, concurrency, and project isolation.
- [x] **Paged raw archive storage:** SQLite source bodies and token pages, lazy chunk reads, configurable bounded payload cache, stable source IDs/hashes/provenance, and lazy restore. See [storage details](specprefill-paged-storage.md).
- [x] **Incremental archive persistence:** commit new raw records/events and bounded active state together; replace full JSON rewrites with a stable descriptor; test rollback, interrupted publication, and legacy migration.
- [x] **Incremental gateway history persistence:** per-message history-ledger rows append once per turn; legacy snapshot databases migrate on open. Long-turn sessions no longer rewrite the full fingerprint list per request.
- [x] Two-turn native P100 smoke: real 0.8B student + Flash-Next, one automatic compaction, both replies `37`, and 230 target prefix tokens reused after rebuild. This is not a latency/accuracy benchmark.
- [ ] Live end-to-end native gateway validation on the existing 0.8B student + Flash-Next hardware, including repeated automatic compactions and actual target prefix reuse.
- [ ] Matched accuracy/recall and total latency against ordinary cached Flash-Next; include archive search and compaction cost. No additional unrequested arm sweep.
- [x] Quick cached 32K versus 8K comparison on a 26.6K-source-token conversation: full context 44/44 in 174.3s; normal compaction 30/44 in 415.6s. SpecPrefill failed before turn 9 (12/16 partial); see [report](../artifacts/strata-specprefill/three-way-28k-20261016/REPORT.md). This does not complete broad validation above.
- [x] Cached 32K SpecPrefill follow-up completed: 39/44 in 296.9s, one summary-headroom compaction; cached full context remains 44/44 in 174.3s. Co-resident P100 run required 512-token student blocks and the expandable PyTorch allocator; see [follow-up report](../artifacts/strata-specprefill/cached-32k-specprefill-20261016/REPORT.md). These benchmark settings do not change the gateway defaults.
- [x] Retrieval-fix rerun on the same 44 fields: 41/44 overall and 6/8 late recall in 299.9s; see [rerun report](../artifacts/strata-specprefill/cached-32k-retrieval-fix-20261017/REPORT.md).
- [x] Integrated bounded exact file/revision/function lookup and literal source-link traversal, with guaranteed delivery to both model inputs. Three isolated live checks through normal session.answer recovered all remaining failed questions (6/6 fields); see [integration report](../artifacts/strata-specprefill/integrated-three-20261017/REPORT.md). This is not a complete 44-field rerun or general semantic-recall validation.
- [x] Current 8K exact-lookup stress run: 20/20 partial across ten turns and seven successful compactions, then failed before turn 11 when a 1,024-token summary was truncated inside a provenance string. No late-recall fields reached; no disabled rerun per user direction. See [8K report](../artifacts/strata-specprefill/8k-exact-20261017/REPORT.md). Structured-summary generation remains unresolved.
- [ ] Reliable live structured-summary provenance coverage: the model returned archive IDs instead of the requested M IDs and omitted required coverage at the sixth compaction attempt. Keep fail-closed validation; do not treat missing references as valid output.
- [ ] Tool/function-call and multimodal protocol support. The initial native gateway rejects these explicitly.
- [ ] Real semantic-encoder recall validation and gateway embedding configuration. The current gateway factory uses lexical/symbol retrieval; optional semantic adapters remain available programmatically and in the experiment CLI.
- [ ] Operational limits for queued requests, readiness/warmup, retention/disk capacity, backup/export, and long-running session soak tests.

The existing native binary, running service configuration, and earlier measured
latency/correctness baselines are unchanged by this implementation.
