# Native SpecPrefill gateway (opt-in)

The existing `/v1/chat/completions` route can now use the local 0.8B student and
Flash-Next through the packaged `memory_gateway.specprefill` runtime. The old
summary/observer/backend route remains the default. This is an implemented text
integration with offline verification, not a completed production rollout or a
new live-model latency result. See the [production plan](specprefill-production-plan.md).

## Lean engine (recommended)

`ORCHID_SPECPREFILL_ENGINE=lean` switches the route to `SpecSession`
(`memory_gateway/specprefill/spec_session.py`), the design from
[specprefill-goal.md](specprefill-goal.md). The student reads every input. The
target receives only top-ranked, line-aligned excerpts under a per-turn token
budget and never writes summaries. The default engine stays `compacting` (the
earlier summary/provenance path), so existing state directories keep working.

How a turn works:

- Earlier messages, tool results and assistant tool calls are ingested as
  sources. A final user message longer than `QUERY_SPLIT_TOKENS` is ingested
  too, and only its tail (its last paragraph, at most `QUERY_TAIL_TOKENS`) is
  sent verbatim. A conversation may end with a `tool` message; it is ingested
  and the verbatim query becomes a short "continue" instruction.
- One cached student trace scores every not-yet-delivered chunk.
  - Requests that name a file path (and a version) boost those sources.
  - Each excerpt carries its enclosing top-level line (a `def`, `class` or
    assignment) and a `[path vN current|superseded by vM]` header.
- The budget is `ceil(NEW_RATE × new tokens) + RECALL_TOKENS` rendered tokens,
  framing included. Filling stops early once chunks score below
  `RELATIVE_FLOOR` × the `FLOOR_RANK`-th best, so easy turns read less.
- When the target window fills, it is rebuilt from student-selected chunks plus
  the most recent turns. When the student window fills, its oldest sources roll
  out and come back through identifier search. Both are append-only between
  rebuilds, so prefix caches keep hitting.
- Sessions checkpoint atomically after every committed change. A failed target
  call commits nothing and can be retried with the same request ID.

