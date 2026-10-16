# FreetoShop Long-Horizon Engineering Goal

You are working on FreetoShop, a local-first browser image editor intended to provide Photoshop-familiar professional editing workflows without sacrificing the existing high-performance tiled architecture.

This is a sustained engineering task. Work in ordered milestones, keep the repository buildable, and leave a coherent implementation with meaningful automated tests. Prefer a small complete slice over decorative breadth. Do not replace the tiled engine with a whole-canvas model, do not add controls that are not wired to real behavior, and do not hide unsupported behavior behind mocks.

## Product and architecture constraints

Preserve these principles throughout the work:

- Editing operations are real, undoable transactions and should remain compatible with the existing command/history model.
- Pixel storage, selections, masks, channels, compositing, and persistence remain tile- or region-oriented. Reads, writes, allocation, and rendering should be bounded by the affected region whenever the operation permits it.
- Preserve copy-on-write behavior and immutable historical state. An operation must not mutate an unrelated layer, source document, cached tile, or prior history entry.
- Preserve the existing Brush and Eraser workflows while adding neighboring tools.
- Preserve local-first behavior, browser compatibility, recovery behavior, and the existing file/document model.
- Use existing coordinate, raster, worker, renderer, persistence, and UI infrastructure where it is the correct extension point. Avoid broad rewrites merely to make a feature easier.
- Every visible control, menu item, shortcut, panel, or tool option must be connected to observable behavior and covered by a test at the most appropriate level.
- Keep CPU/software behavior as the correctness oracle when adding optional accelerated paths. Unsupported hardware must retain a valid software path.
- Document limitations honestly. Passing a shallow smoke test is not evidence that a professional workflow is complete.

## Milestone 1 — Clone Stamp foundation

Implement a Photoshop-familiar Clone Stamp tool in the appropriate retouch/painting toolbar family.

Required user behavior:

- Alt/Option-click establishes a clone source and the UI clearly indicates whether a source exists.
- Pointer painting samples pixels from the source and writes them into the destination using the existing brush/raster workflow.
- Aligned mode preserves the source-to-destination offset throughout a gesture and across stroke segments.
- Unaligned mode restarts sampling from the source origin as required by the tool convention.
- Clone sampling and destination writes use the correct transformed-layer coordinate mapping.
- The operation respects the active selection and clips writes to the selected region.
- The operation respects the current mask/edit-target model and does not write to the wrong layer or mask.
- A single pointer gesture creates one atomic undoable history transaction. Undo and redo restore exact pixels and layer state.
- Saving and reopening, or the existing local recovery path, preserves the edited pixels and the relevant document state.
- Missing source state is handled visibly and safely; painting without a source must not silently corrupt pixels.

The clone source, aligned/unaligned behavior, and coordinate relationship must be represented by an explicit testable data model rather than being implicit UI-handler state. Reuse tiled stores and coordinate helpers. Do not materialize an entire canvas or layer for a clone operation. Add focused unit tests for source/destination pixel correctness, multi-segment aligned strokes, unaligned restart, transformed layers, selection clipping, masks/edit targets, and tile-boundary behavior. Add a browser workflow using real pointer/keyboard events and verify undo/redo plus persistence or recovery.

Do not regress existing Brush or Eraser behavior.

## Milestone 2 — Retouch and paint workflow completion

After Clone Stamp, extend the real editing workflow with a coherent set of professional retouch and paint operations. Prioritize behavior that can be exercised end to end rather than adding isolated labels.

Include, as appropriate to the existing architecture:

- Healing Brush with source sampling and tone/texture-aware transfer.
- Spot Healing with automatic surrounding-area sampling.
- Patch-style source/destination workflow.
- Dodge, Burn, Smudge, Sponge, and Airbrush modes with visible options and meaningful pixel effects.
- Linear Gradient with foreground/background drag behavior.
- Paint Bucket with a bounded tolerance-based fill workflow.
- Vibrance or equivalent color-intensity adjustment in the relevant retouch workflow.

