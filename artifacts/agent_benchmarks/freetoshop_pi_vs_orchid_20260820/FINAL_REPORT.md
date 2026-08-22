# FreetoShop Long-Horizon Memory A/B

## Status

This was an exploratory paired run, n=1. It is not a completed six-hour comparison. The valid attempt ran for 90 minutes and was terminated early because the ORCHID arm stopped producing RPC or gateway work for approximately 43 minutes after a provider stream error and an accepted predeclared follow-up. No arm was manually rescued or restarted.

The initial invalid attempt was discarded before meaningful work because the ORCHID model configuration used a dummy API key. The valid attempt used Solar Pro through OpenRouter for both arms: `upstage/solar-pro4`. The earlier GPT-5.6/Sol configuration was not used for the valid run.

## Measured facts

- PI_NATIVE produced 192 assistant messages, 236 tool calls, and 24 tool failures in the raw RPC log. It had 4 provider errors (`terminated` once and `Upstream idle timeout exceeded` three times). No native `compaction_end` event was observed.
- ORCHID produced 86 assistant messages, 114 tool calls, and 6 tool failures. It had four `Stream ended without finish_reason` errors, then one settled event and one accepted follow-up with no subsequent model request.
- At the 90-minute checkpoint, PI_NATIVE was still advancing at 186 provider messages/228 tools. ORCHID remained at 86/114 from the 60-minute checkpoint onward.
- ORCHID created 85 compaction jobs/generations. One capsule was successfully promoted to ACTIVE. At stop, the database contained 1 promoted job, 41 queued jobs, 1 running job, and 42 stale jobs.
- ORCHID’s fresh database contained 172 events, 1 ACTIVE capsule, 0 long-term memories, and 0 provenance rows. No retrieved memory entered the event stream or ACTIVE.
- ORCHID attempted lexical cold retrieval 86 times. All 86 were `no_match`; there were 0 timeouts, 0 fail-open events, 0 would-inject memories, and 0 injected memories. Mean recorded cold retrieval time was 1.58 ms; maximum was 2.22 ms.
- ORCHID’s database was 42,766,336 bytes at stop. The gateway process had a measured working set of approximately 174 MB at an intermediate checkpoint; final peak local resource data was not captured.
- Observed raw RPC input tokens were 14,706,539 for PI_NATIVE and 17,262,397 for ORCHID. Provider usage reporting and ORCHID’s additional model-run accounting make this a measured comparison, not a normalized cost estimate.

### Goal progress and correctness

Neither arm completed a public milestone. Both added Clone Stamp-related raster code and each passed the focused raster-engine suite at 30/30 tests. Neither produced a buildable repository:

- PI_NATIVE fails typecheck/build because `clone` is not accepted by the existing `Tool` union. Its focused tool-registry test fails because the expected family list was not updated. Lint also reports five new unused Clone Stamp imports.
- ORCHID passes its focused tool-registry test at 3/3, but fails typecheck/build because `App.tsx` does not pass `cloneAlignMode` and `onCloneAlignModeChange` to `ToolOptionsBar`. Lint reports three new Clone Stamp errors.
- Both retain the historical 251-file format-check failure. The historical `selection-engine` 4096-square timeout remains. ORCHID additionally hit the existing/changed composite-worker timeout during its full suite.
- The hidden browser probe ran exactly one Clone Stamp test per arm after Chromium was installed post-run. Both failed at the same UI point: timeout waiting for the `Clone stamp` button. No end-to-end Clone Stamp workflow passed.

## Direct answers

