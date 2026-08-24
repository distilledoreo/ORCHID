# Document Model

Pixel layers canonically own immutable sparse `TiledPixelStore` resources.
Pixel patches use copy-on-write tiles, so history versions may share unchanged
tiles without exposing mutable RGBA buffers. Contiguous materialization is an
explicit compatibility operation at legacy filter, resize, codec, export, or
version-1 persistence boundaries. Raster masks, selections, and saved alpha
channels own immutable sparse one-channel `TiledCoverageStore` resources only;
there is no dual contiguous ownership field. Coverage stores carry an implicit
8-bit default plus sparse deviation tiles, so partial selection and raster-mask
complements remain sparse through inversion, crop, resize, worker transport,
native persistence, and selection refinement. Feather, square maximum/minimum,
median smooth, and border refinement process only deviation-tile neighborhoods
plus the finite-canvas edge bands required by zero-outside semantics. Some
filter sampling paths still densify at documented algorithm boundaries. Affine selection-to-raster-mask baking reads only active
selection tiles and writes a bounded sparse destination rather than allocating
a full layer-sized coverage buffer. Sparse selection combine also reconciles
different source tile sizes on one bounded output grid without materializing
either full canvas. The
complete ownership contract is specified in
[TILED_RENDERING.md](TILED_RENDERING.md).

The cold-composite worker keeps an isolated registry of immutable tiled
resources. Version-1 task envelopes upload each pixel/coverage snapshot once per
store and snapshot scope, then use resource references for later metadata-only
document revisions. Version-2 delta envelopes derive the next immutable store
from its retained predecessor, transferring only COW tiles whose buffer identity
changed and listing removed tile keys without a payload. A task carries its live
resource set so superseded stores are evicted on both sides. Abort terminates the
worker and discards the namespace; it never reuses references or delta bases
across cancellation. Filter, resize, and native-export operations share stable
document resource slots in one sequential session. Resize, PSD-import, and
native-import results use collision-safe request-scoped resource IDs; the worker
retains owned clones before transferring buffers, and the client adopts hydrated
stores under those same IDs for zero-upload follow-up tasks.

The document model is the canonical, serializable description of an edit. A
document has a version, canvas dimensions, ordered layers, and stable IDs.
Renderable layers refer to image resources rather than DOM or canvas objects.
Viewport zoom, pan, selection chrome, dialogs, and hover state are transient UI
state. The full-resolution selection mask is document state because raster
commands, masks, and adjustments consume its 8-bit coverage.
Free Transform snapshots, active handles, pointer positions, and live previews
are also transient; only the final validated affine matrix enters document
history and persistence. Crop drafts are likewise transient; an applied crop is
represented by new canvas dimensions, translated root transforms, and remapped
canvas-sized coverage buffers.

## Invariants

- Dimensions, coordinates, opacity, and resource sizes are finite and bounded.
- Layer IDs are unique within a document; layer order is deterministic.
- Unsupported layer kinds or enum values fail validation rather than being
  executed or silently reinterpreted.
- Edits are expressed as commands or equivalent pure state transitions so they
  can be tested and later support undo/redo.
- Saved documents contain data, never executable JavaScript.
- Every layer owns an invertible Canvas-style affine matrix (`a` through `f`).
  Nested world transforms compose parent × child, supporting translation,
  rotation, scale, reflection, and shear without rewriting source pixels.
- A selection is either `null` (all pixels) or an owned canvas-sized sparse
  `TiledCoverageStore`. Rectangles write only dirty patches; arbitrary masks can
  replace, add, subtract, or intersect coverage. Add uses coverage union;
  subtract and intersect multiply soft coverage rather than thresholding it.
  Saved alpha channels use the same sparse ownership contract.
- Ellipse and lasso results retain 8-bit antialiased edges. Magic Wand produces
  binary coverage from composite RGBA byte distance. Feather, expand, contract,
  smooth, and border produce new owned masks and enter history as one selection
  command; derived outline geometry and live gesture previews remain view state.
