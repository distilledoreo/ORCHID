# Contributing

ORCHID is an experimental project focused on correctness at the memory and
capsule boundary. Small, focused changes are easiest to review.

## Before opening a pull request

1. Create a branch from `master`.
2. Install the test extras:

   ```powershell
   python -m pip install -e ".[test]"
   ```

3. Run the full local suite:

   ```powershell
   pytest
   ```

4. Keep provider-dependent checks opt-in and document any required credentials.
5. Do not commit databases, raw prompts, provider responses, credentials, or
   machine-specific paths. Use sanitized fixtures instead.

## Pull requests

Explain the behavioral change, the invariant it protects, and the tests that
cover it. Changes to capsule lineage, leases, provenance, or promotion should
include regression tests for failure paths as well as the happy path.

Please keep formatting and refactoring separate from behavioral changes when
possible. The default branch CI must remain green without access to external
model providers.
