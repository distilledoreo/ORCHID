from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..context import ContextSnapshot


def telemetry_path() -> Path | None:
    raw = os.environ.get("ORCHID_FOREGROUND_TELEMETRY_PATH")
    if not raw:
        return None
    path = Path(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(json.dumps(message, ensure_ascii=False)) for message in messages)


def substitution_record(
    *,
    request_id: str,
    thread_id: str,
    inbound: list[dict[str, Any]],
    snapshot: ContextSnapshot,
    stream: bool,
) -> dict[str, Any]:
    substitution = snapshot.substitution
    record = {
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "request_id": request_id,
        "thread_id": thread_id,
        "stream": stream,
        "inbound_messages": len(inbound),
        "inbound_chars": _chars(inbound),
        "inbound_estimated_tokens": (
            substitution.inbound_request_tokens if substitution else sum(max(1, _chars([m]) // 4) for m in inbound)
        ),
        "assembled_messages": len(snapshot.messages),
        "assembled_chars": _chars(snapshot.messages),
        "assembled_estimated_tokens": snapshot.estimated_tokens,
        "foreground_target_tokens": snapshot.foreground_target_tokens,
        "foreground_hard_limit_tokens": snapshot.foreground_hard_limit_tokens,
        "foreground_target_exceeded": snapshot.target_exceeded,
        "foreground_hard_limit_exceeded": snapshot.hard_limit_exceeded,
        "emergency_spill_tokens": snapshot.emergency_spill_tokens,
        "organizer_lag_events": snapshot.organizer_lag_events,
        "organizer_lag_tokens": snapshot.organizer_lag_tokens,
        "compaction_wait_ms": snapshot.compaction_wait_ms,
        "memory_maintenance_awaited": snapshot.memory_maintenance_awaited,
        "assembly_wall_ms": snapshot.assembly_wall_ms,
        "component_tokens": dict(snapshot.component_tokens),
        "active": substitution.active if substitution else False,
        "active_capsule_id": snapshot.active_capsule_id,
        "active_tokens": substitution.active_tokens if substitution else snapshot.capsule_tokens,
        "uncovered_tail_tokens": substitution.uncovered_tail_tokens if substitution else snapshot.raw_tail_tokens,
        "suppressed_covered_tokens": substitution.suppressed_covered_tokens if substitution else 0,
        "protocol_continuity_tokens": substitution.protocol_continuity_tokens if substitution else 0,
        "covered_raw_tokens_forwarded": substitution.covered_raw_tokens_forwarded if substitution else 0,
        "non_protocol_covered_raw_tokens_forwarded": (
            0
            if substitution is None
            else max(
                0,
                substitution.covered_raw_tokens_forwarded
                - substitution.protocol_continuity_tokens,
            )
        ),
        "provider_input_tokens": None,
        "provider_output_tokens": None,
        "estimator_vs_provider_ratio": None,
    }
    if snapshot.prompt_cache is not None:
        record["prompt_cache"] = snapshot.prompt_cache.to_dict()
    return record


def append_record(record: dict[str, Any]) -> None:
    path = telemetry_path()
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def provider_usage_tokens(payload: Any) -> tuple[int | None, int | None, int | None, int | None]:
    if not isinstance(payload, dict):
        return None, None, None, None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None, None, None
    prompt = usage.get("prompt_tokens")
    if prompt is None:
        prompt = usage.get("input_tokens")
    completion = usage.get("completion_tokens")
    if completion is None:
        completion = usage.get("output_tokens")
    cache_read = usage.get("cache_read_tokens")
    if cache_read is None:
        cache_read = usage.get("cacheRead")
    cache_write = usage.get("cache_write_tokens")
    if cache_write is None:
        cache_write = usage.get("cacheWrite")
    return (
        int(prompt) if isinstance(prompt, (int, float)) else None,
        int(completion) if isinstance(completion, (int, float)) else None,
        int(cache_read) if isinstance(cache_read, (int, float)) else None,
        int(cache_write) if isinstance(cache_write, (int, float)) else None,
    )


def _provider_input_semantics(payload: Any) -> tuple[int | None, bool | None]:
    """Return uncached input and whether the provider prompt includes cache hits."""

    if not isinstance(payload, dict) or not isinstance(payload.get("usage"), dict):
        return None, None
    usage = payload["usage"]
    prompt, completion, cache_read, _ = provider_usage_tokens(payload)
    if prompt is None:
        return None, None
    if not cache_read:
        return prompt, False
    if "prompt_tokens" in usage:
        return max(0, prompt - cache_read), True

    total = usage.get("total_tokens")
    if total is None:
        total = usage.get("totalTokens")
    if isinstance(total, (int, float)):
        completion_value = completion or 0
        if int(total) == prompt + completion_value:
            return max(0, prompt - cache_read), True
        if int(total) == prompt + completion_value + cache_read:
            return prompt, False

    # Ambiguous old/provider-specific records are counted conservatively as
    # uncached; the scorecard will then add cache reads as a separate class.
    return prompt, False


def finalize_record(record: dict[str, Any], payload: Any) -> dict[str, Any]:
    prompt, completion, cache_read, cache_write = provider_usage_tokens(payload)
    uncached_input, input_includes_cache = _provider_input_semantics(payload)
    record["provider_input_tokens"] = prompt
    record["provider_uncached_input_tokens"] = uncached_input
    record["provider_input_includes_cache"] = input_includes_cache
    record["provider_output_tokens"] = completion
    record["provider_cache_read_tokens"] = cache_read
    record["provider_cache_write_tokens"] = cache_write
    assembled = record.get("assembled_estimated_tokens")
    if isinstance(prompt, int) and prompt > 0 and isinstance(assembled, int):
        record["estimator_vs_provider_ratio"] = round(assembled / prompt, 4)
    prompt_cache = record.get("prompt_cache")
    if isinstance(prompt_cache, dict) and cache_read is not None:
        estimated_read = prompt_cache.get("estimated_cache_read_tokens")
        if isinstance(estimated_read, int) and estimated_read > 0:
            record["cache_estimate_error_tokens"] = cache_read - estimated_read
    return record


def record_provider_latency(
    record: dict[str, Any],
    *,
    provider_wall_ms: float,
    time_to_first_event_ms: float | None = None,
    success: bool = True,
) -> dict[str, Any]:
    record["direct_provider_latency"] = {
        "measurement": "gateway_monotonic_clock",
        "provider_wall_ms": max(0.0, float(provider_wall_ms)),
        "time_to_first_event_ms": (
            max(0.0, float(time_to_first_event_ms))
            if time_to_first_event_ms is not None
            else None
        ),
        "success": bool(success),
    }
    return record


