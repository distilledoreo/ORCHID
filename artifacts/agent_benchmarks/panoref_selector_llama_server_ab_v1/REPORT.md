# PanoRef llama-server backend A/B

Frozen 24-call serial selector comparison using the exact first 24 PanoRef 1.2K requests.
The request body, model name, prompt, dynamic exact-ID schema, and decoding settings were held fixed; only the serving endpoint changed.

| Condition | Calls | TTFT p50 | TTFT p95 | Decode p50 | Total wall p50 | Decision match | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|
| LLAMA_CPP_EXPLICIT | 24 | 8038.0 ms | 14874.2 ms | 1920.1 ms | 9992.0 ms | 20/24 | 0 |
| LM_STUDIO_CURRENT | 24 | 8052.4 ms | 14896.5 ms | 1957.9 ms | 10042.5 ms | 20/24 | 0 |

Decision equivalence: 20/24 identical; divergences: 4.
Raw request hashes identical: 24/24; semantic request hashes identical: 24/24.

The four divergences were chunks 3, 7, 14, and 15. In each divergent pair,
llama.cpp returned a strict subset of the LM Studio selection (the first legal
event ID only), despite identical raw request hashes.

## Cache evidence

- `n_prompt_tokens_cache` was `0` in all 24 llama.cpp `/slots` snapshots.
- llama.cpp `/metrics` exposed prompt-token and prompt-time counters, but no
  cached-prompt-token counter showing reuse; the cumulative prompt processing
  continued across the full arm.
- The server log explicitly recorded: `cache_reuse is not supported by this
  context, it will be disabled`.
- Only two requests selected the slot by LCP similarity. Their measured
  similarity/keep values were `0.185 / 0.030` and `0.138 / 0.037`, not a large
  stable-prefix reuse.
- No full 148-call expansion was run because the tiny arm already failed the
  100% decision-equivalence gate and showed no sustained warm TTFT phase.

## llama.cpp controls

The explicit arm used one server slot, no continuous batching, prompt caching, cache reuse threshold 256, 148 context checkpoints, 8192 MiB cache RAM, slot prompt similarity 0.1, /slots, and /metrics.

The raw /slots and /metrics samples are preserved in each telemetry row. LM Studio endpoint probes are preserved too; unsupported endpoints are recorded as observations, not treated as cache evidence.

The standalone server launch log is preserved at
`server/stderr.log`; its stdout is at `server/stdout.log`.

## Frozen identity

- Source commit: `78be7ffd157c62bd6fedbb9de166547bb1f43498`
- Plan SHA-256: `3be117f6334f6875519269af59523855d1a23a78a1bbd11d5da52d2f615182c5`
- Replay SHA-256: `aa0e7326c2514501a8c53a5a2c4669b1512383507989e71403d4e6f00f7eaf07`
- Chunk plan SHA-256: `493d81592ee3d664a87cfa54b61c213946e27f1c2ea192345b01af7329669730`
- Profile config SHA-256: `19157d0b9e59859182b512d24f2b26e9bfbba17aea76f49e8cca0246e1d7d4d2`

No production ORCHID changes, selector semantic changes, chunk-size changes, concurrency, or Solar calls were made.
