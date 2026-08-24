# Third-Party Notices

FreetoShop is licensed under MIT. Its root `package.json` currently declares
the following development tools under MIT licenses: `@eslint/js`,
`@playwright/test`, `@types/node`, `eslint`, `eslint-plugin-react-hooks`,
`eslint-plugin-react-refresh`, `globals`, `prettier`, `typescript-eslint`, and
`vitest`. TypeScript is licensed under Apache-2.0. These license classifications
must be reconfirmed against the installed, locked artifacts before distribution.

The web application directly uses React, React DOM, and Vite, which declare MIT
licenses. Internal `@freetoshop/*` packages are original project code.

The file-codecs package uses `ag-psd` 31.0.2 (MIT) for PSD parsing. Its locked
runtime dependencies are `base64-js` 1.5.1 (MIT) and `pako` 2.1.0
(`MIT AND Zlib`). Their copyright and license texts must accompany a
distribution as required.

The text-engine package uses `harfbuzzjs` 1.4.0 (MIT), including its bundled
HarfBuzz WebAssembly build, for local OpenType shaping. Copyright (c) 2019-2026
the harfbuzzjs project authors; its MIT notice must accompany distributions.
The test-only `dejavu-fonts-ttf` 2.37.3 package supplies deterministic TrueType
fixtures under the Bitstream Vera / Arev-derived DejaVu font license. It is not
bundled into the production web output; its copyright and permission notices
must be retained anywhere the fixture fonts are redistributed.

This file is an inventory, not a substitute for the complete license text
shipped by each installed package and its transitive dependencies. Before a
distribution, generate notices from the committed lockfile/install tree and
retain required copyright and license notices. Browser and operating-system
components are not bundled by this repository.
