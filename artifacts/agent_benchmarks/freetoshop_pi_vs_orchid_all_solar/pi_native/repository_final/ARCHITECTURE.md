# Architecture

## Status

FreetoShop is a browser-only, local-first editor under phased development. Its
npm workspaces are `apps/web`, `packages/core-document`,
`packages/command-history`, `packages/compositor`, `packages/renderer`, `packages/raster-engine`,
`packages/selection-engine`, `packages/filter-engine`,
`packages/transform-engine`, `packages/text-engine`, `packages/file-codecs`, `packages/persistence`, and
`packages/benchmarks`, and `packages/pixel-storage`.
The concrete source tree and tests are the authority when this document and code
disagree.

## Boundaries

- **`apps/web`:** owns interaction, accessibility, PWA behavior, and view
  state. Components should not encode file formats or pixel algorithms. The
  application shell delegates dialog lifecycle to `components/`, filter-command
  adaptation to `editor/filterOperations`, shared interaction contracts to
  `editor/types`, and pure document/geometry helpers to `editor/model`; new
  editor behavior must extend these boundaries instead of growing `App.tsx`.
  `workers/` is the browser-process boundary for long-running local operations:
  it owns typed request/result protocols, progress delivery, cancellation by
  worker termination, and error normalization while delegating algorithms to
  headless packages.
- **`core-document`:** owns serializable editor state, layer ordering, stable
  identifiers, invertible affine layer transforms, validated edits, and
  canonical editable shape geometry/styles, owned line/cubic vector-path
  commands, and text content/styles. It also owns
  ordered typed layer effects, their validated nondestructive parameters, owned
  embedded smart-object source trees, ordered typed smart-filter state, and
  nondestructive canvas geometry. Crop and Canvas Size change canvas bounds,
  translate only root-layer world placement, and remap canvas-sized
  selection/channel data without trimming layer-local pixels or masks. Reveal
  All derives a safe expanded rectangle from nested transformed renderable-layer
  bounds and uses the same path.
  Pixel layers canonically own immutable sparse `TiledPixelStore` resources;
  patches replace only changed tiles and history may safely share unchanged
  resources. Raster masks use the corresponding one-channel
  `TiledCoverageStore` and copy-on-write mask patches.
- **`command-history`:** applies undoable commands and owns undo/redo history.
- **`compositor`:** owns DOM-free premultiplied-alpha source-over equations, 20
  separable and six RGB-vector professional blend modes, and immutable/in-place
  same-sized surface composition. The renderer test suite keeps its mode list
  synchronized with the canonical document contract.
- **`renderer`:** turns a document and viewport into Canvas 2D pixels. Rendering must not
  mutate the document. It rasterizes affine pixel, rectangle/ellipse/path, and
  structured text layers, resolves clipping and
  group isolation, rasterizes local and document-space raster/vector masks (including
  masks over combined pass-through and isolated group results), recursively composites smart objects, applies their
  filter-engine-backed smart filters before outer masks/effects, applies
  adjustment layers and layer effects, and delegates blend math to the compositor.
- **`raster-engine`:** owns deterministic, DOM-free pixel and mask operations.
  Brush, eraser, and adjustments consume document-space selection coverage and
  inverse-map affine coordinates, and return minimal layer-local RGBA or
  one-channel coverage patches. The same mask stroke path edits pixel, shape,
  text, and smart-object masks through their nested world transforms and group
  masks directly in document space. Image Size uses its nearest-neighbor and
  bilinear pixel-center resamplers for pixel layers, layer masks, selections,
  and channels; bilinear RGBA interpolation is performed in premultiplied alpha.
- **`selection-engine`:** creates and refines owned, canvas-sized 8-bit coverage
  masks. It owns supersampled ellipse/polygon rasterization, RMS-color Magic
  Wand matching, and deterministic mask morphology; it does not depend on the
  DOM or renderer.
- **`filter-engine`:** returns bounded immutable pixel patches for destructive
  color and Gaussian operations. It maps document-space selection coverage
  through the supplied layer world transform and leaves command/history policy
  to the caller. The renderer also reuses its Gaussian implementation for
  nondestructive smart-object intermediate surfaces.
