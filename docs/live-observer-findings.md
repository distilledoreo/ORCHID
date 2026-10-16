# Live observer findings (2026-10-12)

The separate `upgrade/observer-live-readiness` research branch took the observer from the memory upgrade
(phases 0-5) and ran it against real models for the first time. This report and its grading fixtures are
preserved here from commit `9dbb077a8d479fb365114bca3ac2d2f24d64d170`; that branch's experimental code
remains unmerged. It records what broke, the fixes tried there, and the result of one quality test. The
itemized observer, as tested, is not worth adopting as the only memory. Fixes listed below describe the
research branch and must not be read as guarantees about the integrated runtime.

## Setup

- **Input:** a recorded Pi coding session on FreetoShop (213 events, about 210,000 observable tokens),
  replayed through the observer. No agent ran; the session was only realistic input.
- **Observer models:** Qwen3.8-Flash-Next on a Tesla P100 (Strata), then FrogNano-4B Q6_K on an RX 580
  (llama.cpp, Vulkan). Both read at about 320 tokens/s and write at about 33 tokens/s.
- Strata checks only that a structured reply is one JSON object; llama.cpp enforces the schema.

## What broke live, and the fix

| Symptom | Cause | Fix on this branch |
| --- | --- | --- |
| Three core-goal tests failed in CI | Manifest sealed against a CRLF copy of one evidence file | Resealed against LF bytes |
| Items rejected although the quote was verbatim | Model-supplied character offsets were wrong | Software locates the quote; whitespace may differ |
| Memory held only the harness system prompt | System and developer messages were observed | They are covered but never sent |
| Stuck on a dense message | Reply exceeded the output cap, retried identically | Tighter retry, then split the input |
| One bad entry discarded a whole reply | All-or-nothing validation | Operations validated one by one |
| Most requirements discarded | Model used kinds outside the schema ("constraint") | Goal and constraint added; unknown kinds stored as fact |
| Design documents yielded nothing | Asked about a whole document at once | Long tool output read in parts |
| Memory froze past its budget | Reflection reply always exceeded the output cap | Reflection lists only changes; software evicts by priority when it fails |
| Every addition rejected on the 4B model | It filled `target_id` on plain additions | A wrong target makes an addition, never a rejection |

## Still wrong

- Reflection has never completed through a model. Budget is held only by priority eviction.
- A batch whose only proposals have inexact quotes fails forever (the last 10 of 213 events).
- Identical claims from different events are stored twice (11 duplicates in the full run).
- About one proposed item in three is lost to inexact quotes on the 4B model.
- The observer read 2.8 tokens per token of history; each call resends instructions, schema and items.

## Full-session run (FrogNano on the RX 580)

| | Observer | Rolling summary, same model |
| --- | ---: | ---: |
| Events covered | 203 of 213 | 203 of 203 requested |
| Time | 52 min | 38 min |
| Model calls | 120 | 26 |
| Tokens read / written | 589,000 / 49,000 | 271,000 / 45,000 |
| Final memory | 2,030 tokens (40 visible of 93 items) | 1,505 tokens |

The point where the legacy pipeline built a 547,802-token consolidator request (near event 106) passed as
ordinary small calls.

## Quality test

Twenty-two questions with answer keys written from the transcript. Flash Next answered each from three
starting points; answers were graded 0, 0.5 or 1 with the starting point hidden.

| Question type | Observer memory | Rolling summary | Nothing |
| --- | ---: | ---: | ---: |
| What the user asked for (8) | 2.5 | 6.5 | 0 |
| What was done and what went wrong (6) | 4.0 | 3.5 | 0 |
| State of the work at the end (4) | 1.5 | 4.0 | 0 |
| Codebase facts (2) | 1.0 | 0 | 0 |
| Not in the session (2) | 2.0 | 2.0 | 2.0 |
| **Total (22)** | **11** | **16** | **2** |

The summary held the user's intent and the current state of the work. The observer held specifics the
summary dropped (missing type names, a test failure's cause, size limits). Neither invented answers.

### Limits

- One session, 22 questions, one grader.
- Blinding was imperfect: observer-based answers often cite item IDs.
- The questions were written after the observer's memory had been seen, which would favour the observer.
- Ten of the summary's 26 calls were cut off at the output cap.
- Raw recall was not part of the test. Sessions spanning days were not tested.

## Reading of the result

Moving compaction off the foreground GPU saves an estimated two to four minutes on a session of this
size, and a plain summary on the spare GPU gets the same saving. On quality, the rolling summary was
better and cheaper. If this work continues, the evidence points to a hybrid: keep the user's goal and a
small rolling "current state" section as a summary does, and use the itemized ledger for the specifics a
summary drops. That is a redesign, and it is untested.

The questions and grades are in `artifacts/memory_upgrade/live/`.
