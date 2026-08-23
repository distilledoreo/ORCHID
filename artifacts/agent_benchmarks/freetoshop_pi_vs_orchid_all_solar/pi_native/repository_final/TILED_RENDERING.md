# Tiled Pixel Storage and Dirty Rendering

## Coverage-source migration

`CoverageSource` gives large one-channel resources the same format-neutral
read/materialization boundary as pixel resources. Contiguous and sparse tiled
adapters produce identical owned region reads. Selections and saved alpha
channels now **own only** a required `TiledCoverageStore`. The dual contiguous
`coverage` mirror field has been removed from `SelectionMask` and
`AlphaChannel`. Ordinary ownership, copy, history, worker transport, channel
load/save, rectangular selection creation, and live outline/bounds paths use
the store directly and share immutable store references without densifying.
Live saved-alpha preview also consumes `CoverageSource` chunks directly: the
renderer paints an opaque black document plane, then allocates tile-sized grayscale
RGBA surfaces for explicit tiles or bounded implicit-default chunks. Dense preview buffers remain accepted
for compatibility, and export-only composite output materializes explicitly.

Selection feather, square maximum/minimum morphology, median smooth, and border
refinement preserve the implicit coverage default. They filter deviation-tile
neighborhoods plus bounded canvas-edge tile bands where the required
zero-outside-canvas samples can change a nonzero default. Each working buffer is
one destination tile plus its radius halo; a complement never becomes one dense
full-canvas work rectangle. Some filter/brush sampling and explicit export/codec
boundaries still materialize documented dense buffers, so M1 remains incomplete.

Coverage inversion itself is tile-native and complement-aware.
`TiledCoverageStore` snapshot schema 2 records an implicit `defaultCoverage`
value plus tiles that deviate from it; snapshot schema 1 loads as zero-default
for compatibility. `invert()` flips the default and only the existing deviation
tiles. Layer-mask and partial-selection inversion therefore preserve one sparse
tile on a 4096² one-tile source instead of allocating the 16 MiB complement.
Crop, image resize, worker snapshots, native schema-20 sparse payloads, bounds,
outlines, thumbnails, selection morphology, and affine selection-to-mask bake
preserve the same invariant.

Cold composite tasks use a persistent sequential worker with version-1
snapshot/reference and version-2 delta resource envelopes. The first task
transfers scoped pixel and coverage snapshots; later tasks whose immutable stores
are unchanged send references and zero tile bytes even when opacity or other
document metadata changes. A changed store derives from the retained resource in
the same logical document slot and scope: only COW tiles with changed buffer
identity transfer, while removed tiles travel as validated keys without buffers.
The client and worker prune superseded resources while retaining multiple
viewport/full scopes for live stores. The editor's sequential document-worker
session shares the same logical slots across filter, resize, and native-export
tasks, so an unchanged document can cross task kinds with zero tile upload and a
later COW edit transfers only its changed tiles. Resize and import results receive
collision-safe request-scoped IDs. The worker hydrates owned retained clones
before transferring serialized buffers; the client adopts hydrated stores under
the same IDs and logical slots. A first follow-up native save of a resized
document therefore uploads zero tile bytes. Cancellation still terminates the
worker and resets both registries before another task can run.

Standalone schema-20 JSON serialization preserves its legacy row-major base64
payload for dense resources without first producing a contiguous store copy.
Pixel and coverage stores are read as full-width stripes targeting at most 64
KiB (or one row when wider), and base64 carry crosses stripe boundaries
byte-exactly. Native file format v2 instead writes schema-20 metadata plus binary
pixel/coverage tiles addressed by SHA-256. Identical tile content across stores
or coordinates is emitted once; encoding iterates canonical allocated tiles
without snapshot cloning or `toContiguous`. The outer payload checksum and every
resource hash are verified before hydration. Legacy v1 JSON files still open.
IndexedDB autosave remains a full schema-20 JSON record until the CAS resource
table is shared with the repository.

