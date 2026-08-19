# Why ORCHID exists

ORCHID is built around a simple tension: an agent may need a long-lived
conversation, but every model call still has a finite active context window.
Increasing that window helps, but it does not make memory durable, cheap, or
safe to update. ORCHID treats context management as a systems problem rather
than asking a single prompt to solve it.

## Finite active context

At request time, an agent sees a bounded working set: a durable capsule plus a
recent raw tail. The tail preserves immediate continuity; the capsule carries
older distilled state. This is deliberately different from repeatedly
summarizing the entire conversation in place. An in-place summary can silently
overwrite an earlier fact, lose the evidence behind a claim, or race with a
new request.

The active context is finite by design. What grows without the model's context
window is the underlying event history on disk. That separation creates an
“infinite-context illusion”: the agent can continue across many turns while
the system manages a bounded view of an unbounded record. It is an illusion,
not magic. Recall quality still depends on compaction quality, and the current
prototype does not claim perfect retrieval.

## Immutable episodic history

ORCHID stores interactions as append-only events. A correction is a new event,
not an edit to the old one. Events have stable IDs, thread-local sequence
numbers, content hashes, and optional request or parent identifiers. The raw
record is the ground truth from which later memory can be reinterpreted.

This is the episodic layer: what happened, in order, with its original source
available for inspection. Capsules are not replacements for that history.
They are derived, versioned interpretations of a frozen range of it. Keeping
both layers makes memory evolution auditable and gives a failed compaction a
safe outcome: the previous capsule and the raw events remain untouched.

## Sleep and hibernation compaction

Compaction is intentionally asynchronous. When uncompacted history creates
pressure, the gateway queues a job instead of forcing the foreground request
to perform a large summary. The job freezes a snapshot and records the active
capsule from which it started. Later events are outside that snapshot and
cannot be accidentally consumed by the worker.

The worker then runs a composable pipeline:

```text
frozen raw snapshot
        ↓
selector → canonicalizer → lossless evidence packet → consolidator
        ↓
validated descendant capsule + protected recent tail
```

The analogy is sleep or hibernation: active work continues with a compact
working state while older experience is consolidated in the background. The
analogy also explains why compaction must be versioned. A worker may wake up
late, lose its lease, or produce a candidate from an obsolete base. Such a
candidate must become stale rather than replacing newer state.

## Provenance belongs to software

Models are useful interpreters, but they are not authoritative database
writers. ORCHID therefore keeps capsule state, source coverage, hashes,
lineage, leases, and promotion in software.

The selector may identify evidence worth considering. The local canonicalizer
may clarify that evidence in bounded batches. Software mechanically preserves
the complete ordered set of selected source events or spans and attaches the
authoritative raw evidence to the lossless packet. The stronger consolidator
performs the global semantic merge and may cite a subset of that evidence, but
its citations cannot expand or redefine software-owned coverage.

Every stage crosses an explicit validation boundary. Structured output is
schema-constrained, unknown or duplicate references fail validation, model
calls are recorded in persistent telemetry, and a failed stage cannot promote
a candidate. Final promotion is a SQLite compare-and-swap against the
thread's current active capsule. This turns model uncertainty into an
observable failed attempt instead of silent state corruption.

## What the Bluejay result demonstrates

The Bluejay experiment was the motivating end-to-end test. A newer capsule was
created as a descendant of an existing capsule, while preserving stable
deployment facts and updating newer lease-race findings. The relevant raw
events were then advanced outside the protected recent tail. Solar was asked
for the updated facts without having them restated, and returned the required
answers from the descendant capsule alone.

That result is narrower than “ORCHID has solved memory.” It demonstrates the
property this architecture is meant to establish: memory can evolve across
generations, retain lineage, and remain usable after its originating raw
evidence leaves the active tail. The public repository preserves a sanitized
fixture and short PASS report as evidence; it does not present the project as
production-ready.

## The design stance

ORCHID prefers explicit boundaries over clever prompts:

- raw history is append-only;
- active context is bounded;
- compaction is asynchronous and snapshot-based;
- models interpret but software owns provenance and promotion;
- failures preserve the old active state;
- every capsule has lineage back to source history.

Future work can improve packet efficiency, selector precision, local inference
speed, setup, and retrieval. Those improvements should fit inside these
boundaries. The core idea comes first: give an agent a finite working memory,
an immutable episodic record, and a safe way to sleep older experience into
durable context.
