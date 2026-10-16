# Memory upgrade evaluation

Run `python -m memory_gateway.evaluation --output /tmp/orchid-replay.json`.
`--fixture path.json` accepts a sanitized JSON message array. The default
ten-generation fixture includes an early stable fact, an updated region, a
negative decision, and an absent-fact probe.

The replay exercises the actual HTTP gateway, snapshot worker, CAS promotion,
and production three-stage adapters with a local fake backend and deterministic
structured replies. It uses temporary databases and explicitly constructs
configuration without reading provider credentials from the environment.

Initial deterministic baseline (estimated tokens; not live model quality):

| Arm | Compaction calls | Input tokens | Output tokens | Probes passed |
| --- | ---: | ---: | ---: | ---: |
| Full context | 0 | 0 | 0 | 4/4 |
| Rolling summary | 10 | 15,055 | 130 | 4/4 |
| Current three-stage | 40 | 56,495 | 1,168 | 4/4 |

There are 14,831 estimated source tokens. The JSON report includes cost and
measured wall time per 1,000 source tokens, fixture hash, promotion/event counts,
and per-call measurements. Token estimates describe serialized fake-stage
payloads, excluding provider-specific hidden overhead; they are not provider
billing. Fake timing measures software execution, not GPU throughput. Probe
success establishes deterministic protocol behavior, not semantic quality of
an external model. Root-task continuity is interpreted as historical instructions
whose old factual values can be superseded by promoted state.

The default suite skips three historical experiment checks when their optional
ignored databases are absent. These are recorded-data checks, not the new replay
acceptance tests. Existing live-provider tests remain opt-in. Historical sealed
fixtures and benchmark thresholds have not been rewritten.

Phase 0 default-suite validation: 376 passed, seven optional checks skipped.
JSON/JSONL fixtures now use explicit LF checkout attributes so sealed byte
hashes do not depend on Git's global autocrlf setting.

Phase 1: 379 passed, seven optional checks skipped. Twelve turns of full-history
resends store exactly 24 events; identical assistant replies remain separate,
completion usage is preserved, and fragmented SSE tool arguments reconstruct.
Legacy envelope/SSE views are tested without changing their stored rows. The
three baseline arms retain 4/4 deterministic probe results. The full report is
`artifacts/memory_upgrade/phase-1.json`; ingestion does not change the memory
algorithm and establishes no live-model quality or cost improvement.

Phase 2: 382 passed, seven optional checks skipped. Thirty generated occurrence
and promotion sequences verify coverage and instruction/memory prefix stability.
Capacity tests include all uncovered events, tool overhead, and output reserve;
tool-call mates remain real protocol messages across coverage. The gateway
replay uses the new runtime layout; all baseline probes remain 4/4. See
`artifacts/memory_upgrade/phase-2.json`. Token budgeting remains an estimate;
provider-specific tokenization and live cache behavior are not verified.

Phase 3: 390 passed, seven optional checks skipped. The observer arm uses 10
calls / 1,153 estimated input / 415 output tokens, versus legacy 40 / 56,495 /
1,168: 49.00x fewer inputs on the repetition-heavy synthetic fixture, with
4/4 probes passing. Exact-range rejection, durable supersession, reflection
accounting, idle admission/cancellation, real structured-client telemetry, and
lost CAS races are tested. Report: `artifacts/memory_upgrade/phase-3.json`.
Live-provider non-inferiority, representative non-repetitive cost ratios, and
Python 3.11 execution remain unverified.

Phase 4: 396 passed, seven optional checks skipped. Raw lexical/range scoping,
privacy exclusion, append/index-failure recovery, fake embedding transport and
usage, dense-failure fallback, HTTP-backed MCP calls, and subprocess stdio are
tested. The amended fixture intentionally omits the deadline from observer
memory: unknown before raw recall, Friday after source retrieval. Observer cost
is 10 calls / 1,159 input / 415 output tokens, with a 48.76x input ratio against
the same-fixture legacy arm. Report: `artifacts/memory_upgrade/phase-4.json`.
Live embeddings, external GUI MCP clients, and live semantic quality remain
unverified. The fixture hash differs from phase 3 because the recall detail was
added to every baseline arm.

Final phase 5: Python 3.11.15 and 3.12 each pass 405 tests, seven optional checks
skipped. Input accounting now includes schemas/framing/fake system instructions:
legacy 40 calls / 65,722 input / 1,168 output; observer 10 / 5,300 / 415 (12.40x
lower input), with 4/4 probes and raw recall passing. The separate coding fixture
uses 14 / 7,533 / 603, passes 6/6 probes plus recall, and verifies file actions.
Oversized unique-source promotion/range recovery is tested. Earlier payload-only
figures remain historical. See `artifacts/memory_upgrade/phase-5.json` and
[the delivery report](memory-upgrade-delivery.md).
