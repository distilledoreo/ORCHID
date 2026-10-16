# SpecPrefill routing and evidence sufficiency experiment

This experiment lives under `experiments/specprefill`. The production gateway,
configuration defaults, memory format, and existing summarizer are unchanged.
The earlier generative prefill prototype is preserved on the separate local
`codex/prefill-evidence-selection` branch and is not an attention router.

## Phase 0 inventory and integration proposal

PRs #7–#14 were merged before this experiment. `gateway.py` ingests originals
before assembling the main-model request. `stable_context.py` preserves roles,
tool relationships, and capsule coverage. `coding.py` optionally replaces large
messages with prefix/suffix excerpts and recoverable handles. `observer.py`
maintains evidence-linked durable memory; it is not SpecPrefill. `raw_recall.py`
provides scoped FTS5 search and bounded exact character-range recovery.

Reuse SQLiteStore and RawRecall in an isolated replay. Add query-specific
selection metadata outside production capsules. Preserve every source and its
ordering; count retained text with the actual main tokenizer. Keep governing
instructions and the complete active request outside the selectable sources.
Do not wire an experimental controller into production before the controlled
experiment establishes a benefit.

The existing deterministic memory evaluation reports 65,722 estimated
three-stage input tokens versus 5,300 observer tokens on its synthetic fixture.
Those figures are historical compactor costs, not main-model TTFT or accuracy.
The separate live observer study in `live-observer-findings.md` failed to
establish superiority over rolling summaries. New paired measurements must
therefore establish the baseline; neither result is treated as a speedup here.

## Reference and adaptations

Reference: Liu et al., [Speculative Prefill](https://arxiv.org/abs/2502.02789),
especially sections 3.2.2–3.2.4 and Algorithm 1. Official source:
[Jingyu6/speculative_prefill](https://github.com/Jingyu6/speculative_prefill),
inspected at `c1d6bd323efd1dc11a2ba85bc49f52879db43c7b`.

Capture post-RoPE Q/K for the final prompt position and up to eight generated
positions. Compute softmax against the complete original prompt keys, maximum
over heads/layers, then mean over query positions. No generated relevance
ratings are substituted for attention. The additional score computation holds
only query rows, not an S-by-S attention matrix. Normal model attention still
uses SDPA.

The official implementation pools individual head/layer attention before its
maximum. Preserve that as a separate reference-order condition; the requested
aggregate-then-pool order is a distinct ablation. A 13-token zero-padded average
pool comes from the inspected reference configuration. Chunk sizes 32/64/128
are experiment settings. In this experiment eight look-ahead means eight new
positions plus the original final prompt position; the reference counts eight
total positions. Report actual row count and EOS truncation.

The local Qwen3.5-0.8B has six full-attention layers and eighteen recurrent
layers. Only the six full-attention layers provide these scores. This is a
hybrid-model adaptation, not a reproduction on the paper's Llama models.
The available llama-server chat API does not expose those Q/K rows. The
prototype therefore uses an optional, isolated Transformers runtime; no heavy
dependency is added to ORCHID's production environment.

Ordinary API forwarding re-tokenizes selected text with contiguous positions.
Original token and byte/character offsets remain in provenance, but original
noncontiguous model positions are **not restored**. No engine-level position
restoration condition is claimed.

## Evaluation contract

Generate 128 cases across eight categories with disjoint development and
held-out seeds. Seal the corpus before evaluating held-out cases. Answer keys
and supporting spans are scoring-only data and never controller/evaluator
inputs. Report exact answer correctness, evidence recall, false-sufficient and
false-retrieve rates, retrieval/fallback costs, support-model tokens, actual
main-tokenizer counts, paired TTFT, and total latency separately.

The deterministic coverage gate and the untrained three-label next-token gate
are separate baselines. Neither has calibrated confidence. Limit retrieval to
two rounds using existing FTS5 and exact range reads. Unresolved insufficiency
falls back to the full source; failures cannot silently forward an inadequate
reduced prompt. Full fallback can improve recall while eliminating savings,
so always report its rate.

Attention extraction overhead, full speculator work, evaluator time, retrieval
time, main-only TTFT, and complete sequential latency must remain distinguishable.
Do not infer end-task quality from excerpt retention or a net speedup from a
token ratio. The proposed 95% evidence recall, <1% false sufficiency, 30% input
reduction, <=2-point quality loss, and positive net TTFT are experimental decision
criteria, not established performance.
