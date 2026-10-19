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

**Side test: adaptive prefill stacks with SpecPrefill (2026-10-18).** This is
not part of the experiments, which all use the stock Strata binary. The
research build (`~/AI/state-projection-strata-expert`,
`STRATA_RESEARCH_ADAPTIVE_PREFILL=ON`: 2–3 routed experts at mass 0.35 for
prefill chunks of 64+ tokens; decode unchanged) ran the same fresh single-turn
chats, native without thinking, all answers correct:

| Prompt tokens | Lean stock → adaptive | Native stock → adaptive | Lean vs native, stock / adaptive |
|---:|---|---|---|
| ~1.1K | 4.38 → 3.60 s (−18%) | 9.26 → 9.31 s (0%) | 2.1× / 2.6× |
| ~4.1K | 4.86 → 3.70 s (−24%) | 25.29 → 22.65 s (−10%) | 5.2× / 6.1× |
| ~8.2K | 6.16 → 4.87 s (−21%) | 45.43 → 40.57 s (−11%) | 7.4× / 8.3× |
| ~16.4K | 9.00 → 7.75 s (−14%) | 88.31 → 81.08 s (−8%) | 9.8× / 10.5× |

The gains stack, and adaptive prefill helps lean about twice as much as
native: lean's turns are mostly Flash-Next's fixed per-call prefill cost,
which fewer experts per token cuts.

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

**Phase 2 status (2026-10-18): gate passed (offline suite 623 tests, 0 failures, 5 skipped), with the user's sign-off, for
recently used threads; the idle case is a documented RAM limit of this
machine.**

Built:
- Per-thread student parking (`parking.py`: a RAM LRU tier plus background
  disk files, memory-mapped on load). The resumed scores are identical to
  never leaving (maximum difference 0.0 on a 40K-token thread).
- Flash-Next session files on every thread switch: the outgoing thread is
  saved to `/dev/shm`, one background copier puts it on disk, and the
  incoming thread is restored. Hot files are evicted only once copied and
  never while in use.
- Shutdown save of the active thread, and warm-up of the most recent thread
  at startup (`PRELOAD`, `/readyz`, `503 warming` with `Retry-After`).
- Lean threads survive settings changes.

Measured on two ~250K-token threads (204K student tokens, 45K Flash-Next
tokens each). Resume overhead = student + Flash-Next save + restore:

| Case | Resume overhead | Whole turn |
|---|---|---|
| Switch back after ~1 turn (B 120, 123, 124) | **4.5–5.0 s** | 14.9–17.2 s |
| Switch back after minutes idle (A 120 after a 20-min build, A 123) | 38–45 s | 52–55 s |
| Gateway restart, first answer, warmed thread | ~0 | 13.7 s |
| Gateway restart, first answer, other thread | 15.5 s | 26.1 s |
| Reboot emulation (`/dev/shm` cleared), warmed thread | ~0 | **8.55 s** after ready |
| Reboot emulation, other thread (from disk) | 18 s | 25.1 s |
| No resume (re-reading the history) | — | 286–297 s |

