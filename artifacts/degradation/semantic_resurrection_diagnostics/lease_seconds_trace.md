# lease_seconds=30 Forensic Trace

## Executive finding

**OBSERVED:** At generation 50 (first semantic failure), the promoted capsule states
`Lease Duration: 900 seconds` — the authoritative current value. The benchmark scorer
still flags `superseded current lease_seconds=30`.

**DERIVED:** The scorer's `_claimed_as_current()` uses naive substring windows on value
`30`. The consolidator's `Additional Log` section quotes protected-tail padding lines such as
`Protected-tail padding after generation 30`, which contain `30` without negation markers.

**INFERRED:** This is not evidence that the consolidator reasserted lease_seconds=30 as an
operational fact. No promoted capsule through generation 200 contains an explicit
`30 second` lease assertion separate from padding-generation references.

## Authoritative event history

| Event | Generation introduced | Content |
|-------|----------------------|---------|
| `evt_f5eaf7738487420697dc1786aeae0b91` | 1 | `[FACT id=lease_seconds kind=current] 30` |
| `evt_6534adfb861e4d83a6bc8b596078c9c0` | 5 | `[FACT id=lease_seconds kind=current] 900` |

Supersession: generation 5 (script_for_generation).

## Generation 50 (first bad scorer flag)

- Capsule ID: `cap_29b8e430ff974e538ee3cc9fdacf87ee`
- Base capsule ID: `cap_e63f9cc04ea84e049db75e22805c5202`
- Job ID: `job_38963666c7ee44bbbaf3be3c0ec1b6f0`
- Selector selected lease_seconds=30 event: **False**
- Selector selected lease_seconds=900 event: **False**
- Capsule lease line: `Lease Duration: 900 seconds` (OBSERVED)
- Scorer trigger window: `Protected-tail padding after generation 30` (OBSERVED)

## Persistence mechanism (generations 50–200)

1. **S6 — Recursive log accumulation:** Each promoted capsule inherits the `Additional Log`
   section from its base capsule. Chunk compactions (`selected_reference_count=13`) append
   more padding/filler generation references.
2. **S4 — Consolidator synthesis:** Gemini consolidates padding events into prose rather than
   dropping or temporally marking them.
3. **Scorer collision:** Value `30` in padding text re-triggers `lease_seconds=30` every generation.

## Selector behavior

- Gen 50 selector refs: 13 events
- Lease obsolete event re-selected at gen 50: False
- Current lease event re-selected at gen 50: False

## Evidence sufficiency (gen 50)

**SUFFICIENT AND CLEAR** for consolidator: packet includes authoritative lease_seconds=900;
capsule output correctly states 900 seconds. The `lease_seconds=30` resurrection flag is a
**scoring artifact**, not consolidator confusion about current lease.

## Stage attribution

| Stage | Role |
|-------|------|
| S0 | Unambiguous authoritative history |
| S1 | Selector not primary failure for lease |
| S2 | UNKNOWN (canonical text not persisted) |
| S3 | Lossless packet preserves both events when selected |
| S4 | Consolidator correctly states 900s; also copies padding log |
| S5 | No semantic supersession validator |
| S6 | Padding log in base capsule perpetuates substring collision |

## Natural cleanup

**OBSERVED:** `lease_seconds=30` scorer flag never disappears generations 50–200 because
padding log references to generation 30 persist and grow.

## Model replay

Not required for lease_seconds mechanism. Frozen telemetry proves substring collision at gen 50.
