# Text Engine

FreetoShop uses `@freetoshop/text-engine` as the boundary between editable text
layers and professional OpenType shaping. HarfBuzz is loaded lazily only after a
user explicitly selects a local `.ttf`, `.otf`, or `.ttc` file. The font remains
on the device and is capped at 64 MiB.

## Current pipeline

1. The web app reads the user-selected font into an owned `ArrayBuffer`.
2. The text engine constructs a HarfBuzz face/font and retains it in an
   in-memory family registry.
3. Whole candidate lines are shaped with inferred LTR/RTL direction and the
   `kern`, `liga`, `clig`, and `calt` OpenType features.
4. HarfBuzz glyph IDs, clusters, advances, offsets, and outlines are converted
   into a bounded layout.
5. The renderer fills those positioned outlines for both display and export.

System fonts whose binary data is unavailable continue through the existing
Canvas text fallback. The text panel states which path is active. Loading a
local font while a text layer is selected switches that layer to the shaped
path without rasterizing it.

## Portability and limits

- Font binaries are not embedded in `.freetoshop` documents or IndexedDB.
  Reloading a document preserves the family name but requires the user to load
  the font again before professional shaping resumes. This avoids silently
  redistributing fonts whose embedding permissions are unknown.
- The current automatic direction detector selects a single LTR or RTL
  direction per line. Mixed-direction bidi paragraph resolution is not yet
  implemented.
- Font fallback across multiple faces, vertical layout, per-character styles,
  variable-axis controls, hyphenation, text-on-path, and editable OpenType
  feature controls remain future work.
- HarfBuzz WebAssembly is allowed by the CSP through the narrow
  `wasm-unsafe-eval` source expression. JavaScript string evaluation remains
  disallowed.

The renderer must keep the system-font path as an explicit compatibility
fallback; new typography behavior belongs in `text-engine`, not in additional
Canvas `measureText` or `fillText` branches.
