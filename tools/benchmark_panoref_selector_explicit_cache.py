"""Full-trace validation of LM Studio llama.cpp prefix-cache capacity.

This is a disposable, selector-only benchmark harness. It reuses the frozen
PanoRef 1.2K selector chunks and request construction from the preceding
microprofile. The two measured conditions differ only in the backend model
instance selected by ``model`` and the instance's llama.cpp context-checkpoint
capacity (32 versus 148). No production ORCHID path is changed.

The backend instance is loaded/unloaded by a separate benchmark-only LM
Studio SDK control command. This Python harness deliberately does not load,
unload, retry, or repair requests.
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

from memory_gateway.pipeline_adapters import selector_response_format_for_chunk
from tools.benchmark_panoref_selector_prefix_reuse import (
    PROFILE_CONFIG_PATH,
    build_chunk_lookup,
    digest,
    endpoint_url,
    extract_metric,
    file_sha256,
    header_metric,
    load_profile_inputs,
    percentile,
)


OUT_ROOT = ROOT / "artifacts/agent_benchmarks/panoref_selector_explicit_cache_v1"
PLAN_PATH = OUT_ROOT / "EXPLICIT_CACHE_PLAN.json"
CONFIG_PATH = OUT_ROOT / "EXPLICIT_CACHE_CONFIG.json"
CAPABILITIES_PATH = OUT_ROOT / "BACKEND_CAPABILITIES.md"
RUN_ROOT = OUT_ROOT / "runs"

MODEL_BASELINE = "qwen3.5-4b@q6_k"
MODEL_EXPLICIT = "orchid-prefix-cache-validation"
ENDPOINT = "http://127.0.0.1:1234/v1"
API_MODELS_ENDPOINT = "http://127.0.0.1:1234/api/v1/models"
CALL_COUNT = 148
TIMEOUT_SECONDS = 180.0
LOW_TTFT_THRESHOLD_MS = 1_000.0
COLD_TTFT_THRESHOLD_MS = 5_000.0
IDLE_SECONDS = 2.0
HISTORICAL_SELECTOR_WALL_SECONDS = 1_805.1
HISTORICAL_END_TO_END_WALL_SECONDS = 2_034.4
HISTORICAL_RETIRED_SOURCE_TOKENS = 138_149
HISTORICAL_DOWNSTREAM_SECONDS = (
    HISTORICAL_END_TO_END_WALL_SECONDS - HISTORICAL_SELECTOR_WALL_SECONDS
)
REAL_FREETOSHOP_ARRIVAL_TOKENS_PER_SECOND = 59.148
WRAPPER_PREFIX = "INPUT_JSON\n--- BEGIN INPUT ---\n"
WRAPPER_SUFFIX = "\n--- END INPUT ---"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


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


def load_backend_models() -> dict[str, Any]:
    response = httpx.get(API_MODELS_ENDPOINT, timeout=10.0)
    response.raise_for_status()
    return response.json()


def loaded_instances(models_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        instance
        for model in models_payload.get("models", [])
        for instance in model.get("loaded_instances", [])
    ]


def instance_snapshot(model_id: str) -> dict[str, Any] | None:
    payload = load_backend_models()
    for instance in loaded_instances(payload):
        if instance.get("id") == model_id:
            return instance
    return None


def plan_without_hash() -> dict[str, Any]:
    profile_config, sample, items = load_profile_inputs()
    lookup = build_chunk_lookup(profile_config, items)
    if len(lookup) != CALL_COUNT:
        raise RuntimeError(f"expected {CALL_COUNT} chunks, got {len(lookup)}")
    chunk_records = []
    for index in range(CALL_COUNT):
        chunk = lookup[index]
        schema = selector_response_format_for_chunk(chunk["event_ids"])
        chunk_records.append(
            {
                "chunk_index": index,
                "event_ids": chunk["event_ids"],
                "event_count": len(chunk["items"]),
                "estimated_source_tokens": chunk["estimated_source_tokens"],
                "request_payload_hash": digest(chunk["payload"]),
                "response_schema_hash": digest(schema),
            }
        )
    frozen_model_snapshot = instance_snapshot(MODEL_BASELINE)
    plan = {
        "schema_version": "panoref-selector-explicit-cache-plan-v1",
        "created_at": utc_now(),
        "source_commit": git_commit(),
        "replay": profile_config["replay"],
        "replay_sha256": profile_config["replay_sha256"],
        "prior_profile_config": str(PROFILE_CONFIG_PATH.resolve()),
        "prior_profile_config_sha256": file_sha256(PROFILE_CONFIG_PATH),
        "prior_profile_sample_sha256": profile_config["sample_sha256"],
        "endpoint": ENDPOINT,
        "api_models_endpoint": API_MODELS_ENDPOINT,
        "prompt_version": profile_config["prompt_version"],
        "system_prompt": profile_config["system_prompt"],
        "chunk_target_tokens": profile_config["chunk_target_tokens"],
        "chunk_count": CALL_COUNT,
        "chunk_plan_sha256": hashlib.sha256(canonical_bytes(chunk_records)).hexdigest(),
        "profile_generation": profile_config["profile_generation"],
        "timeout_seconds": TIMEOUT_SECONDS,
        "no_retries": True,
        "no_concurrency": True,
        "no_prompt_restructuring": True,
        "no_decode_optimization": True,
        "condition_order": ["BASELINE_CURRENT_32", "EXPLICIT_PREFIX_148"],
        "conditions": {
            "BASELINE_CURRENT_32": {
                "model": MODEL_BASELINE,
                "instance_identifier": MODEL_BASELINE,
                "context_checkpoints": 32,
                "designation": "fresh dedicated baseline instance, current default checkpoint capacity",
            },
            "EXPLICIT_PREFIX_148": {
                "model": MODEL_EXPLICIT,
                "instance_identifier": MODEL_EXPLICIT,
                "context_checkpoints": CALL_COUNT,
                "designation": "fresh dedicated serial instance with one checkpoint budget per frozen selector call",
            },
        },
        "backend_load_fields": [
            {"key": "llm.load.contextLength", "value": 32768},
            {"key": "llm.load.numParallelSessions", "value": 1},
            {"key": "llm.load.llama.contextCheckpoints", "value": 32},
            {"key": "llm.load.llama.flashAttention", "value": True},
            {"key": "llm.load.llama.acceleration.offloadRatio", "value": 1},
            {"key": "llm.load.llama.evalBatchSize", "value": 2048},
        ],
        "explicit_backend_change": {
            "only_changed_field": "llm.load.llama.contextCheckpoints",
            "baseline_value": 32,
            "explicit_value": CALL_COUNT,
            "reason": "avoid checkpoint-capacity eviction across the exact 148-call frozen trace; capacity chosen before outcomes",
            "control_transport": "LM Studio SDK internal loadModel channel with raw KVConfig fields; benchmark-only, no ORCHID production change",
        },
        "baseline_backend_snapshot_at_freeze": frozen_model_snapshot,
        "durability": {
            "changing_chunks": "covered by the full 148-call trace",
            "short_idle_seconds": IDLE_SECONDS,
            "solar_between_selector_calls": {
                "run": False,
                "reason": "the existing selector apparatus completes the selector stage before Solar; inserting Solar would change workflow ordering"
            },
            "request_cleanup": "close the HTTP client, issue a subsequent selector request, then record instance state",
        },
        "historical_reference": {
            "selector_wall_seconds": HISTORICAL_SELECTOR_WALL_SECONDS,
            "end_to_end_wall_seconds": HISTORICAL_END_TO_END_WALL_SECONDS,
            "downstream_seconds": HISTORICAL_DOWNSTREAM_SECONDS,
            "retired_source_tokens": HISTORICAL_RETIRED_SOURCE_TOKENS,
            "freetoshop_arrival_tokens_per_second": REAL_FREETOSHOP_ARRIVAL_TOKENS_PER_SECOND,
        },
        "telemetry_thresholds": {
            "low_ttft_ms_lt": LOW_TTFT_THRESHOLD_MS,
            "cold_regression_ms_gte": COLD_TTFT_THRESHOLD_MS,
        },
    }
    return plan


def freeze() -> None:
    if PLAN_PATH.exists() or CONFIG_PATH.exists():
        raise RuntimeError(f"refusing to overwrite frozen plan under {OUT_ROOT}")
    plan = plan_without_hash()
    plan["plan_sha256"] = hashlib.sha256(canonical_bytes(plan)).hexdigest()
    write_json(PLAN_PATH, plan)
    write_json(
        CONFIG_PATH,
        {
            "schema_version": "panoref-selector-explicit-cache-config-v1",
            "plan_path": str(PLAN_PATH.resolve()),
            "plan_sha256": plan["plan_sha256"],
            "source_commit": plan["source_commit"],
            "arm_order": plan["condition_order"],
            "no_retries": True,
            "no_concurrency": True,
        },
    )
    print(f"plan: {PLAN_PATH}")
    print(f"config: {CONFIG_PATH}")
    print(f"plan_sha256: {plan['plan_sha256']}")
    print(f"baseline_snapshot: {json.dumps(plan['baseline_backend_snapshot_at_freeze'], sort_keys=True)}")


def verify_plan(plan: dict[str, Any]) -> None:
    recorded_hash = plan.get("plan_sha256")
    without_hash = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if recorded_hash != hashlib.sha256(canonical_bytes(without_hash)).hexdigest():
        raise RuntimeError("frozen plan hash mismatch")
    current_commit = git_commit()
    if current_commit != plan["source_commit"]:
        code_paths = [
            "tools/benchmark_panoref_selector_explicit_cache.py",
            "artifacts/agent_benchmarks/panoref_selector_explicit_cache_v1/BACKEND_CAPABILITIES.md",
        ]
        unchanged = subprocess.run(
            ["git", "diff", "--quiet", plan["source_commit"], current_commit, "--", *code_paths],
            cwd=ROOT,
        ).returncode == 0
        if not unchanged:
            raise RuntimeError("working benchmark code differs from frozen source commit")
    if file_sha256(Path(plan["replay"])) != plan["replay_sha256"]:
        raise RuntimeError("frozen replay changed")
    if file_sha256(PROFILE_CONFIG_PATH) != plan["prior_profile_config_sha256"]:
        raise RuntimeError("prior selector profile config changed")


def build_request(
    plan: dict[str, Any], chunk: dict[str, Any], model: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    event_ids = chunk["event_ids"]
    schema = selector_response_format_for_chunk(event_ids)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": plan["system_prompt"]},
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
    semantic_body = dict(body)
    semantic_body["model"] = "<backend-condition-model>"
    return body, {
        "request_hash": hashlib.sha256(canonical_bytes(body)).hexdigest(),
        "semantic_request_hash": hashlib.sha256(canonical_bytes(semantic_body)).hexdigest(),
        "prefix_hash": hashlib.sha256(
            canonical_bytes(
                {
                    "system_prompt": plan["system_prompt"],
                    "wrapper_prefix": WRAPPER_PREFIX,
                    "wrapper_suffix": WRAPPER_SUFFIX,
                }
            )
        ).hexdigest(),
        "evidence_hash": hashlib.sha256(
            canonical_bytes(chunk["payload"]["events"])
        ).hexdigest(),
        "schema_hash": hashlib.sha256(canonical_bytes(schema)).hexdigest(),
    }


def parse_selector_response(content: str, allowed_ids: list[str]) -> tuple[dict[str, Any], str | None]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        return {}, f"assistant content was not JSON: {exc}"
    if not isinstance(value, dict) or set(value) != {"selected_event_ids"}:
        return {}, "selector response did not have exactly selected_event_ids"
    selected = value.get("selected_event_ids")
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        return {}, "selected_event_ids was not a string array"
    if not set(selected).issubset(set(allowed_ids)):
        return {}, "selector returned an ID outside the current chunk"
    return value, None


def explicit_fields(value: Any, path: str = "") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            normalized = key.lower().replace("-", "_")
            if any(term in normalized for term in ("cache", "prefix", "slot", "session", "kv_")):
                if isinstance(child, (str, int, float, bool)) or child is None:
                    result.append({"path": child_path, "value": child})
                else:
                    result.append({"path": child_path, "value_type": type(child).__name__})
            result.extend(explicit_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(explicit_fields(child, f"{path}[{index}]"))
    return result


async def call_selector(
    client: httpx.AsyncClient,
    *,
    plan: dict[str, Any],
    chunk: dict[str, Any],
    condition: str,
    call_index: int,
) -> dict[str, Any]:
    condition_data = plan["conditions"][condition]
    body, identity = build_request(plan, chunk, condition_data["model"])
    started = time.perf_counter()
    first_content_at: float | None = None
    content = ""
    frames = 0
    usage: dict[str, Any] | None = None
    response_headers: dict[str, str] = {}
    response_status: int | None = None
    response_payload_keys: set[str] = set()
    response_models: list[str] = []
    fingerprints: list[str] = []
    cache_fields: list[dict[str, Any]] = []
    backend_prompt_eval_ms: float | None = None
    backend_decode_ms: float | None = None
    backend_queue_wait_ms: float | None = None
    error: str | None = None
    error_category: str | None = None
    finish_reason: str | None = None
    try:
        async with client.stream(
            "POST", endpoint_url(plan["endpoint"]), json=body, headers={"content-type": "application/json"}
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
                    text = line[5:].strip()
                    if text == "[DONE]":
                        continue
                    payload = json.loads(text)
                    frames += 1
                    response_payload_keys.update(payload.keys())
                    cache_fields.extend(explicit_fields(payload))
                    usage = payload.get("usage") or usage
                    if isinstance(payload.get("model"), str):
                        response_models.append(payload["model"])
                    if isinstance(payload.get("system_fingerprint"), str):
                        fingerprints.append(payload["system_fingerprint"])
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
                cache_fields.extend(
                    {
                        "path": key,
                        "value": value,
                    }
                    for key, value in response.headers.items()
                    if any(term in key.lower() for term in ("cache", "prefix", "slot", "session", "kv_"))
                    and key.lower() != "cache-control"
                )
                backend_prompt_eval_ms = backend_prompt_eval_ms or header_metric(
                    response.headers, {"x_prompt_eval_ms", "x_prompt_eval_duration_ms", "x_prefill_ms"}
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
    parsed: dict[str, Any] = {}
    validation_error: str | None = None
    if error is None:
        parsed, validation_error = parse_selector_response(content, chunk["event_ids"])
        if validation_error:
            error_category = "selector_protocol"
            error = validation_error
    input_tokens = (usage or {}).get("prompt_tokens")
    output_tokens = (usage or {}).get("completion_tokens")
    selected = parsed.get("selected_event_ids") if parsed else None
    return {
        "timestamp": utc_now(),
        "condition": condition,
        "call_index": call_index,
        "chunk_index": chunk["chunk_index"],
        "chunk_event_ids": chunk["event_ids"],
        "chunk_event_count": len(chunk["items"]),
        "estimated_source_tokens": chunk["estimated_source_tokens"],
        **identity,
        "backend_instance_identifier": condition_data["instance_identifier"],
        "backend_cache_identifier_observed": None,
        "status": "SUCCEEDED" if error is None else "FAILED",
        "error": error,
        "error_category": error_category,
        "http_status": response_status,
        "finish_reason": finish_reason,
        "frame_count": frames,
        "response_payload_keys": sorted(response_payload_keys),
        "response_models": sorted(set(response_models)),
        "system_fingerprints": sorted(set(fingerprints)),
        "response_headers": {
            key: value
            for key, value in response_headers.items()
            if key.lower().startswith("x-")
            or "time" in key.lower()
            or "queue" in key.lower()
            or "cache" in key.lower()
            or "server" in key.lower()
        },
        "explicit_backend_fields": cache_fields,
        "http_cache_control": [
            {"name": key, "value": value}
            for key, value in response_headers.items()
            if key.lower() == "cache-control"
        ],
        "selected_event_ids": selected,
        "decision_hash": digest(selected) if selected is not None else None,
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
            "input_tok_s_ttft_proxy": (
                float(input_tokens) / (ttft_ms / 1000)
                if input_tokens and ttft_ms and ttft_ms > 0
                else None
            ),
            "output_tok_s_client_observed": (
                float(output_tokens) / (decode_ms / 1000)
                if output_tokens and decode_ms and decode_ms > 0
                else None
            ),
        },
    }


async def run_condition(plan: dict[str, Any], condition: str, run_dir: Path) -> None:
    verify_plan(plan)
    if condition not in plan["conditions"]:
        raise ValueError(f"unknown condition {condition}")
    if run_dir.exists():
        raise RuntimeError(f"refusing to overwrite existing run directory {run_dir}")
    run_dir.mkdir(parents=True)
    instance = instance_snapshot(plan["conditions"][condition]["instance_identifier"])
    if instance is None:
        raise RuntimeError(f"required backend instance is not loaded: {condition}")
    write_json(
        run_dir / "RUN_META.json",
        {
            "schema_version": "panoref-selector-explicit-cache-run-v1",
            "started_at": utc_now(),
            "condition": condition,
            "plan_sha256": plan["plan_sha256"],
            "source_commit": plan["source_commit"],
            "backend_instance_at_start": instance,
            "timeout_seconds": TIMEOUT_SECONDS,
            "no_retries": True,
            "no_concurrency": True,
        },
    )
    lookup = build_chunk_lookup(*load_profile_inputs()[::2])
    telemetry_path = run_dir / "telemetry.jsonl"
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        with telemetry_path.open("x", encoding="utf-8") as handle:
            for call_index in range(CALL_COUNT):
                row = await call_selector(
                    client,
                    plan=plan,
                    chunk=lookup[call_index],
                    condition=condition,
                    call_index=call_index,
                )
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                metric = row["metrics"]
                print(
                    f"{condition} {call_index + 1:03d}/{CALL_COUNT} "
                    f"chunk={row['chunk_index']:03d} status={row['status']} "
                    f"ttft_ms={metric['ttft_ms'] if metric['ttft_ms'] is not None else 'n/a'} "
                    f"wall_ms={metric['total_wall_ms']:.1f}",
                    flush=True,
                )
    end_instance = instance_snapshot(plan["conditions"][condition]["instance_identifier"])
    write_json(
        run_dir / "RUN_END.json",
        {
            "ended_at": utc_now(),
            "elapsed_wall_seconds": time.perf_counter() - started,
            "backend_instance_at_end": end_instance,
            "full_trace_rows": sum(1 for _ in telemetry_path.open(encoding="utf-8")),
        },
    )


def rows_for(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "telemetry.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    names = ["total_wall_ms", "ttft_ms", "decode_ms_client_observed", "prompt_eval_ms_backend", "decode_ms_backend", "queue_wait_ms_backend"]
    result: dict[str, Any] = {
        "calls": len(rows),
        "succeeded": sum(row["status"] == "SUCCEEDED" for row in rows),
        "failed": sum(row["status"] == "FAILED" for row in rows),
        "timeouts": sum(row.get("error_category") == "timeout" for row in rows),
        "errors": sum(row.get("error") is not None for row in rows),
        "explicit_backend_field_rows": sum(bool(row.get("explicit_backend_fields")) for row in rows),
        "decision_hashes": len({row["decision_hash"] for row in rows if row.get("decision_hash")}),
        "total_selector_wall_ms": sum(float(row["metrics"]["total_wall_ms"]) for row in rows),
        "low_ttft_calls_lt_1000ms": sum(
            row["metrics"].get("ttft_ms") is not None and row["metrics"]["ttft_ms"] < LOW_TTFT_THRESHOLD_MS
            for row in rows
        ),
        "cold_or_regression_calls_gte_5000ms": sum(
            row["metrics"].get("ttft_ms") is not None and row["metrics"]["ttft_ms"] >= COLD_TTFT_THRESHOLD_MS
            for row in rows
        ),
    }
    for name in names:
        values = [float(row["metrics"][name]) for row in rows if row["metrics"].get(name) is not None]
        result[name] = {
            "count": len(values),
            "p50": percentile(values, 50),
            "p95": percentile(values, 95),
            "p99": percentile(values, 99),
            "mean": sum(values) / len(values) if values else None,
        }
    low = [row for row in rows if row["metrics"].get("ttft_ms") is not None and row["metrics"]["ttft_ms"] < LOW_TTFT_THRESHOLD_MS]
    result["first_low_ttft_call_index"] = low[0]["call_index"] if low else None
    result["warmup_wall_ms_to_first_low_ttft"] = (
        sum(float(row["metrics"]["total_wall_ms"]) for row in rows[: low[0]["call_index"] + 1])
        if low
        else None
    )
    return result


def compare(plan: dict[str, Any], baseline: list[dict[str, Any]], explicit: list[dict[str, Any]]) -> dict[str, Any]:
    b = {row["chunk_index"]: row for row in baseline if row["status"] == "SUCCEEDED"}
    e = {row["chunk_index"]: row for row in explicit if row["status"] == "SUCCEEDED"}
    common = sorted(set(b) & set(e))
    matches = [index for index in common if b[index].get("decision_hash") == e[index].get("decision_hash")]
    divergences = [
        {
            "chunk_index": index,
            "baseline_selected_event_ids": b[index].get("selected_event_ids"),
            "explicit_selected_event_ids": e[index].get("selected_event_ids"),
        }
        for index in common
        if b[index].get("decision_hash") != e[index].get("decision_hash")
    ]
    explicit_metrics = metrics(explicit)
    baseline_metrics = metrics(baseline)
    projected_total = explicit_metrics["total_selector_wall_ms"] / 1000 + HISTORICAL_DOWNSTREAM_SECONDS
    return {
        "baseline_metrics": baseline_metrics,
        "explicit_metrics": explicit_metrics,
        "decision_equivalence": {
            "identical": len(matches),
            "compared": len(common),
            "planned": CALL_COUNT,
            "match_fraction": len(matches) / CALL_COUNT,
            "divergences": divergences,
            "unknown_id_or_schema_failures": sum(
                row.get("error_category") == "selector_protocol" for row in baseline + explicit
            ),
            "timeouts": sum(row.get("error_category") == "timeout" for row in baseline + explicit),
            "retries": 0,
        },
        "projected_end_to_end": {
            "selector_wall_seconds": explicit_metrics["total_selector_wall_ms"] / 1000,
            "downstream_held_constant_seconds": HISTORICAL_DOWNSTREAM_SECONDS,
            "total_wall_seconds": projected_total,
            "retirement_tok_s": HISTORICAL_RETIRED_SOURCE_TOKENS / projected_total,
            "speedup_vs_historical_total": HISTORICAL_END_TO_END_WALL_SECONDS / projected_total,
            "margin_over_freetoshop_arrival_tok_s": (
                HISTORICAL_RETIRED_SOURCE_TOKENS / projected_total
                - REAL_FREETOSHOP_ARRIVAL_TOKENS_PER_SECOND
            ),
            "is_projection": True,
        },
    }


def summarize(plan: dict[str, Any]) -> None:
    verify_plan(plan)
    baseline = rows_for(RUN_ROOT / "baseline")
    explicit = rows_for(RUN_ROOT / "explicit")
    result = compare(plan, baseline, explicit)
    write_json(OUT_ROOT / "SUMMARY.json", {"plan_sha256": plan["plan_sha256"], "completed_at": utc_now(), **result})

    def f(value: Any) -> str:
        return "n/a" if value is None else f"{float(value):.1f}"

    lines = [
        "# PanoRef selector explicit prefix-cache / slot-affinity validation",
        "",
        "This report measures the same frozen 148-call, serial, streamed selector request trace in two fresh dedicated LM Studio llama.cpp instances. Request prompt, evidence, dynamic exact-ID schema, model settings, and decoding are unchanged; only backend instance routing and context-checkpoint capacity differ. No retries were issued, and failed rows remain in each telemetry file.",
        "",
        "## Result",
        "",
        "| Condition | Calls | TTFT p50 | TTFT p95 | Selector wall | Decision match | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, data in (("Baseline/current 32", result["baseline_metrics"]), ("Explicit prefix 148", result["explicit_metrics"])):
        lines.append(
            f"| {label} | {data['calls']} | {f(data['ttft_ms']['p50'])} ms | {f(data['ttft_ms']['p95'])} ms | "
            f"{data['total_selector_wall_ms'] / 1000:.1f} s | "
            f"{result['decision_equivalence']['identical']}/{result['decision_equivalence']['compared']} paired | {data['errors']} |"
        )
    b = result["baseline_metrics"]
    e = result["explicit_metrics"]
    lines.extend(
        [
            "",
            "## Cold to warm and durability observations",
            "",
            f"- Baseline first low-TTFT call: {b['first_low_ttft_call_index']}; warmup wall to that point: {f(b['warmup_wall_ms_to_first_low_ttft'])} ms; low-TTFT calls: {b['low_ttft_calls_lt_1000ms']}; >=5 s regressions: {b['cold_or_regression_calls_gte_5000ms']}.",
            f"- Explicit first low-TTFT call: {e['first_low_ttft_call_index']}; warmup wall to that point: {f(e['warmup_wall_ms_to_first_low_ttft'])} ms; low-TTFT calls: {e['low_ttft_calls_lt_1000ms']}; >=5 s regressions: {e['cold_or_regression_calls_gte_5000ms']}.",
            "- Changing evidence durability is measured by the full 148-call trace. Explicit cache-hit, slot, eviction, and session counters were not emitted by the OpenAI-compatible SSE response; the recorded backend instance identifier is the loaded model instance, not a cache-hit claim.",
            f"- The narrow short-idle and post-cleanup check is recorded separately when present; Solar-between-selector-calls was not run because it would change the existing apparatus ordering (the selector stage precedes Solar).",
            "",
            "## Decision equivalence",
            "",
            f"{result['decision_equivalence']['identical']}/{result['decision_equivalence']['compared']} successful paired chunks matched. Planned denominator is {CALL_COUNT}; unresolved calls therefore cannot count as equivalent. Divergences: {result['decision_equivalence']['divergences']}",
            "",
            "## Projected end-to-end impact",
            "",
            "The following holds the historical 229.3 s downstream Solar/validation wall constant; it is not a full pipeline result.",
            "",
            f"- Explicit selector wall: {result['projected_end_to_end']['selector_wall_seconds']:.1f} s.",
            f"- Projected total: {result['projected_end_to_end']['total_wall_seconds']:.1f} s.",
            f"- Projected retirement throughput: {result['projected_end_to_end']['retirement_tok_s']:.1f} source tok/s.",
            f"- Projected speedup vs historical selector-control total: {result['projected_end_to_end']['speedup_vs_historical_total']:.2f}x.",
            f"- Projected margin over 59.148 tok/s FreetoShop arrival rate: {result['projected_end_to_end']['margin_over_freetoshop_arrival_tok_s']:.1f} tok/s.",
            "",
            "## Frozen identity",
            "",
            f"- Source commit: `{plan['source_commit']}`",
            f"- Plan SHA-256: `{plan['plan_sha256']}`",
            f"- Replay SHA-256: `{plan['replay_sha256']}`",
            f"- Chunk plan SHA-256: `{plan['chunk_plan_sha256']}`",
            f"- Prior selector profile config SHA-256: `{plan['prior_profile_config_sha256']}`",
            f"- Backend capability note: `{CAPABILITIES_PATH}`",
            "",
            "No production ORCHID architecture, selector prompt, selector policy, chunk size, concurrency, dynamic exact-ID schema, decode setting, Solar path, or oracle was changed.",
        ]
    )
    (OUT_ROOT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"summary: {OUT_ROOT / 'SUMMARY.json'}")
    print(f"report: {OUT_ROOT / 'REPORT.md'}")


async def durability(plan: dict[str, Any]) -> None:
    verify_plan(plan)
    run_dir = RUN_ROOT / "durability"
    if run_dir.exists():
        raise RuntimeError(f"refusing to overwrite {run_dir}")
    run_dir.mkdir(parents=True)
    lookup = build_chunk_lookup(*load_profile_inputs()[::2])
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        rows.append(await call_selector(client, plan=plan, chunk=lookup[0], condition="EXPLICIT_PREFIX_148", call_index=0))
        await asyncio.sleep(IDLE_SECONDS)
        rows.append(await call_selector(client, plan=plan, chunk=lookup[1], condition="EXPLICIT_PREFIX_148", call_index=1))
    # A new HTTP client after normal cleanup tests that the loaded instance, not
    # the Python connection, carries any reusable backend state.
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        rows.append(await call_selector(client, plan=plan, chunk=lookup[2], condition="EXPLICIT_PREFIX_148", call_index=2))
    with (run_dir / "telemetry.jsonl").open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    write_json(
        run_dir / "SUMMARY.json",
        {
            "plan_sha256": plan["plan_sha256"],
            "idle_seconds": IDLE_SECONDS,
            "solar_interruption_run": False,
            "rows": len(rows),
            "metrics": metrics(rows),
            "backend_instance_after": instance_snapshot(MODEL_EXPLICIT),
        },
    )
    print(f"durability: {run_dir / 'SUMMARY.json'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze-only", action="store_true")
    parser.add_argument("--run", choices=["BASELINE_CURRENT_32", "EXPLICIT_PREFIX_148"])
    parser.add_argument("--durability", action="store_true")
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    if args.freeze_only:
        freeze()
        return
    plan = read_json(PLAN_PATH)
    if args.run:
        run_dir = RUN_ROOT / ("baseline" if args.run == "BASELINE_CURRENT_32" else "explicit")
        asyncio.run(run_condition(plan, args.run, run_dir))
        return
    if args.durability:
        asyncio.run(durability(plan))
        return
    if args.summarize:
        summarize(plan)
        return
    parser.error("choose --freeze-only, --run, --durability, or --summarize")


if __name__ == "__main__":
    main()
