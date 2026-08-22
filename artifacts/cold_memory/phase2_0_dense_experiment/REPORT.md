# ORCHID Phase 2.0 — Dense Retrieval Incremental Value Experiment

## Scope

This run tests a precomputed dense index against the unchanged calibrated
Phase 1 FTS path. Dense candidates were never passed to `ContextAssembler`,
never injected into a model context, never written as events, and never used to
modify ACTIVE. The measurement-only dense would-inject gate is a report
instrument, not a production policy.

## Measured facts

- Model: `sentence-transformers/all-MiniLM-L6-v2` at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`; dimension `384`, attention-mask mean pooling, L2 normalization, cosine dot product.
- Corpus: `100` ACTIVE RETIRE fixture memories, including `14` durable anchors and `86` deterministic distractors.
- Frozen semantic cases: `5`; dense Recall@1/3/5 = `20.0%` / `40.0%` / `40.0%`; incremental top-5 recovery = `2/5`.
- Frozen semantic measurement-only would-injection recovery at the registered 0.50 cutoff: `0/5`.
- Paraphrase holdout: `15`; dense Recall@1/3/5 = `53.3%` / `93.3%` / `93.3%`; incremental top-5 recovery over FTS would-inject misses = `73.3%`.
- Lexical guardrail FTS would-inject success: `100.0%`; dense does not receive credit for these cases.
- Hard negatives: `15`; measurement-only false would-inject rate = `6.7%`; top-5 candidate rate = `100.0%`.
- Dense query latency (embedding plus in-memory cosine search, precomputed memory embeddings excluded): p50 `3.332` ms, p95 `3.850` ms, p99 `4.049` ms, mean `3.334` ms, max `4.380` ms over `350` samples.
- FTS comparison latency: p50 `2.226` ms, p95 `4.529` ms; FTS code and calibrated policy were not changed by this experiment.
- Production dense injections: `0 by design`; production dense failures/timeouts: `not applicable` because the gateway does not import this module.
- Offline hot-path state fingerprint: unchanged = `True`; event count remained `100`; ACTIVE-memory and ACTIVE-capsule ID fingerprints were unchanged.

## Interpretation

Dense retrieval is credited only for cases where calibrated FTS did not
would-inject the expected memory. Lexical guardrail queries are recorded in
`quality_results.jsonl` but are not counted as semantic wins. A dense candidate
is not itself an injection: the 0.50 measurement threshold exists only to
quantify contamination risk on this experiment's hard negatives.

The five frozen cases are a small engineering gate, not a general claim about
semantic retrieval. The paraphrase holdout and hard negatives provide the more
useful evidence for whether the observed semantic gap is repeatable.

## Decision gate and recommendation

Registered gate: at least one frozen semantic *injection* recovery at the
measurement cutoff, hard-negative false
would-inject rate at most `10.0%`, and dense query p95 at most `20.0` ms. This is an engineering screening gate, not a learned threshold.

**Recommendation:** Keep dense retrieval offline/shadow-only; its current quality, contamination, or latency does not clear the registered integration gate.

## Artifacts and reproduction

- `SUMMARY.json` — machine-readable result and gate values.
- `quality_results.jsonl` — per-query original input, constructed query, FTS candidates, dense scores, incremental recovery, and failure evidence.
- `latency_samples.jsonl` — per-query latency samples after `2` warm-up iterations.
- `CONFIG.json` — pinned corpus, model revision, threshold, and run parameters.
- `CORPUS.json` — indexed memory metadata and fixture description.
- `dense_embeddings.npz` — generated offline index, not imported by ORCHID.

The dense harness uses optional tooling only; the gateway's mandatory runtime
dependencies are unchanged. In an environment without these packages, install
`numpy`, `onnxruntime`, `tokenizers`, and `huggingface_hub` before running the
command below.

Reproduce from the repository root:

```text
python tests/cold_memory_phase2_dense_experiment.py --output artifacts\cold_memory\phase2_0_dense_experiment --corpus-size 100 --warmup 2 --iterations 10 --revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41
```

## Non-goals preserved

No vectors were added to the gateway, no RRF/fusion/reranker/graph/raw-history
fallback was added, and no ACTIVE promotion, retrieval policy, or event schema
was changed.
