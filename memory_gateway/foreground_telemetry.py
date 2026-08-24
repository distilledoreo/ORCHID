from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .context import ContextSnapshot


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
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
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
        "active": substitution.active if substitution else False,
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


def append_record(record: dict[str, Any]) -> None:
    path = telemetry_path()
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def provider_usage_tokens(payload: Any) -> tuple[int | None, int | None]:
    if not isinstance(payload, dict):
        return None, None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None
    prompt = usage.get("prompt_tokens")
    if prompt is None:
        prompt = usage.get("input_tokens")
    completion = usage.get("completion_tokens")
    if completion is None:
        completion = usage.get("output_tokens")
    return (
        int(prompt) if isinstance(prompt, (int, float)) else None,
        int(completion) if isinstance(completion, (int, float)) else None,
    )


def finalize_record(record: dict[str, Any], payload: Any) -> dict[str, Any]:
    prompt, completion = provider_usage_tokens(payload)
    record["provider_input_tokens"] = prompt
    record["provider_output_tokens"] = completion
    assembled = record.get("assembled_estimated_tokens")
    if isinstance(prompt, int) and prompt > 0 and isinstance(assembled, int):
        record["estimator_vs_provider_ratio"] = round(assembled / prompt, 4)
    return record


