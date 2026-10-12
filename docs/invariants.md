# V1 invariants

## Event truth

Events are append-only. Corrections append new events; they never mutate an earlier event. Each event has a thread-local monotonic sequence, a content hash, and optional request/parent identifiers.

Gateway ingestion stores one normalized protocol message per new occurrence.
Resent history reuses event identities; identical replies from different request
IDs remain distinct. Completion usage is attached when the assistant row is
created. Aggregate request usage is a separate numeric-only record, never an
update to earlier events. Invalid provider wire payloads are not conversation
events. Legacy envelopes/SSE are parsed through deterministic model views while
their original database rows and hashes remain unchanged.

## Snapshot isolation

A job records `base_capsule_id` and `snapshot_end_event_id` when queued. A worker must compact only that snapshot. Events appended afterward remain outside the job.

## Capsule lineage

Observer capsules retain a versioned item ledger with claims, kinds, current or
superseded status, valid-from sequences, source event IDs, and exact quote ranges.
New quotes must match `message-text-v1` at the cited ranges. Observer input hashes
use raw immutable events, and capsule hashes include the complete observer state.
Normal deltas append rendered items; only budget-triggered reflection may rebuild
the hot view, with exhaustive current-item accounting. Neither supersession nor
reflection deletes raw events or durable item history. Legacy text is preserved
until an explicit evidence-backed migration; failed migration cannot erase it.

Capsules are immutable artifacts. A candidate records its base capsule, source range, input/output hashes, capsule hash, and model metadata. A failed candidate cannot become active.

## Compare-and-swap promotion

Promotion runs in `BEGIN IMMEDIATE` and succeeds only when:

- candidate state is `READY`;
- candidate belongs to the thread;
- candidate's base is the thread's current active capsule;
- the conditional thread update affects exactly one row.

Lost races become `STALE`.

## Job leases

Workers claim jobs with a lease. Expired `RUNNING` jobs can be reclaimed, and the attempt count remains visible for diagnosis.

## Context exhaustion

The scheduler creates jobs before exhaustion. If compaction cannot safely produce a validated candidate, the active capsule and raw events remain unchanged. The HTTP layer must eventually expose a retryable capacity error rather than silently dropping context.

The runtime defaults to the stable layout: client instructions, ACTIVE memory,
then every uncovered message in event order. The legacy root task is retained
once for instruction continuity; covered assistant/tool mates may be retained
to keep a live tool loop valid. Stored occurrences and resent client copies are
not both forwarded. Tail token targets do not truncate this layout. Serialized
message overhead, tool schemas, and reserved completion capacity are counted
before forwarding. Urgent work is queued without foreground maintenance wait;
an over-capacity request returns a retryable 503 and does not call the provider.
The instruction/memory prefix is byte-stable between promotions when client
instructions are unchanged. Request-specific retrieval follows that prefix.
`overlap_tokens` is an ignored compatibility argument, never coverage policy.
The legacy layout remains explicitly selectable for historical experiments.

## Cold-memory isolation

`RETIRE` records and their `memory_evidence` are a sidecar to hot memory. They are persisted only after hot promotion, retrieval never mutates `ACTIVE` capsules, and retrieved text is never appended to `events`. Retrieval, indexing, scoring, logging, and injection failures are fail-open: the assembled context must be no worse than the validated hot-memory context.
