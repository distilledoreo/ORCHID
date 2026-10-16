# Follow-up: 10% and 30% SpecPrefill retention

**30% retained the correct answers on all four cases; 10% lost one answer.**
Both reduced observed warmed pipeline TTFT, including fresh student inference
and selection, on the same Swift IQ3_S model, Qwen3.5-0.8B student, and P100.
These are four synthetic lookup cases measured once per arm, not a general
quality or latency benchmark.

## Fresh controls and results

| Requested keep | Actual target IDs retained | Mean pipeline TTFT | Reduction vs full alone | Correct |
|---|---:|---:|---:|---:|
| Full, student absent | 100% | 5.949 s | — | 4/4 |
| Full, student resident | 100% | 5.741 s | 3.5% | 4/4 |
| Student, 100% | 100% | 6.146 s | −3.3% | 4/4 |
| Student, 50% | 49.61% | 4.516 s | 24.1% | 4/4 |
| Student, 30% | 30.71% | **3.733 s** | **37.2%** | **4/4** |
| Student, 10% | 10.24% | **2.680 s** | **55.0%** | **3/4** |

Against the co-located full-prefill control, the 30% and 10% arms had 35.0% and
53.3% lower observed mean pipeline TTFT. These are descriptive ratios of means;
there are no confidence intervals or significance claims. All four 100% outputs
matched their corresponding ordinary co-located outputs token for token.

The selector keeps `ceil(chunk_count * rate)` whole 32-token chunks. Because a
short final chunk can be selected, nominal rates are not exact token budgets.
The student still reads the entire original prompt; only target reads shrink.

| Prompt / evidence location | Full alone | Keep 50% | Keep 30% | Keep 10% | 10% correct? |
|---|---:|---:|---:|---:|---|
| 504 IDs / 20% | 4.273 s | 3.337 s | 2.774 s | 2.288 s | No |
| 504 IDs / 80% | 4.270 s | 3.326 s | 2.750 s | 2.259 s | Yes |
| 1528 IDs / 20% | 8.034 s | 5.754 s | 4.743 s | 3.132 s | Yes |
| 1528 IDs / 80% | 7.217 s | 5.646 s | 4.665 s | 3.040 s | Yes |

## Failure inspection

The failed 10% case was the 504-ID prompt with the relevant record at 20%.
The selected input retained a distractor record and the question, but discarded
all IDs encoding the correct answer code. The target returned the distractor's
code. An offline exact-token span audit found the expected code retained in
all four 30% and 50% cases, and three of four 10% cases. The raw failed output
and selected IDs remain private. No repair, retrieval, query pinning, or tuning
was applied after seeing the failure.

The 30% setting therefore passed this small lookup test. The 10% setting was
already lossy on this set; its lower TTFT is not a quality-equivalent speedup.
This does not establish that 30% preserves quality on harder tasks or longer
contexts.

## Protocol and verification

- Reused the exact original frozen cases, verified by source-protocol hash and
  input-ID equality. The earlier report and artifacts remain unchanged.
- Collected 24 measured requests: four fresh student-free full baselines and
  20 shuffled co-located full/100/50/30/10 requests. Six warmups were excluded.
  Each timed selected arm recomputed the student trace.
- Used the same original-position interface, model-dtype QK / FP32 softmax,
  pool-before-max kernel 13, maximum over full-attention layers/heads, query-row
  mean, 32-token chunk means, and at most eight total look-ahead query rows.
- Verified all 24 original input identities and all selected subsets, GPU
  position acknowledgements, and original-length decode coordinates. Zero model
  API errors. All 100% generation controls matched.
- Kept the same diagnostic controls: int8 KV, MTP=4, 4096 context, caching off,
  route-tail skipping off, CPU prefill share off, PCIe fraction one. Full alone
  used a 700-MiB reserve (9.54-GiB expert cache); co-location used 4096 MiB
  (6.23-GiB expert cache). Resource epochs were sequential.
- Warmed mean student/selection time was about 0.39 s at every rate. The first
  excluded selector call took 4.07 s. Model loading was excluded. These warmed
  results do not establish cold-start performance.
- Offline suite after inference stopped: **428 passed, 10 skipped**. The student
  environment passed all **26 focused tests**; the adapter suite passed ten.
  Ruff passed. The cancellation test now waits for an actual HTTP request before
  cancellation, replacing a scheduling-sensitive sleep; production adapter code
  was not changed. One earlier loaded-system suite hit an existing retrieval
  deadline; the clean full rerun passed.

All owned servers stopped, port 18586 closed, and P100 memory returned to zero.
Production configuration and the existing service were unchanged. This remains
an adaptation to hybrid Qwen/Strata, not the dense Llama/vLLM paper reproduction.
No ORCHID evidence gate or recovery path was used.

`summary.json` contains sanitized aggregates, per-case results, and answer-span
retention flags. Exact prompts, responses, positions, configs, warmups and logs
remain in ignored `data/strata_specprefill/keep-rates-v1`. See the experiment
README for `--cases-from` and `--keep-rates`; the default remains 100%/50%.