- **`transform-engine`:** owns DOM-free transform snapshots, local/document
  handle geometry, nested-parent-aware scaling, rotation, translation, and
  singularity protection. The web app owns pointer capture, previews, and
  accessible controls. The web app likewise projects editable path anchors and
  cubic controls through nested world transforms, while core-document remains
  the canonical geometry and matrix authority.
- **`text-engine`:** lazily loads HarfBuzz WebAssembly, owns bounded local-font
  registration, OpenType substitution/positioning, direction inference, line
  layout, and positioned glyph outlines. The renderer consumes those outlines;
  system fonts without available binaries use an explicit Canvas fallback. See
  [TEXT_ENGINE.md](TEXT_ENGINE.md).
- **`file-codecs`:** translates PNG/JPEG bytes to or from the document model and
  performs bounded, import-only PSD translation through `ag-psd`. PSD parsing
  validates the binary header before decode and returns an explicit compatibility
  report; it does not leak `ag-psd` objects into canonical state. Imported data
  is untrusted.
- **`persistence`:** saves/restores supported state in IndexedDB. No server is
  required and document contents should not leave the device implicitly. It
  also owns the versioned `.freetoshop` binary container, schema migration,
  SHA-256 integrity verification, and canonical native-file decoding. See
  [NATIVE_FORMAT.md](NATIVE_FORMAT.md).
- **`benchmarks`:** builds versioned synthetic documents and enforces broad,
  CI-safe ceilings for compositor time, brush latency, and typed-array storage
  amplification. Production packages must never depend on it.
- **`pixel-storage`:** owns sparse immutable tile stores, copy-on-write pixel
  patches, sparse one-channel coverage stores, contiguous and tiled
  `PixelSource` adapters, owned region reads,
  chunk iteration, validated deterministic worker/persistence snapshots, and
  clipped/coalesced dirty-region accumulation. It has no dependency on the
  document model or renderer. See
  [TILED_RENDERING.md](TILED_RENDERING.md) for the staged integration contract.

Dependencies point inward: UI and adapters may call domain contracts; the
domain does not depend on React, browser DOM nodes, or a particular codec.

Selection outlines are renderer view data derived from the canonical coverage
mask. Filter dialog previews are transient documents; only Apply patches the
canonical pixel layer through command history. Neither engine introduces a new
persistence format or external dependency.

Schema-v6 compositing state remains framework-independent. The web layer only
chooses commands and displays controls; core-document validates layer properties
and adjustment payloads, persistence migrates them, and the renderer/compositor
produce display and export pixels from the same state.

Layer-tree duplicate/group/ungroup/remove/replace operations are headless core
commands. The UI owns transient multiselection. Merge and Flatten deliberately
cross the renderer boundary first to produce a full-canvas pixel layer, then use
one validated root replacement command; this keeps history atomic without
placing rendering dependencies inside core-document.

The web Magic Wand supplies the flattened document composite and enables alpha
sampling. Contiguous matching is a four-connected flood fill; disabling it
scans the full canvas. Freehand lasso points are retained after at least
0.5 document pixels of movement, closed on release, and rasterized by the same
four-times-supersampled even-odd polygon engine used by the headless API.

## Data flow

1. A user explicitly selects a local file.
2. A file adapter validates and decodes it into document state and pixel data.
3. Commands produce validated document updates. Long gestures may derive
   transient previews, but completion commits exactly one command. Crop drag
   and numeric-bound editing remain view state until Apply or Enter commits the
   crop through command history. Canvas Size and Image Size collect validated
   modal input before each commits one history operation. Image resampling and
   destructive filter application run in isolated module workers; completed
   results are rejected if the canonical document changed while work was in
   flight, and cancellation never commits a partial result.
4. The renderer derives the visible frame from document plus viewport state.
5. An explicit export serializes a supported output.

## Current non-goals

Collaboration, cloud sync, server rendering, arbitrary third-party code,
Photoshop parity, lossless round-tripping of unknown PSD data, and production
plugin or scripting ABI stability are not current guarantees. Pixel layers and
raster masks are tile-owned, but selections/channels, legacy algorithms, version-1 persistence,
and advanced composition still cross explicit contiguous boundaries. Resizing
is not high-bit-depth, profile-aware, or equipped with a dedicated
area-downsampling filter. Worker progress is currently operation-level rather
than per-tile.
