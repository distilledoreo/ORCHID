# ORCHID Pareto Improvement Plan

Last updated: 2026-08-28

## Goal

Make ORCHID's live-compaction architecture beat traditional compaction on the same workloads and provider model while preserving:

- the selector's current organizing role and frozen request contract;
- a main model on the primary GPU and the selector on a smaller secondary GPU;
- evidence-grounded, recursively valid memory capsules;
- fail-open serving behavior until V2 is proven safe.

Release requires three valid paired runs in which ORCHID is non-inferior on hidden-task quality and reliability while improving total VRAM, foreground API cost, prompt/KV footprint, and peak context without unacceptable latency regression.

## Current Baseline

- Full suite: 318 passed, 2 skipped; no failures.
- Ruff passes for every production/test/tool file touched by this continuation.
- Recursive V2 carry-forward probe: passing for two generations.
- Frozen selector offline V2 gate: 100% accepted after normalizing ANSI transport noise before grounding checks.
- Sealed consolidator V2 gate: live checkpoints admit 13/32 after skipping citation checks for unchanged carried-forward items; remaining rejections are mostly `excerpt not found`. The fixture must be regenerated with the V3 citation-view prompt plus excerpt clipping, so benchmark remains on V1.
- Release blocker tool correctly refuses a run when provider, V2, or worktree checks fail.
- Foreground exact-once/control/root/token-accounting regressions are fixed and covered by focused tests.
- Foreground assembly now keeps developer/system, capsule, and root-task as a reusable prefix and places cold memory, raw tail, and conversation in the suffix so growing tails no longer invalidate the cached root task.
- Publication now compares eight-turn prompt savings against cached-prefix invalidation cost; without cache telemetry it requires a 25% prompt cut or urgent pressure.
- Agnes pricing is explicitly unknown rather than zero/free. Until measured billing or a complete versioned rate card exists, the cost gate uses conservative componentwise dominance over uncached input, cache reads/writes, and output.
- Corrected historical baseline (including cache-read prompt area and excluding the local selector from cloud spend): Pi prompt area 2,424,057 tokens versus ORCHID 7,944,825. ORCHID remains far from the target; the old benchmark is diagnostic, not release evidence.
- Remaining measurement defects: no trusted Agnes dollar rate and no arm-isolated two-GPU VRAM traces. Direct latency and hidden functional-quality schemas are now enforced for new release artifacts.

## Work Plan

### 1. Foreground correctness and exact-once assembly

- [x] Forward every conversational occurrence exactly once before and after the first promotion.
- [x] Preserve system/developer instructions as authoritative messages across promotions.
- [x] Preserve the original root task exactly once alongside fresh user turns.
- [x] Preserve assistant/tool protocol pairs when a promotion watermark splits a tool loop.
- [x] Make estimated foreground tokens equal the messages actually sent to the provider.
- [x] Add adversarial regression tests for duplicates, repeated identical events, control messages, fresh turns, and cold-memory injection.

### 2. V2 admission and safe rollout

- [x] Preserve unchanged evidence-backed items across recursive V2 generations.
- [x] Classify and fix every frozen-selector gate rejection without changing the selector contract.
- [x] Reach 100% acceptance on the frozen selector fixture without relaxing evidence requirements.
- [ ] Reach 100% acceptance on a freshly generated sealed consolidator fixture.
- [x] Make `v2_shadow` record failures while serving the valid V1 result unchanged.
- [x] Make verifier budget exhaustion/unavailability explicit; never silently omit changed items or evidence.
- [ ] Promote V2 only after offline, recursive, shadow, and full-suite gates pass.

### 3. Benchmark truth and release gates

