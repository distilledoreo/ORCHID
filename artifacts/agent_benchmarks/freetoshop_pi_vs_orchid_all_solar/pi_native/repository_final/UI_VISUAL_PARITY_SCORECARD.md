# FreetoShop Rendered Workspace Visual Parity Scorecard

## Final decision

All bounded P0 acceptance gates pass in the rendered production-equivalent
application. The checkpoint is **Functionally complete**: the professional
workspace hierarchy, geometry, evidence, and supported workflows are in place,
while the P1 refinements below remain deliberately deferred. This status does
not imply Photopea feature parity or production readiness.

The comparison authority is FreetoShop's generated screenshots, not component
existence. Adobe assets, branding, icons, and source material were not copied.

## Rendered evidence

All captures use Chromium, browser zoom 100%, device scale factor 1, the
production preview, and deterministic editor operations.

| Cycle          | Evidence                                         |
| -------------- | ------------------------------------------------ |
| Before         | `docs/ui-checkpoints/visual-parity/before/`      |
| Iteration 1    | `docs/ui-checkpoints/visual-parity/iteration-1/` |
| Iteration 2    | `docs/ui-checkpoints/visual-parity/iteration-2/` |
| Accepted final | `docs/ui-checkpoints/visual-parity/final/`       |

Accepted final files:

- `2048x1046-layered.png`
- `1672x941-new.png`
- `1672x941-layered.png`
- `1672x941-selection.png`
- `1672x941-transform.png`
- `1440x900-layered.png`
- `1366x768-layered.png`

## Final measured geometry

| Viewport    | Menu / options / tabs / status | Rail | Canvas     |    Layers | Properties |   History | Total dock |
| ----------- | ------------------------------ | ---: | ---------- | --------: | ---------: | --------: | ---------: |
| 2048 × 1046 | 38 / 44 / 36 / 30              |  104 | 1308 × 898 | 320 × 898 |  316 × 539 | 316 × 359 |        636 |
| 1672 × 941  | 38 / 44 / 36 / 30              |  104 | 932 × 793  | 320 × 793 |  316 × 476 | 316 × 317 |        636 |
| 1440 × 900  | 38 / 44 / 36 / 30              |  104 | 816 × 752  | 519 × 347 |  519 × 237 | 519 × 167 |        520 |
| 1366 × 768  | 38 / 44 / 36 / 30              |   46 | 800 × 620  | 519 × 292 |  519 × 195 | 519 × 133 |        520 |

At 1672 × 941, the Layers list has at least 360px usable height and nine
fixture rows, with at least eight rows intersecting its visible viewport. The
document is entirely within and centered in the canvas viewport within the
48px asserted tolerance. Body overflow and normal panel horizontal overflow
are absent.

## Three correction cycles

### Cycle 1

The ten largest differences were the single dock, placeholder Color/Swatches
priority, constrained Layers height, icon-only rail, undersized chrome,
scrolling Layers controls, generic thumbnails, form-like Properties, global
status bar, and weak document/workspace relationship. The correction added
independent 320px/316px docks, a 104px labeled rail, 38/44/36px chrome, visible
History, persistent workspace v3, and baseline geometry assertions.

### Cycle 2

The five largest remaining differences were shallow fixture content, generic
previews, oversized Layers actions, tall rows, and non-contextual Properties.
The correction added a nine-row real editor fixture, tile-sampled cached pixel
and mask thumbnails, 42px rows, a fixed 44px footer, and transform-first
Properties.

### Cycle 3

Final correction scoped the 30px status strip precisely to the canvas, exposed
selection/transform/crop Properties contexts, restored the established 36px
per-side fit margin, kept the taskbar outside the rendered document, removed
responsive Properties scroll traps, made advanced Layers actions close after
execution, and fixed stale autosave status with latest-revision generation
tracking. Final screenshots and accepted Playwright baselines were regenerated.

## P0 acceptance gates

