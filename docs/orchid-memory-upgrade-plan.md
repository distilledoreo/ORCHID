# ORCHID memory upgrade: revised implementation plan

Revised 2026-10-12 after comparing the attached upgrade brief with the local
`protocol-hardened-continuation` branch and its uncommitted work.

## Starting point and scope

The local branch is at `f7672868773f3381190b6851f780b648bb1e325b`, 53 commits
ahead of public `master` (`f1102024828a4eb4660a66a293285fe8a82c95df`). There
were 30 modified tracked files when inspected, plus untracked implementation,
tests, research notes, and benchmark artifacts. Starting the upgrade from
public master alone would discard important foundations. Use the local branch
and reviewed pending runtime fixes as the upgrade baseline; preserve unrelated
benchmark work and all existing artifacts.

The new upgrade brief authorizes architectural changes beyond the earlier
frozen viability experiment. Keep that experiment's manifests, thresholds,
results, and legacy engine intact. Give the upgrade its own configuration and
evaluation manifest rather than resealing historical evidence.

The clean `/tmp/orchid-memory-upgrade` worktree was created from public master
before this comparison. It has no implementation changes and is not the chosen
upgrade baseline.

## What the comparison changes

| Brief phase | Existing implementation | Remaining work |
| --- | --- | --- |
| 0: evaluation | Three-arm strategy comparison, endurance probes, telemetry, foreground substitution metrics, and benchmark scorecards already exist. | Make a self-contained replay through the actual gateway, using fake engines and temporary databases; remove dependence on private local databases/provider credentials; normalize costs per 1,000 source tokens. |
| 1: ingestion | `ingestion.py` reconstructs protocol messages and streamed tool calls; SQLite stores canonical per-message events and suppresses resends. Pending fixes handle unmatched middle messages and identical assistant replies from different requests. | Prove occurrence identity across ingestion and assembly; record actual usage separately from estimates; eliminate model-visible raw-response fallbacks; test legacy wire rows without rewriting them. |
| 2: layout | Covered client messages are suppressed; tool-loop continuity and prefix telemetry already exist; the gateway returns retryable 503 errors. | Replace the bounded, flattened system tail with every uncovered protocol message. Make capacity checks account for the complete history and output reservation. Verify complete prefix stability, including retrieval/root-task blocks. |
| 3: itemized observer | V2 items, evidence excerpts, deterministic patch carry-forward, supersession references, and bounded similar-record context already exist. Selected evidence is already sent to the consolidator once. | Add a one-call observer, exact raw-event quote ranges and validity sequences, durable superseded items, creation-order rendering, and explicit reflection accounting. Keep the legacy engine selectable. |
| 4: raw recall | Cold-memory FTS5 and retrieval telemetry exist for promoted `RETIRE` records; dense retrieval experiments exist. | Index normalized raw events, expose scoped HTTP and MCP recall, enforce privacy, and optionally connect an embeddings endpoint. Cold semantic recall is not raw-event recall. |
| 5: coding mode | Source projection already stubs bulky/repeated tool results before compaction, and root-task persistence exists. | Make coding masking explicitly optional and promotion-bound; add a file ledger and recoverable offload handles. Do not classify existing lossy projection as completed recall. |

## Pending work to retain or isolate

Retain the intent of these runtime fixes, with regression coverage before
making them dependencies of the upgrade:

- `db.py`, `context.py`, and `tests/test_event_truth.py`: resend suppression,
  response idempotency by request ID, and preserved event occurrences. Use one
  shared alignment rule for ingestion and foreground assembly; identical text
  can represent separate occurrences, especially after promotion or truncation.
- `db.py`, `compaction.py`, and scheduler tests: atomically commit capsule
  promotion and job completion; allow failed/stale snapshots to retry with new
  generations while retaining the old attempts.
- `compaction.py` and pipeline adapters: bounded snapshot progress, shared
  selector chunking, delayed result caching until candidate validation, and a
  separate consolidator timeout. The four-chunk limit is a legacy selector
  safeguard, not a future observer batching policy.
- `gateway.py` and gateway tests: zero foreground maintenance wait, urgent
  enqueue, truthful capacity errors, and stream-failure telemetry. Preserve
  zero wait: urgent work runs in the background and clients retry if capacity
  remains exceeded; do not turn the upgrade into a foreground summarizer.

