# Mapping report — Pi request messages to ORCHID events

## Smallest reliable mapping

Coverage is **not** semantic. It uses the same exact suffix/prefix identity already used for ingestion:

1. Canonical `message_hash(message)` from `memory_gateway.ingestion` (role, content, tool call IDs/arguments; transient reasoning fields stripped).
2. Persisted `metadata_json.message_hash` on each chat event.
3. `_ordered_history_overlap(known_hashes, incoming_hashes)`:
   - prefer `incoming[:n] == known[:n]` (full resent transcript)
   - else suffix `incoming[:n] == known[-n:]`
4. Align inbound index `i < overlap` to the overlapping journal events.
5. An aligned message is **covered** iff `event.sequence <= ACTIVE.covered_end_event.sequence`.

Unaligned suffix messages (new user, new tool results, stream-follow-up) are **not** covered, even if their text equals an older event.

## What remains after ACTIVE

- ACTIVE capsule system message
- Uncovered raw events with `sequence > covered_sequence` (no 12k floor into compacted history)
- Inbound messages whose aligned event is uncovered, or that have no alignment
- Protocol exception: assistant `tool_calls` + matching `tool` results when the watermark splits an in-flight tool loop

## Explicitly unused

Fuzzy text match, embeddings, selector/consolidator output, retrieval hits.

## Watermark

`covered_end_event_id` on the ACTIVE capsule → `get_event` → integer `sequence`. Tail and suppression both use that sequence.
