# Threat Model

## Assets and trust boundaries

Assets include user images, document metadata, exported files, and local
preferences. Trust boundaries exist at file import, clipboard/drag-and-drop,
browser storage, worker messages, dependencies, future plugins/scripts, and any
future network integration.

## Principal threats

| Threat                                            | Phase A mitigation                                                           |
| ------------------------------------------------- | ---------------------------------------------------------------------------- |
| Malformed decompression or oversized image        | Signature checks, dimension/allocation limits, controlled errors             |
| Script or markup injection through names/metadata | Treat as text; no HTML interpretation or dynamic evaluation                  |
| Data exfiltration                                 | No implicit upload; explicit review for every network path                   |
| Browser storage disclosure                        | Store only required local data; provide clear/delete behavior                |
| Denial of service from expensive rendering        | Input limits, cancellable work where practical, bounded loops                |
| Dependency compromise                             | Exact versions, lockfile review, minimal dependencies, audit before release  |
| Confused export/download                          | Explicit user action, clear filename/type, no silent overwrite               |
| Corrupt or spoofed native document                | Binary magic, bounded length, SHA-256 integrity, schema/invariant validation |
| Malformed or oversized local font                 | Explicit selection, 64 MiB cap, isolated HarfBuzz API, controlled errors     |
| WebAssembly broadening script execution           | CSP permits `wasm-unsafe-eval` only; JavaScript evaluation remains blocked   |

## Out of scope today

FreetoShop does not claim to sandbox hostile plugins, scripts, or PSD embedded
objects because those execution surfaces are not supported in Phase A. Adding
one requires a threat-model update before release. Browser and operating-system
compromise are outside the application boundary.
