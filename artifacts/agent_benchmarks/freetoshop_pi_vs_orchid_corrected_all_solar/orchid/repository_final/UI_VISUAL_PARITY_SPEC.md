# FreetoShop Rendered Workspace Visual Parity Specification

## Authority and scope

Rendered FreetoShop screenshots are the completion authority for this bounded
milestone. The target reference informs professional hierarchy, proportions,
density, and familiar workflow placement; it does not authorize copying Adobe
code, branding, assets, icons, or exact trade dress. Engine algorithms remain
out of scope except for narrow cached preview adapters.

Baseline evidence was captured in Chromium at browser zoom 100%, device scale
factor 1, from the production-equivalent preview. The deterministic baseline
contains nine visible layers and is intentionally more demanding than a blank
document.

## Baseline measurements

| Viewport    | Menu | Options | Tabs | Rail | Canvas viewport | Right dock | Properties group | Layers group | Status |
| ----------- | ---: | ------: | ---: | ---: | --------------- | ---------: | ---------------: | -----------: | -----: |
| 2048 × 1046 |   32 |      34 |   28 |   46 | 1716 × 928      |        286 |              298 |          408 |     24 |
| 1672 × 941  |   32 |      34 |   28 |   46 | 1340 × 823      |        286 |              263 |          373 |     24 |
| 1440 × 900  |   32 |      34 |   28 |   46 | 1108 × 782      |        286 |              249 |          359 |     24 |
| 1366 × 768  |   32 |      34 |   28 |   46 | 1034 × 650      |        286 |              205 |          308 |     24 |

All heights and widths are CSS pixels. The page itself had no horizontal or
vertical scrollbar. The baseline has no independent primary Layers dock or
secondary Properties dock: both measurements refer to groups inside the same
286px column. History is collapsed and has no rendered content bounds.

Baseline screenshots are under
`docs/ui-checkpoints/visual-parity/before/` for the four required layered
viewports plus 1672 × 941 new-document, selection, and transform states.

## Target geometry

| Region                    | Wide-desktop target                                         |
| ------------------------- | ----------------------------------------------------------- |
| Application menu          | 36–40px high                                                |
| Tool options              | 40–46px high                                                |
| Document tabs             | 34–38px high                                                |
| Labeled tool rail         | 96–116px wide; approximately 104px preferred                |
| Primary Layers dock       | 280–400px; 310–330px preferred                              |
| Secondary Properties dock | 280–420px; 300–330px preferred                              |
| Combined right docks      | approximately 580–690px at 1672 × 941                       |
| Layers list               | at least 360px usable height and eight visible 36–44px rows |
| Canvas status strip       | 28–32px high and scoped beneath the canvas                  |
| Document margin           | 24–40px, centered horizontally and vertically               |

At widths below 1600px, the secondary dock may collapse into an overlay/drawer.
At widths below 1440px, the tool rail may become icon-only. Neither adaptation
may hide the canvas or remove access to supported panels.

## Baseline discrepancy audit

The ten largest rendered differences, in priority order, are:

1. One narrow right dock replaces the target's simultaneous Layers and
   Properties columns.
2. Color/Swatches consumes the highest-value region despite minimal capability.
3. Layers is pushed beneath Properties and receives only 373px total group
   height at 1672 × 941, including tabs, controls, list, and actions.
4. The 46px icon-only rail does not transfer the target's labeled tool-location
   mental model.
5. Top chrome is 4–10px shorter than the specified professional scale, and much
   interface text renders around 10–11px.
6. Layer controls and creation actions are mixed into a vertically scrolling
   form instead of fixed header/list/footer regions.
7. Layer rows use generic icons rather than cached pixel/mask previews.
8. Properties is a developer-oriented summary followed by a long detailed form,
   not a compact transform-first contextual surface.
9. The global 24px status bar competes with docks instead of forming a
   canvas-scoped document strip.
10. The document is fitted, but the huge canvas-to-controls ratio and single
    edge dock make it feel disconnected from everyday editing workflows.

Additional issues include collapsed History, horizontally scrollable panel tabs,
weak selected-row contrast, oversized blank property regions, tiny footer icons,
and no committed geometry/visual regression baseline.

## Iteration 1 rendered checkpoint

The first implementation cycle is committed separately from the accepted final
baseline under `docs/ui-checkpoints/visual-parity/iteration-1/`. At 1672 × 941,
the measured workspace now has 38/44/36px menu, options, and tab regions; a
104px labeled rail; a 320px primary Layers column; a 316px secondary column;
476px Properties and 317px History regions; and a 30px status bar. The page has
no body scrollbar and the fitted document remains centered and fully visible.

The ten largest differences identified from the first rendered comparison are:

1. The fixture is still structurally shallow and visually sparse instead of a
   plausible compositing document with group, masks, adjustment, effect, and
   smart-object evidence.
2. Layer thumbnails remain generic resource icons rather than cached pixel and
   mask previews.
3. The Layers footer is a tall grid of selects and text buttons instead of a
   fixed compact icon action bar.
4. Ordinary layer rows are approximately 48px high rather than the target
   36–44px density.
5. Properties is now simultaneous but still begins as a long form instead of a
   compact W/H/X/Y/rotation transform block.
6. The application-wide status bar still extends beneath both dock columns.
7. The canvas/task-bar relationship is serviceable but the task bar remains
   visually heavy and too close to the document edge.
8. Panel tabs and headings use too much accent simultaneously, weakening the
   primary selection hierarchy.
