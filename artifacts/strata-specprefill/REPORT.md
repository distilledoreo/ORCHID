# SpecPrefill on the existing Strata model: warmed functional smoke

The exact-token SpecPrefill path ran successfully on the existing Swift IQ3_S
model and P100. Selecting roughly half the target prompt reduced observed mean
pipeline TTFT from **5.99 s to 4.64 s**, including fresh student inference and
selection. All four synthetic lookup cases answered correctly. This small run
establishes a working method adaptation and a promising measurement, not a
general quality or speedup result.

## Observed results

Four frozen cases, one measurement per arm/case, 16 measured target requests,
four excluded warmups, zero model API errors. Generation was greedy, at most
24 target output IDs. Each selected request performed its own fresh student
inference; traces were not reused between arms.

| Variant | Mean pipeline TTFT | Mean target/server TTFT | Mean student + selection | Correct |
|---|---:|---:|---:|---:|
| Full, student absent | 5.989 s | 5.988 s | — | 4/4 |
| Full, student resident | 5.799 s | 5.798 s | — | 4/4 |
| Student, retain 100% | 6.167 s | 5.775 s | 0.389 s | 4/4 |
| Student, retain nominal 50% | **4.636 s** | 4.245 s | 0.388 s | 4/4 |

The selected arm's mean pipeline TTFT was **22.6% lower** than standalone full
prefill and **20.1% lower** than co-located full prefill. All four selected
requests were faster than their standalone controls, by 18.1–28.6%. These are
descriptive ratios of the observed means, with no confidence or significance
claim. Standalone full versus co-located full also differed; resource epochs
were sequential, so that difference cannot be attributed solely to cache size.

| Case | Original / retained IDs | Full alone | Full co-located | Retain 100% | Retain 50% |
|---|---:|---:|---:|---:|---:|
| Short, evidence at 20% | 504 / 248 | 4.268 s | 4.405 s | 4.684 s | 3.494 s |
| Short, evidence at 80% | 504 / 248 | 4.271 s | 4.413 s | 4.675 s | 3.446 s |
| Longer, evidence at 20% | 1528 / 760 | 8.204 s | 7.190 s | 7.690 s | 5.854 s |
| Longer, evidence at 80% | 1528 / 760 | 7.213 s | 7.188 s | 7.618 s | 5.749 s |

Actual retention was **49.61% of original target IDs**, because whole-chunk
selection includes a short final chunk. This is a 50.39% reduction in tokens
read by the target. The student still reads the complete original prompt;
this is not a reduction in summed token reads across both models.

## What was verified

- Student and target received the same original prompt ID sequence. All 248,044
  base-vocabulary entries and 26 student special tokens share IDs; unsupported
  target-only IDs are rejected. No separate student chat template or text
  reconstruction was used.
- The student captured post-RoPE Q/K from layers 3, 7, 11, 15, 19, and 23. It
  followed reference softmax/cast, pool-before-max, query-row averaging,
  32-token chunk means, ceil retention, and sorted original indices.
- Eight look-ahead rows includes the last prompt query. End tokens shortened
  the selected arms to 5–7 actual query rows. Generated keys were excluded from
  importance scoring; generated draft IDs did not enter the target prompt.
- Strata's GPU-table acknowledgement matched the selected original IDs and
  positions for every selected request. Next decode positions used original
  lengths 504/1528, rather than retained lengths 248/760.
- All four 100%-retention outputs matched the corresponding ordinary
  co-located outputs token for token.
- The server's first-token monotonic timestamp and the local client's start
  before prompt preparation measure actual pipeline TTFT. This includes fresh
  template/tokenization, student work, selection, request handling, GPU-table
  verification, and target prefill. It excludes loading and warmups and measures
  the first raw target token, not necessarily the first visible answer token.

## Hardware, controls, and limits

Both models used the same Tesla P100 16GB (driver 580.178.04). Target: existing
Swift IQ3_S GGUF, Strata 0.1.43 built with CUDA 12.9, int8 KV, MTP=4, 4096 context.
Student: local Qwen3.5-0.8B FP16, PyTorch 2.7.1+cu126 / Transformers 5.19.0.
The student used reference convolution and gated-linear-attention fallbacks.
Its mean measured selection cost was 0.388 s after warmup; its **first excluded
selector call cost 4.06 s**. Cold-start performance is not established by the
warmed means above.

Both target epochs disabled prompt caching, route-tail skipping, and CPU prefill
sharing, and fixed PCIe fraction at one for controlled numerical behavior.
Standalone full used a 700-MiB reserve, with 5,014 expert-cache slots / 9.54 GiB.
Co-location used a 4096-MiB reserve, with 3,253 slots / 6.23 GiB. Full prefill
therefore received a separate student-free resource baseline. These are
experimental controls; production defaults were not changed.

This is an adaptation of the
[pinned reference worker](https://github.com/Jingyu6/speculative_prefill/blob/c1d6bd323efd1dc11a2ba85bc49f52879db43c7b/speculative_prefill/vllm_patch/worker/look_ahead_spec_worker.py),
not the paper's eight-H100/H200 dense Llama/vLLM experiment. Hybrid Qwen recurrent
state, QSA storage, and PLE history advance over retained tokens in the target;
original rotary positions do not restore omitted-token computation. Four easy
lookup cases at modest context lengths cannot establish robustness on reasoning,
multi-hop evidence, long contexts, different seeds, or production settings.
No ORCHID retrieval, provenance layer, or evidence gate was involved.

## Verification and artifacts

Offline suite: **415 passed, 10 skipped**, including optional Torch skips.
The student environment ran all **13 focused tests successfully**. The updated
Strata interface suite passed nine tests; source patch application/hash checks
and Ruff passed. All owned servers stopped, port 18586 closed, P100 memory
returned to zero, and the existing service was untouched.

Machine-readable aggregates are in `smoke-summary.json`. Exact frozen prompts,
IDs, selected positions, responses, warmups, resource configs, and logs remain
in the ignored local `data/strata_specprefill/smoke-v1` directory. See
`experiments/strata_specprefill/README.md` for the reproducible protocol and CLI.
The previous gateway pilot remains separate and stopped.
