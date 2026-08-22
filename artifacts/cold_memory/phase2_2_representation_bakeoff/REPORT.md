# ORCHID Phase 2.2 — Representation Bakeoff

## Scope

Four ONNX sentence encoders were evaluated with the frozen Phase 2.1
query corpus, split, feature extraction, calibration grid, and decision
protocol. No model was connected to the gateway, and no dense candidate
was injected into context.

## Primary metric

The gate is maximum holdout ACCEPT recall among models whose calibrated
policy achieves at least 99% holdout ACCEPT precision with at least one
accepted query. Holdout data was not used to select thresholds.

| Model | Calibration precision | Holdout precision | Holdout ACCEPT recall | ACCEPT count | p50 ms | p95 ms | Gate |
|---|---:|---:|---:|---:|---:|---:|---|
| sentence-transformers/all-MiniLM-L6-v2 | 90.0% | 71.4% | — | 7 | 3.470 | 4.504 | FAIL |
| sentence-transformers/all-MiniLM-L12-v2 | 100.0% | 100.0% | 8.0% | 4 | 6.653 | 8.231 | PASS |
| sentence-transformers/paraphrase-MiniLM-L3-v2 | 100.0% | 85.7% | — | 7 | 1.879 | 2.658 | FAIL |
| sentence-transformers/multi-qa-MiniLM-L6-cos-v1 | 87.5% | 100.0% | 6.0% | 3 | 3.708 | 5.109 | PASS |

## Measured facts

- Frozen corpus: `cbcaa9ea29a3c0b93c504f678e66331675f832eb7cce7acfca1467db39856541`; `301` semantic queries (`151` calibration and `150` holdout) plus `18` lexical guardrails, `319` rows total.
- Candidate count: `4`; model embeddings were precomputed before query timing.
- Primary target: `99.0%` ACCEPT precision on untouched holdout.
- Holdout precision is a small-sample point estimate: each model's threshold was calibrated only on the 151-case calibration split; the 150-case holdout was used only for the registered comparison gate.
- Hot-path state fingerprint unchanged: `True`; production injections/events/ACTIVE mutations were `0`.

## Failure-type breakdown

### all_minilm_l6_v2_baseline
- `clear_semantic_positive`: 50 queries, ACCEPT precision 100.0%, ACCEPT recall 10.0%, false accepts 0.
- `near_miss_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.
- `no_cold_memory_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.
- `superseded_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 2.
- `wrong_fact_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.

### all_minilm_l12_v2
- `clear_semantic_positive`: 50 queries, ACCEPT precision 100.0%, ACCEPT recall 8.0%, false accepts 0.
- `near_miss_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.
- `no_cold_memory_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.
- `superseded_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.
- `wrong_fact_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.

### paraphrase_minilm_l3_v2
- `clear_semantic_positive`: 50 queries, ACCEPT precision 100.0%, ACCEPT recall 12.0%, false accepts 0.
- `near_miss_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.
- `no_cold_memory_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.
- `superseded_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 1.
- `wrong_fact_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.

### multi_qa_minilm_l6_cos_v1
- `clear_semantic_positive`: 50 queries, ACCEPT precision 100.0%, ACCEPT recall 6.0%, false accepts 0.
- `near_miss_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.
- `no_cold_memory_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.
- `superseded_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.
- `wrong_fact_negative`: 25 queries, ACCEPT precision 0.0%, ACCEPT recall 0.0%, false accepts 0.

## Lexical guardrail

- `all_minilm_l6_v2_baseline`: dense ACCEPT precision 100.0%, positive ACCEPT recall 80.0%, ACCEPT count 12; FTS behavior was unchanged.
- `all_minilm_l12_v2`: dense ACCEPT precision 100.0%, positive ACCEPT recall 73.3%, ACCEPT count 11; FTS behavior was unchanged.
- `paraphrase_minilm_l3_v2`: dense ACCEPT precision 100.0%, positive ACCEPT recall 86.7%, ACCEPT count 13; FTS behavior was unchanged.
- `multi_qa_minilm_l6_cos_v1`: dense ACCEPT precision 100.0%, positive ACCEPT recall 80.0%, ACCEPT count 12; FTS behavior was unchanged.

## Interpretation

The L12 candidate is the cleanest separator in this run, but its 8.0% holdout ACCEPT recall means it recovers only 4 of 50 semantic positives. The 100.0% precision point estimate is based on four accepted holdout queries, so it is not evidence that implicit dense injection is production-ready. The other candidates either admitted false superseded-history memories or produced too little accepted recall to be preferable. Exact/symbol recall remains the FTS responsibility and is not credited to dense retrieval here.

## Recommendation

`all_minilm_l12_v2` cleared the gate with 8.0% holdout ACCEPT recall. This is a representation candidate, not an injection approval: keep it shadow-only and run a larger calibration/reproducibility check before any fusion or injection experiment.

The bakeoff does not authorize dense injection. RRF and reranking are
explicitly deferred until a representation first demonstrates useful
precision-constrained separation.

## Reproduction

```text
python tests/cold_memory_phase2_2_representation_bakeoff.py --output artifacts\cold_memory\phase2_2_representation_bakeoff --query-corpus artifacts\cold_memory\phase2_1_abstention_calibration\QUERY_CORPUS.jsonl --corpus-size 100 --warmup 2 --iterations 5
```
