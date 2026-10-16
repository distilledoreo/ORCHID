"""Pareto-Dominance benchmark manifest extensions and pre-run validation."""

from __future__ import annotations

from typing import Any

from .core_goal import (
    CORE_CANONICALIZER_ENABLED,
    FOREGROUND_EMERGENCY_CEILING_TOKENS,
    FOREGROUND_TARGET_TOKENS,
)
from .selector_contract import contract_document, contract_sha256

REFERENCE_CLOUD_MODEL = "agnes-2.5-flash"
AGNES_PROVIDER_BASE_URL = "https://apihub.agnes-ai.com/v1"
SOLAR_PRO4_MODEL = "upstage/solar-pro4"
OPENROUTER_PROVIDER_BASE_URL = "https://openrouter.ai/api/v1"
BACKEND_CONTEXT_LIMIT_TOKENS = 128_000
INITIAL_COMPACTION_THRESHOLD_TOKENS = 8_000
FOREGROUND_HARD_LIMIT_TOKENS = FOREGROUND_EMERGENCY_CEILING_TOKENS
RECENT_TAIL_TARGET_TOKENS = 8_000
CAPSULE_MAX_TOKENS = 2_048

DEFAULT_RATE_CARD = {
    "provider": "Agnes",
    "model": REFERENCE_CLOUD_MODEL,
    # The benchmark account does not currently expose a trustworthy public
    # rate card. Zeroes are transport placeholders, not a claim that calls are
    # free; benchmark_scorecard marks this card as unknown and falls back to a
    # conservative token-class comparison.
    "pricing_known": False,
    "pricing_usd_per_million_tokens": {
        "input": 0.0,
        "output": 0.0,
    },
    "cached_input_usd_per_million": None,
    "uncached_input_usd_per_million": None,
}

SOLAR_PRO4_RATE_CARD = {
    "provider": "OpenRouter",
    "model": SOLAR_PRO4_MODEL,
    "pricing_known": True,
    "pricing_usd_per_million_tokens": {
        "input": 0.03,
        "output": 0.12,
    },
    # These values describe the sealed historical reference artifact. Keep
    # them separate from the active Agnes profile so compatibility tooling
    # cannot silently change providers when the reference model changes.
    "cached_input_usd_per_million": 0.0,
    "uncached_input_usd_per_million": 0.03,
}

DEFAULT_ARM_ORDER = ("PI_NATIVE", "ORCHID")


def pareto_manifest_extensions(
    *,
    arm_order: tuple[str, ...] = DEFAULT_ARM_ORDER,
    rate_card: dict[str, Any] | None = None,
    selector_inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rate_card = rate_card or DEFAULT_RATE_CARD
    selector_inventory = selector_inventory or {
        "device": "AMD Radeon RX 580",
        "selector_endpoint": "http://127.0.0.1:1234/v1",
        "max_vram_utilization_gate": 0.9,
        "fallback_to_cloud_forbidden": True,
    }
    return {
        "pareto_schema_version": "orchid.pareto_manifest.v1",
        "reference_cloud_model": REFERENCE_CLOUD_MODEL,
        "baseline_arm": "PI_NATIVE",
        "arm_order": list(arm_order),
        "context_limits": {
            "backend_context_limit_tokens": BACKEND_CONTEXT_LIMIT_TOKENS,
            "foreground_target_tokens": FOREGROUND_TARGET_TOKENS,
            "initial_compaction_threshold_tokens": INITIAL_COMPACTION_THRESHOLD_TOKENS,
            "foreground_hard_limit_tokens": FOREGROUND_HARD_LIMIT_TOKENS,
            "recent_tail_target_tokens": RECENT_TAIL_TARGET_TOKENS,
            "capsule_max_tokens": CAPSULE_MAX_TOKENS,
        },
        "rate_card": rate_card,
        "selector_contract": {
            "path": "artifacts/selector_contract/FROZEN_REQUEST_HASHES.json",
            "contract_sha256": contract_sha256(),
            "frozen": contract_document(),
        },
        "selector_inventory": selector_inventory,
        "canonicalizer_policy": {
            "enabled": CORE_CANONICALIZER_ENABLED,
            "frozen_gate_verdict": "KEEP",
        },
        "cold_memory_excluded": True,
        "required_paired_runs": 3,
        "gates": {
            "quality_noninferior": True,
            "api_cost_savings_fraction": 0.10,
            "prompt_kv_savings_fraction": 0.20,
            "peak_context_savings_fraction": 0.10,
            "total_peak_vram_savings_fraction": 0.10,
            "integrated_vram_savings_fraction": 0.10,
            "foreground_latency_median_noninferior": True,
            "foreground_latency_p95_worse_fraction": 0.05,
            "minimum_promotions": 3,
            "minimum_post_final_promotion_turns": 5,
            "selector_max_vram_fraction": 0.90,
        },
    }


def validate_manifest_for_run(
    manifest: dict[str, Any],
    *,
    orchid_config: dict[str, Any],
    pi_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    pareto = manifest.get("pareto") or manifest.get("pareto_manifest_extensions")
    if not pareto:
        checks["pareto_section_present"] = False
        reasons.append("manifest is missing pareto extensions")
    else:
        checks["pareto_section_present"] = True
        limits = pareto.get("context_limits") or {}
        checks["backend_context_limit_128k"] = (
            limits.get("backend_context_limit_tokens") == BACKEND_CONTEXT_LIMIT_TOKENS
        )
        checks["reference_model"] = (
            pareto.get("reference_cloud_model") == REFERENCE_CLOUD_MODEL
        )
        checks["selector_contract_sha"] = (
            (pareto.get("selector_contract") or {}).get("contract_sha256")
            == contract_sha256()
        )
        if not checks["backend_context_limit_128k"]:
            reasons.append("backend context limit is not 128k")
        if not checks["reference_model"]:
            reasons.append(f"reference cloud model is not {REFERENCE_CLOUD_MODEL}")
        if not checks["selector_contract_sha"]:
            reasons.append("selector contract fingerprint mismatch")

    checks["orchid_model"] = orchid_config.get("foreground_model") == REFERENCE_CLOUD_MODEL
    checks["canonicalizer_policy_aligned"] = (
        orchid_config.get("canonicalizer_enabled") is CORE_CANONICALIZER_ENABLED
    )
    checks["selector_chunk_target"] = orchid_config.get("selector_chunk_target_tokens") == 1_200
    checks["manual_rescue_forbidden"] = (
        (manifest.get("run_policy") or {}).get("manual_rescue") == "forbidden"
    )
    if not checks["orchid_model"]:
        reasons.append("orchid foreground model mismatch")
    if not checks["canonicalizer_policy_aligned"]:
        reasons.append(
            "canonicalizer runtime setting conflicts with the frozen KEEP decision"
        )
    if not checks["selector_chunk_target"]:
        reasons.append("selector chunk target is not 1200 tokens")
    if not checks["manual_rescue_forbidden"]:
        reasons.append("manual rescue is not forbidden")

    if pi_config is not None:
        checks["pi_native_compaction"] = pi_config.get("native_compaction") is True
        checks["pi_model"] = pi_config.get("foreground_model") == REFERENCE_CLOUD_MODEL
        if not checks["pi_native_compaction"]:
            reasons.append("pi arm is not native compaction")
        if not checks["pi_model"]:
            reasons.append("pi foreground model mismatch")

    return {
        "checks": checks,
        "all_ok": all(checks.values()),
        "reasons": reasons,
    }
