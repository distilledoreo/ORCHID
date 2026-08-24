# PSD/PSB Compatibility

FreetoShop has an initial **import-only** PSD reader backed by pinned `ag-psd`
31.0.2. It is not a PSD editor or round-trip implementation: imported content
is translated into the native document model, and the original PSD payload is
not retained for saving. There is no PSD or PSB export.

| Capability                     | Current status                                                                                                                                                                                                                                      |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PSD signature/version          | PSD v1 only; signature checked from bytes                                                                                                                                                                                                           |
| Color mode / depth             | 8-bit RGB only                                                                                                                                                                                                                                      |
| Dimensions                     | 1–16,384 px/axis; 100M pixels; 512 MiB decode ceiling                                                                                                                                                                                               |
| Pixel layers                   | Cropped bounds, visibility, opacity, clipping, and locks                                                                                                                                                                                            |
| Layer ordering / nested groups | Imported into bottom-to-top native groups                                                                                                                                                                                                           |
| Blend modes                    | Supported native modes mapped; unsupported modes become Normal                                                                                                                                                                                      |
| Text layers                    | Imported as editable text (plain text + basic style subset)                                                                                                                                                                                         |
| Smart objects                  | Embedded SO from placedLayer raster preview; **nested source from linked PSD payload** (depth ≤2) when signature matches                                                                                                                            |
| Linked files                   | Metadata + base64 payloads ≤4 MiB in document.preservation; used as nested SO sources when PSD                                                                                                                                                      |
| Vector previews                | Rasterized as pixel layers when preview exists                                                                                                                                                                                                      |
| Composite-only documents       | Imported as one `PSD composite` pixel layer                                                                                                                                                                                                         |
| Raster layer masks             | Imported when mask imageData is present (layer-local coverage)                                                                                                                                                                                      |
| Vector layer masks             | Imported as layer-local Bezier PathCommands (boolean ops flattened)                                                                                                                                                                                 |
| Layer effects                  | drop-shadow, stroke, color-overlay imported; others warned/skipped                                                                                                                                                                                  |
| Unknown/skipped layer records  | Attached as opaque `document.preservation` (psd-import-v1); native schema 19 round-trips                                                                                                                                                            |
| Guides / grid                  | Imported into first-class `document.guides` / `document.grid` (schema ≥18); also kept in preservation bag                                                                                                                                           |
| Adjustment layers              | invert, brightness/contrast, levels, hue/saturation, posterize, threshold imported; others preserved+warned                                                                                                                                         |
| Saved alpha channels           | Names from imageResources → `document.channels` (sparse). Coverage from injects, **multi-plane raw/RLE composite decoder** (`decodePsdAlphaPlanes` for 8-bit RGB), or single ag-psd composite A plane. Zip/16-bit/PSB multi-plane still unsupported |
| Document paths                 | Native `document.paths` (schema 19) from injects **or** image resource path blocks (IDs 2000–2998, 8.24 fixed-point); path selection state preserved                                                                                                |
| Image resources (metadata)     | resolution, guides/grid, global light, pixel aspect, XMP (≤256 KiB), iccUntagged flag preserved in bag (not live-applied)                                                                                                                           |
| CMYK, Lab, grayscale, indexed  | Rejected                                                                                                                                                                                                                                            |
| 16-bit / 32-bit                | Rejected                                                                                                                                                                                                                                            |
| PSB                            | Rejected before decode                                                                                                                                                                                                                              |
| PSD/PSB save / unknown blocks  | Unsupported                                                                                                                                                                                                                                         |

Parsing runs in a cancellable module worker. The pinned `ag-psd` parser is a
synchronous, opaque phase, so it can report only start and completion; cancelling
the editor terminates that worker. FreetoShop-owned layer conversion and the five
document-resource passes report monotonic progress and accept `AbortSignal`
checkpoints. Result tiles then stream into private stores before atomic adoption.
A completed import is rejected if the open document changed while parsing, so
stale results cannot replace newer work. The UI explicitly reports that PSD
export is unavailable and counts compatibility warnings returned by the importer.

The deterministic corpus lives in
`packages/file-codecs/test-fixtures/psd` and covers a flattened RGB document,
positioned pixel layers with opacity/blending, a nested pass-through group, a
truncated header, and a PSB header. Unit tests also inject structured and
previewless layers to verify rasterization/skip reporting. This matrix describes
only those tested behaviors; visual similarity is not lossless compatibility.

Users must retain the original PSD separately. Standard raster export from an
imported document permanently flattens unsupported PSD-specific structure.

## Compatibility corpus (repository-safe)

| Fixture                                                            | Purpose                                              | Notes                                                                            |
| ------------------------------------------------------------------ | ---------------------------------------------------- | -------------------------------------------------------------------------------- |
| `packages/file-codecs/test-fixtures/psd/multi-alpha-raw-rgb-8.psd` | 8×4 8-bit RGB + 2 raw alpha planes                   | Synthetic; decoded via `decodePsdAlphaPlanes` (no ag-psd injects)                |
| `mask-effects-guides-rgb-8.psd`                                    | 8×8 layers + user mask + drop-shadow/stroke + guides | Real binary import (no parse injects); unit asserts sparse mask, effects, guides |
| `text-layer-rgb-8.psd`                                             | 16×8 text layer + background                         | Real binary import; unit asserts importedTextLayers ≥ 1                          |
| `smart-object-linked-rgb-8.psd`                                    | 8×8 placedLayer SO + linked nested PSD               | Real binary import; unit asserts smart-object layer + source layers              |
| `work-path-resource.psd`                                           | 100×80 resource 2000 Work Path only                  | Path decoder corpus; minimal header+resources (no layer section)                 |
| `flattened-rgb-8.psd`                                              | Basic 8-bit RGB                                      | Existing unit import host                                                        |
| `pixel-layers-rgb-8.psd`                                           | Layered RGB                                          | Existing                                                                         |
| `nested-group-rgb-8.psd`                                           | Groups                                               | Existing                                                                         |
| `truncated.psd` / `psb-header.psb`                                 | Malformed / PSB reject                               | Security bounds                                                                  |