- **Answers after a resume match.** Flash-Next save → another conversation
  → restore reproduced the live follow-up token for token in 5/5 trials
  (9.5K–12.6K-token prompts from the user's session); a cold re-read
  matched too. Two independent 250K builds of thread A were identical for
  121 turns.
- **The idle case is RAM-bound.** With 47 GB of experts pinned on a 62 GB
  machine, the kernel swaps out idle parked state (swap use rose from 3 to
  7 GB during the run), and Strata refuses a restore without 1.4 GB plus its
  floor free. The restore floor was lowered to 1024 MiB
  (`TARGET_RESTORE_FLOOR_MIB`), and memory refusals no longer delete the
  file.
- **Bugs found and fixed by the live runs:**
  - parking evicted the state about to be resumed;
  - a session restore reset the just-resumed student;
  - the hot tier's trim raced its disk copy;
  - `systemctl stop` killed the engine before the shutdown save (units need
    `KillMode=mixed`);
  - restores refused for lack of RAM deleted the file.
- Phase 3 must also delete a thread's `/dev/shm` hot file and student cache
  file.

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

**Phase 3 status (2026-10-18): gate passed.** Offline suite: 635 tests, 0 failures, 5 skipped.
- **Incremental lean storage** (`session_store.py`): one SQLite file per
  thread (WAL, `synchronous=FULL`), one transaction per turn.
  - Sources are inserted once; the student and Flash-Next token lists grow by
    appended chunks, rewritten only on a rebuild, rollout or rolled-back turn.
  - A `.json` checkpoint migrates on first open and is kept as
    `.json.migrated`.
  - At a ~270K-token synthetic session, a checkpoint takes 91.5 ms vs 304 ms
    for the JSON rewrite.
- **Data classes.** Archives (session file, ledger rows, the gateway's event
  store) are kept until the user deletes the thread. Caches (parked student
  states 16 GiB on disk / 4 GiB in RAM, Flash-Next session files 16 GiB on
  disk / 4 GiB in `/dev/shm`) stay within their budgets and can be purged.
  Compressing idle archives was left out: it's optional, and a 250K thread's
  archive is only ~7 MB.
- **Controls** over HTTP (`/v1/orchid/threads`, `.../export`,
  `DELETE .../{thread}`, `/v1/orchid/caches/purge`, `/v1/orchid/storage`)
  and a CLI, `python -m memory_gateway.specprefill.admin
  list|export|delete|purge-caches|storage`. The quota is `STATE_QUOTA_MIB`:
  past 80% responses carry `orchid.storage_warning`, and at 100% turns go
  native ("storage quota").
- **Delete** removes the session file, both caches, the hot file, the ledger
  rows and every event-store row derived from the thread (events, capsules,
  memories and their full-text index, raw-recall text and embeddings, jobs,
  runs, usage). `secure_delete` and a WAL truncate keep freed pages from
  holding the text.
- **Safety.**
  - The state directory is owner-only (0700).
  - Below `MIN_FREE_DISK_MIB` free, turns go native ("disk full").
  - A write that fails (out of space, I/O) is answered natively, and the
    in-memory session is dropped and reloads from its last checkpoint.
  - Encryption at rest is left to the filesystem (LUKS or fscrypt for the
    state directory, documented); application-level encryption would need
    key management the gateway doesn't have.
- **Gate tests** (`tests/test_specprefill_controls.py`,
  `tests/test_specprefill_session_store.py`):
  - every control, including the CLI;
  - the quota warning and the full quota;
  - disk full, and out of space in the middle of a write;
  - a `kill -9` of a process mid-write, 3 times, reopening each time to a
    consistent state;
  - a crash inside a commit;
  - after deleting a thread holding a unique secret, no file anywhere in the
    test's state, event-store and hot directories contains it.

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

**Phase 4 status (2026-10-18): in progress, gate not yet passed.**

What is built:
- Whole-history requests (`BROAD` in `spec_session.py`) get a larger evidence
  budget (`broad_tokens`), shown for that answer only (not kept in the target
  transcript).
- Grounded edits (`grounding.py`): when an answer starts changing a file, the
  target drafts about 12 lines, the draft's hunk location and context lines
  (or a new file's imports) are found in the archive, and only that region
  (±40 lines), the newest earlier patch to the file and the last 1,024 tokens
  are shown before the change is written again. Per file, up to 3 files.
- Fresh tool results: tool messages since the model's last message (what it
  just asked for, e.g. a file read) are delivered in full, up to 4,096 tokens
  per turn, instead of trimmed like other input.
- One-message turns: the turn's excerpts and question are one user message.
  As two, Flash-Next often replied with nothing when the excerpts ended on a
  shell command: 65 of 79 filler turns in the first agent-eval replay were
  empty (native: 0).

Replay measurements (a recorded Codex session, 98 turns to 200K tokens,
11 edit and 4 new-file probes, blind side-by-side grade by a separate Sonnet
agent that never saw which arm was which):

| Variant | Acceptable (correct + ½ partial) | Native | Speed by depth bin vs native |
|---|---:|---:|---|
| Sparse only (new_rate 0.06) | 6.7% (0/2/13) | 56.7–60% | 1.85 / 1.85 / 1.42 / 0.61× |
| new_rate 0.25 | 6.7% (0/2/13) | 56.7% | 1.37 / 1.72 / 1.31 / 0.59× |
| Grounding v1 (12K fetch, kept) + one-message turns | 30% (3/3/9) | 60% | 1.03 / 1.00 / 0.90 / 0.38× |

Findings: a larger budget does not fix edits; the first grounding doubled
edit quality but cost the speed (Flash-Next prefills at about 170 tokens/s on
the P100, so 12K fetched tokens cost about 70 s, and kept evidence forced
100–150 s rebuilds). That led to region grounding and ephemeral evidence above.
The same first version also slowed the matched 256K benchmark to 1.09×,
because 12 of its questions quote a `git diff` command and the request-side
"patch" trigger fetched 12K tokens on each; that trigger is gone.

**Method change (user decision, 2026-10-18):** replays ask Flash-Next for the
next edit of someone else's session with no tools, which no real agent does,
so they are dropped as the coding measurement. The coding eval is now a live
agent loop (`live_tasks.py`, `live_agent_eval.py`): Codex CLI fixes planted
one-line bugs in a copy of this repository, one task after another in one
thread so its context grows past 150K, through the gateway (lean) and through
native Strata Flash-Next at 256K. A task passes when its test file passes and no
test was edited. The gate compares tasks passed and wall time per depth bin.

Live agent eval, first results (2026-10-19). Codex 0.162.1, 11 planted one-line
bugs in a copy of this repository, thinking on in both arms. Deep variant: the
agent first reads about 125K tokens of non-task code, so the tasks run at 128K+.

| Run | Tasks passed | Notes |
|---|---:|---|
| lean, deep (Codex never compacts) | 9 / 11 | tasks 7 and 8 hit the 40-minute limit |
| native, deep (compacts near 230K) | 2 / 2 | stopped by the user after task 1 |

Seconds per model call (input ready to response; tool time excluded), by
conversation depth (transcript characters, calibrated on native's token counts):

| Depth | lean | native |
|---|---:|---:|
| 0–64K | 26.2 | 18.4 |
| 64–128K | 25.5 | 18.2 |
| 128–192K | 30.6 | not reached |
| 192K+ | 44.5 | not reached |

Finding: in a tool-heavy agent loop each step's new input is a small tool
result the model needs in full, so selection has little to cut, while lean adds
a student pass (1–4 s), about 0.7–0.9K excerpt tokens per call, window rebuilds,
and more generated tokens per call (about 300 vs 200). Flash-Next's prefill
barely slows with depth on the P100 (about 125 tokens/s at 222K), so native's
cached incremental prefill is hard to beat there. SpecPrefill's wins are turns
with large new input and conversations past native's 256K window.

VRAM limit for a combined design (engine logs): fixed about 5.6 GB, Flash-Next
K/V about 26 KB per token (1.2 GB at 48K, 6.4 GB at 256K), a 256K student about
8 GB, a useful expert cache about 1 GB: a 256K native window and a resident 256K
student need about 21 GB of the card's 15.9. Strata's `--kv-grow` (K/V sized to
use) and `--vram-elastic` (VRAM handed to other programs at runtime) each exist
but exclude each other. Open decision (user): how agents and lean share the GPU.

Elastic engine (2026-10-19, an isolated Strata copy; the installed Strata is unchanged).
A new engine command, `VRAM <reserve_mib> [drop]`, for engines with the elastic
K/V (`--kv-grow`): it frees the cache's spare chunks, the K/V past the live
session, then whole chunks of expert slots below the prompt loan, and later maps
and refills them. Measured on the P100: 7.1 GB freed in 0.41 s (a 7 GB PyTorch
allocation then succeeds), refilled in 0.70 s; the live session survives. With it,
the gateway (`ORCHID_SPECPREFILL_ELASTIC_ENGINE`) runs one Flash-Next at a 256K
elastic window: requests with tools are answered natively from the cached
conversation until it reaches `AGENT_NATIVE_TOKENS` (200K), while lean turns
park the native conversation, free 8 GB and load the student (0.4–0.6 s; back to
native about 2.4 s, mostly expert refill). Turns whose new input is only fresh
tool results skip the student (verbatim); `FRESH_MODE=intent` lets the student
read long tool results for what the model's last step was looking for.

Matched deep live eval (thinking on, Codex compacts at 230K for native only):

| Arm | Passed | Notes |
|---|---:|---|
| Arm | Passed | Warm-up | s per call | 0K+ / 64K+ / 128K+ / 192K+ (s per call) |
|---|---:|---:|---:|---|
| native Strata (baseline) | 10 / 11 | 1,060 s | 28.3 | 28.9 / 25.3 / 15.8 / 43.9 |
| elastic gateway | 11 / 11 | 1,470 s | 26.3 | 19.4 / 17.8 / 19.0 / 36.0 |
| intent-guided lean (`FRESH_MODE=intent`) | 10 / 11 | 648 s | 32.1 | 29.3 / 23.6 / 29.2 / 48.4 |

Seconds per model call run from the call's input being ready to its response
(tool time excluded); depth is conversation characters x 0.246 + 4.4K tokens
(calibrated on native before compaction). Codex does not pin sampling, so the
arms' paths differ (197, 270 and 210 calls): per-call time and pass rate are the
comparisons, not total time. Native's 192K+ calls include Codex compacting near
230K; the gateway never compacts (it goes lean at 200K). Findings:
- Elastic is at least native's quality (it passed the task native timed out on)
  and about 7% faster per call overall (about 30% below 128K: the elastic K/V
  leaves room for 2.8x the experts in VRAM). It is about 20% slower at 128–192K:
  its 1,280-token prompt chunk prefills at 110 tokens/s against native's 161;
  a larger chunk with the VRAM command shrinking the prompt loan is the fix.
- Intent mode read the warm-up's 12 files (all to their last line) in 39% less
  time than native, but after the switch to lean its debugging calls were slower;
  it stays opt-in.

Final matched 256K benchmark on this code (lean arm, case unchanged):
2.03 / 2.15 / 1.99 / 1.63x by depth bin, 1.94x overall; 117/122 correct vs
native 104/122 (artifacts/strata-specprefill/log256k-final.json).

**Phase 4 gate (2026-10-19):**
- Coding eval within 1 field (or ≤2 points) of native: the live agent eval
  (the user's replacement for replays): elastic 11/11, native 10/11. Met.
- Long outputs: the agent tasks' answers (reasoning plus calls and patches, up
  to 1–4K tokens per call) are part of that eval. Met with it.
- Whole-history tasks no worse than native: they are answered natively when
  the conversation fits (identical), and past native's window native cannot
  answer without compaction. Met by routing.
- Faster than native end to end at every depth bin: the matched benchmark, yes
  at every bin (1.63–2.15x). Agent loops: per call faster overall and in three of
  four bins, slower at 128–192K (prefill chunk, above).

**Phase 4 status: gate passed with a documented limit (user sign-off,
2026-10-19).** Limit: agent loops on the elastic engine are about 20% slower per
call than native at 128–192K. Follow-up: a larger prompt chunk, with the VRAM
command shrinking the prompt loan when the student needs room. Offline suite:
654 passed, 5 skipped.

Gateway bugs the live eval found, all fixed with tests:
- Tool calls used the old Qwen JSON format; Flash-Next's template uses
  `<tool_call><function=…><parameter=…>`, so the model wrote broken calls. The
  gateway now renders tools with Strata's own template and parses calls as Strata.
- Thinking was always off; it now follows the request (on by default, as
  native), with reasoning streamed as `reasoning_content` and Responses reasoning
  items. The lean prompt frame equals the native template byte for byte (test).
- Usage reported Flash-Next's trimmed prompt; it now reports the whole
  conversation (clients size their context from it), with
  `orchid.target_prompt_tokens` for what was read.
- Every tool step of a request containing "summarize" was a whole-history turn
  (12–16K uncached evidence per call); now only a turn answering the user's own
  message can be, and negations ("without summarizing") don't count.
- A reply the client never received (a cancelled turn) forked the thread and
  re-ingested everything; such replies are now dropped and the thread continues.
- The matched 256K benchmark, rerun on this code (before the thinking change,
  which the harness doesn't use): 115/122 vs native 104/122, 2.01 / 2.12 / 1.96 /
  1.24× by depth bin. The 192K+ bin was slowed by one quote question containing
  `summary(` that the whole-history detector matched; quoted text no longer counts.

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
