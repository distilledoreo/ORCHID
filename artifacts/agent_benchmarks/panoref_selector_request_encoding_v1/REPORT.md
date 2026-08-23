# PanoRef selector request-encoding benchmark

This is a benchmark-only result. No production ORCHID path, selector chunking,
backend concurrency, Solar stage, or oracle was changed.

- Source commit: `a699d6a4af3696dc2992049b2ebbc748493eb3d9`
- Plan hash: `ee0e056f215c881640fd6ac3caededac735aecac5fe558cbc057402fccddfd19`
- Frozen replay SHA-256: `aa0e7326c2514501a8c53a5a2c4669b1512383507989e71403d4e6f00f7eaf07`
- Frozen historical selector summary SHA-256: `68af78ade0c3ba69c39a67d12269161519ced0d322c5c0d0ca82bdbf6640966e`
- Model/backend: `qwen3.5-4b@q6_k` / `http://127.0.0.1:1234/v1`
- Trace: 148 serial 1.2K selector chunks; gate is 24 evenly spaced chunks
- Retries: none; failed calls retained

## Measured arms

| Arm | Calls | Success | Selector wall | TTFT p50 | Wall p50 | Decode p50 | Input tokens | Output tokens | Frozen decision match | Paired decision match | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| keep_opaque_gate | 24 | 24/24 | 276.3 s | 7776.4 ms | 9718.1 ms | 1955.8 ms | 40529 | 772 | 23/24 | 24/24 | 0 |
| keep_short_gate | 24 | 24/24 | 228.8 s | 7423.0 ms | 8210.8 ms | 628.1 ms | 38966 | 276 | 23/24 | 24/24 | 0 |
| keep_opaque_full | 148 | 148/148 | 1821.3 s | 8409.9 ms | 10988.2 ms | 2027.8 ms | 261050 | 5478 | 139/148 | 142/148 | 0 |
| keep_short_full | 148 | 148/148 | 1474.7 s | 8168.0 ms | 9078.5 ms | 638.4 ms | 250235 | 1869 | 137/148 | 142/148 | 0 |
| drop_opaque_gate | 24 | 24/24 | 273.6 s | 7840.8 ms | 9864.9 ms | 2031.8 ms | 40769 | 730 | 0/24 | 0/24 | 0 |

`Frozen decision match` recomputes the historical decision per chunk by
intersecting that chunk's legal IDs with the historical global selector set.
The historical telemetry rows do not contain a per-row selection field.

## Request composition

The frozen serializer is evidence-dominated. Across all 148 chunks:

| Variant | Evidence chars | Schema chars | System chars | Request-ID chars | Actual provider input tokens |
|---|---:|---:|---:|---:|---:|
| KEEP opaque | 575935 | 50502 | 31376 | 7530 | 261050 |
| KEEP short IDs | 569097 | 43664 | 31376 | 692 | 250235 |
| DROP opaque | 575935 | 50946 | 39516 | 7530 | 40769 / gate |

Short IDs reduced actual full-trace input usage by
`10815`
tokens (4.14%).
The observed short-ID output reduction is much larger, but it is coupled to
the six semantic divergences and is therefore not an acceptable semantics-
preserving win.

## Semantic findings

- Short-ID KEEP: 24/24 paired gate matches; full trace 142/148 paired matches.
- Full short-ID divergences: chunks 71, 85, 90, 94, 129, and 133.
- DROP-only KEEP derivation: 0/24 paired matches; no full DROP run was allowed.
- No selector protocol failures, unknown-ID failures, or timeouts occurred in
  any executed arm.

## Interpretation

Short IDs clearly reduce serialized/request tokens and measured wall on this
backend, but the exact-ID representation is not decision-equivalent across the
full trace. The gate was too small to expose the six full-trace divergences.
DROP-only did not preserve the selector's semantic job in the first gate and
was rejected before expansion.

This phase therefore does not validate either request variant as a safe
semantics-preserving optimization. The full-arm timing is descriptive only;
the two 148-call runs occurred serially in different backend latency regimes.

Next hypothesis only: isolate the six short-ID divergence chunks with a
semantics-preserving request representation (for example, preserve original
IDs in evidence while shortening only schema/output aliases) before any
production consideration.
