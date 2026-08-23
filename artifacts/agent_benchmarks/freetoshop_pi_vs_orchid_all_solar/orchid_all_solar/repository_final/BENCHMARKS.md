# Benchmarks

Measurements are reproducible guardrails and must not be presented as guarantees
from a single development machine. Run the checked-in headless profile with:

```bash
npm run benchmark
```

The `ci-safe-v1` profile lives in `packages/benchmarks`. It emits machine-readable
JSON and fails when a scenario exceeds the versioned ceiling in `baselines.json`.
CI runs it after a clean production build. Ceilings are intentionally broader
than a single workstation result so the gate detects major regressions without
encoding runner noise as product behavior.

## Initial baseline

Commit baseline environment: Windows x64, Node 24.12.0, warmup 1, samples 5.

| Scenario          | Fixture                                                 |                             Median |     p95 |     CI ceiling |
| ----------------- | ------------------------------------------------------- | ---------------------------------: | ------: | -------------: |
| Composite         | 512 × 512, 12 full-canvas mixed-blend pixel layers      |                            2100 ms | 2148 ms | 4000 / 6000 ms |
| Brush             | 2048 × 2048 layer, 48 px pressure-aware diagonal stroke |                              43 ms |   50 ms |  500 / 1000 ms |
| Sparse tile patch | 32768 × 32768 transparent RGBA store, one 64 × 64 patch |                             2.3 ms |  3.8 ms |     25 / 75 ms |
| Sparse mask patch | 32768 × 32768 zero coverage store, one 64 × 64 patch    |                   recorded per run |       — |     25 / 75 ms |
| Document storage  | 1024 × 1024, 8 RGBA layers                              | 0.875× observed array-buffer delta |       — |          1.35× |
| Warm tile cache   | 128 × 128, 4 layers, dependency-keyed composite tiles   |                            ~0.5 ms | ~0.6 ms |    50 / 100 ms |

The memory fixture owns 33,554,432 logical pixel bytes. The first observed
array-buffer delta was 29,360,128 bytes (0.875×); garbage collection can make
the delta smaller than logical ownership, so only upward amplification is gated.
These numbers expose the current whole-surface compositor cost and provide the
comparison point for tiled storage and dirty-region rendering. The sparse tile
fixture allocates exactly one 256 × 256 tile (262,144 bytes) instead of a 4-GiB
contiguous surface; that allocation is also enforced by the baseline. The
one-channel mask fixture enforces a 65,536-byte tile ceiling rather than RGBA's
262,144 bytes.

The current checkpoint settles V8 memory accounting with three garbage
collection/event-loop turns before and after allocation. This keeps the
existing 1.35× guard unchanged while eliminating deferred backing-store noise.
The latest 33,554,432-byte fixture reports a 33,554,432-byte array-buffer delta
(1.00× amplification). This remains a Node guardrail, not browser peak-memory
evidence; browser working-set and long-task suites remain an M1/M2 priority.

Record, at minimum: commit, browser/version, OS, CPU, memory, device pixel
ratio, document dimensions, layer count, input encoding/size, warmup, sample
count, and median/p95. Candidate scenarios are cold application start, PNG/JPEG
decode, first render, repeated layer transform/composite, undo/redo, IndexedDB
save/restore, export, and repeated live Free Transform preview pointer moves.

Separate CPU time, wall time, peak memory, and output size. Use representative
documents that can be redistributed, keep the tab visible, and note throttling.
Correctness tests and memory/allocation limits take priority over benchmark
scores. Update a baseline only with an intentional architecture change, the raw
JSON report, and a documented fixture or environment revision.

## Composite tile cache (M2 foundation)

`CompositeTileCache` proves warm dependency-keyed tiles assemble without re-invoking
the reference compositor. Latest Node result for 128 × 128 / 4 layers: warm median
~0.46 ms versus cold reference ~50 ms with `fullyWarm: true`. Cold full-frame
512 × 512 / 12-layer composite remains multi-second until the live path and
region-local population land.

## Browser composite probe (M2 evidence)

`npm run test:browser-perf` runs Chromium Playwright against `window.__freetoshopPerf`.
Latest recorded probe (`docs/perf-evidence/browser-composite-probe.json`):

