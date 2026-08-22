# PanoRef serial selector chunk-size sweep

## Scope and freeze

This is the selector -> Solar chunk-size sweep on the frozen PanoRef replay.
Only the serial selector chunk target varied. The selector prompt, model,
schema, Solar consolidator, recursive batching, oracle, replay order, timeout
policy, and source trace were unchanged. No canonicalizer, concurrency, retry,
or between-arm tuning was used.

- Sweep commit: `01b887a1ff967b397d08225efa8d1006c291ea01`
- Historical 1.2K baseline commit: `5afba2cc0dd34b11a04201c0cf35a3b7a428fd94`
- Replay SHA256: `aa0e7326c2514501a8c53a5a2c4669b1512383507989e71403d4e6f00f7eaf07`
- Oracle manifest SHA256: `88e37b2803c028bbc50b0ebaf6a1ed62c639a9776a50d4b400b7b14f0a6ef323`
- Oracle checks SHA256: `88d38ad16471cb13eb23ae75233e9a3a5c59aa9ddec714f1ca63c4739a59980`
- Frozen raw-plan SHA256: `5f8960bf2f643742c5b26bf26da9deda485d69e76a7bf079362b76538473766b`
- Immutable source universe: 346 items, 143,670 source tokens, 276 events
- Apparatus hashes: Phase 3.3 `bdc29750268ad788c8998a5640d4822aaa91435eeed66575027212dcf8ef5659`; selector `f3074ae5540f6c5dd50e57e68805fe745ff6dac829931bc41dc29b8721bb8300`; control adapter `ed00faa77c7f26ea34b29ab3e519648ab571c0f7bb5dc6151804c98987adafac`

The historical 1.2K arm is the existing three-repeat selector control. New
sweep arms are one repeat each. All new arm metadata records a fresh root,
configuration hash, frozen-input hashes, `tuning_between_arms=false`,
`concurrency=false`, and no internal transport retry.

New-arm `benchmark_config_hash` values: 400 `092fb2f88ccf178bf50f47ea8f80b871ee9eab3cc2a5f351ed889faf25db1c79`; 800 `01dbd1f052a68cd114cf26166d2924cf11d8a12cbdfbf8c5f638811e4ee240c4`; 2K `1bf5f009ef0de495cb1a7aeaad39a8840d2bc9c21b6d444416a878c81bd08416`; 4K `f035aa1a397088406e81ccd4b7ea84a3222d791307190bced749374d3a984b97`; 8K `f23fd600daff3e7cd73ca1ad85482eb1491cd2c9babd8eb773dedd2b5dfee630`.

## Chunk-size curve

Selector request p50/p95 values use linear interpolation over the measured
per-request `wall_ms` samples. Oracle values marked `prefix` are not final
trace results and are not treated as semantic wins.

| Chunk target | Calls | Selector wall | End-to-end wall | Tok/s | Selected % | Oracle P/F | Timeouts |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 400 | 274 | 1,917.2 s | 2,171.6 s | 16.1 | 100.0% | 12/3 prefix | 0; Solar exact-ID failure |
| 800 | 195 | 1,852.6 s | 1,887.9 s | 6.1 | 99.5% | 3/2 prefix | 0; Solar invalid JSON |
| 1,200 baseline | 148 | 1,805.1 s median | 2,034.4 s median | 67.9 median | 96.2% | 23/10 final, 3 repeats | 0/3 |
| 2,000 | 81 | 1,738.4 s | 2,003.5 s | 56.0 | 78.2% | 5/6 final | 0 |
| 4,000 | 39 | 1,840.7 s | 1,894.9 s | 6.3 | 59.8% | 0/5 prefix | 0; Solar exact-ID failure |
| 8,000 | n/a; 19 planned | n/a | n/a | n/a | n/a | n/a | 1 selector timeout |

Completion was 0/1 for 400, 0/1 for 800, 3/3 for the historical 1.2K
baseline, 1/1 for 2K, 0/1 for 4K, and 0/1 for 8K. The 2K run completed all
10/10 Solar generations; its final oracle was 5/11, materially below the
baseline aggregate of 23/33 (7.7/11 per repeat). The other new arms did not
reach a final trace oracle.

The curve shows why call count is not a speed proxy. 2K reduced calls and
selector wall versus 1.2K, but only reduced end-to-end wall by 30.9 seconds
and also reduced selection to 78.2%. 4K had half as many calls as 2K but more
selector wall, with much higher p50/p95 request latency. 8K hit the existing
180-second selector timeout before Solar replay.

