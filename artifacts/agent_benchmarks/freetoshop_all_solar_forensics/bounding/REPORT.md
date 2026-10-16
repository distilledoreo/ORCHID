# Source-window bounding

The failed implementation froze every event after the ACTIVE watermark through
the newest event. There was no source-token, byte, event-count, or provider
context bound. The 547,802-token request was therefore an unbounded backlog
generation, not a bounded semantic window.

The minimal invariant is now: one immutable generation covers one bounded
event-prefix; after promotion, the next job starts at the new watermark. The
generic budget is `(provider_context - reserved_output - fixed_overhead) *
safety_margin / request_amplification`, yielding **119,466 source
envelope tokens** for the captured Solar context configuration; the active
capsule estimate is subtracted again at snapshot freeze.

The failed run has 213 unique event IDs and 213 unique content hashes. Events
are appended in contiguous request suffixes; no repeated-prefix ingestion
regression was found.
