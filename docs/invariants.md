# V1 invariants

## Event truth

Events are append-only. Corrections append new events; they never mutate an earlier event. Each event has a thread-local monotonic sequence, a content hash, and optional request/parent identifiers.

## Snapshot isolation

A job records `base_capsule_id` and `snapshot_end_event_id` when queued. A worker must compact only that snapshot. Events appended afterward remain outside the job.

## Capsule lineage

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