| Metric                       |                                    Value |
| ---------------------------- | ---------------------------------------: |
| Fixture                      | 256 × 256, 8 mixed-blend layers, tile 64 |
| Cold median                  |                                  ~348 ms |
| Warm median                  |                                  ~0.8 ms |
| Visibility cold / warm       |                        ~282 ms / ~0.5 ms |
| Fully warm                   |                                     true |
| Long tasks during warm burst |                       0 ms (best-effort) |

Warm cache is more than 100× faster than cold reference recompose in the browser.
Node still records multi-second cold 512×512/12-layer full composites; the warm tile
path is the interactive path for repeated frames.

## Worker-scheduled composite (M2)

Cold advanced stacks may schedule a module worker (`kind: 'composite'`) via
`CompositeWorkerScheduler`. While the worker is in flight, the UI uses
`warmAssembleOnly` (no main-thread `referenceComposite` miss-fill). Matching
worker frames seed the tile cache. Browser evidence
(`docs/perf-evidence/browser-worker-composite-cold-path.json`, 128×128 / 6 layers):

| Metric                       |   Value |
| ---------------------------- | ------: |
| Main-thread reference        |  ~58 ms |
| Main-thread warmAssembleOnly |   ~1 ms |
| warmOnlyReferenceCalls       |       0 |
| Worker frame wait            | ~135 ms |
| workerMatchesReference       |    true |

Working-set probe (24 opacity edits, 128×128 / tile 64 / maxBytes 48 KiB):
`stayedWithinBudget: true`, `boundedGrowth: true`, `totalEvictions > 0`
(`docs/perf-evidence/browser-composite-working-set.json`).

Larger cold path (256×256 / 8 layers): warmOnlyReferenceCalls=0, worker≡reference;
see `docs/perf-evidence/browser-worker-composite-cold-path-large.json`.

Long-session probe (120 opacity revisions, 192×192 / 8 layers / tile 64 /
maxBytes 96 KiB): `stayedWithinBudget: true`, `boundedGrowth: true`,
`peakAllocatedBytes == maxBytes`, `totalEvictions > 0`
(`docs/perf-evidence/browser-composite-long-session.json`). Unit suite covers
an 80-edit multi-revision session under a tighter budget.

Large-document interactive probe (1024×1024 / 6 layers / tile 64 /
viewport 384×384, pan 512,256) after batched miss-union region fill:

| Metric                        |                                   Value |
| ----------------------------- | --------------------------------------: |
| Cold progressive viewport     | **~64 ms** (was ~241 → ~526 → ~3608 ms) |
| Warm progressive viewport     |                                 ~2.9 ms |
| Pan cold (new region)         |         **~40 ms** (was ~226 → ~523 ms) |
| Pan warm                      |                                 ~2.1 ms |
| Return-home warm              |                                 ~1.9 ms |
| Mip-1 warm                    |                                 ~0.4 ms |
| Progressive first-paint tiles |                           36 / 256 full |
| Interactive budget (warm)     |                                   50 ms |

Evidence: `docs/perf-evidence/browser-composite-large-doc-interactive.json` (re-probed after
region store-read + allocation-free surface blend + **normal/multiply/screen inline paths**).
Progressive cold viewport fill is now under 100 ms on this host for 1024²/6 layers.
Node progressive cold 512²/6 ~95 ms; unit 512² progressive cold budget &lt;1s;
unit 4096² sparse progressive stays region-local (tileCount bounded, 0 full-ref misses).

Chromium suite: `npm run test:browser-perf` (includes live App pan/zoom E2E and
**4096² sparse progressive** writing `browser-composite-4k-sparse-interactive.json`:
cold ~34 ms / pan cold ~15 ms / warm ~5 ms, 36 of 4096 tiles).
**Tri-engine**: Firefox + WebKit pass large-doc progressive interactive probe
(`docs/perf-evidence/browser-composite-large-doc-tri-engine.txt`). Dense 4k×20 and
8192 sparse progressive are tri-engine gated. Sustained progressive memory budgets
are recorded below (Chromium E2E).
Brush frame (Chromium): paint median ~0.8 ms, commit ~0.9 ms, frame ~1.7 ms on 2048² sparse
(`docs/perf-evidence/browser-brush-frame-interactive.json`).

