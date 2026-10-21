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

## Retention-rate follow-up

The default remains 100% and 50%. To compare 10% and 30% on exactly the same
frozen prompts, add these arguments to the run command, with a new output directory:

```text
--cases-from data/strata-specprefill-smoke/protocol.json --keep-rates 1 .5 .3 .1
```

The source protocol's hash and the current template/tokenizer must reproduce
every original ID. Include the 100% equivalence control and distinct rates in
`(0, 1]`. All rates use the same scoring and chunk policy; there is no special
preservation of the query, answer span, or template suffix. Whole-chunk rounding
means the actual retained-token percentage can differ from the requested rate.

With four cases and four keep rates, the follow-up has 24 measured requests:
four main-only full-prefill requests and 20 co-located requests covering full,
100%, 50%, 30%, and 10%. Six additional warmups are excluded. Each timed selected
request computes a fresh student trace. The report derives its expected arms,
case count, and completion count from the frozen protocol and preserves support
for the original 16-request run. Keep the original results and the follow-up in
separate artifact directories; compare new rates primarily with their fresh
controls, rather than treating different runs as simultaneous measurements.

## Multi-turn caching experiment

`cache_experiment.py` implements four explicit phases on identical frozen target
IDs. It uses Strata's native `StrataEngine` transport and measures the host clock
at the first yielded token. This excludes HTTP/template overhead consistently
across arms; it includes live student work and target generation in pipeline
arms. Both models are resident before measured pipeline requests. Model loading
is excluded, and no stage-sum latency estimate is used.

1. `prepare` freezes two scripted conversations with approximately 500, 1,000,
   and 2,000 added tokens between probes.
2. `control` runs ordinary target caching off/on, with no student resident.
3. `student` compares full-prefill, uncached chunked, and cached chunked student
   scores, draft IDs, selections, and latency. `student-pipeline` measures both
   chunked variants live with target caching disabled.
4. `both-pipeline` compares full-context target caching with both models resident
   against cached student + stable selected target caching. It also compares each
   selected cached response with a cold replay of the exact IDs/positions, and
   tests identical IDs with changed original positions.

Example, using the corresponding local Python environments:

```bash
python -m experiments.strata_specprefill.cache_experiment prepare
python -m experiments.strata_specprefill.cache_experiment control --config /path/to/isolated-config.json
python -m experiments.strata_specprefill.cache_experiment student --student /path/to/Qwen3.5-0.8B
python -m experiments.strata_specprefill.cache_experiment student-pipeline --student /path/to/Qwen3.5-0.8B
python -m experiments.strata_specprefill.cache_experiment both-pipeline --student /path/to/Qwen3.5-0.8B
```

Specify `--strata-root`, `--target-tokenizer`, `--config`, and `--output` for a
nondefault installation. Apply the selected-prefix-cache integration patch and
build its executable before the final phase. Run phases sequentially on the GPU.
Existing result files are not overwritten.

The student cache reuses only an exact, strictly extending prompt prefix. A
changed, repeated, or shortened prompt resets it. The cached prompt state is
cloned for lookahead so speculative tokens cannot contaminate the next turn's
hybrid recurrent state. This first implementation does not retain multiple
branches or partial-prefix checkpoints. Scores may differ numerically when
prefill shapes change; exact selection/draft comparisons are reported explicitly.

These are bounded synthetic recall conversations with fixed assistant histories,
not a live agent loop or repository-edit benchmark. They test cache behavior and
latency, not general coding quality.

## Append-only delivery and addressable omission

`omission_archive.py` archives immutable source revisions with exact UTF-8 text,
byte/character/token offsets, original student coordinates and SHA-256 provenance.
Source IDs (`S1`) and chunk handles (`S1:3-8`) resolve only through an in-memory
lookup table. Sources and grouped omissions survive disk reload; a disk archive
is explicitly **not** evidence that any recurrent model cache remains live.

`append_experiment.py` appends newly selected or recovered exact source text with
source/version metadata at fresh sequential target positions. It never inserts
old positions into the cached target stream. Previously delivered payload is not
appended twice. The native API still serializes the full prefix IDs to identify
cache matches; positive native reuse means that prefix is not re-prefilled.
Cache loss requires replay of the saved complete target stream, recorded separately
from semantic duplicate delivery. No compaction or target-history rewriting occurs.

The persistent student sees the original growing conversation, including actual
model replies. The frozen adaptive policy is unchanged. The three automatic
variants differ only in visible omission references: none, individual chunks,
and grouped ranges. References are emitted once, upon first source encounter.
Superseded versions remain addressable and carry historical/current metadata.

The optional `fetch-grouped` variant withholds automatic historical recovery and
lets the model issue `fetch_context(S1)` or a bounded chunk range. The executor
returns only missing chunks; it cannot read arbitrary filesystem paths. At most
two lookups are allowed. Timing includes the fetch-generation call, lookup,
response delivery and the final-answer call. Unsupported or unresolved requests
remain failures in the reported results.

