# SpecPrefill-first reproduction plan

## Checkpoint and scope

The gateway experiment is stopped and retained as an **exploratory engineering
pilot**, not a final result or a faithful engine-level SpecPrefill reproduction.
Its corrected run reached 42 complete cases out of 128: 252 main requests and
zero API errors. The earlier 39-case snapshot advanced before the clean stop.
No records were deleted to force the count back to 39. Public pilot artifacts
are in `artifacts/specprefill/controlled-v4`; complete private logs, databases,
earlier attempts, and excluded timing runs remain under
`data/specprefill_repurpose`. The completed descriptive quality replay is also
preserved, without promoting it into a final viability finding.

Both orchestration jobs were stopped. The main/student P100 allocations are
released, and experiment ports 18583/18584 are closed. No production defaults,
model weights, or the user's existing service were changed. PR #15 stays draft.
No new long benchmark is authorized by this plan.

## What the reference actually does

Reference repository commit: `c1d6bd323efd1dc11a2ba85bc49f52879db43c7b`.
Its requirements pin PyTorch 2.4.0, Transformers 4.50.2, vLLM 0.6.3.post1,
and lm_eval 0.4.5. Use a separate environment, not the pilot's Transformers 5.19
runtime or ORCHID's production dependencies.

The reference worker indexes original prompt token IDs with the selected
indices and stores those same indices as `_position_ids`. Its patched model
input builder passes those positions into the target model. The worker retains
the original prompt length for subsequent decoding positions. This is an
engine path, not text clipping followed by tokenization.

For a five-token prompt, retaining indices `[0, 2, 4]` must mean:

```text
target input IDs = original IDs[[0, 2, 4]]
target positions = [0, 2, 4]
next generated position = 5, then 6, ...
```

Reproduce the upstream softmax, pool-before-max ordering, head/layer maximum,
query-row mean, 32-token chunk mean, ceil-based top-chunk selection, sorted
original order, and 13-token average pooling. Follow the upstream look-ahead
count exactly and record the actual rows; do not substitute the pilot's
last-prompt-plus-eight-new-positions interpretation. Start with the upstream
raw / Full / Full-LAH variants and keep configurations, plus a 100% control.
No cross-tokenizer character weighting, rewritten passages, ORCHID inventory,
retrieval, evidence gate, or source-header insertion belongs in this baseline.

