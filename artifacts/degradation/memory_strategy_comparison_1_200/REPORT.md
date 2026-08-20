# Memory Strategy Comparison 1-200

Controlled three-arm comparison on the frozen ORCHID endurance corpus.

## Arms

- **A. full_raw** — complete authoritative raw history, no compaction
- **B. traditional_recursive_summary** — Gemini rolling summary + benchmark raw tail
- **C. orchid** — frozen chained residency shadow ACTIVE + benchmark raw tail

## Raw tail policy

- Target tokens: **80**
- Minimum tokens: **1**

## Gen-200 resident memory (awake totals, like-for-like)

- Full raw: **52416** tokens
- Traditional summary + tail: **216** (138 + 78)
- ORCHID ACTIVE + tail: **191** (113 + 78)

## Resident-token area (gens 1-200)

- Full raw: **5,252,523**
- Traditional: **42,660**
- ORCHID: **37,662**

## Semantic correctness at gen 200

- Traditional current fact loss: **True**
- ORCHID current fact loss: **False**
- Traditional resurrection: **0**
- ORCHID resurrection: **0**

## Awake recall

- **full_raw**: mean score 1.0, completed 3/10, skipped context limit 6
- **traditional_recursive_summary**: mean score 1.0, completed 9/10, skipped context limit 0
- **orchid**: mean score 0.925, completed 10/10, skipped context limit 0

## Notes

- ARM A is the semantic evidence ceiling; it is not expected to be token-efficient.
- ORCHID cold/raw information lives outside resident ACTIVE context by design.
- Low RETIRE count is not treated as under-retirement; see raw_only_audit.json.
