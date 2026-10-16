# ORCHID Core Goal Plan

Last updated: 2026-08-28

## North Star

Make a deliberately small main-model context feel effectively unbounded by moving older information into lossless long-term storage, organizing it continuously on a secondary GPU, and retrieving the right memory when needed—without ever pausing a foreground request for compaction.

ORCHID succeeds first when it provides the experience above. Beating traditional compaction on API cost, total VRAM, and cache behavior remains the final optimization and release target, but those metrics must not distort the core architecture.

## Non-Negotiable Product Contract

1. **Raw history is never destroyed.** Every accepted event remains in an append-only journal with a stable ID and content hash.
2. **Foreground never waits for memory maintenance.** Selector, canonicalizer, consolidation, indexing, and verification are background work only.
3. **The selector stays frozen.** Preserve its current model, prompt, schema, chunking, serial execution, and organizing role. It organizes new material; it is not treated as a disposable filter.
4. **Each new event is admitted once.** A large raw event may become multiple deterministic source spans, but every resulting source item is delivered once. Crash replays must reuse validated work by exact input hash; normal operation must not repay old history through the model pipeline.
5. **The main prompt stays bounded.** It contains authoritative instructions, compact current state, relevant retrieved memory, and a recent raw tail—not the whole journal.
6. **Memory remains recoverable.** Every semantic memory points back to raw source events so a missed detail can be retrieved rather than guessed.
7. **The intended deployment remains two-tier.** The main model runs on the primary GPU; the frozen selector and related small-model work run serially on the smaller secondary GPU.

## Initial Measurable Target

Use the FreetoShop workload as the first proof with a declared **32k foreground target** and **48k emergency ceiling**, both configurable in the sealed manifest.

A core-goal run passes only when:

- no foreground request awaits a compaction job (`compaction_wait_ms == 0` for every request);
- assembled foreground context never exceeds the emergency ceiling;
- the workload grows beyond at least five times the 32k target without prompt growth proportional to total history;
- every raw event remains retrievable by ID and hash;
- hidden task quality is non-inferior to traditional compaction;
- frozen recall checks preserve current facts, goals, decisions, constraints, blockers, negative knowledge, and exact identifiers;
- the background queue drains faster than the measured arrival rate with enough headroom for the p99 job duration.

If an adversarial input burst exceeds the declared service envelope, the run must report an SLO violation. It must not silently drop history, fabricate memory, or claim that an unavoidable emergency fallback is “zero pause.”

## Minimal Runtime Architecture

### 1. Lossless journal

The journal is the source of truth. Capsules and indexes are disposable materialized views. A bad summary can be rebuilt from raw events; raw events are never rebuilt from a summary.

### 2. Hot raw tail

Keep the newest unresolved interaction in the foreground prompt verbatim. Size it by tokens and protocol boundaries, preserving tool-call/result pairs and authoritative system/developer messages.

### 3. Incremental organizer

Feed only the monotonic delta after the organizer watermark to the frozen selector. Persist the validated selector result by input hash so retries are free and exact. The selector may group and relate events without deleting their journal representation.

### 4. Stable memory records

Represent durable memory as keyed records such as goals, decisions, constraints, current state, blockers, resolved facts, files/symbols, and open questions. Records carry stable IDs, status, recency, and raw source references.

Updating memory is a patch operation: upsert changed records, supersede obsolete records, and leave untouched records untouched. Do not recursively rewrite the entire accumulated capsule.

### 5. Local retrieval

Before each foreground request, use a bounded local hybrid retrieval step over memory records and raw-source metadata. Build the query from the current user turn, active goal, touched files/entities, and unresolved blockers. Retrieval must not require a foreground model call.

### 6. Bounded foreground assembly

Assemble the prompt in stable-to-volatile order:

1. system/developer instructions;
2. root task and current goal;
3. compact current-state records;
4. retrieved memory and raw evidence;
5. recent raw tail;
6. current conversation turn.

Use explicit budgets for each section. When the background worker lags, retain unprocessed events in the hot tail or emergency spill allowance; never wait synchronously for a capsule.

### 7. Exception-driven expensive work

Deterministic reducers handle exact facts such as file paths, commands, test results, IDs, and unchanged records. Model consolidation handles semantic conflicts, supersession, and ambiguity. Main-model background work must be preemptible/idle-only and can never contend with a foreground request without being counted as an SLO failure.

## Ordered Implementation Plan

### Phase 0 — Freeze the reset

- [x] Record the core goal and selector invariants in this canonical roadmap.
- [x] Add a sealed core-goal manifest containing context targets, workload, selector contract hash, and pass/fail thresholds.
- [x] Align runtime configuration with frozen decisions; the canonicalizer `KEEP` verdict and corrected launcher now both enable it.
- [x] Mark existing Pareto, V2, cache, and VRAM machinery as supporting evidence rather than the runtime design authority.

**Exit gate: passed.** `tools/core_goal_status.py check` prints the contract, frozen hashes, exact-once replay counts, and unpaid blockers without provider calls.

### Phase 1 — Make background work truly incremental

