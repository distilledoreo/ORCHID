# Strata SpecPrefill 8k validation — full report (P100, sequential)

Validates full prefill vs fixed 30% vs adaptive (mass-0.80, frozen) at ~8k
tokens on real coding-agent workloads. 18/18 correct. Selected prefill is
~2.3x faster end-to-end than full prefill on the same Swift model and P100.
16k/32k student selection is blocked by P100 VRAM (OOM evidence below).

## Result tables

Dev (3 cases x 3 arms, one measurement each):

| Case | Full (srv) | Keep30 (srv / pipe) | Adaptive (srv / pipe) |
|---|---:|---:|---:|
| large_read 8182 | 25.18s ok | 9.98s / 12.82s ok (2454) | 10.07s / 12.91s ok, rate 0.3 (2454) |
| multi_hop 8208 | 28.51s ok | 11.89s / 14.56s ok (2480) | 7.79s / 10.46s ok, rate 0.2 (1648) |
| adversarial 8285 | 30.70s ok | 11.93s / 14.63s ok (2493) | 10.11s / 12.81s ok, rate 0.3 (2493) |

Held (3 cases x 3 arms):

| Case | Full (srv) | Keep30 (srv / pipe) | Adaptive (srv / pipe) |
|---|---:|---:|---:|
| large_read 8172 | 25.18s ok | 9.95s / 15.40s ok (2444) | 10.09s / 15.54s ok, rate 0.3 (2444) |
| multi_hop 8211 | 28.76s ok | 11.87s / 14.55s ok (2483) | 10.07s / 12.75s ok, rate 0.3 (2483) |
| adversarial 8291 | 30.73s ok | 11.80s / 14.50s ok (2467) | 7.68s / 10.37s ok, rate 0.2 (1635) |

srv = live target server TTFT. pipe = offline student_s + srv (sequential
estimate: student ran first, unloaded, then the target-only server ran the
frozen positions — not a colocated measurement). All outputs exact-match the
planted 5-digit codes (target-tokenizer decoded, control markers stripped).

Means: full ~28.2s srv; keep30 ~11.2s srv / ~14.4s pipe; adaptive ~9.5s srv /
~12.7s pipe at ~27% average retention.

## Workloads (contamination-safe, frozen)

`data/strata_specprefill/longctx-sets-v1.json` (ignored, private): 18 cases,
dev+held x large_read/multi_hop/adversarial x 8k/16k/32k budgets. All module,
function, record names and answer codes freshly randomized
(`build_long_sets`, seed 20261017); evidence buried mid-file (40-60%), never
at edges. Adversarial adds near-duplicate decoy names and a stale-v1 vs
current-v2 pair where only v2 counts. Rendered through the real Swift chat
template: dev 8182/8208/8285, held 8172/8211/8291 tokens. 16k cases
16358-16496, 32k cases 32739-32867 tokens (frozen for future hardware).

## Method

1. Offline (student venv, P100): one Qwen3.5-0.8B forward per prompt, capture
   post-RoPE Q/K from the 6 full-attention layers, reference
   FP16-matmul/FP32-softmax/pool-13/max-over-layers-heads/mean-over-queries,
   32-token chunk means, ceil retention. Saved per-rate positions for
   5/10/20/30/50/75/100% + gini/entropy/top-mass/margin signals + proxy
   answer-span coverage (30%: covered on all six 8k cases).
2. Live (target-only server, 16k context for 8k prompts — an 8192 context
   rejects prompt+margin with HTTP 400, fixed by cause): full + keep30 +
   frozen adaptive rate per case (mass-0.80 picks from step 1, no live
   student, no extra inference). Greedy, max 8 tokens, same diagnostic
   controls as the smoke (cache off, tail-skip off, CPU share off, PCIe 1).
3. Scoring with the real target tokenizer; 100%-style position assertions
   (device-table verified, next_position == original length) on every
   selected request.

## 16k/32k status: blocked with evidence

The 16k student forward OOMs on the 16GB P100: `torch.OutOfMemoryError:
tried to allocate 7.98 GiB` with 11.88 GiB already in use (SDPA forward
itself, not the capture). 8k peaks at ~7.1-7.3 GiB / ~2.7s warmed (one held
trace 5.45s cold). Target-only long-context is feasible, but without a
selector there is no SpecPrefill comparison to validate — so 16k/32k quality
is unproven and needs either bigger VRAM or blockwise student scoring.

## Files (this directory + pointers)

- `quality.json`: recomputed rollup (this report's tables).
- `environment.json`: model, hardware, engine, runtime, controls, commits.
- `longctx-offline-8k-{dev,held}-v1.json`: scores hashes, signals, per-rate
  positions + selected token IDs, student_s, peak MiB.
- `live-8k-{dev,held}-requests.jsonl` + `COMPLETE.json`: per-request server
  TTFT, pipeline estimate, retained counts, decoded outputs, correctness.
- `config-*`: the exact 4k + 8k/16k-ctx server configs run.
- `prior-*.json`: short-context smoke / keep-rates / adaptive summaries.
- `MANIFEST.json`: sha256 of every file above.
- Private raw prompts/logs: `data/strata_specprefill/longctx-*` (ignored).

## Verification

- 18 measured + 2 excluded warmups, zero model API errors, GPU back to 0 MiB.
- Ruff clean, `tests/test_strata_specprefill.py` 21 passed / 5 skipped.
- Prior reports unchanged: `../REPORT.md` (50% smoke),
  `../keep-rates/REPORT.md` (10%/30%), `../adaptive-retention/REPORT.md`.

## Limitations

One measurement per arm/case; sequential (not colocated) pipeline figures;
warmed student means with real cold-start cost excluded; reference fallbacks
in the student; 8k only for live quality. Treat as a validated 8k reference,
not a general long-context claim.
