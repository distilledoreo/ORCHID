# Structural Noise Prefilter Generalization Test

Status: complete. This was a benchmark-only experiment. No production ORCHID
code, ingestion behavior, selector policy, chunk size, concurrency, Solar
prompt, lineage rule, or oracle was changed.

## Frozen ruleset

Ruleset: `RULESET.json` / `RULESET.md`

Rules:

1. Drop explicitly typed mechanical `wait`, `wait_agent`, `poll`, or `sleep`
   calls whose arguments contain only wait-control keys.
2. Drop typed tool outputs whose payload is exactly empty.
3. Drop a typed output linked to a wait call only when its parsed payload has
   exactly `{message, timed_out}` with string/boolean values.

Everything ambiguous is KEEP. Code, commands, source inspection, diagnostics,
compiler/test output, file contents, implementation reasoning, user/assistant
state, and coordination artifacts are not matched.

Ruleset SHA-256:
`482daeefb7f2d238e8884b3426cdc4dbb53e1302cb65726955acff8652808043`

The ruleset was committed before semantic execution at
`90307406d8c581f221193b2fd1926406f0172699`.

## PanoRef decision audit

Frozen source universe: 346 items / 143,670 estimated source tokens.

| Decision/category | Items | Source tokens |
|---|---:|---:|
| Deterministic removed | 75 | 10,175 (7.082%) |
| `explicit_mechanical_wait_call` | 48 | 6,727 |
| `typed_wait_protocol_result` | 22 | 2,873 |
| `empty_typed_tool_output` | 5 | 575 |
| Historical Qwen discarded | 33 | 5,521 (3.843%) |
| Deterministic ∩ Qwen discarded | 19 | 2,558 |
| Deterministic-only (Qwen kept) | 56 | 7,617 |
| Qwen-only (deterministic kept) | 14 | 2,963 |

The authoritative per-item values are preserved in
`preflight_panoref/STRUCTURAL_AUDIT.json` and each run's
`STRUCTURAL_AUDIT.json`.

The deterministic-only set was structurally mechanical on read-only audit:
36 wait calls, 17 exact typed wait results, and 3 empty typed outputs. It did
not contain code, diagnostics, tests, or source inspection. Nevertheless, its
removal is a semantic risk because Qwen historically kept those items and the
final oracle degraded when they were removed.

## PanoRef comparison

`Oracle P/F` is the aggregate final-checkpoint oracle across the listed full
runs. The all-checkpoint totals are reported below separately because the
frozen oracle has multiple checkpoints per generation.

| Arm | Removed % | Full runs | Median wall | Tok/s | Oracle P/F |
|---|---:|---:|---:|---:|---:|
| Qwen selector → Solar | 3.843% | 3/3 | 2,034.4 s | 67.9 | 23/10 |
| KEEP-100% control | 0% | 1/3 | 551.1 s* | 260.7* | 3/8* |
| Structural prefilter → Solar | 7.082% | 3/3 | 311.3 s | 428.8 | 14/19 |

`*` KEEP-100% values are the one completed full historical repeat; the two
other repeats failed before full trace completion.

PanoRef all-checkpoint totals:

- Qwen selector: 125 PASS / 73 FAIL across 198 checks.
- Structural prefilter: 122 PASS / 76 FAIL across 198 checks.

The final-checkpoint comparison is more discriminating: structural filtering
was below Qwen in every repeat (5/11, 5/11, 4/11 versus 6/11, 7/11, 10/11).
The difference is accompanied by a consistent category pattern:

| PanoRef final category | Qwen | Structural |
|---|---:|---:|
| Current intent (`rebase`) | 3/3 | 3/3 |
| Allowed-floor architecture fact | 1/3 | 0/3 |
| Connected-component constraint | 1/3 | 0/3 |
| Large-import blocker | 0/3 | 1/3 |
| Stale-analysis evidence | 2/3 | 0/3 |
| Audit/subagent continuation | 3/3 | 3/3 |
| Test-count verification | 3/3 | 3/3 |
| Runtime/performance evidence | 3/3 | 0/3 |
| Export/byte-size evidence | 2/3 | 0/3 |
| Final-round continuation | 1/3 | 3/3 |

The exact-ID response hardening and structural validator were unchanged. All
three full PanoRef structural runs passed 12/12 structural generations and
the isolated promotion check. That is a lineage/validation fact, not evidence
that semantic retention was preserved.

## FreetoShop holdout

Frozen source universe: 317 items / 202,761 estimated source tokens.

| Decision/category | Items | Source tokens |
|---|---:|---:|
| Deterministic removed | 0 | 0 (0%) |
| Historical Qwen discarded | 19 | 4,371 (2.156%) |
| Deterministic ∩ Qwen discarded | 0 | 0 |
| Deterministic-only | 0 | 0 |
| Qwen-only | 19 | 4,371 |

No FreetoShop event matched the frozen wait/empty/typed-wait predicates. No
potential useful-content removal was observed. The result is a holdout safety
check, but the filter provided no holdout workload reduction.