Run standalone and colocated epochs in separate processes, sequentially:

```bash
python -m experiments.strata_specprefill.append_experiment prepare
python -m experiments.strata_specprefill.append_experiment run --arms full-alone
python -m experiments.strata_specprefill.append_experiment run --student /path/to/Qwen3.5-0.8B
python -m experiments.strata_specprefill.append_report \
  --input data/strata_specprefill/append-v1 \
  --output artifacts/strata-specprefill/append-v1
```

Use the corresponding existing local runtimes. `--strata-root`, `--config`,
`--target-tokenizer`, `--output`, `--arms`, and `--cases` allow explicit isolated
runs. Existing conversation outputs are not overwritten. The frozen set reuses
previous incoming content and adds fresh delayed-recovery/version probes. Actual
responses make these stateful trials different from fixed cumulative-prompt
replays. All changes remain experimental; production Strata is unchanged.

Identical repeated file revisions reuse their source/chunk handles while recording
all original student-stream occurrences. Changed content under the same revision
is rejected. A historical revision encountered later stays historical; current
version is independent of delivery order.

The report retains both `fetch-grouped` (the initial instruction) and
`fetch-explicit` (a clarified tool-call exception). The clarified prompt is an
exploratory repair on the same frozen cases, not an additional fresh held-out
validation. Runtime source snapshots and case hashes remain private with the
results, while public artifacts contain statistics and provenance hashes.

### Permanent-omission negative control

`append-no-recovery` freezes the omitted chunk handles after each source's first
selection and permanently prevents their delivery. Repeated appearances of that
revision cannot bypass the ban; a new revision receives a new first selection.
The current question is always retained, both caches stay enabled, and there are
no omission placeholders or retrieval tools. Selection never uses answer labels.

Create a fresh output directory and copy the existing frozen `cases.json` into it
(the measured run also saves its protocol and runtime sources). Then run:

```bash
python -m experiments.strata_specprefill.append_experiment run \
  --student /path/to/Qwen3.5-0.8B \
  --output data/strata_specprefill/no-recovery-v1 \
  --arms append-none append-no-recovery
python -m experiments.strata_specprefill.no_recovery_report
```

The report audits exact initial prompt/selection equality, permanent omission
ledgers, full current questions, append-only prompt hashes, and delivered source
bytes. It compares the same recovery-needed turns across arms, separately showing
repeat questions that remain unanswered only in the control. Actual replies feed
each arm's later student history, so later selections may diverge. Existing cases
provide an ablation, not a fresh held-out benchmark. Results are in
`artifacts/strata-specprefill/no-recovery-v1/REPORT.md`.

### Matched provenance ablation

`provenance_experiment.py` compares bare content, minimal provenance (file, version,
current/historical status), and the existing detailed provenance. Generic EVIDENCE
boundaries remain in all three formats. Payload bytes and delivery order are
frozen before inference; all formats keep their actual target replies and reuse
normal sequential target positions. No omission placeholders, separate revision
notices, or model-directed retrieval calls are enabled.

The frozen set combines the four original `append-none` delivery schedules with
four new controlled revision/source-conflict conversations. New schedules select
symbol boundaries independently of numeric answer values. They deliberately
recover historical revisions after newer ones and include repeat/current-version
questions. These controlled cases isolate provenance usefulness, rather than
measure the frozen student's retrieval quality or production error rates.

```bash
python -m experiments.strata_specprefill.provenance_experiment prepare
python -m experiments.strata_specprefill.provenance_experiment run \
  --student /path/to/Qwen3.5-0.8B
python -m experiments.strata_specprefill.provenance_report
```

Use the existing native/tokenizer runtime for preparation/reporting and the
student-capable runtime for inference. Preparation refuses to overwrite frozen
inputs. Format order rotates across conversations. The student remains resident
but its selection is replayed, so reported delivery/target TTFT excludes selector
planning and cannot be compared directly with earlier full-pipeline TTFT.
The report checks exact evidence payload/order, reconstructs every target prompt,
verifies source byte spans, and independently confirms that every new probe's
required full function definition reached the target.

## Addressable compaction prototype

[COMPACTION.md](COMPACTION.md) documents the opt-in `CompactingSession`, native
adapters, and isolated script runner. Summary items carry compact external
manifest handles. Indexed symbol/text retrieval now shortlists original chunks
before student rescoring, with optional local semantic embeddings and an explicit
finite-scan fallback. This is separate from the existing measured append-only
arms and is not enabled in the production gateway.

The opt-in [native gateway](../../docs/specprefill-gateway.md) adds automatic
compaction admission, text chat/SSE, durable retries, and scoped session ownership.
Remaining work is tracked in the [production plan](../../docs/specprefill-production-plan.md).
