"""Pareto-Dominance release gate evaluation for paired FreetoShop runs."""

from __future__ import annotations

import math
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .benchmark_manifest import (
    AGNES_PROVIDER_BASE_URL,
    REFERENCE_CLOUD_MODEL,
    pareto_manifest_extensions,
)
from .benchmark_scorecard import (
    TOKEN_CLASS_KEYS,
    compare_api_token_classes,
    generate_scorecard,
    load_json,
    load_jsonl,
)
from .selector_contract import contract_sha256, verify_golden_hashes


def provider_profile_ready(
    *,
    api_key: str | None = None,
    model_id: str | None = None,
    base_url: str | None = None,
) -> bool:
    """True when benchmark.env targets the sealed Agnes reference profile."""

    resolved_key = api_key or os.environ.get("BENCHMARK_PROVIDER_API_KEY")
    resolved_model = model_id or os.environ.get("BENCHMARK_MODEL_ID")
    resolved_base = (base_url or os.environ.get("BENCHMARK_PROVIDER_BASE_URL") or "").rstrip("/")
    return bool(
        resolved_key
        and resolved_model == REFERENCE_CLOUD_MODEL
        and resolved_base == AGNES_PROVIDER_BASE_URL.rstrip("/")
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _parse_timestamp_ms(raw: Any) -> int | None:
    if isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    if not isinstance(raw, str) or not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    except ValueError:
        return None


def _milestone_scores(scorecard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    milestones = (scorecard.get("quality") or {}).get("milestones") or []
    by_arm: dict[str, dict[str, Any]] = {}
    for row in milestones:
        if isinstance(row, dict) and isinstance(row.get("arm"), str):
            by_arm[row["arm"]] = row
    return by_arm


def orchid_release_gate_complete(
    gate_status: str,
    *,
    post_final_promotion_turns: int,
    minimum_post_final_promotion_turns: int = 5,
) -> bool:
    """True when ORCHID passed the clean-promotion gate and post-final turns."""
    return (
        gate_status == "PASSED"
        and post_final_promotion_turns >= minimum_post_final_promotion_turns
    )


def count_post_final_promotion_turns(artifact: Path) -> int:
    """Count orchid assistant turns after the final clean promotion."""
    promotions = load_jsonl(artifact / "memory_transitions/orchid_promotions.jsonl")
    promoted = [
        row
        for row in promotions
        if row.get("status") == "PROMOTED" and row.get("clean_promotion") is not False
    ]
    if not promoted:
        return 0
    last_promotion = max(
        promoted,
        key=lambda row: _parse_timestamp_ms(row.get("finished_at")) or 0,
    )
    boundary_ms = _parse_timestamp_ms(last_promotion.get("finished_at"))
    if boundary_ms is None:
        return 0

    def _count_after(boundary: int, *, substitution: bool) -> int:
        if substitution:
            path = artifact / "orchid/foreground_substitution.jsonl"
            if not path.exists():
                return 0
            rows = load_jsonl(path)
        else:
            rows = load_jsonl(artifact / "orchid/telemetry.jsonl")
        turns = 0
        for row in rows:
            if substitution:
                timestamp_ms = _parse_timestamp_ms(row.get("timestamp"))
            else:
                if row.get("type") != "message_end":
                    continue
                message = row.get("message")
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                timestamp_ms = _parse_timestamp_ms(message.get("timestamp"))
            if timestamp_ms is not None and timestamp_ms >= boundary:
                turns += 1
        return turns

    substitution_turns = _count_after(boundary_ms, substitution=True)
    if substitution_turns:
        return substitution_turns
    assistant_turns = _count_after(boundary_ms, substitution=False)
    if assistant_turns:
        return assistant_turns

    started_ms = _parse_timestamp_ms(last_promotion.get("started_at"))
    if started_ms is None or started_ms >= boundary_ms:
        return 0
    return _count_after(started_ms + 1, substitution=False)


def _foreground_prompt_tokens(scorecard: dict[str, Any]) -> float:
    substitution = scorecard.get("foreground_substitution") or {}
    if substitution.get("present"):
        return float(
            substitution.get("sum_provider_prompt_tokens")
            or substitution.get("sum_provider_input_tokens")
            or 0
        )
    return float(
        scorecard.get("orchid", {}).get("prompt_tokens")
        or scorecard.get("orchid", {}).get("input_tokens")
        or 0
    )


def _pi_foreground_prompt_tokens(scorecard: dict[str, Any]) -> float:
    return float(
        scorecard.get("pi_native", {}).get("prompt_tokens")
        or scorecard.get("pi_native", {}).get("input_tokens")
        or 0
    )


def _orchid_foreground_peak_tokens(scorecard: dict[str, Any]) -> float:
    substitution = scorecard.get("foreground_substitution") or {}
    orchid_foreground = (scorecard.get("economics") or {}).get("orchid_foreground") or scorecard.get("orchid") or {}
    rpc_peak = float(
        orchid_foreground.get("largest_prompt_tokens")
        or orchid_foreground.get("largest_context_tokens")
        or 0
    )
    if not substitution.get("present"):
        return rpc_peak
    active_peak = float(
        substitution.get("peak_active_provider_prompt_tokens")
        or substitution.get("peak_active_provider_input_tokens")
        or 0
    )
    substitution_peak = float(
        substitution.get("peak_provider_prompt_tokens")
        or substitution.get("peak_provider_input_tokens")
        or 0
    )
    if active_peak > 0:
        return min(rpc_peak, active_peak) if rpc_peak > 0 else active_peak
    return min(rpc_peak, substitution_peak) if rpc_peak > 0 else substitution_peak


def _orchid_total_cost_usd(scorecard: dict[str, Any]) -> float | None:
    economics = scorecard.get("economics") or {}
    substitution = scorecard.get("foreground_substitution") or {}
    pricing_complete = economics.get("pricing_complete_for_observed_usage")
    if pricing_complete is False:
        return None
    if substitution.get("present"):
        foreground_cost = substitution.get("fully_priced_foreground_cost_usd")
        if foreground_cost is None and pricing_complete is None:
            foreground_cost = substitution.get("calculated_foreground_cost_usd")
        memory_cost = economics.get("orchid_cloud_memory_fully_priced_cost_usd")
        if memory_cost is None and pricing_complete is None:
            memory_cost = economics.get("orchid_memory_cost_usd", 0)
        if foreground_cost is not None and memory_cost is not None:
            return float(foreground_cost) + float(memory_cost)
    corrected = economics.get("orchid_total_fully_priced_cost_usd")
    if corrected is not None:
        return float(corrected)
    if pricing_complete is None:
        legacy = economics.get("orchid_total_provider_equivalent_cost_usd")
        return float(legacy) if legacy is not None else None
    return None


def _effective_api_token_comparison(scorecard: dict[str, Any]) -> dict[str, Any]:
    economics = scorecard.get("economics") or {}
    comparison = economics.get("api_token_class_comparison") or {}
    pi_classes = comparison.get("pi") or {}
    substitution = scorecard.get("foreground_substitution") or {}
    if substitution.get("present") and substitution.get("api_token_classes"):
        memory_classes = economics.get("orchid_cloud_memory_token_classes") or {}
        orchid_classes = {
            key: float((substitution["api_token_classes"] or {}).get(key) or 0)
            + float(memory_classes.get(key) or 0)
            for key in TOKEN_CLASS_KEYS
        }
        return compare_api_token_classes(pi_classes, orchid_classes)
    return comparison


def extract_run_metrics(
    scorecard: dict[str, Any],
    *,
    artifact: Path | None = None,
) -> dict[str, Any]:
    economics = scorecard.get("economics") or {}
    pi_foreground = economics.get("pi_foreground") or scorecard.get("pi_native") or {}
    pricing_complete = economics.get("pricing_complete_for_observed_usage")
    pi_cost_raw = pi_foreground.get("fully_priced_cost_usd")
    if pi_cost_raw is None and pricing_complete is None:
        pi_cost_raw = pi_foreground.get("calculated_cost_usd")
    pi_cost = float(pi_cost_raw) if pi_cost_raw is not None else None
    orchid_cost = _orchid_total_cost_usd(scorecard)
    token_cost_comparison = _effective_api_token_comparison(scorecard)
    if pi_cost is not None and orchid_cost is not None and pi_cost > 0:
        cost_savings = (pi_cost - orchid_cost) / pi_cost
        cost_basis = "dollar_rate_card"
    else:
        cost_savings = token_cost_comparison.get("guaranteed_savings_fraction")
        cost_basis = "token_class_dominance"
    pi_prompt = _pi_foreground_prompt_tokens(scorecard)
    orchid_prompt = _foreground_prompt_tokens(scorecard)
    pi_peak = float(
        pi_foreground.get("largest_prompt_tokens")
        or pi_foreground.get("largest_context_tokens")
        or 0
    )
    orchid_peak = _orchid_foreground_peak_tokens(scorecard)
    direct_latency = scorecard.get("direct_foreground_latency") or {}
    pi_direct_latency = direct_latency.get("pi_native") or {}
    orchid_direct_latency = direct_latency.get("orchid") or {}
    pi_latency = pi_direct_latency.get("provider_wall_ms") or {}
    orchid_latency = orchid_direct_latency.get("provider_wall_ms") or {}
    direct_latency_present = bool(
        pi_direct_latency.get("present")
        and orchid_direct_latency.get("present")
        and int(pi_latency.get("samples") or 0) > 0
        and int(orchid_latency.get("samples") or 0) > 0
    )
    milestones = _milestone_scores(scorecard)
    pi_quality = milestones.get("PI_NATIVE", {})
    orchid_quality = milestones.get("ORCHID", {})
    gate_status: dict[str, Any] = {}
    if artifact:
        gate_path = artifact / "memory_transitions/GATE_STATUS.json"
        if gate_path.exists():
            gate_status = load_json(gate_path)
        else:
            orchid_summary = artifact / "orchid/SUMMARY.json"
            if orchid_summary.exists():
                gate_status = (load_json(orchid_summary).get("gateway") or {}).get("gate") or {}
    promotions = int(gate_status.get("promotions") or gate_status.get("clean_promotions") or 0)
    failures = int(gate_status.get("failures") or 0) + int(
        gate_status.get("invariant_failures") or 0
    )
    post_final_turns = count_post_final_promotion_turns(artifact) if artifact else 0
    paired_vram = scorecard.get("paired_vram") or {}
    pi_vram_total = (paired_vram.get("pi_native") or {}).get("total") or {}
    orchid_vram = paired_vram.get("orchid") or {}
    orchid_vram_total = orchid_vram.get("total") or {}
    pi_peak_vram = pi_vram_total.get("peak_bytes")
    orchid_peak_vram = orchid_vram_total.get("peak_bytes")
    pi_integrated_vram = pi_vram_total.get("byte_seconds")
    orchid_integrated_vram = orchid_vram_total.get("byte_seconds")
    max_vram = orchid_vram.get("selector_peak_fraction")
    return {
        "valid": bool((scorecard.get("validity") or {}).get("valid")),
        "pi_total_cost_usd": pi_cost,
        "orchid_total_cost_usd": orchid_cost,
        "cost_basis": cost_basis,
        "cost_savings_fraction": cost_savings,
        "api_token_class_comparison": token_cost_comparison,
        "pi_prompt_tokens": pi_prompt,
        "orchid_prompt_tokens": orchid_prompt,
        "prompt_savings_fraction": (
            (pi_prompt - orchid_prompt) / pi_prompt if pi_prompt > 0 else None
        ),
        "pi_peak_context_tokens": pi_peak,
        "orchid_peak_context_tokens": orchid_peak,
        "peak_context_savings_fraction": (
            (pi_peak - orchid_peak) / pi_peak if pi_peak > 0 else None
        ),
        "direct_latency_present": direct_latency_present,
        "latency_measurement": "per_provider_request_wall_clock",
        "pi_latency_median_ms": float(pi_latency.get("median") or 0),
        "orchid_latency_median_ms": float(orchid_latency.get("median") or 0),
        "pi_latency_p95_ms": float(pi_latency.get("p95") or 0),
        "orchid_latency_p95_ms": float(orchid_latency.get("p95") or 0),
        "promotions": promotions,
        "reliability_failures": failures,
        "post_final_promotion_turns": post_final_turns,
        "selector_peak_vram_fraction": (
            float(max_vram) if isinstance(max_vram, (int, float)) else None
        ),
        "vram_telemetry_complete": bool(paired_vram.get("complete")),
        "pi_total_peak_vram_bytes": pi_peak_vram,
        "orchid_total_peak_vram_bytes": orchid_peak_vram,
        "total_peak_vram_savings_fraction": (
            (float(pi_peak_vram) - float(orchid_peak_vram)) / float(pi_peak_vram)
            if isinstance(pi_peak_vram, (int, float))
            and pi_peak_vram > 0
            and isinstance(orchid_peak_vram, (int, float))
            else None
        ),
        "pi_integrated_vram_byte_seconds": pi_integrated_vram,
        "orchid_integrated_vram_byte_seconds": orchid_integrated_vram,
        "integrated_vram_savings_fraction": (
            (float(pi_integrated_vram) - float(orchid_integrated_vram))
            / float(pi_integrated_vram)
            if isinstance(pi_integrated_vram, (int, float))
            and pi_integrated_vram > 0
            and isinstance(orchid_integrated_vram, (int, float))
            else None
        ),
        "quality": {
            "pi": pi_quality,
            "orchid": orchid_quality,
            "hidden_eval": (scorecard.get("quality") or {}).get("hidden_eval") or {},
        },
    }


def _hidden_arm(hidden_eval: dict[str, Any], *names: str) -> dict[str, Any]:
    arms = hidden_eval.get("arms") or {}
    for name in names:
        value = arms.get(name)
        if isinstance(value, dict):
            return value
    return {}


def quality_noninferior(metrics: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    pi = metrics.get("quality", {}).get("pi") or {}
    orchid = metrics.get("quality", {}).get("orchid") or {}
    if not pi or not orchid:
        return False, ["missing milestone quality scores"]
    hidden_eval = metrics.get("quality", {}).get("hidden_eval") or {}
    pi_hidden = _hidden_arm(hidden_eval, "PI_NATIVE", "arm_a_pi", "pi_native")
    orchid_hidden = _hidden_arm(hidden_eval, "ORCHID", "arm_b_orchid", "orchid")
    hidden_provenance = bool(
        hidden_eval.get("evaluator")
        and hidden_eval.get("oracle_commit")
        and hidden_eval.get("source_sha256")
    )
    pi_hidden_pass = bool(
        pi_hidden
        and int(pi_hidden.get("exit_code", 1)) == 0
        and pi_hidden.get("timed_out") is not True
    )
    orchid_hidden_pass = bool(
        orchid_hidden
        and int(orchid_hidden.get("exit_code", 1)) == 0
        and orchid_hidden.get("timed_out") is not True
    )
    checks = {
        "hidden_eval_provenance": hidden_provenance,
        "orchid_hidden_functional_pass": orchid_hidden_pass,
        "hidden_noninferior": orchid_hidden_pass or not pi_hidden_pass,
        "orchid_typecheck_pass": int(orchid.get("typecheck_exit", 1)) == 0,
        "orchid_build_pass": int(orchid.get("build_exit", 1)) == 0,
        "tool_failures": int(orchid.get("tool_failures") or 0)
        <= int(pi.get("tool_failures") or 0),
    }
    for name, ok in checks.items():
        if not ok:
            reasons.append(f"quality.{name} inferior to PI_NATIVE")
    return all(checks.values()), reasons


@dataclass(frozen=True)
class GateThresholds:
    api_cost_savings_fraction: float = 0.10
    prompt_kv_savings_fraction: float = 0.20
    peak_context_savings_fraction: float = 0.10
    total_peak_vram_savings_fraction: float = 0.10
    integrated_vram_savings_fraction: float = 0.10
    foreground_latency_p95_worse_fraction: float = 0.05
    minimum_promotions: int = 3
    minimum_post_final_promotion_turns: int = 5
    selector_max_vram_fraction: float = 0.90

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any] | None) -> GateThresholds:
        gates = ((manifest or {}).get("pareto") or {}).get("gates") or {}
        return cls(
            api_cost_savings_fraction=float(
                gates.get("api_cost_savings_fraction", 0.10)
            ),
            prompt_kv_savings_fraction=float(
                gates.get("prompt_kv_savings_fraction", 0.20)
            ),
            peak_context_savings_fraction=float(
                gates.get("peak_context_savings_fraction", 0.10)
            ),
            total_peak_vram_savings_fraction=float(
                gates.get("total_peak_vram_savings_fraction", 0.10)
            ),
            integrated_vram_savings_fraction=float(
                gates.get("integrated_vram_savings_fraction", 0.10)
            ),
            foreground_latency_p95_worse_fraction=float(
                gates.get("foreground_latency_p95_worse_fraction", 0.05)
            ),
            minimum_promotions=int(gates.get("minimum_promotions", 3)),
            minimum_post_final_promotion_turns=int(
                gates.get("minimum_post_final_promotion_turns", 5)
            ),
            selector_max_vram_fraction=float(gates.get("selector_max_vram_fraction", 0.90)),
        )


def evaluate_single_run(
    scorecard: dict[str, Any],
    *,
    artifact: Path | None = None,
    thresholds: GateThresholds | None = None,
) -> dict[str, Any]:
    artifact = artifact or Path(scorecard.get("artifact", "."))
    thresholds = thresholds or GateThresholds()
    metrics = extract_run_metrics(scorecard, artifact=artifact)
    quality_ok, quality_reasons = quality_noninferior(metrics)
    checks: dict[str, bool] = {
        "run_valid": metrics["valid"],
        "foreground_substitution_present": bool(
            (scorecard.get("foreground_substitution") or {}).get("present")
        ),
        "memory_format_v2": (scorecard.get("config_orchid") or {}).get("memory_format")
        == "v2",
        "quality_noninferior": quality_ok,
        "direct_latency_present": metrics["direct_latency_present"],
        "api_cost_savings": (
            metrics["cost_savings_fraction"] is not None
            and metrics["cost_savings_fraction"] >= thresholds.api_cost_savings_fraction
        ),
        "prompt_kv_savings": (
            metrics["prompt_savings_fraction"] is not None
            and metrics["prompt_savings_fraction"] >= thresholds.prompt_kv_savings_fraction
        ),
        "peak_context_savings": (
            metrics["peak_context_savings_fraction"] is not None
            and metrics["peak_context_savings_fraction"]
            >= thresholds.peak_context_savings_fraction
        ),
        "foreground_latency_median_noninferior": (
            metrics["orchid_latency_median_ms"] <= metrics["pi_latency_median_ms"]
            if metrics["pi_latency_median_ms"] > 0
            else True
        ),
        "foreground_latency_p95_within_tolerance": (
            metrics["orchid_latency_p95_ms"]
            <= metrics["pi_latency_p95_ms"] * (1 + thresholds.foreground_latency_p95_worse_fraction)
            if metrics["pi_latency_p95_ms"] > 0
            else True
        ),
        "minimum_promotions": metrics["promotions"] >= thresholds.minimum_promotions,
        "post_final_promotion_turns": metrics["post_final_promotion_turns"]
        >= thresholds.minimum_post_final_promotion_turns,
        "zero_reliability_failures": metrics["reliability_failures"] == 0,
        "selector_vram_under_limit": (
            metrics["selector_peak_vram_fraction"] is not None
            and metrics["selector_peak_vram_fraction"] < thresholds.selector_max_vram_fraction
        ),
        "vram_telemetry_complete": metrics["vram_telemetry_complete"],
        "total_peak_vram_savings": (
            metrics["total_peak_vram_savings_fraction"] is not None
            and metrics["total_peak_vram_savings_fraction"]
            >= thresholds.total_peak_vram_savings_fraction
        ),
        "integrated_vram_savings": (
            metrics["integrated_vram_savings_fraction"] is not None
            and metrics["integrated_vram_savings_fraction"]
            >= thresholds.integrated_vram_savings_fraction
        ),
    }
    reasons = list(quality_reasons)
    if not checks["run_valid"]:
        reasons.append("run validity check failed")
    if not checks["foreground_substitution_present"]:
        reasons.append("foreground substitution telemetry missing")
    if not checks["memory_format_v2"]:
        reasons.append("benchmark memory format is not v2")
    if not checks["direct_latency_present"]:
        reasons.append("direct per-provider-request latency telemetry missing")
    if not checks["api_cost_savings"]:
        reasons.append("api cost savings below threshold")
    if not checks["prompt_kv_savings"]:
        reasons.append("foreground prompt-token savings below threshold")
    if not checks["peak_context_savings"]:
        reasons.append("peak context savings below threshold")
    if not checks["vram_telemetry_complete"]:
        reasons.append("paired arm-isolated VRAM telemetry missing or incomplete")
    if not checks["total_peak_vram_savings"]:
        reasons.append("total peak VRAM savings below threshold")
    if not checks["integrated_vram_savings"]:
        reasons.append("time-integrated VRAM savings below threshold")
    if not checks["minimum_promotions"]:
        reasons.append("insufficient promotions")
    if not checks["post_final_promotion_turns"]:
        reasons.append("insufficient post-final-promotion turns")
    if not checks["zero_reliability_failures"]:
        reasons.append("reliability failures observed")
    if not checks["selector_vram_under_limit"]:
        reasons.append("selector VRAM telemetry missing or over limit")
    return {
        "artifact": str(artifact),
        "run_id": scorecard.get("run_id"),
        "metrics": metrics,
        "checks": checks,
        "all_pass": all(checks.values()),
        "reasons": reasons,
    }


def anti_overfit_evidence(artifacts: list[Path]) -> dict[str, Any]:
    contract_sha = contract_sha256()
    selector_verify = verify_golden_hashes()
    config_hashes: list[str] = []
    manifest_shas: list[str] = []
    for artifact in artifacts:
        config_path = artifact / "CONFIG_HASHES.json"
        manifest_path = artifact / "BENCHMARK_MANIFEST.json"
        if config_path.exists():
            config_hashes.append(
                load_json(config_path).get("config_orchid_sha256", "")
            )
        if manifest_path.exists():
            manifest = load_json(manifest_path)
            manifest_shas.append(
                ((manifest.get("pareto") or {}).get("selector_contract") or {}).get(
                    "contract_sha256"
                )
            )
    return {
        "selector_contract_sha256": contract_sha,
        "selector_frozen_hashes_verified": selector_verify.get("ok", False),
        "selector_hash_mismatches": selector_verify.get("mismatches", []),
        "orchid_config_hashes_stable": len(set(config_hashes)) <= 1,
        "orchid_config_hashes": config_hashes,
        "manifest_selector_contract_shas": manifest_shas,
        "manifest_selector_contract_stable": len(set(manifest_shas)) <= 1,
        "all_manifest_selector_contract_match": all(
            sha == contract_sha for sha in manifest_shas if sha
        ),
    }


PER_RUN_REQUIRED_CHECKS = (
    "run_valid",
    "foreground_substitution_present",
    "memory_format_v2",
    "quality_noninferior",
    "direct_latency_present",
    "foreground_latency_median_noninferior",
    "foreground_latency_p95_within_tolerance",
    "minimum_promotions",
    "post_final_promotion_turns",
    "zero_reliability_failures",
    "vram_telemetry_complete",
    "total_peak_vram_savings",
    "integrated_vram_savings",
    "selector_vram_under_limit",
)

MEDIAN_METRIC_CHECKS = (
    "median_api_cost_savings",
    "median_prompt_kv_savings",
    "median_peak_context_savings",
)


def validate_pareto_pair_artifact(
    artifact: Path,
    *,
    thresholds: GateThresholds | None = None,
) -> dict[str, Any]:
    """Validate one paired benchmark artifact against per-run release gates."""

    artifact = artifact.resolve()
    scorecard = generate_scorecard(artifact, require_hidden_eval=True)
    result = evaluate_single_run(scorecard, artifact=artifact, thresholds=thresholds)
    failed = [name for name in PER_RUN_REQUIRED_CHECKS if not result["checks"][name]]
    return {
        "artifact": str(artifact),
        "run_id": scorecard.get("run_id"),
        "ready": not failed,
        "failed_checks": failed,
        "metrics": result["metrics"],
        "checks": {name: result["checks"][name] for name in PER_RUN_REQUIRED_CHECKS},
    }


def evaluate_paired_release(
    artifacts: list[Path],
    *,
    required_runs: int = 3,
    thresholds: GateThresholds | None = None,
) -> dict[str, Any]:
    artifacts = [path.resolve() for path in artifacts]
    thresholds = thresholds or GateThresholds()
    if len(artifacts) < required_runs:
        return {
            "schema_version": "orchid.pareto_release.v1",
            "required_runs": required_runs,
            "runs_evaluated": len(artifacts),
            "release_pass": False,
            "reasons": [f"need {required_runs} paired runs, got {len(artifacts)}"],
        }

    per_run: list[dict[str, Any]] = []
    for artifact in artifacts[:required_runs]:
        scorecard = generate_scorecard(artifact, require_hidden_eval=True)
        per_run.append(
            evaluate_single_run(scorecard, artifact=artifact, thresholds=thresholds)
        )

    medians = {
        "cost_savings_fraction": _percentile(
            [
                float(run["metrics"]["cost_savings_fraction"] or 0)
                for run in per_run
                if run["metrics"]["cost_savings_fraction"] is not None
            ],
            0.5,
        ),
        "prompt_savings_fraction": _percentile(
            [
                float(run["metrics"]["prompt_savings_fraction"] or 0)
                for run in per_run
                if run["metrics"]["prompt_savings_fraction"] is not None
            ],
            0.5,
        ),
        "peak_context_savings_fraction": _percentile(
            [
                float(run["metrics"]["peak_context_savings_fraction"] or 0)
                for run in per_run
                if run["metrics"]["peak_context_savings_fraction"] is not None
            ],
            0.5,
        ),
    }
    anti_overfit = anti_overfit_evidence(artifacts)
    aggregate_checks = {
        "all_per_run_required_pass": all(
            all(run["checks"][name] for name in PER_RUN_REQUIRED_CHECKS)
            for run in per_run
        ),
        "median_api_cost_savings": medians["cost_savings_fraction"]
        >= thresholds.api_cost_savings_fraction,
        "median_prompt_kv_savings": medians["prompt_savings_fraction"]
        >= thresholds.prompt_kv_savings_fraction,
        "median_peak_context_savings": medians["peak_context_savings_fraction"]
        >= thresholds.peak_context_savings_fraction,
        "selector_contract_unchanged": anti_overfit["selector_frozen_hashes_verified"],
    }
    reasons: list[str] = []
    if not aggregate_checks["all_per_run_required_pass"]:
        reasons.append("one or more paired runs failed per-run required gates")
    for name in MEDIAN_METRIC_CHECKS:
        if not aggregate_checks.get(name):
            reasons.append(f"aggregate check failed: {name}")
    if not anti_overfit["selector_frozen_hashes_verified"]:
        reasons.append("selector frozen request hashes changed")
    return {
        "schema_version": "orchid.pareto_release.v1",
        "required_runs": required_runs,
        "runs_evaluated": len(per_run),
        "thresholds": thresholds.__dict__,
        "per_run": per_run,
        "medians": medians,
        "aggregate_checks": aggregate_checks,
        "anti_overfit": anti_overfit,
        "release_pass": all(aggregate_checks.values())
        and anti_overfit["selector_frozen_hashes_verified"],
        "reasons": reasons,
        "default_manifest_gates": pareto_manifest_extensions()["gates"],
    }


def consolidator_live_gate_status(
    report_path: Path | None = None,
    *,
    required_chunks: int = 32,
) -> dict[str, Any]:
    """Return sealed live consolidator gate readiness from the frozen report artifact."""

    path = report_path or Path("artifacts/selector_contract/V2_CONSOLIDATOR_GATE_REPORT.json")
    if not path.exists():
        return {
            "ready": False,
            "reason": "missing_report",
            "accepted": 0,
            "rejected": 0,
            "required_chunks": required_chunks,
            "report_path": str(path),
        }
    report = load_json(path)
    accepted = int(report.get("accepted") or 0)
    rejected = int(report.get("rejected") or 0)
    generation_count = int(report.get("generation_count") or accepted + rejected)
    ready = (
        generation_count >= required_chunks
        and accepted >= required_chunks
        and rejected == 0
    )
    if ready:
        reason = "passed"
    elif generation_count < required_chunks:
        reason = "stale_partial_probe"
    elif rejected > 0:
        reason = "rejected_chunks"
    else:
        reason = "incomplete_or_rejected"
    return {
        "ready": ready,
        "reason": reason,
        "accepted": accepted,
        "rejected": rejected,
        "generation_count": generation_count,
        "required_chunks": required_chunks,
        "report_path": str(path),
    }


def benchmark_memory_format(
    *,
    explicit: str | None = None,
    report_path: Path | None = None,
    required_chunks: int = 32,
) -> str:
    """Choose benchmark gateway memory format after the live consolidator gate."""

    if explicit:
        return explicit
    if consolidator_live_gate_status(
        report_path,
        required_chunks=required_chunks,
    ).get("ready"):
        return "v2"
    return "v1"


def substitution_probe_status(root: Path) -> dict[str, Any]:
    """Summarize the latest orchid-only substitution probe artifact, if present."""

    probes = sorted(
        (root / "artifacts/agent_benchmarks").glob(
            "freetoshop_orchid_only_substitution_probe_*"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not probes:
        return {"present": False, "reason": "no substitution probe artifacts"}
    artifact = probes[0]
    telemetry = artifact / "orchid/foreground_substitution.jsonl"
    if not telemetry.exists():
        return {
            "present": False,
            "artifact": str(artifact),
            "reason": "foreground_substitution.jsonl missing",
        }
    rows = load_jsonl(telemetry)
    active_rows = sum(1 for row in rows if row.get("active"))
    suppressed = [
        float(row.get("suppressed_covered_tokens") or 0)
        for row in rows
        if row.get("active") and row.get("suppressed_covered_tokens")
    ]
    return {
        "present": True,
        "ready": active_rows > 0 and bool(suppressed),
        "artifact": str(artifact),
        "requests": len(rows),
        "active_requests": active_rows,
        "peak_suppressed_covered_tokens": max(suppressed, default=0),
    }


def summarize_worktree_dirty_lines(lines: list[str]) -> dict[str, Any]:
    """Group tracked dirty paths for release-prep visibility."""

    buckets: dict[str, int] = {}
    for line in lines:
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if not path:
            continue
        if path.startswith("memory_gateway/"):
            key = "memory_gateway"
        elif path.startswith("tests/"):
            key = "tests"
        elif path.startswith("tools/"):
            key = "tools"
        elif path.startswith("artifacts/"):
            key = "artifacts"
        else:
            key = "other"
        buckets[key] = buckets.get(key, 0) + 1
    return {
        "total": len(lines),
        "by_prefix": buckets,
        "sample_paths": [line[3:].strip() for line in lines[:5]],
    }


def build_release_next_steps(
    checks: dict[str, bool],
    *,
    gate: dict[str, Any],
) -> list[str]:
    """Ordered unblock commands for the sealed Pareto release path."""

    steps: list[str] = []
    if not checks.get("provider_profile"):
        steps.extend(
            [
                "tools/start_freetoshop_corrected_all_solar.sh setup-agnes",
                "tools/start_freetoshop_corrected_all_solar.sh probe-provider",
            ]
        )
        return steps
    if not checks.get("consolidator_live_gate"):
        if gate.get("reason") == "stale_partial_probe":
            steps.append("tools/start_freetoshop_corrected_all_solar.sh reset-consolidator-gate")
        steps.append(
            "ORCHID_RUN_LIVE_CONSOLIDATOR_GATE=1 "
            "tools/start_freetoshop_corrected_all_solar.sh check-consolidator-gate"
        )
        return steps
    if not checks.get("selector_frozen_hashes"):
        steps.append("tools/freeze_selector_contract.py  # restore frozen selector hashes")
        return steps
    if not checks.get("worktree_clean"):
        steps.append("git commit -am 'pareto release prep'  # or: git stash push --keep-index")
        return steps
    steps.extend(
        [
            "tools/start_freetoshop_corrected_all_solar.sh check-offline",
            "tools/start_freetoshop_corrected_all_solar.sh run-pareto-triplet",
            "inspect artifacts/RELEASE_REPORT.json and artifacts/RELEASE_REPORT.md",
        ]
    )
    return steps


def pareto_prerequisite_report(
    root: Path | str,
    *,
    api_key: str | None = None,
    required_chunks: int = 32,
) -> dict[str, Any]:
    """Summarize blockers for the sealed Pareto release path."""

    repo_root = Path(root).resolve()
    report_path = repo_root / "artifacts/selector_contract/V2_CONSOLIDATOR_GATE_REPORT.json"
    resolved_key = (
        api_key
        or os.environ.get("BENCHMARK_PROVIDER_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
    )
    resolved_model = os.environ.get("BENCHMARK_MODEL_ID")
    resolved_base = os.environ.get("BENCHMARK_PROVIDER_BASE_URL")
    gate = consolidator_live_gate_status(report_path, required_chunks=required_chunks)
    selector_verify = verify_golden_hashes()
    worktree = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--short", "--untracked-files=no"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty_lines = [line for line in (worktree.stdout or "").splitlines() if line.strip()]
    worktree_clean = not dirty_lines
    worktree_summary = summarize_worktree_dirty_lines(dirty_lines)
    checks = {
        "provider_profile": provider_profile_ready(
            api_key=resolved_key,
            model_id=resolved_model,
            base_url=resolved_base,
        ),
        "consolidator_live_gate": bool(gate.get("ready")),
        "selector_frozen_hashes": bool(selector_verify.get("ok")),
        "worktree_clean": worktree_clean,
    }
    blockers: list[str] = []
    if not checks["provider_profile"]:
        blockers.append(
            f"provider profile must use {REFERENCE_CLOUD_MODEL} at {AGNES_PROVIDER_BASE_URL}; "
            "run tools/start_freetoshop_corrected_all_solar.sh setup-agnes"
        )
    if not checks["consolidator_live_gate"]:
        gate_reason = gate.get("reason")
        if gate_reason == "stale_partial_probe":
            blockers.append(
                "consolidator live gate has a stale partial probe report "
                f"(generation_count={gate.get('generation_count')}, required={required_chunks}); "
                "run reset-consolidator-gate then "
                "ORCHID_RUN_LIVE_CONSOLIDATOR_GATE=1 check-consolidator-gate"
            )
        else:
            blockers.append(
                "consolidator live gate incomplete "
                f"(accepted={gate.get('accepted')}, rejected={gate.get('rejected')}, "
                f"required={required_chunks}, reason={gate_reason})"
            )
    if not checks["selector_frozen_hashes"]:
        blockers.append("selector frozen request hashes do not match production contract")
    if not checks["worktree_clean"]:
        blockers.append("tracked git worktree is dirty; commit or stash before release runs")
    reference_path = repo_root / "artifacts/agent_benchmarks/freetoshop_pi_vs_orchid_corrected_all_solar"
    reference_run: dict[str, Any] | None = None
    if reference_path.is_dir():
        try:
            scorecard = generate_scorecard(reference_path, require_hidden_eval=False)
            single = evaluate_single_run(scorecard, artifact=reference_path)
            reference_run = {
                "artifact": str(reference_path),
                "failed_checks": [
                    name for name, ok in (single.get("checks") or {}).items() if not ok
                ],
                "substitution_present": bool(
                    (scorecard.get("foreground_substitution") or {}).get("present")
                ),
                "promotions": (single.get("metrics") or {}).get("promotions"),
                "post_final_promotion_turns": (single.get("metrics") or {}).get(
                    "post_final_promotion_turns"
                ),
            }
        except Exception:
            reference_run = None
    substitution_probe = substitution_probe_status(repo_root)
    next_steps = build_release_next_steps(checks, gate=gate)
    return {
        "schema_version": "orchid.pareto_prerequisites.v1",
        "ready": not blockers,
        "blockers": blockers,
        "next_steps": next_steps,
        "checks": checks,
        "consolidator_gate": gate,
        "selector_contract": selector_verify,
        "worktree_dirty_count": len(dirty_lines),
        "worktree_dirty_summary": worktree_summary,
        "benchmark_memory_format": benchmark_memory_format(
            report_path=report_path,
            required_chunks=required_chunks,
        ),
        "reference_run": reference_run,
        "substitution_probe": substitution_probe,
    }
