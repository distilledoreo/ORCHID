# Residency Shadow Sample Diagnostic Report

**Prompt version:** `residency_shadow_v1`
**Prompt bundle hash:** `124048d4f7475a2ec70141d8483026ee2d77817b93682ee38a65f0dcac89068c`

## Executive summary

- Sample generations succeeded: **14**
- Current fact loss: **0**
- Semantic resurrection (fact-key-scoped): **0**
- Invented state: **0**
- Original aggregate tokens: **4667**
- Shadow ACTIVE aggregate tokens: **1923**
- ACTIVE reduction: **58.8%**
- Residue: **429 -> 60**
- RETIRE entries (total): **3**
- RAW_ONLY entries (total): **38**
- Base-capsule log eviction observed: **True**
- Chained 1-200 replay recommended: **True**
- Confidence: **high**

## Sample methodology

Frozen protocol-hardened endurance corpus (`data/live_endurance_protocol_hardened.db`).
Each generation reconstructed independently from base capsule + selector refs + canonicalizer replay.
No chained shadow outputs; no production pipeline mutation.

| Gen | Selection reason |
|-----|------------------|
| 1 | early_clean_baseline_before_log_contamination |
| 12 | first_major_capsule_growth_promotion_policy_injection |
| 25 | required_checkpoint |
| 31 | first_residue_spike_and_legacy_scorer_failure_era |
| 41 | first_additional_log_style_growth_770_chars |
| 50 | required_checkpoint |
| 63 | protocol_hardened_continuation_start_chunk_compaction_13_refs |
| 75 | required_checkpoint |
| 82 | chunk_compaction_heavy_canonicalizer_batch |
| 100 | required_checkpoint |
| 125 | required_checkpoint |
| 150 | required_checkpoint |
| 175 | required_checkpoint |
| 200 | required_checkpoint |

## Per-generation table

| Gen | Orig tok | Shadow tok | Residue orig | Residue shadow | Fact loss | Resurrection | RETIRE | RAW_ONLY | Base log evicted |
|-----|----------|------------|--------------|----------------|-----------|--------------|--------|----------|------------------|
| 1 | 94 | 81 | 0 | 0 | False | 0 | 0 | 2 | False |
| 12 | 126 | 126 | 4 | 4 | False | 0 | 0 | 2 | False |
| 25 | 149 | 149 | 4 | 4 | False | 0 | 0 | 2 | False |
| 31 | 170 | 121 | 6 | 2 | False | 0 | 1 | 3 | False |
| 41 | 192 | 140 | 11 | 2 | False | 0 | 0 | 2 | True |
| 50 | 192 | 149 | 10 | 3 | False | 0 | 0 | 3 | True |
| 63 | 232 | 149 | 17 | 4 | False | 0 | 0 | 3 | True |
| 75 | 261 | 149 | 19 | 4 | False | 0 | 0 | 3 | True |
| 82 | 299 | 131 | 23 | 4 | False | 0 | 1 | 3 | True |
| 100 | 403 | 149 | 36 | 5 | False | 0 | 0 | 3 | True |
| 125 | 517 | 132 | 52 | 8 | False | 0 | 1 | 3 | True |
| 150 | 584 | 149 | 67 | 7 | False | 0 | 0 | 3 | True |
| 175 | 691 | 149 | 84 | 8 | False | 0 | 0 | 3 | True |
| 200 | 757 | 149 | 96 | 5 | False | 0 | 0 | 3 | True |

## ACTIVE size comparison by era

- Early (gens <=50): 923 -> 766 tokens
- Middle (51-125): 1712 -> 710 tokens
- Late (126-200): 2032 -> 447 tokens

Shadow ACTIVE plateaus around ~149 tokens in late generations while original capsules grow to 757 at gen-200.

## Additional Log analysis

For generations with Additional Log / padding residue in the original capsule, shadow classification tended to:
- move protected-tail padding and filler chatter to **RAW_ONLY**
- retain current operational facts in **ACTIVE**
- rarely use **RETIRE** (only 3 entries across 14 samples)

