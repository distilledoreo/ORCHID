# PanoRef selector language representation benchmark: `gate_01`

Plan hash: `485600a1da4cd40317b9a29a80b62e157c6de34334da9ebaccf025eb7e3628c5`; source commit: `b09c1b865905e5d806d7ce2413726127e3ad467a`.
The three arms were run serially in the same chunk order with the same Qwen model, schema, opaque IDs, and generation settings. Tool/code/diagnostic source items were unchanged.

| Arm | Calls | Success | Input p50 / mean / total | Token reduction vs A | TTFT p50 / p95 / mean | Prompt eval | Decode p50 | Wall p50 | Output tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A_CANONICAL_ENGLISH | 24 | 24 | 1482.5 / 1688.7083333333333 / 40529.0 | None | 8164.61170000548 / 15320.737969981565 / 9409.143579159718 | None | 1948.0035500018857 | 10105.427600006806 | 695.0 |
| B_FAITHFUL_CHINESE | 24 | 24 | 1489.5 / 1697.5416666666667 / 40741.0 | -0.0052308223741024396 | 8193.901649996405 / 15275.864070017997 / 9444.181908334334 | None | 1938.4573499846738 | 9823.245850013336 | 578.0 |
| C_DENSE_CHINESE | 24 | 24 | 1471.5 / 1679.4166666666667 / 40306.0 | 0.005502232968985221 | 8062.547900000936 / 15274.732575011149 / 9387.153300001955 | None | 1935.5408500123303 | 9833.017900004052 | 525.0 |

## Paired semantic and latency audit

- B_FAITHFUL_CHINESE: decision agreement `18/24`; token-reduction/TTFT-reduction correlation `0.7433215030931047`.
- C_DENSE_CHINESE: decision agreement `16/24`; token-reduction/TTFT-reduction correlation `0.5740355447360831`.

Prompt-eval telemetry is reported only when the backend exposed it; client-observed TTFT is measured from request start to the first streamed content token and is not inferred from total wall time.

