# Uncached SpecPrefill behavioral divergence

Twelve frozen prompts: two coding and two multi-hop tasks at each exact length 8192, 16384, 32768. Same Flash-Next Swift IQ3_S target and P100 16GB. The student is Qwen3.5-0.8B. Both cross-request caches are disabled; no append-only retrieval, placeholders, query pinning, or policy changes. Native speculative decoding is disabled for aligned diagnostics.

At continuation index j, all arms see exactly the same previous continuation IDs, at original positions N through N+j-1. Only prompt retention differs. We evaluate two separate trajectories: the full-context model's greedy continuation (up to 96 tokens, including EOS when emitted) and an independently constructed complete reference answer including EOS. These are conditional distribution comparisons, not independently generated completions.

KL is D_KL(P_full || P_selected), in nats, over all 248320 vocabulary entries. Metrics use float64 log-softmax from unmodified float32 model logits. Top-1 agreement compares raw argmax IDs. Reference-answer log probability is teacher-forced, summed over the complete reference sequence; token-normalized values are also reported. No top-k approximation is used.

The unchanged adaptive mass-0.80 selector chose about 5%, so an additional fixed27 arm was frozen before target inference to answer the specific approximately-27% question directly.

| Arm | Actual retention, 8K | 16K | 32K |
|---|---:|---:|---:|
| fixed30 | 30.08% | 30.08% | 30.08% |
| adaptive | 5.08% | 5.08% | 5.08% |
| fixed27 | 27.34% | 27.15% | 27.05% |

## full-generated trajectory

| Arm | Top-1 agreement | Mean KL | P95 KL | LogP delta/token | Prompts with ≥95% agreement |
|---|---:|---:|---:|---:|---:|
| fixed30 | 99.29% (558/562) | 0.0067 | 0.0008 | -0.0088 | 12/12 |
| adaptive | 99.29% (558/562) | 0.0119 | 0.0018 | -0.0147 | 12/12 |
| fixed27 | 99.47% (559/562) | 0.0077 | 0.0008 | -0.0099 | 12/12 |

| Length | Arm | Top-1 agreement | Mean KL | LogP delta/token |
|---|---|---:|---:|---:|
| 8K | fixed30 | 99.46% | 0.0053 | -0.0084 |
| 8K | adaptive | 99.46% | 0.0124 | -0.0179 |
| 8K | fixed27 | 99.46% | 0.0077 | -0.0118 |
| 16K | fixed30 | 99.47% | 0.0049 | -0.0028 |
| 16K | adaptive | 99.47% | 0.0069 | -0.0018 |
| 16K | fixed27 | 99.47% | 0.0078 | -0.0105 |
| 32K | fixed30 | 98.95% | 0.0100 | -0.0151 |
| 32K | adaptive | 98.95% | 0.0162 | -0.0243 |
| 32K | fixed27 | 99.48% | 0.0075 | -0.0073 |

## reference trajectory

| Arm | Top-1 agreement | Mean KL | P95 KL | LogP delta/token | Prompts with ≥95% agreement |
|---|---:|---:|---:|---:|---:|
| fixed30 | 99.10% (549/554) | 0.0115 | 0.0057 | -0.0031 | 11/12 |
| adaptive | 98.92% (548/554) | 0.0213 | 0.0072 | -0.0143 | 12/12 |
| fixed27 | 99.28% (550/554) | 0.0131 | 0.0056 | -0.0064 | 12/12 |

| Length | Arm | Top-1 agreement | Mean KL | LogP delta/token |
|---|---|---:|---:|---:|
| 8K | fixed30 | 99.45% | 0.0091 | -0.0085 |
| 8K | adaptive | 98.36% | 0.0311 | +0.0001 |
| 8K | fixed27 | 98.91% | 0.0138 | -0.0027 |
| 16K | fixed30 | 98.92% | 0.0105 | -0.0183 |
| 16K | adaptive | 99.46% | 0.0117 | -0.0401 |
| 16K | fixed27 | 99.46% | 0.0113 | -0.0252 |
| 32K | fixed30 | 98.92% | 0.0149 | +0.0175 |
| 32K | adaptive | 98.92% | 0.0212 | -0.0026 |
| 32K | fixed27 | 99.46% | 0.0142 | +0.0087 |

## Reference facts, excluding boilerplate

These positions overlap the required literal constants, relay destination IDs, and final access code in the independently constructed reference. This grading-only breakdown checks whether syntax-heavy aggregate agreement conceals disagreement on answer facts.

| Arm | Content Top-1 agreement | Content KL | Content LogP delta/token |
|---|---:|---:|---:|
| fixed30 | 100.00% (245/245) | 0.0002 | -0.0001 |
| adaptive | 100.00% (245/245) | 0.0004 | -0.0002 |
| fixed27 | 100.00% (245/245) | 0.0002 | -0.0001 |

## Reference-answer sequence probability

