# Strata SpecPrefill smoke test

This standalone experiment uses the existing Qwen3.5-0.8B student and Swift IQ3_S
target on one P100. It requires the opt-in Strata patch in `integrations/strata`.
It does not invoke ORCHID retrieval, evidence gates, source rewriting, or memory
management. Production configuration is not changed.

## Method

Render and tokenize the target's actual chat template once to freeze the cases.
Every timed request renders/tokenizes it afresh and checks the IDs against the
frozen input. The student receives exactly those IDs, without a different prompt
or chat template. Both tokenizer vocabulary entries and special-token IDs must
agree; unsupported IDs cause an error.

Capture post-RoPE Q/K from the student's six full-attention layers. The remaining
18 recurrent layers still participate in inference, but do not supply ordinary
attention matrices. Score only original prompt keys. Follow the pinned upstream
SpecPrefill operations:

1. Model-dtype QK matmul divided by square root of head dimension.
2. FP32 softmax, cast back to model dtype.
3. Average pooling with kernel 13 and zero padding, before aggregation.
4. Maximum over attention layers/heads, then mean over query rows.
5. Mean per 32-token chunk; retain `ceil(chunk_count * ratio)` top chunks,
   returning all their token IDs in original order.

Look-ahead eight means **at most eight total query rows**: the last prompt query
and up to seven subsequent generated-input queries. Stop early on the student's
end tokens. This corrects the older pilot's last-prompt-plus-eight interpretation.
Sampled draft IDs are never substituted into the target's prompt. Retention of
100% must reproduce the ordinary target generation.

The upstream reference is pinned at
[`c1d6bd323efd1dc11a2ba85bc49f52879db43c7b`](https://github.com/Jingyu6/speculative_prefill/blob/c1d6bd323efd1dc11a2ba85bc49f52879db43c7b/speculative_prefill/vllm_patch/worker/look_ahead_spec_worker.py).
This is a method adaptation to hybrid Qwen models and Strata, not an exact
reproduction of the dense Llama/vLLM experiments.

## Scope and timing

Four synthetic record-lookup cases use two prompt lengths (about 500/1,500 IDs)
and two evidence locations. The private protocol saves exact inputs and hashes
before inference. There are 16 measured target requests, plus four excluded
warmups:

- Four standalone full-prefill requests, without student weights resident and
  with a 700-MiB main-engine VRAM reserve.
- Twelve co-located requests: full prefill, fresh student selection at 100%, and
  fresh student selection at 50%, on the same four cases. These use a 4096-MiB
  reserve for the student. Arm order is shuffled with a fixed seed.

The two resource epochs otherwise use identical engine arguments/weights. Both
use int8 KV, MTP=4, 4096 context, prompt cache disabled, CPU prefill sharing off,
route-tail skipping off, and PCIe fraction fixed at one. These are diagnostic
controls, not recommended production settings. The student uses FP16 and the
installed reference convolution/linear-attention fallbacks.

The local server returns its first-token `time.perf_counter()` timestamp. Since
client and server run on the same host, subtracting the client timestamp before
prompt preparation measures the pipeline to the first emitted target token,
including HTTP handling, fresh tokenization, student inference, pooling,
selection, GPU position verification, and target prefill. Model loading and
warmups are excluded. This is not time to first visible answer after hiding
control/reasoning tokens. The endpoint returns the full response later; the
first-token timestamp is measured inside the server, not inferred from response
completion. Do not use this clock method across hosts.

Exact-code quality checks remove control markers and require the expected code
without additional code-shaped words. Four cases and one measurement each do
not support a general quality or latency claim. Sequential resource epochs,
cache warmness, model sizes, runtime, precision, and hardware differ from the
paper's protocol. Report all student overhead and compare against both full
baselines; a reduction in target input tokens alone is not a speedup.

## Running

Use a separate environment with the locally validated PyTorch 2.7.1+cu126 and
Transformers 5.19.0 runtime; model calls are not part of normal tests. Models must
already exist locally. Do not install packages into ORCHID's production runtime.
Both explicit configs must use the same patched Strata executable, tokenizer,
model, MTP, and all non-reserve engine options. The runner only launches on
loopback and refuses an occupied port. It owns and stops its subprocess groups.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m experiments.strata_specprefill.run \
  --strata-root /path/to/Strata \
  --server-python /path/to/Strata/.venv/bin/python \
  --student /path/to/Qwen3.5-0.8B \
  --alone-config /private/alone-config.json \
  --colocated-config /private/colocated-config.json \
  --output data/strata-specprefill-smoke --port 18586
```

Keep raw responses, prompts, machine paths and logs in the ignored output
directory. The report exporter refuses an incomplete run or a changed protocol
and exports only aggregate measurements:

```bash
python -m experiments.strata_specprefill.report data/strata-specprefill-smoke \
  --output artifacts/strata-specprefill/smoke-summary.json
```

The ordinary offline suite tests tokenizer identity and request/clock invariants.
The scoring tests require PyTorch and otherwise skip; run them in the student
environment before live inference. They verify pool-before-max order, query-row
averaging, mean-based chunk ranking, ceil retention, and partial chunks.
