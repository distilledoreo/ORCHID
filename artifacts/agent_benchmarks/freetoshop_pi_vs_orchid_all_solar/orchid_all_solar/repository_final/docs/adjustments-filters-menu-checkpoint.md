# Direct adjustments and filters menu checkpoint

This bounded milestone replaces the old generic operation picker with direct Photoshop-style command discovery while preserving FreetoShop's existing engine, worker, command-history, and smart-object document behavior.

## Final menu tree

- Image → Adjustments → Contrast…, Levels…, Hue/Saturation…, Grayscale, Invert, Threshold…, Posterize…
- Filter → Last Filter, Convert for Smart Filters, Blur → Gaussian Blur…
- Layer → New Layer, New Adjustment Layer → Brightness/Contrast…, Levels…, Hue/Saturation…, Invert, then the existing layer commands

The old generic `Adjustments…`, `Filters…`, `filter.open`, and `layer.adjustment` commands are removed. The settings dialog no longer contains an operation-selection dropdown; each parameterized command opens its own operation-specific dialog. Grayscale and Invert execute immediately.

## Supported behavior

- Destructive Image adjustments remain selection-aware and pixel-layer-only.
- Adjustment-layer commands create the existing four nondestructive document adjustment types, select the new layer, activate Properties, and switch the narrow dock to Properties.
- Gaussian Blur is destructive on pixel layers and is stored as a nondestructive smart filter on smart objects.
- Convert for Smart Filters reuses the existing smart-object conversion command.
- Last Filter starts disabled, remembers successful Gaussian Blur settings in the current editor session, and repeats without reopening the dialog.
- The Layers footer and Adjustments panel execute the same registry commands as the Layer menu.
- Recursive submenus use shared ARIA/keyboard/pointer behavior and clamp nested popups to the viewport. Forced-colors and reduced-motion behavior remain covered by the existing shell styles and tests.

Unsupported categories are intentionally omitted: there are no empty Blur, Sharpen, Noise, Distort, Pixelate, Render, Stylize, or Other menus, and no unsupported smart-filter document types were introduced. This is not full Photoshop filter or adjustment parity, full plugin/filter-gallery support, or nondestructive Image-adjustment support.

## Validation

Targeted command-tree and operation-definition tests pass. The direct menu E2E suite covers eight deterministic Chromium workflows, including menu hierarchy, operation dialogs, footer/panel reuse, keyboard return focus, and Last Filter's initial disabled state. The existing editor adjustment/history regression also passes.

The full validation commands for this checkpoint are:

```text
npm run typecheck -- --pretty false
npm run lint
npm run format:check
npm test
npm run build --workspace @freetoshop/web
npm run audit
npm run benchmark
npm run test:ui-visual
npx playwright test --project=chromium
npx playwright test --project=firefox
npx playwright test --project=webkit
```

No image-processing algorithms or mathematical filter outputs were changed. Selection/channel dual contiguous mirror ownership was subsequently removed (see `TILED_RENDERING.md` and `PHOTOPEA_PARITY_MATRIX.md`). The exact next task is M2 dependency-keyed composite tile caching, with remaining tile-local selection operators completed when they block large-document workflows.

## Smart-filter live preview (added)

While Filter → Gaussian Blur is opened on a smart object (`mode: smart`), the
canvas shows a temporary smart-filter stack via
`previewDocumentWithSmartFilterDraft` (preview filter id
`smart-filter-preview`). Apply still commits one atomic undo step; Cancel
clears the preview without history.

## Direct-operation apply E2E (added)

Tri-engine suite `tests/e2e/adjustments-filters-menu.spec.ts` now covers **11**
workflows per engine (33 total), including:

- Immediate **Invert** apply with undoable history step
- **Gaussian Blur** apply enabling **Last Filter** repeat without reopening the dialog
- **Smart-filter dialog** live-preview copy on Convert for Smart Filters path

Evidence: Chromium / Firefox / WebKit all green (33 passed).
