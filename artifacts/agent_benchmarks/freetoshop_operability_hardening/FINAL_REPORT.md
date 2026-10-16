# ORCHID FreetoShop operability hardening

Recommendation: **NOT_READY_FOR_FULL_AB_RERUN**

This phase fixed and tested the gateway stream-continuity boundary, added durable coalescing state, audited exact event ingestion, froze the captured replay, measured structural retirement throughput, simulated backlog, and ran one short live Pi → ORCHID → Solar preflight. It did not add retrieval capabilities or change semantic memory policy.

## Measured facts

### 1. What caused the continuity stall?

The captured upstream stream ended without a normal `finish_reason` and `[DONE]` terminator. ORCHID forwarded the incomplete stream as if it were a normal response. Pi exhausted its retry path and settled; its queued follow-up was accepted but did not emit another provider request. The gateway log contains no post-follow-up POST, so the captured evidence does not indicate a gateway lock held across requests.

The gateway now detects HTTP/provider error frames, malformed/partial/EOF streams, emits a bounded synthetic error chunk plus `[DONE]`, records cleanup separately, and keeps failure diagnostics fail-open.

### 2–4. Can it be reproduced, and did recovery wedge?

Yes. `tests/test_operability_hardening.py` covers disconnect-before-token, partial text, partial tool call, provider error frame, missing finish/EOF, HTTP error, immediate follow-up, cancellation, and split SSE frames. A 120-cycle fault-injection soak passed with no persistent request/queue/lock wedge. The live preflight also observed one Solar `provider_error_frame`; the next two requests completed successfully.

### 5–6. Was ingestion duplicating prefixes?

The old captured journal stored full request payloads and response blobs. It therefore retained repeated prefixes inside successive request events. The audit reconstructed `199` unique logical events (`166,107` estimated tokens), versus `8,691,344` request-payload tokens and `10,607,383` stored authoritative event tokens. Reused prefix tokens were `7,804,154`; request-payload amplification was `52.32x`, and stored-event amplification was `63.86x` over the exact replay.

The new path uses ordered canonical message hashes, normalizes only provider-transient reasoning/metadata and JSON argument formatting, and appends only the novel suffix. It does not fuzzy-deduplicate legitimate repeated messages. The live preflight produced `185` message-hash events with `185` unique hashes.

### 7–8. Is compaction work bounded, and how many stale jobs are produced?

Yes under the tested scheduler pressure pattern: fifty pressure signals during one running job produced one RUNNING job, zero queued duplicates, and one durable dirty indication. After promotion, one fresh snapshot was admitted from the current watermark. New coalescing pressure produced zero new stale queued jobs; legacy queued snapshots are safely parked as stale/obsolete. Lease/CAS and expired-lease tests pass.

The old captured run had `85` jobs, including `42` stale and `41` queued. That is historical evidence of amplification, not a result from the new scheduler.

### 9. Which batch size was fastest?

In the provider-free structural replay, the exact event-integrity-achievable `64K` target was fastest: `166,107` source tokens across `3` jobs in `232.43 ms`, or `714,655.72` source tokens/sec. All promotions, watermarks, and provenance checks passed. This is not a model-quality or provider-latency result.

The only captured provider-backed promotion retired `39,293` source tokens in `1,232.789 s`, or `31.873` tokens/sec, with `63` internal model runs. It is a single warning-level sample, not a stable batch-size comparison.

### 10–13. Arrival, retirement, and safety margin

The frozen real trace contains `166,107` novel tokens over `2,808.335 s`: average arrival `59.148 tokens/sec`, tool arrival `49.289 tokens/sec`, assistant arrival `8.215 tokens/sec`, and peak 60-second arrival `407.283 tokens/sec`.

The measured provider-backed retirement rate is `31.873 tokens/sec`. Retirement/arrival ratio is `0.5389`; the shortfall is approximately `27.275 tokens/sec`. Therefore retirement is not greater than sustained average arrival, and there is no positive safety margin in the captured provider-backed evidence.

### 14. Does backlog converge?

No under the measured provider-backed rate. The replay simulation reaches a peak of approximately `77,909` uncompacted tokens and ends at `76,596` tokens, with an estimated `2,403 s` catch-up time after the trace. The old pressure scheduler would represent `191` queued jobs at the end; the coalesced scheduler represents zero queued duplicates and one dirty condition. Coalescing bounds control-plane work, but cannot make a slower compactor catch up by itself.

The simulation uses the real arrival timestamps and measured provider rate; it does not invent a model throughput number or pretend the provider-free structural rate is production compaction throughput.

### 15–16. ACTIVE and provenance correctness

Focused tests and the structural replay preserved CAS promotion, watermarks, coverage, and provenance; every replay promotion was structurally correct and provenance-valid. The live preflight produced zero promotions and zero ACTIVE capsules, so it provides no evidence of successful live ACTIVE content quality, but it also showed no ACTIVE corruption.

### 17–19. Short live preflight

The preflight ran approximately `43.3` minutes with Pi `0.84.2`, Solar Pro via OpenRouter, native Pi compaction disabled, a fresh ORCHID database, and no synthetic continuation messages. It recorded `185` assistant message ends, `122` tool calls, `61` successful streams, and one provider-error-frame failure followed by two successful requests.

It did not complete the required promotion gate: two canonicalizer calls timed out at `180 s`, a third job was still RUNNING at harness stop, zero promotions occurred, the watermark did not advance, and awake context did not collapse. No manual coding-agent rescue or restart was performed. The controller needed bounded child cleanup after its abort command did not close the Pi process; that harness cleanup was not a work rescue.

### 20. Is ORCHID ready for another full A/B?

**No.** Stream continuity and exact ingestion are substantially improved, and scheduler work is bounded. The live compaction provider path still failed to produce repeated ACTIVE promotions or catch-up, while the real captured throughput ratio is below one. A six-hour A/B now would conflate the repaired gateway boundary with an unresolved canonicalizer/compaction-throughput blocker.

## Interpretation

The phase separated three issues that had been mixed together:

1. Provider stream continuity is now fail-open and recovered in deterministic tests and in the one observed live provider error.
2. Event ingestion was a genuine source of historical amplification and is now exact-suffix based.
3. Scheduler coalescing removes unbounded queued pressure, but the compaction model pipeline remains too slow or unreliable on real traffic to prove steady-state operation.

The provider-free `64K` result is useful for validating event boundaries, CAS, watermark, and provenance behavior. It must not be used as evidence that Solar/Qwen compaction can keep up. The provider-backed observation is the relevant operational rate, and it is below arrival.

## Limitations

- This is an exploratory single captured trace and one live preflight, not a replicated A/B benchmark.
- Goal-mode control-origin semantics were not enabled because the existing Pi RPC/provider boundary did not expose a reliable origin distinction; no synthetic follow-ups were sent.
- The live preflight stopped with one provider-started stream in flight, so it is not a clean successful completion sample.
- Semantic capsule correctness was not model-scored in the provider-free replay.
- The full test suite result was `142 passed, 1 skipped, 1 failed`. The sole failure is the known unrelated `test_openai_adapter.py::test_selector_and_canonicalizer_send_json_schema_response_formats`, which still expects the old static selector schema. The protocol-hardened dynamic per-chunk enum schema was preserved.

## Next recommendation

Before another full A/B, isolate and fix the canonicalizer timeout/throughput path, then rerun the short live preflight until it demonstrates at least three successful ACTIVE promotions, watermark advancement, bounded backlog, and context collapse. Do not add retrieval sophistication or use the provider-free structural rate as a substitute for that evidence.
