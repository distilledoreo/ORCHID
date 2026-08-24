# Release

FreetoShop is pre-release. Phase A demonstrates an end-to-end local workflow;
it is not a production-readiness or compatibility declaration.

## Release checklist

1. Use a clean checkout with the supported Node.js version and committed lockfile.
2. Run type checking, lint, formatting, unit tests, build, and browser E2E tests.
3. Smoke-test install/start, PNG and JPEG import, layer editing, undo/redo,
   persistence restore, and PNG/JPEG export in supported browsers.
4. Verify offline/PWA behavior and that no document content is uploaded.
5. Review size limits, malformed-input behavior, accessibility, dependency
   vulnerabilities/licenses, and generated third-party notices.
6. Record known limitations, browser matrix, build provenance, commit, and
   checksums for distributed artifacts.
7. Tag only the exact verified revision and retain validation output.

Do not advertise PSD/PSB, advanced color fidelity, plugin/script compatibility,
or feature parity. Rollback means withdrawing the affected artifact/site build
and restoring the last independently verified revision.