### Dense large-document progressive (M2)

| Probe                                   | Result                                            |
| --------------------------------------- | ------------------------------------------------- |
| Unit 2048²/4 dense progressive viewport | ~0.6 s (budget &lt;5 s), region-local, 0 full-ref |
| Unit 4096²/2 dense progressive viewport | ~0.7 s (budget &lt;8 s), region-local, 0 full-ref |
| Browser 2048²/4 dense progressive cold  | **~53 ms**                                        |
| Browser 2048² pan cold                  | **~31 ms**                                        |
| Browser 2048² warm                      | **~4 ms**                                         |

Evidence: `docs/perf-evidence/browser-composite-2048-dense-interactive.json`.

| Browser 4096²/2 dense progressive cold | **~47 ms** |
| Browser 4096²/2 pan cold | **~28 ms** |
| Browser 4096²/2 warm | **~9 ms** |

Evidence: `docs/perf-evidence/browser-composite-4096-dense-interactive.json`.

| Browser 4096²/6 dense progressive cold | **~96 ms** |
| Browser 4096²/6 pan cold | **~64 ms** |
| Browser 4096²/6 warm | **~20 ms** |
| Unit 4096²/6 dense progressive home+pan | ~3.9 s (budget 15 s), budget-bounded bytes |

Evidence: `docs/perf-evidence/browser-composite-4096-dense-x6-interactive.json`.

| Unit 4096²/12 dense progressive home+pan | ~4.3 s (budget 30 s), region-local |

### Representative 4096×4096 × 20 layers (M2 goal fixture)

| Probe                     | Result                                                                |
| ------------------------- | --------------------------------------------------------------------- |
| Unit progressive home+pan | ~7.7 s construction+compose, region-local, warm reassemble &lt;500 ms |
| Browser progressive cold  | **~232 ms**                                                           |
| Browser pan cold          | **~198 ms**                                                           |
| Browser warm              | **~45 ms** (&lt;50 ms interactive budget)                             |
| Progressive tiles         | 36 / 4096 full                                                        |

Evidence: `docs/perf-evidence/browser-composite-4096-dense-x20-interactive.json`.

### 8192×8192 sparse document (M2 goal fixture)

| Probe                     | Result                                      |
| ------------------------- | ------------------------------------------- |
| Unit progressive home+pan | region-local, &lt;10 s budget (~84 ms host) |
| Browser progressive cold  | **~39 ms**                                  |
| Browser pan cold          | **~20 ms**                                  |
| Browser warm              | **~10 ms**                                  |
| Progressive tiles         | 36 / 16384 full                             |

Evidence: `docs/perf-evidence/browser-composite-8192-sparse-interactive.json`.

### Sustained progressive pan memory (M2)

| Probe                                      | Result                                                             |
| ------------------------------------------ | ------------------------------------------------------------------ |
| Fixture                                    | 1024² / 6 layers / tile 64 / viewport 384 / 48 pan+edit iterations |
| Cache budget                               | **2.5 MiB** (≪ 24 MiB full multi-layer surface)                    |
| Peak allocated                             | **2.5 MiB** (at budget)                                            |
| Final allocated                            | **2.5 MiB**                                                        |
| Total LRU evictions                        | **1024**                                                           |
| Peak unique spatial tiles                  | **158 / 256** full grid (~0.62)                                    |
| Peak entry count                           | **160** (dependency-key revisions can exceed unique spatial)       |
| Stayed within budget                       | **true**                                                           |
| Bounded growth (2nd half ≤ 1.25× 1st half) | **true**                                                           |
| Progressive sparse vs full multi-layer     | **true** (peak &lt; fullMultiLayerBytes)                           |

Evidence: `docs/perf-evidence/browser-composite-large-doc-sustained-memory.json`
(Chromium E2E `large document progressive pan session keeps tile memory bounded`).
**Tri-engine**: Firefox + WebKit also pass the same sustained-memory gate (peak at
budget, evictions &gt; 0, unique spatial &lt; full grid, progressiveSparseVsFull).
`CompositeTileCache.stats.uniqueSpatialTileCount` separates spatial footprint from
dependency-key entry churn so revision history does not look like unbounded spatial growth.
