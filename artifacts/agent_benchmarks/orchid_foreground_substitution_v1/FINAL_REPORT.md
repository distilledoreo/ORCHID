# FINAL REPORT — ORCHID foreground substitution v1

## What was wrong

Previous `ContextAssembler.assemble` copied the full Pi `request_messages` and prepended ACTIVE + raw tail. Compaction rewrote the journal; Solar still saw the entire transcript. That is why ORCHID mean foreground was ~137970 vs Pi ~42128 (3.3×) and cumulative input ~7.86M vs ~1.81M (4.3×). Those figures are evidence of the **additive** bug, not intended ORCHID economics.

Independently, the sealed driver sent `follow_up` on `agent_settled`. Pi only **queues** `follow_up`; an idle agent needs `prompt` to start a turn.

## What changed

- `memory_gateway/context.py`: after ACTIVE, suppress inbound messages covered by `covered_end_event_id` sequence; tail is strictly newer; 12k floor cannot re-enter compacted history; protocol tool pairing kept; `SubstitutionReport` on each snapshot.
- `tools/pi_continuation_policy.mjs` + sealed driver: idle → `prompt`, busy → `follow_up`, watchdog without rescue spam.
- Tests: `tests/test_foreground_substitution.py`, `tests/test_pi_continuation_policy.py`. Cold fail-open assertion updated so a fully covered sole event is not required in the tail.

Selector, consolidator, ingestion suffix identity, CAS promotion, and retrieval were not redesigned.

## Replay

Mean foreground 144630.5 → 41945.6. Cumulative 8243937 → 2390898 on 57 reconstructed turns. Post-promotion collapse vs additive: 35%, 78%, 96% after gens 1–3. Gens 4–5 not replayable from Pi `message_end` alone.

## Live

Not run. No six-hour A/B.

## Answers

1. Yes — full Pi history plus ORCHID capsule/tail.
2. Yes, after ACTIVE, except documented protocol pairing.
3. Exact `message_hash` + ordered prefix/suffix overlap + `event.sequence <= covered_sequence`.
4. Yes — `_tail(..., after_sequence=covered_sequence)`.
5. No. After ACTIVE, `apply_minimum` is false / window is already uncovered-only.
6. Yes, via `_protocol_continuity_indices`.
7. Yes; unaligned suffix user text is kept.
8. Replay vs old additive immediately after gens 1–3: 35.19%, 78.14%, 95.99%.
9. Replay new mean: 41945.6 estimated tokens (not live Solar).
10. Replay cumulative new: 2390898 vs old additive 8243937 vs historical live ORCHID 7864313.
11. Live collapse: not measured.
12. Provider tokens: not measured live.
13. Live ≥3 promotions: no.
14. Live continuation after promotion: unknown.
15. Idle `follow_up` stall: reproduced from the prior sealed log, not re-run live.
16. Policy/driver now send `prompt` when idle.
17. Busy continuation still `follow_up`.
18. Live: not shown. Unit watchdog classifies missing `message_start`.
19. Another full Pi-vs-ORCHID A/B is **not** justified until a short live preflight shows collapse and turn starts.

INSUFFICIENT_EVIDENCE
