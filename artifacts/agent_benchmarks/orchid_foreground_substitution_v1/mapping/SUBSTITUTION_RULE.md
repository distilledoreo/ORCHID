# Substitution rule

After an ACTIVE capsule exists, `ContextAssembler.assemble` does:

1. Prefix ACTIVE (capsule tokens only).
2. Raw tail: events with `sequence > covered_sequence`, target 16k. The 12k minimum applies **only** inside that uncovered window. It never walks into compacted history.
3. Drop inbound messages aligned to events with `sequence <= covered_sequence`.
4. Keep unaligned / uncovered inbound, including a repeated identical user/tool string that is a **new** suffix event.
5. If the watermark splits a tool loop, keep the assistant `tool_calls` message and matching `tool` results. That overlap is counted as `protocol_continuity_tokens` and is the only allowed `covered_raw_tokens_forwarded`.
6. Do not dump uncovered journal events that are already present as forwarded protocol messages.

Before ACTIVE, assembly is unchanged: full inbound + tail with the historical 12k floor.

Overlap policy: **none** (constructor `overlap_tokens` retained, unused for substitution).