| Arm | Removed % | Full runs | Median wall | Tok/s | Oracle P/F |
|---|---:|---:|---:|---:|---:|
| Qwen selector → Solar | 2.156% | 1/1 | 2,884.9 s | 68.8 | 11/8* |
| KEEP-100% control | not separately recorded | — | — | — | — |
| Structural prefilter → Solar | 0% | 1/3 | 462.2 s** | 438.7** | 10/9** |

`*` Existing frozen FreetoShop selector result, one full run.

`**` One full structural run. The other two structural repeats are preserved
partial runs ending in the existing exact-ID protocol failure: 9/18 and 5/18
generations. No timeout occurred. The full structural run passed 18/18
structural generations and promotion.

The full FreetoShop final oracle is close in the one completed sample (10/9
structural versus 11/8 Qwen), but this is not enough to claim semantic parity.
The important measured holdout result is that the frozen filter did nothing.

## Throughput and economics

PanoRef structural selection used zero Qwen calls, zero selector input/output
tokens, and zero selector wall time. Median Solar input/output was 287,776 /
10,818 tokens versus 305,637 / 12,319 for the Qwen arm. Median end-to-end wall
fell from 2,034.4 s to 311.3 s because the approximately 1,800 s local
selector stage disappeared. Median selected-source retirement throughput rose
from 67.9 to 428.8 tok/s.

FreetoShop likewise used zero selector calls. The completed structural run
sent all 202,761 source tokens to Solar, with 401,693 Solar input and 9,966
output tokens. The historical Qwen arm sent 198,390 selected source tokens,
used 230 selector calls / 2,115.24 s local selector wall, and had 405,044
Solar input tokens. The apparent wall/throughput advantage is therefore the
cost of bypassing Qwen, not a filtering gain on FreetoShop.

## Reliability and failure classification

- PanoRef Qwen: 3/3 full; structural: 3/3 full; no structural-arm timeouts.
- FreetoShop structural: 1/3 full; two partial runs failed with
  `direct consolidator returned an unknown source ID` at generations 9 and 5.
  These are existing exact-ID/provider-output protocol failures, not
  deterministic-filter removals and not timeouts.
- No resurrection or invention failures occurred in PanoRef structural runs.
  The one full FreetoShop structural run had one resurrection failure and zero
  invention failures.

## Hashes and preservation

All new structural runs used commit
`90307406d8c581f221193b2fd1926406f0172699` and ruleset hash
`482daeefb7f2d238e8884b3426cdc4dbb53e1302cb65726955acff8652808043`.
Per-run benchmark configuration hashes are preserved in `RUN_METADATA.json`:

| Trace/repeat | Config hash |
|---|---|
| PanoRef 01 | `aaafb38481505b42162323ad38541159537c8a40b916e4f24af50b98e9c5c310` |
| PanoRef 02 | `3fa6e974a77d148dd8843d616c760ed79676d87c4d40345d5e9a5838039aca3a` |
| PanoRef 03 | `662e689e3b716190b0ab5608dc4bb00ff62a149165fe4e7e4cee5f04f346baaf` |
| FreetoShop 01 | `2a20ee2c76ae8103b3adb976ec9bf95907e18b7378dc7fea493d62e96b56cfaa` |
| FreetoShop 02 | `e45d6306fb7c2e104d132b3f3d82c24a4ca9288accd2e59bc10968e8c3f2669d` |
| FreetoShop 03 | `cd2f81967af22ffb255cc42d57c31b92332e104a2fe45ee23a27a8dcf6102eb7` |

Frozen replay, oracle, and raw-plan hashes remain in each run metadata and
match the existing apparatus. Historical PanoRef controls retain their own
commits (`5afba2c...` Qwen and `7a5750a...` KEEP-100%).

## Interpretation

Measured facts support three conclusions:

1. The rules are cheap, auditable, and did not false-positive on the untouched
   FreetoShop schema.
2. On PanoRef they did not explain the selector decision set: only 19/33
   historical Qwen discards overlapped, while 56 deterministic removals were
   items Qwen kept.
3. Removing those structurally mechanical items was not semantically neutral
   under the frozen PanoRef oracle. Final retention was 14/33 versus 23/33 for
   Qwen across the matched three full repeats, with losses in connected/floor
   constraints, stale-analysis evidence, runtime/export evidence, and
   continuation facts.

Therefore the experiment does not justify replacing the generative selector
with this ruleset. It also does not justify claiming the ruleset is generally
unsafe for every workload: FreetoShop had no matching events. It does show
that the current broad wait-removal abstraction is unsafe as a semantic
substitute on PanoRef and has no demonstrated holdout utility.

## Next hypothesis only

Test whether an exact-empty-output-only deterministic prefilter preserves the
PanoRef final oracle while retaining the near-zero selector cost; do not
implement or tune it in this phase.

STRUCTURAL_PREFILTER_OVERFIT_OR_UNSAFE
