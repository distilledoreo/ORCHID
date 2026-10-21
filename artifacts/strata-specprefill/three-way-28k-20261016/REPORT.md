# Cached Flash-Next: full context vs normal vs SpecPrefill compaction

Cached 32K full context was fastest and answered every scored value correctly. Normal 8K compaction completed with lower recall and 2.38× the total time. The current SpecPrefill path failed before turn 9 and also trailed both controls on the matched first eight turns; it has no completed-session score.

One deterministic 22-turn conversation, 26,583 original source tokens. The conservative full-context bound, including capped replies, is 29,250 tokens. All arms use the same Flash-Next executable/model, greedy answers capped at 64 tokens, sequential positions, and prefix caching. SpecPrefill additionally uses the existing 0.8B student and paged archive.

| Method | Outcome | Values correct | Late recall | Total turn time | Mean end-to-end TTFT | Compactions |
|---|---|---:|---:|---:|---:|---:|
| full32k | Completed 22 turns | 44/44 | 8/8 | 174.3s | 7.26s | 0 |
| summary8k | Completed 22 turns | 30/44 | 1/8 | 415.6s | 18.28s | 5 |
| specprefill8k | Failed before turn 9 | 12/16 (partial) | Not reached | ≥366.0s | 39.10s (partial) | 5 |

**The failed arm has no completed 22-turn accuracy or latency score.** Its time is a lower bound including completed turns and the failed summary call; its TTFT average covers completed answers only. The failure was `Compaction would orphan archive references`. The generated summary returned archive IDs instead of the requested M IDs and omitted required coverage. The runtime rejected it without advancing the committed generation.

## Matched prefix before the failure

| Method | First 8 turns: correct values | First 8 turns: total time | Compactions |
|---|---:|---:|---:|
| full32k | 16/16 | 76.1s | 0 |
| summary8k | 14/16 | 176.1s | 2 |
| specprefill8k | 12/16 | 318.3s | 5 |

Timing starts before turn admission/compaction and ends after the final answer and session persistence. End-to-end TTFT includes preceding compaction, archive indexing/search, and student work. Engine/model load and identical short warmups are excluded. The normal summary and SpecPrefill summary budgets are both 1,024 tokens; SpecPrefill uses the existing 3,072-token total compacted seed and 2,048-token expansion budgets. Compaction is scheduled by actual headroom and can occur on different turns. This is a native session harness, not an HTTP gateway benchmark: full/normal histories remain in memory, while SpecPrefill includes its paged archive and checkpoint costs. HTTP/network and gateway request-ledger time are excluded. All native engines use the same 4 GiB VRAM reserve and run sequentially on the P100.

| Method | Median end-to-end TTFT | Mean answer backend TTFT | Target maintenance | Reused answer prefix tokens (sum) | Largest native input |
|---|---:|---:|---:|---:|---:|
| full32k | 8.23s | 7.25s | 0.0s | 348,927 | 28,113 |
| summary8k | 8.07s | 7.80s | 230.5s | 56,157 | 6,340 |
| specprefill8k | 46.88s | 6.70s | 283.3s | 12,000 | 5,730 |

## Quality by task

SpecPrefill counts cover its completed prefix only; differing denominators are not a full-session ranking.

| Task | full32k | summary8k | specprefill8k (partial) |
|---|---:|---:|---:|
| current-code | 6/6 | 6/6 | 6/6 |
| revision-multihop | 16/16 | 12/16 | 3/6 |
| delayed-revision | 14/14 | 11/14 | 3/4 |
| late-unasked-recall | 8/8 | 1/8 | Not reached |

## Differences from the full-context control

