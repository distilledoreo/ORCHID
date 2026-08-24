# File Formats

External files are untrusted input. Support is capability-based: the open and
export UI should advertise only formats actually implemented in the current
build.

## Phase A policy

- Validate byte signatures; do not trust an extension or MIME type alone.
- Bound dimensions, decoded pixel count, nesting, and allocation before decode.
- Reject malformed or unsupported files with an actionable message.
- Export is explicit and must not overwrite an input without user choice.
- A successful import is not a promise of lossless round-trip compatibility.

Browser-decodable raster import and standard raster export may be used by the
vertical slice where implemented. Exact codecs and limitations belong beside
their tests. IndexedDB recovery currently uses internal schema v16 with explicit
v1–v15 migrations. Schema v4 added owned full-resolution saved alpha channels;
schema v5 replaces translation-only layer state with canonical affine matrices.
Schema v6 adds blend/fill/clipping/lock properties, group compositing mode, and
typed adjustment layers with optional document-space raster masks. Schema-v5
documents migrate to Normal, full fill opacity, no clipping, no locks, and
pass-through groups.
Schema v7 preserves editable rectangle/ellipse shape and text layers; raster
exports composite those layers without changing their structured payloads.
Schema v8 adds ordered editable layer effects without changing external raster
export formats.
Schema v9 adds recursively serialized embedded smart-object sources. Imported
raster replacements become owned nested sources; this is not yet linked-file or
PSD smart-object compatibility.
Schema v10 adds ordered editable smart filters to embedded smart objects. Schema
v9 snapshots migrate with empty filter stacks; this remains an internal recovery
capability rather than PSD smart-filter compatibility.
Schema v11 adds the expanded deterministic blend-mode enum. Schema v10 snapshots
retain their original mode unchanged.
Schema v12 adds ordered move, line, cubic, and close commands to shape geometry.
Schema v11 rectangles and ellipses migrate with empty command lists.
Schema v13 adds owned pixel-layer vector masks with path commands, enabled state,
density, and inversion. Schema v12 snapshots migrate with no vector mask.
Schema v14 extends the same owned vector-mask contract to shape, text,
smart-object, and adjustment layers. Schema v13 snapshots migrate those layer
kinds with no vector mask.
Schema v15 adds document-space vector masks to groups. Schema v14 snapshots
migrate groups with no vector mask.
Schema v16 adds canvas-sized document-space raster masks to groups. Schema v15
snapshots migrate groups with no raster mask.
An applied crop needs no new schema: v5 already records its canvas dimensions,
root transforms, and remapped selection/channel buffers. Crop drafts and undo
history are not serialized. Canvas Size and Reveal All use the same existing
fields. Image Size serializes its already-resampled pixel and mask buffers,
scaled transforms, selection, and channels without a schema revision. Dialog
state, resampling choice, and undo history are not serialized. Applied results
recover through IndexedDB and determine subsequent PNG export dimensions and
pixels. The same canonical schema is now framed by the versioned, checksummed
`.freetoshop` user-facing format described in
[NATIVE_FORMAT.md](NATIVE_FORMAT.md). IndexedDB records remain internal and do
not include the native container header.

Selection-engine operations change the existing schema-v16 selection coverage;
saved results can be stored as existing alpha channels. Applied filters change
only existing pixel-layer bytes. Tool geometry, Magic Wand settings, refinement
settings, filter parameters, and cancelled/live previews are not serialized, so
no schema migration is required. Applied selection and pixel results participate
in normal IndexedDB recovery and subsequent PNG export. Smart filters and
adjustment layers round-trip in both IndexedDB recovery and native format v1;
this is not PSD interoperability.

PSD/PSB has separate limitations in
[PSD_PSB_COMPATIBILITY.md](PSD_PSB_COMPATIBILITY.md).
PSD v1 import now detects its byte signature, validates the header, and decodes
supported 8-bit RGB layer previews in a worker. It is an import translation, not
round-trip preservation; PSD/PSB export remains unavailable.
