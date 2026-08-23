# FreetoShop Pi-vs-ORCHID All-Solar Architecture Feasibility A/B

Status: **INCOMPLETE_POST_STOP**. Frozen start commit: `db24908e582c1642a001abd2feca4ec47b1947f8`. Public goal SHA256: `d789f01c840878946c1cd885bcd48dfb4f96b4db2eafb82615dfdd4064290e7e`. All semantic roles used `upstage/solar-pro4` via OpenRouter; no local Qwen was used.

## Direct measured result

| Arm | Assistant messages | Tool calls | Pi compactions / ORCHID promotions | Foreground input/output | Typecheck | Build |
|---|---:|---:|---:|---:|---:|---:|
| Pi native | 67 | 142 | 0 | 3000735 / 115427 | True | True |
| ORCHID all-Solar | 94 | 117 | 1 | 15965998 / 46882 | False | False |

Both arms launched once and ran without controller rescue or restart. The controller was stopped after four ORCHID compaction failures and a new generation remained running; this is recorded in `RUN_TERMINATION.json`. Pi never reached native compaction. ORCHID reached one ACTIVE promotion, not multiple promotions.

ORCHID did not demonstrate retirement throughput exceeding arrival throughput or backlog catch-up. At stop, the database retained a running generation and the active capsule had not advanced beyond its first promotion; therefore retirement ratio, catch-up time, repeated post-promotion collapse, and context-token savings after repeated promotion are **not established**. The active capsule was about 211 estimated tokens while the raw tail was about 15,951 estimated tokens at the last checkpoint.

## Failure evidence

- Generation 2: canonicalizer rejected source references out of supplied order.
- Generation 3: consolidator rejected an unknown evidence event ID.
- Generation 4: Solar Pro rejected a 547,802-token request against its 524,288-token maximum context.
- Generation 5: canonicalizer again rejected source references out of supplied order.
- Generation 6 was running when the controller/gateway were stopped; its incomplete state is preserved.

These are operational/protocol failures, not semantic-oracle passes. No retries or fuzzy provenance repair were added.

## Quality and hidden validation

Hidden Clone Stamp probe: **arm_a_pi exit=1; arm_b_orchid exit=1**. Both exits were 1. Pi timed out waiting for the Clone stamp button; ORCHID found the button but failed the required `Alt/Option-click to set clone source` text. Neither arm passed the browser workflow. Pi produced a buildable tree in the independent checks; ORCHID failed typecheck/build on missing `cloneStampStroke`, `createCloneSource`, `CloneSource`, and task-bar symbols. Neither final repository earns a full functional-quality claim.

The observed work was not a fair post-compaction quality comparison: Pi had zero native compactions and ORCHID had only one promotion before repeated failures. Coding degradation after a memory transition is therefore **not isolatable** for either arm. The all-Solar ORCHID path nevertheless failed the required operational sustainability test.

## Economics and context area

Observed foreground token totals are recorded above. ORCHID memory-stage aggregates are in `economics/SUMMARY.json` and include selector, canonicalizer, and consolidator calls/tokens/wall time. A causal foreground context-token-area saving cannot be claimed because the arms diverged before comparable repeated promotions; the measured proxy is the sum of foreground input tokens only.

## Required answers

1. Both arms ran continuously without rescue until the predeclared stop: **yes**.
2. Pi native compaction: **no; 0**.
3. ORCHID multiple ACTIVE promotions: **no; 1 total**.
4. ORCHID retirement exceeded arrival: **not demonstrated**.
5. ORCHID backlog bounded/caught up: **no evidence of catch-up; one running stale generation remained**.
6. ORCHID awake context repeatedly collapsed: **not reached**.
7. ORCHID promotion degradation: **not isolatable after one promotion; later operational failures occurred**.
8. Pi post-native-compaction degradation: **not testable; no native compaction**.
9. More task progress: **Pi had the stronger validated outcome because it typechecked/built; ORCHID changed more files but failed validation**.
10. Architecture constraints/current intent/blockers: **not proven across memory transitions; only the initial ORCHID capsule preserved them**.
11. Better final repository: **Pi for build validity; neither passed the hidden browser workflow**.
12. Compile: **Pi yes; ORCHID no**.
13. Clone Stamp browser workflow: **neither**.
14. Hidden evaluator: **both exit 1; full diagnostics in `quality/hidden_eval.json`**.
15. Foreground usage and ORCHID extra cost: **measured in `economics/SUMMARY.json`; selector/canonicalizer/consolidator records are separated by stage**.
16. Foreground context-token area saved: **not causally measurable in this incomplete, non-comparable transition run**.
17. Solar selection coherent: **not fully; exact ordering/provenance failures were observed**.
18. Architecture feasibility independent of RX580: **not supported; this all-Solar path was operationally weak before local hardware became relevant**.

## Verdict

**ARCHITECTURE_OPERATIONALLY_WEAK**

RX580_LOCAL_SELECTOR_OPTIMIZATION: **NOT_REQUIRED_FOR_FEASIBILITY**