| Gen | Orig log markers | Shadow log markers | RAW_ONLY groups |
|-----|------------------|--------------------|-----------------|
| 1 | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 0} | 2 |
| 12 | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | 2 |
| 25 | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | 2 |
| 31 | {'protected_tail_padding': 1, 'filler_chatter': 0, 'additional_log_marker': 1, 'generation_bookkeeping': 1} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 0} | 3 |
| 41 | {'protected_tail_padding': 1, 'filler_chatter': 0, 'additional_log_marker': 1, 'generation_bookkeeping': 1} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 0} | 2 |
| 50 | {'protected_tail_padding': 1, 'filler_chatter': 0, 'additional_log_marker': 1, 'generation_bookkeeping': 1} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | 3 |
| 63 | {'protected_tail_padding': 1, 'filler_chatter': 0, 'additional_log_marker': 1, 'generation_bookkeeping': 1} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | 3 |
| 75 | {'protected_tail_padding': 1, 'filler_chatter': 1, 'additional_log_marker': 1, 'generation_bookkeeping': 2} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | 3 |
| 82 | {'protected_tail_padding': 1, 'filler_chatter': 1, 'additional_log_marker': 1, 'generation_bookkeeping': 2} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | 3 |
| 100 | {'protected_tail_padding': 1, 'filler_chatter': 1, 'additional_log_marker': 1, 'generation_bookkeeping': 2} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | 3 |
| 125 | {'protected_tail_padding': 1, 'filler_chatter': 1, 'additional_log_marker': 1, 'generation_bookkeeping': 2} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | 3 |
| 150 | {'protected_tail_padding': 1, 'filler_chatter': 1, 'additional_log_marker': 1, 'generation_bookkeeping': 2} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | 3 |
| 175 | {'protected_tail_padding': 1, 'filler_chatter': 1, 'additional_log_marker': 1, 'generation_bookkeeping': 2} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | 3 |
| 200 | {'protected_tail_padding': 1, 'filler_chatter': 1, 'additional_log_marker': 1, 'generation_bookkeeping': 2} | {'protected_tail_padding': 0, 'filler_chatter': 0, 'additional_log_marker': 0, 'generation_bookkeeping': 1} | 3 |

## Base-capsule cleaning

Generations where base capsule contained log residue and shadow ACTIVE evicted it:
- Observed at generations: [41, 50, 63, 75, 82, 100, 125, 150, 175, 200]

## RETIRE behavior (qualitative)

Very sparse RETIRE usage. When present, entries captured durable rationale rather than raw log excerpts.
No current facts were incorrectly retired (fact-key-scoped scorer: 0 loss).

## RAW_ONLY behavior (qualitative)

Primary sink for protected-tail padding, generation bookkeeping, and filler chatter.
Total RAW_ONLY groups: 38.

## Reconstruction notes

Canonicalizer output_hash replay matched for all samples.
Consolidator telemetry input_hash differed from reconstructed payload on all samples;
shadow calls used reconstructed payload with status CANONICALIZER_OUTPUT_VERIFIED.

## Failure cases

- None. All 14 shadow calls succeeded.

## Implications for capsule growth

Late-run original capsules accumulated historical/log prose via recursive base-capsule carry-forward.
Shadow ACTIVE classification materially flattens effective resident context (~59% aggregate token reduction)
without current-fact loss in this sample. A chained 1-200 replay is justified to test compounding hygiene.

## Interpretation (8 questions)

1. **ACTIVE vs nonresident:** Yes — distinguishes current state from padding/logs.
2. **Base-capsule cleaning:** Partial — strong residue reduction; some generation bookkeeping lingers in ACTIVE.
3. **Current facts preserved:** Yes — 0 fact loss across sample.
4. **RETIRE durable cold info:** Weak in sample — rarely used; most history went RAW_ONLY.
5. **RAW_ONLY absorbs padding:** Yes — primary destination for synthetic material.
6. **ACTIVE size early/mid/late:** See era table above; late shadow ACTIVE ~flat vs growing original.
7. **Flatter growth under classification:** Promising in sample; chained replay needed to confirm.
8. **Chained 1-200 replay justified:** Yes — safety criteria met; hygiene benefit large.

## Representative before/after examples

### Generation 200

**ORIGINAL CAPSULE** (excerpt):