| P0 criterion                             | Result | Evidence                                                                                                                                                                 |
| ---------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Two real wide-desktop right dock columns | Pass   | 1672px measurement: 320px Layers + 316px secondary dock                                                                                                                  |
| Layers visible and dominant by default   | Pass   | Full-height primary column with fixed tabs, controls, list, and footer                                                                                                   |
| Properties visible simultaneously        | Pass   | 316 × 476px upper secondary region at 1672 × 941                                                                                                                         |
| History reachable simultaneously         | Pass   | 316 × 317px lower secondary region                                                                                                                                       |
| Eight or more visible fixture rows       | Pass   | Geometry assertion over nine deterministic rows                                                                                                                          |
| No prime placeholder panel               | Pass   | Color/Swatches hidden in the default profile                                                                                                                             |
| Real pixel/mask thumbnails               | Pass   | Tile-chunk sampled, resource-identity cached previews; minimum four image previews asserted                                                                              |
| Compact contextual Properties            | Pass   | Transform-first fixed summary plus one detailed body scroller; document/layer/mask/selection/path/transform/crop contexts                                                |
| Recognizable labeled tool rail           | Pass   | 104px rail at 1440px and wider; 46px compact mode at 1366px                                                                                                              |
| Correct top/status hierarchy             | Pass   | 38/44/36/30px measured and asserted                                                                                                                                      |
| Centered, fitted, unobscured document    | Pass   | 36px fit margin contract; actual rendered bounds asserted inside viewport                                                                                                |
| No page or panel scroll traps            | Pass   | Body overflow false; one Properties body scroller; fixed Layers controls/footer                                                                                          |
| Representative layered fixture           | Pass   | Pixels, group, masks, text, shape, adjustment, smart object, effect, History, selection                                                                                  |
| Three render-and-correct cycles          | Pass   | Before, iteration-1, iteration-2, and final evidence directories                                                                                                         |
| Reproducible visual command              | Pass   | `npm run test:ui-visual`; 3/3 Chromium visual/geometry tests                                                                                                             |
| Stable screenshot baselines              | Pass   | Seven accepted Windows Chromium baselines under `tests/e2e/visual-workspace.spec.ts-snapshots/`                                                                          |
| Existing canvas coordinates preserved    | Pass   | Unchanged pointer-coordinate assertions pass across Chromium, Firefox, and WebKit                                                                                        |
| Existing editor behavior preserved       | Pass   | 101 passed, 4 expected skips, 0 failed in the final 105-case tri-engine run                                                                                              |
| Engine boundary preserved                | Pass   | No renderer, compositor, raster, selection, filter, PSD, tile, or history algorithm changed; only UI preview/diagnostic adapters and autosave status arbitration changed |
| Roadmap returned to M1/M2                | Pass   | Active next task is removing selection/channel contiguous mirrors, then dependency-keyed composite tiles                                                                 |

## Deliberate divergences from the direction reference

- FreetoShop keeps its own name, restrained mint accent, SVG icons, and visual
  identity; Adobe trademarks and assets are absent.
- The deterministic fixture is an original abstract compositing document, not
  the copyrighted reference photograph.
- Unsupported tool families and AI controls are omitted rather than faked.
- Below 1600px, docks use the existing safe stacked layout instead of arbitrary
  drag docking or a finished drawer system.
- Multi-document concurrency, full ruler/guide chrome, and arbitrary panel
  docking remain outside this bounded checkpoint.

## Remaining P1 differences

- Refine the original icon set and alignment/distribution glyphs.
- Improve disabled states, dock-collapse transitions, and panel context menus.
- Add workspace-reset confirmation and a richer narrow-layout drawer.
- Complete high-contrast and reduced-motion review.
- Add mask-thumbnail target highlighting and layer search/filter.
- Improve tool-flyout keyboard cycling.
- Continue extracting detailed Properties sections from `App.tsx` as their
  underlying workflows are touched.

## Verification record

- `npm run test:ui-visual`: 3 passed.
- `npx playwright test --workers=4`: 101 passed, 4 expected skips, 0 failed.
- Unit/type/lint/format/build/audit/benchmark results are recorded in the
  roadmap checkpoint and final milestone report.

`App.tsx` was 5,600 lines at baseline and is 5,647 lines at this final
checkpoint. The net 47-line increase is limited to document-bound diagnostics,
context selection, latest-revision autosave status, and shell wiring; the new
Properties and dock presentation logic remains in extracted components.
