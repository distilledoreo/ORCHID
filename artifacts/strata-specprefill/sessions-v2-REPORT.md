# Sessions v2: harness fixes + revised policy, fresh-seed validation (P100)

Quality-first revision before cache optimization. All three requested items are
done: the harness is fixed, the bypass+floor policy is implemented and tested
on fresh cases, and the revised policy is frozen.

## 1. Harness fixes

**Provenance probe (multifile t25).** v1's module-name echo and v2's import-line
completion both failed even on full context, so neither was discriminative. v3
asks "Which module defines {leaf}()? Reply with only the module name." with
exact-match scoring. The first v3 run exposed a second harness bug: the shared
`max_tokens=8` number budget truncated the answer (`ubusnyf_321_a` became
`ubusnyf_321`) on every arm. `session_live.py` now uses 16 tokens for
module-name probes; the targeted rerun passes 3/3 (full, keep30,
policy-keep20) with the exact module name on every arm.

**Filler probe removed.** v2's replacement count probes expected "13" but full
context answered "12" on both turns (three full-context "12"s vs the expected
"13") — a wrong expected value, non-discriminative by construction. Removed
entirely, not repaired. Sleeper is back to its single recall probe, as in v1.

**Turn-23 reconciliation + v1 erratum.** The v1 report's "20% miss =
multifile/t23" contradicts the frozen v1 data: adapt20 t23 answered correctly
with all evidence retained; the only adaptive miss was t25 (the malformed
probe). t23 passes on every arm in v1, v2, and v3 across three seeds — early
recall and multifile chain joins work at 10–20% retention. The v1 report is
left untouched; this correction lives here.

**Reusable builder.** `build_session_sets.py` freezes the template rule
(assistant think-block, ack-before-follow-up, `parse_special` tokenization).
Rebuilding seed 20261019 reproduces the frozen v1 `input_ids` byte-exactly for
early_late, task_shift, and multifile-t23 — the remaining diffs are the
intended generator changes.

## 2. Revised policy (frozen)

`retention_policy.py`: BYPASS_TOKENS=1024 (shorter inputs go full — selection
overhead dominates and short-prompt selection has failed with evidence
present), FLOOR_TOKENS=160 (selections retaining fewer fall back to full, not
topped up with unranked chunks). Constants were set from v1/v2 failure analysis
*before* fresh validation and are unchanged since — that is the freeze.
Boundary unit tests pass (`decide`/`enforce_floor` at 1023/1024, 159/160).

## 3. Fresh validation (seed 20261107, all answers distinct from v1/v2)

32 live requests + 3-row provenance rerun, 0 API errors, position/device
assertions on every selected request:

| Arm | Main | With rerun | Residual miss |
|---|---|---|---|
| full | 10/11 | 11/11 | truncation artifact only |
| keep30 | 9/11 | 10/11 | systematic t1 abstention |
| policy (bypass+adaptive) | 9/10 | 10/10 | none |

Bypass: 3/3 t1 turns correct via policy-full (6/6 across v2+v3). keep30 on the
same t1s abstains for the third time, again task_shift-specific ("Not
defined"/"Not found") with function name AND value retained — the bypass is
load-bearing, not cosmetic. Adaptive selected 10–50% per turn; every selected
turn passes, incl. sleeper recall at 10% (559 tokens) and the restored
provenance probe at 20%.

**Selection beat full, twice.** ses2-task_shift t17 fails on full AND keep30
("Noted.") but passes on policy-keep20 ("41430"), reproduced 2/2 on the
stability rerun — systematic for that seed's content, not noise. v1 and v3
confirm probes pass on full, so this is content-dependent full-context
ack-collapse (attention dilution), not a policy failure. Needs more seeds to
characterize; left as a finding, not a probe change.

**Timing (sequential pipeline).** Late turns (4.4–5.9k): full ~16–21s server
vs policy-keep10/20 ~4.6–6.7s (~6.4–10.6s pipeline). Early bypass turns run
full at ~4–5s with no selection overhead.

## Limitations (honest, blocking nothing)

- The 160-token floor never bound live (min policy retention 525 v3 / 524 v2);
  the fallback path is unit-tested only. A seed that triggers it is future work.
- One measurement per turn except ses2-t17 (2/2) and v3-t25 (rerun).
- v2 sets keep their broken probes; only non-probe v2 rows (bypass evidence,
  t23 passes) are cited. Caching comparisons rerun the frozen v1+v3 sets.

## Files

- Generator: `experiments/strata_specprefill/session_cases.py` (v3 probes)
- Builder: `experiments/strata_specprefill/build_session_sets.py` (new)
- Policy: `experiments/strata_specprefill/retention_policy.py` (frozen)
- Runners: `session_offline.py`, `session_live.py` (+`--only`, +module-name
  scoring/budget)
- Fresh sets: `data/strata_specprefill/session-sets-v3.json` (seed 20261107)
- Offline: `session-offline-v3.json` (11 turns, adaptive 10–50%)
- Live: `session-live-v3/` (32), `session-live-v3-rerun/` (t25 ×3, all pass),
  `session-live-v2-rerun/` (ses2-t17 ×3: full/keep30 fail, policy passes)

## Verification

Ruff clean on `experiments/strata_specprefill/`; full pytest green; GPU 0 MiB
after. env note: the student venv had lost `transformers` (and the project
venv `regex`); restored via uv (`transformers==5.19.0` into
state-projection `.venv-gpu`, `regex` into project `.venv`) — matches the
frozen environment (torch 2.7.1+cu126, transformers 5.19.0).
