# P1–P3: chunked student prefill, equivalence, colocated pipeline (P100)

## P1: blockwise prefill unlocks 16k/32k (done)

`experiments/strata_specprefill/chunked_student.py`: build the KV cache in
8k blocks, then record Q_last @ K_full from the final block (the backend
passes past+current keys — verified `seen_key_lens=[4096, 8182]`, no replay
needed; a one-token replay fallback exists if a backend ever passes
block-local keys). Lookahead loop unchanged.

| Length | Full prefill | Chunked (8k blocks) | Peak |
|---|---:|---:|---:|
| 8k | 7.1 GiB / ~2.7–5.4s | 3.2 GiB / ~2.5s | 3150 MiB |
| 16k | **OOM** (7.98 GiB ask) | ~6.0s, cover30 true 6/6 | 7045 MiB |
| 32k | **OOM** (not attempted full) | ~14.7s, cover30 true 6/6 | 7045 MiB |

Peak is flat vs length (block-bound). 16k dev+held and 32k dev+held offline
sweeps saved: `data/strata_specprefill/longctx-offline-{16,32}k-{dev,held}-v1.json`.
Answer-span coverage holds even at 5% on all 12 (student-tokenizer proxy).

## P2: equivalence at 8k (done, with one honest caveat)

Same prompt, full vs chunked (2x4k): layers, query rows (6), sampled drafts
identical; max abs score diff 1.2e-4 (mean 1.3e-6); positions identical at
6/7 rates (Jaccard 1.0); the 75% rate differs by one boundary 32-token chunk
(Jaccard 0.9896, 6102/6166). An earlier replay-based variant matched drafts
but flipped chunks at every rate (diff 9e-3) — chunk means are near-tied, so
any ulp-level noise flips boundary chunks. Conclusion: chunked prefill
reproduces full-forward selection up to fp-tie noise; exact position equality
is not achievable by *any* implementation change and should not be required —
evidence coverage + live accuracy are the gates. Script:
`experiments/strata_specprefill/check_equivalence.py`.

## P3: colocated pipeline pilot (measured residency cost)

Both models resident (student 1.7 GiB idle + target 16k-ctx with 8192 reserve
for a shrunk expert cache), chunked trace then immediate request, client-start
to first-token timing. 2/2 correct:

| Case (held 8k, keep30) | student | server | measured pipeline | sequential estimate |
|---|---:|---:|---:|---:|
| large_read 8172 | 2.81s | 16.20s | **19.02s** | ~15.4s |
| adversarial 8291 | 2.52s | 16.81s | **19.34s** | ~14.5s |

The colocated server is ~6.5s *slower* than target-only (10–12s): the cache
shrink needed to fit both models costs more than colocation saves. On a single
P100, this configuration shows a substantial co-residency cost. **The sequential
figures are stage-sum estimates, not measured request latency**: they omit
model unloading/loading and cache restoration. They do not establish that
sequential model switching is a faster deployment. Raw:
`data/strata_specprefill/coloc-pipeline-8k-v1/`. Script:
`experiments/strata_specprefill/coloc_pipeline.py`.

## P4: next (not started)

Larger realistic coding evaluation for task-level quality: more cases per
(length x kind) cell, multi-file tasks, and live 16k/32k target runs using
chunked-selected positions (target 16k-ctx config exists; 32k needs a 32k-ctx
config + KV residency check). Reuse `longctx_live.py` with the 16k/32k offline
position files.
