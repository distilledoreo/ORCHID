# Testing

Use the cheapest check that proves the changed behavior, then broaden based on
risk.

```sh
npm run typecheck
npm run lint
npm test
npm run audit
npm run build
npm run test:e2e
npm run format:check
```

## Test layers

- Unit tests cover document invariants, commands, geometry, validation, and
  deterministic renderer helpers.
- End-to-end tests cover document recovery and export plus pixel-verified brush,
  eraser, selection algebra, brightness, movement, raster/vector-mask compositing,
  editable pixel and structured-layer mask coverage, source-content preservation, mask persistence, and
  history workflows in Chromium, Firefox, and WebKit. Saved-channel coverage
  additionally verifies
  black/white canvas preview pixels, composite-only export, rename/load/delete,
  undo/redo, schema migration, local recovery, and keyboard panel navigation.
- Affine coverage verifies matrix validation/composition/inversion, nested
  rendering, selection-to-mask sampling, document-space brush footprints,
  transformed mask/pixel painting and brightness, legacy-schema migration, and
  a pixel-exact browser rotate/paint/undo/redo/export/recovery workflow.
- Transform-engine coverage verifies all stable handles, opposite-anchor corner
  and edge scaling, uniform scaling, reflections, nested parents, rotation,
  translation, immutable snapshots, and invalid/singular inputs. Browser
  coverage verifies 28px accessible handles, live scale/rotate gestures,
  cancellation, keyboard rotation, one-step undo, exact export pixels, and
  recovery.
- Crop unit coverage verifies root-world translation across nested transforms,
  preservation of layer-local pixels and masks, soft selection/channel remap,
  zero-filled out-of-canvas coverage, immutable inputs, and dimension/area
  limits. The tri-engine workflow verifies drag preview, Escape cancellation,
  exact numeric entry with Enter, rotated pixel export, remapped selections and
  saved channels, one-step undo/redo, and IndexedDB recovery of the cropped
  canvas.
- Canvas Size unit coverage exercises all nine named anchors, deterministic
  fractional-anchor rounding, anchored growth and trimming, root-only
  translation, selection/channel remap, unchanged nested pixel/mask resources,
  immutability, and allocation limits. Tri-engine coverage verifies modal focus
  and cancellation, validation, top-left expansion, transparent PNG bounds,
  transformed Reveal All output, and one-step Reveal All undo/redo.
- Reveal All unit coverage verifies nested rotation/translation, floor/ceil
  bounds, exclusion of hidden layers, union with the existing canvas,
  selection/channel remap, and contained-content no-ops.
- Image Size unit coverage verifies nearest and bilinear pixel-center sampling,
  premultiplied-alpha interpolation, pixel/mask/selection/channel resizing,
  nested affine placement, immutability, no-ops, invalid methods, and allocation
  limits. Its tri-engine workflow verifies constrained proportions, method
  selection, rotated pixel-exact PNG export, remapped selection/channel data,
  one-step undo/redo, and IndexedDB recovery.
- Selection-engine unit coverage verifies four-times-supersampled symmetric and
  clipped ellipses, owned even-odd convex/concave polygons, RMS RGB/RGBA Magic
  Wand distance, four-neighbor contiguous and global matching, diagonal
  separation, zero-padded feather, square maximum/minimum expansion and
  contraction, square-median smoothing, centered borders, owned no-op copies,
  malformed inputs, and dimension/radius/allocation ceilings. Core tests cover
  soft arbitrary-mask replace/add/subtract/intersect ownership. Renderer tests
  verify true pixel-edge outlines for arbitrary shapes. The tri-engine suite
  verifies ellipse/lasso/Wand gestures, modes, tolerance, refinement history,
  and saving refined coverage as a channel.
- Filter-engine unit coverage verifies Contrast and Levels mapping,
  Hue/Saturation, Invert, Rec. 709-weighted Grayscale and Threshold, Posterize,
  alpha preservation, nested-affine document selection sampling, empty/no-op
  patches, separable premultiplied-alpha Gaussian Blur, soft-selection blur
  blending, validation, and output/working-set limits. The tri-engine suite
  verifies a selection-aware Invert live preview, cancellation without export
  mutation, one-step Apply/undo/redo, pixel-exact PNG output, and IndexedDB
  recovery.
- Compositor golden tests cover all 26 deterministic modes, including the six
  RGB-vector modes, partial alpha, independent opacity/fill opacity,
  transparent-edge behavior, divide-by-zero, hard-mix thresholds, clamping,
  malformed surfaces, and immutable/in-place surface APIs. Renderer tests keep
  model/compositor mode contracts synchronized and cover pixel-exact separable
  and nonseparable blending, clipping-base alpha, isolated groups, and masked
  nondestructive adjustments. Core and persistence tests cover locks, typed
  adjustment validation, schema-v16 smart-object/filter/effect/structured-layer/group-mask
  round-trip, schema-v5/v6/v7/v8/v9/v10/v11/v12/v13/v14/v15 defaults, and malformed
  payload rejection. The tri-engine suite covers masked adjustment export, density,
  undo/redo, locked transforms, and IndexedDB recovery.