- Selection-engine canvases are limited to 32,768 pixels per axis and
  100,000,000 pixels total. The UI limits refinement radii to 100 pixels. Its
  feather is a zero-padded square box filter, expand/contract are square
  maximum/minimum morphology, smooth is a square median, and border is expanded
  coverage minus contracted coverage; these are deterministic foundations, not
  a professional Select and Mask/refine-edge model.
- Pixel, shape, text, smart-object, group, and adjustment raster masks own
  immutable sparse 8-bit coverage stores. Their enabled state and normalized density are nondestructive
  layer properties; painting a structured layer mask never rasterizes its source.
- Mask painting applies immutable bounded coverage patches and never rewrites
  the owning layer's RGBA buffer. Pixel/mask targeting is transient UI state.
- Every layer owns a validated blend mode, independent opacity/fill opacity,
  clipping flag, and transparency/pixel/position/all lock set. Pixel locks reject
  raster and mask mutation; transparency locks preserve existing alpha during
  pixel patches; position locks reject affine edits; all locks reject property
  edits. Groups additionally choose pass-through or isolated compositing.
- Adjustment layers own a typed Brightness/Contrast, Levels, Hue/Saturation, or
  Invert payload. Their optional raster mask is canvas-sized document-space
  coverage; parameters and masks remain editable and never rewrite underlying
  pixel layers.
- Shape layers own validated rectangle, ellipse, or arbitrary path geometry and
  editable fill, stroke, stroke width, and rectangle corner radius. Paths own
  ordered move, line, cubic Bézier, and close commands with finite anchors and
  controls, bounded to 10,000 commands. Text layers own multiline
  content, a fixed layout box, and validated family, size, weight, italic,
  line-height, alignment, and color style data. Both remain structured rather
  than storing cached pixels and may carry a layer-local raster mask.
- Renderable layers own an ordered list of validated color-overlay, outside-
  stroke, and drop-shadow effects. Effect enabled state, opacity, color, blend
  mode, size, offsets, and blur are canonical editable data; source pixels and
  structured payloads are never baked when an effect changes.
- Embedded smart-object layers own a stable source ID, source canvas, and nested
  layer tree. Conversion moves the original layer payload into an identity-
  transformed source while retaining outer placement and compositing;
  replacement swaps the source atomically and clears an incompatible outer
  mask. Nested editing uses a separate history; Save commits one source-
  replacement command to the retained parent history, while Cancel restores the
  parent without mutation. Programmatic and serialized nesting is limited to 16 levels.
- Duplicate recursively safely shares immutable pixel and raster-mask
  resources, owns mutable adjustment resources, and assigns new
  stable descendant IDs. Grouping requires common-parent layers and preserves
  their stack order. Ungrouping is limited to unclipped Normal pass-through
  groups so composing the group transform and opacity into its children does
  not silently change rendered pixels. Merge replaces selected root siblings at
  their lowest stack position; Flatten replaces all roots. Their rasterization
  is supplied by the renderer before one core tree-replacement command.
- Saved alpha channels are owned, uniquely identified, canvas-sized 8-bit
  coverage buffers. Saving and loading preserve soft coverage exactly; channel
  preview choice remains transient UI state.
- Cropping is nondestructive with respect to layer resources. It pre-multiplies
  each root transform by the inverse crop-origin translation while leaving
  nested transforms, pixel buffers, and layer-local raster masks intact.
  Explicit selection and saved-channel coverage is copied into the new canvas;
  crop area outside the old canvas receives zero coverage. An implicit `null`
  selection remains the full new canvas.
- Crop coordinates are integers, dimensions are positive, neither axis may
  exceed 32,768 pixels, and total area may not exceed 100,000,000 pixels.
- Canvas Size supports top, center, and bottom crossed with left, center, and
  right anchors. The anchor fixes the corresponding old-canvas point to a
  deterministic nearest-pixel position. Root transforms receive the resulting
  translation; nested transforms, pixel buffers, and layer-local masks remain
  unchanged. Canvas-sized selection and channel coverage is shifted, trimmed,
  or zero-padded with the canvas.
