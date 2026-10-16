# LM Studio / llama.cpp backend capability inventory

Inventory captured before the explicit prefix-cache validation run.

## Local process and runtime

- LM Studio service process: `LM Studio.exe --run-as-service`.
- LM Studio version observed in the local crashpad command metadata: `0.4.21+2`.
- `lms.exe` CLI commit: `71bd99c`.
- Selected runtime: `llama.cpp-win-x86_64-vulkan-avx2@2.28.2`.
- OpenAI-compatible inference endpoint: `http://127.0.0.1:1234/v1/chat/completions`.
- Native `/api/v1/chat` was not available on this local server (HTTP 404 on a read-only `OPTIONS` probe and on a read-only `GET` probe). The local server did expose `/api/v1/models`.

## Observed loaded Qwen instance

`GET /api/v1/models` reported one loaded Qwen instance:

```json
{
  "id": "qwen3.5-4b@q6_k",
  "config": {
    "context_length": 32768,
    "eval_batch_size": 2048,
    "physical_batch_size": 512,
    "parallel": 1,
    "flash_attention": true,
    "context_checkpoints": 32,
    "offload_kv_cache_to_gpu": true
  }
}
```

The OpenAI-compatible response exposed usage and token arrival, but no explicit cache-hit, slot, session, prompt-eval, queue-wait, eviction, or KV-cache identifier. Ordinary `Cache-Control: no-cache` headers are HTTP transport metadata and are not treated as backend cache evidence.

## Controls verified locally

| Capability | Local evidence | Status for this phase |
|---|---|---|
| Loaded model instance identifier | `GET /api/v1/models`; LM Studio SDK `loadModel` channel | Used to route the explicit arm to a dedicated serial instance |
| Serial slot count | Loaded config `parallel: 1`; raw SDK load field `llm.load.numParallelSessions=1` | Held fixed |
| Context checkpoints | Loaded config `context_checkpoints: 32`; bundled LM Studio SDK schema and raw load field `llm.load.llama.contextCheckpoints` | Explicit arm changes only 32 to 148 |
| KV-cache offload | Loaded config `offload_kv_cache_to_gpu: true` | Held fixed |
| Context length, batch, Flash Attention | Loaded config and raw load fields | Held fixed |
| Prompt/prefix cache key | No OpenAI-compatible request control observed | Not available through the selector request path |
| Persistent slot save/restore | No LM Studio control or endpoint observed | Not used |
| Slot prompt similarity | No LM Studio control or endpoint observed | Not used |
| Cache RAM / cache lifetime / eviction counters | No LM Studio control or telemetry observed | Not directly measurable |
| Stateful native chat | Public LM Studio documentation describes `/api/v1/chat`, but the local server returned 404; using it would also change the selector request semantics and dynamic schema path | Not used |

## Frozen explicit condition

The benchmark uses the smallest verified backend-only capacity control that can be sent to this local runtime: `llm.load.llama.contextCheckpoints`. The explicit value is `148`, selected before outcome evaluation because the frozen selector trace contains exactly 148 calls. All other load fields are held to the observed baseline values, and the exact same streamed OpenAI-compatible request body is used apart from the backend instance identifier in the `model` field.

The control is benchmark-only. It is not wired into ORCHID production behavior.