```
Project: Orchid Memory Gateway. Owner: Disti. Timezone: America/New_York. Lease Duration: 900 seconds. Database Status: SQLite selected (Postgres rejected). Compaction Generation: Current (200). Promotion Policy: Promote READY descendants with compare-and-swap against the active capsule. Job Recovery Policy: Expired RUNNING jobs restart only if recover_expired_jobs is true. Event Model: Events are append-only; corrections add a new event. Preferences: Coffee order is black coffee; favorite color is teal. Lab Mascot: Okapi. Caching: A coworker mentioned Redis caching, but no decision was made. Additional Log: Protected-tail padding after generation 30 (pads 0 through 7), after generation 35 (pads 0 through 7), after generation 40 (pads 0 through 7), after generation 51 (pads 0 through 7), after generation 56 (pads 0 through 7), after generation 57 (pads 0 through 7), after generation 62 (pads 0 through 7), after generation 63 (pads 0 through 7), after generation 76 (pads 0 through 7), after generation 81 (pads 0 through 7), after generation 83 (pads 0 through 7), after generation 88 (pads 0 through 7), after generation 92 (pads 0 through 7), after generation 96 (pads 0 through 7), a
```

Original: 757 tokens, residue 96.

**SHADOW ACTIVE:**

```
Project: Orchid Memory Gateway. Owner: Disti. Timezone: America/New_York. Lease Duration: 900 seconds. Database Status: SQLite selected (Postgres rejected). Compaction Generation: Current (200). Promotion Policy: Promote READY descendants with compare-and-swap against the active capsule. Job Recovery Policy: Expired RUNNING jobs restart only if recover_expired_jobs is true. Event Model: Events are append-only; corrections add a new event. Preferences: Coffee order is black coffee; favorite color is teal. Lab Mascot: Okapi. Caching: A coworker mentioned Redis caching, but no decision was made.
```

Shadow: 149 tokens, residue 5.

**RETIRE (0 entries):**
**RAW_ONLY (3 groups):**
- Historical compaction log prose and filler chatter accumulated in base_capsule
- Protected-tail padding after generation 199 (pads 0 through 7)
- Filler chatter notes 0 through 3 for compaction generation 200

### Generation 31

**ORIGINAL CAPSULE** (excerpt):

```
Project: Orchid Memory Gateway. Owner: Disti. Timezone: America/New_York. Lease Duration: 900 seconds. Database Status: SQLite selected (Postgres rejected). Compaction Generation: Current (30). Promotion Policy: Promote READY descendants with compare-and-swap against the active capsule. Job Recovery Policy: Expired RUNNING jobs restart only if recover_expired_jobs is true. Event Model: Events are append-only; corrections add a new event. Preferences: Coffee order is oat latte; favorite color is teal. Lab Mascot: Okapi. Caching: A coworker mentioned Redis caching, but no decision was made. Additional Log: Protected-tail padding after generation 30 (pads 0 through 7) recorded.
```

Original: 170 tokens, residue 6.

**SHADOW ACTIVE:**

```
Project: Orchid Memory Gateway. Owner: Disti. Timezone: America/New_York. Lease Duration: 900 seconds. Database Status: SQLite selected (Postgres rejected). Promotion Policy: Promote READY descendants with compare-and-swap against the active capsule. Job Recovery Policy: Expired RUNNING jobs restart only if recover_expired_jobs is true. Event Model: Events are append-only; corrections add a new event. Preferences: Coffee order is oat latte; favorite color is teal. Lab Mascot: Okapi.
```

Shadow: 121 tokens, residue 2.

**RETIRE (1 entries):**
- A coworker mentioned Redis caching, but no decision was made.
**RAW_ONLY (3 groups):**
- Protected-tail padding entries after generation 30 (pads 0-7).
- Mechanical compaction generation marker update to 31.
- Filler chatter notes 0 through 3 for compaction generation 31.

### Generation 1

**ORIGINAL CAPSULE** (excerpt):

```
Project: Orchid Memory Gateway
Owner: Disti
Timezone: America/New_York
Lease Duration: 30 seconds
Database Status: Undecided
Compaction Generation: Current (1)
Job Recovery Policy: Expired RUNNING jobs restart only if recover_expired_jobs is true.
Event Model: Events are append-only; corrections add a new event.
Preferences: Coffee order is oat latte; favorite color is teal.
```

Original: 94 tokens, residue 0.

**SHADOW ACTIVE:**

```
Project: Orchid Memory Gateway
Owner: Disti
Timezone: America/New_York
Lease Duration: 30 seconds
Database Status: Undecided
Coffee Order: Oat Latte
Favorite Color: Teal
Event Model Policy: Events are append-only; corrections add a new event.
Recovery Policy: Expired RUNNING jobs restart only if recover_expired_jobs is true.
```

Shadow: 81 tokens, residue 0.

**RETIRE (0 entries):**
**RAW_ONLY (2 groups):**
- Compaction generation 1 metadata bookkeeping marker.
- Filler chatter regarding Redis caching without any decision.