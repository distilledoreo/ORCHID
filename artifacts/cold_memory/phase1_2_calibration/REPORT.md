# ORCHID cold-memory Phase 1.2 — Deterministic Calibration

This phase changes only lexical candidate scoring and injection gating. Query
construction, FTS candidate generation, telemetry, hot-memory assembly, and
the five semantic-only cases remain unchanged. No dense retrieval or LLM is
used.

## Calibration protocol

- The 26 Phase 1.1 quality traces were loaded from the frozen artifact at
  `phase1_1_decision_audit/quality_results.jsonl`.
- The development set contains 18 exploratory
  perturbation and negative queries. It was inspected while shaping the
  policy and is not presented as an untouched holdout.
- The acceptance holdout contains 18
  separately fixed queries with reordered prose, identifiers surrounded by
  noise, common terms mixed with distinctive terms, and vague/negative cases.
- The calibrated policy is fixed in `CONFIG.json` before the acceptance
  holdout comparison; no acceptance result was used to change it.
- Baseline replay matched frozen candidate IDs on 26/26 queries and frozen would-inject IDs on 26/26 queries.
- Identifier decomposition is ranking-only; the FTS MATCH query is unchanged.

## Policy

```json
{
  "activation_weight": 0.05,
  "distinctive_weight": 0.25,
  "fts_rank_weight": 0.4,
  "identifier_weight": 0.15,
  "lexical_saturation": 3,
  "lexical_weight": 0.15,
  "minimum_distinctive_overlap": 1,
  "minimum_identifier_coverage": 0.66,
  "minimum_lexical_overlap": 2,
  "minimum_margin": 0.05,
  "minimum_score": 0.6,
  "name": "phase1_2_calibrated",
  "secondary_score_gap": 0.1
}
```

The calibrated score is a bounded deterministic combination of FTS rank,
lexical overlap, strong-identifier coverage, distinctive-token overlap, and
a small activation prior. It requires at least 66% strong-identifier coverage
when query identifiers exist, otherwise at least two lexical terms including one
candidate-distinctive term. A 0.05 minimum adjacent margin and 0.10 maximum
gap for secondary injections prevent weak related memories from joining a
strong primary match.

## Measured before/after

| Set / metric | Baseline | Calibrated |
|---|---:|---:|
| Calibration exact expected injection | 100.0% | 100.0% |
| Calibration lexical expected injection | 37.5% | 87.5% |
| Calibration false injection queries | 7.7% | 0.0% |
| Development exact expected injection | 88.9% | 100.0% |
| Development lexical expected injection | 83.3% | 100.0% |
| Development false injection queries | 5.6% | 0.0% |
| Development negative would-inject rate | 33.3% | 0.0% |
| Acceptance exact expected injection | 72.7% | 100.0% |
| Acceptance lexical expected injection | 100.0% | 100.0% |
| Acceptance false injection queries | 22.2% | 0.0% |
| Acceptance negative would-inject rate | 33.3% | 0.0% |

The calibrated policy recovered **4** frozen ranking/threshold
misses: `lexical_cas, lexical_sqlite, lexical_provenance, lexical_budget`. It intentionally does
not claim to solve the semantic-only Phase 2 cases. The remaining frozen
ranking/threshold miss is `lexical_timeout`.

## Interpretation

The calibration objective is precision first. Exact/symbol recall is a hard
guardrail; a lexical match is injected only with enough lexical evidence and
candidate distinction. The report preserves all per-candidate score details in
`calibration_comparison.jsonl`, `development_holdout_results.jsonl`, and
`holdout_results.jsonl`, including margins, evidence gates, and decisions.

The calibrated policy is **safe for shadow/inject enablement** under the explicit comparison rule in this report. This is not evidence for dense retrieval; the remaining semantic-only cases remain labeled for a later phase.

## Recommendation

Adopt the calibrated policy for optional cold-memory modes, keep retrieval shadow-only while observing production traces, and make Phase 1.3 a separate synchronous-telemetry reduction experiment.

Do not mix telemetry buffering or asynchronous writes into this ranking result.

## Reproduction

```powershell
python tests/cold_memory_phase1_2_calibration.py --output-root artifacts/cold_memory/phase1_2_calibration
```

Focused validation and the known unrelated selector-schema result are recorded
outside this measurement artifact.
