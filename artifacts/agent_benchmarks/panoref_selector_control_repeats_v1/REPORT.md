# PanoRef same-workload selector control

## Scope and freeze

This is the selector -> Solar control for the frozen PanoRef replay. The
control delegates to the existing Phase 3.3 apparatus, uses no canonicalizer,
no internal transport retry, and no between-repeat tuning.

- Commit: `5afba2cc0dd34b11a04201c0cf35a3b7a428fd94`
- Replay SHA256: `aa0e7326c2514501a8c53a5a2c4669b1512383507989e71403d4e6f00f7eaf07`
- Oracle manifest SHA256: `88e37b2803c028bbc50b0ebaf6a1ed62c639a9776a50d4b400b7b14f0a6ef323`
- Oracle checks SHA256: `88d38ad16471cb13eb23ae75233e9a3a5c59aa9ddec714f1ca63c4739a59980`
- Frozen raw-plan SHA256: `5f8960bf2f643742c5b26bf26da9deda485d69e76a7bf079362b76538473766b`
- Workload: 276 events, 143,670 source tokens, 11 final oracle checks
- Selector: `qwen3.5-4b@q6_k`, 1,200-token chunks, 148 sequential calls
- Solar: `upstage/solar-pro4`, unchanged recursive direct target/settings

All three run metadata files contain the same commit, replay/oracle/plan
hashes, apparatus hashes, model/settings, and `tuning_between_repeats=false`.

## Direct comparison

Wall, throughput, and Solar token columns are medians across all three repeats;
final oracle P/F uses full traces only. The direct arms are the preserved
historical 1K/2K results.

| Arm | Full traces | Generations | Median total wall | Median retired tok/s | Source selected / omitted | Solar input / output tokens | Final oracle P/F | Timeouts |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| Selector -> Solar | 3/3 | 12/12, 12/12, 12/12 | 2,034.4 s | 67.9 | 138,149 / 5,521 | 305,637 / 12,319 | 23/10 | 0/3 |
| Direct 1K | 1/3 | 13/13, 7/13, 8/13 | 454.8 s | 315.9 | 143,670 / 0 | 166,608 / 16,551 | 5/6 | 2/3 |
| Direct 2K | 2/3 | 7/13, 13/13, 13/13 | 842.8 s | 190.7 | 143,670 / 0 | 265,869 / 25,481 | 14/8 | 1/3 |

The selector processed the full 143,670-token source workload, selected and
retired 138,149 tokens, and omitted 5,521 tokens (3.84%). It added a median
1,805.1 s of local selector wall time, 261,050 selector input tokens, and
5,159 selector output tokens per repeat. Its median Solar-only wall time was
228.7 s, so local selector work was the dominant cost. Selector Solar cost
was $0.01065 median versus $0.00777 for direct 1K and $0.01097 for direct 2K.

Full-trace capsule comparison uses estimated content tokens; growth is
final-generation size divided by first-generation size, and `max` is the
largest generation. These are derived from each preserved `capsules.jsonl`.

| Arm | Full-trace median final / max capsule tokens | Median final/first ratio |
|---|---:|---:|
| Selector -> Solar | 324 / 1,354 | 0.287x |
| Direct 1K | 367 / 1,974 | 0.186x |
| Direct 2K | 870 / 4,846 | 0.437x |

## Semantic and structural comparison

Across all applicable prefix checks, selector control scored 125/73 P/F;
direct 1K scored 99/47 and direct 2K scored 141/36. For final full-trace
checks, selector scored 23/10 across three completed traces. The corresponding
direct full-trace evidence was 5/6 for 1K and 14/8 for 2K.

Final full-trace miss categories:

| Arm | Blocker preservation | Continuation sufficiency | Current fact preservation | Current intent preservation |
|---|---:|---:|---:|---:|
| Selector -> Solar | 3 | 3 | 4 | 0 |
| Direct 1K | 1 | 1 | 3 | 1 |
| Direct 2K | 1 | 4 | 3 | 0 |

Selector misses still include `100,000/100k`, `large imported`,
`connected component`, allowed/floor-region constraints, and the
`33,120,308`-byte/valid export evidence. It also missed stale-analysis and
round-five/six audit evidence. The direct arms show the same families, with
the 2K full traces additionally missing `101,250`, `36,788 triangles`, and
`36.9 seconds` in their final oracle rows. These are semantic misses, not
provider failures.

The selector structural gate passed all 36 completed generations, with zero
structural failures, zero resurrection failures, zero invention failures, and
promotion validation `PASSED` in all three runs. The preserved direct report
does not contain a comparable structural-row series, so direct exact-ID
structural preservation is not graded here rather than inferred.

Constraint callout:

- Persistent architecture constraints: no production architecture was
  changed; neither arm semantically preserves every frozen constraint.
- Current intent: no selector final miss; one direct-1K final miss; none in
  the direct-2K full traces.
- Blockers: selector has three final blocker misses; direct has one in each
  historical full-arm set.
- Verification/audit: selector retains most obligations but misses stale
  analysis and round-five/six evidence; direct has the same obligation class.
- Exact identifiers: selector is structurally/audit validated; direct has no
  comparable preserved structural report.
- Runtime/export: selector has no final miss for the 36,788-triangle/36.9 s
  evidence, but does miss the 33,120,308-byte validity evidence; direct shows
  the complementary misses noted above.
- Stale-analysis avoidance, connected-component constraints, and large-import
  constraints remain unresolved semantic risk in both approaches.

## Recommendation

The selector control improved observed PanoRef completion (3/3 versus 1/3
direct 1K and 2/3 direct 2K) and final semantic retention (23/33 checks), and
it eliminated observed provider/runtime timeouts in these three repeats. That
benefit came from filtering only 3.84% of source tokens while adding about
1,805 s of local work and reducing retirement throughput to 67.9 tok/s.

For PanoRef, the measured semantic/reliability gain does not justify that
local latency and added complexity against the direct 2K arm’s lower median
wall time and higher throughput. This conclusion is limited to PanoRef and
the already-recorded FreetoShop evidence; it is not a broader architecture
claim.

**DIRECT_REMAINS_PREFERRED**