## Selector, provider, and capsule economics

| Chunk target | Selector p50/p95 | Selector input/output | Selected / omitted source tokens | Solar calls; input/output | Capsule final / max tokens |
|---:|---:|---:|---:|---:|---:|
| 400 | 6.19 / 14.61 s | 272,012 / 6,287 | 143,670 / 0 | 4; 108,344 / 8,507 | 3,422 / 3,422 (partial) |
| 800 | 9.18 / 15.89 s | 265,139 / 5,758 | 142,948 / 722 | 2; 52,844 / 2,950 | 1,376 / 1,376 (partial) |
| 1,200 | 11.05 / 19.52 s median | 261,050 / 5,159 median | 138,149 / 5,521 | 12; 305,637 / 12,319 median | 324 / 1,354 median |
| 2,000 | 19.84 / 34.31 s | 255,221 / 3,615 | 112,288 / 31,382 | 10; 232,635 / 11,764 | 196 / 3,780 |
| 4,000 | 43.40 / 71.13 s | 251,567 / 2,546 | 85,873 / 57,797 | 2; 44,075 / 1,833 | 691 / 691 (partial) |
| 8,000 | n/a | n/a | n/a | 0; n/a | n/a |

Selector input/output falls as chunks grow, but request latency rises sharply
and selection becomes materially more aggressive even though selector policy
was unchanged. The 1.2K baseline remains the only condition with repeated
full-trace evidence and stable high retention.

Structural lineage and promotion validation passed for every completed
generation in each accepted root: 3/3 at 400, 1/1 at 800, 36/36 across the
1.2K baseline, 10/10 at 2K, and 1/1 at 4K. Resurrection and invention
failures were zero in all those completed generations. The 8K timeout occurred
before Solar replay, so structural/oracle grading was not reached.

## Semantic retention and required constraints

The frozen oracle has 11 checks. The 1.2K baseline final results across its
three repeats were 23 PASS / 10 FAIL. Its final expectation results were:

- current intent (`rebase`): 3/3 PASS;
- blocker / large import (`100K`, `100,000`, or `large imported`): 0/3 PASS;
- architecture constraints: allowed-floor 2/3 and connected-components 1/3;
- stale-analysis avoidance: 2/3 PASS;
- verification: test-evidence 3/3 and large-optimizer 3/3;
- runtime/export: runtime evidence 3/3 and export evidence 2/3;
- audit completion: 1/3 PASS;
- exact identifiers: structural exact-ID validation passed for all 36 completed generations.

The 2K complete run passed current intent, test evidence, large-optimizer
evidence, and audit completion, but failed allowed-floor, connected-component,
large-import, stale-analysis, runtime, and export checks in its final capsule
(5/11 final). The partial 400 and 800 prefixes retained current intent and
large-import evidence but missed connected-component; 800 also missed rebase.
The partial 4K prefix missed all five checks reached. These are semantic
prefix observations only, not full-trace claims.

Provider/runtime failures are kept separate from semantic misses: 400 and 4K
failed on Solar exact-ID response validation, 800 failed on Solar JSON parsing,
and 8K timed out in the selector. None is counted as an oracle semantic fail.

The first attempted 400 root, `panoref/target_0400`, is preserved but excluded
from the curve because the initial wrapper re-expanded source spans at the
target and produced a non-immutable 726-item universe. The accepted corrected
400 root is `target_0400_frozen_source_universe`; its metadata token-field
correction is recorded in `RUN_METADATA_CORRECTION.json` without changing the
run output.

## Decision metric and recommendation

The primary metric is semantically acceptable retired source tokens per wall
second. A run qualifies only if it completes the trace and its final frozen
oracle is qualitatively comparable to the 1.2K baseline. Under that gate, the
1.2K baseline is the only repeated qualifying configuration: 138,149 selected
and retired source tokens / 2,034.4 seconds = 67.9 semantically acceptable
source tok/s median. The 2K run was faster by 30.9 seconds but failed the
semantic gate; no other new size completed the trace.

No FreetoShop confirmation was run because the PanoRef curve produced no new
full-trace semantic winner to validate, and no selector-parallelism phase was
started.

SELECTOR_CHUNK_SIZE_RECOMMENDATION: 1200

BASELINE_REMAINS_BEST
