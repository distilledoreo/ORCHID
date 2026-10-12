# Raw-event recall

`POST /v1/memory/recall` accepts `{"query":"deadline","k":5}` and requires
`X-Memory-Project` and `X-Memory-Thread`. Results contain event IDs, sequence,
role, `message-text-v1` excerpts, character ranges, and total source length.
`event:<id>` looks up an item source directly.

`GET /v1/memory/events/{event_id}?start=0&end=4096` reads a range under the same
scope. Each response is bounded to 4,096 characters and reports its actual end;
subsequent ranges recover the rest. Cross-scope or non-normal-privacy IDs return
no source. Only `privacy_class=normal` is indexed, returned, or sent for optional
embedding. Scope/privacy are rechecked when embedding results are saved and
when search results are returned.

FTS5 indexes normalized event text separately from the append-only rows. Legacy
envelopes/SSE are backfilled through the same deterministic text view, without
rewriting events. Index failure cannot prevent an authoritative append. Reopen
backfill restores missing documents/FTS entries. Unavailable lexical storage
returns a retryable error rather than changing active memory.

Optional vector indexing uses `ORCHID_EMBEDDINGS_URL`,
`ORCHID_EMBEDDINGS_MODEL`, and `ORCHID_EMBEDDINGS_API_KEY`, with
`ORCHID_EMBEDDINGS_TIMEOUT_SECONDS` defaulting to 15. No embedding calls occur
merely because configuration is present. Explicitly call
`POST /v1/memory/index-embeddings` with `{"batch_size":32}` under the same scope.
Each call indexes up to 64 chunks of 2,048 source characters, then resumes on
the next call. Index identity includes endpoint/model; vector dimensions and
finite values are validated. Queries use available vectors as a lexical
supplement; embedding failures preserve lexical results. Calls persist numeric
usage, timing, and hashes through the model telemetry table, never credentials
or raw diagnostic payloads.

Run the thin MCP adapter with:

```sh
python -m memory_gateway.recall_mcp --url http://127.0.0.1:7331 --project demo --thread thread-1
```

It exposes `recall(query, k)` and binds scope at process startup; tool arguments
cannot override the project/thread. The adapter uses newline-delimited JSON-RPC
on stdio, initialization, tool discovery, and text/structured tool results as
defined by the [MCP stdio specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
and [tool specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).
Protocol JSON is the only stdout output. GUI-client integration has not been
verified; HTTP, in-process MCP, and subprocess stdio behavior are tested.

The replay adds a `DETAIL deadline=friday` source that the fake observer
deliberately omits. Before recall the answer is unknown; scoped HTTP retrieval
provides source excerpts and the foreground answer becomes Friday. The amended
fixture has a new hash, and all four arms use that same fixture for comparison.
