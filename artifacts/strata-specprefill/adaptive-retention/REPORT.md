# Adaptive retention for Strata SpecPrefill — exploratory results

Frozen rule (chosen on dev signals only, before any held-out live data):
**smallest keep rate whose top chunk-mass captures 80%** (`adaptive_rate_by_mass`,
target 0.80). One student forward per prompt; the rate only changes how many
top 32-token chunks are kept. No extra model inference.

Contamination-safe sets: 8 dev + 8 held-out, balanced lookup/coding x
~500/~1500 tokens x evidence early/late. All codes, record IDs, module and
function names freshly randomized (no KELP47/FERN83/WILLOW* reuse), rendered
through the real Swift chat template. Full prefill answers 16/16, so the tasks
are solvable and novel.

## Headline (held-out, frozen rule)

| Held (8) | Correct | Avg retention | Mean pipeline TTFT |
|---|---:|---:|---:|
| Full, co-located | 8/8 | 100% | 5.95 s |
| Fixed 30% | 7/8 | 30.8% | 3.84 s |
| **Adaptive mass-0.80** | **8/8** | **26.5%** | **3.68 s** |

Adaptive picks on held: 0.5, 0.3, 0.1, 0.1, 0.5, 0.5, 0.3, 0.3
(token-weighted 26.5%, mean rate 0.325). It fixes the one case fixed-30% misses
(`held-coding-512-at80`: keep30 wrong, keep50 right) and keeps the two
long-lookup cases at 10% (both right).

## Dev (where the threshold was set)

| Dev (8) | Correct | Avg retention | Mean TTFT |
|---|---:|---:|---:|
| Full, co-located | 8/8 | 100% | 5.98 s |
| Fixed 30% | 8/8 | ~30% | 3.83 s |
| Adaptive mass-0.80 | 7/8 | 26.4% | 3.66 s |

Miss: `dev-coding-1536-at20` picked 0.2 (wrong; needs ≥0.3). Thresholds 0.70
(6/8) and 0.75 (7/8) also miss 8/8 on dev. Pooled over both splits adaptive and
fixed-30 tie at 15/16, adaptive keeping fewer tokens (~26.5% vs ~30.5%) and
~0.16 s lower mean TTFT.

## Retention curves (live, one measurement per arm/case)

Dev: 75→8/8, 50→8/8, 30→8/8, 20→6/8, 10→3/8, 5→4/8.
Held: 75→8/8, 50→8/8, 30→7/8, 20→7/8, 10→5/8, 5→2/8.
30% is the accuracy knee on dev; on held 20% ties 30% at 7/8. 5% is erratic
(4/8 dev vs 2/8 held) — see failures.

## Signals (no extra inference)

- Concentration works as a ranking signal: gini 0.49–0.79, top-30 mass
  0.73–0.90; long prompts more concentrated (max/mean 12–14 vs 3–4.5 short);
  coding slightly less concentrated than lookup.
- Keep/drop **margins are tiny (0.0000–0.01) and noisy** — not a usable elbow
  without more work.
- Mass-0.80 averages 26–27% retention, but the mapping is fragile: one
  long-coding case needs more than its mass suggests. Do not retune the
  threshold on these 16 cases; that would be overfitting.

## Failures and caveats (read before using)

- `select_positions` with whole-chunk `ceil` lets **5% collapse to 3 tokens**
  on 515-token prompts when the single kept chunk is the final partial.
- **Non-monotonic quality**: `dev-coding-1536-at20` keep5 right but keep20
  wrong — extra retained chunks can hurt. Token count ≠ quality.
- `adaptive_signals.top_mass` lacked 0.75/1.0 keys (KeyError at target 0.85);
  fixed to include them. Stored offline JSON predates the fix; thresholds ≤0.80
  unaffected.
- `run.py`'s `[A-Za-z]+\d+` check misses pure-digit coding answers;
  `adaptive_live.py` uses digit-exact scoring for coding, lookup logic unchanged.
- Small samples: 8+8 cases, one measurement each, sequential epochs, warmed
  means (first selector call ~0.5 s excluded from means but real on cold start),
  reference PyTorch fallbacks for student linear-attention/conv. No confidence
  intervals. The dev/held flip (adaptive loses dev, wins held) is exactly what
  small-benchmark noise looks like — treat this as exploratory, not a victory.
- 100%-retention generation equivalence passed both splits; zero model API
  errors; standalone-full 8/8 both splits (novel prompts are solvable).

## Verification and isolation

- `tests/test_strata_specprefill.py`: **21 passed, 5 skipped**; Ruff clean on
  all four `adaptive_*.py` files.
- Both live sweeps: 72 requests each (8 standalone + 64 co-located),
  `COMPLETE.json`, exit 0, port 18587 closed afterwards.
- Existing implementation untouched except the compatible `top_mass` key fix.
  New code: `adaptive_cases.py`, `adaptive_signals.py`, `adaptive_offline.py`,
  `adaptive_live.py`. Raw prompts/responses/logs under ignored
  `data/strata_specprefill/adaptive-*`; production config and service unchanged.

## Recommendation

Keep mass-0.80 as a **frozen exploratory baseline**, not a production router.
Next step for genuine generalization: larger diverse sets (multi-hop, reasoning,
longer contexts, real repos with novel identifiers), repeated measurements with
intervals, and cold-start accounting — before any claim that adaptive beats
fixed 30% in general. `summary.json` has the machine-readable numbers.
