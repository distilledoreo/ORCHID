# PanoRef Selector Value Isolation — KEEP-100% Control

Date: 2026-08-22

This is a benchmark-only control. The frozen PanoRef replay, replay ordering,
1.2K source chunk plan, Solar path, 12K Solar batching, exact-ID validation,
timeout policy, lineage, promotion, checkpoints, and semantic oracle were
preserved. The only changed selector decision was mechanical KEEP of every
legal source item; no Qwen selector request was made.

The frozen inputs are identical across the arms:

- 276 events, 346 source items, 143,670 source tokens
- 148 selector chunks; chunk-plan hash `f700f52fa66feb88a4544d0e7711c3d0b67a883b67d16bac8b16127a109d14ef`
- replay SHA-256 `aa0e7326c2514501a8c53a5a2c4669b1512383507989e71403d4e6f00f7eaf07`
- oracle manifest SHA-256 `88e37b2803c028bbc50b0ebaf6a1ed62c639a9776a50d4b400b7b14f0a6ef323`
- oracle checks SHA-256 `88d38ad16471cb13eb23ae75233e9a3a5c59aa9ddec714f1ca63c4739a59980`
- frozen raw-plan SHA-256 `5f8960bf2f643742c5b26bf26da9deda485d69e76a7bf079362b76538473766b`

The selector control is the existing three-repeat result at commit
`5afba2cc0dd34b11a04201c0cf35a3b7a428fd94`. KEEP-100% repeats ran at commit
`7a5750afc9cc64ff544741ae37127f2ff38add65`; production ORCHID files were not
changed.

## Comparison

| Arm | Full runs | Median wall | Tok/s | Source passed % | Solar input | Oracle P/F | Timeouts |
|---|---:|---:|---:|---:|---:|---:|---:|
| Selector control, 1.2K serial | 3/3 | 2,034.4 s | 67.9 | 96.2% (138,149/143,670) | 305,637 | 23/10 final aggregate | 0 |
| KEEP-100% | 1/3 | 551.1 s* | 260.7* | 100.0% (143,670/143,670) | 330,581* | 3/8* full-run terminal | 1 timeout (+1 protocol) |

`*` is the one completed KEEP-100% run, not a repeat median. Across all three
KEEP attempts, median wall was 551.1 s, median attempted throughput was 128.2
tok/s, median Solar input was 296,962, and terminal checkpoint results were
12/9; those terminal results include two incomplete traces and are not a final
trace semantic aggregate.

### KEEP-100% repeats

| Repeat | Result | Completed generations | Wall | Retired tok/s | Solar calls | Solar input/output | Oracle | Runtime failure |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 01 | failed | 1/13 | 316.5 s | 36.2 | 2 | 25,774/2,555 | 4/1 prefix | timeout at generation 1 |
| 02 | failed | 10/13 | 907.6 s | 128.2 | 11 | 296,962/22,462 | 44/6 prefix | exact-ID unknown source ID at generation 10 |
| 03 | full | 13/13 | 551.1 s | 260.7 | 13 | 330,581/15,164 | 3/8 final | none |

All three KEEP runs recorded zero selector calls, zero selector input/output
tokens, and zero selector wall time. Solar ran serially with no added retry.
The all-source arm needed 13 Solar batches versus 12 in the selector arm
because the extra 5,521 source tokens crossed the existing 12K batching
boundary.

## Semantic comparison

The one complete KEEP run had final failures for allowed-floor,
connected-components, large-import-risk, stale-analysis, large-optimizer,
runtime-evidence, export-evidence, and audit-completion. It passed current
intent/rebase, adversarial-audit, and test-evidence. The selector control’s
three final runs aggregate to 23 PASS / 10 FAIL, with current-intent/rebase,
adversarial-audit, and test-evidence passing in all three runs.

Final oracle categories, shown as PASS/FAIL counts, are not a matched sample:
KEEP has only one complete run while SELECTOR has three.

