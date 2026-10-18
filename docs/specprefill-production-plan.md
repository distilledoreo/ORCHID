# SpecPrefill production plan

Replaces the old production punchlist (see git history for it). Research
results and the 256K configuration are in
[specprefill-goal.md](specprefill-goal.md). The gateway reference is
[specprefill-gateway.md](specprefill-gateway.md).

**Target:** a user running Flash-Next through the ORCHID gateway gets
SpecPrefill by default without knowing it exists. Long conversations get
faster, answers stay as good as native, nothing breaks when SpecPrefill can't
help, and their conversation data is kept, bounded and under their control.

## Where it stands (2026-10-18)

Works:
- The lean engine serves one session to ~253K tokens on the P100 over HTTP:
  tool calls, streaming, checkpoint restart, idempotent retry.
- It is 1.61–2.10× faster than native cached Flash-Next by depth bin (1.92×
  overall), with 115/122 correct vs native's 104/122 on a real Codex session
  (`specprefill-goal.md`).
- The lossless conversation checkpoint is small: 3.4 MB for 253K tokens.

Blocks making it the default:

| Gap | Today | Where |
|---|---|---|
| Thread identity | Requires `x-memory-project` and `x-memory-thread` headers; plain OpenAI clients get a 400 | `specprefill/http.py` |
| Output length | `max_tokens` above `ANSWER_TOKENS` (default 128) is rejected | `service.py` `validate` |
| Sampling | Only `temperature=0`, `n=1` | `service.py` `validate` |
| Unsupported input | Images, `tool_choice` other than `auto`, extra fields → 400, not native | `service.py` `validate` |
| Thread switch / reboot | Correct but slow: the student cache is VRAM-only and holds one thread; the restart turn at 147K took 482 s (~160 s of it engine start) | `cached_student.py`, `native.py` |
| Flash-Next cache on resume | Rebuilt by prefill; Strata's RAM/file cache tier is off (`--conversation-cache-mib 0`) | `native.py` |
| Storage | Lean checkpoint is one JSON file rewritten every turn; no retention, quota, export or delete controls | `spec_session.py` `_save` |
| Settings changes | The session identity digest includes tuning settings, so changing one strands existing threads | `service.py` |
| Whole-history tasks | "Summarize everything" and large refactors can't be answered from ~10% of history; not detected | — |
| Quality coverage | Proven on exact-recall probes only; not on coding-agent edits or long generations | — |
| Install | Two Python environments plus `PYTHONPATH`, the pinned Strata patch, manual student download | — |
| Operations | No readiness endpoint, queue limits, request timeouts or content-free metrics; no soak test | — |

Host facts that constrain the design:
- 62 GB RAM, of which Flash-Next's streamed experts use ~54 GB. Per-thread
  student state can't live in RAM.
- The student cache is ~12 KB per token (6 full-attention layers × 2 K/V
  heads × 256 dims × K and V × FP16), so ~3 GB at 256K, plus small recurrent
  state.
- The repo drive (`/media/distilledoreo/Storage`) is 100% full (1.7 GB free).
  Fix this before anything writes there.

## Phases

Each phase has a gate; don't start the next until it passes. Every phase
keeps the offline suite green and adds tests for what it adds.

### Phase 1: safe as a default (correctness first)

1. **Native fallback.** Any request the lean engine can't serve goes to
   native Flash-Next with the same response shape, and is logged with a
   reason code. That covers unsupported fields, images, tool modes, a
   SpecPrefill error, or a conversation too short for SpecPrefill to pay off.
   The short-conversation threshold is set by measurement.
2. **Automatic thread identity.** Recognize a thread from the message history
   itself (chained prefix fingerprints), so clients need no headers. Headers
   stay as an optional override. Handle:
   - a continued thread;
   - a fork, where two requests share a prefix and then diverge;
   - an edited or truncated history (rewind to the longest matching prefix);
   - two simultaneous identical threads.
3. **Real-client request shapes.**
   - `max_tokens` up to the model's limit, so the answer budget is separate
     from the generation length;
   - `temperature`/`top_p` sampling;
   - `stop` sequences;
   - `tool_choice` `none`/`required`/named, or native fallback for those.

**Gate:** an unmodified OpenAI client and two real agents (Codex and
OpenCode) run multi-turn sessions through the gateway with no headers and no
errors. Every unsupported case is served natively. Forks, edits and retries
pass offline tests.

