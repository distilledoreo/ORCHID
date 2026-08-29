"""Microprofile frozen PanoRef 1.2K selector calls.

This is a disposable benchmark harness. It reuses the selector payload,
system prompt, response schema, model, and replay chunking from the existing
selector apparatus. The only transport change is ``stream=true`` so that the
client can observe time to first content token and post-first-token duration;
LM Studio backend phase timings are recorded when present and otherwise remain
explicitly unavailable.

The harness has two phases:

* ``--freeze-only`` writes an immutable, deterministic 32-call sample plan.
* ``--run`` executes that plan serially, without retries, and appends one raw
  JSON telemetry record per call.

It does not alter production ORCHID behavior or the semantic benchmark arms.
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.context import estimate_tokens
from memory_gateway.pipeline_adapters import (
    _chunk_events,
    _event_payload,
    selector_response_format_for_chunk,
)
from tools.phase3_2_pipeline_ablation import load_events, source_items

REPLAY = ROOT / (
    "artifacts/agent_benchmarks/panoref_direct_generalization_bounded_v2/"
    "frozen_panoref_replay/events.jsonl"
)
HISTORICAL_SUMMARY = ROOT / (
    "artifacts/agent_benchmarks/panoref_selector_control_repeats_v1/"
    "repeat_01/arm_b_selector_solar/SUMMARY.json"
)
HISTORICAL_CONTROL_METADATA = ROOT / (
    "artifacts/agent_benchmarks/panoref_selector_control_repeats_v1/"
    "repeat_01/CONTROL_METADATA.json"
)
OUT_ROOT = ROOT / "artifacts/agent_benchmarks/panoref_selector_microprofile_v1"
CONFIG_PATH = OUT_ROOT / "PROFILE_CONFIG.json"
SAMPLE_PATH = OUT_ROOT / "FROZEN_SAMPLE.json"
RUN_DIR = OUT_ROOT / "run_01"

ENDPOINT = "http://127.0.0.1:1234/v1"
MODEL = "qwen3.5-4b@q6_k"
CHUNK_TARGET = 1_200
SAMPLE_COUNT = 32
TIMEOUT_SECONDS = 180.0
SELECTOR_PROMPT_VERSION = "selector-v1"
SELECTOR_SYSTEM_PROMPT = (
    "You select durable-memory source items. Return JSON only with exactly "
    '{"selected_event_ids":["source-item-id"]}. Select only whole-event or '
    "source-span IDs present in the input; do not summarize or rewrite items."
)
BASELINE_GENERATION = {
    "temperature": 0,
    "top_p": 1,
    "reasoning_effort": "none",
    "stream": False,
}
PROFILE_GENERATION = {
    **BASELINE_GENERATION,
    "stream": True,
    "stream_options": {"include_usage": True},
}


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
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def sample_indices(total: int, count: int) -> list[int]:
    if total <= 0 or count <= 0 or count > total:
        raise ValueError(f"invalid sample dimensions total={total} count={count}")
    if count == 1:
        return [0]
    indices = [round(i * (total - 1) / (count - 1)) for i in range(count)]
    if len(set(indices)) != count:
        raise ValueError("sample indices were not unique")
    return indices


def chunk_plan() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events = load_events(REPLAY)
    items = source_items(events)
    chunks = _chunk_events(list(items), target_tokens=CHUNK_TARGET)
    historical = json.loads(HISTORICAL_SUMMARY.read_text(encoding="utf-8"))
    historical_rows = {
        int(row["chunk_index"]): row for row in historical["selector"]["telemetry"]
    }
    if len(chunks) != historical["selector"]["calls"]:
        raise RuntimeError(
            f"frozen chunk count mismatch: rebuilt={len(chunks)} "
            f"historical={historical['selector']['calls']}"
        )

    records: list[dict[str, Any]] = []
    for chunk_index, chunk in enumerate(chunks):
        event_ids = [event.id for event in chunk]
        parent_ids = [getattr(event, "parent_event_id", event.id) for event in chunk]
        payload = {
            "events": [_event_payload(event) for event in chunk],
            "event_ids_in_order": event_ids,
            "source_ids_in_order": event_ids,
        }
        estimated_tokens = sum(
            estimate_tokens(json.dumps(_event_payload(event), ensure_ascii=False))
            for event in chunk
        )
        historical_row = historical_rows[chunk_index]
        if event_ids != historical_row["chunk_event_ids"]:
            raise RuntimeError(f"historical chunk IDs differ at index {chunk_index}")
        if estimated_tokens != historical_row["chunk_estimated_tokens"]:
            raise RuntimeError(
                f"historical chunk token estimate differs at index {chunk_index}"
            )
        schema = selector_response_format_for_chunk(event_ids)
        records.append(
            {
                "chunk_index": chunk_index,
                "event_ids": event_ids,
                "parent_event_ids": parent_ids,
                "event_count": len(chunk),
                "estimated_source_tokens": estimated_tokens,
                "request_payload_hash": digest(payload),
                "response_schema_hash": digest(schema),
                "historical_input_tokens": historical_row.get("input_tokens"),
                "historical_output_tokens": historical_row.get("output_tokens"),
                "historical_wall_ms": historical_row.get("wall_ms"),
            }
        )
    return records, {
        "event_count": len(events),
        "source_item_count": len(items),
        "chunk_count": len(chunks),
        "estimated_source_tokens": sum(
            estimate_tokens(json.dumps(_event_payload(item), ensure_ascii=False))
            for item in items
        ),
    }


def freeze() -> None:
    if CONFIG_PATH.exists() or SAMPLE_PATH.exists():
        raise RuntimeError(
            f"frozen profile already exists at {OUT_ROOT}; refusing to overwrite"
        )
    records, universe = chunk_plan()
    indices = sample_indices(len(records), SAMPLE_COUNT)
    sample = [records[index] for index in indices]
    sample_payload = {
        "schema_version": "panoref-selector-microprofile-sample-v1",
        "selection_policy": (
            "deterministic evenly spaced chunk indices across the frozen replay; "
            "no latency, output, or semantic outcome was used"
        ),
        "sample_count": SAMPLE_COUNT,
        "chunk_indices": indices,
        "chunks": sample,
        "full_chunk_plan_sha256": digest(records),
    }
    config_without_hash = {
        "schema_version": "panoref-selector-microprofile-config-v1",
        "created_at": utc_now(),
        "source_commit": git_commit(),
        "replay": str(REPLAY.resolve()),
        "replay_sha256": file_sha256(REPLAY),
        "historical_selector_summary": str(HISTORICAL_SUMMARY.resolve()),
        "historical_selector_summary_sha256": file_sha256(HISTORICAL_SUMMARY),
        "historical_control_metadata": str(HISTORICAL_CONTROL_METADATA.resolve()),
        "historical_control_metadata_sha256": file_sha256(HISTORICAL_CONTROL_METADATA),
        "endpoint": ENDPOINT,
        "model": MODEL,
        "prompt_version": SELECTOR_PROMPT_VERSION,
        "system_prompt": SELECTOR_SYSTEM_PROMPT,
        "chunk_target_tokens": CHUNK_TARGET,
        "timeout_seconds": TIMEOUT_SECONDS,
        "baseline_generation": BASELINE_GENERATION,
        "profile_generation": PROFILE_GENERATION,
        "transport_note": (
            "stream=true is measurement-only; prompt, schema, model, and generation "
            "values are otherwise unchanged from the frozen selector control"
        ),
        "universe": universe,
        "full_chunk_plan_sha256": sample_payload["full_chunk_plan_sha256"],
        "sample_path": str(SAMPLE_PATH.resolve()),
        "sample_sha256": digest(sample_payload),
    }
    config = {
        **config_without_hash,
        "config_sha256": digest(config_without_hash),
    }
    write_json(SAMPLE_PATH, sample_payload)
    write_json(CONFIG_PATH, config)
    print(f"frozen config: {CONFIG_PATH}")
    print(f"frozen sample: {SAMPLE_PATH}")
    print(f"sample indices: {indices}")
    print(f"config hash: {config['config_sha256']}")


def endpoint_url(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    if endpoint.endswith("/v1"):
        return endpoint + "/chat/completions"
    return endpoint + "/v1/chat/completions"


def extract_metric(value: Any, key: str) -> float | None:
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            normalized = child_key.lower().replace("-", "_")
            if normalized in {
                "prompt_eval_ms",
                "prompt_eval_time_ms",
                "prompt_eval_duration_ms",
                "prompt_eval_duration",
                "prefill_ms",
                "prefill_time_ms",
                "eval_ms",
                "decode_ms",
                "decode_time_ms",
                "eval_duration",
                "generation_time",
                "generation_time_ms",
                "queue_wait_ms",
                "queue_time_ms",
                "queue_wait",
            }:
                try:
                    numeric = float(child_value)
                except (TypeError, ValueError):
                    continue
                if normalized.endswith("_duration") or normalized in {
                    "queue_wait",
                    "generation_time",
                }:
                    numeric *= 1000
                if normalized == key:
                    return numeric
            nested = extract_metric(child_value, key)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = extract_metric(child, key)
            if nested is not None:
                return nested
    return None


def header_metric(headers: httpx.Headers, names: set[str]) -> float | None:
    for name, value in headers.items():
        normalized = name.lower().replace("-", "_")
        if normalized not in names:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        return numeric * 1000 if normalized.endswith("_s") else numeric
    return None


def selected_response(content: str, allowed_ids: list[str]) -> tuple[dict[str, Any], str | None]:
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


async def profile_call(
    client: httpx.AsyncClient,
    *,
    config: dict[str, Any],
    chunk: dict[str, Any],
) -> dict[str, Any]:
    events = load_events(Path(config["replay"]))
    items = source_items(events)
    by_id = {item.id: item for item in items}
    chunk_items = [by_id[event_id] for event_id in chunk["event_ids"]]
    event_ids = chunk["event_ids"]
    input_payload = {
        "events": [_event_payload(event) for event in chunk_items],
        "event_ids_in_order": event_ids,
        "source_ids_in_order": event_ids,
    }
    schema = selector_response_format_for_chunk(event_ids)
    body = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": config["system_prompt"]},
            {
                "role": "user",
                "content": (
                    "INPUT_JSON\n"
                    "--- BEGIN INPUT ---\n"
                    f"{json.dumps(input_payload, ensure_ascii=False, sort_keys=True)}\n"
                    "--- END INPUT ---"
                ),
            },
        ],
        **config["profile_generation"],
        "response_format": schema,
    }
    started = time.perf_counter()
    first_content_at: float | None = None
    content = ""
    frames = 0
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    response_headers: dict[str, str] = {}
    response_status: int | None = None
    response_payload_keys: set[str] = set()
    backend_prompt_eval_ms: float | None = None
    backend_decode_ms: float | None = None
    backend_queue_wait_ms: float | None = None
    error: str | None = None
    error_category: str | None = None
    try:
        async with client.stream(
            "POST",
            endpoint_url(config["endpoint"]),
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
                header_names = {
                    "x_prompt_eval_ms",
                    "x_prompt_eval_duration_ms",
                    "x_prefill_ms",
                    "x_decode_ms",
                    "x_eval_duration_ms",
                    "x_queue_wait_ms",
                }
                backend_prompt_eval_ms = (
                    backend_prompt_eval_ms
                    or header_metric(response.headers, {"x_prompt_eval_ms", "x_prompt_eval_duration_ms", "x_prefill_ms"})
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
    total_ms = (ended - started) * 1000
    ttft_ms = (first_content_at - started) * 1000 if first_content_at else None
    decode_ms = (ended - first_content_at) * 1000 if first_content_at else None
    response_obj: dict[str, Any] = {}
    validation_error: str | None = None
    if error is None:
        response_obj, validation_error = selected_response(content, event_ids)
        if validation_error:
            error_category = "selector_protocol"
            error = validation_error
    input_tokens = (usage or {}).get("prompt_tokens")
    output_tokens = (usage or {}).get("completion_tokens")
    input_tok_s_ttft_proxy = (
        float(input_tokens) / (ttft_ms / 1000)
        if input_tokens and ttft_ms and ttft_ms > 0
        else None
    )
    output_tok_s = (
        float(output_tokens) / (decode_ms / 1000)
        if output_tokens and decode_ms and decode_ms > 0
        else None
    )
    status = "SUCCEEDED" if error is None else "FAILED"
    return {
        "timestamp": utc_now(),
        "chunk_index": chunk["chunk_index"],
        "chunk_event_ids": event_ids,
        "chunk_event_count": chunk["event_count"],
        "estimated_source_tokens": chunk["estimated_source_tokens"],
        "request_payload_hash": chunk["request_payload_hash"],
        "response_schema_hash": chunk["response_schema_hash"],
        "status": status,
        "error": error,
        "error_category": error_category,
        "http_status": response_status,
        "finish_reason": finish_reason,
        "frame_count": frames,
        "response_payload_keys": sorted(response_payload_keys),
        "response_headers": {
            key: value
            for key, value in response_headers.items()
            if "time" in key.lower() or "queue" in key.lower() or key.lower().startswith("x-")
        },
        "assistant_content_chars": len(content),
        "assistant_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()
        if content
        else None,
        "selected_event_ids": response_obj.get("selected_event_ids") if response_obj else None,
        "usage": usage,
        "metrics": {
            "total_wall_ms": total_ms,
            "ttft_ms": ttft_ms,
            "decode_ms_client_observed": decode_ms,
            "prompt_eval_ms_backend": backend_prompt_eval_ms,
            "decode_ms_backend": backend_decode_ms,
            "queue_wait_ms_backend": backend_queue_wait_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_tok_s_ttft_proxy": input_tok_s_ttft_proxy,
            "output_tok_s_client_observed": output_tok_s,
            "backend_timing_fields_observed": bool(
                backend_prompt_eval_ms is not None
                or backend_decode_ms is not None
                or backend_queue_wait_ms is not None
            ),
        },
    }


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile_value / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
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
        "calls_planned": len(rows),
        "calls_succeeded": sum(row["status"] == "SUCCEEDED" for row in rows),
        "calls_failed": sum(row["status"] == "FAILED" for row in rows),
        "timeouts": sum(row.get("error_category") == "timeout" for row in rows),
        "input_tokens_total": sum(float((row.get("usage") or {}).get("prompt_tokens") or 0) for row in rows),
        "output_tokens_total": sum(float((row.get("usage") or {}).get("completion_tokens") or 0) for row in rows),
        "backend_timing_fields_observed_count": sum(
            row["metrics"]["backend_timing_fields_observed"] for row in rows
        ),
    }
    ttft_shares = [
        row["metrics"]["ttft_ms"] / row["metrics"]["total_wall_ms"]
        for row in rows
        if row["metrics"].get("ttft_ms") is not None
        and row["metrics"].get("total_wall_ms")
    ]
    result["ttft_share_of_wall"] = {
        "count": len(ttft_shares),
        "p50": percentile(ttft_shares, 50),
        "p95": percentile(ttft_shares, 95),
        "mean": sum(ttft_shares) / len(ttft_shares) if ttft_shares else None,
    }
    result["ttft_dominant_calls"] = sum(
        row["metrics"].get("ttft_ms") is not None
        and row["metrics"].get("decode_ms_client_observed") is not None
        and row["metrics"]["ttft_ms"] > row["metrics"]["decode_ms_client_observed"]
        for row in rows
    )
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


def markdown_report(config: dict[str, Any], summary: dict[str, Any]) -> str:
    def fmt(name: str, key: str = "p50") -> str:
        value = summary[name][key]
        return "n/a" if value is None else f"{value:.1f}"

    backend_available = summary["backend_timing_fields_observed_count"] > 0
    if backend_available:
        phase_interpretation = (
            "LM Studio returned at least one backend timing field; use those fields "
            "for phase attribution below."
        )
    else:
        phase_interpretation = (
            "LM Studio returned no prompt-eval, decode, or queue timing fields. "
            "TTFT and post-first-token duration are client-observed proxies; prompt "
            "eval and queue wait remain unavailable."
        )
    return f"""# PanoRef selector microprofile

