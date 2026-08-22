# ORCHID Phase 2.3 — Status-Aware Dense Eligibility

## Scope

MiniLM-L12 (`a50ef00143b4d5391434df20ae11632588ac25be`) was evaluated offline using the frozen Phase 2.1 calibration split, 25 additional status-specific calibration negatives, and an expanded untouched holdout. The implicit path filters dense candidates by scope and status before confidence features and ACCEPT calibration. Superseded candidates remain available to the explicit-search policy. No dense result entered ORCHID context.

## Primary metric

Maximum semantic holdout ACCEPT recall at >=99% ACCEPT precision: **—**.

| Path | Holdout precision | Recall | ACCEPT | False ACCEPTs | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| Status-aware implicit | — | — | 0 | 0 | 6.912 | 8.327 |
| Ungated comparison | 0.0% | 0.0% | 3 | 3 | — | — |

## Measured facts

- Base Phase 2.1 corpus SHA-256: `cbcaa9ea29a3c0b93c504f678e66331675f832eb7cce7acfca1467db39856541`; expanded corpus SHA-256: `1c535234a3374e2b705b3501980151711bd11ee8220b8447db6ec10e0a73be0b`.
- Semantic queries: `476` total; calibration `176`; untouched holdout `300`. External lexical guardrails: `18`.
- Memory rows: `105` total; ACTIVE `100`; SUPERSEDED `5`.
- Status filter removed `403` raw top-k candidates across the holdout queries.
- Superseded candidates appeared in `50/50` status-negative queries and remained explicit-searchable in `50` of them; implicit superseded candidates accepted: `0`.
- Hot-state fingerprint unchanged: `True`; events, ACTIVE state, and memory status/content fingerprints were unchanged by query evaluation.

## Status-gate effect

The status-aware path changed holdout false ACCEPTs from `3` to `0` and recall from `0.0%` to `0.0%`. This comparison uses independently calibrated policies over the same calibration split.

## Failure-type breakdown

- `clear_semantic_positive`: 50 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false ACCEPTs 0.
- `expanded_near_miss_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false ACCEPTs 0.
- `expanded_no_cold_memory_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false ACCEPTs 0.
- `expanded_semantic_positive`: 50 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false ACCEPTs 0.
- `expanded_wrong_fact_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false ACCEPTs 0.
- `near_miss_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false ACCEPTs 0.
- `no_cold_memory_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false ACCEPTs 0.
- `superseded_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false ACCEPTs 0.
- `superseded_status_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false ACCEPTs 0.
- `wrong_fact_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false ACCEPTs 0.

## Interpretation

The gate is a valid safety rule: SUPERSEDED memories are absent from implicit eligibility but remain searchable in explicit mode. It should not be credited for removing false positives whose winning candidate is still ACTIVE; those remain genuine relevance/abstention failures. The larger holdout is the decision gate, and its precision is still a point estimate rather than a production guarantee.

## Recommendation

Keep dense shadow-only. If status-aware recall remains in the single-digit or low-teens range, stop tuning eligibility and move to a small reranker over ambiguous dense candidates only. Do not add RRF, graph expansion, raw fallback, or production injection in this phase.

## Reproduction

```text
python tests/cold_memory_phase2_3_status_eligibility.py --output artifacts\cold_memory\phase2_3_status_eligibility --base-corpus artifacts\cold_memory\phase2_1_abstention_calibration\QUERY_CORPUS.jsonl --corpus-size 100 --warmup 2 --iterations 5
```
