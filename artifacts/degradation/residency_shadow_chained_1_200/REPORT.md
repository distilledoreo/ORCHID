# Chained Residency Shadow Replay 1-200

## Methodology

Verified logical-input chained shadow replay using frozen `residency_shadow_v1`.
Shadow ACTIVE from generation N-1 feeds forward as base capsule for generation N.
Canonicalizer output_hash replay verified per generation; not byte-identical consolidator replay.

**Prompt bundle hash:** `124048d4f7475a2ec70141d8483026ee2d77817b93682ee38a65f0dcac89068c`
**Completed through generation:** 200
**Experiment passed:** True

## Current-state correctness

- Current fact loss: **0**
- Semantic resurrection: **0**
- Invented state: **0**

## ACTIVE growth

- Gen-200 shadow ACTIVE tokens: **113**
- Gen-200 original tokens: **757**
- Gen-200 reduction: **85.1%**
- Peak shadow ACTIVE: **114**
- Late-run mean ACTIVE (126-200): **113**
- Late-run growth slope: **0.0** tok/gen
- Plateau observed: **True**

## Residue

- Gen-200 original residue: **96**
- Gen-200 shadow residue: **2**
- Recursive accumulation prevented: **True**

## RETIRE / RAW_ONLY

- Total RETIRE entries: **3**
- Total RAW_ONLY groups: **595**

## Gemini usage

- Input tokens: **1407332**
- Output tokens: **154829**
- Runtime: **4951.9s**

## Recommendations

- Production residency semantics justified: **True**
- RETIRE policy tuning before SSD: **True**

## Per-generation metrics (selected)

| Gen | Orig | Shadow | Residue O/S | RETIRE | RAW |
|-----|------|--------|-------------|--------|-----|
| 1 | 94 | 85 | 0/0 | 0 | 2 |
| 10 | 102 | 85 | 2/1 | 0 | 3 |
| 25 | 149 | 114 | 4/1 | 0 | 3 |
| 50 | 192 | 113 | 10/1 | 0 | 3 |
| 75 | 261 | 113 | 19/1 | 0 | 3 |
| 100 | 403 | 113 | 36/2 | 0 | 3 |
| 125 | 517 | 113 | 52/2 | 0 | 3 |
| 150 | 584 | 113 | 67/2 | 0 | 3 |
| 175 | 691 | 113 | 84/2 | 0 | 3 |
| 200 | 757 | 113 | 96/2 | 0 | 3 |