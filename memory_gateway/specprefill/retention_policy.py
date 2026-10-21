"""Revised retention policy v2 (quality-first, frozen after fresh validation).

Rules (applied in order, no extra model inference):
1. BYPASS: inputs shorter than BYPASS_TOKENS go full — selection overhead
   dominates and short-prompt selection has failed with evidence present
   (task_shift/t1 keep30 abstention at 477 tokens).
2. FLOOR: never retain fewer than FLOOR_TOKENS target tokens — protects the
   intermediate range from whole-chunk-ceil collapse (5% -> 3 tokens on a
   515-token prompt).
3. Otherwise the frozen mass-0.80 adaptive rate, with the retained-token
   count raised to the floor when it falls short.

BYPASS_TOKENS=1024, FLOOR_TOKENS=160: set from v1 failure analysis BEFORE
fresh validation (see sessions-v2-REPORT). The floor keeps >=10 chunks on
512-token prompts at worst; the bypass covers all sub-1k turns where the
pipeline never beats full prefill anyway.
"""

from __future__ import annotations

BYPASS_TOKENS = 1024
FLOOR_TOKENS = 160
TARGET_MASS = 0.80


def decide(input_tokens: int, adaptive_rate: float) -> tuple[str, float | None]:
    """Return (arm, rate). arm in {full, keepXX}; rate None means full prefill."""
    if input_tokens < BYPASS_TOKENS:
        return "full", None
    return f"keep{adaptive_rate * 100:g}", adaptive_rate


def enforce_floor(retained: int, rate: float) -> tuple[str, float | None]:
    """Bump a too-small selection up to full (floor as a bypass, not a top-up).

    Topping up with arbitrary extra chunks would add unranked tokens the
    student did not select; falling back to full is the safe, auditable
    behavior. Returns (arm, rate) with rate None on fallback.
    """
    if retained >= FLOOR_TOKENS:
        return f"keep{rate * 100:g}", rate
    return "full", None