- [x] Use active-capsule coverage as the organizer watermark and persist validated per-delta stage results by exact input hash.
- [x] Prove frozen-plan admission once per raw event and delivery once per deterministic source item (199 raw events, 216 source items, 63 chunks).
- [x] Prove a selector restart reuses every completed chunk and sends only the unfinished chunk again.
- [x] Make stage-result cache read/write failures fail closed instead of silently issuing potentially duplicate paid calls.
- [x] Add stable-record patch responses and deterministic carry-forward in V2 record mode.
- [x] Omit prior capsule prose and evidence from record-mode consolidation; send at most 16 compact, relevant prior record headers plus changed/new sources.
- [x] Coalesce queued event-watermark deltas while preserving immutable running snapshots and protocol-safe event boundaries.
- [x] Retain the current canonicalizer and restrict it to selected source items from the current delta, with exact-retry reuse.
- [ ] Make the accepted stable-record envelope the primary runtime path; V2 admission is not yet good enough to retire the legacy whole-capsule path.
- [ ] Prove byte-identical final memory after a crash at every pipeline boundary, not only selector chunk recovery.

**Exit gate:** the 199-event frozen replay completes with complete coverage, no duplicate semantic processing, bounded per-event work, and identical output after crash/retry replay.

### Phase 2 — Enforce bounded, non-blocking foreground assembly

- [x] Align runtime defaults and gateway assembly with the sealed 32k target and 48k emergency ceiling.
- [x] Prove foreground execution does not await a running compaction worker; a second request completes while background maintenance is deliberately blocked.
- [x] Preserve authoritative root-task and tool-call/result continuity while suppressing covered history; local retrieval remains bounded and foreground-only.
- [x] Emit per-request and debug component tokens, compaction wait time, organizer lag, target/ceiling state, assembly time, and emergency-spill usage.
- [x] Add a six-window synthetic run (more than 5×32k of journal history) that checks every raw ID/hash at every bounded assembly checkpoint.

**Exit gate: passed for the unpaid deterministic harness.** Live two-GPU service-envelope and failure-injection proof remains Phase 4.

### Phase 3 — Prove memory quality before optimizing it

- [ ] Build frozen checkpoint questions for old facts, changed facts, decisions, blockers, negative knowledge, exact identifiers, and current intent.
- [ ] Compare full-source and projected-source inputs through the identical frozen selector and downstream pipeline.
- [ ] Require projected input to be semantically non-inferior before keeping its token savings.
- [ ] Exercise semantic “page faults”: retrieve original raw evidence when a compact record is insufficient.
- [ ] Run the hidden task evaluator at multiple history depths, not only at the final checkpoint.
- [ ] Keep V2 in shadow until every sealed generation admits; make citation excerpts deterministic from stored sources rather than asking a model to reproduce formatting exactly.

**Exit gate:** ORCHID is non-inferior to traditional compaction on every required hidden-quality and frozen-recall category.

### Phase 4 — Establish the no-pause service envelope

- [ ] Measure arrival tokens/second, selector service tokens/second, queue age, and p50/p95/p99 job duration on the secondary GPU.
- [ ] Trigger work early enough that measured headroom covers p99 completion plus a burst margin.
- [ ] Stress bursts, provider failures, selector restarts, and stale-job recovery.
- [ ] Demonstrate that foreground execution remains independent while the worker catches up.

**Exit gate:** background service is at least 1.5× measured average arrival, the sealed burst trace stays inside the emergency ceiling, and all injected worker failures recover without a foreground compaction wait.

### Phase 5 — Optimize economics without changing behavior

- [ ] Generate a real full-pipeline token ledger: selector, canonicalizer, consolidator, verifier, foreground, cache reads/writes, outputs, and retries.
- [ ] Require background provider work to scale with changed information rather than total history.
- [ ] Keep stable prompt/memory pages content-addressed so unchanged prefixes can be cached.
- [ ] Invoke expensive semantic consolidation only for changed or conflicting records.
- [ ] Prove price-independent API savings when rates are unknown by reducing every billed token class; otherwise use a sealed measured rate card.
- [ ] Record local selector latency/energy separately from cloud/API spend.

**Exit gate:** ORCHID is cheaper per accepted milestone or componentwise cheaper in every billed token class while preserving the Phase 3 quality result.

### Phase 6 — Measure the intended two-GPU deployment

- [ ] Support stable device identities and both NVIDIA and AMD VRAM sampling.
- [ ] Measure exact arm windows with multiple samples, coverage duration, peak residency, and byte-seconds; one sample is never “complete.”
- [ ] Run a local main-model hardware track for total VRAM. Do not claim cloud Agnes VRAM from local selector measurements.
- [ ] Verify that main-GPU KV savings exceed the secondary model footprint for total-VRAM dominance.
- [ ] Counterbalance arm order and use a predeclared fixed duration; exclude warm-up and shutdown transitions.

**Exit gate:** complete isolated traces show lower main-GPU context/KV usage and either lower total two-GPU VRAM or an explicitly reported remaining tradeoff.

### Phase 7 — Paid paired proof