- Core tests cover immutable shape/text/path creation and editing plus invalid
  geometry/style/path-command rejection. Renderer tests pixel-check structured
  rectangle, ellipse, even-odd path fill, and path stroke rasterization. Each
  engine creates an anchor path with the Pen tool, closes and fills it, converts a line
  to a cubic segment, drags its transformed on-canvas control handle, verifies
  accessible handle exposure, live geometry, export, undo/redo, and reloads the
  schema-v16 recovery snapshot. Pixel, structured,
  adjustment, and group vector/raster-mask coverage, mask multiplication, density,
  inversion, enable/disable, deletion, malformed paths, migration, export, and
  recovery are covered too. Group coverage exercises pass-through and isolated compositing.
- Text-engine tests cover owned font registration, input limits, OpenType
  feature validation, direction inference, scale/position conversion, shaped
  wrapping, truncation, and a real HarfBuzz/DejaVu fixture proving ligature
  substitution, glyph outlines, and Arabic shaping. The tri-engine browser flow
  loads the same local TTF, renders shaped Latin/Arabic text, exports visible
  pixels, and verifies the honest fallback state after reload.
- Web editor-model tests pin selection bounds, path-handle adjacency, affine
  draft validation, and bounded document-size parsing independently of the
  React application shell so continued `App.tsx` decomposition remains
  behavior-preserving.
- Worker-client tests verify typed result delivery, bounded progress callbacks,
  immediate `AbortError` rejection, and worker termination on both completion
  and cancellation. Worker-protocol tests verify recursive pixel-layer tile
  snapshots, structured-clone transfer ownership, nested smart-object sources,
  and malformed snapshot rejection. The existing Image Size and destructive-filter browser
  workflows exercise the production module-worker entry point.
- PSD codec tests parse the committed composite, positioned-layer, and nested-group
  fixtures; verify ordering, transforms, opacity, blending, structured-preview
  warnings, and signature detection; and reject truncated PSD and PSB headers.
  Chromium, Firefox, and WebKit open the real layered fixture through the
  production worker, check native layers and export pixels, and exercise
  malformed-file reporting.
- Native-format tests round-trip editable structured layers, raster masks,
  selections, and alpha channels through the versioned binary container. They
  reject tampered checksums, invalid signatures, unsupported versions, invalid
  payload lengths, and truncated headers. The tri-engine suite verifies Save document,
  filename/MIME framing, editable reopen, and corruption rejection without
  replacing the current document.
- Effects tests cover immutable validation, pixel-exact overlay/stroke/shadow
  ordering, schema migration, editable browser controls, export, undo, badges,
  and recovery.
- Smart-object tests cover owned conversion and replacement, invalid sources,
  nested pixel compositing, schema round-trip/migration, browser conversion,
  pixel-exact replacement export, nested source editing with isolated history,
  atomic parent save/undo/redo, cancel-without-mutation, ordered smart filters,
  immutable source pixels, filter enable/opacity/parameters, malformed and
  duplicate filter rejection, pixel-exact filtered export, filter undo/redo, and
  IndexedDB recovery.
- Layer-tree unit coverage verifies recursive owned duplication, common-parent
  grouping, cross-parent rejection, transform/opacity-preserving ungroup,
  unsafe-group rejection, and owned root replacement. The tri-engine suite verifies
  Ctrl-multiselect, hierarchy badges, duplicate/group/ungroup counts, merge and
  flatten pixels, single-step undo/redo, and recovered tree/raster state.
- Component, accessibility, malformed-input, oversized-input, offline, and
  performance suites remain required work; they are not implied by the current
  test commands. `npm run test:e2e` runs every browser workflow in Chromium,
  Firefox, and WebKit; CI installs and exercises all three engines.

Behavioral fixes should include a regression test when feasible. Do not relax a
valid assertion to make a failure disappear. Renderer snapshots must document
their browser, dimensions, color assumptions, and tolerance. A passing unit
suite alone is not runtime verification of a user-facing workflow.

Pixel-storage and renderer tests cover clipped region reads, immutable source
ownership, sparse chunk placement, direct canonical store resolution, and the
explicit full-buffer fallback used by advanced compositing. Core and raster
tests verify copy-on-write patch/history behavior and explicit legacy algorithm
materialization.

Tiled pixel and one-channel coverage snapshot tests cover deterministic
ordering, owned rehydration, copy-on-write dirt, malformed tile lengths,
duplicates, out-of-bounds coordinates, and rejection of redundant transparent
or zero tiles. Worker tests transfer nested pixel and raster-mask tiles together.
Renderer coverage verifies that document-space dirt is
inverse-transformed and only intersecting source chunks are requested.

Render-invalidation tests cover transformed and clipped patch bounds. The
tri-engine paint workflow asserts that a committed brush gesture leaves the
actual canvas in dirty-render mode with one propagated region; pixel export and
undo/redo assertions remain the correctness check.
