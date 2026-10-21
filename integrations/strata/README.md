# Selected-token prefill in Strata

This opt-in local Strata modification accepts exact token IDs and their original
rotary positions. It uses the existing Swift IQ3_S model, P100 CUDA-12 engine,
int8 KV, and MTP=4. No new model, inference engine, or GPU is required.
The selected-token input is still an approximation to the full prompt.

## Applying and building

`selected-prefill.patch` includes the engine, raw HTTP endpoint, and independent
Python/C++ tests. `source-manifest.json` records the exact before/after hashes
of the local sources. The installed Strata directory has no Git checkout; the
portable patch is the reviewable source artifact. Original files and private
probe logs are preserved in ORCHID's ignored `data/strata_selected_prefill`.

From a matching Strata source directory, check the original hashes before applying:

```bash
git apply --check /path/to/ORCHID/integrations/strata/selected-prefill.patch
git apply /path/to/ORCHID/integrations/strata/selected-prefill.patch
cmake --build build-cuda12 --target strata -j 4
python -m unittest serve.test_selected_prefill serve.test_server
g++ -std=c++17 -I include src/program/selected_prefill_test.cpp -o /tmp/selected_prefill_test
/tmp/selected_prefill_test
```

The already modified local sources must not have the patch applied twice.
The normal installed `engine-cuda12/strata` binary and existing service are not
replaced. Set the isolated experiment configuration's `exe` to the rebuilt
`build-cuda12/strata`. Append these engine arguments:

```text
--selected-prefill --prompt-cache 0
```

This first implementation requires one device, no vision, no continuous batching,
and no layer/spec split. The engine rejects incompatible startup settings.
The server must remain on loopback, or use Strata's existing API-key protection.
CUDA was built and exercised on the P100. HIP has not been built or tested;
its compiler is unavailable locally. The separate SYCL engine is not modified.

## Request and response

`POST /v1/strata/generate` is a non-streaming raw-token endpoint. It uses the
server's existing authorization and request-body handling. No chat template,
text tokenization, output parsing, tool execution, retrieval, or evidence gate
runs on this path. Token IDs must come from this model's tokenizer, with all
required chat/control tokens already included.

```json
{
  "token_ids": [100, 200, 300],
  "position_ids": [0, 2, 4],
  "original_prompt_length": 7,
  "max_tokens": 8,
  "sampling": {"temperature": 0}
}
```

The IDs above illustrate the wire format, not a meaningful prompt. `position_ids`
must be nonnegative, strictly increasing, and have exactly one entry per token.
Every position must be below `original_prompt_length`. The **original** length
plus `max_tokens` and the engine's eight-token safety margin must fit context.
Missing paired fields, booleans in integer fields, unknown fields, invalid IDs,
and out-of-range requests are refused rather than silently renumbered.

For an ordinary full-prefill control on the same isolated engine, omit both
`position_ids` and `original_prompt_length`; send the complete original IDs.
This sends ordinary `GEN` without any selected-position metadata.

The response contains generated `token_ids`, retained-token usage, the original
prompt length, and a `prefill` audit. Explicit requests report
`device_table_verified: true` only after the engine confirms the uploaded table
and the server matches its acknowledgement to the request. Ordinary controls
report `mode: implicit_contiguous` and `device_table_verified: false`.
Raw output includes native stop/control token IDs. Supply a positive finite
`max_tokens`; unlimited generation and streaming are not part of this interface.

## Engine semantics

For retained positions `[0, 2, 4]` from an original length of seven:

```text
compact KV cell: 0  1  2  3  4  5 ...
rotary position: 0  2  4  7  8  9 ...
```

All three rotary axes receive the original text position. The table is installed
before CUDA graph capture, and its contents are uploaded between requests.
Existing prefill, QSA/indexer, verification, and MTP rotary kernels already use
this table. The engine reads the device table back and compares it before
acknowledging the request. Generation uses successive compact cells whose table
entries continue at the original prompt length. A following ordinary request
restores the identity table. No position metadata is serialized into prompt text.

KV cells and causal ordering stay compact. Recurrent state, QSA pooling/index
storage, and PLE/ngram history advance over retained tokens. Omitted tokens do
not acquire synthetic hidden states. This interface provides original rotary
coordinates; it does not restore full-prompt computation or establish that this
model matches the dense Llama models in the SpecPrefill paper.

In the original selected-prefill mode, prompt and conversation cache reuse is disabled,
including full-prefill controls, to prevent state reuse across position layouts.
Without the new flag, no new table is allocated for ordinary text; existing
requests retain their previous protocol. The patch does not change kernels,
weights, model defaults, or ORCHID's production routing.