- [ ] Run one short paid smoke pair only after Phases 1–4 pass.
- [ ] Audit raw quality, latency, token, cache, and failure telemetry before repetitions.
- [ ] Run three sealed pairs with counterbalanced order and identical fixed workloads.
- [ ] Require every run—not merely the median—to pass the core-goal gates.
- [ ] Publish core-goal results separately from optional Pareto metrics.

## Work We Are Explicitly Stopping

- No selector-removal or selector-configuration experiments.
- No more full-history model replays presented as an incremental architecture.
- No new pipeline stage without evidence that it removes more work than it adds.
- No paid A/B while the unpaid core-quality or no-pause gates are red.
- No treating a compact capsule as the only copy of history.
- No using message counts, files changed, or model activity as a quality score.
- No allowing cache, pricing, or perfect VRAM instrumentation to delay proving the core experience.

## Immediate Next Slice

1. Remove the remaining strict-V2 admission failures so stable records and patch-only consolidation can become the core runtime path.
2. Add full-pipeline crash injection after selector, canonicalizer, consolidation, admission, and publication; require byte-identical recovery without repaying validated stages.
3. Run the frozen semantic checkpoint suite at multiple history depths before doing any paid work.
4. Measure the secondary-GPU service envelope only after the unpaid semantic gates pass.

## Progress Log

### 2026-08-28 — Core-goal reset

- Reframed ORCHID around the actual product: bounded active context, lossless external memory, continuous secondary-GPU organization, retrieval, and zero foreground compaction pauses.
- Preserved the selector as a frozen organizer.
- Demoted strict Pareto dominance from architecture driver to final optimization/release proof.
- Kept the useful completed foundations: append-only event storage, exact-once foreground fixes, stable prefix assembly, fail-open V2 shadowing, direct latency telemetry, hidden-evaluation schemas, conservative API accounting, and fail-closed missing-metric behavior.
- Confirmed the current full suite passes: 341 passed, 4 skipped.
- Identified the next implementation target as delta-only organization and patch-based memory maintenance, not another benchmark-harness expansion.

### 2026-08-28 — First core implementation slice

- Added and sealed `artifacts/core_goal/CORE_GOAL_MANIFEST.json`, including the 32k target, 48k emergency ceiling, selector contract/golden hashes, canonicalizer `KEEP` evidence, frozen-trace hash, and two-GPU deployment contract.
- Added `tools/core_goal_status.py`; its unpaid gate verifies the checked-in manifest and frozen evidence without model calls and keeps the paid benchmark blocked while runtime gates remain open.
- Aligned the corrected FreetoShop launcher and manifest with the frozen canonicalizer `KEEP` result instead of forcibly bypassing the stage.
- Added a frozen exact-once organizer report. The sealed trace contains 199 unique raw events, deterministically expands to 216 source items, and schedules 63 selector chunks with no duplicate, missing, unknown, or reordered deliveries.
- Confirmed the existing SQLite stage-result cache is the durable per-delta ledger, then made cache read/write failures fail closed. Added restart coverage proving a completed selector chunk is reused after a later chunk fails.
- Added the first delta-only stable-record consolidation protocol. In record mode, ORCHID omits the old capsule and prior evidence, supplies at most 16 compact relevant record headers, returns only new/changed patches, and carries untouched records forward deterministically during admission.
- Added deterministic evidence canonicalization: formatting-only differences (JSON escapes, ANSI styling, whitespace, smart quotes, and case) are mapped back to exact stored source slices, and uniquely misattributed quotes are relinked to their true source. Fuzzy/paraphrase repair and ambiguous relinking remain forbidden. The existing 32-generation consolidator checkpoint replay repaired 72 citations across 20 generations and improved strict admission from 13/32 to 14/32; 18 generations still correctly fail, so V2 remains shadow-only.
- Fixed a consolidator parsing bug where a retire entry could overwrite the main rendered content.
- Aligned the actual `RuntimeConfig` defaults and gateway assembler with the sealed 32k/48k contract; the earlier 22k/64k runtime split is gone.
- Added foreground invariants and telemetry for exact component tokens, target/ceiling breaches, emergency spill, organizer lag, assembly latency, and `compaction_wait_ms`. The runtime records `memory_maintenance_awaited=false` by construction.
- Added a concurrency test that holds compaction open and proves the next foreground request still reaches the backend, plus a six-window journal test with more than 160k historical tokens, bounded prompts, zero compaction wait, and byte-verified raw history.
- Verified the complete repository suite after this slice: 366 tests collected, 364 passed, 2 skipped. Ruff, shell syntax, and whitespace checks also pass for the touched surface.
- Remaining truth: patch mode still depends on an accepted V2 record envelope, and exact-once recovery is sealed at the frozen planner and selector-chunk boundary—not yet at every end-to-end crash point.

## Decision Log

- The product promise is “effectively unbounded memory under a declared service envelope,” not mathematically infinite attention.
- Raw history is authoritative; summaries and indexes are rebuildable views.
- The selector organizes and remains frozen.
- Product correctness and no-pause behavior are proved before cost and VRAM dominance.
- Unknown pricing does not permit a zero-cost claim; componentwise billed-token dominance is an acceptable price-independent proof.
- Missing evidence fails its associated release claim, but optional evidence cannot block development of the core runtime.
