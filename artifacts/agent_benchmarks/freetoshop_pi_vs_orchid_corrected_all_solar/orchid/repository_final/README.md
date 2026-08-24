# FreetoShop

FreetoShop is a local-first, browser-based image editor. Its working foundation
now includes the first Phase B raster workflow. It is not yet a claim of
feature parity with Photoshop or any other commercial editor.

## Current scope

Phase A provides a strict TypeScript monorepo, a headless document/command
model, affine layer rendering through Canvas 2D, undo/redo, PNG/JPEG
import and export, content-addressed incremental IndexedDB persistence, a PWA shell, and responsive accessible
UI. Phase B adds a pressure-aware hard/soft round brush, eraser, rectangular
full-resolution selections with replace/add/subtract/intersect algebra,
selection-constrained brightness, layer movement, and nondestructive pixel-layer
raster masks with enable, density, inversion, persistence, and direct
selection-aware Brush/Eraser editing. Shape, text, and smart-object layers expose
the same local raster-mask controls and painting without rasterizing their source.
Groups expose the same controls through canvas-sized document-space raster masks
that constrain the combined pass-through or isolated result.
Pixels and masks are explicit edit targets,
and each pointer gesture is a single undoable transaction. Saved full-resolution
alpha channels can be created from selections, renamed, previewed, restored as a
selection, deleted, undone/redone, and recovered locally. Composite RGB channels
also have read-only grayscale inspection views, while export always uses the
unchanged composite document. Layers, Channels, and History are functional,
keyboard-navigable panels. Layers now use canonical affine matrices with nested
group composition, numeric matrix/position editing, centered 90-degree rotation,
horizontal/vertical flipping, reset, transformed painting and adjustment
coordinates, undo/redo, export, and local recovery. Free Transform adds
constant-screen-size accessible corner/edge scale and rotation handles, body
translation, live nondestructive preview, Shift-constrained scaling, keyboard
handle operation, Escape cancellation, and one history transaction per pointer
gesture. The Crop tool accepts a canvas drag or exact integer bounds, previews
the proposed rectangle, and commits one undoable canvas change. Cropping keeps
layer-local pixels and raster masks intact, translates root-layer placement,
remaps selections and saved alpha channels, and is preserved by local recovery
and PNG export. Crop, Canvas Size, Reveal All, and Image Size limit canvases to
32,768 pixels per axis and 100,000,000 pixels in total. Canvas Size can grow or
trim around any of nine anchors while preserving layer resources and remapping
selections and saved
channels. Reveal All expands, but never shrinks, the canvas to contain the
transformed bounds of visible, nonzero-opacity pixel layers. Image Size
resamples raster layers, masks, selections, and channels with nearest-neighbor
or bilinear pixel-center sampling; RGBA bilinear interpolation uses
premultiplied alpha. It preserves nested affine placement. All three operations
are undoable and survive PNG export and local recovery. Phase B selection tools
now include a four-times-supersampled ellipse, a freehand lasso converted to an
antialiased even-odd polygon, and a composite-image Magic Wand with 0–255
tolerance and contiguous/global matching. All selection tools support replace,
add, subtract, and intersect; full-resolution masks can be feathered, expanded,
contracted, smoothed, bordered, saved as alpha channels, undone, and recovered.
File → Export Selection PNG composites only the selected bounds and applies soft
selection coverage to the exported alpha channel.
Canvas chrome follows the actual arbitrary mask boundary instead of reducing it
to a bounding rectangle. The Wand samples the flattened composite, includes
alpha, and uses four-neighbor connectivity in Contiguous mode.

The destructive Filters dialog provides live, cancellable previews and one-step
application of Contrast, Levels, Hue/Saturation, Invert, Grayscale, Threshold,
Posterize, and Gaussian Blur. Filters respect soft document selections through
nested affine layer placement while changing only the selected pixel layer's
source pixels. Gaussian Blur is separable, edge-clamped, and
premultiplied-alpha aware, with an integer radius from 0 through 100.