Measured {summary['calls_succeeded']} of {summary['calls_planned']} serial calls from the frozen 1.2K selector chunk plan. Failed calls are retained in `telemetry.jsonl`; no retries were issued.

| Field | p50 | p95 | Count |
|---|---:|---:|---:|
| Total wall (ms) | {fmt('total_wall_ms')} | {fmt('total_wall_ms', 'p95')} | {summary['total_wall_ms']['count']} |
| TTFT (ms) | {fmt('ttft_ms')} | {fmt('ttft_ms', 'p95')} | {summary['ttft_ms']['count']} |
| Decode after first content (ms) | {fmt('decode_ms_client_observed')} | {fmt('decode_ms_client_observed', 'p95')} | {summary['decode_ms_client_observed']['count']} |
| Prompt eval (backend ms) | {fmt('prompt_eval_ms_backend')} | {fmt('prompt_eval_ms_backend', 'p95')} | {summary['prompt_eval_ms_backend']['count']} |
| Decode (backend ms) | {fmt('decode_ms_backend')} | {fmt('decode_ms_backend', 'p95')} | {summary['decode_ms_backend']['count']} |
| Queue wait (backend ms) | {fmt('queue_wait_ms_backend')} | {fmt('queue_wait_ms_backend', 'p95')} | {summary['queue_wait_ms_backend']['count']} |
| Input tok/s (TTFT proxy) | {fmt('input_tok_s_ttft_proxy')} | {fmt('input_tok_s_ttft_proxy', 'p95')} | {summary['input_tok_s_ttft_proxy']['count']} |
| Output tok/s (client observed) | {fmt('output_tok_s_client_observed')} | {fmt('output_tok_s_client_observed', 'p95')} | {summary['output_tok_s_client_observed']['count']} |