Keep provider routing, quota preflight, frozen proof tooling, pricing cards,
VRAM sampling, continuation policy, and benchmark finalization in their own
review scope. They may support later live evaluation but are not prerequisites
for a credential-free memory harness. Historical prices must come from the
artifact's sealed rate card, not the current provider default.

The pending model-identity and minimum-content-length checks are heuristics,
not proof of grounding or retention. Evaluate them as legacy safeguards and
do not use an 80-character minimum as an observer acceptance criterion.

## Revised delivery sequence

Each numbered phase remains a separate PR. Review and isolate pending safety
fixes before the phase PRs; do not bundle the entire dirty checkout into one
upgrade commit. Phase PRs build sequentially on the reviewed local baseline.

### Phase 0: reproducible baseline and characterization

Reuse scoring and telemetry helpers, then create an isolated gateway replay
with full context, one-call rolling summary, and the corrected current
three-stage engine. Record baseline commit/configuration and fixture hashes.
Public-master behavior can be a historical comparison, not the main control.

Use sanitized synthetic fixtures and optional explicitly supplied recorded
threads. Exercise history resends, streamed tool calls, repeated identical
messages, failed retries, and arbitrary promotion schedules. Probe generation-1
retention at generation 10, corrected values, absent facts, negative decisions,
and exact artifact identifiers. Add the observer arm in phase 3.

Report calls, input/output tokens, and wall time per 1,000 unique source tokens.
Count all attempts, failures, observer/reflection/grounding calls, and cache
hits. Distinguish provider usage, estimated tokens, fake timing, and real wall
time. Fake-engine correctness does not establish live-model recall or speed.
Keep known-gap characterization separate from acceptance tests so the baseline
can be recorded honestly without pretending the gaps are fixed.

### Phase 1: finish ingestion and legacy compatibility

Extend the existing ingestion path instead of replacing it. Preserve roles,
content blocks, tool IDs, names, arguments, request identity, and distinct
occurrences. Validate malformed messages explicitly instead of silently
filtering them. Separate message identity from provider-local reasoning fields
without changing authoritative identifiers or original stored payloads.

Provider prompt usage is aggregate request usage, not the token count of each
resent user message. Store usage at request/response scope; assign completion
usage to the final response when applicable and keep estimates clearly marked.
Do not mutate already appended events to fill usage later.

Keep raw wire/error payloads outside model-visible conversation and compaction.
Read legacy request/SSE rows through a deterministic compatibility projection;
do not rewrite them or invent assistant text after a malformed/partial stream.
Keep credentials out of any optional audit storage.

Acceptance: an N-turn full-history client stores approximately 2N message
events plus explicitly counted control messages; repeated occurrences survive;
neither request envelopes nor SSE frames reach foreground/background models;
legacy databases remain readable.

### Phase 2: close the coverage gap and stabilize serialization

Construct outbound messages as client instructions, active memory, and every
uncovered event in stored order, preserving protocol fields. Align client
copies to stored occurrences once. Include covered tool mates only when needed
for a valid live tool loop and account for that explicit continuity policy.
The stored history, not the client's current resend window, determines coverage.

Remove the flattened raw-tail system message and sliding tail selection from
the new layout. Retire `overlap_tokens` through a compatibility deprecation
rather than treating a currently ignored argument as functional overlap.
Keep root-task and retrieved context from changing the stable memory prefix.
Request-specific retrieval should follow that prefix.

Budget the fully serialized request, tool schemas, and reserved output. Enqueue
urgent maintenance when necessary; preserve zero foreground wait and return
retryable capacity errors if the complete context does not fit. No event may
vanish merely because the old tail budget was exceeded.

Acceptance: property tests over event occurrences and promotion schedules prove
that every event is covered or forwarded verbatim, without duplicate client
copies, and bytes through the memory block remain identical without promotion
when client instructions are unchanged. Include truncated histories and
multi-turn tool loops, not just full-history clients.

### Phase 3: observer deltas on existing item foundations

Version the item schema instead of silently reinterpreting V2. Add claim,
required kind/status vocabulary, valid-from sequence, source event IDs, and
exact quotes with character ranges into a defined immutable source-text view.
For normalized JSON message rows, define that view and its mapping explicitly;
projected tool stubs cannot be the sole evidence for raw-history claims.

