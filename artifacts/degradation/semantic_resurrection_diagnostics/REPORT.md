# Semantic Resurrection Forensic Report

## Executive summary

Forensic analysis of the protocol-hardened continuation (generations 63–200) and the
immutable gen-50 first-failure artifact shows that **observed resurrection signals are
predominantly driven by three interacting mechanisms**, not selector protocol failure:

1. **Consolidator log synthesis (S4):** Gemini copies protected-tail padding and filler
   chatter into an accumulating `Additional Log` prose section instead of discarding or
   temporally marking historical generation references.

2. **Recursive base-capsule contamination (S6):** That log section is inherited on every
   incremental compaction (base_capsule_id chain) and grows at chunk compactions
   (`selected_reference_count=13`).

3. **Benchmark scorer substring collision (measurement artifact):** `_claimed_as_current()`
   flags obsolete **values** (e.g. `30`, `3`, `7`) appearing anywhere in the capsule without
   negation — including inside unrelated text such as `generation 30` or `through 7`.

**Critical finding for lease_seconds=30:** The most important operational fact was **not**
semantically resurrected. Generation-50 and generation-200 capsules both state
`Lease Duration: 900 seconds`. The persistent `lease_seconds=30` resurrection flag is
triggered by the substring `30` in padding-log generation references.

## Strongest supported root-cause hypothesis

**Primary:** S6 recursive contamination of consolidator-produced historical log prose +
S4 consolidator failure to garbage-collect or mark non-current generation references.

**Secondary:** Scorer design amplifies the signal via value-level substring matching
unrelated to fact keys.

**Confidence:** High for mechanisms above; medium for canonicalizer-specific contribution
(canonical text not persisted in telemetry).

## Stage attribution

| Stage | Finding | Confidence |
|-------|---------|------------|
| S0 | Authoritative history unambiguous | High |
| S1 | Selector retains current facts (zero selector_loss) | High |
| S2 | Temporal kind likely preserved in packet; canonical prose UNKNOWN | Low–medium |
| S3 | Lossless packet includes selected authoritative events | High |
| S4 | Consolidator emits correct current facts + growing historical log | High |
| S5 | Validators check structure/provenance only; no supersession semantics | High |
| S6 | Base capsule log section self-reinforces without re-selecting obsolete FACTs | High |

## lease_seconds=30

See [lease_seconds_trace.md](lease_seconds_trace.md).

- Genuine semantic resurrection of 30-second lease: **Not observed**
- Scorer flag cause: padding log `generation 30` substring
- Selector at gen 50: lease30=False, lease900=False

## compaction_generation

See [compaction_generation_trace.md](compaction_generation_trace.md).

Early values (3,4,5,7): substring collision.
Mid/recent values (30+): explicit log-reference accumulation.

## Base-capsule recursive contamination

**PROVEN (OBSERVED + DERIVED):**

- Incremental compactions (`selected_reference_count=1`) carry forward entire prior
  capsule including `Additional Log`.
- Example gen 88: consolidator `evidence_event_ids` empty in telemetry, no new
  resurrection additions — full resurrection set carried forward from base capsule.
- New resurrections at chunk compactions correlate with new padding lines added to log.

## Temporal information loss

**PROVEN at capsule layer:** Raw `[FACT kind=current]` supersession is not represented in
final prose. Padding lines become timeless narrative. **INFERRED at canonicalizer:** same,
based on consolidator input containing raw event content.

## Evidence sufficiency classification

| Case | Classification |
|------|----------------|
| lease_seconds @ gen 50 | SUFFICIENT AND CLEAR — consolidator knew current=900 |
| compaction_generation early | Scorer artifact more than semantic error |
| compaction_generation recent | SUFFICIENT BUT AMBIGUOUS — generation numbers without temporal tags |

## Natural cleanup

Rare residue healing observed (e.g. compaction_generation=8). No systematic removal of
obsolete generation references from log section. **natural_gc_observed: false**

## Capsule growth vs resurrection

| Gen | resurrection_count | capsule_chars | raw_tokens |
|-----|-------------------|---------------|------------|
| 50 | 8 | 770 | 13157 |
| 63 | 15 | 930 | 16428 |
| 100 | 33 | ~1400 | ~26000 |
| 200 | 93 | 3031 | 52416 |

**DERIVED:** ~70% of capsule growth (gen 63→200) is log-section expansion correlating with
resurrection count increases. Current durable facts remain present (zero current_fact_loss).

## What is proven

- lease_seconds=30 scorer flags are substring collisions, not 30-second lease assertions
- Recursive log accumulation in base capsule chain
- Consolidator states correct current operational facts
- Zero selector loss, zero current fact loss, zero protocol failures (hardened 63–200)

## What remains uncertain

- Exact canonicalizer phrasing per batch (only hashes persisted)
- Whether consolidator or canonicalizer first strips `kind=current` semantics
- Model replay would need consolidator `input_hash` from gen-50 job to inspect exact packet

## Narrowest plausible fix locations (NOT implemented)

1. **Scorer:** Fact-key-scoped resurrection (match `lease_seconds` only near lease context)
2. **Consolidator prompt/schema:** Drop or explicitly mark non-current generation references
3. **S6 mitigation:** Strip historical padding from base capsule input or segregate log appendix
4. **S5:** Semantic supersession validator on promoted capsules

## Artifacts

- `resurrection_timeline.jsonl`
- `generation_trace.jsonl`
- `lease_seconds_trace.md`
- `compaction_generation_trace.md`
- `SUMMARY.json`
