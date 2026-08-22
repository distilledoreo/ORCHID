# ORCHID Phase 2.4 — Reranker-Only Shadow Evaluation

## Scope

Dense candidates were fixed to MiniLM-L12 (`a50ef00143b4d5391434df20ae11632588ac25be`), top-5, after Phase 2.3 scope/status eligibility. `cross-encoder/ms-marco-MiniLM-L-2-v2` (`1b5cd67b15209f24824c50370e0397743aa9b787`) scored only queries without a confident FTS would-inject result. No reranker result entered ORCHID context.

## Primary metric

Maximum reranker ACCEPT recall at >=99% precision on the untouched reranker-eligible holdout: **—**.

| Path | Precision | Recall | ACCEPT | False ACCEPTs |
|---|---:|---:|---:|---:|
| Reranker-only eligible queries | 24.5% | — | 49 | 37 |
| Combined FTS bypass + reranker | 23.5% | 46.0% | 196 | 150 |

## Measured facts

- Holdout: `350` rows, `203` reranker-evaluated, `147` lexical bypasses; calibration: `196` rows, `107` evaluated.
- Dense candidate Recall@5 before reranking: `81.0%` over holdout positives.
- Reranker latency over `1015` samples: p50 `7.643` ms, p95 `9.388` ms, p99 `10.169` ms.
- Hot-state fingerprint unchanged: `True`; no events, ACTIVE mutations, or production injections.

## Failure-type breakdown

- `clear_semantic_positive`: 29 evaluated, ACCEPT precision 85.7%, recall 20.7%, false ACCEPTs 1.
- `current_irrelevant_negative`: 15 evaluated, ACCEPT precision 0.0%, recall 0.0%, false ACCEPTs 8.
- `expanded_near_miss_negative`: 12 evaluated, ACCEPT precision 0.0%, recall 0.0%, false ACCEPTs 1.
- `expanded_no_cold_memory_negative`: 25 evaluated, ACCEPT precision 0.0%, recall 0.0%, false ACCEPTs 0.
- `expanded_semantic_positive`: 29 evaluated, ACCEPT precision 85.7%, recall 20.7%, false ACCEPTs 1.
- `expanded_wrong_fact_negative`: 10 evaluated, ACCEPT precision 0.0%, recall 0.0%, false ACCEPTs 4.
- `near_duplicate_negative`: 14 evaluated, ACCEPT precision 0.0%, recall 0.0%, false ACCEPTs 6.
- `near_miss_negative`: 12 evaluated, ACCEPT precision 0.0%, recall 0.0%, false ACCEPTs 1.
- `no_cold_memory_negative`: 25 evaluated, ACCEPT precision 0.0%, recall 0.0%, false ACCEPTs 0.
- `superseded_negative`: 17 evaluated, ACCEPT precision 0.0%, recall 0.0%, false ACCEPTs 6.
- `superseded_status_negative`: 5 evaluated, ACCEPT precision 0.0%, recall 0.0%, false ACCEPTs 5.
- `wrong_fact_negative`: 10 evaluated, ACCEPT precision 0.0%, recall 0.0%, false ACCEPTs 4.

## Interpretation

The reranker is evaluated as a relevance judge, not as a way to claim dense candidate recall. Lexical hits are bypassed and remain the authoritative Phase 1 path. Abstention is reported as zero recall contribution; it is not treated as success.

## Recommendation


The reranker does not clear the precision-constrained recall gate. Pause dense implicit retrieval; do not add RRF, graph expansion, or more threshold heuristics. Keep dense/reranker available only for explicit memory search or revisit representation/training data.

## Reproduction

```text
python tests/cold_memory_phase2_4_reranker_shadow.py --phase23 artifacts\cold_memory\phase2_3_status_eligibility --output artifacts\cold_memory\phase2_4_reranker_shadow --warmup 2 --iterations 5 --top-k 5
```
