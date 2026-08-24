# Professional Editor Transition Audit

This document evaluates learnability for users of established desktop image editors. It is an interoperability aid, not an Adobe endorsement, and does not imply visual, asset, or implementation copying.

The default **Professional Editing** workspace uses familiar menu locations, tool-family order, panel grouping, terminology, and safe shortcuts. Only implemented behavior is active.

| Workflow                      | Expected location       | FreetoShop location                            | Familiar shortcut                     | Status and divergence                                                        |
| ----------------------------- | ----------------------- | ---------------------------------------------- | ------------------------------------- | ---------------------------------------------------------------------------- |
| Open / save                   | File                    | File                                           | Ctrl/Cmd+O, Ctrl/Cmd+S                | Implemented; native save uses `.freetoshop`.                                 |
| Image / Canvas Size           | Image                   | Image                                          | Ctrl/Cmd+Alt+I / C                    | Implemented.                                                                 |
| Crop                          | left rail               | Crop family and options                        | C                                     | Implemented.                                                                 |
| Move                          | left rail               | first tool family                              | V                                     | Implemented.                                                                 |
| Paint / erase                 | left rail               | Brush / Eraser families                        | B / E; brackets resize                | Implemented.                                                                 |
| Make / modify selection       | rail and Select         | marquee, lasso, wand; Select menu/options      | M, L, W; Ctrl/Cmd+D; Ctrl/Cmd+Shift+I | Implemented subset; reselect/refine-edge parity is incomplete.               |
| Add / duplicate / group layer | Layer and Layers footer | Layer menu and Layers footer                   | Ctrl/Cmd+Shift+N; Ctrl/Cmd+J          | Implemented.                                                                 |
| Add mask                      | Layer / Layers footer   | Layer Mask menu and Layers actions             | —                                     | Implemented raster and vector mask subset.                                   |
| Add adjustment layer          | Layer and Adjustments   | Layer → New Adjustment Layer and Layers action | —                                     | Four adjustment types; broader parity deferred.                              |
| Blend / opacity / fill        | top of Layers           | top of Layers and Properties                   | —                                     | Implemented.                                                                 |
| Free Transform                | Edit / canvas           | Edit → Free Transform and Move family          | Ctrl/Cmd+T                            | Implemented affine subset.                                                   |
| Text                          | Type / rail             | Type menu and text controls                    | T                                     | Editable text implemented; typography parity incomplete.                     |
| Shapes / paths                | rail and Properties     | layer actions, Pen family, canvas, Properties  | P for Pen                             | Editable subset; path-selection families are not active.                     |
| Smart Objects                 | Layer / context         | Layer controls and contextual task bar         | —                                     | Embedded smart objects supported; linked objects deferred.                   |
| Filters                       | Filter                  | Filter → Blur → Gaussian Blur; Last Filter     | Ctrl/Cmd+F                            | Direct Gaussian Blur workflow; current destructive/smart-filter subset only. |
| Undo / redo                   | Edit                    | Edit and History                               | Ctrl/Cmd+Z / Ctrl/Cmd+Shift+Z         | Implemented.                                                                 |
| Export                        | File                    | File → Export PNG                              | —                                     | PNG and supported codecs; not format-parity complete.                        |

## Current divergences

- Browser-reserved shortcuts are not overridden when doing so would damage accessibility or platform safety.
- Shortcuts are ignored while focus is in text, number, select, and textarea controls.
- Multi-document concurrency, arbitrary docking, complete preferences, and unsupported tool families are omitted instead of simulated.
- Color and Swatches remain modest because ICC color management and persistent swatch libraries are not implemented.
- Original FreetoShop SVGs, visual tokens, names, and measurements are used throughout.

Future compatibility work must continue through the central command, tool-family, panel, and workspace-profile registries rather than local key listeners or hard-coded dock boxes.

The direct adjustments/filter checkpoint adds familiar Image → Adjustments, Filter → Blur, and Layer → New Adjustment Layer hierarchy without claiming full Photoshop parity. Unsupported filter galleries remain omitted intentionally; see `docs/adjustments-filters-menu-checkpoint.md`.
