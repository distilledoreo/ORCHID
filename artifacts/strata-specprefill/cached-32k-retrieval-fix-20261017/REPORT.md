# Cached 32K SpecPrefill: retrieval-fix rerun

The same frozen 22-turn conversation now scores **41/44**, versus **39/44** before the retrieval fixes. Late recall improves from **4/8 to 6/8**. Two of the four original late misses recover; the original full-context cached control remains 44/44.

| Method | Correct fields | Late recall | Total time | Mean end-to-end TTFT | Compactions |
|---|---:|---:|---:|---:|---:|
| Cached full context (original control) | 44/44 | 8/8 | 174.3s | 7.26s | 0 |
| SpecPrefill before retrieval fixes | 39/44 | 4/8 | 296.9s | 12.81s | 1 |
| SpecPrefill with retrieval fixes | 41/44 | 6/8 | 299.9s | 12.95s | 1 |

## Changed answers

| Turn | Expected | Previous answer | New answer |
|---|---|---|---|
| 20 | [205464, 476472] | `205464,500609` | `205464,476472` |
| 21 | [436414, 265615] | `UNKNOWN,UNKNOWN` | `UNKNOWN,265615` |

## Remaining misses

- Turn 4: expected `[217379, 385680]`, got `217379,UNKNOWN`. Question: In config/component_0.py revision 2, return field_003() first. Second, follow its ROUTE_KEY to the earlier revision and return that linked field value.
- Turn 19: expected `[320624, 731401]`, got `320624,618409`. Question: Return field_071() from config/component_0.py revision 1, then field_063() from config/component_1.py revision 3. Use the exact revisions.
- Turn 21: expected `[436414, 265615]`, got `UNKNOWN,265615`. Question: Return field_071() from config/component_2.py revision 1, then field_063() from config/component_2.py revision 5. Use the exact revisions.

## Conditions and limits

Same case bytes, native executable hash, and target engine configuration as the earlier SpecPrefill run were verified. Both models use 32,768-token contexts; native prefix caching is enabled. The student uses 512-token prefill blocks and `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`. Expansion remains 2,048 tokens, summary output 1,024 tokens, and answer cap 64 tokens.

The rerun uses the current retrieval implementation: explicit source/revision/symbol pinning, direct hits before surrounding context, and atomic definition groups during student expansion. The existing student scoring and target selection path still applies. Gateway history-ledger changes are not exercised by this native session harness.

Total time includes student work, retrieval, compaction, native calls, and persistence. Loading and warmup are excluded. The full-context control and pre-fix results are retained earlier measurements, not rerun controls. This is one synthetic conversation and one new run; no statistical speed claim follows from the small timing difference.

All 22 turns completed; all native call input-plus-output lengths were within 32K. Case/expected-answer alignment and target configuration equality were checked. The native and student processes exited and GPU memory returned to zero.

Raw run and implementation snapshot: [/home/distilledoreo/.cache/orchid-three-way-28k/run-32k-spec-retrieval-fix-20261017](/home/distilledoreo/.cache/orchid-three-way-28k/run-32k-spec-retrieval-fix-20261017).

Case SHA256: `d745a202951fca47fbb1bea27fd97d6631dad0e6aeb7ad9e89b08678af319b98`.
