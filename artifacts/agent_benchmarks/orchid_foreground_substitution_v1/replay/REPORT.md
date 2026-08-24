# Replay report

Source: `artifacts/agent_benchmarks/freetoshop_pi_vs_orchid_corrected_all_solar/orchid/telemetry.jsonl`

Method: convert Pi `message_end` payloads to OpenAI messages, ingest into a fresh journal, apply recorded ACTIVE watermarks at `finished_at` when the reconstructed sequence exists, assemble each `turn_start`.

## Results (57 reconstructed provider turns)

| | Additive (old) | Substitutive (new) |
|---|---:|---:|
| Mean foreground tokens | 144630.5 | 41945.6 |
| Cumulative foreground tokens | 8243937 | 2390898 |

Historical live ORCHID cumulative provider input: **7864313** (additive bug). Historical Pi: **1811513**. Those live numbers are not rewritten.

## Collapse immediately after promotions 1–3

| Gen | Pre (new) | Post (new) | Post (old additive) | Collapse vs old | Collapse vs pre |
|---:|---:|---:|---:|---:|---:|
| 1 | 126759 | 93229 | 143851 | 35.19% | 26.45% |
| 2 | 109675 | 36637 | 167613 | 78.14% | 66.59% |
| 3 | 39126 | 6959 | 173415 | 95.99% | 82.21% |

Promotions 4–5 were **not** applied: reconstructed journal has 131 events; live watermarks end at sequences 169 and 278 (gateway ingested extra copies not present as Pi `message_end`).

## Offline gate

`non_protocol_covered_raw_tokens_forwarded == 0` on every post-ACTIVE replayed request.

`covered_raw_tokens_forwarded` may be > 0; it equals `protocol_continuity_tokens` only (split tool loops).

## Cache (observation only)

ACTIVE prefix changes at each generation. Uncovered tail and current suffix still grow. No cache redesign.
