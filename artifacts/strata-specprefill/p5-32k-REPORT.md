# P5: 32k validation — full SpecPrefill loop on P100 (first time)

Chunked student traces (P1, 4x8k blocks, peak 7045 MiB) supplied frozen
positions; the target ran in a 40k context (32k ctx rejects prompt+margin
with HTTP 400 — same margin cause as 8k-in-8kctx, fixed the same way).
40k-ctx config loads clean: `longctx-40kctx-alone.json`.

## Results (3 cases x 3 arms x 2 splits, one measurement each)

| Split | Arm | Correct | Mean server TTFT | Mean pipeline* |
|---|---|---:|---:|---:|
| dev | full (32.8k) | 3/3 | 104.0s | 104.0s |
| dev | keep30 (~9.8k) | 3/3 | 35.2s | 50.0s |
| dev | adaptive (20–30%) | 3/3 | 29.8s | 44.4s |
| held | full (32.8k) | 3/3 | 103.6s | 103.6s |
| held | keep30 (~9.8k) | 3/3 | 34.6s | 49.3s |
| held | adaptive (20–30%) | 3/3 | 29.0s | 43.7s |

Pooled 32k: **18/18** (full 6/6, keep30 6/6, adaptive 6/6). Server speedup
~3x (104s -> ~35s); pipeline ~2.1x (~104s -> ~49s) including the ~14.7s
chunked student trace. Adaptive picks (frozen mass-0.80): 0.2 on one
large_read per split, 0.3 elsewhere — all correct, retention ~27%.

Cases: large_read / multi_hop / adversarial at 32739–32867 tokens, evidence
mid-file, contamination-safe (seed 20261017). Live scoring with the real
target tokenizer; device-table + original-length assertions every selected
request; 0 API errors.

## Files

- Offline (chunked): `data/strata_specprefill/longctx-offline-32k-{dev,held}-v1.json`
- Live: `data/strata_specprefill/longctx-live-32k-{dev,held}-v1/` (requests.jsonl + COMPLETE)
- Config: `data/strata_specprefill/longctx-40kctx-alone.json`

## Limitations

One measurement per arm/case; sequential pipeline figures (student offline,
not colocated — P3 showed colocated is slower on 1xP100 anyway); warmed
student means; 32k-ctx expert cache is small (watch server TTFT variance on
repeats). Combined with P4: 8k 41/42, 16k 60/60, 32k 18/18 live correct.

*Pipeline = offline chunked student_s + live server TTFT (sequential estimate).
