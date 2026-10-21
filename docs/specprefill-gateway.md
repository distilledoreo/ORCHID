# SpecPrefill gateway

With `ORCHID_SPECPREFILL_ENABLED=true` (the installer sets it), `/v1/chat/completions`
and `/v1/responses` are served by Flash-Next with the local 0.8B student. Install and
first steps: [specprefill-install.md](specprefill-install.md); measurements and the
production gates: [the production plan](specprefill-production-plan.md).

Engines (`ORCHID_SPECPREFILL_ENGINE`):
- `lean` (the default): SpecPrefill, below.
- `native`: SpecPrefill off; plain Flash-Next through Strata's own OpenAI path
  at its full window. The one-setting opt-out; stored threads are kept for when
  lean is turned back on. A GPU without a measured configuration, or a missing
  student, also runs native, with one notice line.
- `compacting`: the earlier summary/provenance session (kept for existing state).

Operations:
- Load: up to `QUEUE_LIMIT` (4) requests wait; more get a 503 `busy` with
  `Retry-After: 5`. A request is stopped where it is when its client disconnects
  or after `REQUEST_TIMEOUT_S` (1800; 0 = none); what it generated is kept.
- `GET /v1/orchid/metrics`: turns by engine and native reason, the time split
  (student, selection, Flash-Next prefill and decode), Flash-Next and student
  cache reuse, rebuilds, verbatim and grounded turns, the queue and storage. No
  message text, thread name or tool argument is ever recorded.
- Time-based storage: a thread idle `CACHE_TTL_DAYS` (14) loses its caches (its
  archive stays; it resumes with one re-read); idle `ARCHIVE_IDLE_DAYS` (30), its
  archive is compacted once (SQLite VACUUM, lossless). Checked at start and hourly.
- State formats: every released state directory (fixtures in
  `tests/fixtures/formats`) opens and continues with the current code.

## Lean engine