| Frozen expectation | KEEP full run | Selector control, 3 runs |
|---|---:|---:|
| allowed-floor / persistent floor constraint | 0/1 | 2/1 |
| connected-component constraint | 0/1 | 1/2 |
| large-import blocker | 0/1 | 0/3 |
| current intent / rebase | 1/0 | 3/0 |
| adversarial-audit obligation | 1/0 | 3/0 |
| stale-analysis avoidance | 0/1 | 2/1 |
| test-evidence obligation | 1/0 | 3/0 |
| large-optimizer evidence | 0/1 | 3/0 |
| runtime evidence | 0/1 | 3/0 |
| export evidence | 0/1 | 2/1 |
| audit-completion obligation | 0/1 | 1/2 |

Structural validation passed for every successfully retired prefix and every
full selector-control run. Promotion was `PASSED` for all attempted roots,
with the successful prefix promoted where a run stopped. Resurrection and
invention failures were zero in every evaluated root. Exact source IDs were
mechanically preserved by KEEP-100%; repeat 02's unknown-ID rejection was a
Solar model/protocol failure caught by the existing exact-ID validation, not
an oracle miss.

## Throughput and economics

The selector control made 148 calls per run, with a median selector wall time
of 1,805.1 s, 261,050 input tokens, and 5,159 output tokens. Request latency
was approximately 11.0 s p50 and 19.8 s p95. KEEP-100% made no selector
requests.

For the one completed KEEP run versus the selector-control median, retaining
everything increased Solar input by 8.2% (330,581 vs 305,637), output by
23.1% (15,164 vs 12,319), and estimated Solar cost by 10.2% ($0.011737 vs
$0.010647). It nevertheless removed the measured 1,805 s local selector
stage. The observed full-run wall time was 551.1 s versus 2,034.4 s and the
observed retirement throughput was 260.7 versus 67.9 source tok/s. Those
speed figures are from one full KEEP run against three complete selector runs;
the 316.5 s and 907.6 s incomplete KEEP attempts demonstrate substantial
Solar/provider variance.

The completed KEEP run ended with an estimated 156-token capsule and a
2,054-token maximum intermediate capsule. Selector-control final capsule
size had a 324-token median and a 1,354-token maximum median. Capsule text
size varied with Solar output; there was no structural capsule-growth or
promotion failure attributable to KEEP-100%.

## Characterization of the discarded 3.8%

The selector discarded 33 source items / 5,521 estimated source tokens. This
was manually characterized from the frozen source spans, not classified by a
new model or rule:

- most were transient orchestration artifacts: wait calls/timeouts,
  progress messages, empty tool outputs, and run-state coordination;
- the remainder were diagnostic/tool-call payloads, code-inspection output,
  and test-command/wait telemetry.

Examples include repeated `wait_agent` timeouts around sequences 971–977,
wait/run-state artifacts around 1066–1083, audit progress chatter at 1070,
and test-command polling around 1169–1171. No discarded span was identified
in this inspection as a standalone current intent or large-import fact. This
supports the interpretation that the selector is mostly removing a narrow
class of low-value transient evidence, but it does not establish that those
removals caused the semantic difference in the incomplete experiment.

## Answers to the decision questions

1. The one completed KEEP run was semantically worse than the selector
   control, but 2/3 KEEP traces were incomplete. Selector filtering is not
   proven causally valuable by this run set.
2. The 3.8% discard set appears disproportionately transient/low-value, but
   its semantic importance is not established because the full KEEP sample is
   only one run.
3. The selector demonstrably costs about 1,805 s of local wall per run. This
   evidence does not show that the cost is currently worth paying, but the
   incomplete KEEP controls prevent a definitive value judgment.
4. The full KEEP run lost floor, connected-component, large-import, stale,
   optimizer, runtime/export, and audit-completion checks while preserving
   current intent, adversarial audit, and test evidence. The category counts
   are confounded by the unmatched complete-run counts.
5. Keeping the extra source increased Solar input and added one batch, but
   semantic contamination is not separable from Solar/provider variance here.
6. If the selector is valuable, the evidence it appears to remove is mainly
   transient waits, progress chatter, empty tool results, diagnostics, and
   test/run-state telemetry rather than broad durable project facts.

The two incomplete KEEP runs and the one complete run with materially
different Solar behavior meet the experiment's insufficient-evidence case.

INSUFFICIENT_EVIDENCE