9. The 1440px and 1366px layouts use one oversized stacked dock rather than a
   deliberate secondary-dock drawer/collapse treatment.
10. The original icon language is inconsistent in the Layers footer and several
    panel signals, where text glyphs remain.

The first correction cycle therefore establishes and verifies the required
wide-desktop geometry, but does not satisfy the thumbnail, fixture, compact
Properties, scoped-status, or final visual-regression P0 gates.

## Iteration 2 rendered checkpoint

The second cycle replaces the empty-row fixture with a deterministic workflow
that paints five differently colored pixel resources, groups layers, adds a
raster mask and vector mask, creates text/shape/adjustment content, converts an
embedded smart object, applies a drop shadow, and leaves a populated History
panel. Pixel and raster-mask thumbnails are sampled directly from immutable
tile chunks, cached by resource identity, and covered by focused unit tests.
The Layers footer is fixed at 44px, while secondary supported actions remain
available in an anchored overflow menu. Properties now begins with a sticky,
compact transform-first context surface.

The five largest differences after the second rendered comparison are:

1. The status strip still needs to be scoped precisely beneath the canvas at
   each responsive width and asserted against the canvas geometry.
2. The detailed Properties controls beneath the compact transform surface are
   still dense and need clearer advanced-section disclosure for every context.
3. Selection and transform screenshot states need final-cycle evidence using
   the richer fixture, including task-bar non-obstruction assertions.
4. The responsive single-column dock below 1600px works without body overflow
   but still lacks the intended secondary-dock drawer refinement.
5. Accepted final screenshots, stable comparison baselines, the scorecard, the
   reproducible visual command, tri-engine evidence, and full project checks
   remain outstanding.

Iteration 2 therefore satisfies the representative-fixture, real-thumbnail,
compact-Layers-footer, and transform-first Properties foundations, but remains
an intermediate checkpoint rather than P0 completion.

## Proposed component changes

- Evolve workspace state to a versioned two-column dock layout with independent
  widths, group splits, visibility, migration, and reset behavior.
- Replace `PanelDock`'s single group stack with `DockColumn` primitives: Layers
  owns the primary column; Properties/Adjustments and History/Actions share the
  secondary column. Color/Swatches is hidden by default.
- Make Layers a fixed tab/control header, flexing list, and fixed action footer.
  Add cached thumbnail adapters keyed by resource identity/revision.
- Extract compact context-specific Properties components from `App.tsx`, with a
  transform-first common block and collapsible advanced sections.
- Render labels beside tool icons at widths of 1440px and above, retaining the
  existing stable tool-family registry and keyboard behavior.
- Raise chrome and control dimensions through central tokens; distinguish frame,
  tabs, pasteboard, primary/secondary panels, selections, focus, and disabled
  states without accent saturation.
- Scope the status strip to the canvas column and preserve fit centering with
  measured pasteboard margins.
- Add a deterministic layered visual fixture, geometry assertions, accepted
  screenshots, and `npm run test:ui-visual`.

## Priority classification

### P0

- Two independently resizable wide-desktop dock columns.
- Dominant Layers surface with eight visible fixture rows and fixed controls.
- Compact contextual Properties with required context states.
- Labeled 96–116px desktop tool rail and compact lower-width mode.
- Specified chrome typography, heights, pointer targets, and grouping.
- Centered fitted document, clear edge, canvas-scoped status, no obstruction.
- No page scroll, horizontal panel scroll, clipped controls, or nested scroll traps.
- Real cached pixel and mask thumbnails where supported.
- Deterministic representative fixture and at least one history entry.
- Three screenshot-and-correct cycles at every required state/viewport.
- Committed final screenshots, scorecard, geometry tests, and visual command.
- Existing editor behavior green in Chromium, Firefox, and WebKit.

### P1 after every P0 passes

- More refined original icons and alignment/distribution glyphs.
- Improved disabled states and dock collapse transitions.
- Expanded panel context menus and reset confirmation.
- Better narrow-layout drawer and tool-flyout keyboard refinement.
- High-contrast/reduced-motion review, layer search, and mask-thumbnail targeting.

### Deferred

- Exact Photoshop reproduction or Adobe assets.
- Arbitrary drag-anywhere docking, multi-window documents, full mobile parity,
  unsupported tools, generative AI, and final localization/preferences systems.

## Completion rule

No P0 item may be marked complete from architecture or prose alone. The final
`UI_VISUAL_PARITY_SCORECARD.md` must cite rendered evidence and geometry for all
required viewports, mark every P0 row Pass, document three correction cycles,
and record every deliberate divergence before the roadmap returns to M1/M2.

## Final rendered checkpoint

The third correction cycle is accepted. `UI_VISUAL_PARITY_SCORECARD.md` records
the final measurements, seven committed screenshots, every P0 gate as Pass,
deliberate divergences, and remaining P1 work. The reproducible visual command
passes 3/3 Chromium cases, and the final configured tri-engine run passes 101
cases with four expected engine-independent screenshot/geometry skips and zero
failures.

The accepted 1672 × 941 geometry is 38/44/36/30px chrome, a 104px labeled rail,
a 932 × 793 canvas viewport, a 320px full-height Layers column, and a 316px
secondary column split into 476px Properties and 317px History regions. The
page does not scroll. Actual document bounds remain centered and entirely
inside the canvas; the contextual taskbar does not overlap them.

This bounded visual milestone is Functionally complete, not
Compatibility-hardened or Production-ready. P1 polish is deferred and the
active roadmap has returned to M1 canonical tiled editing and M2 incremental
compositing.
