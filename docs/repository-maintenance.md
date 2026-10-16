# Repository maintenance

Source, tests, sanitized fixtures, and historical experiment reports remain in
Git. Generated repository snapshots (`repository_final/`), downloaded experiment
models (`artifacts/**/model/`), SQLite databases, and coverage output are local
assets and must not be committed.

The October 2026 cleanup archived these generated files before removing them
from the current tree. Their historical Git versions remain available. Model
download helpers in the experiment scripts can restore downloaded assets.
Recorded-database checks remain optional when their local recordings are absent.

Uncommitted provider and benchmark research from the original checkout was
preserved separately before returning the checkout to the integrated branch.
It is not part of the reviewed memory upgrade.

The separate `upgrade/observer-live-readiness` branch is retained for reference,
with its experimental code excluded from integration because the recorded live
trial still stalled and underperformed rolling-summary memory. Its report and
grading fixtures are preserved in [live observer findings](live-observer-findings.md).

Sealed fixtures use the LF bytes specified in `.gitattributes`. When deliberately
updating a sealed fixture, regenerate its manifest against the committed bytes
and verify from a fresh checkout. Do not change the seal merely to bypass a
content or contract mismatch.

The memory upgrade's deterministic evaluations verify software behavior, not
live-model quality, prefill latency, or GPU/cache performance. Those require
separate experiments.
