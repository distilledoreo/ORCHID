# ORCHID memory upgrade delivery

Implemented in an isolated worktree from `protocol-hardened-continuation` and
its reviewed runtime fixes. Original uncommitted work and unrelated benchmark
changes were archived separately during repository cleanup. The PR links below
record integration status; merging does not imply deployment.

| Change | PR | Result |
| --- | --- | --- |
| Runtime foundation | [#8](https://github.com/distilledoreo/ORCHID/pull/8) | Distinct occurrences, atomic promotion/job completion, retry progress, validated caching, tracked core imports. |
| Phase 0 | [#9](https://github.com/distilledoreo/ORCHID/pull/9) | Credential-free real-gateway replay and portable tests. |
| Phase 1 | [#10](https://github.com/distilledoreo/ORCHID/pull/10) | Normalized ingestion, real usage, SSE parsing, immutable legacy views. |
| Phase 2 | [#11](https://github.com/distilledoreo/ORCHID/pull/11) | Gap-free runtime layout, fixed coverage, stable serialization, capacity errors. |
| Phase 3 | [#12](https://github.com/distilledoreo/ORCHID/pull/12) | Observer deltas, exact evidence, supersession, reflection, endpoint/idle settings. |
| Phase 4 | [#13](https://github.com/distilledoreo/ORCHID/pull/13) | Scoped FTS5/range HTTP recall, optional embeddings, stdio MCP. |
| Phase 5 | [#14](https://github.com/distilledoreo/ORCHID/pull/14) | Optional recoverable offload/masking and file ledger; off by default. |

## Verification and costs

Python 3.11.15 and Python 3.12 each passed **405 tests, seven optional checks
skipped**. The clean 3.11 environment used independently installed declared test
extras. Import isolation, reply roles, deterministic serialization, exact quote
rejection, failed/lost CAS paths, sanitized diagnostics, stream cancellation,
scope/privacy, and oversized-source recovery are tested. Targeted syntax and
undefined-name lint passed. No provider credentials or paid model calls were used.

| Final deterministic arm | Calls | Input tokens | Output tokens | Probes |
| --- | ---: | ---: | ---: | ---: |
| Full context | 0 | 0 | 0 | 4/4 |
| Rolling summary | 10 | 15,061 | 130 | 4/4 |
| Corrected three-stage | 40 | 65,722 | 1,168 | 4/4 |
| Observer | 10 | 5,300 | 415 | 4/4 |
| Separate coding fixture | 14 | 7,533 | 603 | 6/6 |

Observer input is **12.40x lower** than the same-fixture three-stage arm. Coding
uses a separate fixture/hash and is not part of that ratio. Observer and coding
both recover the omitted deadline through scoped HTTP recall: unknown before
retrieval, Friday afterward. `artifacts/memory_upgrade/phase-5.json` includes
fixture hashes, costs/wall times per 1,000 source tokens, per-call estimates,
promotion counts, ledger entries, and probe results.

Final input estimates include known schemas, framing, and fake system text.
Rolling summary has no structured schema and reports its text-input estimate.
Earlier reports retain their original payload-only estimates/hashes; the earlier
49x figure is not the final cost claim. Output estimates describe JSON/text.
Provider tokenization, hidden overhead, GPU timing, and live quality are not
established by fake runs.

## Configuration and limits

The gateway defaults to stable layout. Existing direct `ContextAssembler`
callers retain the legacy constructor default and must select `layout="stable"`;
historical layout remains for comparisons.

Set `ORCHID_OBSERVER_URL` and `ORCHID_OBSERVER_MODEL` to enable the observer in
auto mode, with `ORCHID_OBSERVER_API_KEY` for transport if required. Observer
output defaults to 2,048 tokens; quotes are limited to 512 characters. Reserve
output/provider overhead when configuring input capacity. Idle admission does
not preempt a dispatched call or coordinate external GPU workloads. Coding
stays off unless explicitly enabled.

Live-model non-inferiority, non-repetitive workload ratios, real cache/GPU
behavior, live embeddings, and GUI MCP integration remain unverified. Exact
quotes establish source identity, not semantic entailment. Protected legacy
text/file state that cannot fit causes a safe failed candidate. Historical live
artifacts and thresholds were preserved.

Details: [evaluation](upgrade-evaluation.md), [observer](observer-memory.md),
[recall](raw-recall.md), [coding mode](coding-agent-mode.md), and
[invariants](invariants.md).
