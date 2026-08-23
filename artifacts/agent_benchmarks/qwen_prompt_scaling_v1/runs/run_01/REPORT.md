# Qwen prompt-length scaling benchmark: `run_01`

Plan hash: `265d64e8759bdd217c467104d77d3225bf7c6f56de2ee0eb859170cc9d158772`; source commit: `c4458ffeca7f1e02f9c4d4e571bab979b5ecaecd`.
Model: `qwen3.5-4b@q6_k`; serial; 6 repetitions per target; 3 common warmups. Each measurement used a distinct nonce to avoid exact-repeat request identity.

| Target tokens | Calls | Actual input p50 / mean | TTFT p50 | TTFT p95 | Total wall p50 | Backend prompt eval |
|---:|---:|---:|---:|---:|---:|---:|
| 128 | 6 (6 ok) | 129.0 / 129.0 | 909.3027499911841 ms | 939.8262750110007 ms | 988.4964999946533 ms | None ms |
| 256 | 6 (6 ok) | 224.0 / 224.0 | 1473.742799993488 ms | 1481.8496749794576 ms | 1553.369350003777 ms | None ms |
| 512 | 6 (6 ok) | 414.0 / 414.0 | 2348.5506500001065 ms | 2358.9155249937903 ms | 2430.772399995476 ms | None ms |
| 1000 | 6 (6 ok) | 774.0 / 774.0 | 4122.114350000629 ms | 4136.03485000931 ms | 4205.824300006498 ms | None ms |
| 1500 | 6 (6 ok) | 1144.0 / 1144.0 | 6110.2192499965895 ms | 6133.153649992892 ms | 6191.0698499996215 ms | None ms |
| 2000 | 6 (6 ok) | 1514.0 / 1514.0 | 8153.901600002428 ms | 8168.5791249983595 ms | 8236.18589999387 ms | None ms |
| 4000 | 6 (6 ok) | 2999.0 / 2999.0 | 16683.84125000739 ms | 17334.470100016915 ms | 16770.414950005943 ms | None ms |

## Fixed-plus-linear fit

`TTFT_ms = 23.022 + 5.528220 * input_tokens` (n=42, R²=0.9980).
Estimated fixed component: `23.0 ms`; estimated prompt component: `5528.2 ms per 1,000 input tokens`.
Predicted TTFT at 0 / 128 / 4,000 tokens: `23.0` / `730.6` / `22135.9` ms.

Backend prompt-eval, queue-wait, and scheduling metrics were recorded when exposed. LM Studio did not expose prompt-eval timing if the corresponding summary values are null; client TTFT is measured directly from request start to first streamed content token.

## Conclusion

TTFT rises materially with prompt length and the linear fit explains a substantial fraction of observed variance. Investigate prompt-eval throughput and kernels next.

PREFILL_SCALING_DOMINATES