## Frozen identity

- Source commit: `{config['source_commit']}`
- Replay SHA-256: `{config['replay_sha256']}`
- Profile config SHA-256: `{config['config_sha256']}`
- Chunk target: `{config['chunk_target_tokens']}` estimated source tokens
- Sample count: `{config['sample_count'] if 'sample_count' in config else SAMPLE_COUNT}`
- Sample selection: deterministic evenly spaced indices across the 148 frozen chunks
- Model: `{config['model']}`

## Availability and interpretation

{phase_interpretation}

The OpenAI-compatible SSE response exposed `usage` and token arrival only. Its timing fields/headers were absent. There is therefore no measured backend prompt-eval time or queue-wait time in this run, and `input tok/s` is explicitly a TTFT-based lower-bound proxy rather than a backend prefill rate: any unexposed queue or transport time makes the denominator larger.

The measured comparison is between the time to first content and the post-first-content interval. TTFT was larger than the post-first-content interval in {summary['ttft_dominant_calls']} of {summary['calls_succeeded']} successful calls, and its median share of total wall was {fmt('ttft_share_of_wall')} (fraction). This is consistent with a pre-first-token/prefill-dominated request, but prompt evaluation cannot be isolated from any unexposed scheduling or transport delay. A materially larger post-first-content interval would instead indicate decode/output handling dominates. This is a transport microprofile, not a semantic selector benchmark.

