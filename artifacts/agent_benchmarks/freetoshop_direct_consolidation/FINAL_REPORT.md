# Phase 3.3 — Full-Trace Direct Consolidation and Semantic Sufficiency

## MEASURED FACTS

Frozen replay: 199 events, 317 source items, 202,761 planned source tokens; SHA-256 `6feb697961f22654d813159d07e2e519492b9ec431cdf8df08ae549508c26b16`. Reference arrival: 59.148 source tok/s.

### ARM B — selector → Solar

- Processing status: SUCCEEDED; strict full-trace coverage: **False**.
- Selector: 230 calls, 2115.24s, 318794 input tokens, 6089 output tokens.
- Selected/retired source: 198,390 / 202,761; omitted by selector: 4,371.
- Solar: 18 calls, 405044 input tokens, 26374 output tokens, $0.015316 estimated input/output cost.
- End-to-end wall: 2884.91s; selected-source throughput: 68.768 tok/s; planned-token equivalent: 70.283 tok/s.
- Direct Solar wall excluding selector: 769.44s.
- Timeouts/retries: 0 / 0.
- Max generated ACTIVE-equivalent capsule observed: 2885 estimated tokens.
- Frozen structural checks: 18/18; production validator/CAS: PROMOTED.

### ARM C — bounded raw → Solar

- Processing status: FAILED; strict full-trace coverage: **False**.
- First batch: 20.869s, 20056 provider input tokens, 1132 output tokens.
- Failure: `assistant content was not valid JSON`. No C capsule generation completed and no C semantic score exists.

## INTERPRETATION

ARM B demonstrates a structurally promotable direct-consolidation lineage and a rate above the captured 59.148 tok/s arrival, but it does not retire all planned source tokens and its 68.768 selected-source tok/s is below the 75 tok/s minimum gate. The selector consumed 2115.2s, approximately 73.3% of end-to-end wall time.

ARM C is not comparable economically or semantically because its first provider response was non-JSON. The earlier pilot speed therefore cannot authorize raw-to-Solar.

Frozen ARM B semantic checks: 131 PASS / 54 FAIL; current-fact losses 10, intent/blocker/continuation losses 39, resurrection failures 0, invention failures 0. These are deterministic coverage checks, not a claim that every failure is a human-judged semantic failure.

## EXPLICIT FINAL QUESTIONS

1. Did the exact ARM C second-batch request reproduce its timeout? **No.** One same-policy trial completed in 43.206s; the second completed provider generation in 97.789s but failed unknown-ID validation; payload hashes did not match the historical hash.

2. Was the failure deterministic or long-tail/transient? **Not input-deterministic; classify the prior timeout as an unreproduced provider-tail incident, with a separate nondeterministic protocol-output risk.**

3. Did ARM B complete all 202,761 planned source tokens? **No.** It processed the full raw trace through 230 selector calls but selected 198,390.

4. Did ARM C complete all 202,761 planned source tokens? **No; it failed on batch 0.**

5. ARM B full-trace retirement throughput: **68.768 selected-source tok/s** (70.283 planned-token equivalent).

6. ARM C full-trace retirement throughput: **0; no completed generation.**

7. ARM B Solar input: **405044 tokens**.

8. ARM C Solar input: **20056 tokens observed before first failure**; no full-trace total.

9. ARM B local selector work: **230 calls, 318794 input tokens, 6089 output tokens, 2115.24s**.

10. Provider-equivalent estimated cost: **ARM B $0.015316; ARM C $0.000738 partial only**. Local compute is not priced.

11. ARM B current-fact losses: **10 frozen-check failures**; ARM C N/A.

12. ARM C current-fact losses: **N/A; no capsule**.

13. Genuine resurrection failures: **ARM B 0; ARM C N/A**.

14. Invention failures: **ARM B 0; ARM C N/A**.

15. Current-intent/blocker/continuation losses: **ARM B 39; ARM C N/A**.

16. ACTIVE bloat: **No separate bloat failure was established; maximum B capsule was measured, but no fixed bloat threshold is in the frozen oracle.**

17. Provenance: **Exact for all 18 successful B promotions/checks; final validator and CAS promotion passed. C N/A.**

18. RETIRE behavior: **B emitted zero retire records in the observed generations, so no positive RETIRE sufficiency claim is possible; no invalid RETIRE record was accepted.**

19. Is canonicalization measurably necessary? **INSUFFICIENT_EVIDENCE.** B is structurally viable but semantically incomplete under the frozen checks; C did not complete.

20. Selector value: **It removed 4,371 estimated source tokens from direct Solar input, but required 2,115.24s local wall time; whole-trace savings versus C are not measurable because C failed.**

21. Is selector semantically required? **INSUFFICIENT_EVIDENCE.**

22. Is selector economically useful? **INSUFFICIENT_EVIDENCE.**

23. Better whole-system economics: **Not determined; ARM C has no full-trace result.**

24. Better operational reliability: **ARM B completed its selected lineage; ARM C failed batch 0. This is not enough to claim ARM B semantic production readiness.**

25. Simplest semantically sufficient architecture: **None demonstrated.**

26. Selected candidate exceeds 59.148 tok/s? **ARM B yes at 68.768; no candidate is authorized.**

27. Exceeds 75 tok/s? **No; ARM B 68.768, ARM C 0.**

28. Exceeds 90 tok/s? **No completed candidate.**

29. Live preflight authorized? **No.**

30. If run, did it achieve three promotions? **Not run; zero.**

31. Is ORCHID ready for another full Pi-vs-ORCHID A/B? **No.**

## DECISIONS

- CANONICALIZER: **INSUFFICIENT_EVIDENCE**
- SELECTOR: **INSUFFICIENT_EVIDENCE**
- PIPELINE: **NO_PRODUCTION_CANDIDATE**
- BENCHMARK READINESS: **NOT_READY_FOR_FULL_AB_RERUN**

## LIMITATIONS

The semantic oracle is a frozen deterministic content-coverage rubric. It is intentionally conservative and does not claim to be a complete independent human/model semantic judge. ARM C's first-batch provider protocol failure prevented a full comparison. No threshold, prompt, schema, or batch policy was changed after candidate execution began.

## VALIDATION

- Focused regression suite: **59 passed** (`tests/test_pipeline.py tests/test_model_telemetry.py tests/test_operability_hardening.py`).
- `python -m compileall -q memory_gateway tools tests`: **passed**.
- `git diff --check`: **passed**.
- Full `python -m pytest -q`: **one known failure** in `tests/test_openai_adapter.py::test_selector_and_canonicalizer_send_json_schema_response_formats`. The test still expects the old static selector schema; the implementation retains the protocol-hardened dynamic exact-ID enum schema. No schema weakening was made.

## NEXT RECOMMENDATION

**NOT_READY_FOR_FULL_AB_RERUN** — preserve this result, investigate provider-valid structured-output reliability for direct Solar, and do not run the six-hour A/B until a full-trace candidate passes the semantic and throughput gates.