- Reveal All unions the existing canvas with the floor/ceil bounds of every
  visible, nonzero-opacity pixel layer after nested affine transforms. Hidden
  layers and zero-opacity branches do not expand it. It is deliberately
  bounds-based: transparent source pixels and mask coverage are not scanned, so
  a fully transparent or masked layer can still contribute geometric bounds.
- Image Size scales the document coordinate system, resamples every pixel layer
  and layer-local raster mask, and resamples explicit document selections and
  saved channels to the new canvas. Root transforms absorb document scaling;
  nested affine transforms remain structurally nested, while resized pixel
  layers compensate their local coordinate scale so transformed content keeps
  its proportional document placement. A `null` selection remains implicit
  full-canvas coverage.
- Canvas Size, Reveal All, and Image Size enforce the same 32,768-pixel-per-axis
  and 100,000,000-pixel canvas-area ceilings as Crop. Image Size also validates
  every resized layer allocation against those ceilings and rejects an estimated
  resize working set above 1,000,000,000 bytes.
- Destructive filters are not document objects. They return a minimal changed
  rectangle whose RGBA bytes replace part of one pixel layer in one history
  step. Cancelled live previews and filter parameters are transient and are not
  persisted. Color filters retain source alpha; Gaussian Blur may blur alpha and
  blends its result through soft selection coverage in premultiplied space.
- Filter output is capped at 512 MiB. Gaussian radius is an integer from 0
  through 100; its estimated Float32 convolution buffer plus source bytes is
  separately capped at 512 MiB. Operations are synchronous CPU passes over the
  selected pixel layer; they neither bake nor modify its separate raster mask.

The current model supports affine-transformed pixel/group, shape, and text layers,
full-resolution selections, raster masks and editable vector masks, saved alpha channels,
nondestructive canvas geometry operations, and destructive raster Image Size
resampling. It also supports arbitrary raster selections and destructive pixel
filter patches, 26 deterministic professional blend modes, clipping, isolated
groups, layer locks, four typed nondestructive adjustment layers, editable vector
paths, three editable layer effects, embedded smart objects, and ordered smart-filter stacks. Smart
filters have stable IDs, enabled state, opacity, and validated type-specific
parameters; filter IDs are unique within their owning smart object. Vector masks
on pixel, shape, text, smart-object, adjustment, and group layers own bounded
move/line/cubic/close commands plus enabled, density, and inversion state; they
combine multiplicatively with raster masks. Group raster and vector masks are canvas-sized and
constrain the group's combined pass-through or isolated result. Smart-filter masks/blend options, linked/shared smart-object
instances, embedded-font persistence, mixed-direction bidi text, Boolean path operations, Dissolve,
broader layer effects, and knockout behavior remain
reserved concepts unless tests and UI demonstrate otherwise.

Persistence schema v16 stores affine transforms, selections, raster masks, saved
alpha channels, layer compositing properties and locks, group isolation, and
typed adjustment, shape, text, and embedded smart-object layers plus editable
effects and smart filters. It explicitly migrates v1 snapshots with identity
transforms, schemas v2–v4 from legacy `{x,y}` translations, and schema v5 with
safe Normal/pass-through/unlocked defaults, plus schema v6 documents without
structured layers, schema v7 layers with an empty effects list, schema v8
documents with no smart objects, schema v9 smart objects with empty filter
stacks, schema v10 documents with the original blend-mode set, and schema v11
shape geometry with empty path-command lists, and schema v12 pixel layers with
no vector mask, schema v13 structured/adjustment layers with no vector mask, and
schema v14 groups with no vector mask, and schema v15 groups with no raster mask.
Future evolution
must follow the same migration rule. Crop requires no schema revision because
schema v16 already stores every changed field; recovery stores the applied
result, not transient dialog/draft state or the undo stack. Canvas Size, Reveal
All, and Image Size likewise require no schema change because their applied
canvas, transforms, raster resources, masks, selections, and channels already
belong to schema v16.
See [FILE_FORMATS.md](FILE_FORMATS.md).
