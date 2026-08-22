# Local endpoint concurrency

## Measured facts

- model `qwen3.5-4b@q6_k`, concurrency 1: aggregate rate 27.43 source tok/s, timeouts 0, failures 0.
- model `qwen3.5-4b@q6_k`, concurrency 2: aggregate rate 32.70 source tok/s, timeouts 0, failures 0.
- model `qwen3.5-4b@q6_k:2`, concurrency 1: aggregate rate 11.84 source tok/s, timeouts 0, failures 0.
- model `qwen3.5-4b@q6_k:2`, concurrency 2: aggregate rate 20.20 source tok/s, timeouts 0, failures 0.


The earlier controlled parallel=1 contextual pair completed in approximately 39.09s and 78.24s, demonstrating serialized endpoint queuing. A parallel=2 result is only accepted if the separately recorded endpoint status confirms the runtime setting.

## Interpretation

Overlapping client coroutines are not sufficient evidence of concurrent inference. The endpoint's configured parallel slots and per-request latency must be read together.