See `validation.json` for observed functional checks. These are not latency or
quality benchmark results. GPU-table readback is diagnostic overhead included in
this first interface; timing from these probes is not an optimization claim.

## Repeating the bounded live check

Use an isolated server and a fresh output/logits prefix. For the numerical
correctness diagnostic only, set `STRATA_ROUTE_TAIL_SKIP=0`,
`STRATA_PREFILL_CPU_SHARE=0`, and append `--pcie-frac 1` to its engine args.
These controls keep the same weights, quantization, P100, and MTP setting;
they change expert execution/approximation settings. They are not deployment
recommendations. Default-runtime ordinary repeats exhibited logit variation,
so matching generated IDs alone is insufficient evidence of numerical parity.

Set `STRATA_DUMP_FIRST_LOGITS=/absolute/private/first-logits` before starting
that server. Strata's existing diagnostic writes one binary float32 vocabulary
row per request, with suffixes `.0`, `.1`, and so on. Do not send earlier
inference requests to this diagnostic instance. Then run:

```bash
python integrations/strata/probe.py \
  --strata-root /path/to/Strata \
  --tokenizer-dir /path/to/Swift-pack/tokenizer \
  --url http://127.0.0.1:18585 \
  --out data/strata-probe \
  --logits-prefix /absolute/private/first-logits
```

This sends five short generation requests (eight output tokens each) and three
invalid requests. It compares ordinary full input, explicit full retention,
sparse original positions, compact-position sparse control, and ordinary full
input again after sparse requests. The same retained IDs are used for the two
sparse variants. The full-retention and reset comparisons use a declared
`1e-5` maximum absolute logit tolerance; the sparse comparison must change the
actual logits. Private responses and numeric diagnostics remain under `--out`.
The script preserves its numeric summary even if the tolerance check fails.
The endpoint's GPU readback is separately checked against the requested map.

## Observed validation

The controlled check used the existing Swift IQ3_S weights on the P100, CUDA
12.9, int8 KV, MTP=4, and 4096 context. One synthetic prompt contained 305 IDs;
the sparse version retained 171. Each valid request generated at most eight IDs.

- Full retention versus ordinary prefill: all 248,320 first-token logits were
  bit-identical, and generated IDs matched.
- Ordinary prefill after sparse requests: logits and generated IDs matched the
  initial ordinary request, verifying table reset.
- Sparse original versus compact positions, same retained IDs: logits changed
  (maximum absolute difference 1.4906144; RMSE 0.2575726). Generated IDs happened
  to match on this prompt; output text alone would have missed the difference.
- Device-table verification passed; the sparse request's next decode coordinate
  was 305, rather than retained length 171. Three invalid HTTP requests returned
  400; four incompatible engine configurations were rejected before loading.
- CUDA build passed. Strata server tests: 305 passed, including nine interface
  tests. The standalone C++ position test passed. ORCHID regression suite on this standalone
  master-based branch: 407 passed, five skipped (the earlier experiment-branch
  regression also passed: 422 passed, five skipped). Probe lint and patch/hash checks passed.

With the default approximation/expert-execution settings, generated IDs matched,
but ordinary repeats already had different logits (maximum difference 0.6742).
Numerical parity is therefore established only for the stated controlled
configuration. All failed diagnostic attempts and raw outputs are preserved
privately. No benchmark or quality conclusion follows from this smoke test.

### Experimental prefix caching for selected positions

`selected-prefix-cache.patch` applies **after** `selected-prefill.patch`.
`prefix-cache-manifest.json` binds the four changed files to their exact before
and after hashes. This extension is opt-in:

```text
--selected-prefill --selected-prefix-cache --prompt-cache 6 --conversation-cache-mib 0
```

Without the new flag, the original selected-prefill mode still requires
`--prompt-cache 0`. Ordinary Strata caching keeps its existing default behavior.

The experimental cache compares both token IDs and all three rotary coordinates.
A checkpoint can resume only before the first changed coordinate. Token matches
alone are insufficient. Sparse/contiguous transitions force a cold start. Parked
conversation snapshots, session SAVE/RESTORE, batching, vision, and device splits are excluded here.

The ORCHID `StableSelection` experiment preserves previously selected positions
and unions newly selected positions in original order. A newly relevant earlier
chunk is inserted at its original position and invalidates the affected suffix;
it is never appended out of order to an existing recurrent state. Retention can
grow above 30%, and backfill can eliminate most cache savings. These are measured
costs, not hidden assumptions.

CUDA is the validated backend for this extension. HIP and SYCL builds have not
been validated on this host. The installed production executable is not replaced.
