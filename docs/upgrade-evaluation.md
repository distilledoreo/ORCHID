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