- Turn 4, revision-multihop: expected `[217379, 385680]`; full32k: `217379,385680`; summary8k: `217379,385680`; specprefill8k: `217379,UNKNOWN`.
- Turn 5, delayed-revision: expected `[931548, 883757]`; full32k: `931548,883757`; summary8k: `931548,883757`; specprefill8k: `931548,UNKNOWN`.
- Turn 6, revision-multihop: expected `[945770, 964919]`; full32k: `945770,964919`; summary8k: `945770,UNKNOWN`; specprefill8k: `945770,UNKNOWN`.
- Turn 7, delayed-revision: expected `[376598, 803538]`; full32k: `376598,803538`; summary8k: `376598,UNKNOWN`; specprefill8k: `376598,803538`.
- Turn 8, revision-multihop: expected `[266356, 201449]`; full32k: `266356,201449`; summary8k: `266356,201449`; specprefill8k: `266356,UNKNOWN`.
- Turn 9, delayed-revision: expected `[803316, 870360]`; full32k: `803316,870360`; summary8k: `803316,870360`; specprefill8k: not reached.
- Turn 10, revision-multihop: expected `[117782, 419963]`; full32k: `117782,419963`; summary8k: `117782,419963`; specprefill8k: not reached.
- Turn 11, delayed-revision: expected `[439595, 771568]`; full32k: `439595,771568`; summary8k: `439595, 771568`; specprefill8k: not reached.
- Turn 12, revision-multihop: expected `[282676, 563631]`; full32k: `282676,563631`; summary8k: `282676, 130252`; specprefill8k: not reached.
- Turn 13, delayed-revision: expected `[758001, 848877]`; full32k: `758001,848877`; summary8k: `758001, UNKNOWN`; specprefill8k: not reached.
- Turn 14, revision-multihop: expected `[831976, 495799]`; full32k: `831976,495799`; summary8k: `831976, 130252`; specprefill8k: not reached.
- Turn 15, delayed-revision: expected `[964372, 473996]`; full32k: `964372,473996`; summary8k: `964372, UNKNOWN`; specprefill8k: not reached.
- Turn 16, revision-multihop: expected `[980657, 107904]`; full32k: `980657,107904`; summary8k: `980657, 107904`; specprefill8k: not reached.
- Turn 17, delayed-revision: expected `[778534, 678857]`; full32k: `778534,678857`; summary8k: `778534, 678857`; specprefill8k: not reached.
- Turn 18, revision-multihop: expected `[945151, 972413]`; full32k: `945151,972413`; summary8k: `945151, UNKNOWN`; specprefill8k: not reached.
- Turn 19, late-unasked-recall: expected `[320624, 731401]`; full32k: `320624,731401`; summary8k: `UNKNOWN, UNKNOWN`; specprefill8k: not reached.
- Turn 20, late-unasked-recall: expected `[205464, 476472]`; full32k: `205464,476472`; summary8k: `UNKNOWN, UNKNOWN`; specprefill8k: not reached.
- Turn 21, late-unasked-recall: expected `[436414, 265615]`; full32k: `436414,265615`; summary8k: `UNKNOWN, UNKNOWN`; specprefill8k: not reached.
- Turn 22, late-unasked-recall: expected `[614443, 311092]`; full32k: `614443,311092`; summary8k: `UNKNOWN, 311092`; specprefill8k: not reached.

## Interpretation limits

This is one synthetic code/configuration conversation with 44 scored fields, run once per method in the stated order. It measures exact revision lookup and simple linked-field reasoning, not broad coding ability or prose quality. Each arm retains its own actual answers. There are no confidence intervals or order-balanced repetitions. Summary generation and archive selection can lose or fail to recover facts; on-disk lossless storage does not imply lossless model answers. No earlier accuracy/latency baseline was changed.

The initial SpecPrefill attempt stopped before turn 8 because summary text plus 13 provenance addresses exceeded the rendered 1,024-token budget. The controller was fixed to coalesce adjacent summary items, retaining all text and references without another model call. SpecPrefill was then rerun from turn 1. The two completed controls were copied unchanged. The failed attempt is retained in `../run-v2/specprefill8k`; its partial timings are excluded from this comparison. The student uses the existing reference PyTorch recurrent kernels; no runtime packages or kernels were changed for this test.

Raw case, configuration, summaries, native-call metrics, answers, and engine logs: [/home/distilledoreo/.cache/orchid-three-way-28k/run-v3](/home/distilledoreo/.cache/orchid-three-way-28k/run-v3).

Case SHA256: `d745a202951fca47fbb1bea27fd97d6631dad0e6aeb7ad9e89b08678af319b98`.