1. Both agents did not operate for the intended six hours. The paired attempt ran 90 minutes.
2. Pi native compactions observed: 0.
3. ORCHID compaction jobs/generations created: 85; successful ACTIVE promotions: 1.
4. Neither arm produced an explicit context-limit error.
5. Both completed only a partial Clone Stamp foundation. Neither completed a public milestone; later milestones were not reached.
6. Neither passed the hidden Clone Stamp behavioral check: both scored 0/1.
7. PI_NATIVE used fewer observed non-cache Solar input tokens: 14.71M versus ORCHID’s 17.26M, with the reporting caveat above.
8. ORCHID incurred 42.8 MB of database storage plus gateway/background work; a reliable final CPU peak was not captured. Its database recorded 441 model runs and 5,433,188.6 ms of model-run wall time, which includes the memory pipeline and is not equivalent to local CPU time.
9. The isolated cold lookup was approximately 1.58 ms average and 2.22 ms maximum in this run. That lookup itself was not perceptible relative to a Solar request; the ORCHID continuation stall was materially perceptible and dominated the user-visible behavior.
10. No defensible semantic “forgetting” comparison was reached. PI did not compact during the observed window. ORCHID preserved one capsule and then failed to continue after a provider error.
11. ORCHID did not demonstrate a semantic memory distortion; it demonstrated a continuity/recovery failure.
12. No useful cold lexical memory was retrieved: the fresh corpus had zero RETIRE memories and all 86 searches returned no match.
13. No irrelevant cold memory was injected. This is not a precision result because there were no candidates.
14. Dense shadow retrieval was disabled from production and produced no production injections.
15. No reliable repeated-investigation comparison is available; ORCHID did not reach a second autonomous implementation phase.
16. No reliable undo/contradiction comparison is available. The concrete correctness failures were incomplete Clone Stamp integration and build/test regressions.
17. No system reached a complete milestone first. PI had a passing focused raster suite; ORCHID had a passing focused registry suite and a more developed visible options surface, but both failed build/e2e acceptance.
18. Neither produced a better final repository in the required sense. ORCHID was slightly further along in visible Clone Stamp UI modeling; PI had the same focused raster result with a different incomplete UI/type path. Neither was buildable.
19. ORCHID is not currently credible as a transparent Pi/OpenCode provider replacement for a long-running coding thread based on this run. The architecture remains testable, but the observed continuation failure is a release-blocking integration issue.

## Interpretation

This run did not measure the intended memory-quality question because the ORCHID thread produced no RETIRE objects before termination and Goal mode was disabled symmetrically. It did measure a more basic prerequisite: a memory proxy must recover from provider/API stream errors and continue the same autonomous work. In this attempt, ORCHID did not.

The result also does not show that native Pi compaction is superior. PI simply continued making requests while its own provider experienced idle timeouts; it had not reached a compaction boundary in the observed 90 minutes. The two systems therefore were not compared at equivalent context-turnover depth.

The cold-memory path itself was cheap and fail-open in the data captured. It did not pollute context, events, or ACTIVE, but it also had no semantic memories to retrieve. Retrieval quality cannot be inferred from this run.

## Limitations

- Exploratory paired n=1; early termination makes statistical claims inappropriate.
- Goal mode was disabled for both because the installed Pi had no verified Goal-mode extension/control-origin distinction.
- Agent subprocesses were not firewall-isolated from the public network, although remotes/future refs were removed and no target checkout was provided.
- Final CPU/peak-memory sampling was incomplete because the run was terminated after the ORCHID stall.
- The hidden browser test was post-hoc, not part of the timed agent run. Chromium had to be installed after the run.
- The target commit was used only by the controller-side evaluator and was not exposed to either agent.
- The committed target is an oracle for secondary comparison, not a source-similarity requirement; this report does not treat the partial diffs as a reconstruction score.

## Single next recommendation

Before another A/B run or any retrieval tuning, fix the provider-error continuation path. Add a controller-side preflight that deliberately exercises a failed stream followed by a queued follow-up and requires a new `message_start`/provider request within a bounded time, for both PI_NATIVE and ORCHID. Then rerun from fresh sealed repositories and fresh ORCHID state. Do not add dense retrieval or alter cold-memory policy until that long-horizon control loop passes.
