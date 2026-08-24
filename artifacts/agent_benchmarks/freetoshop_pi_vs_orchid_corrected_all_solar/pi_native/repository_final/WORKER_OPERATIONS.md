# Worker-backed operations

Image Size, destructive filter application, PSD parsing, native-document
encoding/decoding, and cold full-document composition run outside the UI thread
in a dedicated module worker created for each operation. The application
sends only typed document-model data, file bytes, and algorithm parameters.
Workers call the same headless raster/filter/codec/composite paths used by tests,
report normalized progress, and are terminated after success, failure, or
cancellation. PSD/native input bytes, completed native output bytes, composite
RGBA buffers, filter patches, and every tile buffer in resize/PSD/native document
results are transferred rather than cloned. Document-result transfer lists are
deduplicated before `postMessage` so each owned buffer moves exactly once.

Selection-aware committed filters additionally prune their pixel-layer snapshot
to tiles intersecting the inverse-mapped selection work region. RGB filters use
a one-pixel mapping pad; Gaussian Blur includes the full two-radius source halo
required by its separable convolution. Raster masks are not sent because filter
patch computation does not read them. Unselected filters continue to transfer
all allocated sparse source tiles, preserving whole-layer filter semantics.

## Composite worker (M2)

Cold advanced composites (adjustment/shape/text/smart-object stacks, non-normal
blends, fill opacity, clipping, isolated groups) may be scheduled through
`CompositeWorkerScheduler` when the live `CompositeTileCache` is not fully warm
for the progressive viewport.

- Protocol kind: `composite` with a document snapshot and client `generation`.
- Result: transferred full-canvas RGBA `buffer`, `width`, `height`, and echoed
  `generation` for stale-result rejection.
- The scheduler cancels obsolete work via `AbortSignal` (worker terminate) and
  ignores late results whose generation is no longer current.
- While the worker is in flight and no frame is ready, the UI uses
  `warmAssembleOnly` so miss tiles stay transparent and **never** call
  `referenceComposite` on the main thread (`planAdvancedCompositeRender`).
- When a matching worker frame arrives, App seeds the main-thread
  `CompositeTileCache` from that full RGBA once (dependency-keyed tiles), then
  uses warm assemble on subsequent paints. `precomposedPixels` is only a fallback
  if a frame is available but the progressive viewport is not fully warm.
- Progressive warm tile hits remain interactive while the worker owns cold fill.

## Commit and cancellation contract

- A worker result is committed through command history as one undoable change.
- A result derived from a stale document snapshot is rejected rather than
  overwriting intervening edits.
- Cancel terminates the worker and does not apply a partial document or patch.
- Closing or unmounting the editor aborts an active operation.
- Worker errors become user-visible local errors; no network fallback exists.

The current algorithms are synchronous inside the worker, so cancellation is
implemented by terminating its isolated execution context. Progress is coarse
(`started` and `complete`) until resampling and filters operate tile-by-tile.
That later tile integration should add cooperative checkpoints without changing
the public worker protocol.

Creating the input snapshot for Image Size and native export still copies every
allocated canonical tile before transferring that owned snapshot to the worker.
This is an intentional transitional cost and must remain represented in memory
benchmarks until a persistent worker can retain dependency-keyed resources and
accept deltas safely. Completed document snapshots no longer incur a second
structured-clone copy on the return path.