- [x] Never interpret an unknown/all-zero rate card as free; use a conservative rate-independent cost proof instead.
- [ ] Capture a complete versioned Agnes rate card or measured billed cost for dollar-denominated comparisons.
- [x] Account separately for uncached input, cache reads/writes, output, local selector/canonicalizer, and cloud consolidator/verifier calls.
- [x] Measure provider request latency directly rather than inferring it from assistant-message spacing.
- [ ] Capture primary-GPU and selector-GPU baseline, peak, and time-integrated VRAM; missing telemetry must fail the gate.
- [x] Require a hidden-evaluation artifact in every paired release artifact.
- [x] Replace file/message-count quality proxies with task-specific correctness results.
- [ ] Validate preflight, V2 eligibility, telemetry completeness, and worktree provenance before paid runs.

### 4. Measured optimization

- [x] Stabilize the longest reusable prompt prefix and move volatile context into the suffix.
- [x] Optimize promotion timing using forecasted future token-cost savings minus compaction cost and cache invalidation cost.
- [x] Start the first live compaction at a separate early bootstrap watermark while preserving the steady-state policy and frozen selector contract.
- [ ] Deduplicate semantic state without deleting distinct event occurrences or selector structure.
- [ ] Bound capsule growth with evidence-aware incremental updates and stable item identities.
- [ ] Tune selector batching/concurrency for the secondary GPU without changing its selected organization.
- [ ] Evaluate quantization, residency, and scheduling choices using total two-GPU VRAM and end-to-end latency.

### 5. Proof of Pareto dominance

- [ ] Run cheap deterministic and replay gates first.
- [ ] Run one paid smoke pair and audit raw provider/cache/VRAM telemetry.
- [ ] Run three sealed paired repetitions with fixed workload, model, ordering policy, and evaluator.
- [ ] Require every run—not only the median—to pass correctness, reliability, and telemetry-validity gates.
- [ ] Publish the scorecards, raw metric provenance, confidence intervals, and remaining tradeoffs.

## Progress Log

### 2026-08-28 — Codex handoff

- Created this roadmap and adopted the existing Composer worktree without overwriting it.
- Confirmed the selector configuration is an invariant, not an optimization target.
- Confirmed recursive V2 carry-forward is fixed.
- Reproduced the three current targeted test failures and the foreground assembly defects.
- Started Phase 1 at exact-once assembly and authoritative instruction preservation.

### 2026-08-28 — Foreground exact-once slice

- Removed the pre-capsule double-forward path by excluding request-aligned event occurrences from the raw-tail block in every memory state.
- Kept system/developer messages authoritative across promotion and rendered the persisted root task exactly once even when a fresh user turn is present.
- Preserved genuinely repeated identical user events by deduplicating with aligned event identity rather than text hashes.
- Corrected cold-memory budgeting and final token accounting to measure the provider packet exactly once.
- Added three adversarial regressions; 63 focused gateway/context/cache/invariant tests pass and the full suite has no new failures.
- Remaining full-suite failures are isolated to the zero-priced provider configuration and two frozen V2 admission rejections.

### 2026-08-28 — V2 admission and citation protocol slice

- Normalized ANSI transport escapes before grounding-token extraction, bringing the frozen selector gate to 100% without weakening numerical grounding.
- Changed the consolidator input to the exact deterministic citation view used by admission and tightened excerpts to short contiguous evidence spans; bumped the prompt protocol to `consolidator-v3-citation-view`.
- Made `v2_shadow` genuinely fail open to a valid legacy capsule while recording V2 violations, and prevented rejected shadow envelopes from becoming recursive bases.
- Removed silent verifier truncation. Oversized, unavailable, or failed verification now produces a typed semantic rejection; strict V2 fails while shadow mode safely serves the legacy result.
- Full V2/capsule/pipeline regression slices pass. The old sealed consolidator output is intentionally not grandfathered and needs a fresh gate run.

### 2026-08-28 — Cache-aware API economics slice