Sources: [official worker](https://github.com/Jingyu6/speculative_prefill/blob/c1d6bd323efd1dc11a2ba85bc49f52879db43c7b/speculative_prefill/vllm_patch/worker/look_ahead_spec_worker.py),
[position input builder](https://github.com/Jingyu6/speculative_prefill/blob/c1d6bd323efd1dc11a2ba85bc49f52879db43c7b/speculative_prefill/vllm_patch/data/input_builder.py),
[decode context tracking](https://github.com/Jingyu6/speculative_prefill/blob/c1d6bd323efd1dc11a2ba85bc49f52879db43c7b/speculative_prefill/vllm_patch/worker/spec_prefill_worker.py).

## Concrete local blockers and viable setups

| Route | Status and limits |
|---|---|
| Official patched vLLM on P100 | Blocked: observed compute capability 6.0; vLLM 0.6.3 requires at least 7.0. FlashAttention-2's supported CUDA GPU families start at Ampere. |
| Official AMD backend on RX 580 | Not a supported replacement: Polaris is outside the documented FlashAttention-2 AMD GPU families. The existing local llama.cpp is Vulkan-enabled and CUDA-disabled. |
| Existing Strata text API | Does not provide the required selected-token-ID/original-position interface used by the reference. Re-tokenized text is not an acceptable substitute. |
| Local llama.cpp C API | A possible functional port: `llama_batch` exposes both `token` and `pos`. Those fields were verified in the installed header. Sparse-prefill cache semantics, later decode positions, and draft Q/K extraction are **not yet validated**; this is not the upstream implementation. |

The paper's actual setup is an 8B Llama-3.1 BF16 speculator with a 70B BF16
or 405B FP8 Llama-3.1 target, TP=8 on eight H100/H200 GPUs. A 70B BF16 target
alone needs roughly 140 GB just for weights, before the draft, KV cache, and
runtime buffers. That is not a local reproduction on a 16-GB P100 plus an
8-GB RX 580 and 64 GB system RAM.

The smallest practical **proposed scaled setup** is Llama-3.2-1B-Instruct draft
plus Llama-3.1-8B-Instruct target, shared token IDs, using the pinned vLLM patch
on one supported Ampere GPU. A 24-GB RTX 3090 is a short-context allocation-probe
candidate; fit is not yet measured. An A100 40-GB provides safer headroom.
Start at 4K context, batch one, then increase only after measuring peak memory.
Compatible model weights/access must also be verified before downloading or
running them. This differs from the paper in model sizes, TP, hardware, and
context/batch scale, so its latency must never be presented as matching the
published results.

Without new GPU access, a CPU/Vulkan port with a compatible dense Llama pair
could test the mechanism locally. It requires a bounded position/cache probe
and draft-attention adapter first. It can establish functional behavior, not
the reference vLLM latency. Do not expand this into an engine-porting project
without resolving the setup choice.

Sources: [paper §4.1 and appendices C/D](https://arxiv.org/abs/2502.02789),
[official example model pair](https://github.com/Jingyu6/speculative_prefill#example-usage),
[vLLM 0.6.3 requirements](https://docs.vllm.ai/en/v0.6.3/getting_started/installation.html),
[FlashAttention GPU support](https://github.com/Dao-AILab/flash-attention#installation-and-features),
[llama.cpp batch API](https://github.com/ggml-org/llama.cpp/blob/master/include/llama.h).

## Validation sequence before another long benchmark

1. Pin engine/model/tokenizer revisions and select an available compatible
   setup. Check allocation with a short prompt; no latency claim from this check.
2. Instrument the target boundary to record the actual token IDs, original
   positions, original prompt length, and first decode position. Assert sorted
   unique retained indices and exact ID correspondence. Metadata alone is not
   proof that the model received these positions.
3. With 100% retention, compare target logits and generation against ordinary
   full prefill using the same dtype/backend and declared numeric tolerance.
   Expect extra draft latency, not identical TTFT. Then exercise a sparse
   noncontiguous prompt and verify cached decode positions continue at the
   original prompt length.
4. Freeze a small official LongBench/RULER subset, exact prompts/chat templates,
   input token IDs, output budgets, seeds, and scoring scripts. Run full prefill
   versus upstream raw / Full / Full-LAH variants on the same cases. Use eager
   execution and disable chunked prefill as in the reference. Preserve the
   official chunk/keep policy rather than the pilot's main-token greedy budget.
5. Measure draft tokens/work, selection/QK costs, target prefill, main TTFT,
   **complete** TTFT from before tokenization/preprocessing, total latency,
   quality, peak memory, and cache/resource allocation. Include the main-only
   full baseline's actual memory advantage; do not handicap it with a resident
   draft and call that the deployment comparison. Record all deviations from
   the published model/hardware/context/batch protocol.

Only after this standalone baseline passes the functional checks and has a
credible measured comparison should ORCHID be added, one frozen stage at a time:

1. **Lossless source/provenance:** persist originals and map bytes/tokens without
   changing the target IDs/positions. Verify baseline equivalence and ingestion
   overhead on the frozen cases.
2. **Retrieval:** recover exact omitted IDs/ranges and their original positions;
   measure quality, added tokens, and latency against that same baseline.
3. **Evidence gate:** evaluate decisions and recovery separately, then compare
   the full pipeline. Keep uncertainty uncalibrated until validated.

Current outcome: the official local reproduction is blocked by GPU/engine
support. The next action is setup selection and a short position-preservation
probe, not another 128-case gateway benchmark.
