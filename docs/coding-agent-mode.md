# Optional coding-agent mode

Off by default. Set `ORCHID_CODING_AGENT_MODE=true` to enable it.
`ORCHID_CODING_KEEP_TURNS` defaults to 4; a turn here is an assistant message.
`ORCHID_CODING_OFFLOAD_TOKENS` defaults to 8,000 estimated text tokens.

Large text messages/results are recorded and indexed in full. Outbound content
becomes bounded prefix/suffix excerpts with an `event:<id>` handle and original
length. Roles, tool IDs, and arguments are preserved. Non-text content is not
offloaded by this text policy. Failed indexing retains original content and
ordinary capacity handling; an unrecoverable stub is never sent.

The observer can service oversized coding sources using bounded exact excerpts
and a software-created artifact item pointing to the complete indexed source.
Original character ranges/hashes remain authoritative. A unique 5,000-line
source is tested through promotion and range recovery, including its final fact.

Successful CAS promotion atomically appends a prompt-view boundary based on the
most recent K assistant turns. Older tool output then gets a source-ID stub.
New turns do not slide that boundary. This foreground view does not add later
events to the worker's compaction sources and is separate from memory coverage.
Coding stubs are an explicit exception to default verbatim forwarding and always
have recoverable originals.

The observer maintains a file ledger from known read/read_file, edit/edit_file,
write/write_file, create_file, and apply_patch schemas. Paths/actions cite call
and result event IDs. Reads need a non-error response; writes/edits need explicit
success. Creation requires an explicit creation tool/result. Recognized patch
success supports Add/Update paths. Failed/unrecognized writes remain attempts,
and pending calls carry across batches. Shell commands and unknown schemas are
not guessed into successful edits.

The ledger changes at promotion and appears in memory. Reflection protects it
alongside legacy text; an oversized protected block causes a safe failed
candidate. Masking never changes raw events or complete item/ledger history.

The separate coding fixture checks retention, updates, absent facts, negative
decisions, exact artifact paths, continuation, and raw recall, plus confirmed
read/create ledger entries. All six primary probes and recall pass with fakes.
Live coding quality and external MCP-client integration remain unverified.
