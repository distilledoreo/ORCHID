# PanoRef selector language representation experiment

Verdict: Chinese input is not qualified for the 148-call expansion. The 24-call gate completed all 72 selector calls, but neither Chinese arm preserved selector decisions and neither materially reduced prompt tokens or TTFT.

## Frozen identity

- Plan: `LANGUAGE_PLAN.json`, hash `485600a1da4cd40317b9a29a80b62e157c6de34334da9ebaccf025eb7e3628c5`
- Harness source commit pinned by the plan: `b09c1b865905e5d806d7ce2413726127e3ad467a`
- Frozen PanoRef replay SHA-256: `aa0e7326c2514501a8c53a5a2c4669b1512383507989e71403d4e6f00f7eaf07`
- Model: `qwen3.5-4b@q6_k`; serial; temperature 0; top-p 1; reasoning off; exact opaque IDs and response schema preserved.
- Gate: the existing 24 frozen indices `[0,6,13,19,26,32,38,45,51,58,64,70,77,83,89,96,102,109,115,121,128,134,141,147]`.
- No full 148-call run was authorized after the gate failed semantic qualification.

## Results

| Arm | Calls | Success | Input p50 / mean / total | Reduction vs English | TTFT p50 / p95 / mean | Prompt eval | Decode p50 | Wall p50 | Output total | Decision agreement |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Canonical English | 24 | 24 | 1,482.5 / 1,688.7 / 40,529 | control | 8,164.6 / 15,320.7 / 9,409.1 ms | unavailable | 1,948.0 ms | 10,105.4 ms | 695 | control |
| Faithful Chinese | 24 | 24 | 1,489.5 / 1,697.5 / 40,741 | **-0.52%** | 8,193.9 / 15,275.9 / 9,444.2 ms | unavailable | 1,938.5 ms | 9,823.2 ms | 578 | **18/24 (75.0%)** |
| Dense Chinese | 24 | 24 | 1,471.5 / 1,679.4 / 40,306 | **0.55%** | 8,062.5 / 15,274.7 / 9,387.2 ms | unavailable | 1,935.5 ms | 9,833.0 ms | 525 | **16/24 (66.7%)** |

TTFT is measured directly from request start to the first streamed content token. LM Studio exposed no prompt-eval/prefill duration, queue-wait, or cache counter in these responses.

## Paired analysis

Relative to the English control, median token deltas were negligible. Faithful Chinese increased total input tokens by 212; dense Chinese reduced them by 223. Mean TTFT changed by +35.0 ms for faithful Chinese and -22.0 ms for dense Chinese. These are below the requested qualification threshold and are not evidence of a prefill win.

The paired token-reduction/TTFT-reduction correlations were `r=0.743` for faithful Chinese and `r=0.574` for dense Chinese, but the token deltas were approximately zero, so these correlations do not establish a useful compression relationship.

Backend prompt-eval telemetry was unavailable; therefore no claim is made that prefill time changed independently of client-observed TTFT.

## Divergence audit

Faithful Chinese diverged at chunks `13, 77, 102, 115, 134, 141`. Dense Chinese diverged at chunks `13, 77, 89, 102, 109, 115, 134, 141`.

Notably, chunks `13`, `77`, `109`, and `134` had identical canonical, faithful, and dense evidence payload hashes, yet the Chinese instruction/wrapper still changed the selected-ID set. This is a representation-dependent selector decision change, not an evidence translation effect. The canonical English arm matched the prior opaque-ID gate decision on 23/24 chunks; its one difference was chunk 38, consistent with previously observed backend/model variance.

No schema errors, unknown IDs, timeouts, or transport failures occurred.

## Recommendation

Keep the canonical English selector representation with the existing opaque IDs. The Chinese arms did not achieve meaningful token compression, did not reduce measured TTFT, and failed the semantic gate. Do not use either Chinese representation in production or expand it to the full trace.

Next experiment: none is justified by this phase; any further selector optimization should first expose backend prompt-eval telemetry or test a representation that can pass the exact decision-equivalence gate.

LANGUAGE_INPUT_COMPRESSION_NOT_VALIDATED
