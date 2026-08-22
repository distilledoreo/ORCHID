# Fact-Key-Scoped Offline Rescore (Generations 1–200)

## Verdict

**no_genuine_semantic_resurrection**

Under fact-key-scoped scoring, **ORCHID never asserted an obsolete operational value as current** across all 200 promoted capsules in the frozen hardened corpus.

The prior `no_resurrection` failures were almost entirely measurement artifacts: value-only substring matching against padding/filler log text and numeric collisions (e.g. `3` inside `30 seconds`). Those mentions remain visible as **historical residue**.

## Comparison

- Generations rescored: **200**
- First legacy (value-only) resurrection failure: **4**
- First fact-key-scoped resurrection failure: **none**
- Legacy failure generations: **171** / 200
- Scoped failure generations: **0** / 200
- Gen-200 legacy resurrection count: **93**
- Gen-200 scoped resurrection count: **0**
- Gen-200 historical residue count: **96**
- Gen-200 current fact loss: **0**

## Branch decision

Per measurement protocol: **do not treat this as a supersession/consolidator failure.** Next target: **capsule hygiene** — prevent `Additional Log` / historical filler from recursively occupying the active working set. Residue should be tracked separately.

Do **not** run generation 201, tweak Gemini, or build long-term memory until capsule hygiene is addressed and a new benchmark measures growth without padding noise.

## Scorer changes (measurement only)

1. Fact-key assertion anchors (lease near `lease`, compaction near `Compaction Generation:`)
2. Historical log context exclusion (padding, filler, additional log)
3. Numeric word-boundary matching for digit values

## Artifacts

- `artifacts/degradation/fact_key_scoped_rescore/rescore_1_200.jsonl`
- `artifacts/degradation/fact_key_scoped_rescore/SUMMARY.json`
