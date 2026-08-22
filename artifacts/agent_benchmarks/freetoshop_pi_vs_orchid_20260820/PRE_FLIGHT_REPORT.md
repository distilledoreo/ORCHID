# Pre-flight report

Status at this checkpoint: ready to launch; no timed benchmark run has started.

## Measured facts

- `START_COMMIT` exists and is an ancestor of the canonical repository `HEAD`.
- The committed target `HEAD` is recorded privately as `71ed699e605ddfcec6f172d529040f39544dcc8f`.
- Both sealed arm repositories were exported from `START_COMMIT`, re-initialized without an origin, and have the same source tree fingerprint: `1507e5b4565e3c86d98bb245ea3fad29801989c3`.
- The public goal is byte-identical in both arms. SHA-256: `D789F01C840878946C1CD885BCD48DFB4F96B4DB2EAFB82615DFDD4064290E7E`.
- Dependencies installed successfully in both arms: 251 packages.
- Historical baseline results are identical:
  - typecheck: pass
  - lint: pass
  - build: pass
  - E2E test listing: pass
  - format check: pre-existing failure with 251 formatting warnings/files
  - unit tests: 509/510 passed; one pre-existing selection-engine timeout
- Pi version is `0.84.2`.
- ORCHID `python -m compileall -q memory_gateway tests` passed, and `git diff --check` passed; the latter emitted only existing CRLF normalization warnings.
- The provider is OpenRouter using model `upstage/solar-pro4`. Direct OpenRouter, Pi-native, and ORCHID-gateway smoke calls all returned `READY`.
- ORCHID's final benchmark database (`orchid-state-actual-2/memory.db`) was recreated after all provider/RPC smoke testing and the discarded invalid attempt. Initial counts are events=0, threads=0, long_term_memories=0. No prior FreetoShop or ORCHID benchmark memory is being reused.

## Provider correction

The initial setup mistakenly selected Pi's `openai-codex/gpt-5.6-sol` catalog entry. That was corrected before launch. It was an OpenAI Codex OAuth route, not Solar Pro through OpenRouter. The frozen configuration now uses OpenRouter's documented `upstage/solar-pro4` slug for both arms, with the API key supplied only through the user environment and excluded from artifacts.

## Configuration

PI_NATIVE uses Pi's native automatic compaction. ORCHID disables Pi compaction and uses the current validated ORCHID hot-memory path, fresh thread-local state, buffered telemetry, and the existing bounded lexical cold-memory injection path. Dense retrieval, reranking, RRF, graphs, raw-history fallback, and retrieval-driven ACTIVE mutation are disabled/offline-only.

Pi Goal mode is disabled for both arms. The installed Pi has no Goal-mode extension, and the required control-origin guarantee could not be proven from the available integration. Enabling it for only ORCHID would be invalid. The controller sends the same exact ordinary follow-up prompt to both arms when a settled session remains within the run window; those prompts are not treated as Goal-mode control events.

## Isolation

The agent repositories contain no post-start Git refs, remotes, reflogs, alternates, or future objects. The canonical repository and controller-private evaluator are outside their working directories. Dependencies were installed before launch. Browser cache sharing is treated as immutable cache reuse.

Strong outbound network denial is not enabled in this environment. This is an exploratory limitation: agents could technically reach public network resources. The benchmark will audit runtime command logs and will be invalidated if an agent intentionally queries later FreetoShop source/history or otherwise receives answer-key material.

## Evaluator

A controller-private hidden evaluator is prepared. After the run it will execute a black-box Chromium Clone Stamp workflow derived from the committed oracle behavior against each arm, without exposing the test to either agent during the run. Public tests, builds, diffs, checkpoints, Pi RPC logs, ORCHID debug state, and target comparison remain secondary evidence rather than textual-diff scoring.

## Launch gate

The provider identity, source equality, fresh ORCHID state, asymmetric-compaction configuration, and RPC driver have all passed preflight. The run may now launch with the common six-hour wall-clock limit, paired start, 15-minute checkpoints, and at most one automatic process restart per arm.

One earlier launch attempt is explicitly excluded in `prelaunch_invalid_attempt_01.json`: it stopped before meaningful work after a 401 caused by a controller provider-header configuration error. The corrected run uses the same OpenRouter credential in both Pi provider paths.
