# Frozen FreetoShop canonicalizer replay

## Measured facts

- Replay events supplied: 199
- Expanded source items: 317
- Planned source tokens: 202761
- Planned 12K batches: 18
- Attempted batches: 3
- Valid successful batches: 2
- Timeout batches: 1
- First failed batch: 2
- Unattempted batches after failure: 15
- Valid partial-progress source tokens: 22993
- Partial successful-batch rate: 74.619 source tokens/sec
- Harness status: FAILED
- Error: model request timed out after 180.0s

## Gate result

The frozen replay did not complete and did not satisfy the zero-timeout requirement. The partial successful-batch rate is diagnostic only: it excludes the timed-out batch and all remaining work, and it does not include selector, consolidator, promotion, semantic correctness, or provenance completion. No live preflight was run after this failure.

## Interpretation

The explicit request deadline is functioning as intended by making the stalled/non-progressing request observable and bounded. It is not itself evidence that the underlying local inference workload is healthy. The next optimization must explain or eliminate the long-tail canonicalizer behavior before any production batch-size or concurrency change is accepted.