`ORCHID_SPECPREFILL_ENGINE=lean` (the default) routes to `SpecSession`
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
- Grounded edits (`grounding.py`). A sparse selection rarely holds the current
  text of the lines an edit changes, so an edit written from it patches lines
  that aren't there. When the answer starts changing a file (a `*** Update
  File:` / `*** Add File:` header, or a git diff), the gateway holds that part
  back from the client and lets the target draft about 12 lines of it. It then
  finds what the draft points at in the lossless archive: its hunk location
  and context lines, or the definitions of the names it imports. That region
  (±40 lines), the newest earlier patch to the file and the last 1,024 tokens of
  history are shown, and the target writes the change again from just before
  the draft. Each file of a multi-file patch is grounded in turn (up to 3). A
  request to "write the file X" is drafted (its imports) and grounded the same
  way. If nothing is located, the file's newest reads are shown instead. This
  evidence, like a whole-history answer's, is shown for that answer only and
  not kept in the target transcript, so it never fills the window. The client
  sees one answer. `PATCH_TOKENS` (per file, 4096) bounds it; 0 turns it off.
- Each turn's excerpts and its question go to the target as one user message.
  As two messages, Flash-Next often ended its turn at once (an empty reply)
  when the excerpts ended mid-transcript, for example on a shell command.
- When the target window fills, it is rebuilt from student-selected chunks plus
  the most recent turns. When the student window fills, its oldest sources roll
  out and come back through identifier search. Both are append-only between
  rebuilds, so prefix caches keep hitting.
- Sessions checkpoint atomically after every committed change. A failed target
  call commits nothing and can be retried with the same request ID.

| Variable suffix after `ORCHID_SPECPREFILL_` | Lean default |
|---|---:|
| `ENGINE` | `lean` (`native` = SpecPrefill off; `compacting`) |
| `CONTEXT_TOKENS` (target window) | 8192 |
| `STUDENT_CONTEXT_TOKENS` | 32768 |
| `ANSWER_TOKENS` | 128 |
| `NEW_RATE` | 0.06 |
| `RECALL_TOKENS` | 160 |
| `RELATIVE_FLOOR` / `FLOOR_RANK` | 0.30 / 3 |
| `PATCH_TOKENS` (grounded-edit evidence per file; 0 = off) | 4096 |
| `FRESH_TOOL_TOKENS` (fresh tool results delivered whole, per turn) | 4096 |
| `FRESH_MODE` (`intent`: the student reads long tool results for the last step) | `full` |
| `ELASTIC_ENGINE` (a Strata build with integrations/strata/elastic-vram.patch) | unset |
| `AGENT_NATIVE_TOKENS` (elastic: tool requests go native below this) | 200000 |
| `ELASTIC_PREFILL` / `STUDENT_VRAM_MIB` / `ENGINE_VRAM_MIB` | 1280 / 8192 / 700 |
| `AUTO` (fill unset settings from the measured table for this GPU) | true |
| `QUEUE_LIMIT` / `REQUEST_TIMEOUT_S` | 4 / 1800 |
| `CACHE_TTL_DAYS` / `ARCHIVE_IDLE_DAYS` | 14 / 30 |
| `QUERY_SPLIT_TOKENS` / `QUERY_TAIL_TOKENS` | 1024 / 256 |
| `VRAM_RESERVE_MIB` (left free for the co-located student) | 4096 |
| `STUDENT_SPARSE_KEYS` (block-sparse student prefill) | unset (dense) |
| `STUDENT_SPARSE_ROWS` (query rows per block selection) | unset (256) |
| `TARGET_KV` (`int8` or `fp16`) | unset (the config's) |
| `OUTPUT_TOKENS` (generation length when a request names none) | 4096 |
| `NATIVE_BELOW_TOKENS` (new threads below this go native) | 1024 |
| `STUDENT_PARK_RAM_MIB` (parked student states kept in RAM) | 4096 |
| `STUDENT_PARK_DISK_MIB` (parked student states on disk) | 16384 |
| `TARGET_PARK_MIB` (Flash-Next parked conversations, RAM) | 0 (session files instead) |
| `TARGET_DISK_MIB` (Flash-Next session files) | 16384 |
| `TARGET_HOT_MIB` (Flash-Next session files in `/dev/shm`) | 4096 |
| `HOT_RAM_FLOOR_MIB` (a switch file goes to `/dev/shm` only if MemAvailable stays above this after it; else to disk) | 4096 |
| `HOT_SWAP_FLOOR_MIB` (below this much free swap, switch files go to disk and older hot ones already on disk are evicted) | 2048 |
| `STALE_HOT_HOURS` (another state directory's `/dev/shm` files nothing wrote for this long go at maintenance; 0 keeps them) | 24 |
| `TARGET_RESTORE_FLOOR_MIB` (RAM Strata keeps free when restoring) | 1024 |
| `STATE_QUOTA_MIB` (archive quota; 0 = none) | 0 |
| `MIN_FREE_DISK_MIB` (below this, turns go native) | 1024 |
| `PRELOAD` (load models and resume the latest thread at startup) | on for `lean` |

**256K sessions (P100):** `CONTEXT_TOKENS=49152`,
`STUDENT_CONTEXT_TOKENS=262144`, `VRAM_RESERVE_MIB=8192`,
`STUDENT_SPARSE_KEYS=16384`, `STUDENT_SPARSE_ROWS=8192`, `TARGET_KV=fp16`. See the 256K section of
`specprefill-goal.md` for why each value was chosen.

**Plain clients, no headers.** On the lean engine, `x-memory-project` and
`x-memory-thread` are optional:
- **Thread matching.** The thread is found from the history itself: the
  longest stored thread whose recorded messages are a prefix of the request.
  Replies the gateway produced match even if the client re-serializes them
  (spacing, reasoning, call IDs).
- **Forks and edits.** A fork, an edited or truncated history, or a different
  system prompt starts a new thread, which ingests the whole history.
- **Retries.** A repeated request with no `x-request-id` replays its stored
  answer without calling the model. Any change to a field other than
  `stream`/`stream_options` counts as a new request.
- **Explicit headers** keep the strict behavior: a changed history is a 409.

**Native fallback.** Requests the lean engine can't serve are answered by
plain Flash-Next through Strata's own OpenAI path, on the same engine and
prompt cache. `orchid.engine` is `native` and `orchid.reason` says why. That
covers:
- an image or other non-text part in the message being answered;
- `tool_choice` `none`/`required`/named;
- `logprobs`, `logit_bias` or `response_format` (JSON);
- a lean failure before any output reached the client.

Native turns don't touch the lean session; the next lean turn ingests
whatever it missed. A native request must fit the engine's window
(`CONTEXT_TOKENS`); one that doesn't is a 400 with the reason.
`NATIVE_BELOW_TOKENS` (default 1024, measured) sends a new conversation below
that size to native. Under the query-split size lean sends the whole message
anyway, so native was 0.3–0.6 s faster there. From ~1K tokens lean wins (4.1 s
vs 8.2 s at 1.1K, 5.7 s vs 24.5 s at 4.1K).

**Request shapes.** The route accepts:
- content-part lists and the `developer` role;
- extra message keys (`name`, `refusal`, `reasoning_content`);
- `temperature`/`top_p`/`top_k`/`seed`/penalties, passed to the engine;
- `stop` (up to 4 strings, cut before they're sent);
- `stream_options.include_usage`;
- `max_tokens`/`max_completion_tokens` up to the target window. 0 or -1 means
  "no limit of its own", which uses `OUTPUT_TOKENS` (default 4096).

**Responses API.** `POST /v1/responses` (what Codex CLI speaks) is served on the
lean engine. Strata's converter (`serve/responses.py`) turns the request into
chat messages and tools, the chat path answers it, and Strata's assembler
writes the Responses items and events. It is stateless:
`previous_response_id` is refused, and the client sends the whole
conversation in `input`.

**Resume.** Switching threads and restarting don't re-read whole histories:
- **Student.** A thread's student prefix cache (~12 KB per token of history,
  ~3 GB at 250K) is parked when another thread takes the student. Recent
  states stay in RAM; every one is also written to
  `STATE_DIR/student-cache/` in the background. Resuming copies it back, and
  the scores are identical to never having left.
- **Flash-Next.** When a lean turn arrives for a thread other than the one
  the engine holds, the engine first saves the outgoing thread's K/V to a
  session file (`STATE_DIR/target-cache/`), then loads the incoming
  thread's. Session files go to `/dev/shm` first, so a switch costs about
  1.3 s to save and 1.4 s to restore, and a single background copier puts
  them on disk for restarts. Shutdown saves the active thread straight to
  disk; run the gateway as its own service with `KillMode=mixed`, or the
  engine dies before that save. Idle parked state can be swapped out on a
  machine short of RAM, which makes resuming a long-idle thread slower:
  measured 38–45 s at 250K, against ~5 minutes without resume. Strata's RAM conversation parking (`TARGET_PARK_MIB`) is off by
  default: it restored a 44K-token conversation in 10 s, and two 250K
  threads don't fit the RAM left beside the experts.
- **Startup.** With `PRELOAD`, the gateway loads the models and resumes the
  most recently used thread at startup. Until then, `GET /readyz` answers 503
  and requests get `503 {"error": {"type": "warming"}}` with `Retry-After`.
- **All caches are disposable.** A missing, stale or corrupt entry only means
  the history is read again.
- **Settings changes keep threads.** On the lean engine, only the ledger
  format and the model name define a thread. New budgets and windows apply
  from the next turn, and caches built under other settings are ignored.

**Stored threads.** Each lean thread is a SQLite file in `STATE_DIR`,
updated incrementally after every turn. Archives are kept until you delete
the thread; caches can be purged at any time. Manage them over HTTP
(`GET /v1/orchid/threads`, `GET /v1/orchid/threads/{thread}/export`,
`DELETE /v1/orchid/threads/{thread}`, `POST /v1/orchid/caches/purge`,
`GET /v1/orchid/storage`) or with
`python -m memory_gateway.specprefill.admin list|export|delete|purge-caches|storage`.

The state directory is owner-only. Use filesystem encryption (LUKS,
fscrypt) if threads must be encrypted at rest.

**Tools.** The lean route accepts OpenAI `tools` (`tool_choice` `auto`; other
choices go native).
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

## In place of Strata's own server

A client written for Strata's server can use the gateway on Strata's port:
- `/health` and `/v1/status` answer in Strata's format. `loaded` follows the
  gateway's readiness; `started`, `engine` and `model` stay the same while it
  runs.
- `ORCHID_SPECPREFILL_MODEL_ALIASES` lists other model names to accept, with
  `*` for any, as Strata accepts.
- Strata's own request fields (`cache_prompt`, `strata_prefix`) reach native
  turns unchanged and do not divert lean ones.
- A Strata config with a `vision` block starts Strata's image encoder before
  the engine, so image requests (served natively) work.

A deployment that runs this way replaces Strata's server unit with
`orchid-specprefill serve --port <Strata's port>` and points the gateway at a
copy of its Strata config. A measured setup for a client that also sends short
chats and pasted documents uses:
- a combined engine: the elastic VRAM patch plus Strata's research top-k MoE
  prefill (`STRATA_RESEARCH_ADAPTIVE_PREFILL=ON`), 15–29% faster prefill than
  the elastic engine alone;
- FP16 K/V and a 262,144-token context;
- `NATIVE_BELOW_TOKENS=32768`, so short and medium conversations, including
  a freshly pasted document, are read whole by native.

Restoring the original unit goes back to Strata's server.

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
