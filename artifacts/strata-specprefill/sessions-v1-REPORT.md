# Sessions v1: multi-turn coding-agent harness + per-turn quality (P100)

Reusable harness: 5 sessions x 10-20 turns (early_late, task_shift, multifile,
sleeper, supersede), 11 probed turns, each replayed cumulatively as an
independent cache-off request (the exact baseline future caching work reruns).
Full vs fixed-30% vs frozen adaptive (mass-0.80) at every turn: 32 requests.

## Results: 28/32

| Arm | Correct | Notes |
|---|---|---|
| full | 10/11 | misses only the malformed provenance probe (below) |
| keep30 | 9/11 | + task_shift/t1 abstention with evidence present |
| adaptive | 9/10 | picks 10–50% per turn; 20% miss = multifile/t23 (below) |

Per-pattern: early_late 3/3 all arms (14-turn recall works); sleeper 3/3
(config recalled after 12 filler turns); supersede 9/9 incl. v2-only finals;
task_shift post-shift 6/6; multifile chain 3/3 at turn 23.

## Three harness flaws found live (fix in v2, do not hide)

1. **Multifile provenance probe is malformed** (turn 25): the "module name"
   answer renders through the template as `uhxntpo_376_a` but the model
   echoes the read+ack suffix and answers "Noted." — full context also fails,
   so this is a broken probe, not a selection failure. Exclude turn 25 from
   quality claims; keep it as evidence-retention data only (module name
   retained 3/3 arms).
2. **task_shift turn-1 keep30 abstains** ("Not found") with the function name
   AND value retained — sufficiency failure at 33% on a 477-token prompt.
   Full and adapt50 correct. Short-prompt selection is not safe by default.
3. **Filler probe (sleeper turn 7) has no answer** and was correctly excluded
   from live runs (scores True by vacuity) — remove it from the set in v2.

## Evidence retention (target-tokenizer decode of retained IDs)

Sleeper/supersede/early-recall evidence present in 100% of retained prompts
across arms. The keep30 task_shift miss retains both evidence strings yet
fails — retention is necessary, not sufficient (same lesson as P4's_span
finding). Turn-25 module name retained everywhere including the failed
selections.

## Timing (sequential pipeline = offline student + live server)

Late turns (4.4–6.1k tokens): full ~15–21s server; keep30 ~6–8s; adaptive-10%
~4.7–4.9s server (~6.6–6.8s pipeline). Early turns (<600 tokens): selection
overhead dominates — keep30 2.2–2.6s vs full 4.2–4.8s server but pipeline
parity once student time added. Caching comparison must rerun these exact
frozen turns.

## Files

- Generator: `experiments/strata_specprefill/session_cases.py` (seed 20261019)
- Frozen turns: `data/strata_specprefill/session-sets-v1.json` (ignored)
- Offline: `data/strata_specprefill/session-offline-v1.json` (scores, positions)
- Live: `data/strata_specprefill/session-live-v1/` (requests.jsonl + COMPLETE)
- Runners: `session_offline.py`, `session_live.py` (both take --sets/--offline/--output)

## Verification

32 measured + 1 warmup, 0 API errors, position assertions every selected
request, GPU 0 MiB after. Ruff clean on new files. Prior reports unchanged.
v2 must fix the provenance probe, drop the vacuous filler, and add a
short-prompt floor before any caching comparison.