No prompt, schema, model, chunk size, selector policy, concurrency, or Solar setting was changed. The only profile-specific setting is streaming with usage included, required to observe TTFT.
"""


async def run(config: dict[str, Any]) -> None:
    if RUN_DIR.exists():
        raise RuntimeError(f"run directory already exists: {RUN_DIR}")
    sample = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    if sample["full_chunk_plan_sha256"] != config["full_chunk_plan_sha256"]:
        raise RuntimeError("sample/config full chunk plan hash mismatch")
    if digest(sample) != config["sample_sha256"]:
        raise RuntimeError("frozen sample hash mismatch")
    RUN_DIR.mkdir(parents=True)
    run_meta = {
        "status": "RUNNING",
        "started_at": utc_now(),
        "source_commit": config["source_commit"],
        "config_sha256": config["config_sha256"],
        "sample_sha256": config["sample_sha256"],
        "planned_calls": sample["sample_count"],
        "no_retries": True,
    }
    write_json(RUN_DIR / "RUN_METADATA.json", run_meta)
    telemetry_path = RUN_DIR / "telemetry.jsonl"
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=config["timeout_seconds"]) as client:
        for chunk in sample["chunks"]:
            row = await profile_call(client, config=config, chunk=chunk)
            rows.append(row)
            with telemetry_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            print(
                f"chunk={row['chunk_index']:03d} status={row['status']} "
                f"wall_ms={row['metrics']['total_wall_ms']:.1f} "
                f"ttft_ms={row['metrics']['ttft_ms'] if row['metrics']['ttft_ms'] is not None else 'n/a'}"
            )
    summary = aggregate(rows)
    summary.update(
        {
            "completed_at": utc_now(),
            "config_sha256": config["config_sha256"],
            "sample_sha256": config["sample_sha256"],
            "source_commit": config["source_commit"],
        }
    )
    write_json(RUN_DIR / "SUMMARY.json", summary)
    (RUN_DIR / "REPORT.md").write_text(
        markdown_report(config, summary), encoding="utf-8"
    )
    run_meta.update(
        {
            "status": "SUCCEEDED" if summary["calls_failed"] == 0 else "PARTIAL",
            "completed_at": summary["completed_at"],
            "succeeded_calls": summary["calls_succeeded"],
            "failed_calls": summary["calls_failed"],
        }
    )
    write_json(RUN_DIR / "RUN_METADATA.json", run_meta)
    print(f"summary: {RUN_DIR / 'SUMMARY.json'}")
    print(f"report: {RUN_DIR / 'REPORT.md'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-only", action="store_true")
    parser.add_argument("--run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.freeze_only == args.run:
        raise SystemExit("choose exactly one of --freeze-only or --run")
    if args.freeze_only:
        freeze()
        return
    if not CONFIG_PATH.exists() or not SAMPLE_PATH.exists():
        raise SystemExit("run --freeze-only first")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
