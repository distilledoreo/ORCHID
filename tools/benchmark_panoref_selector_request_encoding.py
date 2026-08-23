"""Benchmark-only selector request-encoding A/Bs on the frozen PanoRef trace.

This harness keeps the model, backend, 1.2K chunk plan, generation settings,
and evidence fixed.  It measures two request representations independently:

* ``KEEP_SHORT_IDS`` maps only exact legal source IDs to local ``E0`` IDs and
  maps the response back to the frozen IDs before comparison.
* ``DROP_OPAQUE`` keeps the existing opaque IDs but asks for the discard set;
  software derives KEEP as the legal universe minus DROP.

The harness is intentionally disposable.  It does not change production
ORCHID code, retry calls, repair responses, or tune a condition after seeing
an outcome.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.context import estimate_tokens
from memory_gateway.pipeline_adapters import selector_response_format_for_chunk
from tools.benchmark_panoref_selector_prefix_reuse import (
    PROFILE_CONFIG_PATH,
    WRAPPER_PREFIX,
    WRAPPER_SUFFIX,
    build_chunk_lookup,
    endpoint_url,
    file_sha256,
    load_profile_inputs,
)


OUT_ROOT = ROOT / "artifacts/agent_benchmarks/panoref_selector_request_encoding_v1"
PLAN_PATH = OUT_ROOT / "REQUEST_ENCODING_PLAN.json"
CONFIG_PATH = OUT_ROOT / "REQUEST_ENCODING_CONFIG.json"
COMPOSITION_PATH = OUT_ROOT / "PROMPT_COMPOSITION.json"
RUNS_ROOT = OUT_ROOT / "runs"

CALL_COUNT = 148
GATE_COUNT = 24
TIMEOUT_SECONDS = 180.0
MODEL = "qwen3.5-4b@q6_k"

KEEP_SYSTEM_PROMPT = (
    'You select durable-memory source items. Return JSON only with exactly '
    '{"selected_event_ids":["source-item-id"]}. Select only whole-event or '
    'source-span IDs present in the input; do not summarize or rewrite items.'
)
DROP_SYSTEM_PROMPT = (
    'You select durable-memory source items to discard. Return JSON only with '
    'exactly {"dropped_event_ids":["source-item-id"]}. Select only whole-event '
    'or source-span IDs present in the input that should be discarded from '
    'durable memory; do not summarize or rewrite items.'
)

VARIANTS = (
    "KEEP_OPAQUE",
    "KEEP_SHORT_IDS",
    "DROP_OPAQUE",
    "DROP_SHORT_IDS",
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def evenly_spaced_indices(total: int, count: int) -> list[int]:
    if total <= 0 or count <= 0 or count > total:
        raise ValueError(f"invalid positions total={total} count={count}")
    if count == 1:
        return [0]
    indices = [round(i * (total - 1) / (count - 1)) for i in range(count)]
    if len(set(indices)) != count:
        raise ValueError("selected indices were not unique")
    return indices


def load_historical_summary(profile_config: dict[str, Any]) -> dict[str, Any]:
    path = Path(profile_config["historical_selector_summary"])
    summary = read_json(path)
    telemetry = summary["selector"]["telemetry"]
    by_chunk = {int(row["chunk_index"]): row for row in telemetry}
    if set(by_chunk) != set(range(CALL_COUNT)):
        raise RuntimeError("historical selector summary does not cover all 148 chunks")
    return {
        "path": str(path.resolve()),
        "sha256": file_sha256(path),
        "by_chunk": by_chunk,
    }


def replace_exact_strings(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: replace_exact_strings(child, mapping) for key, child in value.items()}
    if isinstance(value, list):
        return [replace_exact_strings(child, mapping) for child in value]
    if isinstance(value, str):
        return mapping.get(value, value)
    return value


def local_id_mapping(event_ids: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    forward = {event_id: f"E{index}" for index, event_id in enumerate(event_ids)}
    reverse = {local: original for original, local in forward.items()}
    return forward, reverse


def drop_response_format(allowed_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "selector_drop_response_v1",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "dropped_event_ids": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(allowed_ids)},
                    }
                },
                "required": ["dropped_event_ids"],
                "additionalProperties": False,
            },
        },
    }


def variant_definition(variant: str) -> dict[str, Any]:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant}")
    return {
        "variant": variant,
        "selection_mode": "DROP" if variant.startswith("DROP") else "KEEP",
        "id_mode": "SHORT_LOCAL" if variant.endswith("SHORT_IDS") else "OPAQUE",
        "changed_fields": (
            ["payload exact legal ID strings", "response schema enum values"]
            if variant.endswith("SHORT_IDS")
            else (["system prompt", "response schema property name"] if variant.startswith("DROP") else [])
        ),
        "mapping": (
            "exact string equality only; response IDs map back through the per-chunk inverse map"
            if variant.endswith("SHORT_IDS")
            else "none"
        ),
    }


def build_request(
    plan: dict[str, Any], chunk: dict[str, Any], variant: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], dict[str, str]]:
    original_ids = list(chunk["event_ids"])
    forward, reverse = local_id_mapping(original_ids)
    short_ids = variant.endswith("SHORT_IDS")
    drop_mode = variant.startswith("DROP")
    request_ids = [forward[event_id] for event_id in original_ids] if short_ids else original_ids
    payload = replace_exact_strings(chunk["payload"], forward) if short_ids else chunk["payload"]
    schema = drop_response_format(request_ids) if drop_mode else selector_response_format_for_chunk(request_ids)
    system_prompt = DROP_SYSTEM_PROMPT if drop_mode else plan["system_prompt"]
    user_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    body = {
        "model": plan["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": WRAPPER_PREFIX + user_payload + WRAPPER_SUFFIX},
        ],
        **plan["profile_generation"],
        "response_format": schema,
    }
    profile = {
        "system_prompt_chars": len(system_prompt),
        "wrapper_chars": len(WRAPPER_PREFIX) + len(WRAPPER_SUFFIX),
        "evidence_json_chars": len(json.dumps(payload["events"], ensure_ascii=False, sort_keys=True)),
        "payload_json_chars": len(user_payload),
        "schema_chars": len(json.dumps(schema, ensure_ascii=False, sort_keys=True)),
        "legal_id_count": len(original_ids),
        "original_id_chars": sum(len(event_id) for event_id in original_ids),
        "request_id_chars": sum(len(event_id) for event_id in request_ids),
        "estimated_system_tokens": estimate_tokens(system_prompt),
        "estimated_wrapper_tokens": estimate_tokens(WRAPPER_PREFIX + WRAPPER_SUFFIX),
        "estimated_evidence_tokens": estimate_tokens(json.dumps(payload["events"], ensure_ascii=False, sort_keys=True)),
        "estimated_payload_tokens": estimate_tokens(user_payload),
        "estimated_schema_tokens": estimate_tokens(json.dumps(schema, ensure_ascii=False, sort_keys=True)),
    }
    identity = {
        "variant": variant,
        "request_hash": digest(body),
        "original_payload_hash": digest(chunk["payload"]),
        "request_payload_hash": digest(payload),
        "response_schema_hash": digest(schema),
        "original_event_ids_hash": digest(original_ids),
        "request_event_ids_hash": digest(request_ids),
        "system_prompt_hash": digest(system_prompt),
        "evidence_hash": digest(chunk["payload"]["events"]),
        "local_id_map_hash": digest(forward),
        "profile": profile,
    }
    return body, identity, forward, reverse


def plan_without_hash() -> dict[str, Any]:
    profile_config, sample, items = load_profile_inputs()
    lookup = build_chunk_lookup(profile_config, items)
    if len(lookup) != CALL_COUNT:
        raise RuntimeError(f"expected {CALL_COUNT} chunks, got {len(lookup)}")
    historical = load_historical_summary(profile_config)
    chunk_records: list[dict[str, Any]] = []
    for index in range(CALL_COUNT):
        chunk = lookup[index]
        baseline_body, baseline_identity, _forward, _reverse = build_request(
            {"model": MODEL, "system_prompt": profile_config["system_prompt"], "profile_generation": profile_config["profile_generation"]},
            chunk,
            "KEEP_OPAQUE",
        )
        chunk_records.append(
            {
                "chunk_index": index,
                "event_ids": chunk["event_ids"],
                "event_count": len(chunk["items"]),
                "estimated_source_tokens": chunk["estimated_source_tokens"],
                "original_payload_hash": digest(chunk["payload"]),
                "baseline_request_hash": baseline_identity["request_hash"],
                "baseline_schema_hash": baseline_identity["response_schema_hash"],
                "historical_input_tokens": historical["by_chunk"][index].get("input_tokens"),
                "historical_output_tokens": historical["by_chunk"][index].get("output_tokens"),
                "historical_selected_hash": digest(sorted(historical["by_chunk"][index].get("selected_event_ids") or [])),
            }
        )
    return {
        "schema_version": "panoref-selector-request-encoding-plan-v1",
        "created_at": utc_now(),
        "source_commit": git_commit(),
        "replay": profile_config["replay"],
        "replay_sha256": profile_config["replay_sha256"],
        "profile_config": str(PROFILE_CONFIG_PATH.resolve()),
        "profile_config_sha256": file_sha256(PROFILE_CONFIG_PATH),
        "historical_selector_summary": historical["path"],
        "historical_selector_summary_sha256": historical["sha256"],
        "endpoint": profile_config["endpoint"],
        "model": MODEL,
        "prompt_version": profile_config["prompt_version"],
        "system_prompt": profile_config["system_prompt"],
        "drop_system_prompt": DROP_SYSTEM_PROMPT,
        "chunk_target_tokens": profile_config["chunk_target_tokens"],
        "chunk_count": CALL_COUNT,
        "gate_count": GATE_COUNT,
        "gate_chunk_indices": evenly_spaced_indices(CALL_COUNT, GATE_COUNT),
        "profile_generation": profile_config["profile_generation"],
        "timeout_seconds": TIMEOUT_SECONDS,
        "no_retries": True,
        "no_concurrency": True,
        "no_prompt_policy_tuning": True,
        "variants": {variant: variant_definition(variant) for variant in VARIANTS},
        "chunk_plan_sha256": digest(chunk_records),
        "chunk_plan": chunk_records,
        "composition_method": {
            "provider_total": "historical prompt_tokens and per-run usage.prompt_tokens",
            "component_estimate": "repository dependency-free estimate_tokens, len(text)//4; directional only",
            "exact_id_savings": "request serialization byte/character deltas and exact local-map counts",
        },
    }


def freeze() -> None:
    if PLAN_PATH.exists() or CONFIG_PATH.exists():
        raise RuntimeError(f"refusing to overwrite frozen plan under {OUT_ROOT}")
    plan = plan_without_hash()
    plan["plan_sha256"] = digest({key: value for key, value in plan.items()})
    write_json(PLAN_PATH, plan)
    write_json(
        CONFIG_PATH,
        {
            "schema_version": "panoref-selector-request-encoding-config-v1",
            "plan_path": str(PLAN_PATH.resolve()),
            "plan_sha256": plan["plan_sha256"],
            "source_commit": plan["source_commit"],
            "arm_order": list(VARIANTS),
            "no_retries": True,
            "no_concurrency": True,
        },
    )
    print(f"plan: {PLAN_PATH}")
    print(f"config: {CONFIG_PATH}")
    print(f"plan_sha256: {plan['plan_sha256']}")
    print(f"gate_chunks: {plan['gate_chunk_indices']}")


def verify_plan(plan: dict[str, Any]) -> None:
    recorded = plan.get("plan_sha256")
    without = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if recorded != digest(without):
        raise RuntimeError("frozen request-encoding plan hash mismatch")
    if git_commit() != plan["source_commit"]:
        code_path = "tools/benchmark_panoref_selector_request_encoding.py"
        unchanged = subprocess.run(
            ["git", "diff", "--quiet", plan["source_commit"], "HEAD", "--", code_path],
            cwd=ROOT,
        ).returncode == 0
        if not unchanged:
            raise RuntimeError("benchmark code differs from frozen source commit")
    if file_sha256(Path(plan["replay"])) != plan["replay_sha256"]:
        raise RuntimeError("frozen replay changed")
    if file_sha256(PROFILE_CONFIG_PATH) != plan["profile_config_sha256"]:
        raise RuntimeError("frozen profile config changed")
    if file_sha256(Path(plan["historical_selector_summary"])) != plan["historical_selector_summary_sha256"]:
        raise RuntimeError("historical selector summary changed")


def parse_response(content: str, allowed_ids: list[str], drop_mode: bool) -> tuple[list[str] | None, str | None]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        return None, f"assistant content was not JSON: {exc}"
    key = "dropped_event_ids" if drop_mode else "selected_event_ids"
    if not isinstance(value, dict) or set(value) != {key}:
        return None, f"selector response did not have exactly {key}"
    selected = value.get(key)
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        return None, f"{key} was not a string array"
    if len(selected) != len(set(selected)):
        return None, f"{key} contained duplicate IDs"
    if not set(selected).issubset(set(allowed_ids)):
        return None, f"selector returned an ID outside the current chunk"
    return selected, None


def metric_number(value: Any, keys: set[str]) -> float | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in keys:
                try:
                    return float(child)
                except (TypeError, ValueError):
                    pass
            nested = metric_number(child, keys)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = metric_number(child, keys)
            if nested is not None:
                return nested
    return None


async def call_selector(
    client: httpx.AsyncClient,
    plan: dict[str, Any],
    chunk: dict[str, Any],
    variant: str,
    call_index: int,
    historical_row: dict[str, Any],
) -> dict[str, Any]:
    body, identity, forward, reverse = build_request(plan, chunk, variant)
    drop_mode = variant.startswith("DROP")
    request_ids = [forward[event_id] for event_id in chunk["event_ids"]] if variant.endswith("SHORT_IDS") else list(chunk["event_ids"])
    started = time.perf_counter()
    first_content_at: float | None = None
    content = ""
    usage: dict[str, Any] | None = None
    response_status: int | None = None
    finish_reason: str | None = None
    response_headers: dict[str, str] = {}
    frames = 0
    error: str | None = None
    error_category: str | None = None
    try:
        async with client.stream(
            "POST",
            endpoint_url(plan["endpoint"]),
            json=body,
            headers={"content-type": "application/json"},
        ) as response:
            response_status = response.status_code
            response_headers = dict(response.headers)
            if response.status_code >= 400:
                error_category = "http"
                error = f"HTTP {response.status_code}: {(await response.aread())[:500]!r}"
            else:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        continue
                    payload = json.loads(raw)
                    frames += 1
                    usage = payload.get("usage") or usage
                    choice = (payload.get("choices") or [{}])[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    token = delta.get("content") or choice.get("text") or ""
                    if token and first_content_at is None:
                        first_content_at = time.perf_counter()
                    content += token
    except httpx.TimeoutException as exc:
        error_category = "timeout"
        error = str(exc) or "request timed out"
    except Exception as exc:
        error_category = "protocol_or_transport"
        error = f"{type(exc).__name__}: {exc}"
    ended = time.perf_counter()
    total_ms = (ended - started) * 1000
    ttft_ms = (first_content_at - started) * 1000 if first_content_at else None
    decode_ms = (ended - first_content_at) * 1000 if first_content_at else None
    request_selected: list[str] | None = None
    original_selected: list[str] | None = None
    if error is None:
        request_selected, validation_error = parse_response(content, request_ids, drop_mode)
        if validation_error:
            error_category = "selector_protocol"
            error = validation_error
        else:
            if variant.endswith("SHORT_IDS"):
                request_selected = [reverse[item] for item in request_selected or []]
            else:
                request_selected = list(request_selected or [])
            if drop_mode:
                dropped = set(request_selected)
                original_selected = [event_id for event_id in chunk["event_ids"] if event_id not in dropped]
            else:
                original_selected = request_selected
    historical_selected = set(historical_row.get("selected_event_ids") or [])
    actual_selected_set = set(original_selected or []) if original_selected is not None else None
    decision_match = actual_selected_set == historical_selected if actual_selected_set is not None else False
    input_tokens = (usage or {}).get("prompt_tokens")
    output_tokens = (usage or {}).get("completion_tokens")
    return {
        "timestamp": utc_now(),
        "variant": variant,
        "call_index": call_index,
        "chunk_index": chunk["chunk_index"],
        "chunk_event_ids": chunk["event_ids"],
        "request_ids": request_ids,
        "request_identity": identity,
        "status": "SUCCEEDED" if error is None else "FAILED",
        "error": error,
        "error_category": error_category,
        "http_status": response_status,
        "finish_reason": finish_reason,
        "frame_count": frames,
        "assistant_content_chars": len(content),
        "assistant_content_sha256": digest(content) if content else None,
        "request_output_ids": request_selected,
        "selected_event_ids_original": original_selected,
        "selected_event_ids_hash": digest(sorted(original_selected)) if original_selected is not None else None,
        "historical_selected_event_ids_hash": digest(sorted(historical_selected)),
        "decision_match_historical": decision_match,
        "usage": usage,
        "metrics": {
            "total_wall_ms": total_ms,
            "ttft_ms": ttft_ms,
            "decode_ms_client_observed": decode_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_tok_s_ttft_proxy": float(input_tokens) / (ttft_ms / 1000) if input_tokens and ttft_ms else None,
            "output_tok_s_client_observed": float(output_tokens) / (decode_ms / 1000) if output_tokens and decode_ms else None,
        },
        "historical_input_tokens": historical_row.get("input_tokens"),
        "historical_output_tokens": historical_row.get("output_tokens"),
        "response_headers": {
            key: value
            for key, value in response_headers.items()
            if key.lower().startswith("x-") or "time" in key.lower() or "queue" in key.lower()
        },
    }


def summarize(rows: list[dict[str, Any]], planned: int, historical_by_chunk: dict[int, dict[str, Any]]) -> dict[str, Any]:
    succeeded = [row for row in rows if row["status"] == "SUCCEEDED"]
    walls = [row["metrics"]["total_wall_ms"] for row in succeeded if row["metrics"]["total_wall_ms"] is not None]
    ttfts = [row["metrics"]["ttft_ms"] for row in succeeded if row["metrics"]["ttft_ms"] is not None]
    decodes = [row["metrics"]["decode_ms_client_observed"] for row in succeeded if row["metrics"]["decode_ms_client_observed"] is not None]
    input_tokens = [float(row["metrics"]["input_tokens"]) for row in succeeded if row["metrics"]["input_tokens"] is not None]
    output_tokens = [float(row["metrics"]["output_tokens"]) for row in succeeded if row["metrics"]["output_tokens"] is not None]
    def pct(values: list[float], p: float) -> float | None:
        if not values:
            return None
        values = sorted(values)
        index = (len(values) - 1) * p
        lo = math.floor(index)
        hi = math.ceil(index)
        if lo == hi:
            return values[lo]
        return values[lo] + (values[hi] - values[lo]) * (index - lo)
    return {
        "variant": rows[0]["variant"] if rows else None,
        "calls_planned": planned,
        "calls_recorded": len(rows),
        "calls_succeeded": len(succeeded),
        "calls_failed": len(rows) - len(succeeded),
        "full_trace": len(rows) == planned and len(succeeded) == planned,
        "decision_matches": sum(1 for row in rows if row["decision_match_historical"]),
        "decision_total": len(rows),
        "decision_equivalence": len(rows) > 0 and all(row["decision_match_historical"] for row in rows),
        "timeouts": sum(1 for row in rows if row["error_category"] == "timeout"),
        "protocol_or_schema_failures": sum(1 for row in rows if row["error_category"] == "selector_protocol"),
        "selector_wall_ms": sum(walls),
        "ttft_ms": {"p50": pct(ttfts, 0.5), "p95": pct(ttfts, 0.95), "p99": pct(ttfts, 0.99)},
        "wall_ms": {"p50": pct(walls, 0.5), "p95": pct(walls, 0.95)},
        "decode_ms": {"p50": pct(decodes, 0.5), "p95": pct(decodes, 0.95)},
        "input_tokens_total": sum(input_tokens),
        "output_tokens_total": sum(output_tokens),
        "input_tokens_p50": pct(input_tokens, 0.5),
        "output_tokens_p50": pct(output_tokens, 0.5),
        "historical_input_tokens_total": sum(float((historical_by_chunk[row["chunk_index"]].get("input_tokens") or 0)) for row in rows),
        "historical_output_tokens_total": sum(float((historical_by_chunk[row["chunk_index"]].get("output_tokens") or 0)) for row in rows),
        "failed_rows": [
            {"chunk_index": row["chunk_index"], "error_category": row["error_category"], "error": row["error"]}
            for row in rows
            if row["status"] != "SUCCEEDED"
        ],
        "decision_mismatches": [
            {"chunk_index": row["chunk_index"], "actual": row["selected_event_ids_original"], "historical": historical_by_chunk[row["chunk_index"]].get("selected_event_ids")}
            for row in rows
            if not row["decision_match_historical"]
        ],
    }


async def run_variant(plan: dict[str, Any], variant: str, count: int, run_id: str) -> None:
    verify_plan(plan)
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant}")
    if count not in (GATE_COUNT, CALL_COUNT):
        raise ValueError(f"count must be {GATE_COUNT} or {CALL_COUNT}")
    run_dir = RUNS_ROOT / run_id
    if run_dir.exists():
        raise RuntimeError(f"refusing to overwrite existing run directory {run_dir}")
    profile_config, _sample, items = load_profile_inputs()
    lookup = build_chunk_lookup(profile_config, items)
    historical = load_historical_summary(profile_config)
    indices = plan["gate_chunk_indices"] if count == GATE_COUNT else list(range(CALL_COUNT))
    run_dir.mkdir(parents=True)
    write_json(run_dir / "RUN_META.json", {
        "schema_version": "panoref-selector-request-encoding-run-v1",
        "started_at": utc_now(),
        "variant": variant,
        "count": count,
        "chunk_indices": indices,
        "plan_sha256": plan["plan_sha256"],
        "source_commit": plan["source_commit"],
        "no_retries": True,
        "no_concurrency": True,
    })
    rows: list[dict[str, Any]] = []
    timeout = httpx.Timeout(TIMEOUT_SECONDS, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for call_index, chunk_index in enumerate(indices):
            row = await call_selector(client, plan, lookup[chunk_index], variant, call_index, historical["by_chunk"][chunk_index])
            rows.append(row)
            with (run_dir / "telemetry.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            print(json.dumps({"variant": variant, "call_index": call_index, "chunk_index": chunk_index, "status": row["status"], "decision_match": row["decision_match_historical"], "wall_ms": row["metrics"]["total_wall_ms"]}, ensure_ascii=False), flush=True)
    summary = summarize(rows, len(indices), historical["by_chunk"])
    summary.update({"run_id": run_id, "completed_at": utc_now(), "plan_sha256": plan["plan_sha256"], "source_commit": plan["source_commit"]})
    write_json(run_dir / "SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def composition(plan: dict[str, Any]) -> None:
    verify_plan(plan)
    profile_config, _sample, items = load_profile_inputs()
    lookup = build_chunk_lookup(profile_config, items)
    historical = load_historical_summary(profile_config)
    rows: list[dict[str, Any]] = []
    for variant in VARIANTS:
        for index in range(CALL_COUNT):
            _body, identity, _forward, _reverse = build_request(plan, lookup[index], variant)
            row = {
                "variant": variant,
                "chunk_index": index,
                "profile": identity["profile"],
                "historical_input_tokens": historical["by_chunk"][index].get("input_tokens"),
                "historical_output_tokens": historical["by_chunk"][index].get("output_tokens"),
            }
            rows.append(row)
    aggregate: dict[str, Any] = {}
    for variant in VARIANTS:
        selected = [row for row in rows if row["variant"] == variant]
        keys = [
            "system_prompt_chars", "wrapper_chars", "evidence_json_chars", "payload_json_chars", "schema_chars",
            "legal_id_count", "original_id_chars", "request_id_chars", "estimated_system_tokens", "estimated_wrapper_tokens",
            "estimated_evidence_tokens", "estimated_payload_tokens", "estimated_schema_tokens",
        ]
        aggregate[variant] = {
            "chunks": len(selected),
            "totals": {key: sum(float(row["profile"][key]) for row in selected) for key in keys},
            "p50": {key: sorted(float(row["profile"][key]) for row in selected)[len(selected) // 2] for key in keys},
            "historical_provider_input_tokens_total": sum(float(row["historical_input_tokens"] or 0) for row in selected),
            "historical_provider_input_tokens_p50": sorted(float(row["historical_input_tokens"] or 0) for row in selected)[len(selected) // 2],
        }
    output = {
        "schema_version": "panoref-selector-request-composition-v1",
        "created_at": utc_now(),
        "plan_sha256": plan["plan_sha256"],
        "source_commit": plan["source_commit"],
        "method": plan["composition_method"],
        "variants": aggregate,
        "per_chunk": rows,
    }
    write_json(COMPOSITION_PATH, output)
    print(f"composition: {COMPOSITION_PATH}")
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("freeze")
    sub.add_parser("composition")
    run = sub.add_parser("run")
    run.add_argument("--variant", choices=VARIANTS, required=True)
    run.add_argument("--count", type=int, choices=[GATE_COUNT, CALL_COUNT], required=True)
    run.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        freeze()
        return
    if not PLAN_PATH.exists():
        raise RuntimeError(f"missing frozen plan: {PLAN_PATH}")
    plan = read_json(PLAN_PATH)
    if args.command == "composition":
        composition(plan)
    else:
        asyncio.run(run_variant(plan, args.variant, args.count, args.run_id))


if __name__ == "__main__":
    main()
