# Role switching versus role-affine logical clients

## Measured facts

- `CONFIG_1_SHARED_CLIENT_ROLE_SWITCH`: 2 iterations, 1 failures, 1 timeouts, mean request wall time 92.85s.
- `CONFIG_2_TWO_PERSISTENT_ROLE_CLIENTS`: 2 iterations, 0 failures, 0 timeouts, mean request wall time 82.32s.

- TTFT was unavailable because the benchmark used non-streaming JSON requests.
- No KV/prefix-cache hit metric was exposed by the OpenAI-compatible endpoint or LM Studio CLI.

## Interpretation

The experiment can compare total request wall time and failure rate, but cannot claim prompt-prefill or KV-cache savings. Any difference without exposed prefill/cache counters is endpoint timing evidence only, not proof of role affinity.
