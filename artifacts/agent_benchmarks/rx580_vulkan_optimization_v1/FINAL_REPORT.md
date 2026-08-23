# RX 580 / Vulkan Runtime Optimization Audit

Plan hash: `24a85266cf47aa99122b21f7522d4438777676f346ca4ae18d7641b2959637f7`; source commit: `165788062179d0474fcb7d1f217279c3bd54e8de`.
Model: `qwen3.5-4b@q6_k`; GGUF SHA-256: `12a6d0136484ecbd85f91860924b1bf571da66e2cebfa507522efc01f9b7d1d9`; llama.cpp build: `fe2adf0`.
No reboot, driver change, power-plan change, firmware change, display reset, or production ORCHID change was performed.

| Experiment | Prompt tok/s p50 | vs baseline | TTFT p50 | TTFT p95 | Prompt eval p50 | Errors |
|---|---:|---:|---:|---:|---:|---:|
| baseline_full_vulkan | 184.9 | 0.0% | 8320.4 ms | 8337.9 ms | 8210.0 ms | 0 |
| batch_1024 | 184.8 | -0.1% | 8299.5 ms | 8314.8 ms | 8215.0 ms | 0 |
| batch_4096 | 184.9 | 0.0% | 8297.6 ms | 8301.3 ms | 8208.0 ms | 0 |
| ctx_32768 | 184.9 | 0.0% | 8321.6 ms | 8331.8 ms | 8208.0 ms | 0 |
| flash_attention_off | 194.4 | 5.1% | 9323.1 ms | 9348.2 ms | 7810.0 ms | 0 |
| kv_unified_on | 185.0 | 0.0% | 8291.1 ms | 8299.6 ms | 8207.0 ms | 0 |
| mmap_mlock | 185.1 | 0.1% | 8295.2 ms | 8308.9 ms | 8200.0 ms | 0 |
| threads_batch_12 | 184.9 | -0.0% | 8295.3 ms | 8302.4 ms | 8211.0 ms | 0 |
| threads_batch_3 | 184.9 | 0.0% | 8292.6 ms | 8308.2 ms | 8209.0 ms | 0 |
| ubatch_1024 | 181.8 | -1.7% | 8461.3 ms | 8522.7 ms | 8351.0 ms | 0 |
| ubatch_256 | 183.8 | -0.6% | 8347.9 ms | 8350.6 ms | 8257.0 ms | 0 |

## Interpretation

Prompt-evaluation tok/s is taken from direct llama-server `/metrics` counter deltas. Client TTFT is measured from request start to first streamed content token. The fixed request sequence is reused across all arms; direct prompt caching is disabled.

The current direct full-Vulkan baseline is the pinned reference. Any candidate below 5% repeatable improvement is noise/not worth adopting under the audit threshold.

## Hardware/runtime limits

The system inventory records RX 580 device identity, Vulkan device selection, VRAM availability, driver/runtime versions, process configuration, and Windows GPU counters. Core clock, memory clock, temperature, and PCIe negotiated width/generation were not exposed by the available read-only interfaces.

No newer local Vulkan llama.cpp build or llama-bench executable was available; no build toolchain or driver install was added. See `DEFERRED_REBOOT_REQUIRED.md`.
The earlier harness-stop attempt is preserved under `experiments_ATTEMPT_01/` with its original plan and partial artifacts; it is excluded from the primary comparison.

## Decision answers

1. Full Vulkan execution is supported by the selected AMD RX 580 Vulkan device, explicit `--n-gpu-layers 999`, successful model load, stable prompt counters, and no fallback/OOM errors. The server logs do not expose a layer-by-layer allocation report, so this is strong operational evidence rather than a claim about an unavailable internal counter.
2. Vulkan reports 8192 MiB total and about 7402 MiB free at inventory time. During requests the direct process held about 3.96 GiB dedicated GPU memory; Windows also reported about 0.82 GiB non-local usage for that process. No crash or throughput collapse occurred, but the available counters cannot prove that every allocation stayed local to VRAM.
3. Core/memory clocks and temperature were not exposed by the installed read-only tools. Stable repeated prompt timings show no obvious benchmark instability, but they do not prove absence of thermal or power throttling.
4. PCIe negotiated generation/width was not exposed; no normality claim is made.
5. The current driver is not obviously problematic: Vulkan identifies the RX 580 and performs stably. AMD Adrenalin 26.5.2 is deferred for a later maintenance window.
6. No OS power or GPU preference was changed because the machine is remote and the available control was global Balanced/High-performance selection.
7. No newer local Vulkan llama.cpp build or usable local build toolchain was available; no replacement was attempted.
8. No safe runtime flag produced a useful end-to-end improvement. Flash Attention off measured 5.1% prompt-counter improvement but changed TTFT by 12.1% and is rejected. Batch 4096, context 32768, KV unified, mmap+mlock, and thread variants were effectively tied.
9. Batch 2048 / ubatch 512 / Flash Attention on is retained as the safe reference. Shader/pipeline warmup was not materially significant after the one warmup; model startup itself remained separate overhead.
10. Background GPU consumers were visible in Windows counters, but no safe A/B closure test was performed; contention is therefore unquantified.
11. Best repeatable prompt-counter rate was 194.4 tok/s with Flash Attention off, but the best safe configuration is the baseline at 184.9 tok/s because the faster counter did not improve TTFT.
12. Further RX 580 tuning is unlikely to justify engineering time without a clock/thermal/PCIe telemetry provider or a newer Vulkan build; prompt reduction remains the higher-confidence optimization axis.

RX580_STACK_ALREADY_NEAR_OPTIMAL