**Phase 1 status (2026-10-18): gate passed.** Offline suite: 616 tests, 0 failures, 5 skipped.
- Built: `requests.py` (request normalization and native reasons), automatic
  thread matching, content-based retry replay, native fallback through
  Strata's OpenAI path (`NativeResources.native`), sampling, stop strings,
  long `max_tokens`, `include_usage`, and `POST /v1/responses` for Codex
  (Codex 0.162 refuses `wire_api = "chat"`). 15 offline test functions in
  `tests/test_specprefill_gateway_auto.py`.
- Short-conversation threshold, measured live (fresh single-turn chats,
  32-token answers, both routes without thinking, all answers correct):

  | Prompt tokens | Lean | Native |
  |---:|---:|---:|
  | 63 | 3.22 s | 2.98 s |
  | ~230 | 4.94 s | 4.35 s |
  | ~550 | 6.32 s | 5.79 s |
  | ~1,070 | 4.11 s | 8.15 s |
  | ~2,100 | 4.34 s | 13.12 s |
  | ~4,130 | 5.70 s | 24.45 s |

  The crossover is the lean engine's 1,024-token query split: below it lean
  sends the whole message anyway. So `NATIVE_BELOW_TOKENS` defaults to 1024.
  (With native's default thinking on, native took 4.8–26.3 s at the same
  sizes.)
- Gate runs (live gateway, 256K configuration, no headers):
  - **OpenAI Python SDK 3.35.1**, unmodified. All six steps correct:
    - a plain turn (5.7 s) and a streamed turn with `include_usage` (3.7 s);
    - a tool call (`finish_reason: tool_calls`, correct arguments) and the
      answer from its tool result;
    - a retry of the same request, replayed in 0.01 s with the identical
      answer;
    - a sampled turn with `stop`.

    Adding `tools` mid-conversation started a new thread, as designed (the
    tools are part of the pinned system text). Dropping them continued the
    original thread.
  - **OpenCode 1.18.35** (`@ai-sdk/openai-compatible`), two turns:
    - Turn 1 ran the tests, read `calc.py`, fixed the bug and reran the tests
      to green.
    - Turn 2 continued the session and added a test; all 3 pass.
    - All 10 agent requests stayed on one lean thread (21 ledger entries),
      with no forks and no native fallbacks.
  - **Codex CLI 0.162.1** over `POST /v1/responses` (`wire_api = "responses"`),
    two turns:
    - Turn 1 found and fixed the bug, with tests green.
    - Turn 2 (`codex exec resume --last`) added a test; all 3 pass.
    - All 15 requests (13 tool-call turns, 2 final answers) stayed on one
      lean thread (32 ledger entries), with no forks, no fallbacks and no
      HTTP errors.
  - **Unsupported cases go native** (images, forced or `none` tool choice,
    `logprobs`, JSON `response_format`, lean failure). Covered offline, and
    live with `tool_choice: none` (the threshold runs above). **Forks, edits,
    retries and re-serialized replies:** offline tests.
- Found while testing:
  - With tools, the lean path buffered the whole answer, so agents saw
    nothing until generation ended. Text now streams up to the first
    `<tool_call>`, and the calls are parsed at the end.
  - systemd-oomd kills the engine while it pins its 47 GB of experts (user
    slice pressure over 50% for 20 s). It also killed the Claude app twice,
    and FRIDAY's Strata service three times this week.
  - Fix for testing: run the gateway as its own user service. On this
    machine it also needs the user slice's oomd limit raised, a root change
    for the user to make (Phase 5 install docs).
  - Fixed: the retry fingerprint ignored fields like `chat_template_kwargs`,
    so a changed request could replay an old answer. It now covers every
    field except `stream`/`stream_options`.

### Phase 2: fast resume

1. **Student cache on disk, per thread.** When switching away or going idle,
   save the student's K/V and recurrent state, and load it back on resume.
   Keep these files in a size-capped least-recently-used cache. A missing or
   corrupt file falls back to a rebuild.
2. **Flash-Next cache on resume.** Test Strata's RAM/file cache tier for the
   lean target's prompt. If it works, enable it within the same cache budget.
   If not, rebuild it in the background on thread open.
3. **Warm-up and readiness.** A readiness endpoint, engine warm-up at startup,
   a warm-up on thread open, and a clear "warming" response instead of a hung
   request.
4. **Settings changes keep threads.** Split the session identity into the
   conversation (kept) and derived caches (rebuilt when settings change).