| Variable suffix after `ORCHID_SPECPREFILL_` | Lean default |
|---|---:|
| `ENGINE` | `compacting` (set `lean`) |
| `CONTEXT_TOKENS` (target window) | 8192 |
| `STUDENT_CONTEXT_TOKENS` | 32768 |
| `ANSWER_TOKENS` | 128 |
| `NEW_RATE` | 0.06 |
| `RECALL_TOKENS` | 160 |
| `RELATIVE_FLOOR` / `FLOOR_RANK` | 0.30 / 3 |
| `QUERY_SPLIT_TOKENS` / `QUERY_TAIL_TOKENS` | 1024 / 256 |
| `VRAM_RESERVE_MIB` (left free for the co-located student) | 4096 |
| `STUDENT_SPARSE_KEYS` (block-sparse student prefill) | unset (dense) |
| `STUDENT_SPARSE_ROWS` (query rows per block selection) | unset (256) |
| `TARGET_KV` (`int8` or `fp16`) | unset (the config's) |

**256K sessions (P100):** `CONTEXT_TOKENS=49152`,
`STUDENT_CONTEXT_TOKENS=262144`, `VRAM_RESERVE_MIB=8192`,
`STUDENT_SPARSE_KEYS=16384`, `STUDENT_SPARSE_ROWS=8192`, `TARGET_KV=fp16`. See the 256K section of
`specprefill-goal.md` for why each value was chosen.

**Tools.** The lean route accepts OpenAI `tools` (`tool_choice` `auto` only).
Definitions are rendered into the pinned system text in Qwen format, and
`<tool_call>` output comes back as `tool_calls` with
`finish_reason: "tool_calls"`. With tools, streaming sends the parsed content
and a `tool_calls` delta at the end instead of raw tokens. Changing the tool
list changes the pinned system text, so it needs a new thread. The compacting
engine still rejects tools.

Measured results and limits are in the goal doc. The lean route has offline
coverage (session, gateway, tools, restart) and one 256K HTTP run on a real Codex session (122 turns, restart, streaming, retry; goal doc).

## Configuration

Use a Python environment with the gateway dependencies and the already validated
student Torch/Transformers runtime. Strata's local Python frontend dependencies
and the existing native pack must also be available. Models are loaded lazily on
the first valid request; no downloads or package installations run automatically.
The installable runtime does not depend on the `experiments` package.

```bash
export ORCHID_SPECPREFILL_ENABLED=true
export ORCHID_SPECPREFILL_CONFIG=/path/to/existing-private-strata-config.json
export ORCHID_SPECPREFILL_STUDENT=/path/to/Qwen3.5-0.8B
export ORCHID_SPECPREFILL_STATE_DIR=/private/specprefill-state
export ORCHID_SPECPREFILL_MODEL=orchid-flash-next-specprefill
python -m uvicorn memory_gateway.gateway:app --host 127.0.0.1 --port 18080 --workers 1
```

Use a new dedicated state directory and a single gateway worker. An exclusive
owner lock prevents two processes from opening the same native session store.
Changing the configured native model paths/configuration or token budgets requires
a new state directory; policy thresholds and resident-session limits can change
without reusing completions under a different model configuration.
The private native engine copies the existing config, uses ordinary prefix caching
and sequential positions, reserves 4096 MiB for the student, and sets its context
to the configured limit. It never edits the supplied native config, binary, or an
existing running service.

| Variable suffix after `ORCHID_SPECPREFILL_` | Default |
|---|---:|
| `CONTEXT_TOKENS` | 8192 |
| `COMPACT_TOKENS` | 3072 |
| `ANSWER_TOKENS` | 128 |
| `EXPANSION_TOKENS` | 2048 |
| `SUMMARY_TOKENS` | 1024 |
| `HIGH_WATER` | 0.70 |
| `RESIDENT_SESSIONS` | 4 |
| `ARCHIVE_CACHE_MIB` | 8 |

Context and token budgets are actual target-tokenizer counts. Compaction runs
synchronously under the inference owner lock when projected input, expansion,
answer reserve, growth, or summarizer headroom reaches the admission threshold.
It is scheduled automatically; it is not a background timer that mutates live KV
state concurrently with generation. Each admission can compact at most once.
If the input still cannot fit, or summary headroom is already exhausted, it fails
with a capacity response rather than truncating the archive or looping.

The forecast is conservative admission logic, not a proof that every possible
future summary can fit. Invalid/oversized model-generated summaries fail closed;
the old logical generation stays replayable. Thresholds need live calibration.

## Requests

```bash
curl http://127.0.0.1:18080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'x-memory-project: demo' \
  -H 'x-memory-thread: conversation-1' \
  -H 'x-request-id: turn-1' \
  -d '{"model":"orchid-flash-next-specprefill","messages":[{"role":"user","content":"Hello"}],"max_tokens":128,"stream":true}'
```

Project and thread headers are required. An omitted request ID is generated and
returned in `x-request-id`; reuse the same ID and payload for a retry. Completed
retries return the persisted completion without new model inference. A request
ID with changed content gets HTTP 409. An interrupted request must be retried
before a different request advances that thread.

Clients may send an extending full conversation, or one new user message using
the same headers. Full histories must match the accepted prefix, including actual
assistant replies. Edited histories/system instructions require a new thread.
Imported past messages are ingested in bounded UTF-8-safe source sections, with
automatic compaction between sections. Their exact text remains archived. The
current user request itself must fit: the route does not silently summarize or
truncate active instructions.

The initial route supports nonempty plain-text `system`, `user`, and `assistant`
messages, one completion, greedy sampling, `max_tokens`/`max_completion_tokens`,
and optional SSE streaming. Unknown model names, tool calls, developer/tool
messages, images, extra message fields, unsupported sampling controls, or an
output request above `ANSWER_TOKENS` return HTTP 400 before loading models.
The gateway factory currently uses indexed lexical/symbol retrieval. It does not
silently enable a semantic encoder or full archive scan.

## Streaming, ownership, and persistence

SSE content is emitted from actual target token callbacks after scheduling and
student selection. Summary generation and target rebuild probe tokens never go
to the client. CPU/model work runs off the event loop, so health requests remain
responsive. Stream failures use an error event followed by `[DONE]`; headers
already sent cannot change their HTTP status.

Disconnect detaches stream delivery. The bounded native turn finishes under the
same owner lock and checkpoints its outcome; shutdown drains those tasks before
closing models. This intentionally spends the remaining bounded decode budget
rather than leaving uncertain partial recurrent/cache state.

One native model pair serves requests serially. Switching conversation resets the
student's active numerical cache; prompt IDs and archive state are scoped per
session. Only the configured number of session objects remain resident. Evicted
sessions reload from checkpoint. This bounds the number of resident sessions,
with each persistent raw archive paged from disk through its own bounded payload cache.

`gateway.sqlite3` stores per-message history-ledger rows, pending request identity,
and completed responses. Each turn appends only its new message fingerprints;
the full list is never rewritten, and pre-ledger snapshot databases migrate on open. A checkpoint stores the last actual output IDs, allowing
reconciliation if a model turn commits before its response-ledger transaction.
Raw archive/index files use a hash of project and thread identifiers as their
basename. Gateway event storage also uses a derived thread ID to isolate projects
that use the same client thread name. Existing header-scoped recall routes apply
the same mapping when this mode is enabled.

Raw text, token pages, provenance manifests, cues, and events now live in
`<session>.archive.sqlite3`. A small JSON descriptor identifies that database;
normal turns do not rewrite the full archive. Raw updates and the bounded active
session snapshot commit together. Text and token pages load on demand through an
8 MiB retained payload cache per resident session (set `ARCHIVE_CACHE_MIB`, or 0
to disable payload retention). SQLite adds a 4 MiB archive page cache and a 2 MiB
search page cache per resident session. These are not total-process RAM limits:
model/KV memory, active prompts, incoming sources, caller-held objects, per-message
history-ledger rows, and OS file caches are separate.

Legacy JSON sessions migrate on first restore, preserving their original file as
`<session>.legacy.json`. Migration reads the old JSON into memory once. Back up
the descriptor, archive database, search database, and gateway ledger together
with the service stopped; copying a live SQLite main file alone can omit WAL
transactions. See [paged storage](specprefill-paged-storage.md) for recovery and
storage details.

## Verification status

Offline tests cover automatic scheduling, large imported histories, JSON/SSE,
request deduplication, history conflict, target failure/retry, crash-window answer
recovery, process ownership, resident-session eviction, serialized concurrent
requests, project isolation, nonblocking health, summarizer headroom exhaustion,
and disconnect completion. The existing gateway route is regression-tested with
the mode disabled. Storage tests additionally cover lazy reopen, cache bounds, checksums, atomic
rollback, descriptor recovery, and legacy migration. The full offline suite
passed with 549 tests and 13 skips after the history-ledger change.

A separate two-turn native smoke on the existing P100 used the real 0.8B student
and Flash-Next: both replies were `37`, the second turn automatically compacted,
and the target reported 230 reused prefix tokens after rebuild. The private
engine was shut down afterward. This validates connectivity and one boundary,
not broad answer accuracy, long-session stability, or a latency advantage.
The existing running service remains unchanged and the mode stays opt-in.
