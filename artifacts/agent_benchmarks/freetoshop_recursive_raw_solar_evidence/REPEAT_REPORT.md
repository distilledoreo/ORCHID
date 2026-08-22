# Repeated recursive raw → Solar evidence

Disposable benchmark only. No ORCHID production code or policy is changed.

Frozen replay SHA: `6feb697961f22654d813159d07e2e519492b9ec431cdf8df08ae549508c26b16`; raw-plan SHA: `b50194fb9a7af32c096a9b5dbb5ab88905aa8e9b992903b054346cf1b60755aa`; oracle SHA: `9c5b7f4f9e6c0bc7af514901666e91ce1e47fb047ccaa7b6bb9db6f5f195cd0c`.
Runs: 3 independent repeats per budget; no provider retry within a run.

## Repeat reliability

| Budget | Full-trace successes | Success rate | Timeouts | Failures | Median tok/s | Median wall s |
|---:|---:|---:|---:|---:|---:|---:|
| 1000 | 2/3 | 67% | 1 | 1 | 216.4 | 857.2 |
| 2000 | 1/3 | 33% | 2 | 2 | 118.7 | 682.4 |

## Miss taxonomy

### 1K

- By category: `{"BLOCKER_PRESERVATION": 2, "CONTINUATION_SUFFICIENCY": 9, "CURRENT_FACT_PRESERVATION": 35, "CURRENT_INTENT_PRESERVATION": 72}`
- By checkpoint: `{"architecture-baseline": 35, "clone-progress": 2, "intent-start": 81}`
- Missing terms: `{"add unit tests": 2, "bounded": 26, "copy-on-write": 23, "not decorative": 49, "real behavior": 49, "real functionality": 49, "recovery": 9, "region local": 26, "region-local": 26, "run the tests": 2, "save/reopen": 9, "sparse": 9, "tile-sized": 26, "tiled": 10, "tiled pixel": 9, "tiledpixelstore": 9, "undo": 9, "verify": 2}`

### 2K

- By category: `{"CURRENT_INTENT_PRESERVATION": 26}`
- By checkpoint: `{"intent-start": 26}`
- Missing terms: `{"copy-on-write": 16, "not decorative": 10, "real behavior": 10, "real functionality": 10}`

## Direct versus selector

Same replay: **True** (hash independently verified: **False**); same deterministic oracle: **True**.
Selector baseline: SUCCEEDED, 68.768 selected-source tok/s, 2884.9s end-to-end, 131/54 semantic P/F.
Direct repeated medians:

| Direct budget | Full-trace runs | Median full-run wall s | Median tok/s | Median Solar input | Final-checkpoint P/F | All evaluated P/F |
|---:|---:|---:|---:|---:|---:|---:|
| 1000 | 2/3 | 834.6 | 246.643 | 317883 | 34/4 | 350/118 |
| 2000 | 1/3 | 1315.1 | 154.182 | 339772 | 18/1 | 216/26 |

Caveat: The selector arm is the preserved same-replay/oracle historical run, not a same-minute rerun; provider variance remains a limitation. Direct semantic totals include only evaluated prefixes unless marked full-trace-only.

## Additional traces

NOT_RUN: no second frozen coding replay and candidate-independent oracle exists in this checkout

The additional-trace result is intentionally not inferred from unrelated retrieval or media artifacts.
