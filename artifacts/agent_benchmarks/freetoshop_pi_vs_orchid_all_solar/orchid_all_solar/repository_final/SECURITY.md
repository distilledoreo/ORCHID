# Security Policy

## Reporting

Do not disclose a suspected vulnerability in a public issue. Report it
privately to the repository owner with reproduction steps, affected revision,
impact, and any proposed mitigation. Do not include private documents or other
sensitive test data.

No response-time or support-window SLA is currently promised. This project is
pre-release; only the latest development revision is considered for fixes.

## Security principles

FreetoShop is local-first: opening and editing a document should not upload its
contents. Network behavior, if added, must be explicit, narrowly scoped, and
documented. Parsed file data is never code. Avoid dynamic evaluation, unsafely
rendered HTML, remote scripts, and credentials in browser storage or commits.

Dependencies are pinned in `package.json`; the lockfile, when present, is the
install authority and must be reviewed with dependency changes. See
[THREAT_MODEL.md](THREAT_MODEL.md) and [DEPENDENCY_AUDIT.md](DEPENDENCY_AUDIT.md).

The Content Security Policy permits WebAssembly compilation with the narrow
`'wasm-unsafe-eval'` source expression for the local HarfBuzz text shaper. It
does not permit JavaScript `eval`, remote scripts, or remote font requests.

Native file import validates the signature, format version, declared payload
length, and outer SHA-256 checksum before parsing. Format v2 also verifies every
content-addressed binary tile against its declared SHA-256 ID before hydrating
document stores; bounded legacy v1 JSON remains readable.
