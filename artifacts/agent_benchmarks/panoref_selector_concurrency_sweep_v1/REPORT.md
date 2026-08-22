# PanoRef 1.2K selector concurrency sweep

## Scope and freeze

This phase varied only selector worker count on the frozen PanoRef
selector -> Solar control. Selector target remained 1,200 estimated source
tokens. Solar remained serial and unchanged. No canonicalizer, prompt,
schema, model, replay ordering, oracle, timeout policy, retry, or tuning
change was introduced.

- Run commit: `b503057194eaac867122ca261499dc8be5fed9c9`
- Replay SHA256: `aa0e7326c2514501a8c53a5a2c4669b1512383507989e71403d4e6f00f7eaf07`
- Oracle manifest SHA256: `88e37b2803c028bbc50b0ebaf6a1ed62c639a9776a50d4b400b7b14f0a6ef323`
- Oracle checks SHA256: `88d38ad16471cb13eb23ae75233e9a3a5c59aa9ddec714f1ca63c4739a59980`
- Frozen raw-plan SHA256: `5f8960bf2f643742c5b26bf26da9deda485d69e76a7bf079362b76538473766b`
- Immutable source universe: 276 events, 346 source items, 143,670 source tokens
- Selector chunk-plan SHA256: `f700f52fa66feb88a4544d0e7711c3d0b67a883b67d16bac8b16127a109d14ef`
- Selector: `qwen3.5-4b@q6_k`, local endpoint, 180 s timeout
- Solar: `upstage/solar-pro4`, unchanged recursive direct consolidation

Per-arm benchmark configuration hashes are recorded in each
`RUN_METADATA.json`: 1x `db1133f4…`, 2x `e380f050…`, 3x `b174e61e…`.

## Result

| Workers | Chunk target | Calls | Selector makespan | Request wall sum | End-to-end wall | Retired tok/s | Selected % | Oracle P/F | Timeouts |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1x | 1,200 | 148 | 1,806.5 s | 1,806.5 s | 2,140.5 s | 64.5 | 96.2% | 21/45 | 0 |
| 2x | 1,200 | 148 | 1,800.1 s | 3,594.0 s | 2,133.8 s | 64.7 | 96.2% | 38/28 | 0 |
| 3x | 1,200 | 148 | 1,800.5 s | 5,362.9 s | 2,239.3 s | 61.7 | 96.2% | 36/30 | 0 |

All arms completed 12/12 Solar generations. Selector request p50/p95 latency
(linear interpolation over per-request wall samples) was 11.04/19.55 s at
1x, 22.11/35.94 s at 2x, and 35.13/51.18 s at 3x.
Selector input/output was identical in every arm: 261,050 / 5,159 tokens.
Selected source tokens were 138,149; omitted source tokens were 5,521.
There were zero selector errors, provider timeouts, transport retries,
resurrection failures, or invention failures in every completed arm.

The makespan curve is effectively flat while aggregate request work scales
with worker count. Relative to fresh 1x, 2x reduced selector makespan by
6.4 s (0.35%) and end-to-end wall by 6.7 s (0.31%); 3x increased end-to-end
wall by 98.8 s (4.6%). The 2x/3x request-wall sums show that the local
backend serialized or heavily contended concurrent generations rather than
halving selector work.

## Selector identity and semantic behavior

The selector decisions were exactly preserved:

- all three arms have the same per-chunk input hashes;
- all three arms have the same per-chunk raw response hashes;
- all three arms have selector output hash
  `f415058bd7f961e95be0a2f0b333e5eadcdd1b0272e530783fb43d90a4241386`;
- all three arms have Solar selector-output hash
  `f415058bd7f961e95be0a2f0b333e5eadcdd1b0272e530783fb43d90a4241386` and
  Solar batch-plan hash `fe8e6d0a…`.

The end-to-end Solar capsules were not byte-identical, which is consistent
with the already observed provider-generation variance. Frozen oracle totals
were 21/45, 38/28, and 36/30 PASS/FAIL for 1x/2x/3x respectively; this is
downstream Solar content variation, not a selector-decision difference.
The prior three 1.2K controls ranged from 37/29 to 48/18 across the same
66 applicable prefix checks, so the concurrency arms do not show a new
semantic regression attributable to selection scheduling.

Category totals across the 66 applicable checks were:

| Workers | Current facts | Blockers | Current intent | Verification/continuation |
|---:|---:|---:|---:|---:|
| 1x | 5/25 | 6/12 | 3/12 | 7/17 |
| 2x | 12/25 | 5/12 | 5/12 | 16/17 |
| 3x | 9/25 | 9/12 | 7/12 | 11/17 |

The frozen expectation-level behavior remains mixed and provider-variable:
connected-component evidence failed in all three final capsules; stale
analysis also failed in all three; large-import, allowed-floor, rebase,
verification, runtime/export, and audit evidence varied across Solar runs.
Exact-ID behavior was preserved by the selector schema and replay: 148/148
selector calls validated exact allowed IDs in every arm. Solar structural
validation passed 12/12 generations and promotion validation passed in all
three arms; raw journal count remained 276.

Capsule size was also downstream-output variable. Estimated final/max capsule
tokens and final/first growth were 1x `297/988, 0.301x`, 2x `621/974,
1.045x`, and 3x `222/2,591, 0.116x`; these are not evidence that concurrency
changed selector retention because the selected source universe was identical.

## Decision metric and recommendation

All three arms satisfy the structural/full-trace gate, and all preserve the
same selector semantics. However, concurrency does not materially reduce
wall-clock cost: the 2x result is within measurement/provider variation of
serial, while 3x is slower and has substantially higher local request work.
The small 2x end-to-end improvement is not a reliable architectural benefit
because Solar wall and output also vary between runs.

Keep the selector serial at the established 1.2K setting for PanoRef. Do not
proceed to selector parallelism from this evidence; a future parallel phase
would require a backend that provides real concurrent generation rather than
local request fan-out alone.

SELECTOR_CONCURRENCY_RECOMMENDATION: 1x serial at 1,200-token chunks

BASELINE_REMAINS_BEST
