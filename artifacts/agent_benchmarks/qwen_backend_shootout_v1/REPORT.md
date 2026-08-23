# Qwen backend/kernel prompt-processing shootout

Plan hash: `45a06f9caa36761322f83a258777618706bab8f4010afc52d906f78a2c9db914`; source commit: `3c84632fbf02dd528e78ece3303a06e576a7964f`.
Model: `qwen3.5-4b@q6_k`; GGUF SHA-256: `12a6d0136484ecbd85f91860924b1bf571da66e2cebfa507522efc01f9b7d1d9`; server build: `fe2adf0`.
Five serial measurements plus one warmup per condition; nominal estimate 2000, actual usage is reported below. `llama-bench` available: `False`.

| Condition | Calls | Actual input p50 | TTFT p50 | TTFT p95 | Wall p50 | Decode p50 | Backend prompt eval p50 | Backend prompt tok/s | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LM_STUDIO_CURRENT | 5/5 | 1519.0 | 8149.2 ms | 8155.1 ms | 8230.0 ms | 80.6 ms | — ms | — | 0 |
| LLAMA_CPP_VULKAN_ALL | 5/5 | 1522.0 | 8359.0 ms | 8424.9 ms | 8439.9 ms | 80.9 ms | 8248.0 ms | 184.5 | 0 |
| LLAMA_CPP_VULKAN_PARTIAL | 5/5 | 1518.0 | 14777.6 ms | 14904.1 ms | 14895.5 ms | 117.9 ms | 14712.0 ms | 103.2 | 0 |
| LLAMA_CPP_CPU | 5/5 | 1519.0 | 9690.1 ms | 9778.6 ms | 9887.1 ms | 200.9 ms | 9629.0 ms | 157.8 | 0 |

## Findings

LM Studio current and direct llama-server all-layer Vulkan are within the observed small-run spread, while 20-layer partial offload is materially slower. The CPU control is faster than partial Vulkan but slower than all-layer Vulkan.

All 20 measurement calls returned the exact `OK` contract. Direct server return code `1` in `RUN_END.json` records the harness terminating the server after a completed arm; it is not a request failure.

## Interpretation

Client TTFT is measured from request start to the first streamed content token. Direct llama-server prompt processing uses `/metrics` counter deltas; LM Studio's OpenAI endpoint did not expose equivalent prompt-time counters.

The fixed prompt, model request, sampling settings, serial execution, and exact GGUF are held constant. Direct arms disable llama.cpp prompt caching; each request also carries a distinct nonce so the LM Studio arm does not receive exact-repeat requests.

Raw per-call records and direct server logs are preserved under `runs/`.
