# UI Workspace Architecture

## Milestone intent

This bounded milestone establishes a durable professional desktop-editor shell
without changing document, renderer, compositor, raster, selection, filter,
transform, text, codec, persistence, or tile algorithms. The supplied visual
reference informs hierarchy, density, and canvas emphasis only. FreetoShop uses
original branding, tokens, SVG icons, measurements, and interaction details.

The target is a coherent shell at roughly 70–80% visual maturity. Pixel-perfect
polish, arbitrary docking, complete multi-document sessions, final icon artwork,
and unsupported editor features remain deferred.

## Current command and workflow map

| Existing surface                                                                       | New durable location                                                     |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| New, Open, Save document, Export PNG, Forget local                                     | File menu; Save remains discoverable from the document tab/status state  |
| Undo and Redo                                                                          | Edit menu and History panel                                              |
| Image Size, Canvas Size, Reveal All                                                    | Image menu                                                               |
| Layer creation, grouping, duplication, ordering, merge, flatten, smart-object commands | Layer menu plus Layers panel footer/properties                           |
| Select tools, selection modes, refinement, Deselect                                    | Select menu, tool rail, selection options, contextual task bar           |
| Adjustment-layer creation and destructive brightness                                   | Image → Adjustments or Layer → New Adjustment Layer                      |
| Filter dialog and smart-filter controls                                                | Filter menu plus Properties                                              |
| Fit, 100%, zoom, panel visibility                                                      | View/Window menus, options/status bars                                   |
| Brush/eraser settings and pixel/mask target                                            | Context-sensitive options bar and tool rail color/edit-target control    |
| Crop, transform, and path completion/cancellation                                      | Context-sensitive options and canvas task bar                            |
| Smart-object nested Save/Cancel/Edit                                                   | Special document-tab context and contextual task bar                     |
| Layers, Channels, History                                                              | Primary dock panels; all current controls remain operational             |
| Shape, text, effects, masks, transforms, adjustments, smart filters                    | Properties panel architecture while retaining current reachable controls |

Commands are represented once in the command registry with id, label, menu,
enabled/disabled state, checked state where applicable, shortcut metadata, and
execution callback. Menus and global keyboard routing consume the same registry.
Tool and panel registries similarly drive visible controls instead of duplicating
labels and availability rules.

## Layout regions and component boundaries

```text
EditorShell
├── ApplicationMenuBar
├── ToolOptionsBar
├── DocumentTabStrip
├── WorkspaceLayout
│   ├── ToolRail
│   ├── CanvasWorkspace
│   │   ├── existing canvas and overlays
│   │   └── ContextualTaskBar
│   └── PanelDock
│       ├── Color / Swatches group
│       ├── Properties / Adjustments group
│       ├── Layers / Channels / Paths group
│       └── History / Actions group
└── StatusBar
```

`App.tsx` remains the session and command adapter during this incremental
decomposition. It supplies canonical state and existing handlers to shell
components; presentation primitives do not own document algorithms. Existing
dialogs remain modal components and worker coordination remains in `workers/`.

The document tab strip honestly models one active document. Its state contract
includes name, dirty/save state, close, and an optional nested smart-object
context so a later session manager can provide multiple tabs without replacing
the component.

## Dock and workspace state

Workspace state version 2 selects the default **Professional Editing** profile.
The profile owns menu order, quick-access policy, familiar toolbar slots, and a
stacked set of registered panel groups. Each group has keyboard tabs, independent
collapse/visibility/vertical size, while the dock retains bounded horizontal
resizing. State migrates safely from the version 1 single-panel dock.
Contextual-bar dismissal is deliberately session-local. Invalid or future data falls back to
safe defaults. Full drag-anywhere docking and multiple freeform columns are
deferred; the registry and layout schema are the extension boundaries.

Layers and Properties remain visible simultaneously; Channels and History retain
their working content. Paths,
Properties, Adjustments, and Actions are registered honestly: current relevant
controls or explanatory empty states are shown, never fake active commands.

## Responsive behavior

Desktop and large-tablet layouts keep the tool rail, canvas, and one dock.
Below the desktop breakpoint, the dock becomes an overlay drawer and collapses
before shrinking the canvas. The menu bar scrolls or condenses without hiding
essential File access. The tool rail remains keyboard and pointer accessible.
Full mobile editing parity is not part of this milestone.

## Accessibility behavior

- Menus use semantic menu/menuitem roles, roving keyboard focus, Arrow/Home/End,
  Escape dismissal, outside-click dismissal, and focus return.
- Tool buttons retain their existing accessible names, pressed states, shortcut
  hints, tooltips, and practical targets.
- Document and panel tabs use tab semantics and roving keyboard focus.
- Dock splitters expose separator role, current/min/max values, arrow-key resize,
  and visible focus.
- Status and live regions announce save, worker, document, and smart-object
  context without presenting debug diagnostics in the polished UI.
- Reduced motion and high-contrast media queries preserve state cues without
  relying only on animation or color.

## Visual tokens

The token layer defines neutral pasteboard, application chrome, elevated
panels, controls, borders, text hierarchy, restrained mint accent,
focus, danger/success/warning roles, control heights, rail/menu/tab dimensions,
dock widths, icon sizes, spacing, small radii, shadows, z-index layers, and
motion duration. Components consume variables rather than scattering colors.

## Implemented in this milestone

- Central command/menu and tool/panel registries
- Versioned Professional Editing profile, familiar menu hierarchy and shortcuts
- Stable familiar tool families, flyouts/cycling, color swap/default controls
- Persistent stacked panel groups and command-backed canvas/layer/document context menus
- Compact conventional Layers rows and context-sensitive Properties surfaces
- Accessible global menu, context options, honest document tab, SVG icon rail
- Canvas-centered workspace with contextual actions and compact status
- Persistent collapsible/resizable registered dock
- Neutral tokenized professional shell and narrow-layout dock behavior
- Focused shell unit/integration tests and tri-engine runtime workflows

## Deliberately deferred

- Pixel-perfect final polish and final illustration/icon artwork
- Arbitrary drag-and-drop docking or multiple user-authored workspaces
- Real concurrent multi-document or multi-window sessions
- Complete future panels, rulers, guides, and floating tool families
- Final animation system and full mobile editing parity
- Generative AI controls or engine features not already implemented

After this milestone is verified and published, work returns immediately to the
canonical tiled-editing and incremental compositor critical path.