**Gate, measured on the 256K session:**
- Switching between two ~250K threads and back costs < 10 s per switch,
  excluding engine start.
- After a reboot, the first answer comes < 10 s after the engine is ready.
- Answers after a resume match those without one.

### Phase 3: storage and user control

1. **Incremental lean storage.** Move the lean checkpoint from a per-turn
   JSON rewrite to the incremental SQLite storage the compacting engine
   already uses, with a migration from existing checkpoints.
2. **Data classes.**
   - Archives (the lossless conversation) are user data: never deleted
     automatically.
   - Caches (student and Flash-Next state) are disposable, under one byte
     budget.
   - Optionally compress archives of threads idle for N days.
3. **Controls, via CLI and HTTP:**
   - list threads (size, turns, last used);
   - export a thread (JSONL);
   - delete a thread (archive, caches, ledger; space actually reclaimed);
   - purge caches;
   - set a quota, with a warning at 80%.
4. **Safety.**
   - Owner-only file permissions; optional encryption at rest.
   - Disk full fails closed: no corruption, clear error, keep serving
     natively.
   - Power-loss safe (tested by killing mid-write).

**Gate:**
- Offline tests cover every control, the quota, disk full and a kill
  mid-write.
- A deleted thread leaves no recoverable content in the state directory.

### Phase 4: quality beyond exact recall

1. **Whole-history tasks.** Detect requests that need the whole conversation
   ("summarize", "all changes", "review everything"). Route them to a larger
   evidence budget, or to native when native fits.
2. **Coding-agent eval.** Replay real agent sessions where the next turn is a
   code edit or a long generation. Compare SpecPrefill with native on whether
   the edit is right (applies, tests pass, or matches the recorded edit), not
   only on exact quotes.
3. **Long outputs.** Answers of 1–4K tokens with tool loops, compared with
   native.

**Gate:**
- Within 1 field (or ≤2 percentage points) of native on the coding eval and
  on long outputs.
- Whole-history tasks no worse than native.
- Still faster than native end to end at every depth bin.

### Phase 5: install and auto-configuration

1. **One environment.** The gateway, student runtime and Strata frontend
   install together, with no `PYTHONPATH`.
2. **Auto-configuration.** Detect the GPU and VRAM and pick the settings
   (reserve, Flash-Next window, sparse keys and rows, K/V type) from a
   measured table. An unfit card, or a missing student, runs native with a
   one-line notice.
3. **Student model.** A consented one-time download with a checksum, or a
   clear instruction. Never automatic.
4. **Optional fast kernels.** `flash-linear-attention` and `causal_conv1d`,
   used only if they import and pass an equivalence check on the card.

**Gate:** on a clean user account, one documented install command plus the
existing Flash-Next setup gives a working gateway with SpecPrefill on, and
native fallback when the student is absent.

### Phase 6: operations and launch

1. **Load limits.** Queue limit, per-request timeout, cancellation on client
   disconnect, and a clear "busy" error.
2. **Metrics without content:** per-turn timing split, fallback reasons,
   cache hit rates and storage use, all with no conversation text in logs.
3. **Versioning.** Versioned checkpoint and cache formats with migration
   tests from every released format.
4. **Event-count soak.** Scripted on real agent sessions: ≥1,500 turns
   across ≥10 threads, with ≥100 thread switches, ≥10 clean restarts and 2
   hard kills mid-turn. Track VRAM, RAM and disk over the run (~4–5 hours).
   Soak risks scale with events, not wall time.
5. **Fake-clock tests** for time-based policies (idle-archive compression,
   cache expiry). Tests advance an injectable clock instead of waiting.
6. **User documentation.** One paragraph on what changes and how to turn it
   off, plus the storage controls.
7. **Default switch.** `ORCHID_SPECPREFILL_ENGINE=lean` becomes the
   default, with `native` as a single-setting opt-out.

**Gate:**
- The soak has zero errors and zero data loss.
- VRAM and RAM stay flat across the run, and disk stays within the caps.
- No speed regression versus Phase 4.
- The fake-clock tests pass.
- The docs cover install, opt-out and storage.

## Out of scope

- Multimodal inputs: served natively.
- Concurrency across sessions beyond the single serialized engine: a second
  GPU would add throughput, not single-session latency
  (`specprefill-goal.md`).
- Further speed work on Flash-Next's per-call prefill floor (~5 s at 256K):
  a separate engine project.
