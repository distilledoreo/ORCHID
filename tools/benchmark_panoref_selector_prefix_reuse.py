"""Benchmark selector TTFT under exact-repeat and prefix-identity conditions.

This is a disposable benchmark harness. It reuses the frozen PanoRef 1.2K
selector requests and the existing OpenAI-compatible streaming measurement
transport. It does not change production ORCHID behavior, selector policy,
chunking, schema validation, model, or concurrency.

Arm A repeats one identical frozen request. Arm B sends a deterministic subset
of normal frozen chunks. Arm C sends those same chunks with only a harmless,
per-call benchmark nonce appended to the fixed system-prefix comment. The
nonce is not used to repair, relax, or interpret selector output.
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
from memory_gateway.pipeline_adapters import (
    _event_payload,
    selector_response_format_for_chunk,
)
from tools.phase3_2_pipeline_ablation import load_events, source_items
from tools.profile_panoref_selector_calls import (
    CHUNK_TARGET,
    CONFIG_PATH as PROFILE_CONFIG_PATH,
    HISTORICAL_SUMMARY,
    SAMPLE_PATH as PROFILE_SAMPLE_PATH,
    _chunk_events,
    endpoint_url,
    extract_metric,
    header_metric,
    percentile,
)


OUT_ROOT = ROOT / "artifacts/agent_benchmarks/panoref_selector_prefix_reuse_v1"
CONFIG_PATH = OUT_ROOT / "PREFIX_REUSE_CONFIG.json"
PLAN_PATH = OUT_ROOT / "FROZEN_PLAN.json"
RUN_DIR = OUT_ROOT / "run_01"

EXACT_REPEAT_COUNT = 12
SEQUENTIAL_COUNT = 24
PROFILE_TIMEOUT_SECONDS = 180.0
PREFIX_NONCE_TEMPLATE = "<!-- benchmark-only prefix nonce {nonce:04d}; ignore this comment. -->"
WRAPPER_PREFIX = "INPUT_JSON\n--- BEGIN INPUT ---\n"
WRAPPER_SUFFIX = "\n--- END INPUT ---"

# Sensitivity-only inputs from the completed PanoRef selector control and the
# preceding TTFT microprofile. These are not performance claims.
BASELINE_SELECTOR_WALL_SECONDS = 1_805.1
BASELINE_END_TO_END_WALL_SECONDS = 2_034.4
BASELINE_RETIRED_SOURCE_TOKENS = 138_149
TTFT_WALL_SHARE = 0.7744256387337867


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def evenly_spaced_positions(total: int, count: int) -> list[int]:
    if total <= 0 or count <= 0 or count > total:
        raise ValueError(f"invalid positions total={total} count={count}")
    if count == 1:
        return [0]
    positions = [round(i * (total - 1) / (count - 1)) for i in range(count)]
    if len(set(positions)) != count:
        raise ValueError("positions were not unique")
    return positions


def load_profile_inputs() -> tuple[dict[str, Any], dict[str, Any], list[Any]]:
    config = json.loads(PROFILE_CONFIG_PATH.read_text(encoding="utf-8"))
    sample = json.loads(PROFILE_SAMPLE_PATH.read_text(encoding="utf-8"))
    events = load_events(Path(config["replay"]))
    items = source_items(events)
    by_id = {item.id: item for item in items}
    if digest(sample) != config["sample_sha256"]:
        raise RuntimeError("prior frozen selector sample hash mismatch")
    return config, sample, [by_id[item.id] for item in items]


def build_chunk_lookup(config: dict[str, Any], items: list[Any]) -> dict[int, dict[str, Any]]:
    chunks = _chunk_events(list(items), target_tokens=CHUNK_TARGET)
    sample = json.loads(PROFILE_SAMPLE_PATH.read_text(encoding="utf-8"))
    frozen_by_index = {
        int(row["chunk_index"]): row for row in sample["chunks"]
    }
    lookup: dict[int, dict[str, Any]] = {}
    for index, chunk in enumerate(chunks):
        event_ids = [event.id for event in chunk]
        payload = {
            "events": [_event_payload(event) for event in chunk],
            "event_ids_in_order": event_ids,
            "source_ids_in_order": event_ids,
        }
        estimated_tokens = sum(
            estimate_tokens(json.dumps(_event_payload(event), ensure_ascii=False))
            for event in chunk
        )
        frozen = frozen_by_index.get(index)
        if frozen is not None:
            if event_ids != frozen["event_ids"]:
                raise RuntimeError(f"frozen chunk IDs differ at index {index}")
            if digest(payload) != frozen["request_payload_hash"]:
                raise RuntimeError(f"frozen request payload differs at index {index}")
        lookup[index] = {
            "chunk_index": index,
            "items": chunk,
            "event_ids": event_ids,
            "payload": payload,
            "estimated_source_tokens": estimated_tokens,
        }
    if len(lookup) != 148:
        raise RuntimeError(f"expected 148 frozen chunks, got {len(lookup)}")
    return lookup


def freeze() -> None:
    if CONFIG_PATH.exists() or PLAN_PATH.exists():
        raise RuntimeError(
            f"prefix-reuse freeze already exists at {OUT_ROOT}; refusing overwrite"
        )
    profile_config, sample, items = load_profile_inputs()
    lookup = build_chunk_lookup(profile_config, items)
    sample_chunks = sample["chunks"]
    sample_positions = evenly_spaced_positions(len(sample_chunks), SEQUENTIAL_COUNT)
    sequential_chunks = [sample_chunks[position] for position in sample_positions]
    repeat_chunk = sample_chunks[len(sample_chunks) // 2]
    selected_indices = [int(chunk["chunk_index"]) for chunk in sequential_chunks]
    if len(set(selected_indices)) != SEQUENTIAL_COUNT:
        raise RuntimeError("sequential chunk selection was not unique")
    if repeat_chunk["chunk_index"] in selected_indices:
        # This is allowed, but recording it makes arm overlap explicit.
        repeat_overlap = True
    else:
        repeat_overlap = False

    plan_without_hash = {
        "schema_version": "panoref-selector-prefix-reuse-plan-v1",
        "created_at": utc_now(),
        "source_commit": git_commit(),
        "prior_profile_config": str(PROFILE_CONFIG_PATH.resolve()),
        "prior_profile_config_sha256": file_sha256(PROFILE_CONFIG_PATH),
        "prior_profile_sample": str(PROFILE_SAMPLE_PATH.resolve()),
        "prior_profile_sample_sha256": file_sha256(PROFILE_SAMPLE_PATH),
        "historical_selector_summary": str(HISTORICAL_SUMMARY.resolve()),
        "historical_selector_summary_sha256": file_sha256(HISTORICAL_SUMMARY),
        "replay": profile_config["replay"],
        "replay_sha256": profile_config["replay_sha256"],
        "endpoint": profile_config["endpoint"],
        "model": profile_config["model"],
        "prompt_version": profile_config["prompt_version"],
        "system_prompt": profile_config["system_prompt"],
        "chunk_target_tokens": CHUNK_TARGET,
        "profile_generation": profile_config["profile_generation"],
        "timeout_seconds": PROFILE_TIMEOUT_SECONDS,
        "transport_note": (
            "OpenAI-compatible SSE with usage included, matching the preceding "
            "TTFT microprofile; no semantic benchmark arm is run here"
        ),
        "arm_a_exact_repeat": {
            "calls": EXACT_REPEAT_COUNT,
            "chunk_index": repeat_chunk["chunk_index"],
            "event_ids": repeat_chunk["event_ids"],
            "request_payload_hash": repeat_chunk["request_payload_hash"],
            "response_schema_hash": repeat_chunk["response_schema_hash"],
            "designation": "first benchmark call, then warmed-repeat candidates",
        },
        "arm_b_stable_prefix": {
            "calls": SEQUENTIAL_COUNT,
            "sample_positions": sample_positions,
            "chunk_indices": selected_indices,
            "prefix_mode": "natural_frozen_selector_prefix",
            "designation": "normal sequential frozen selector chunks",
        },
        "arm_c_invalidated_prefix": {
            "calls": SEQUENTIAL_COUNT,
            "sample_positions": sample_positions,
            "chunk_indices": selected_indices,
            "prefix_mode": "per_call_benchmark_nonce",
            "nonce_template": PREFIX_NONCE_TEMPLATE,
            "designation": "same evidence/schema with nonce-perturbed fixed prefix",
        },
        "arm_overlap": {
            "repeat_chunk_overlaps_sequential_sample": repeat_overlap,
            "sequential_and_invalidated_chunks_identical": True,
        },
        "sensitivity_baseline": {
            "selector_wall_seconds": BASELINE_SELECTOR_WALL_SECONDS,
            "end_to_end_wall_seconds": BASELINE_END_TO_END_WALL_SECONDS,
            "retired_source_tokens": BASELINE_RETIRED_SOURCE_TOKENS,
            "ttft_wall_share": TTFT_WALL_SHARE,
            "method": (
                "model a percentage TTFT reduction as reducing only the TTFT "
                "share of selector wall; hold non-selector wall and retired "
                "tokens constant"
            ),
        },
    }
    plan = {
        **plan_without_hash,
        "plan_sha256": digest(plan_without_hash),
    }
    write_json(PLAN_PATH, plan)
    write_json(
        CONFIG_PATH,
        {
            "schema_version": "panoref-selector-prefix-reuse-config-v1",
            "plan_path": str(PLAN_PATH.resolve()),
            "plan_sha256": plan["plan_sha256"],
            "source_commit": plan["source_commit"],
            "arm_order": ["ARM_A_EXACT_REPEAT", "ARM_B_STABLE_PREFIX", "ARM_C_INVALIDATED_PREFIX"],
            "no_retries": True,
            "no_concurrency": True,
            "no_prompt_restructuring": True,
        },
    )
    print(f"frozen plan: {PLAN_PATH}")
    print(f"config: {CONFIG_PATH}")
    print(f"plan hash: {plan['plan_sha256']}")
    print(f"repeat chunk: {repeat_chunk['chunk_index']}")
    print(f"sequential chunks: {selected_indices}")


def build_request(
    plan: dict[str, Any],
    chunk: dict[str, Any],
    *,
    prefix_nonce: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    system_prompt = plan["system_prompt"]
    prefix_mode = "natural"
    if prefix_nonce is not None:
        system_prompt = system_prompt + "\n" + PREFIX_NONCE_TEMPLATE.format(nonce=prefix_nonce)
        prefix_mode = "nonce"
    event_ids = chunk["event_ids"]
    schema = selector_response_format_for_chunk(event_ids)
    body = {
        "model": plan["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    WRAPPER_PREFIX
                    + json.dumps(chunk["payload"], ensure_ascii=False, sort_keys=True)
                    + WRAPPER_SUFFIX
                ),
            },
        ],
        **plan["profile_generation"],
        "response_format": schema,
    }
    identity = {
        "prefix_mode": prefix_mode,
        "prefix_nonce": prefix_nonce,
        "prefix_hash": digest(
            {
                "system_prompt": system_prompt,
                "user_wrapper_prefix": WRAPPER_PREFIX,
                "user_wrapper_suffix": WRAPPER_SUFFIX,
            }
        ),
        "evidence_hash": digest(chunk["payload"]["events"]),
        "response_schema_hash": digest(schema),
        "request_hash": digest(body),
    }
    return body, identity


def find_named_fields(value: Any, path: str = "") -> list[dict[str, Any]]:
    """Record explicit cache/prefix fields without inferring from timing."""
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            normalized = key.lower().replace("-", "_")
            if any(term in normalized for term in ("cache", "prefix", "kv_")):
                if isinstance(child, (str, int, float, bool)) or child is None:
                    result.append({"path": child_path, "value": child})
                else:
                    result.append({"path": child_path, "value_type": type(child).__name__})
            result.extend(find_named_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(find_named_fields(child, f"{path}[{index}]"))
    return result


def parse_selector_response(content: str, allowed_ids: list[str]) -> tuple[dict[str, Any], str | None]:
    try:
        response = json.loads(content)
    except json.JSONDecodeError as error:
        return {}, f"assistant content was not JSON: {error}"
    if not isinstance(response, dict) or set(response) != {"selected_event_ids"}:
        return {}, "selector response did not have exactly selected_event_ids"
    selected = response.get("selected_event_ids")
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        return {}, "selected_event_ids was not a string array"
    if not set(selected).issubset(set(allowed_ids)):
        return {}, "selector returned an ID outside the current chunk"
    return response, None


async def call_selector(
    client: httpx.AsyncClient,
    *,
    plan: dict[str, Any],
    chunk: dict[str, Any],
    arm: str,
    arm_call_index: int,
    global_call_index: int,
    prefix_nonce: int | None,
    designation: str,
) -> dict[str, Any]:
    body, identity = build_request(plan, chunk, prefix_nonce=prefix_nonce)
    started = time.perf_counter()
    first_content_at: float | None = None
    content = ""
    frames = 0
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    response_headers: dict[str, str] = {}
    response_status: int | None = None
    response_payload_keys: set[str] = set()
    explicit_cache_fields: list[dict[str, Any]] = []
    http_cache_headers: list[dict[str, Any]] = []
    backend_prompt_eval_ms: float | None = None
    backend_decode_ms: float | None = None
    backend_queue_wait_ms: float | None = None
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
                    payload_text = line[5:].strip()
                    if payload_text == "[DONE]":
                        continue
                    payload = json.loads(payload_text)
                    frames += 1
                    response_payload_keys.update(payload.keys())
                    explicit_cache_fields.extend(find_named_fields(payload))
                    usage = payload.get("usage") or usage
                    backend_prompt_eval_ms = (
                        backend_prompt_eval_ms
                        or extract_metric(payload, "prompt_eval_ms")
                        or extract_metric(payload, "prompt_eval_time_ms")
                        or extract_metric(payload, "prompt_eval_duration_ms")
                        or extract_metric(payload, "prompt_eval_duration")
                        or extract_metric(payload, "prefill_ms")
                    )
                    backend_decode_ms = (
                        backend_decode_ms
                        or extract_metric(payload, "decode_ms")
                        or extract_metric(payload, "decode_time_ms")
                        or extract_metric(payload, "eval_duration")
                        or extract_metric(payload, "generation_time_ms")
                        or extract_metric(payload, "generation_time")
                    )
                    backend_queue_wait_ms = (
                        backend_queue_wait_ms
                        or extract_metric(payload, "queue_wait_ms")
                        or extract_metric(payload, "queue_time_ms")
                        or extract_metric(payload, "queue_wait")
                    )
                    choice = (payload.get("choices") or [{}])[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    token = delta.get("content") or choice.get("text") or ""
                    if token and first_content_at is None:
                        first_content_at = time.perf_counter()
                    content += token
                backend_prompt_eval_ms = backend_prompt_eval_ms or header_metric(
                    response.headers,
                    {"x_prompt_eval_ms", "x_prompt_eval_duration_ms", "x_prefill_ms"},
                )
                backend_decode_ms = backend_decode_ms or header_metric(
                    response.headers, {"x_decode_ms", "x_eval_duration_ms"}
                )
                backend_queue_wait_ms = backend_queue_wait_ms or header_metric(
                    response.headers, {"x_queue_wait_ms"}
                )
    except httpx.TimeoutException as exc:
        error_category = "timeout"
        error = str(exc) or "request timed out"
    except Exception as exc:
        error_category = "protocol_or_transport"
        error = f"{type(exc).__name__}: {exc}"

    ended = time.perf_counter()
    total_wall_ms = (ended - started) * 1000
    ttft_ms = (first_content_at - started) * 1000 if first_content_at else None
    decode_ms = (ended - first_content_at) * 1000 if first_content_at else None
    selected_response: dict[str, Any] = {}
    validation_error: str | None = None
    if error is None:
        selected_response, validation_error = parse_selector_response(content, chunk["event_ids"])
        if validation_error:
            error_category = "selector_protocol"
            error = validation_error
    input_tokens = (usage or {}).get("prompt_tokens")
    output_tokens = (usage or {}).get("completion_tokens")
    input_tok_s_proxy = (
        float(input_tokens) / (ttft_ms / 1000)
        if input_tokens and ttft_ms and ttft_ms > 0
        else None
    )
    output_tok_s = (
        float(output_tokens) / (decode_ms / 1000)
        if output_tokens and decode_ms and decode_ms > 0
        else None
    )
    header_cache_fields = [
        {"name": key, "value": value}
        for key, value in response_headers.items()
        if any(term in key.lower() for term in ("cache", "prefix", "kv_"))
        and key.lower() != "cache-control"
    ]
    explicit_cache_fields.extend(header_cache_fields)
    http_cache_headers = [
        {"name": key, "value": value}
        for key, value in response_headers.items()
        if key.lower() == "cache-control"
    ]
    selected_ids = selected_response.get("selected_event_ids") if selected_response else None
    return {
        "timestamp": utc_now(),
        "global_call_index": global_call_index,
        "arm": arm,
        "arm_call_index": arm_call_index,
        "designation": designation,
        "chunk_index": chunk["chunk_index"],
        "chunk_event_ids": chunk["event_ids"],
        "chunk_event_count": len(chunk["items"]),
        "estimated_source_tokens": chunk["estimated_source_tokens"],
        **identity,
        "status": "SUCCEEDED" if error is None else "FAILED",
        "error": error,
        "error_category": error_category,
        "http_status": response_status,
        "finish_reason": finish_reason,
        "frame_count": frames,
        "response_payload_keys": sorted(response_payload_keys),
        "response_headers": {
            key: value
            for key, value in response_headers.items()
            if key.lower().startswith("x-")
            or "time" in key.lower()
            or "queue" in key.lower()
            or "cache" in key.lower()
        },
        "explicit_cache_fields": explicit_cache_fields,
        "http_cache_headers": http_cache_headers,
        "assistant_content_chars": len(content),
        "assistant_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()
        if content
        else None,
        "selected_event_ids": selected_ids,
        "decision_hash": digest(selected_ids) if selected_ids is not None else None,
        "usage": usage,
        "metrics": {
            "total_wall_ms": total_wall_ms,
            "ttft_ms": ttft_ms,
            "decode_ms_client_observed": decode_ms,
            "prompt_eval_ms_backend": backend_prompt_eval_ms,
            "decode_ms_backend": backend_decode_ms,
            "queue_wait_ms_backend": backend_queue_wait_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_tok_s_ttft_proxy": input_tok_s_proxy,
            "output_tok_s_client_observed": output_tok_s,
        },
    }


def metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = [
        "total_wall_ms",
        "ttft_ms",
        "decode_ms_client_observed",
        "prompt_eval_ms_backend",
        "decode_ms_backend",
        "queue_wait_ms_backend",
        "input_tok_s_ttft_proxy",
        "output_tok_s_client_observed",
    ]
    result: dict[str, Any] = {
        "calls": len(rows),
        "succeeded": sum(row["status"] == "SUCCEEDED" for row in rows),
        "failed": sum(row["status"] == "FAILED" for row in rows),
        "timeouts": sum(row.get("error_category") == "timeout" for row in rows),
        "explicit_backend_cache_field_rows": sum(
            bool(row["explicit_cache_fields"]) for row in rows
        ),
        "http_cache_control_rows": sum(bool(row["http_cache_headers"]) for row in rows),
    }
    for name in metric_names:
        values = [
            float(row["metrics"][name])
            for row in rows
            if row["metrics"].get(name) is not None
        ]
        result[name] = {
            "count": len(values),
            "p50": percentile(values, 50),
            "p95": percentile(values, 95),
            "mean": sum(values) / len(values) if values else None,
        }
    return result


def sensitivity_table(plan: dict[str, Any]) -> list[dict[str, float]]:
    selector_wall = float(plan["sensitivity_baseline"]["selector_wall_seconds"])
    total_wall = float(plan["sensitivity_baseline"]["end_to_end_wall_seconds"])
    retired = float(plan["sensitivity_baseline"]["retired_source_tokens"])
    share = float(plan["sensitivity_baseline"]["ttft_wall_share"])
    ttft_component = selector_wall * share
    result = []
    for reduction in (0.25, 0.50, 0.75):
        saved = ttft_component * reduction
        selector = selector_wall - saved
        end_to_end = total_wall - saved
        result.append(
            {
                "ttft_reduction": reduction,
                "selector_wall_seconds": selector,
                "end_to_end_wall_seconds": end_to_end,
                "retirement_tok_s": retired / end_to_end,
            }
        )
    return result


def compare_decisions(rows: list[dict[str, Any]], historical_ids: set[str]) -> dict[str, Any]:
    successful = [row for row in rows if row["status"] == "SUCCEEDED"]
    arm_a = [row for row in successful if row["arm"] == "ARM_A_EXACT_REPEAT"]
    arm_b = [row for row in successful if row["arm"] == "ARM_B_STABLE_PREFIX"]
    arm_c = [row for row in successful if row["arm"] == "ARM_C_INVALIDATED_PREFIX"]
    a_hash = arm_a[0]["decision_hash"] if arm_a else None
    a_consistent = sum(row["decision_hash"] == a_hash for row in arm_a) if a_hash else 0
    b_by_chunk = {row["chunk_index"]: row for row in arm_b}
    c_pairs = [
        row
        for row in arm_c
        if row["chunk_index"] in b_by_chunk
    ]
    c_matches_b = sum(
        row["decision_hash"] == b_by_chunk[row["chunk_index"]]["decision_hash"]
        for row in c_pairs
    )
    all_historical = [
        row
        for row in successful
        if set(row["selected_event_ids"] or []).issubset(historical_ids)
    ]
    return {
        "arm_a_exact_repeat_consistent": f"{a_consistent}/{len(arm_a)}",
        "arm_b_historical_decision_subset": f"{sum(row in all_historical for row in arm_b)}/{len(arm_b)}",
        "arm_c_historical_decision_subset": f"{sum(row in all_historical for row in arm_c)}/{len(arm_c)}",
        "arm_c_matches_arm_b_by_chunk": f"{c_matches_b}/{len(c_pairs)}",
        "arm_c_mismatch_details": [
            {
                "chunk_index": row["chunk_index"],
                "stable_selected_event_ids": b_by_chunk[row["chunk_index"]]["selected_event_ids"],
                "invalidated_selected_event_ids": row["selected_event_ids"],
            }
            for row in c_pairs
            if row["decision_hash"] != b_by_chunk[row["chunk_index"]]["decision_hash"]
        ],
        "arm_a_distinct_decision_hashes": len({row["decision_hash"] for row in arm_a}),
        "arm_b_distinct_decision_hashes": len({row["decision_hash"] for row in arm_b}),
        "arm_c_distinct_decision_hashes": len({row["decision_hash"] for row in arm_c}),
    }


def report(
    plan: dict[str, Any],
    rows: list[dict[str, Any]],
    historical_ids: set[str],
) -> tuple[dict[str, Any], str]:
    by_arm = {
        arm: metric_summary([row for row in rows if row["arm"] == arm])
        for arm in (
            "ARM_A_EXACT_REPEAT",
            "ARM_B_STABLE_PREFIX",
            "ARM_C_INVALIDATED_PREFIX",
        )
    }
    decisions = compare_decisions(rows, historical_ids)
    a_rows = [row for row in rows if row["arm"] == "ARM_A_EXACT_REPEAT"]
    a_first = metric_summary(a_rows[:1])
    a_warmed = metric_summary(a_rows[1:])
    b_ttft = by_arm["ARM_B_STABLE_PREFIX"]["ttft_ms"]["p50"]
    c_ttft = by_arm["ARM_C_INVALIDATED_PREFIX"]["ttft_ms"]["p50"]
    delta = c_ttft - b_ttft if b_ttft is not None and c_ttft is not None else None
    sensitivity = sensitivity_table(plan)
    summary = {
        "plan_sha256": plan["plan_sha256"],
        "source_commit": plan["source_commit"],
        "completed_at": utc_now(),
        "arm_metrics": by_arm,
        "exact_repeat_first_call": a_first,
        "exact_repeat_warmed_calls": a_warmed,
        "decision_consistency": decisions,
        "stable_vs_invalidated_ttft_delta_ms_p50": delta,
        "explicit_backend_cache_evidence_rows": sum(
            bool(row["explicit_cache_fields"]) for row in rows
        ),
        "http_cache_control_rows": sum(bool(row["http_cache_headers"]) for row in rows),
        "sensitivity": sensitivity,
    }
    def f(value: Any) -> str:
        return "n/a" if value is None else f"{float(value):.1f}"

    lines = [
        "# PanoRef selector TTFT / prefix-reuse microbenchmark",
        "",
        "All calls were serial, benchmark-only, streamed for TTFT observation, and issued without retries. The exact request body, selector policy, model, 1.2K chunk plan, dynamic exact-ID schema, and evidence were frozen before execution.",
        "",
        "## Result",
        "",
        "| Arm | Calls | TTFT p50 | TTFT p95 | Wall p50 | Decode p50 | Decision consistency |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    labels = {
        "ARM_A_EXACT_REPEAT": "A exact repeat",
        "ARM_B_STABLE_PREFIX": "B stable prefix",
        "ARM_C_INVALIDATED_PREFIX": "C invalidated prefix",
    }
    for arm, label in labels.items():
        metrics = by_arm[arm]
        consistency = {
            "ARM_A_EXACT_REPEAT": decisions["arm_a_exact_repeat_consistent"],
            "ARM_B_STABLE_PREFIX": decisions["arm_b_historical_decision_subset"],
            "ARM_C_INVALIDATED_PREFIX": decisions["arm_c_matches_arm_b_by_chunk"],
        }[arm]
        lines.append(
            f"| {label} | {metrics['calls']} | {f(metrics['ttft_ms']['p50'])} ms | "
            f"{f(metrics['ttft_ms']['p95'])} ms | {f(metrics['total_wall_ms']['p50'])} ms | "
            f"{f(metrics['decode_ms_client_observed']['p50'])} ms | {consistency} |"
        )
    lines.extend(
        [
            "",
            "## Exact-repeat warm behavior",
            "",
            f"The first exact-repeat call had TTFT p50 {f(a_first['ttft_ms']['p50'])} ms. The remaining {len(a_rows) - 1} repeated calls had TTFT p50 {f(a_warmed['ttft_ms']['p50'])} ms. The measured delta is {f((a_first['ttft_ms']['p50'] or 0) - (a_warmed['ttft_ms']['p50'] or 0))} ms, first minus warmed. This was not a cold-server reset: chunk 76 also appeared in the preceding 32-call microprofile, so the first call here was already conditioned by prior benchmark activity.",
            "",
            "## Stable versus invalidated prefix",
            "",
            f"Stable-prefix p50 TTFT was {f(b_ttft)} ms; invalidated-prefix p50 TTFT was {f(c_ttft)} ms; invalidated minus stable was {f(delta)} ms. The arms used identical frozen evidence and dynamic exact-ID schemas, with only the benchmark prefix nonce changed in Arm C.",
            "",
            "## Explicit backend cache evidence",
            "",
            f"No explicit backend cache/prefix fields were present in {summary['explicit_backend_cache_evidence_rows']} of {len(rows)} responses. All {summary['http_cache_control_rows']} responses carried the ordinary HTTP `Cache-Control: no-cache` header; that is transport metadata, not evidence of an LM Studio prompt-cache hit. The OpenAI-compatible SSE responses exposed usage and token arrival, but no cache-hit counter, prompt-cache counter, prompt-eval timing, decode timing, or queue-wait timing.",
            "",
            "Timing alone is not treated as proof of a cache hit. Arm A/ B/ C timing differences are observations conditioned on the recorded serial call order and backend state; backend cache identity remains unreported by this endpoint.",
            "",
            "## Decision validation",
            "",
            f"Exact repeats matched the first decision in {decisions['arm_a_exact_repeat_consistent']}. Every successful stable-prefix response was a subset of the historical selector decision set in {decisions['arm_b_historical_decision_subset']}; invalidated-prefix responses were a subset in {decisions['arm_c_historical_decision_subset']}. Paired stable/invalidated decisions matched in {decisions['arm_c_matches_arm_b_by_chunk']}. The two invalidated-prefix mismatches were retained as semantic observations: {decisions['arm_c_mismatch_details']}.",
            "",
            "## TTFT sensitivity model",
            "",
            "This is a model, not a measured speedup. It applies a 25/50/75% reduction to the observed 0.7744 TTFT share of the 1,805.1 s selector wall, holds the 229.3 s non-selector wall and 138,149 retired source tokens constant, and does not assume cache counters.",
            "",
            "| Assumed TTFT reduction | Modeled selector wall | Modeled end-to-end wall | Modeled retired tok/s |",
            "|---:|---:|---:|---:|",
        ]
    )
    for row in sensitivity:
        lines.append(
            f"| {row['ttft_reduction'] * 100:.0f}% | {row['selector_wall_seconds']:.1f} s | "
            f"{row['end_to_end_wall_seconds']:.1f} s | {row['retirement_tok_s']:.1f} |"
        )
    lines.extend(
        [
            "",
            "## Frozen identity",
            "",
            f"- Source commit: `{plan['source_commit']}`",
            f"- Plan SHA-256: `{plan['plan_sha256']}`",
            f"- Replay SHA-256: `{plan['replay_sha256']}`",
            f"- Prior microprofile config SHA-256: `{plan['prior_profile_config_sha256']}`",
            f"- Model: `{plan['model']}`",
            f"- Chunk target: `{plan['chunk_target_tokens']}`",
            f"- Arm order: `{', '.join(['A exact repeat', 'B stable prefix', 'C invalidated prefix'])}`",
            "",
            "No production architecture, selector prompt policy, chunk size, concurrency, schema semantics, or Solar path was changed.",
        ]
    )
    return summary, "\n".join(lines) + "\n"


async def continue_after_a(plan: dict[str, Any]) -> None:
    """Continue B/C after a harness-only transition failure, without retrying A."""
    if not RUN_DIR.exists():
        raise RuntimeError(f"partial run directory does not exist: {RUN_DIR}")
    telemetry_path = RUN_DIR / "telemetry.jsonl"
    rows = [
        json.loads(line)
        for line in telemetry_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != EXACT_REPEAT_COUNT or any(
        row["arm"] != "ARM_A_EXACT_REPEAT" for row in rows
    ):
        raise RuntimeError("continuation requires exactly the preserved 12 Arm A rows")
    if [row["arm_call_index"] for row in rows] != list(range(EXACT_REPEAT_COUNT)):
        raise RuntimeError("preserved Arm A rows have unexpected call indices")

    profile_config, sample, items = load_profile_inputs()
    if plan["prior_profile_config_sha256"] != file_sha256(PROFILE_CONFIG_PATH):
        raise RuntimeError("prior profile config changed after freeze")
    lookup = build_chunk_lookup(profile_config, items)
    sample_by_index = {int(row["chunk_index"]): row for row in sample["chunks"]}
    selected_chunks = [
        sample_by_index[int(chunk_index)]
        for chunk_index in plan["arm_b_stable_prefix"]["chunk_indices"]
    ]
    global_call_index = len(rows)
    async with httpx.AsyncClient(timeout=PROFILE_TIMEOUT_SECONDS) as client:
        for arm_name, prefix_mode in (
            ("ARM_B_STABLE_PREFIX", "natural"),
            ("ARM_C_INVALIDATED_PREFIX", "nonce"),
        ):
            for arm_call_index, frozen_chunk in enumerate(selected_chunks):
                chunk = {**lookup[int(frozen_chunk["chunk_index"])]}
                nonce = arm_call_index + 1 if prefix_mode == "nonce" else None
                row = await call_selector(
                    client,
                    plan=plan,
                    chunk=chunk,
                    arm=arm_name,
                    arm_call_index=arm_call_index,
                    global_call_index=global_call_index,
                    prefix_nonce=nonce,
                    designation=(
                        "normal_sequential_frozen_chunk"
                        if prefix_mode == "natural"
                        else "per_call_prefix_nonce"
                    ),
                )
                rows.append(row)
                global_call_index += 1
                with telemetry_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                print(
                    f"{arm_name[-1]}/{arm_call_index + 1:02d} "
                    f"{row['status']} {row['metrics']['total_wall_ms']:.1f}ms"
                )

    historical = json.loads(HISTORICAL_SUMMARY.read_text(encoding="utf-8"))
    summary, report_text = report(plan, rows, set(historical["selector"]["selected_ids"]))
    summary["recovered_after_harness_failure"] = True
    summary["recovery_execution_commit"] = git_commit()
    write_json(RUN_DIR / "SUMMARY.json", summary)
    (RUN_DIR / "REPORT.md").write_text(report_text, encoding="utf-8")
    previous_meta = json.loads(
        (RUN_DIR / "RUN_METADATA.json").read_text(encoding="utf-8")
    )
    previous_meta.update(
        {
            "status": "SUCCEEDED"
            if all(row["status"] == "SUCCEEDED" for row in rows)
            else "PARTIAL",
            "completed_at": utc_now(),
            "recovered_after_harness_failure": True,
            "recovery_execution_commit": git_commit(),
            "calls": len(rows),
            "succeeded": sum(row["status"] == "SUCCEEDED" for row in rows),
            "failed": sum(row["status"] == "FAILED" for row in rows),
        }
    )
    write_json(RUN_DIR / "RUN_METADATA.json", previous_meta)
    print(f"summary: {RUN_DIR / 'SUMMARY.json'}")
    print(f"report: {RUN_DIR / 'REPORT.md'}")


async def run(plan: dict[str, Any]) -> None:
    if RUN_DIR.exists():
        raise RuntimeError(f"run directory already exists: {RUN_DIR}")
    profile_config, sample, items = load_profile_inputs()
    if plan["prior_profile_config_sha256"] != file_sha256(PROFILE_CONFIG_PATH):
        raise RuntimeError("prior profile config changed after freeze")
    lookup = build_chunk_lookup(profile_config, items)
    historical = json.loads(HISTORICAL_SUMMARY.read_text(encoding="utf-8"))
    historical_ids = set(historical["selector"]["selected_ids"])
    sample_by_index = {int(row["chunk_index"]): row for row in sample["chunks"]}
    RUN_DIR.mkdir(parents=True)
    write_json(
        RUN_DIR / "RUN_METADATA.json",
        {
            "status": "RUNNING",
            "started_at": utc_now(),
            "source_commit": plan["source_commit"],
            "plan_sha256": plan["plan_sha256"],
            "no_retries": True,
            "no_concurrency": True,
            "arm_order": ["ARM_A_EXACT_REPEAT", "ARM_B_STABLE_PREFIX", "ARM_C_INVALIDATED_PREFIX"],
        },
    )
    telemetry_path = RUN_DIR / "telemetry.jsonl"
    rows: list[dict[str, Any]] = []
    global_call_index = 0
    async with httpx.AsyncClient(timeout=PROFILE_TIMEOUT_SECONDS) as client:
        repeat_index = int(plan["arm_a_exact_repeat"]["chunk_index"])
        repeat_chunk = {**lookup[repeat_index], "items": lookup[repeat_index]["items"]}
        for arm_call_index in range(EXACT_REPEAT_COUNT):
            row = await call_selector(
                client,
                plan=plan,
                chunk=repeat_chunk,
                arm="ARM_A_EXACT_REPEAT",
                arm_call_index=arm_call_index,
                global_call_index=global_call_index,
                prefix_nonce=None,
                designation="first_repeat_call" if arm_call_index == 0 else "warmed_repeat_candidate",
            )
            rows.append(row)
            global_call_index += 1
            with telemetry_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            print(f"A/{arm_call_index + 1:02d} {row['status']} {row['metrics']['total_wall_ms']:.1f}ms")

        selected_chunks = [
            sample_by_index[int(chunk["chunk_index"])] for chunk in json.loads(PLAN_PATH.read_text(encoding="utf-8"))["arm_b_stable_prefix"]["chunk_indices"]
        ]
        for arm_name, prefix_mode in (
            ("ARM_B_STABLE_PREFIX", "natural"),
            ("ARM_C_INVALIDATED_PREFIX", "nonce"),
        ):
            for arm_call_index, frozen_chunk in enumerate(selected_chunks):
                chunk = {**lookup[int(frozen_chunk["chunk_index"])]}
                nonce = arm_call_index + 1 if prefix_mode == "nonce" else None
                row = await call_selector(
                    client,
                    plan=plan,
                    chunk=chunk,
                    arm=arm_name,
                    arm_call_index=arm_call_index,
                    global_call_index=global_call_index,
                    prefix_nonce=nonce,
                    designation=(
                        "normal_sequential_frozen_chunk"
                        if prefix_mode == "natural"
                        else "per_call_prefix_nonce"
                    ),
                )
                rows.append(row)
                global_call_index += 1
                with telemetry_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                print(f"{arm_name[-1]}/{arm_call_index + 1:02d} {row['status']} {row['metrics']['total_wall_ms']:.1f}ms")

    summary, report_text = report(plan, rows, historical_ids)
    write_json(RUN_DIR / "SUMMARY.json", summary)
    (RUN_DIR / "REPORT.md").write_text(report_text, encoding="utf-8")
    write_json(
        RUN_DIR / "RUN_METADATA.json",
        {
            "status": "SUCCEEDED" if all(row["status"] == "SUCCEEDED" for row in rows) else "PARTIAL",
            "started_at": json.loads((RUN_DIR / "RUN_METADATA.json").read_text(encoding="utf-8"))["started_at"],
            "completed_at": utc_now(),
            "source_commit": plan["source_commit"],
            "plan_sha256": plan["plan_sha256"],
            "no_retries": True,
            "no_concurrency": True,
            "calls": len(rows),
            "succeeded": sum(row["status"] == "SUCCEEDED" for row in rows),
            "failed": sum(row["status"] == "FAILED" for row in rows),
        },
    )
    print(f"summary: {RUN_DIR / 'SUMMARY.json'}")
    print(f"report: {RUN_DIR / 'REPORT.md'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-only", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--continue-after-a", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    selected_modes = sum(bool(value) for value in (args.freeze_only, args.run, args.continue_after_a))
    if selected_modes != 1:
        raise SystemExit("choose exactly one of --freeze-only, --run, or --continue-after-a")
    if args.freeze_only:
        freeze()
        return
    if not CONFIG_PATH.exists() or not PLAN_PATH.exists():
        raise SystemExit("run --freeze-only first")
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    if args.continue_after_a:
        asyncio.run(continue_after_a(plan))
    else:
        asyncio.run(run(plan))


if __name__ == "__main__":
    main()