The observer receives the new batch once and compact similar current items;
software owns the complete item set and carry-forward. Output explicit add,
supersede, and noop operations with no delete. Reject invalid quote-bearing
operations deterministically, retain their rejection accounting, and apply
valid operations in order. A protocol failure, invalid final candidate, lost
lease, or failed CAS leaves active state unchanged.

Keep prior items, including superseded ones, in the durable item history.
Append replacements with pointers; render in creation order instead of V2's
kind grouping/filtering. Reflection runs only at the memory budget and accounts
for every current item as kept, merged, or dropped with a reason; dropping from
the hot view never deletes raw events or item history. Reject incomplete
reflection accounting.

Give the observer its own endpoint/model/credential/timeout configuration and
an idle-only policy. Coordinate with gateway foreground activity at call
boundaries, including streaming and cancellation; document that external GPU
workloads require an explicit idle signal. Avoid adding routine grounding-model
calls to this arm before the harness shows a need; preserve existing semantic
gates as a separately measurable legacy/configurable policy.

Acceptance: exact quote/range rejection and failure/lease/CAS tests pass; the
generation-10 and update/absence probes meet or beat the corrected legacy arm;
measured compaction input is at most one fifth of legacy input on the same
workload, including reflection and retries. Report the actual ratio and do not
claim the target from fake arithmetic alone.

### Phase 4: normalized raw-history recall

Reuse connection management, query escaping, scoping, and telemetry patterns
from cold retrieval. Build a separate FTS5 index over normalized raw-event
text; safely backfill legacy projections without changing event rows. Return
bounded excerpts, event IDs, and ranges, supporting source-ID lookup as well
as lexical query. Expose HTTP recall and a thin MCP `recall(query, k)` tool.

Resolve project/thread ownership and privacy before returning results or
sending text to an optional embeddings endpoint. Make embeddings optional,
configurable, and testable with fakes; lexical recall must work without them.
Indexing/retrieval failures must not affect active memory or event append truth.

Acceptance: a detail deliberately omitted from memory is recovered via the
real recall path in the harness; cross-project/thread and restricted-privacy
results are excluded; retrieved text never becomes a source event.

### Phase 5: optional coding mode

Keep off by default. Make tool masking a promotion-time view transformation
with original event IDs and a configurable last-K-turn policy. Preserve full
raw/indexed content for recall; keep existing legacy projection behavior in the
legacy arm so comparisons remain interpretable.

Derive a file ledger conservatively from known tool schemas, recording read,
edit, and create evidence. Do not infer successful edits from failed tool calls.
Oversized messages/results get durable, scoped handles, deterministic outlines,
and searchable/range-readable originals. Evaluate artifact, continuation,
decision, and recall probes before enabling this mode.

## Verification from this comparison

- Focused existing suite: **100 passed in 22.29 seconds**, using the repository's
  Python 3.12 environment. Covered event truth, scheduling, gateway runtime,
  model telemetry, incremental capacity, foreground substitution, source
  projection, semantic gates, and cold memory.
- Coverage counterexample in a temporary database: four uncovered events with
  a one-event tail budget and no active capsule produced only one forwarded
  event. Three events were omitted. This is observed current behavior.
- Quote counterexample: `_excerpt_in_source()` accepted an exact source sentence
  followed by the invented word `invented`, although the resulting quote was
  not a substring. Its trailing-word fallback does not satisfy the new contract.
- Full default suite: **418 passed, 4 skipped in 101.34 seconds**, using the
  repository's Python 3.12 environment. Skips are not verified acceptance gates.
- No live provider evaluation, fivefold-cost result, or Python 3.11 execution
  has been established by this comparison.

Each phase updates `README.md`, `docs/why-orchid-exists.md`, and
`docs/invariants.md` as applicable, delivers its harness report against the
sealed baseline, and states unverified acceptance criteria. The upgrade's
remaining work is implementation; this document does not claim completion.

## Implementation status

Phases 0-5 are now implemented in stacked draft PRs. See
[the delivery report](memory-upgrade-delivery.md) for changes, results,
configuration, and unverified live gates. The original comparison above is
preserved as the starting audit; the stack is not merged or deployed.
