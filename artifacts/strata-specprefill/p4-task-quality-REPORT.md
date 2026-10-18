# P4: task-level quality at 8k/16k (P100, frozen mass-0.80 router)

28 new contamination-safe cases (seed 20261018, v1 untouched): 16k replicas
(large_read/multi_hop/adversarial x2) + new two-file kind (hop/large x 8k/16k
x2) with cross-file imports. Evidence mid-file. Live: full + fixed-30% +
frozen adaptive per case, greedy, target-tokenizer scored, sequential
pipeline timing (student offline + live server).

## Headline

| Split | Arm | Correct | Mean server TTFT | Mean pipeline |
|---|---|---:|---:|---:|
| 16k dev (10) | full | 10/10 | 53.3s | 53.3s |
| 16k dev (10) | keep30 | 10/10 | 17.6s | 23.5s |
| 16k dev (10) | adaptive | 10/10 | 16.6s | 22.5s |
| 16k held (10) | full | 10/10 | 53.7s | 53.7s |
| 16k held (10) | keep30 | 10/10 | 17.6s | 23.6s |
| 16k held (10) | adaptive | 10/10 | 15.2s | 21.2s |
| 8k dev v2 (4 multi) | full/keep30 | 4/4, 4/4 | 26.9s / 10.0s | — / 12.6s |
| 8k dev v2 (4 multi) | adaptive | 3/4 | — | — |
| 8k held v2 (4 multi) | all arms | 12/12 | — | — |

16k pooled: **60/60** (full 20/20, keep30 20/20, adaptive 20/20) at ~3x
server speedup (53.5s -> ~17s) and ~2.3x pipeline (~53.5s -> ~22.8s).
Adaptive retention ~27%; never worse than fixed-30% anywhere at 16k.

## The one miss (dev multifile_hop-8k-r2 @ 20%)

Two-hop evidence (service + leaf across files): the proxy span check passed
at 20% yet the model returned a distractor (86986 vs 58128); 30% correct.
Lesson: span survival is necessary, not sufficient — multi-hop needs jointly
sufficient evidence, and the 80%-mass threshold under-kept here (gini 0.755,
top10 0.729, the most diffuse 8k multifile case). The same router was 10/10
on the other multifile cases including all four at 16k.

## Small-sample flips persist

adapt20: 0/1 dev vs 1/1 held on multifile_hop-8k. Pooled 8k-v2 adaptive 7/8
vs fixed-30% 8/8 on dev, reversed on held — same noise pattern as the
short-context study. Do not retune on these cells.

## What was NOT run

32k live target runs (positions frozen and ready in
`longctx-offline-32k-{dev,held}-v1.json`; needs a 32k-ctx residency/timing
run), and larger N per cell. 32k-ctx target config exists and loads
(`longctx-32kctx-alone.json`, verified this turn).

## Files

- Sets: `data/strata_specprefill/longctx-sets-v2.json` (ignored, private)
- Offline (chunked): `longctx-offline-{8k,16k}-{dev,held}-v2.json`
- Live: `longctx-live-{8k,16k}-{dev,held}-v2/` (requests.jsonl + COMPLETE)
- Code: `longctx_cases_v2.py`; `longctx_live.py` gained `--sets/--offline/--output`
- Prior: v1 8k reference (`reference-8k-v1/`), short-context summaries

Verification: 84 measured P4 requests + warmups, 0 API errors; 100%-style
position assertions on every selected request; Ruff clean; GPU 0 MiB after.
Student selection ran chunked (P1) for all v2 offline work — full-forward
equivalence bounded in P2.
