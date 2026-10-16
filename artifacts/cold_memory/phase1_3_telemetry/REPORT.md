# ORCHID cold-memory Phase 1.3 — Buffered Telemetry

This benchmark compares the existing synchronous telemetry path with a
bounded in-memory queue and background batched SQLite flusher. Retrieval
policy, corpus, query construction, FTS candidate generation, and context
assembly policy are unchanged. No awake LLM call is involved.

## Configuration

```json
{
  "awake_model": false,
  "candidate_limit": 20,
  "cold_memory_mode": "shadow",
  "corpus_sizes": [
    100,
    500
  ],
  "iterations": 10,
  "network": false,
  "phase": "cold_memory_phase1_3_telemetry",
  "telemetry_batch_size": 32,
  "telemetry_flush_ms": 5,
  "telemetry_queue_size": 256,
  "timeout_ms": 50,
  "warmup": 2
}
```

## Measured facts

At the largest corpus (500 semantic records):

| Metric | Phase 1.2 synchronous | Phase 1.3 buffered |
|---|---:|---:|
| Context assembly p50 | 26.025 ms | 17.086 ms |
| Context assembly p95 | 35.482 ms | 28.007 ms |
| Retrieval p50 | 1.540 ms | 1.540 ms |
| Retrieval p95 | 2.791 ms | 3.792 ms |
| Retrieval total p50 | 9.415 ms | 1.670 ms |
| Telemetry-path p50 | 7.731 ms | 0.124 ms |
| Timeout count | 0 | 0 |
| Fail-open count | 0 | 0 |

The paired buffered-minus-synchronous context delta was p50
**-8.993 ms**, p95 **4.316 ms**, mean
**-6.827 ms**, and max **102.713 ms**.
The 500-record paired p99 was **31.699 ms**; the large max outlier is retained
rather than excluded and should be treated as a separate tail-contention
follow-up if it appears in production traces.

Candidate ordering, candidate scores, would-inject IDs, and ranking decisions
matched in **260 / 260**
paired comparisons, with **0** mismatches.

## Interpretation

The buffered writer removes durable telemetry transactions from the retrieval
caller. It accepts bounded operations with `put_nowait`, batches them into a
single SQLite transaction, and drops work when the queue is full. Dropped and
flush-failed operation counts are local observability counters; they do not
alter the retrieval result or hot context.

The latency comparison is valid only for this local workload and hardware.
The benchmark does not include an awake model or remote network latency.

## Safety gate

Phase 1.3 is behaviorally equivalent to Phase 1.2 for the measured candidate
and injection decisions. Focused tests also verify FIFO run/update accounting,
queue overflow non-blocking behavior, and existing hot-memory isolation.

## Validation

The focused cold-memory and gateway tests passed (16 tests), as did
`python -m compileall -q memory_gateway tests` and `git diff --check`. The
broader suite passed with the known selector-schema test excluded. The full
suite still has exactly one unrelated failure:
`tests/test_openai_adapter.py::test_selector_and_canonicalizer_send_json_schema_response_formats`,
which compares the old static selector schema with the protocol-hardened
dynamic per-chunk enum. This phase did not modify that schema.

## Recommendation

Keep the buffered writer enabled only for optional cold-memory modes and keep
retrieval in shadow mode while observing queue drops and flush errors. Do not
add dense retrieval, graph expansion, raw-history fallback, reinforcement
policy changes, or ACTIVE promotion in this phase. Revisit the five frozen
semantic misses only after this telemetry baseline is accepted.

## Reproduction

```powershell
python tests/cold_memory_phase1_3_telemetry.py --output-root artifacts/cold_memory/phase1_3_telemetry --iterations 10 --warmup 2
```

Artifacts include `SUMMARY.json`, `CONFIG.json`, `latency_samples.jsonl`, and
the corpus-independent behavior equivalence records in `latency_summary.json`.
