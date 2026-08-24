# Live foreground substitution preflight

Status: **FAIL** (wall_clock_limit)

## Comparison chain

- Mean inbound Pi transcript estimate: 89750.2
- Mean assembled ORCHID estimate: 48785.4
- Mean Solar `input_tokens`: 46417.7
- Mean assembled/provider ratio: 0.9404

## Gate

- `promotions_ge_3`: NO
- `post_promotion_turns_ge_5`: NO
- `non_protocol_covered_raw_forwarded_zero`: yes
- `idle_continuation_used_prompt`: yes
- `no_controller_stall`: yes
- `no_manual_intervention`: yes
- `provider_tokens_present`: yes
- `assembled_tracks_provider`: yes
- `material_collapse_after_promotion`: yes

## Collapse at ACTIVE changes

- active_tokens=54: inbound 82595 → assembled 26304 / provider 29703 (68.15% vs inbound)
