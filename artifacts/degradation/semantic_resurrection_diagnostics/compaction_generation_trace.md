# compaction_generation Resurrection — Representative Analysis

## Mechanism overview

Each generation injects `[FACT id=compaction_generation kind=current] N`. Prior values
become superseded. The scorer flags any superseded generation number appearing in the
capsule without negation words in a ±90 character window.

Two distinct sub-mechanisms produce resurrection signals:

### A. Substring collision (early values 1–9)

**OBSERVED at gen 50:** Resurrections for compaction_generation=3,4,5,7 while capsule
states `Compaction Generation: Current (50)`.

**DERIVED:** Values 3,4,5,7 appear as substrings inside padding log text
(`generation 30`, `generation 35`, `generation 40`, `pads 0 through 7`) — not as
declared current compaction generation.

### B. Log-reference accumulation (mid/recent values)

**OBSERVED at gen 200:** Resurrections include compaction_generation=30,35,40,51,56,57,62,63,67,76,77,81,...
Capsule correctly states `Compaction Generation: Current (200)` but `Additional Log` lists
dozens of `Protected-tail padding after generation X` and `Filler chatter for compaction generation Y`.

**DERIVED:** Each chunk compaction (selected_reference_count=13) surfaces a batch of
historical padding/filler events; consolidator appends them to the log section; base capsule
carries the growing log forward on incremental compactions (selected_reference_count=1).

## Representative traces

### Early obsolete value: compaction_generation=3

- First bad generation: 50
- Trigger: numeric_substring_collision in padding log (generation 30/35/40)
- Genuine current-state assertion: **No**

### Mid obsolete value: compaction_generation=50

- First bad generation: UNKNOWN
- At gen 50, value 50 is CURRENT — not yet resurrected
- Becomes superseded at gen 51
- Reappears in scorer when log references generation 50 or substring collisions

### Recent obsolete value: compaction_generation=190

- First bad generation: UNKNOWN
- Persists via log accumulation after generation 190 padding injected
- Mechanism same as mid-run: S6 + S4, not selector re-preference for obsolete FACT

## Age-dependent mechanism difference

| Age bucket | Primary mechanism |
|------------|-------------------|
| 1–9 | Substring collision inside multi-digit generation numbers and `through 7` |
| 10–49 | Mix of log references and substring collision |
| 50+ | Explicit padding/filler log lines naming generation N |

## Selector at chunk compactions

When `selected_reference_count=13`, selector surfaces padding events from many prior
generations simultaneously. Both obsolete compaction_generation FACT lines and padding
lines enter the packet. This is **both obsolete and current evidence** (category c).

## Temporal semantics

Raw events carry `kind=current` on each compaction_generation FACT — software knows each
is superseded by replay. Canonical/consolidated prose loses per-fact temporal status;
padding lines read as timeless log entries.

## Evidence sufficiency

**SUFFICIENT BUT AMBIGUOUS** for mid/recent values: consolidator receives explicit
generation numbers in padding text without machine-readable supersession markers in the
capsule output format.

## Natural garbage collection

**OBSERVED:** Rare partial cleanup — e.g. compaction_generation=8 residue healed at gen 69.
No systematic GC of obsolete generation references from the log section.

## Resurrection growth

Resurrection count rises from 15 @ gen 63 to 93 @ gen 200, correlating with:
- Growing `Additional Log` section (capsule_chars 930 → 3031)
- Each +1 resurrection often coincides with chunk compaction adding a new log reference

Correlation ≠ causation, but mechanism is supported by gen-50→200 log growth in milestones.
