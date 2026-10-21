# Cached 32K SpecPrefill follow-up

The SpecPrefill run completed all 22 turns with 39/44 values correct and 1 compaction. Its total measured time was 1.70× the cached full-context control.

| Method | Correct values | Exact turns | Late recall | Total time | Mean end-to-end TTFT | Compactions |
|---|---:|---:|---:|---:|---:|---:|
| full32k | 44/44 | 22/22 | 8/8 | 174.3s | 7.26s | 0 |
| specprefill32k | 39/44 | 18/22 | 4/8 | 296.9s | 12.81s | 1 |

## Setup

The frozen conversation has 26,583 source tokens. Its conservative full-context bound, including maximum-length replies, is 29,250 tokens. The case and native executable hashes match the earlier control. Flash-Next runs on the same P100 with a 32,768-token context and resident KV capacity, prompt-cache=6, sequential positions, greedy answers capped at 64 tokens, and a 4 GiB VRAM reserve. The student also has a 32,768-token limit and uses the existing cached adaptive selector.

The student prefill block is 512 tokens, with `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` to reduce fragmentation. The first attempt with 1,024-token blocks ran out of GPU memory before turn 17 after answering 31/32 values correctly. A 512-token-block attempt with the default allocator failed during lookahead before turn 18 after answering 33/34 correctly. The smaller block and allocator reduce temporary memory pressure; model weights, selection policy, keep-rate rules, target configuration, and input case are unchanged. The reported run restarted at turn 1. The failed attempt is retained separately; its partial timings are excluded. Runtime settings are recorded in `runtime.json`.

Times include admission, student work, archive/index access, any compaction/rebuild, and session persistence. Loading and warmup are excluded. This is a native session harness, not an HTTP gateway measurement. The control is the completed earlier run, not an additional control rerun. Each arm keeps its own actual replies.

The headroom policy triggered compaction on turn 20. The summary request itself was 31,590 tokens; adding the 1,024-token summary output reserve and admission allowances exceeded the 32,768-token budget. The preceding student prompt was only 28,653 tokens: this was summary headroom exhaustion, not exhaustion of the original conversation window. The first selection miss occurred on turn 4, before compaction.

## Cache and prefill measurements

| Method | Answer backend TTFT, mean | Outside native calls | Answer prefix reuse (sum) | Native prompt tokens read (sum) | Largest native input |
|---|---:|---:|---:|---:|---:|
| full32k | 7.25s | 0.0s | 348,927 | 27,843 | 28,113 |
| specprefill32k | 8.11s | 47.7s | 227,430 | 37,597 | 31,590 |

Outside-native time includes the student, indexing, framing, and persistence; it is not an isolated student-kernel timer.

## Answers differing from the control

- Turn 4, revision-multihop: expected `[217379, 385680]`; control `217379,385680`; SpecPrefill `217379,UNKNOWN`.
- Turn 19, late-unasked-recall: expected `[320624, 731401]`; control `320624,731401`; SpecPrefill `320624,618409`.
- Turn 20, late-unasked-recall: expected `[205464, 476472]`; control `205464,476472`; SpecPrefill `205464,500609`.
- Turn 21, late-unasked-recall: expected `[436414, 265615]`; control `436414,265615`; SpecPrefill `UNKNOWN,UNKNOWN`.

One synthetic code/configuration conversation, one completed run per method; no order-balanced repeats or confidence intervals. Exact value recall and linked revision lookups do not establish broad coding or prose quality.

Raw follow-up artifacts: [/home/distilledoreo/.cache/orchid-three-way-28k/run-32k-spec-v3](/home/distilledoreo/.cache/orchid-three-way-28k/run-32k-spec-v3).

Case SHA256: `d745a202951fca47fbb1bea27fd97d6631dad0e6aeb7ad9e89b08678af319b98`.