| Case | Arm | Full logP | Selected logP | Difference |
|---|---|---:|---:|---:|
| coding-8k-0 | fixed30 | -15.455 | -17.435 | -1.980 |
| coding-8k-0 | adaptive | -15.455 | -17.516 | -2.061 |
| coding-8k-0 | fixed27 | -15.455 | -15.499 | -0.044 |
| coding-8k-1 | fixed30 | -17.981 | -19.292 | -1.312 |
| coding-8k-1 | adaptive | -17.981 | -18.556 | -0.576 |
| coding-8k-1 | fixed27 | -17.981 | -18.839 | -0.859 |
| multihop-8k-0 | fixed30 | -17.804 | -16.469 | +1.336 |
| multihop-8k-0 | adaptive | -17.804 | -16.013 | +1.792 |
| multihop-8k-0 | fixed27 | -17.804 | -17.210 | +0.595 |
| multihop-8k-1 | fixed30 | -18.173 | -17.775 | +0.397 |
| multihop-8k-1 | adaptive | -18.173 | -17.301 | +0.872 |
| multihop-8k-1 | fixed27 | -18.173 | -18.354 | -0.182 |
| coding-16k-0 | fixed30 | -16.165 | -14.849 | +1.315 |
| coding-16k-0 | adaptive | -16.165 | -19.527 | -3.363 |
| coding-16k-0 | fixed27 | -16.165 | -18.685 | -2.520 |
| coding-16k-1 | fixed30 | -14.352 | -17.255 | -2.903 |
| coding-16k-1 | adaptive | -14.352 | -19.844 | -5.491 |
| coding-16k-1 | fixed27 | -14.352 | -16.712 | -2.359 |
| multihop-16k-0 | fixed30 | -17.860 | -19.663 | -1.803 |
| multihop-16k-0 | adaptive | -17.860 | -18.280 | -0.420 |
| multihop-16k-0 | fixed27 | -17.860 | -18.302 | -0.442 |
| multihop-16k-1 | fixed30 | -18.946 | -18.967 | -0.020 |
| multihop-16k-1 | adaptive | -18.946 | -17.134 | +1.812 |
| multihop-16k-1 | fixed27 | -18.946 | -18.316 | +0.631 |
| coding-32k-0 | fixed30 | -17.139 | -18.103 | -0.964 |
| coding-32k-0 | adaptive | -17.139 | -19.239 | -2.101 |
| coding-32k-0 | fixed27 | -17.139 | -17.781 | -0.642 |
| coding-32k-1 | fixed30 | -18.977 | -14.326 | +4.651 |
| coding-32k-1 | adaptive | -18.977 | -18.553 | +0.424 |
| coding-32k-1 | fixed27 | -18.977 | -17.135 | +1.841 |
| multihop-32k-0 | fixed30 | -17.547 | -18.110 | -0.563 |
| multihop-32k-0 | adaptive | -17.547 | -17.742 | -0.195 |
| multihop-32k-0 | fixed27 | -17.547 | -18.232 | -0.685 |
| multihop-32k-1 | fixed30 | -19.016 | -18.894 | +0.122 |
| multihop-32k-1 | adaptive | -19.016 | -17.623 | +1.393 |
| multihop-32k-1 | fixed27 | -19.016 | -17.916 | +1.100 |

## Validation and limits

All 106 native requests reported zero reused prefix tokens. Teacher-forced IDs matched exactly, and sparse token IDs/coordinate hashes matched the frozen selection. Full-retention explicit-position controls passed at 8K, 16K, and 32K. Short-prompt controls compare forced versus greedy decoding and cold independent sparse-prefix rows versus in-request teacher-forced rows. See summary.json for numerical errors and family breakdowns.

40 existing baseline artifacts and the original native binary/source were verified unchanged. This creates a separate diagnostic result; it does not revise earlier latency or correctness tables. Logit dumping, teacher forcing, and disabled speculative decoding make diagnostic durations unsuitable as production latency measurements.

CUDA diagnostic executable built; focused native unit checks passed. HIP/SYCL compilers are unavailable here, so those backends were not built. The diagnostic is opt-in and distributed as a separate patch/executable; the existing native source was restored.

Limitations: synthetic repository reasoning and relay-graph tasks, four prompts per length, one greedy full-context continuation per prompt, one independent reference, shared quantized target and int8 KV. Adjacent token positions are correlated. Aggregate Top-1 percentages are empirical agreement on this sample, not a deployment-wide guarantee. Formatting and predictable reference prefixes may increase conditional agreement. KL/Top-1 do not by themselves establish task correctness or equivalence of independently generated answers.

Short-control qualification: the initial short forced/full-selection repeats were not bit-identical (maximum raw-logit difference 3.7051). Their observed KL and reference-logP deltas are retained in control-audit.json; startup tolerances were relaxed in the user-resumed run. Cold sparse-prefix versus incremental-decoding comparisons also showed nonzero KL (up to 0.03752). These controls must not be described as exact numerical equivalence. In contrast, the 8K/16K/32K 100%-retention controls had exactly zero raw-logit differences and zero KL. All 106 saved logit files were hash-verified.