Coverage sources also expose deterministic bounded-chunk iteration. Saved alpha
preview streams explicit or implicit-default tiles. The live resolution-independent
selection outline reads allocated tiles for zero-default selections; complements
read deviation tiles plus four neighbors and canvas-edge rows/columns. Its one-pixel
halo remains exact across tile seams without building a full-canvas mask.
Selection bounds return the canvas directly when a nonzero default has missing
tiles. The RGBA pixel-outline compatibility function still returns a
full-canvas surface by contract and remains an explicit legacy boundary.

## Purpose

Pixel layers, raster masks, selections, and saved alpha channels now own sparse
tiled resources as their canonical document fields. The deterministic compositor
still allocates contiguous full-canvas surfaces. The baseline benchmark shows
why the remaining migration matters: a 512 × 512, 12-layer composite takes
seconds rather than meeting an interactive frame budget.

The ordinary Canvas display path streams canonical pixel chunks directly even
when a pixel layer has an enabled raster mask. Each source chunk reads only its
matching coverage region and applies mask density to that chunk's alpha before
drawing. Vector masks and stacks that require deterministic compositing retain
their existing reference path. An 8192² one-tile pixel+mask fixture therefore
uses one 256² RGBA tile plus one 256² coverage tile (327,680 canonical bytes)
without materializing either full surface.

Full-layer Brightness also writes directly into an immutable canonical result
store one 256×256 tile at a time. The old document and unchanged tiles remain
shareable for history, while the new store is committed atomically; there is no
second full-layer rectangular `PixelPatch`. Selection-bounded Brightness keeps
the smaller inverse-mapped selection patch. Contrast, Levels, Hue/Saturation,
Invert, Grayscale, Threshold, Posterize, and Gaussian Blur return deterministic
tile-bounded worker patches that the client folds into one atomic COW store
commit. Gaussian tiles read a radius halo, reuse the reference premultiplied-alpha
kernel, and crop back to their core, preserving seams and canvas-edge clamping.

`@freetoshop/pixel-storage` is the migration boundary for sparse, tile-owned
pixels and exact invalidation. It is intentionally independent of React, Canvas,
the document model, and file formats.

## Storage contract

- Default tiles are 256 × 256 pixels; edge tiles own only their real dimensions.
- Transparent tiles are absent rather than allocated.
- Tile keys are deterministic row-major integers.
- Buffers are owned. Creating a store and applying a patch cannot mutate caller
  arrays or an earlier store version.
- Writes use copy-on-write and clone only changed tiles.
- A write returns the exact changed pixel bounds and sorted changed tile keys.
- Off-canvas writes are clipped; fully off-canvas or byte-identical writes are
  identity operations.
- Contiguous conversion is an explicit compatibility adapter, not an implicit
  operation performed for every edit.
- `PixelSource` exposes owned region reads and chunk iteration. Both tiled and
  contiguous sources implement the same renderer-facing contract.
- Tiled stores expose a deterministic versioned snapshot containing owned,
  row-major sparse tiles. Rehydration validates dimensions, coordinates,
  duplicates, tile lengths, and the no-transparent-tile canonical invariant.

The first implementation deliberately keeps tile lookup and ownership private.
Consumers use the store API so backing data can later move to shared memory,
OPFS, compressed cold tiles, or GPU resources without changing document tools.

## Dirty-region contract

`DirtyRegionAccumulator` clips invalidations to the canvas, merges touching
regions transitively, caps fragmentation, supports full invalidation, and
atomically consumes a render batch. Pixel patches contribute exact dirty bounds.
Structural changes, blend/effect changes, transforms, and adjustment layers may
conservatively invalidate larger regions or the full canvas until dependency
bounds become more precise.

The web paint path now transforms exact local patch bounds into document-space
AABBs and carries them through preview and command commits. The actual canvas
draw clips background, document rendering, and overlays to those regions. A
canvas resize or any operation without precise dependency data still performs a
full redraw. Advanced compositing is clipped at display time. Eligible progressive
pixel/group worker tasks also serialize only inverse-mapped viewport source tiles
and omit selection/channel coverage that the compositor does not read. Raster-masked
pixel layers prune both pixel and coverage tiles to the same layer-local viewport.
Raster-masked groups prune their document-space coverage alongside inverse-mapped
child pixels. Vector-mask paths remain compact metadata and rasterize only the
requested layer-local or document-space region. Effects and non-pixel layers
conservatively retain the full snapshot fallback.

