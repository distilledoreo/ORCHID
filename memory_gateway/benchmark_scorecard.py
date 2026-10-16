"""Unified benchmark scorecard generation for Pareto-Dominance runs."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .benchmark_manifest import DEFAULT_RATE_CARD


@dataclass(frozen=True)
class RunValidity:
    valid: bool
    reasons: tuple[str, ...]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"_invalid_json": line[:500]})
    return rows


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


def _assistant_text(message: dict[str, Any]) -> str:
    values: list[str] = []
    for item in message.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            values.append(item["text"])
        if item.get("type") == "thinking" and isinstance(item.get("thinking"), str):
            values.append(item["thinking"])
    return "\n".join(values)


TOKEN_CLASS_KEYS = (
    "uncached_input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "output_tokens",
)


def _price(pricing: dict[str, Any], key: str) -> float | None:
    value = pricing.get(key)
    if isinstance(value, (int, float)) and math.isfinite(float(value)) and value >= 0:
        return float(value)
    return None


def _base_pricing_known(pricing: dict[str, Any]) -> bool:
    """Reject an all-zero placeholder while permitting legitimately free classes."""

    input_price = _price(pricing, "input")
    output_price = _price(pricing, "output")
    return (
        input_price is not None
        and output_price is not None
        and (input_price > 0 or output_price > 0)
    )


def _pricing_complete_for_classes(
    pricing: dict[str, Any],
    token_classes: dict[str, float],
) -> bool:
    if not _base_pricing_known(pricing):
        return False
    return all(
        not token_classes.get(token_key, 0) or _price(pricing, price_key) is not None
        for token_key, price_key in (
            ("cache_read_tokens", "cache_read"),
            ("cache_write_tokens", "cache_write"),
        )
    )


def _cost_for_token_classes(
    token_classes: dict[str, float],
    pricing: dict[str, Any],
    *,
    require_complete: bool,
) -> float | None:
    if not _base_pricing_known(pricing):
        return None
    if require_complete and not _pricing_complete_for_classes(pricing, token_classes):
        return None
    prices = {
        "uncached_input_tokens": _price(pricing, "input") or 0.0,
        "cache_read_tokens": _price(pricing, "cache_read") or 0.0,
        "cache_write_tokens": _price(pricing, "cache_write") or 0.0,
        "output_tokens": _price(pricing, "output") or 0.0,
    }
    return sum(token_classes[key] * prices[key] for key in TOKEN_CLASS_KEYS) / 1_000_000


def _add_token_classes(*groups: dict[str, Any]) -> dict[str, float]:
    return {
        key: sum(float(group.get(key) or 0) for group in groups)
        for key in TOKEN_CLASS_KEYS
    }


def summarize_rpc_telemetry(
    path: Path,
    *,
    pricing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pricing = pricing or {
        "input": float(os.environ.get("BENCHMARK_INPUT_USD_PER_MILLION", "0")),
        "output": float(os.environ.get("BENCHMARK_OUTPUT_USD_PER_MILLION", "0")),
    }
    rows = load_jsonl(path)
    assistant_rows = [
        row
        for row in rows
        if row.get("type") == "message_end"
        and isinstance(row.get("message"), dict)
        and row["message"].get("role") == "assistant"
    ]
    usage_rows = [
        row["message"]["usage"]
        for row in assistant_rows
        if isinstance(row["message"].get("usage"), dict)
        and float(row["message"]["usage"].get("totalTokens") or 0) > 0
    ]

    def total(field: str) -> float:
        return sum(float(usage.get(field) or 0) for usage in usage_rows)

    timeline: list[dict[str, Any]] = []
    previous_ts: int | None = None
    turn_latencies_ms: list[float] = []
    for row in assistant_rows:
        message = row["message"]
        timestamp_ms = int(message.get("timestamp") or 0)
        if previous_ts is not None and timestamp_ms >= previous_ts:
            turn_latencies_ms.append(timestamp_ms - previous_ts)
        previous_ts = timestamp_ms
        timeline.append(
            {
                "timestamp_ms": timestamp_ms,
                "input_tokens": (message.get("usage") or {}).get("input"),
                "output_tokens": (message.get("usage") or {}).get("output"),
            }
        )

    uncached_input = total("input")
    cache_read = total("cacheRead")
    cache_write = total("cacheWrite")
    output = total("output")
    token_classes = {
        "uncached_input_tokens": uncached_input,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "output_tokens": output,
    }
    prompt_tokens_by_request = [
        float(usage.get("input") or 0) + float(usage.get("cacheRead") or 0)
        for usage in usage_rows
    ]
    legacy_cost = _cost_for_token_classes(
        token_classes,
        pricing,
        require_complete=False,
    )
    fully_priced_cost = _cost_for_token_classes(
        token_classes,
        pricing,
        require_complete=True,
    )
    return {
        "path": str(path),
        "counting_rule": "assistant message_end records with nonzero usage; streaming message_update records excluded",
        "usage_responses": len(usage_rows),
        # input_tokens is retained as the historical RPC field. In Pi's
        # telemetry schema it is the uncached input class.
        "input_tokens": uncached_input,
        "uncached_input_tokens": uncached_input,
        "prompt_tokens": uncached_input + cache_read,
        "output_tokens": output,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "api_token_classes": token_classes,
        "reasoning_tokens": total("reasoning"),
        "largest_context_tokens": max(
            (float(usage.get("input") or 0) for usage in usage_rows), default=0
        ),
        "mean_context_tokens": total("input") / len(usage_rows) if usage_rows else 0,
        "largest_prompt_tokens": max(prompt_tokens_by_request, default=0),
        "mean_prompt_tokens": (
            sum(prompt_tokens_by_request) / len(prompt_tokens_by_request)
            if prompt_tokens_by_request
            else 0
        ),
        "reported_cost_usd": sum(
            float((usage.get("cost") or {}).get("total") or 0)
            for usage in usage_rows
            if isinstance(usage.get("cost"), dict)
        ),
        "pricing_known": _base_pricing_known(pricing),
        "pricing_complete_for_observed_usage": _pricing_complete_for_classes(
            pricing,
            token_classes,
        ),
        # This legacy-compatible value ignores a missing cache price, but is
        # never used as release evidence unless fully_priced_cost_usd exists.
        "calculated_cost_usd": legacy_cost,
        "fully_priced_cost_usd": fully_priced_cost,
        "foreground_latency_ms": {
            "median": _percentile(turn_latencies_ms, 0.5),
            "p95": _percentile(turn_latencies_ms, 0.95),
            "samples": len(turn_latencies_ms),
        },
        "assistant_timeline": timeline,
    }


def summarize_foreground_substitution(
    path: Path,
    *,
    pricing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = load_jsonl(path)
    if not rows:
        return {"present": False, "requests": 0}
    pricing = pricing or {}

    def number(row: dict[str, Any], key: str) -> float:
        value = row.get(key)
        return float(value) if isinstance(value, (int, float)) else 0.0

    provider_inputs = [number(row, "provider_input_tokens") for row in rows]
    provider_outputs = [number(row, "provider_output_tokens") for row in rows]
    cache_reads = [number(row, "provider_cache_read_tokens") for row in rows]
    cache_writes = [number(row, "provider_cache_write_tokens") for row in rows]
    prompt_cache_rows = [
        row["prompt_cache"] for row in rows if isinstance(row.get("prompt_cache"), dict)
    ]
    cache_break_counts: dict[str, int] = {}
    for cache_row in prompt_cache_rows:
        cache_break = cache_row.get("cache_break_block")
        if isinstance(cache_break, str) and cache_break:
            cache_break_counts[cache_break] = cache_break_counts.get(cache_break, 0) + 1
    uncached_inputs: list[float] = []
    prompt_inputs: list[float] = []
    for row, provider_input, cache_read in zip(rows, provider_inputs, cache_reads, strict=True):
        explicit_uncached = row.get("provider_uncached_input_tokens")
        if isinstance(explicit_uncached, (int, float)):
            uncached = float(explicit_uncached)
        elif row.get("provider_input_includes_cache") is True:
            uncached = max(0.0, provider_input - cache_read)
        else:
            # Older records do not declare provider semantics. Counting the
            # raw prompt plus cache as separate classes is conservative for
            # ORCHID: it can only make the candidate look more expensive.
            uncached = provider_input
        uncached_inputs.append(uncached)
        prompt_inputs.append(uncached + cache_read)
    assembled_estimates = [
        float(row.get("assembled_estimated_tokens") or 0) for row in rows
    ]
    active_peaks = [
        prompt
        for row, prompt in zip(rows, prompt_inputs, strict=True)
        if row.get("active") and prompt > 0
    ]
    token_classes = {
        "uncached_input_tokens": sum(uncached_inputs),
        "cache_read_tokens": sum(cache_reads),
        "cache_write_tokens": sum(cache_writes),
        "output_tokens": sum(provider_outputs),
    }
    summary: dict[str, Any] = {
        "present": True,
        "requests": len(rows),
        "sum_provider_input_tokens": sum(provider_inputs),
        "sum_provider_uncached_input_tokens": sum(uncached_inputs),
        "sum_provider_prompt_tokens": sum(prompt_inputs),
        "sum_provider_output_tokens": sum(provider_outputs),
        "sum_provider_cache_read_tokens": sum(cache_reads),
        "sum_provider_cache_write_tokens": sum(cache_writes),
        "api_token_classes": token_classes,
        "prompt_cache": {
            "telemetry_requests": len(prompt_cache_rows),
            "provider_cache_hit_requests": sum(1 for value in cache_reads if value > 0),
            "provider_cache_hit_rate": (
                sum(1 for value in cache_reads if value > 0) / len(cache_reads)
                if cache_reads
                else None
            ),
            "estimated_reusable_prefix_tokens": sum(
                float(row.get("reusable_prefix_tokens") or 0)
                for row in prompt_cache_rows
            ),
            "estimated_cache_write_tokens": sum(
                float(row.get("estimated_cache_write_tokens") or 0)
                for row in prompt_cache_rows
            ),
            "cache_break_counts": cache_break_counts,
        },
        "sum_assembled_estimated_tokens": sum(assembled_estimates),
        "peak_provider_input_tokens": max(provider_inputs, default=0),
        "peak_provider_prompt_tokens": max(prompt_inputs, default=0),
        "peak_active_provider_prompt_tokens": max(active_peaks, default=0),
        # Backward-compatible name; corrected release code uses the prompt
        # variant above.
        "peak_active_provider_input_tokens": max(active_peaks, default=0),
        "peak_assembled_estimated_tokens": max(assembled_estimates, default=0),
        "mean_provider_input_tokens": (
            sum(provider_inputs) / len(provider_inputs) if provider_inputs else 0
        ),
        "mean_provider_prompt_tokens": (
            sum(prompt_inputs) / len(prompt_inputs) if prompt_inputs else 0
        ),
    }
    summary["pricing_known"] = _base_pricing_known(pricing)
    summary["pricing_complete_for_observed_usage"] = _pricing_complete_for_classes(
        pricing,
        token_classes,
    )
    summary["calculated_foreground_cost_usd"] = _cost_for_token_classes(
        token_classes,
        pricing,
        require_complete=False,
    )
    summary["fully_priced_foreground_cost_usd"] = _cost_for_token_classes(
        token_classes,
        pricing,
        require_complete=True,
    )
    return summary


def summarize_direct_foreground_latency(path: Path) -> dict[str, Any]:
    rows = load_jsonl(path)
    wall_ms: list[float] = []
    first_event_ms: list[float] = []
    for row in rows:
        latency = row.get("direct_provider_latency")
        source = latency if isinstance(latency, dict) else row
        success = source.get("success", row.get("success", True))
        wall = source.get("provider_wall_ms")
        if success is not False and isinstance(wall, (int, float)) and wall >= 0:
            wall_ms.append(float(wall))
        first_event = source.get("time_to_first_event_ms")
        if (
            success is not False
            and isinstance(first_event, (int, float))
            and first_event >= 0
        ):
            first_event_ms.append(float(first_event))
    return {
        "present": bool(wall_ms),
        "path": str(path),
        "measurement": "per-provider-request wall clock; tool and idle time excluded",
        "provider_wall_ms": {
            "median": _percentile(wall_ms, 0.5),
            "p95": _percentile(wall_ms, 0.95),
            "samples": len(wall_ms),
        },
        "time_to_first_event_ms": {
            "median": _percentile(first_event_ms, 0.5),
            "p95": _percentile(first_event_ms, 0.95),
            "samples": len(first_event_ms),
        },
    }


def summarize_memory_transitions(artifact: Path) -> dict[str, Any]:
    summary_path = artifact / "memory_transitions/SUMMARY.json"
    orchid_summary_path = artifact / "orchid/SUMMARY.json"
    summary: dict[str, Any] = {}
    if summary_path.exists():
        summary.update(load_json(summary_path))
    if orchid_summary_path.exists():
        gateway = load_json(orchid_summary_path).get("gateway") or {}
        if gateway.get("model_runs_by_stage"):
            summary["model_runs_by_stage"] = gateway["model_runs_by_stage"]
    if summary:
        return summary
    promotions = load_jsonl(artifact / "memory_transitions/orchid_promotions.jsonl")
    pi_compactions = load_jsonl(artifact / "memory_transitions/pi_compactions.jsonl")
    return {
        "pi_compactions": len(pi_compactions),
        "orchid_promotions": len(promotions),
        "orchid_clean_promotions": sum(
            1 for row in promotions if row.get("status") == "PROMOTED"
        ),
    }


def summarize_quality(artifact: Path) -> dict[str, Any]:
    quality: dict[str, Any] = {}
    validation_path = artifact / "quality/validation.json"
    hidden_path = artifact / "quality/hidden_eval.json"
    if validation_path.exists():
        quality["validation"] = load_json(validation_path)
    if hidden_path.exists():
        quality["hidden_eval"] = load_json(hidden_path)
    milestone_path = artifact / "quality/milestone_scores.jsonl"
    if milestone_path.exists():
        quality["milestones"] = load_jsonl(milestone_path)
    return quality


def summarize_selector_gpu(artifact: Path) -> dict[str, Any]:
    for candidate in (
        artifact / "orchid/selector_device.json",
        artifact / "gateway/selector_device.json",
        artifact / "prelaunch/selector_device.json",
    ):
        if candidate.exists():
            return {"present": True, **load_json(candidate)}
    return {
        "present": False,
        "reason": "selector GPU telemetry not recorded in artifact",
    }


def summarize_paired_vram(artifact: Path) -> dict[str, Any]:
    candidates = (
        artifact / "hardware/paired_vram_summary.json",
        artifact / "hardware/VRAM_SUMMARY.json",
    )
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        return {"present": False, "complete": False, "reason": "missing paired VRAM summary"}
    payload = load_json(path)
    arms = payload.get("arms") or {}

    def arm(*names: str) -> dict[str, Any]:
        for name in names:
            value = arms.get(name)
            if isinstance(value, dict):
                return value
        return {}

    def device(arm_payload: dict[str, Any], role: str) -> dict[str, Any]:
        devices = arm_payload.get("devices") or {}
        value = devices.get(role) or arm_payload.get(role)
        return value if isinstance(value, dict) else {}

    def total(arm_payload: dict[str, Any]) -> dict[str, float | int | None]:
        raw_total = arm_payload.get("total") or {}
        devices = arm_payload.get("devices") or {}

        def aggregate(field: str) -> float | None:
            explicit = raw_total.get(field)
            if isinstance(explicit, (int, float)):
                return float(explicit)
            values = [
                float(value[field])
                for value in devices.values()
                if isinstance(value, dict) and isinstance(value.get(field), (int, float))
            ]
            return sum(values) if values else None

        return {
            "baseline_bytes": aggregate("baseline_bytes"),
            "peak_bytes": aggregate("peak_bytes"),
            "byte_seconds": aggregate("byte_seconds"),
            "samples": raw_total.get("samples"),
        }

    pi = arm("PI_NATIVE", "arm_a_pi", "pi_native")
    orchid = arm("ORCHID", "arm_b_orchid", "orchid")
    pi_main = device(pi, "main_gpu")
    orchid_main = device(orchid, "main_gpu")
    orchid_selector = device(orchid, "selector_gpu")
    pi_total = total(pi)
    orchid_total = total(orchid)
    required_devices = (pi_main, orchid_main, orchid_selector)
    complete = bool(
        payload.get("arm_isolated") is True
        and payload.get("measurement_method")
        in {"hardware_samples", "measured_residency_plus_architecture_kv"}
        and all(
            isinstance(item.get("baseline_bytes"), (int, float))
            and isinstance(item.get("peak_bytes"), (int, float))
            and isinstance(item.get("byte_seconds"), (int, float))
            and int(item.get("samples") or 0) > 0
            for item in required_devices
        )
        and all(
            isinstance(value, (int, float))
            for value in (
                pi_total["baseline_bytes"],
                pi_total["peak_bytes"],
                pi_total["byte_seconds"],
                orchid_total["baseline_bytes"],
                orchid_total["peak_bytes"],
                orchid_total["byte_seconds"],
            )
        )
    )
    selector_capacity = orchid_selector.get("capacity_bytes")
    selector_peak = orchid_selector.get("peak_bytes")
    selector_fraction = orchid_selector.get("peak_fraction")
    if (
        not isinstance(selector_fraction, (int, float))
        and isinstance(selector_capacity, (int, float))
        and selector_capacity > 0
        and isinstance(selector_peak, (int, float))
    ):
        selector_fraction = float(selector_peak) / float(selector_capacity)
    return {
        "present": True,
        "complete": complete,
        "path": str(path),
        "schema_version": payload.get("schema_version"),
        "measurement_method": payload.get("measurement_method"),
        "arm_isolated": payload.get("arm_isolated") is True,
        "pi_native": {"main_gpu": pi_main, "total": pi_total},
        "orchid": {
            "main_gpu": orchid_main,
            "selector_gpu": orchid_selector,
            "selector_peak_fraction": (
                float(selector_fraction)
                if isinstance(selector_fraction, (int, float))
                else None
            ),
            "total": orchid_total,
        },
    }


def _is_provider_billed_stage(row: dict[str, Any]) -> bool:
    scope = str(row.get("billing_scope") or row.get("execution_scope") or "").lower()
    if scope in {"provider", "cloud", "remote"}:
        return True
    if scope in {"local", "secondary_gpu", "software"}:
        return False
    stage = str(row.get("stage") or "").lower()
    if stage in {"selector", "canonicalizer"}:
        return False
    # Unknown stages are counted as remote so incomplete telemetry can never
    # make ORCHID's API bill look artificially smaller.
    return True


def _stage_token_classes(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {
        "uncached_input_tokens": sum(float(row.get("input_tokens") or 0) for row in rows),
        "cache_read_tokens": sum(float(row.get("cache_read_tokens") or 0) for row in rows),
        "cache_write_tokens": sum(float(row.get("cache_write_tokens") or 0) for row in rows),
        "output_tokens": sum(float(row.get("output_tokens") or 0) for row in rows),
    }


def compare_api_token_classes(
    pi_classes: dict[str, float],
    orchid_classes: dict[str, float],
) -> dict[str, Any]:
    savings_by_class: dict[str, float | None] = {}
    active_savings: list[float] = []
    noninferior = True
    for key in TOKEN_CLASS_KEYS:
        pi_value = float(pi_classes.get(key) or 0)
        orchid_value = float(orchid_classes.get(key) or 0)
        if pi_value > 0:
            saving = (pi_value - orchid_value) / pi_value
            savings_by_class[key] = saving
            active_savings.append(saving)
        elif orchid_value > 0:
            # PI pays zero units in this class. There is no finite relative
            # saving, and ORCHID cannot dominate for arbitrary nonnegative
            # class prices. -1 is a finite, gate-failing sentinel.
            savings_by_class[key] = -1.0
            active_savings.append(-1.0)
        else:
            savings_by_class[key] = None
        noninferior = noninferior and orchid_value <= pi_value
    return {
        "pi": pi_classes,
        "orchid": orchid_classes,
        "savings_fraction_by_class": savings_by_class,
        "componentwise_noninferior": noninferior,
        "guaranteed_savings_fraction": min(active_savings) if active_savings else None,
        "proof": (
            "If every billed token class is reduced by at least this fraction, "
            "ORCHID is cheaper for every nonnegative provider rate vector."
        ),
    }


def compute_economics(
    *,
    pi_rpc: dict[str, Any],
    orchid_rpc: dict[str, Any],
    memory_transitions: dict[str, Any],
    pricing: dict[str, Any],
) -> dict[str, Any]:
    model_runs_by_stage = memory_transitions.get("model_runs_by_stage")
    if isinstance(model_runs_by_stage, list) and model_runs_by_stage:
        memory_pipeline = list(model_runs_by_stage)
        # Preserve the original all-stage calculation for historical replay.
        legacy_paid_memory_stages = list(model_runs_by_stage)
    else:
        memory_pipeline = memory_transitions.get("generations", [])
        legacy_paid_memory_stages = []
        if isinstance(memory_pipeline, list) and memory_pipeline and isinstance(memory_pipeline[0], dict):
            if "selector_calls" in memory_pipeline[0]:
                legacy_paid_memory_stages = [
                    {
                        "stage": "consolidator",
                        "billing_scope": "provider",
                        "calls": sum(int(row.get("consolidator_calls") or 0) for row in memory_pipeline),
                        "input_tokens": sum(
                            int(row.get("consolidator_input_tokens") or 0) for row in memory_pipeline
                        ),
                        "output_tokens": sum(
                            int(row.get("consolidator_output_tokens") or 0) for row in memory_pipeline
                        ),
                        "wall_ms": sum(
                            float(row.get("consolidator_latency_ms") or 0) for row in memory_pipeline
                        ),
                    }
                ]
            else:
                legacy_paid_memory_stages = list(memory_pipeline)
    cloud_memory_stages = [
        row for row in legacy_paid_memory_stages if _is_provider_billed_stage(row)
    ]
    local_memory_stages = [
        row for row in memory_pipeline if not _is_provider_billed_stage(row)
    ]
    legacy_memory_classes = _stage_token_classes(legacy_paid_memory_stages)
    cloud_memory_classes = _stage_token_classes(cloud_memory_stages)
    local_memory_classes = _stage_token_classes(local_memory_stages)
    memory_input = legacy_memory_classes["uncached_input_tokens"]
    memory_output = legacy_memory_classes["output_tokens"]
    legacy_memory_cost = _cost_for_token_classes(
        legacy_memory_classes,
        pricing,
        require_complete=False,
    )
    cloud_memory_cost = _cost_for_token_classes(
        cloud_memory_classes,
        pricing,
        require_complete=False,
    )
    cloud_memory_fully_priced_cost = _cost_for_token_classes(
        cloud_memory_classes,
        pricing,
        require_complete=True,
    )
    foreground_legacy_cost = orchid_rpc.get("calculated_cost_usd")
    orchid_total = (
        float(foreground_legacy_cost) + float(legacy_memory_cost)
        if foreground_legacy_cost is not None and legacy_memory_cost is not None
        else None
    )
    orchid_total_fully_priced = (
        float(orchid_rpc["fully_priced_cost_usd"])
        + float(cloud_memory_fully_priced_cost)
        if orchid_rpc.get("fully_priced_cost_usd") is not None
        and cloud_memory_fully_priced_cost is not None
        else None
    )
    pi_input = float(pi_rpc["input_tokens"])
    orchid_input = float(orchid_rpc["input_tokens"])
    pi_prompt = float(pi_rpc.get("prompt_tokens") or pi_input)
    orchid_prompt = float(orchid_rpc.get("prompt_tokens") or orchid_input)
    memory_pipeline = (
        list(model_runs_by_stage)
        if isinstance(model_runs_by_stage, list) and model_runs_by_stage
        else legacy_paid_memory_stages
    )
    pi_api_classes = dict(pi_rpc.get("api_token_classes") or {})
    orchid_api_classes = _add_token_classes(
        orchid_rpc.get("api_token_classes") or {},
        cloud_memory_classes,
    )
    token_class_cost = compare_api_token_classes(pi_api_classes, orchid_api_classes)
    pricing_complete = (
        pi_rpc.get("fully_priced_cost_usd") is not None
        and orchid_total_fully_priced is not None
    )
    corrected_cost_delta = (
        float(pi_rpc["fully_priced_cost_usd"]) - orchid_total_fully_priced
        if pricing_complete
        else None
    )

    foreground_keys = (
        "usage_responses",
        "input_tokens",
        "uncached_input_tokens",
        "prompt_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "api_token_classes",
        "reported_cost_usd",
        "calculated_cost_usd",
        "fully_priced_cost_usd",
        "pricing_known",
        "pricing_complete_for_observed_usage",
        "largest_context_tokens",
        "mean_context_tokens",
        "largest_prompt_tokens",
        "mean_prompt_tokens",
        "foreground_latency_ms",
    )
    return {
        "pricing_usd_per_million_tokens": pricing,
        "pricing_known": _base_pricing_known(pricing),
        "pricing_complete_for_observed_usage": pricing_complete,
        "cost_basis": "dollar_rate_card" if pricing_complete else "token_class_dominance",
        "counting_rule": pi_rpc["counting_rule"],
        "pi_foreground": {
            key: pi_rpc[key]
            for key in foreground_keys
        },
        "orchid_foreground": {
            key: orchid_rpc[key]
            for key in foreground_keys
        },
        "orchid_memory_pipeline": memory_pipeline,
        "orchid_cloud_memory_stages": cloud_memory_stages,
        "orchid_local_memory_stages": local_memory_stages,
        "local_selector_cost_usd": 0.0,
        "orchid_local_memory_token_classes": local_memory_classes,
        "orchid_cloud_memory_token_classes": cloud_memory_classes,
        "orchid_memory_input_tokens": memory_input,
        "orchid_memory_output_tokens": memory_output,
        # Legacy all-stage fields reproduce the sealed Solar scorecard. New
        # release logic uses orchid_cloud_memory_* and orchid_total_api_*.
        "orchid_memory_cost_usd": legacy_memory_cost,
        "orchid_total_provider_equivalent_cost_usd": orchid_total,
        "orchid_cloud_memory_cost_usd": cloud_memory_cost,
        "orchid_cloud_memory_fully_priced_cost_usd": cloud_memory_fully_priced_cost,
        "pi_total_fully_priced_cost_usd": pi_rpc.get("fully_priced_cost_usd"),
        "orchid_total_fully_priced_cost_usd": orchid_total_fully_priced,
        "api_token_class_comparison": token_class_cost,
        "cumulative_foreground_context_token_area": {
            "pi_native": pi_prompt,
            "orchid": orchid_prompt,
            "absolute_difference_pi_minus_orchid": pi_prompt - orchid_prompt,
            "fraction_saved_vs_pi": (1 - orchid_prompt / pi_prompt) if pi_prompt else None,
            "counting_rule": "uncached input plus cache-read input",
            "legacy_uncached_only": {
                "pi_native": pi_input,
                "orchid": orchid_input,
            },
        },
        "paired_deltas": {
            "total_cost_usd_pi_minus_orchid": corrected_cost_delta,
            "prompt_token_area_pi_minus_orchid": pi_prompt - orchid_prompt,
            "peak_context_pi_minus_orchid": (
                pi_rpc["largest_prompt_tokens"] - orchid_rpc["largest_prompt_tokens"]
            ),
            "foreground_latency_median_ms_pi_minus_orchid": (
                pi_rpc["foreground_latency_ms"]["median"]
                - orchid_rpc["foreground_latency_ms"]["median"]
            ),
        },
    }


def validate_run(artifact: Path, *, require_hidden_eval: bool = True) -> RunValidity:
    reasons: list[str] = []
    manifest_path = artifact / "BENCHMARK_MANIFEST.json"
    if not manifest_path.exists():
        reasons.append("missing BENCHMARK_MANIFEST.json")
    driver_path = artifact / "driver_summary.json"
    if not driver_path.exists():
        reasons.append("missing driver_summary.json")
    pi_telemetry = artifact / "pi_native/telemetry.jsonl"
    orchid_telemetry = artifact / "orchid/telemetry.jsonl"
    if not pi_telemetry.exists():
        reasons.append("missing pi_native/telemetry.jsonl")
    if not orchid_telemetry.exists():
        reasons.append("missing orchid/telemetry.jsonl")
    if require_hidden_eval and not (artifact / "quality/hidden_eval.json").exists():
        reasons.append("missing quality/hidden_eval.json")
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        policy = manifest.get("run_policy") or {}
        if policy.get("manual_rescue") not in {None, "forbidden"}:
            reasons.append("manual_rescue is not forbidden")
        driver = load_json(driver_path) if driver_path.exists() else {}
        reason = str((driver or {}).get("reason") or "")
        if "manual_rescue" in reason.lower() or "controller_restart" in reason.lower():
            reasons.append(f"driver indicates manual intervention: {reason}")
    pi_rows = load_jsonl(pi_telemetry) if pi_telemetry.exists() else []
    orchid_rows = load_jsonl(orchid_telemetry) if orchid_telemetry.exists() else []
    if pi_rows and not any(
        row.get("type") == "message_end"
        and (row.get("message") or {}).get("role") == "assistant"
        and ((row.get("message") or {}).get("usage") or {}).get("input")
        for row in pi_rows
    ):
        reasons.append("pi telemetry has no provider usage records")
    if orchid_rows and not any(
        row.get("type") == "message_end"
        and (row.get("message") or {}).get("role") == "assistant"
        and ((row.get("message") or {}).get("usage") or {}).get("input")
        for row in orchid_rows
    ):
        reasons.append("orchid telemetry has no provider usage records")
    return RunValidity(valid=not reasons, reasons=tuple(reasons))


def _normalize_pricing(raw: dict[str, Any] | None) -> dict[str, float | None]:
    raw = raw or {}
    nested = raw.get("pricing_usd_per_million_tokens")
    prices = nested if isinstance(nested, dict) else raw

    def first_numeric(*values: Any) -> float | None:
        for value in values:
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return float(value)
        return None

    return {
        "input": first_numeric(
            prices.get("input"),
            raw.get("uncached_input_usd_per_million"),
        ),
        "output": first_numeric(prices.get("output")),
        "cache_read": first_numeric(
            prices.get("cache_read"),
            prices.get("cached_input"),
            raw.get("cached_input_usd_per_million"),
        ),
        "cache_write": first_numeric(
            prices.get("cache_write"),
            prices.get("cache_creation"),
            raw.get("cache_write_usd_per_million"),
        ),
    }


def generate_scorecard(artifact: Path, *, require_hidden_eval: bool = True) -> dict[str, Any]:
    artifact = artifact.resolve()
    validity = validate_run(artifact, require_hidden_eval=require_hidden_eval)
    manifest = load_json(artifact / "BENCHMARK_MANIFEST.json") if (artifact / "BENCHMARK_MANIFEST.json").exists() else {}
    config_orchid = load_json(artifact / "CONFIG_ORCHID.json") if (artifact / "CONFIG_ORCHID.json").exists() else {}
    raw_pricing = (
        config_orchid.get("pricing_usd_per_million_tokens")
        or manifest.get("rate_card")
        or {}
    )
    if not raw_pricing:
        economics_existing = artifact / "economics/SUMMARY.json"
        if economics_existing.exists():
            raw_pricing = load_json(economics_existing).get(
                "pricing_usd_per_million_tokens"
            ) or {}
    pricing = _normalize_pricing(raw_pricing)
    if not _base_pricing_known(pricing):
        pareto = manifest.get("pareto") or {}
        pareto_rate_card = pareto.get("rate_card") or {}
        pareto_pricing = _normalize_pricing(pareto_rate_card)
        pricing = (
            pareto_pricing
            if _base_pricing_known(pareto_pricing)
            else _normalize_pricing(DEFAULT_RATE_CARD)
        )
    pi_rpc = summarize_rpc_telemetry(artifact / "pi_native/telemetry.jsonl", pricing=pricing)
    orchid_rpc = summarize_rpc_telemetry(artifact / "orchid/telemetry.jsonl", pricing=pricing)
    memory_transitions = summarize_memory_transitions(artifact)
    economics = compute_economics(
        pi_rpc=pi_rpc,
        orchid_rpc=orchid_rpc,
        memory_transitions=memory_transitions,
        pricing=pricing,
    )
    substitution_paths = [
        artifact / "orchid/foreground_substitution.jsonl",
        artifact / "foreground_substitution.jsonl",
    ]
    substitution = next(
        (
            summarize_foreground_substitution(path, pricing=pricing)
            for path in substitution_paths
            if path.exists()
        ),
        {"present": False},
    )
    pi_direct_latency = summarize_direct_foreground_latency(
        artifact / "pi_native/foreground_requests.jsonl"
    )
    orchid_direct_path = artifact / "orchid/foreground_requests.jsonl"
    if not orchid_direct_path.exists():
        orchid_direct_path = next(
            (path for path in substitution_paths if path.exists()),
            orchid_direct_path,
        )
    orchid_direct_latency = summarize_direct_foreground_latency(orchid_direct_path)
    scorecard = {
        "schema_version": "orchid.benchmark_scorecard.v1",
        "artifact": str(artifact),
        "run_id": manifest.get("run_id"),
        "validity": {
            "valid": validity.valid,
            "reasons": list(validity.reasons),
        },
        "pi_native": pi_rpc,
        "orchid": orchid_rpc,
        "economics": economics,
        "memory_transitions": memory_transitions,
        "foreground_substitution": substitution,
        "direct_foreground_latency": {
            "pi_native": pi_direct_latency,
            "orchid": orchid_direct_latency,
        },
        "config_orchid": config_orchid,
        "quality": summarize_quality(artifact),
        "selector_gpu": summarize_selector_gpu(artifact),
        "paired_vram": summarize_paired_vram(artifact),
        "driver": load_json(artifact / "driver_summary.json")
        if (artifact / "driver_summary.json").exists()
        else None,
    }
    return scorecard


def compare_to_published(
    scorecard: dict[str, Any],
    *,
    published_economics_path: Path,
    tolerance_usd: float = 1e-6,
    tolerance_tokens: float = 0.5,
) -> dict[str, Any]:
    published = load_json(published_economics_path)
    economics = scorecard["economics"]
    checks = {
        "orchid_total_cost": abs(
            economics["orchid_total_provider_equivalent_cost_usd"]
            - published["orchid_total_provider_equivalent_cost_usd"]
        )
        <= tolerance_usd,
        "pi_input_tokens": abs(
            economics["pi_foreground"]["input_tokens"] - published["pi_foreground"]["input_tokens"]
        )
        <= tolerance_tokens,
        "orchid_input_tokens": abs(
            economics["orchid_foreground"]["input_tokens"]
            - published["orchid_foreground"]["input_tokens"]
        )
        <= tolerance_tokens,
        "orchid_memory_cost": abs(
            economics["orchid_memory_cost_usd"] - published["orchid_memory_cost_usd"]
        )
        <= tolerance_usd,
    }
    return {
        "published_path": str(published_economics_path),
        "checks": checks,
        "all_ok": all(checks.values()),
    }