- Restored the Solar Pro4 compatibility profile as an explicit, versioned provider/model/rate card instead of inheriting the Agnes defaults.
- Marked the all-zero Agnes price placeholder as unknown. Dollar-cost fields are now null when pricing is unknown rather than claiming zero spend.
- Added uncached-input, cache-read, cache-write, output, total-prompt, and peak-prompt metrics. The release path uses prompt tokens including cache reads for KV/context comparisons.
- Split local secondary-GPU selector/canonicalizer work from cloud-billed consolidator/verifier work. Legacy all-stage fields remain only to reproduce the sealed historical scorecard.
- Added a componentwise token-class theorem: when dollar prices are unavailable, ORCHID must reduce every billed class by the gated fraction to prove it is cheaper for any nonnegative rate vector.
- Missing selector-VRAM telemetry now fails rather than passing as zero, and paired release validation once again requires hidden evaluation.
- Added cache/accounting/provider regressions; the complete suite passed at that checkpoint.

### 2026-08-28 — Honest latency, quality, VRAM, and bootstrap gates

- Added controller-side per-provider-request timing for both arms and gateway monotonic timing for ORCHID. Release latency no longer uses assistant-message spacing, which included tools and idle time.
- Replaced message-count/file-count quality proxies. ORCHID must now actually pass the sealed hidden evaluator, typecheck, and build; equal failure on both arms is a failed benchmark, not “non-inferior.”
- Added a paired VRAM evidence schema requiring arm-isolated main-GPU and selector-GPU baseline, peak, sample count, and byte-seconds. Missing traces fail, as do peak or integrated total-VRAM regressions.
- Added an 8k initial compaction watermark (forecast-aware) only before the first capsule. Steady-state remains at 22k and the selector model, prompt, chunk target, schema, and organizing role are unchanged.
- This specifically attacks the report's pre-first-capsule peak: the old run waited until the selector backlog had grown to a 143,790-token foreground. Exact-once assembly plus early bootstrap should remove the two largest measured causes before any selector tuning.
- The complete suite now passes: 301 passed, 4 skipped. Node/shell syntax checks and Ruff pass for all touched production and benchmark files.

### 2026-08-28 — Stable prompt prefix and cache-aware publication

- Reordered foreground assembly to `developer/system → capsule → root task → cold memory → raw tail → conversation`, matching the frozen request contract while putting volatile context in the suffix.
- Prompt-cache telemetry now hashes only the stable prefix. Changing the raw tail no longer busts the reusable prefix estimate; the first cache-break block is reported in assembly order.
- Each assemble persists the latest prompt-cache snapshot on the thread so publication can see recently cached prefix tokens.
- Publication economics now compare eight-turn prompt-token savings against cache-invalidation cost. Known rate cards scale reset cost by `(uncached - cached) / uncached`. Missing cache telemetry requires a ≥25% prompt reduction or urgent pressure. Urgent jobs still publish immediately.
- Scheduling projects tokens at compaction completion from EWMA arrival, p95 service time over the last ten jobs (240s until three samples), optional median turn interval, and a 20% burst margin. Projected 22k pressure queues background work; projected 80% of the hard limit is urgent.
- Benchmark launch enables publication economics. The complete suite passes: 318 passed, 2 skipped. Ruff passes for all touched production and test files.

## Decision Log

- Selector behavior remains fixed unless a future benchmark exposes a correctness bug rather than a tuning opportunity.
- All optimization claims use total system cost, including both GPUs and all model calls.
- A missing metric fails its release gate; absence is never interpreted as zero usage.
- Historical scorecard fields remain reproducible, but corrected release fields are versioned alongside them and take precedence for decisions.
- Selector model tokens count toward local compute/VRAM/latency, never cloud API spend, unless telemetry explicitly marks a stage as remote.
- Unknown pipeline stages are conservatively treated as provider-billed so incomplete labels cannot make ORCHID look cheaper.
- A release-quality VRAM comparison must isolate arms; concurrent shared-GPU sampling is rejected as confounded.
- Activity is not correctness. More messages, edits, or files can never compensate for a failed hidden task.
- V2 validation may become more robust to representation differences, but evidence/grounding standards will not be weakened to make a fixture pass.
- No new paid benchmark runs occur while deterministic correctness or telemetry gates are red.
