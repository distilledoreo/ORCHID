# Compaction throughput replay

## Measured facts

This is a provider-free structural control replay. It uses the real SQLite snapshot, lease, validation, CAS promotion, watermark, and provenance path over the frozen captured event stream. The stage counters are deterministic controls; they are not Qwen/Solar quality or latency measurements.

| target | jobs | source tokens | wall ms | retired tokens/sec | active tokens | promotions correct |
|---:|---:|---:|---:|---:|---:|:---|
| 8000 | 17 | 166107 | 1320.65 | 125776.72 | 91 | True |
| 16000 | 9 | 166107 | 677.12 | 245315.75 | 111 | True |
| 32000 | 6 | 166107 | 456.20 | 364113.63 | 56 | True |
| 64000 | 3 | 166107 | 232.43 | 714655.72 | 311 | True |

The highest provider-free structural rate was the `64000` target at `714655.72` source tokens/sec. No provider calls were made by this control replay.

## Captured provider-backed observation

`{"available": true, "job_id": "job_df20282fc6594352af9b294ca7de6551", "source_tokens": 39293, "wall_seconds": 1232.789, "retired_source_tokens_per_second": 31.87325649401479, "model_runs": 63, "input_tokens": 282179, "output_tokens": 4671, "provider_wall_ms_sum": 1231903.1806001149, "note": "single provider-backed promotion from the failed run; not a stable throughput sample"}`

The captured run contains one promoted provider-backed job only, so it is a warning-level observation rather than a stable batch-size benchmark.
