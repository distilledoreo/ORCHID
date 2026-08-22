# ORCHID cold-memory Phase 1.1 — Retrieval Decision Audit

This artifact audits the existing deterministic exact/FTS5 path. It does not
add dense vectors, RRF, graph expansion, an LLM query rewrite, or threshold
tuning. No awake model call is involved.

## Configuration and reproduction

```json
{
  "candidate_limit": 20,
  "corpus_sizes": [
    25,
    100,
    200
  ],
  "includes_awake_llm": false,
  "iterations": 20,
  "max_injected": 3,
  "minimum_score": 0.35,
  "phase": "cold_memory_phase1_1_decision_audit",
  "project_id": "phase1-project",
  "query_forensics": true,
  "thread_id": "phase1-thread",
  "threshold_tuning_performed": false,
  "timeout_ms": 50,
  "token_budget": 512,
  "warmup": 5,
  "warmups_excluded_from_latency_samples": true
}
```

```powershell
python tests/cold_memory_phase1_1_audit.py --output-root artifacts/cold_memory/phase1_1_decision_audit --iterations 20 --warmup 5 --corpus-sizes 25 100 200
```

## Measured facts

- At the largest corpus (200 active semantic records), paired SHADOW-minus-OFF context-preparation overhead was **p50 17.851 ms, p95 20.849 ms, p99 23.773 ms, mean 16.836 ms, max 33.913 ms**.
- OFF context assembly at that size was p50 **2.712 ms**; SHADOW was p50 **21.024 ms**.
- The isolated SHADOW cold-retrieval core was p50 **1.354 ms** and p95 **1.855 ms**. Its full measured sidecar total, including telemetry, was p50 **8.547 ms** and p95 **10.843 ms**.
- Largest-corpus SHADOW stage distributions: query construction p50/p95 **0.028/0.034 ms**; DB checkout **0.798/0.950 ms**; FTS **0.407/0.597 ms**; ranking **0.052/0.148 ms**; token budget **0.002/0.003 ms**; telemetry **7.116/9.220 ms**.
- The benchmark used a monotonic high-resolution `perf_counter_ns`/`perf_counter` clock with reported resolution **1e-07 seconds**. FTS rounded to exactly 0.000 ms in **3.8%** of 520 largest-corpus samples; this is a measured distribution, not evidence of a 15–16 ms timer quantum.
- The quality fixture contains **26** queries: exact/symbol, lexical natural-language, deliberately semantic-only future-dense, vague, and explicit no-match cases.
- Exact/symbol queries would inject the expected memory **100.0%** of the time. Lexical natural-language queries would inject the expected memory **37.5%** of the time.
- Query-level irrelevant would-injection rate was **7.7%**. False-injection reasons were: **{"bm25_or_activation_ranking": 1, "identifier_collision": 1}**.
- Shadow timeout count was **0** and fail-open count was **0** across 520 measured largest-corpus samples.

## Decision-pipeline audit

Each record in `quality_results.jsonl` preserves the original input, bounded
constructed query, input/identifier/ordinary terms, actual FTS MATCH query,
raw FTS candidates with BM25 scores, score components, threshold decision, and
would-inject decision.

- A — query construction issue: **0** classified failures.
- B — correct candidate ranked too low: **3** classified failures.
- C — candidate ranked sufficiently but rejected by threshold or budget: **2** classified failures.
- D — no useful lexical overlap, likely future dense-retrieval case: **5** classified failures; these correspond to **0.0%** Recall@5 on the deliberately semantic-only subset and are not treated as lexical implementation bugs.
- FTS/tokenizer/scope/actual-bug/timeout categories are preserved separately in `quality_summary.json`; no category was silently folded into D.
- Input-term discard was observed on **0** queries. This is a signal for review, not an automatic claim that every discarded term was harmful.

## Interpretation

The data supports the original Phase 1 conclusion: exact identifier retrieval is
strong, while lexical natural-language recall is the current weakness. The
forensic trace separates query-construction loss from ranking/threshold loss
and genuine semantic gaps. FTS latency is only one component of the measured
sidecar cost; the stage distributions show where the remaining local overhead
is located.

The local ~17.9 ms p50 delta is small relative to a remote model
round trip but is not free before every invocation. Shadow mode should remain
optional while this decision behavior is audited; broad implicit injection is
not justified by the **7.7%** false-injection rate.

## Hot-path and fail-open evidence

Focused tests verify that SHADOW preserves the assembled hot messages, ACTIVE
capsule identity, raw-tail IDs, and current request/tool context. They also
exercise FTS failure, timeout, malformed rows, empty index, and provider
exception. In each case retrieval returns no cold context and hot assembly
continues. Retrieval telemetry is sidecar state, not an event; no retrieved
memory is appended to events or promoted into ACTIVE.

## Explicit answers

1. **Shadow overhead:** p50 **17.851 ms** / p95 **20.849 ms** context-preparation delta at 200 records; isolated cold core p50/p95 **1.354/1.855 ms**.
2. **Perceptibility:** likely not dominant beside a remote model call, but measurable and potentially perceptible in local proxy timing; it is not free.
3. **Exact/symbol solve rate:** **100.0%**.
4. **Lexical natural-language solve rate:** **37.5%**.
5. **Dense motivation:** only category D cases with little/no lexical overlap; the records identify them individually.
6. **Irrelevant would-injections:** **7.7%** of quality queries; reason counts are in `quality_summary.json`.
7. **Hot-memory alteration from cold failure:** **No** in all tested failure modes.
8. **Retrieved memory entering events or ACTIVE:** **No**.
9. **Shadow safety:** **Yes**, for the tested cases; keep it shadow-only and fail-open.
10. **Single next capability:** **deterministic lexical ranking/threshold calibration**, driven by the recorded B/C misses and false-injection reasons. Dense retrieval should wait until those categories are reduced and the residual D gap is measured separately.

## Validation

The focused cold-memory suite, compileall, and `git diff --check` are run after
the implementation changes. The known selector-schema failure in
`tests/test_openai_adapter.py::test_selector_and_canonicalizer_send_json_schema_response_formats`
is unrelated: it expects the old static selector schema while the repository
uses the protocol-hardened dynamic per-chunk enum. It must remain separate from
this retrieval result.

Artifacts: `SUMMARY.json`, `CONFIG.json`, `CORPUS.json`,
`quality_results.jsonl`, `quality_summary.json`, `latency_samples.jsonl`, and
`latency_summary.json`.
