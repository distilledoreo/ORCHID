# ORCHID Phase 2.1 — Dense Abstention Calibration

## Scope

This is an offline calibration of three dense decisions: `ACCEPT`,
`AMBIGUOUS`, and `ABSTAIN`. The calibrated policy uses only deterministic
features: top-1 score, top-1/top-2 margin, score distribution statistics,
lexical overlap, identifier agreement, scope agreement, and activation prior.
It is not imported by the gateway and does not change FTS, ContextAssembler,
RRF, reranking, or injection policy.

## Measured facts

- Labeled semantic corpus: `301` queries; calibration `151`, untouched holdout `150`.
- Holdout positive queries: `50`; holdout negatives: `100`.
- Calibrated policy: `{'name': 'phase2_1_calibrated', 'accept_min_score': 0.52, 'accept_min_margin': 0.0, 'accept_min_percentile': 0.0, 'accept_min_lexical_overlap': 0, 'accept_min_identifier_agreement': 0.0, 'accept_min_scope_agreement': 1.0, 'ambiguous_min_score': 0.2, 'ambiguous_min_percentile': 0.5}`; target precision `99.0%`; target met on calibration = `False`.
- Calibration accept precision/recall: `90.0%` / `18.0%`.
- Holdout accept precision (abstention precision): `71.4%`; accepted `7` of `150`; holdout recall `10.0%`.
- Holdout decision counts: ACCEPT `7`, AMBIGUOUS `123`, ABSTAIN `20`.
- Holdout positive outcomes: accept `10.0%`, ambiguous `90.0%`, abstain `0.0%`.
- Holdout dense query latency: p50 `3.570` ms, p95 `4.522` ms, p99 `5.008` ms, mean `3.623` ms, max `5.652` ms over `1500` samples.
- Holdout negative non-accept rate: `98.0%`; false accepts: `2`.
- Offline hot-path state unchanged: `True`.

## Interpretation

`ACCEPT` is the only state eligible for a future injection experiment. The
current run injected nothing. `AMBIGUOUS` means dense produced a plausible
candidate but the calibrated evidence was insufficient; `ABSTAIN` means no
candidate cleared even the weak plausibility floor.

The holdout is the decision gate. Calibration metrics are not evidence of
generalization. The corpus includes clear positives, near-miss negatives,
same-topic wrong-fact negatives, superseded-history negatives, and unrelated
no-memory queries. Exact/symbol guardrails remain separate lexical behavior.

## Recommendation

Do not enable dense `ACCEPT` in production yet unless holdout precision clears
the registered target. The next useful experiment is a larger labeled corpus
or a separately validated confidence model only if this deterministic gate
fails to achieve the required precision. Do not add RRF while dense still
cannot reliably abstain.

## Artifacts and reproduction

- `SUMMARY.json` — metrics and policy.
- `POLICY.json` — selected thresholds and calibration search evidence.
- `QUERY_CORPUS.jsonl` — frozen labeled query definitions and split.
- `quality_results.jsonl` — per-query features, candidates, FTS comparison, and decision.
- `latency_samples.jsonl` — holdout dense query timings.
- `CONFIG.json`, `MODEL.json`, `CORPUS.json` — reproducibility metadata.

```text
python tests/cold_memory_phase2_1_abstention_calibration.py --output artifacts\cold_memory\phase2_1_abstention_calibration --corpus-size 100 --model-root artifacts\cold_memory\phase2_0_dense_experiment\model --warmup 2 --iterations 10
```

## Preserved non-goals

No dense result entered events or ACTIVE, no production retrieval mode was
changed, and no vectors were fused with FTS.
