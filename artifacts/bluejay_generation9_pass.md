# Bluejay Generation 9 — PASS

Date: 2026-08-19

- Job `job_5e7d81a9840f43429f256436f2c75eec` promoted generation 9.
- Base capsule: `cap_9900875566ad474dba66be920be4abaa`.
- Descendant: `cap_990cd1d1794b48c1be9fac6c865ad567` (`ACTIVE`); prior capsule is `SUPERSEDED`.
- Snapshot coverage: sequences 15–34.
- Pipeline: selector 56/56, canonicalizer 7/7, consolidator 1/1.
- Consolidator telemetry: 115,681 input tokens, 178 output tokens, 0 reasoning tokens, 32,088 ms; strict schema and provenance passed.
- Capsule hashes, source hashes, deterministic validation, lease ownership, and CAS promotion passed.
- Generation 7 remained parked and no later compaction job was created.
- Raw-tail boundary passed: Bluejay-state events from sequences 15–32 were absent from the tail before recall.
- Solar capsule-only recall returned all seven required updated Bluejay facts.

This public repository contains sanitized equivalents of the database, preflight artifact, and generation-9 telemetry. Raw prompts, raw event content, local paths, credentials, and provider identities were removed. The original successful specimens remain preserved outside the public repository.
