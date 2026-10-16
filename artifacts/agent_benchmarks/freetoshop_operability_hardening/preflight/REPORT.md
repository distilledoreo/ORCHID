# Short live Pi → ORCHID → Solar preflight

## Measured facts

- Provider: OpenRouter `upstage/solar-pro4`.
- Pi: `0.84.2`; native Pi compaction disabled.
- Fresh ORCHID DB and fresh sealed FreetoShop historical workspace were used.
- Synthetic continuation/control messages were disabled because the Pi RPC request path did not expose a reliable control-origin distinction to the gateway.
- The observed run span was approximately `43.3` minutes before the controller wall-clock stop path.
- Pi emitted `185` assistant message-end events, `122` tool calls, `62` completed turns, and one provider error event.
- ORCHID recorded `61` successful streams, one `provider_error_frame` failure, and one final provider-started request left in flight by harness termination. The next two requests after the observed live failure completed successfully.
- The journal contained `186` events. `185` message-hash events had `185` unique hashes; no duplicate message hashes were observed.
- Three coalesced compaction jobs were admitted: two failed in the canonicalizer after the 180-second timeout and one was still running at stop. No ACTIVE capsule was promoted.

## Interpretation

The provider-stream continuity fix behaved correctly in the live run: an upstream error frame produced a terminal failure response and the following requests progressed. The deterministic fault-injection suite remains the stronger coverage for disconnect-before-token, partial text/tool streams, EOF, HTTP errors, cancellation, and 120-cycle recovery.

The preflight did not pass the compaction operability gate. The canonicalizer provider/local-model stage timed out twice, so no watermark advancement or ACTIVE promotion occurred. Consequently, it cannot establish backlog convergence or context collapse. This is an operational blocker distinct from the stream-continuity fix and from scheduler queue amplification.

The controller itself also needed forced child cleanup after its abort command did not close the Pi process; this was harness cleanup only, not a coding-agent restart or manual rescue. The preflight driver has since been hardened to kill its child after a bounded post-abort grace period.

## Gate result

`NOT_READY_FOR_FULL_AB_RERUN`

The stream and ingestion portions passed their observed checks, but repeated successful ACTIVE promotions, watermark advancement, retirement catch-up, and awake-context collapse were not demonstrated. The next operational work should isolate and fix the canonicalizer timeout/throughput path before another long A/B run.
