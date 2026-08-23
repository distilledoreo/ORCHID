# All-Solar benchmark forensics

## Findings

1. The canonicalizer **was invoked**: 61 persisted model runs. The all-Solar
manifest explicitly mapped Solar to the legacy canonicalizer role, and the
legacy engine made the call unconditional. This was not the intended
selector → consolidator architecture.
2. The 547,802-token request was a generation-4 **consolidator** input. It
processed 93 raw events / 144,623 stored source tokens / 245 source items.
The request duplicated selected evidence and included the canonicalizer
aggregate.
3. There was no source-window bound. The worker attempted the entire
backlog after the active watermark. Pi did not hit the limit because its
foreground native context policy did not construct this separate duplicated
compaction payload.
4. Ingestion was not the cause: 213/213 event IDs and content hashes were
unique, with contiguous per-request append suffixes.
5. The unknown provenance ID was the previous ACTIVE watermark, outside the
current exact legal source universe. The validator correctly rejected it; no
fuzzy repair was added.

## Minimal correction

- `ORCHID_CANONICALIZER_ENABLED` defaults to true for legacy compatibility, but
the selected path can disable the obsolete model stage and mechanically carry
the software-owned authoritative packet to the consolidator.
- Scheduler-created snapshots now use a provider-aware bounded source window.
The captured Solar configuration derives **119,466** source-envelope
tokens from context, output reserve, fixed overhead, safety margin, and
request amplification.
- The worker, exact-ID validator, CAS promotion, and dirty/coalescing behavior
remain unchanged.

## Required answers

| Question | Answer |
|---|---|
| Canonicalizer invoked? | Yes, 61 model runs. |
| Intended? | No for the selected architecture; yes only according to the legacy all-stage benchmark config. |
| Actual pipeline? | Unbounded snapshot → selector → canonicalizer → packet → consolidator → validator/promotion. |
| Intended pipeline? | Bounded snapshot → selector → software packet → consolidator → validator/promotion. |
| Did benchmark test intended architecture? | No. |
| Overflow role? | Consolidator input. |
| Source tokens? | 144,623 stored raw tokens; 245 selected source items. |
| Duplicate ingestion? | No observed duplicate IDs or content hashes. |
| Bounding present? | No in failed implementation. |
| Why overflow? | Whole backlog plus duplicated evidence and canonicalizer aggregate exceeded provider context. |
| Pi difference? | Pi’s native foreground path did not build this background compaction request. |
| Inherent to ORCHID? | No; it was an implementation/configuration mismatch plus missing bound. |
| Unknown ID cause? | Provider returned prior ACTIVE watermark, not in current legal source universe. |
| Fuzzy correction? | No. |
| Canonicalization required? | `CANONICALIZER_BYPASSED` for the selected selector → consolidator candidate; legacy compatibility remains opt-in/default. |
| Minimal change? | Explicit canonicalizer bypass seam and bounded scheduler snapshot. |
| Safe bound? | 119,466 source-envelope tokens for captured Solar config. |
| Five clean promotions? | Yes in offline structural replay. |
| Requests below provider limit? | Yes by repository estimator. |
| Exact watermark progression? | Yes. |
| New live A/B justified? | Conditionally yes: the replay gate passed; do not launch it automatically in this phase. |

## Interpretation

**MIXED_IMPLEMENTATION_FAILURES**. The benchmark exercised an unintended
canonicalizer stage and also lacked a bounded source-window invariant. The
captured-history replay demonstrates repeated clean local promotions after
both corrections, but it does not establish all-Solar semantic quality. A new
live A/B is conditionally justified by the replay gate and should use the
corrected configuration; it was not run in this phase.

**CANONICALIZER_BYPASSED**

**MIXED_IMPLEMENTATION_FAILURES**
