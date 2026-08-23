# Role map: failed FreetoShop all-Solar run

Historical benchmark manifest: `ced513a794ad0eba51dc87e853ef0d62ac65ac83da78fb0ce0efb70c3014bfc1`
Historical source DB: `9dac3410930e57f69bff4253f913f7ff4756c820c2e63b44fcb441913f539746`
Historical repository commit: `3647e70ab4e5d18e4e9c5107075a2b8d179835d3`
Current forensic code commit: `1f25ec9c145a4f114827ff1608c9ebc23db837aa`

| Role | Expected model | Actual model | Actual code path | Was invoked? | Why? |
|---|---|---|---|---|---|
| Foreground | `upstage/solar-pro4` | `upstage/solar-pro4` | sealed Pi harness → ORCHID `/v1/chat/completions` → OpenRouter | yes | all-Solar arm config |
| Selector | `upstage/solar-pro4` | `upstage/solar-pro4` | scheduler → `CompactionWorker` → `LosslessCompactionEngine` → `OpenAICompatSelectorEngine` | yes (791 recorded model runs) | configured in `compaction_model_roles` |
| Canonicalizer | absent in selected architecture | `upstage/solar-pro4` | selector → `OpenAICompatCanonicalizerEngine` → `build_lossless_packet` | yes (61 recorded model runs) | legacy engine unconditionally constructed/invoked it |
| Consolidator | `upstage/solar-pro4` | `upstage/solar-pro4` | packet → `OpenAICompatConsolidatorEngine` → validator → CAS promotion | yes (3 recorded model runs) | configured in all-Solar arm |

## Conclusion

The benchmark explicitly assigned Solar to every legacy semantic role, and the
legacy `build_lossless_engine()` path required the canonicalizer. The run was
therefore not selector → consolidator only. The intended candidate path is:

`bounded frozen source window → selector → software-owned authoritative packet → consolidator → validation → ACTIVE promotion`.
