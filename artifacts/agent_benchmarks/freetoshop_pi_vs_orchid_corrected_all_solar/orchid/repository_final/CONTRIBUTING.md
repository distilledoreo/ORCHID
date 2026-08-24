# Contributing

FreetoShop is currently a private, pre-release project. Keep changes small,
reviewable, and honest about supported behavior.

1. Use Node.js 22 or newer and install with the repository's npm lockfile.
2. Create a focused branch and avoid unrelated cleanup.
3. Preserve boundaries in [ARCHITECTURE.md](ARCHITECTURE.md).
4. Add tests for changed logic and user-visible regression risk.
5. Update public/developer documentation when contracts or workflows change.
6. Run the targeted checks first, then proportional build/browser verification.
7. Describe limitations and validation evidence in the pull request.

Never commit credentials, private user files, generated dependency trees, or
unreviewed large binaries. Do not claim compatibility, accessibility
conformance, performance, or security guarantees that the submitted tests and
evidence do not establish.

Dependency additions need a concrete use, license review, size/security
consideration, exact version, and corresponding audit update.
