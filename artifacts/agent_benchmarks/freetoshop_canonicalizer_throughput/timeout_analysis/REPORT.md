# Canonicalizer timeout analysis

## Measured facts

The prior live capture contains two canonicalizer timeouts at exactly 180 seconds. Both had no first-token timestamp, no output tokens, no finish reason, and no successful response. The source batches were 8 and 11 source references respectively; one successful preceding canonicalizer call had 11,086 prompt tokens and took 121.67 seconds.

The new client has an explicit total async request deadline and records `error_category=timeout` with `failure_phase=total_request_deadline`. A deterministic delayed HTTP test confirmed the timeout record and an immediate next request succeeded.

## Interpretation

The prior failure was a non-streaming local endpoint request that could remain awaiting response headers beyond the nominal client timeout. The evidence does not prove whether the local model was queued, stalled, or still generating; non-streaming mode exposes no progress signal. The safe response is a bounded deadline plus fail/cleanup, not a larger blind timeout.

Output ceilings of 512 and 1,024 tokens improved some wall times but produced invalid truncated JSON on the same corpus, so they are rejected.

The full frozen replay then reproduced the failure under the corrected harness: two valid 12K batches completed, and batch 2 reached the explicit 180-second deadline without a response. Fifteen later batches were not attempted. This is a real zero-timeout gate failure, not a throughput estimate to average away.