The schema-v12 layer stack supports 26 deterministic professional blend modes:
the Darken/Lighten families, Dodge/Burn families, contrast families, Difference,
Exclusion, Subtract, Divide, and the nonseparable Hue, Saturation, Color, and
Luminosity modes in addition to Normal. Advanced stacks use a deterministic
premultiplied-alpha CPU compositor;
ordinary Normal-only stacks retain the direct Canvas path. Layers have separate
opacity and fill opacity, clipping relationships, transparency/pixel/position/all
locks, and visible state badges. Isolated groups composite into an intermediate
surface, while pass-through groups feed their children into the parent stack.
Editable Brightness/Contrast, Levels, Hue/Saturation, and Invert adjustment
layers render nondestructively, can be clipped, and support enableable,
density-adjustable document-space raster masks. These properties, payloads, and
masks round-trip through IndexedDB. Schema v7 also stores editable shape layers
plus multiline text layers with font family, size, weight, italic, line height,
alignment, and color. Rectangle, ellipse, and arbitrary line/cubic vector paths
retain editable fill, stroke, dimensions, commands, anchors, control points, and
corner radius where applicable. The Pen tool creates open or closed paths;
selected paths expose transformed on-canvas anchors and Bézier controls with
pointer dragging, keyboard nudging, live preview, cancellation, and one-step undo;
structured layers participate in transforms,
blending, clipping, grouping, duplication, recovery, compositing, and PNG export.
Local TrueType/OpenType font loading activates lazy HarfBuzz shaping with
kerning, standard/contextual ligatures, inferred LTR/RTL direction, positioned
glyph outlines, and shaped line wrapping. The text panel identifies when a
layer is using that professional path. System fonts remain an explicit Canvas
fallback when their binary data is unavailable.
Pixel, shape, and text layers share accessible Free Transform handles and
keyboard rotation/scaling without rasterizing structured content.
Editable color overlay, outside stroke, and drop-shadow layer effects share the
same nondestructive display/export compositor and remain adjustable after local
recovery. Renderable layers can be converted into embedded smart objects without
flattening their source. Smart objects retain an owned nested layer tree,
composite recursively, transform nondestructively, accept effects, support local
raster replacement, and open in an isolated full editor session. Saving source
edits creates one parent undo step; cancelling restores the untouched parent.
They survive export and recovery. Each smart object also owns an ordered,
enableable smart-filter stack with editable opacity. Invert, Grayscale, Gaussian
Blur, and Brightness/Contrast render nondestructively between the nested source
composite and the object's outer mask/effects. Nesting is capped at 16 levels.
Pixel, shape, text, smart-object, adjustment, and group layers support an independent editable vector mask with owned path
commands, enable/disable, density, inversion, undo/redo, export, and recovery.
Vector and raster masks multiply nondestructively when both are present.
Schemas v1-v15 migrate explicitly. Group masks apply in document space to the combined
pass-through or isolated group result.
The layer panel also supports Ctrl/Command multiselect, recursive duplication,
grouping of common-parent layers, visual hierarchy indentation, safe ungrouping
of Normal pass-through groups, multi-delete, root-layer merging, and flattening
visible content. Structural operations are single history transactions and
survive local recovery. Merge and Flatten rasterize through the same compositor
used by PNG export, so nondestructive adjustments are baked only when the user
explicitly chooses one of those commands.

Initial import-only PSD support recognizes the binary signature, validates
8-bit RGB PSD v1 files, and parses them in a cancellable worker. Pixel layers,
placement, supported blend modes, visibility, opacity, locks, clipping, and
nested groups translate into editable native layers. Structured previews are
rasterized and unsupported content is reported; masks, effects, channels,
metadata, PSB, higher bit depths, non-RGB modes, and PSD saving remain
unsupported. Keep the original PSD because FreetoShop does not preserve it for
round-trip export.

Save document or Ctrl/Command+S downloads the editable `.freetoshop` native
format. Version 2 stores canonical schema metadata plus binary pixel and coverage
tiles once per SHA-256 content ID; the outer payload and every resource are
validated before a worker replaces the current document. Legacy v1 JSON files
still open. The downloaded file remains a complete snapshot rather than an
incrementally updated or compressed large-document format.

Mixed-direction bidi paragraphs, vertical text, text-on-path, per-character
styles, variable-font controls, font fallback, and font embedding remain
unsupported. Boolean path operations,
linked/reusable smart
object instances, editable/spot channels, perspective and
warp transforms, smart-filter masks/blend options and broader professional filter
families, advanced color workflows, plugins, scripting, and PSD/PSB round-trip
interoperability remain unsupported. Image resizing currently operates on 8-bit
sRGB-like buffers in memory; bilinear is not an area-reduction filter, and tiled
or high-bit-depth image backends are not implemented. Selection algorithms and
live filter previews remain synchronous; committed filters and image resampling
run in module workers. The editor does not yet provide
magnetic/quick or object-aware selection, color range, refine-edge analysis, GPU
processing, or a broader nondestructive filter library. The current advanced
compositor is whole-canvas and CPU-bound. A sparse tiled storage and dirty-region
rendering foundation exists, but canonical layer storage has not migrated to it
and rendering is not GPU-accelerated;
pass-through group opacity is applied to children and does not yet implement
every Photoshop knockout/effect interaction.

The project is private and under active development. Expect file formats and
internal APIs to change before a compatibility promise is published.

## Development

Requirements: Node.js 22 or newer and npm.

```sh
npm install
npm run dev
```

Useful checks:

```sh
npm run typecheck
npm run lint
npm test
npm run audit
npm run build
npm run test:e2e
npm run benchmark
```

Netlify builds from the repository root using [netlify.toml](netlify.toml),
publishes `apps/web/dist`, and uses the same Node/npm versions as local and CI
validation. The browser CI job builds that production output before launching
the preview server.

See [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md),
[ARCHITECTURE.md](ARCHITECTURE.md), [TESTING.md](TESTING.md), and
[CONTRIBUTING.md](CONTRIBUTING.md) before making behavioral changes.
