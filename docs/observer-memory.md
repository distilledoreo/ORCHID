# Evidence-linked observer memory

Configure `ORCHID_OBSERVER_URL`, `ORCHID_OBSERVER_MODEL`, and optionally
`ORCHID_OBSERVER_API_KEY`. `ORCHID_MEMORY_ENGINE=auto` selects the observer when
its endpoint/model are present and otherwise retains the configured legacy
engine. `observer` requires the observer configuration; `legacy` forces the
three-stage comparison engine. Observer transport credentials are not serialized
into items or telemetry.

`ORCHID_OBSERVER_INPUT_TOKENS` defaults to 32,768 and
`ORCHID_OBSERVER_TIMEOUT_SECONDS` to 120. The hot memory budget remains
`ORCHID_CAPSULE_MAX_TOKENS`. Input budgeting currently estimates serialized
payload tokens; real provider overhead/tokenization must be calibrated separately.

One observer call receives each batch and bounded similar current-item claims.
Software retains the complete ledger, validates every new quote/range, assigns
deterministic IDs and validity sequences, and applies add/supersede/noop deltas.
Malformed structured responses fail the attempt. Structurally valid operations
with invalid quotes/targets are rejected individually and recorded without raw
diagnostic excerpts; remaining operations may still promote. There is no delete.
Exact quotes establish source identity, not logical entailment of every claim.

Quote offsets name `message-text-v1`: text content, or canonical JSON for
non-text content, followed by named tool/function calls. This view is derived
deterministically from immutable event rows. Literal repeated bytes within one
event may be collapsed in observer input; retained spans carry original ranges.
Unique source text is not arbitrarily truncated, and raw tool text is not
replaced with the legacy projection before observer validation.

Capsule metadata stores the complete versioned ledger. Superseded items remain
there. Rendering follows creation order and shows source IDs and replacement
pointers; mutable status does not rewrite earlier rendered lines. Reflection
runs only when the hot view exceeds budget. It must account exactly once for
every current item as kept, merged, or dropped with a reason. Merged evidence
is rechecked against original sources. Eviction affects only the hot view;
history and original sources remain. Incomplete accounting or a still oversized
result fails without promotion.

Existing V1/V2 capsule text is retained as a protected legacy block until an
evidence-backed migration is supplied. It is not relabeled as verified observer
items. A legacy block that alone exceeds the new budget cannot be silently
discarded; reflection must fit or the attempt fails. Historical V2 semantic
admission remains part of the legacy engine; observer results use their own
versioned validation and the same global hashes, leases, and CAS boundary.

`ORCHID_BACKGROUND_IDLE_ONLY=true` admits background jobs and observer/reflection
calls only while the gateway has no foreground response in progress. Streaming,
errors, and cancellation are tracked through the full ASGI response lifecycle.
An already dispatched background call is not preempted by a new foreground
request. External GPU workloads are outside this gate and require external
coordination. A separate observer endpoint avoids shared-GPU contention.

The deterministic replay produces 10 observer calls, 1,153 estimated input
tokens, and 415 output tokens versus 40 calls, 56,495 input, and 1,168 output
tokens for the corrected legacy arm: **49.00 times fewer input tokens**, with
4/4 probes passing, including generation-10 retention. This fixture contains
literal repeated diagnostic text, which deterministic prefiltering compresses
well. The ratio is not a claim about non-repetitive workloads or live providers.
Reflection's test-only eviction fake tests accounting, not semantic retention.
