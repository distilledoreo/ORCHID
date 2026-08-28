from __future__ import annotations

from memory_gateway.publication_economics import (
    cached_prefix_tokens_from_report,
    estimate_publication_economics,
)


def test_publication_economics_accepts_when_savings_exceed_ratio() -> None:
    economics = estimate_publication_economics(
        compacted_source_tokens=10_000,
        promoted_capsule_tokens=2_000,
        min_savings_ratio=1.25,
    )
    assert economics.should_publish
    assert economics.reason == "publish:prompt_reduction"


def test_publication_economics_rejects_low_value_consolidation() -> None:
    economics = estimate_publication_economics(
        compacted_source_tokens=2_100,
        promoted_capsule_tokens=2_000,
        min_savings_ratio=1.25,
    )
    assert not economics.should_publish
    assert economics.reason == "defer:prompt_reduction"


def test_publication_economics_publishes_when_urgent() -> None:
    economics = estimate_publication_economics(
        compacted_source_tokens=2_100,
        promoted_capsule_tokens=2_000,
        recently_cached_prefix_tokens=20_000,
        urgent=True,
    )
    assert economics.should_publish
    assert economics.reason == "publish:urgent"


def test_publication_economics_defers_when_cache_invalidation_dominates() -> None:
    economics = estimate_publication_economics(
        compacted_source_tokens=10_000,
        promoted_capsule_tokens=2_000,
        current_prompt_tokens=12_000,
        recently_cached_prefix_tokens=20_000,
        horizon_turns=8,
        min_savings_ratio=1.25,
    )
    # 8 * 8000 = 64000 savings vs 1.25 * 20000 = 25000 reset → publish
    assert economics.should_publish
    expensive = estimate_publication_economics(
        compacted_source_tokens=3_000,
        promoted_capsule_tokens=2_500,
        current_prompt_tokens=20_000,
        recently_cached_prefix_tokens=18_000,
        horizon_turns=8,
        min_savings_ratio=1.25,
    )
    # 8 * 500 = 4000 savings vs 1.25 * 18000 = 22500 reset → defer
    assert not expensive.should_publish
    assert expensive.reason == "defer:cache_invalidation"


def test_publication_economics_prices_reset_cost_from_rate_delta() -> None:
    economics = estimate_publication_economics(
        compacted_source_tokens=4_000,
        promoted_capsule_tokens=1_000,
        current_prompt_tokens=10_000,
        recently_cached_prefix_tokens=8_000,
        uncached_input_usd_per_million=0.03,
        cached_input_usd_per_million=0.003,
        horizon_turns=8,
    )
    assert economics.reset_cost_tokens == 7_200
    assert economics.should_publish


def test_cached_prefix_tokens_prefers_reusable_then_stable() -> None:
    assert cached_prefix_tokens_from_report(None) is None
    assert cached_prefix_tokens_from_report({"reusable_prefix_tokens": 12}) == 12
    assert cached_prefix_tokens_from_report({"stable_prefix_tokens": 9}) == 9
    assert cached_prefix_tokens_from_report({"block_hashes": {}}) == 0


def test_publication_economics_publishes_when_cached_prefix_is_empty() -> None:
    economics = estimate_publication_economics(
        compacted_source_tokens=4_000,
        promoted_capsule_tokens=1_000,
        current_prompt_tokens=5_000,
        recently_cached_prefix_tokens=0,
    )
    assert economics.should_publish
    assert economics.reason == "publish:no_cached_prefix"


def test_publication_economics_token_ratio_without_current_prompt() -> None:
    accepted = estimate_publication_economics(
        compacted_source_tokens=10_000,
        promoted_capsule_tokens=1_000,
        current_prompt_tokens=0,
    )
    assert accepted.reason == "publish:token_ratio"
    rejected = estimate_publication_economics(
        compacted_source_tokens=2_000,
        promoted_capsule_tokens=1_900,
        current_prompt_tokens=0,
    )
    assert rejected.reason == "defer:token_ratio"
