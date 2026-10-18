# 8K cached SpecPrefill with integrated exact lookup

**The run improved partial accuracy but did not complete.** It answered all 20 scored fields correctly across turns 1–10, with seven successful compactions, then stopped during the eighth compaction before answering turn 11. No late-recall question was reached.

| Method | Completion | Correct fields | Successful compactions | Measured time |
|---|---|---:|---:|---:|
| Earlier 8K SpecPrefill | Failed before turn 9 | 12/16 partial | 5 | See original report |
| Current 8K SpecPrefill + exact lookup | Failed before turn 11 | 20/20 partial | 7 | 485.0s to failure |
| Earlier ordinary 8K compaction | Completed 22 turns | 30/44 | 5 | 415.6s |
| Earlier cached full 32K | Completed 22 turns | 44/44 | 0 | 174.3s |

The aligned first eight turns improve from 12/16 to 16/16. A partial 20/20 does not imply 44/44 or establish superiority to a completed baseline.

## Failure diagnosis

The turn-11 summary request had 4,912 input tokens. Flash-Next generated exactly 1,024 tokens and reported `finish=length`; its output ended inside a quoted archive reference, leaving incomplete JSON. NativeSummarizer rejected it with JSONDecodeError. This was summary output truncation, not an 8K input/context overflow. All native calls satisfied input-plus-output capacity checks.

The generated summary was repeating long archive-manifest addresses where the summarizer prompt requests compact M IDs. A truncated address is visible at the end of the saved output. This is a structured-summary/provenance-generation problem, separate from lookup delivery. The test was not repaired or restarted with different budgets after the failure.

The failed turn cost 50.7 seconds. Completed turns cost 434.3 seconds; total measured time to failure was 485.0 seconds. Loading and warmup are excluded. Seven prior compactions succeeded, meeting the repeated-boundary stress objective, but the session did not finish.

## Exact lookup and interpretation

All ten answered turns report `exact_lookup.status=delivered`; there were zero lookup deferrals among those turns. The final late-unasked-recall questions (turns 19–22) were never scored. The run therefore supports improved recall on the completed development questions but cannot establish repeated late recall across the whole workload.

Frequent compaction dominates the practical result: after turn 3, every completed source turn required compaction. Even before failure, the current run exceeded the complete ordinary-compaction baseline time. This is not a latency win.

## Conditions and comparison limits

The original 26,583-source-token conversation and its expected fields are byte-identical. Both student and target use an 8,192-token limit. Native executable hash and target engine configuration match the earlier 8K run; prompt-cache=6, summary output=1,024, compacted seed=3,072, expansion=2,048, answer cap=64. Student prefill block=512 with the expandable PyTorch allocator. The earlier 8K attempt used block=1,024, and the current implementation also includes intervening retrieval changes.

Per user direction, no additional disabled arm was run. This is a historical comparison of the current system, not a paired exact-lookup ablation. The case remains a development benchmark, not independent generalization evidence.

Raw diagnostics, original summaries, checkpoints, runtime metadata and implementation snapshot: [/home/distilledoreo/.cache/orchid-three-way-28k/run-8k-exact-20261017](/home/distilledoreo/.cache/orchid-three-way-28k/run-8k-exact-20261017).
