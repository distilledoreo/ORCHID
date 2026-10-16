"""Publication economics for separating candidate generation from promotion."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_HORIZON_TURNS = 8
DEFAULT_MIN_SAVINGS_RATIO = 1.25
CACHE_UNAVAILABLE_REDUCTION_FRACTION = 0.25


@dataclass(frozen=True)
class PublicationEconomics:
    future_savings_tokens: int
    reset_cost_tokens: int
    min_savings_ratio: float = DEFAULT_MIN_SAVINGS_RATIO
    horizon_turns: int = DEFAULT_HORIZON_TURNS
    current_prompt_tokens: int = 0
    candidate_prompt_tokens: int = 0
    recently_cached_prefix_tokens: int = 0
    cache_telemetry_available: bool = False
    urgent: bool = False
    reason: str = ""
    compacted_source_tokens: int = 0
    promoted_capsule_tokens: int = 0

    @property
    def prompt_reduction_tokens(self) -> int:
        return max(0, self.current_prompt_tokens - self.candidate_prompt_tokens)

    @property
    def should_publish(self) -> bool:
        return self.reason.startswith("publish:")


def _publish_reason(
    *,
    urgent: bool,
    cache_telemetry_available: bool,
    current_prompt_tokens: int,
    prompt_reduction_tokens: int,
    future_savings_tokens: int,
    reset_cost_tokens: int,
    min_savings_ratio: float,
) -> str:
    if urgent:
        return "publish:urgent"
    if not cache_telemetry_available:
        if current_prompt_tokens <= 0:
            if future_savings_tokens >= int(reset_cost_tokens * min_savings_ratio):
                return "publish:token_ratio"
            return "defer:token_ratio"
        threshold = int(current_prompt_tokens * CACHE_UNAVAILABLE_REDUCTION_FRACTION)
        if prompt_reduction_tokens >= threshold:
            return "publish:prompt_reduction"
        return "defer:prompt_reduction"
    if reset_cost_tokens <= 0:
        return "publish:no_cached_prefix"
    if future_savings_tokens >= int(reset_cost_tokens * min_savings_ratio):
        return "publish:cache_aware"
    return "defer:cache_invalidation"


def estimate_publication_economics(
    *,
    compacted_source_tokens: int,
    promoted_capsule_tokens: int,
    min_savings_ratio: float = DEFAULT_MIN_SAVINGS_RATIO,
    current_prompt_tokens: int | None = None,
    candidate_prompt_tokens: int | None = None,
    recently_cached_prefix_tokens: int | None = None,
    horizon_turns: int = DEFAULT_HORIZON_TURNS,
    uncached_input_usd_per_million: float | None = None,
    cached_input_usd_per_million: float | None = None,
    urgent: bool = False,
) -> PublicationEconomics:
    """Estimate whether promoting a candidate is net-positive for foreground budget.

    When provider cache telemetry is present, reset cost is the extra uncached
    bill for invalidating the currently cached prefix. Future savings are the
    prompt reduction over ``horizon_turns``. Without cache telemetry, publish
    only when the candidate cuts the projected prompt by at least 25%, or when
    urgent pressure applies.
    """

    source_tokens = max(0, compacted_source_tokens)
    capsule_tokens = max(0, promoted_capsule_tokens)
    resolved_current = (
        source_tokens if current_prompt_tokens is None else max(0, current_prompt_tokens)
    )
    if candidate_prompt_tokens is None:
        uncovered_after = max(0, resolved_current - source_tokens)
        resolved_candidate = uncovered_after + capsule_tokens
    else:
        resolved_candidate = max(0, candidate_prompt_tokens)
    prompt_reduction = max(0, resolved_current - resolved_candidate)
    cache_available = recently_cached_prefix_tokens is not None
    cached_prefix = max(0, recently_cached_prefix_tokens or 0)
    turns = max(1, int(horizon_turns))
    future_savings_tokens = prompt_reduction * turns
    if cache_available:
        reset_cost_tokens = cached_prefix
        uncached = uncached_input_usd_per_million
        cached = cached_input_usd_per_million
        if (
            isinstance(uncached, (int, float))
            and uncached > 0
            and isinstance(cached, (int, float))
            and 0 <= cached < uncached
        ):
            # Convert the priced comparison back into token units so one
            # threshold handles known and unknown rate cards.
            delta = float(uncached) - float(cached)
            reset_cost_tokens = int(round(cached_prefix * delta / float(uncached)))
    else:
        reset_cost_tokens = capsule_tokens
        future_savings_tokens = max(0, source_tokens - capsule_tokens)
    reason = _publish_reason(
        urgent=urgent,
        cache_telemetry_available=cache_available,
        current_prompt_tokens=resolved_current,
        prompt_reduction_tokens=prompt_reduction,
        future_savings_tokens=future_savings_tokens,
        reset_cost_tokens=reset_cost_tokens,
        min_savings_ratio=min_savings_ratio,
    )
    return PublicationEconomics(
        future_savings_tokens=future_savings_tokens,
        reset_cost_tokens=reset_cost_tokens,
        min_savings_ratio=min_savings_ratio,
        horizon_turns=turns,
        current_prompt_tokens=resolved_current,
        candidate_prompt_tokens=resolved_candidate,
        recently_cached_prefix_tokens=cached_prefix,
        cache_telemetry_available=cache_available,
        urgent=urgent,
        reason=reason,
        compacted_source_tokens=source_tokens,
        promoted_capsule_tokens=capsule_tokens,
    )


def cached_prefix_tokens_from_report(cache: dict[str, object] | None) -> int | None:
    """Return recently cached prefix tokens, or None when telemetry is absent."""

    if not cache:
        return None
    reusable = cache.get("reusable_prefix_tokens")
    stable = cache.get("stable_prefix_tokens")
    if isinstance(reusable, (int, float)) and reusable > 0:
        return int(reusable)
    if isinstance(stable, (int, float)) and stable > 0:
        return int(stable)
    if "block_hashes" in cache or "stable_prefix_hash" in cache:
        return 0
    return None


def economics_to_dict(economics: PublicationEconomics) -> dict[str, object]:
    return {
        "future_savings_tokens": economics.future_savings_tokens,
        "reset_cost_tokens": economics.reset_cost_tokens,
        "min_savings_ratio": economics.min_savings_ratio,
        "horizon_turns": economics.horizon_turns,
        "current_prompt_tokens": economics.current_prompt_tokens,
        "candidate_prompt_tokens": economics.candidate_prompt_tokens,
        "recently_cached_prefix_tokens": economics.recently_cached_prefix_tokens,
        "cache_telemetry_available": economics.cache_telemetry_available,
        "urgent": economics.urgent,
        "reason": economics.reason,
        "should_publish": economics.should_publish,
        "compacted_source_tokens": economics.compacted_source_tokens,
        "promoted_capsule_tokens": economics.promoted_capsule_tokens,
    }