For ordinary unmasked normal layers, the renderer inverse-transforms document
dirt into layer-local bounds and asks the pixel source only for intersecting
chunks. Translated or affine-transformed sparse layers therefore avoid uploading
unaffected tiles during a local redraw.

## Migration sequence

1. **Complete:** establish the tile package, contiguous compatibility adapter,
   ownership validation, snapshots, and storage benchmarks.
2. **Complete for pixel layers:** make `TiledPixelStore` the canonical pixel
   resource. Creation, duplication, patch/history operations, worker messages,
   PSD/native imports, and renderer pixel-source resolution retain tile
   ownership. Brush and eraser commits write only changed tiles. Advanced masks
   or blend modes explicitly materialize at the legacy compositor boundary.
3. **Coverage ownership integrated for selections and channels:** brush, eraser,
   and mask patch bounds reach the live render scheduler. Pixel and raster-mask
   writes are copy-on-write; masks cross validated worker snapshots. Selections
   and saved alpha channels own required sparse stores without a dual contiguous
   mirror field. Rectangular selection creation writes only dirty patches;
   outline/bounds analysis and copy/load/save share stores tile-locally.
   Remaining dense algorithm boundaries are explicit and documented.
4. **Composite tile cache integrated into the live advanced path:** `CompositeTileCache`
   keys tiles by layer dependency signatures (visibility, opacity, blend, clipping,
   transforms, effects, and source tile buffer identities), tile coordinates, and
   mip 0. Misses populate from the deterministic reference compositor; warm
   re-composites assemble without re-invocation. LRU eviction enforces a memory
   budget. `renderDocument` accepts an optional cache with viewport-priority
   scheduling and progressive viewport-only display; the web app retains a
   session cache and exposes warm/hit diagnostics on the canvas dataset.
   Region-local miss population, mip levels, and AbortSignal cancellation are
   supported. Pixel layers inverse-map requested regions into bounded source
   patches for translation, scale, rotation, or shear; pass-through and isolated
   groups apply combined raster/vector mask density through matching region reads
   and bounded path rasterization. Progressive pixel/group worker snapshots are
   viewport-pruned; enabled pixel effects expand the requested document region
   by their exact stroke/drop-shadow halo before inverse mapping;
   results return only a tile-aligned mip-space region, and `seedRegion` writes
   those dependency tiles without allocating a full temporary frame. Full refine
   output remains canvas-sized. RGB channel previews reuse the same dependency
   tiles and transform only the assembled display buffer; alpha previews read
   requested coverage regions directly. Rasterizers without region output retain
   the exact full-reference fallback.
5. Persist native documents tile-by-tile and decode PSD channels lazily into the
   same store.
6. Remove mandatory full-buffer materialization from ordinary display and edit
   paths. Retain explicit contiguous adapters only for codecs or algorithms that
   have not yet migrated.

No phase may silently change pixel precision, alpha representation, mask space,
or file round-trip behavior. Golden-image output remains the correctness gate;
benchmark improvements are not accepted if pixels regress.

Canonical pixel-layer storage no longer depends on a web display cache. The
renderer consumes each layer's store directly, and worker messages use validated
owned tile snapshots. Document-bearing resize/PSD/native worker results transfer
every snapshot tile buffer back to the client with deduplicated ownership lists,
avoiding an additional structured-clone copy. Image resize maps allocated source
tiles to candidate destination tiles and samples pixels/coverage directly; it
does not create a dense rectangle spanning distant allocations. Filters retain documented bounded
or full-algorithm boundaries, while broad export/compositing paths materialize
explicitly where required. Magic Wand consumes rendered 256² regions: global
matching holds one source and coverage tile at a time, while contiguous flood
matching uses a bounded tile LRU. Advanced stacks can still reach the documented
full-composite region fallback. Complement-aware partial selection inversion,
native content-addressed storage, lazy PSD channel decoding, and broader
composite-region support remain required before this subsystem can be marked
functionally complete.