Each operation must identify its edit target, selection/mask behavior, coordinate space, history boundary, and persistence behavior. Use bounded processing and preserve copy-on-write. Add tests that distinguish a real pixel result from a no-op or UI-only implementation, including at least one browser-level workflow for the most important tools. Keep options and tool status synchronized with the active tool and do not make the user guess whether a mode is active.

## Milestone 3 — Selection, transform, adjustment, and layer workflows

Build the surrounding workflows needed for the editor to support realistic professional editing sessions:

- Color Range selection with sampling behavior and selection clipping.
- Magnetic lasso or equivalent edge-following selection behavior, plus a practical quick/object selection path where the existing product exposes one.
- Select and Mask refinement workspace with an observable preview/commit behavior.
- Four-corner perspective distortion with correct transformed coordinates and undoable application.
- Layer alignment and distribution actions, including persisted layer relationships/links where the document model supports them.
- Curves with per-channel R/G/B control and a real graph or equivalent interaction. Support both a destructive operation and an adjustment-layer path when the document model allows it.
- Adjustment and fill workflows needed by the existing parity contract, including color lookup/gradient-map or other already-defined adjustment families where their data model and UI are present.
- Layer-style controls that are actually connected to rendering and persistence, including relevant contour/global-light behavior rather than decorative panels.
- Text leading and tracking controls that affect whole-layer text layout and survive persistence.

Operations must remain undoable and must not break existing layer ordering, visibility, masks, blend behavior, or save/reopen semantics. Add deterministic unit tests for the mathematical or model portions and browser tests for representative interactions, including at least one multi-layer workflow and one adjustment/layer-style persistence workflow.

## Milestone 4 — Local-first persistence, partial documents, and recovery

Harden the local-first document lifecycle for large documents and incomplete hydration:

- Open documents through viewport-demand or otherwise partial resource hydration so only required resources are loaded initially.
- Keep clean hydrated resources bounded with explicit pinning and eviction behavior. Dirty or required resources must not be evicted incorrectly.
- Preserve unhydrated content when saving a partially opened document. A save must not silently discard untouched tiles/resources.
- Provide a coherent document-session abstraction for open, render, edit, save, close, and recovery transitions.
- Preserve the existing native/project format, checksums, content-addressed storage, incremental saves, and legacy compatibility behavior.
- Support the browser-appropriate open/save and Save As flows, including handle reuse or safe fallback where available.
- Verify partial open, pan/viewport demand, edit, save, restart/reopen, and recovery with real browser tests. Include tests for eviction pressure, dirty-resource pinning, and saving after visiting only part of a document.

Resource management must be observable enough to prove the working set is bounded. Do not solve a persistence problem by eagerly materializing the full document.

## Milestone 5 — Rendering and format compatibility

Advance the rendering and file-format paths while keeping a trustworthy software oracle:

- Establish optional GPU/WebGPU capability detection and a renderer/backend contract that can fall back safely.
- Route supported Normal compositing and related operations through GPU buffers only when the backend is valid, with byte- or pixel-exact comparison against the CPU path.
- Cover clipped regions, blend modes, isolated/nested groups, pass-through behavior, masks, and alpha handling in renderer tests.
- Preserve bounded region/tile work and avoid requiring GPU availability for ordinary editing or tests.
- Extend color/depth handling needed by the product contract, including native higher-precision pixel clipping or adjustment behavior where the document model supports it.
- Improve PSD/PSB and related codec behavior for the supported compatibility tiers, including bounded parsing and owned compressed alpha decoding where required. Preserve unsupported data safely rather than pretending to export it.
- Add malformed-input, corpus, round-trip, and write/reimport tests appropriate to each supported tier.

Performance work must include measurements. A faster path that changes pixels, loses resources, or cannot recover on unsupported browsers is not an acceptable improvement.

## Milestone 6 — Professional shell, compatibility, and hardening

Finish the user-visible integration around the implemented capabilities:

- Keep menus, shortcuts, toolbar families, options bars, panels, dialogs, toasts, and history controls Photoshop-familiar and tool-aware.
- Ensure overlays, portals, dialogs, dock content, long labels, hostile viewport sizes, and overflow behavior remain usable.
- Preserve keyboard focus, labels, reduced motion, forced-colors behavior, and the existing accessibility contract.
- Exercise Chromium and the supported cross-browser set for important workflows, including real mouse and keyboard interaction rather than only model-level calls.
- Keep CI, formatting, lint, typecheck, unit, build, and relevant browser checks reproducible. Do not weaken strict validation to make a feature pass.
- Keep security and bounded-input protections intact for imported files, workers, persistence, and browser-hostile inputs.
- Update product/architecture/parity documentation with implemented behavior, evidence, and explicit limitations.

## Cross-cutting acceptance contract

Every pixel-changing feature must answer the following questions in its implementation and tests:

1. What document/layer/mask/channel is the active edit target?
2. In which coordinate space are pointer, source, selection, layer, and document values interpreted?
3. Which tiles or bounded region are read, allocated, rendered, and written?
4. What happens when the selection is empty, the source is missing, the layer is transformed, the resource is not hydrated, or the browser lacks an optional backend?
5. What exact command/history boundary represents one user action, and can it be undone and redone without changing unrelated state?
6. What is persisted, how is it reopened, and how is recovery handled after interruption?

Do not declare a feature complete because a button changes color or because a unit test calls a helper directly. The browser path should exercise the same production wiring a user invokes. For important workflows, tests should begin from a known document, perform real pointer/keyboard/menu actions, inspect visible state and pixels, undo/redo, and verify save/reopen or recovery. Where a browser test would be excessively expensive, pair a deterministic model test with one representative browser proof and explain the boundary.

Maintain a small set of adversarial documents while working:

- a multi-layer transformed document with masks and a nontrivial selection;
- a document large enough to cross tile boundaries and exercise bounded processing;
- a partially hydrated or recovery-backed document;
- a document with nested groups, blend modes, and hidden/locked layers;
- representative PSD/native files containing supported and unsupported data;
- hostile viewport dimensions and keyboard/focus conditions.

Use these documents to catch the common failure where a feature works only on a flat, fully resident, untransformed canvas. Record allocation/working-set observations for operations that claim bounded behavior. A fallback may be slower, but it must be correct, deterministic, and explicit.

For UI changes, verify both the interaction contract and the state contract: tool selection must select the intended tool, options must modify the operation, status must reflect source/mode/target state, keyboard shortcuts must not conflict with text input, and closing/reopening a dialog must not lose pending edits. For persistence changes, verify both a clean save and an interrupted or partial-resource path. For GPU or worker changes, compare against the software result and test startup, failure, and fallback paths.

For compatibility work, define the supported subset rather than silently accepting data that cannot be preserved. A read-only or subset-write path is preferable to data loss. Keep diagnostics actionable so a user can tell whether a document was fully loaded, partially hydrated, recovered, or saved through a fallback.

At each milestone, leave a short evidence trail in the repository's existing documentation style: behavior implemented, tests run, performance/resource observations, known gaps, and follow-up work. Do not replace a failing test with a weaker assertion merely to obtain a green run. If an existing baseline test fails before the change, isolate and report it; if the change causes a regression, repair the implementation or clearly stop at that boundary.

The ordered milestones are intentionally broad enough to require prioritization. A useful partial result has a complete first milestone and then the highest-value coherent slices of later milestones. It is better to finish and harden Clone Stamp, persistence, and one real retouch workflow than to expose many disconnected controls. Preserve a runnable application after every checkpoint and commit coherent slices so the final repository can be evaluated even if the time limit arrives before the entire backlog is complete.

## Working method and definition of done

At the beginning, inspect the existing architecture and establish which tests already pass or fail. Work milestone by milestone and keep completed work intact while investigating later issues. For each milestone, add or update tests before claiming completion, run focused validation, then run the relevant broader checks. Do not rewrite correct prior work to chase superficial similarity to an imagined implementation.

The final result should be a coherent local-first editor with a real Clone Stamp foundation and the strongest feasible set of the ordered catch-up capabilities above. A partial result is acceptable only if the implemented milestones are real, tested, persisted, and documented, and the remaining limitations are explicit. The repository must remain buildable and the final report must distinguish measured behavior from assumptions.
