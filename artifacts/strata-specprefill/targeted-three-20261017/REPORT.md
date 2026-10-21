# Targeted exact-evidence counterfactuals

All three previously failing questions recover: unchanged prompts score **3/6 fields**, while prompts with bounded exact archive evidence score **6/6**. Each unchanged prompt reproduced its previous answer exactly.

| Turn | Unchanged prompt | Exact evidence appended | Expected | Added tokens |
|---|---|---|---|---:|
| 4 | `217379,UNKNOWN` | `217379,385680` | `[217379, 385680]` | 274 |
| 19 | `320624,618409` | `320624,731401` | `[320624, 731401]` | 246 |
| 21 | `UNKNOWN,265615` | `436414,265615` | `[436414, 265615]` | 254 |

## Intervention

A deterministic experimental resolver binds each explicitly named function to its own file and revision, independent of summary-nominated retrieval scope. It reads the exact original definition from the source archive. When the question explicitly requests ROUTE_KEY traversal, it follows the literal archived link to the referenced revision and function. Visited triples and a 32-unit limit terminate cycles; the complete evidence frame must fit the existing 2,048-token allowance. Oversized units are rejected without truncation.

The block is appended immediately before the original question. Its headers identify immutable source ID, path, revision, and source-body SHA256; its instruction tells the target to use the requested revisions rather than earlier summary claims or cached current/historical labels. Existing historical prompt content is unchanged. The resolver receives the question and only sources available at that turn; expected values are used solely for scoring.

The same native executable/configuration and 64-token answer cap are used. There are six live target calls, one baseline and one combined intervention per question. No student model is loaded for these calls.

## Reconstruction and verification

All 22 prior turns were reconstructed using frozen logged expanded/delivered chunk decisions, original replies, and the original compaction summary. Every target input length matched, the compaction seed event matched, and final student and target token arrays matched the saved checkpoint exactly. This reconstructs prior histories; it does not rerun student attention or prove new student selection behavior.

Resolver checks: four tests pass, covering distinct revision binding, source-link cycles, missing-revision rejection, and token-budget rejection. Ruff passes. All six live calls completed and GPU memory returned to zero.

## Interpretation and limits

This shows that exact question-bound retrieval and a nearby revision-labelled evidence block can recover all three residual errors on their frozen histories. The combined intervention does not separate the effects of repetition, proximity, revision wording, and retrieval routing.

**This is not a new 44/44 full-session result.** Earlier answers and the compaction summary were frozen, and exact definitions bypassed student filtering for final target delivery. Production integration must preserve this delivery path within budgets, handle unsupported references safely, and be evaluated in a full session with new replies and summaries. These isolated historical prompts rebuild context, so their call timings are not comparable to the cached multi-turn benchmark.

Raw prompts, implementation snapshot, replay databases, and live calls: [/home/distilledoreo/.cache/orchid-three-way-28k/targeted-three-20261017](/home/distilledoreo/.cache/orchid-three-way-28k/targeted-three-20261017).
