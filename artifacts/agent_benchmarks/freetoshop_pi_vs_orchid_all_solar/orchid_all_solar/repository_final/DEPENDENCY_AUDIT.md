# Dependency Audit

Audit date: 2026-07-13. Scope: direct root declarations plus direct web runtime
dependencies. The committed install was checked with `npm audit`; transitive
license classification and a machine-readable SBOM are still pending.

| Dependency                    | Pinned version | Purpose                         | Declared license               |
| ----------------------------- | -------------: | ------------------------------- | ------------------------------ |
| `@eslint/js`                  |         9.32.0 | ESLint recommended rules        | MIT                            |
| `@playwright/test`            |         1.55.1 | Browser end-to-end tests        | MIT                            |
| `@types/node`                 |         24.1.0 | Node.js TypeScript declarations | MIT                            |
| `eslint`                      |         9.32.0 | Static analysis                 | MIT                            |
| `eslint-plugin-react-hooks`   |          5.2.0 | React hooks lint rules          | MIT                            |
| `eslint-plugin-react-refresh` |         0.4.20 | Refresh-safety lint rules       | MIT                            |
| `globals`                     |         16.3.0 | ESLint global definitions       | MIT                            |
| `prettier`                    |          3.6.2 | Formatting                      | MIT                            |
| `typescript`                  |          5.8.3 | Compiler and type checking      | Apache-2.0                     |
| `typescript-eslint`           |         8.38.0 | TypeScript ESLint integration   | MIT                            |
| `vitest`                      |          3.2.7 | Unit/integration tests          | MIT                            |
| `ag-psd`                      |         31.0.2 | Import-only PSD parser          | MIT                            |
| `harfbuzzjs`                  |          1.4.0 | OpenType shaping in WebAssembly | MIT                            |
| `dejavu-fonts-ttf`            |         2.37.3 | Test-only deterministic fonts   | Custom permissive font license |

The web workspace directly declares React 19.1.1, React DOM 19.1.1, and Vite
7.3.6; each declares MIT. Internal `@freetoshop/*` workspace dependencies are
covered by this repository's MIT license. The new `@freetoshop/selection-engine`
and `@freetoshop/filter-engine` packages depend only on the internal
`@freetoshop/core-document` package and add no external runtime dependency.
`ag-psd` is pinned in `packages/file-codecs/package.json`; its locked runtime
dependencies are `base64-js` 1.5.1 (MIT) and `pako` 2.1.0 (`MIT AND Zlib`).
`harfbuzzjs` is pinned in `packages/text-engine/package.json`, has no npm runtime
dependencies, and lazily loads its 390,365-byte HarfBuzz WASM artifact.
`dejavu-fonts-ttf` is a root development dependency used only by shaping tests;
its custom Bitstream Vera / Arev-derived license was reviewed and requires its
notices to remain with redistributed font files.

Correction: TypeScript is Apache-2.0, so the direct root set is not exclusively
MIT. License fields should be reconfirmed from the actual locked artifacts
before release. CI runs `npm audit --audit-level=high` against the committed
lockfile. The current audited install reports zero known vulnerabilities.
