"""Measure English versus faithful and dense Simplified-Chinese selector input.

This is a disposable benchmark harness.  It reuses the frozen PanoRef 1.2K
chunk plan and exact opaque source IDs.  Only natural-language text in
message/agent_message source items is translated; tool payloads, diagnostics,
code, protocol keys, hashes, paths, URLs, numbers, and IDs are protected or
left unchanged.  Translation variants are generated and frozen before any
selector benchmark arm is run.

Commands:

  freeze-plan       write the immutable experiment plan
  generate --scope gate|full
                    generate and freeze translated chunk payloads
  run --scope gate|full --run-id NAME
                    run serial A/B/C selector calls
  report --run-id NAME
                    write summary, paired audit, and Markdown report
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.compaction import expand_source_items, source_item_payload
from memory_gateway.context import estimate_tokens
from memory_gateway.pipeline_adapters import selector_response_format_for_chunk
from tools.phase3_2_pipeline_ablation import load_events
from tools.profile_panoref_selector_calls import (
    CHUNK_TARGET,
    _chunk_events,
    endpoint_url,
    extract_metric,
    header_metric,
    percentile,
)

OUT_ROOT = ROOT / "artifacts/agent_benchmarks/panoref_selector_language_v1"
PLAN_PATH = OUT_ROOT / "LANGUAGE_PLAN.json"
GATE_VARIANTS_PATH = OUT_ROOT / "VARIANTS_GATE.json"
FULL_VARIANTS_PATH = OUT_ROOT / "VARIANTS_FULL.json"
PROFILE_CONFIG_PATH = ROOT / "artifacts/agent_benchmarks/panoref_selector_microprofile_v1/PROFILE_CONFIG.json"
PROFILE_SAMPLE_PATH = ROOT / "artifacts/agent_benchmarks/panoref_selector_microprofile_v1/FROZEN_SAMPLE.json"
REQUEST_ENCODING_PLAN_PATH = ROOT / "artifacts/agent_benchmarks/panoref_selector_request_encoding_v1/REQUEST_ENCODING_PLAN.json"

MODEL = "qwen3.5-4b@q6_k"
TRANSLATOR_SYSTEM = (
    "You are a faithful translation preprocessor. Translate only ordinary "
    "natural-language prose into Simplified Chinese. Do not summarize, omit, "
    "reorder, or add facts. Preserve every ORCHIDKEEP token, every "
    "ORCHIDNL token, JSON syntax, protocol keys, code, paths, URLs, hashes, "
    "opaque IDs, filenames, numbers, technical identifiers, and markdown "
    "structure exactly. Return JSON only with translated_text."
)
DENSIFIER_SYSTEM = (
    "将输入的简体中文压缩成更简洁的简体中文，但必须保留全部事实、范围、"
    "时间关系、实体关系、技术细节和选择判断含义。不要总结、删除、合并或新增"
    "信息。必须原样保留每个 ORCHIDKEEP token、每个 ORCHIDNL token、JSON 语法、"
    "代码、路径、URL、哈希、原始 ID、文件名、数字、技术标识符和 Markdown 结构。"
    "只返回 JSON，字段为 dense_text。"
)
FAITHFUL_SYSTEM_PROMPT = (
    "你要选择可长期保留的记忆源项目。仅返回 JSON，且必须严格包含 "
    '{"selected_event_ids":["source-item-id"]}。只能选择输入中存在的完整事件 ID '
    "或源跨度 ID；不要总结或改写项目。"
)
DENSE_SYSTEM_PROMPT = (
    "选出耐久记忆源项。仅输出 JSON："
    '{"selected_event_ids":["source-item-id"]}。只能选输入中存在的完整事件或源跨度 ID；勿总结或改写。'
)
ENGLISH_SYSTEM_PROMPT = (
    'You select durable-memory source items. Return JSON only with exactly '
    '{"selected_event_ids":["source-item-id"]}. Select only whole-event or '
    "source-span IDs present in the input; do not summarize or rewrite items."
)
ENGLISH_PREFIX = "INPUT_JSON\n--- BEGIN INPUT ---\n"
ENGLISH_SUFFIX = "\n--- END INPUT ---"
FAITHFUL_PREFIX = "输入JSON\n--- 输入开始 ---\n"
FAITHFUL_SUFFIX = "\n--- 输入结束 ---"
DENSE_PREFIX = "输入JSON\n---\n"
DENSE_SUFFIX = "\n---"
ELIGIBLE_EVENT_TYPES = {"message", "agent_message"}
GATE_INDICES = [
    0, 6, 13, 19, 26, 32, 38, 45, 51, 58, 64, 70,
    77, 83, 89, 96, 102, 109, 115, 121, 128, 134, 141, 147,
]
TIMEOUT_SECONDS = 180.0


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def load_inputs() -> tuple[dict[str, Any], dict[int, dict[str, Any]], dict[str, Any]]:
    profile = json.loads(PROFILE_CONFIG_PATH.read_text(encoding="utf-8"))
    sample = json.loads(PROFILE_SAMPLE_PATH.read_text(encoding="utf-8"))
    events = load_events(Path(profile["replay"]))
    items = list(expand_source_items(events, selector_budget_tokens=CHUNK_TARGET, safety_margin=0.8))
    chunks = _chunk_events(items, target_tokens=CHUNK_TARGET)
    frozen_by_index = {int(row["chunk_index"]): row for row in sample["chunks"]}
    lookup: dict[int, dict[str, Any]] = {}
    for index, chunk in enumerate(chunks):
        payload = {
            "events": [source_item_payload(item) for item in chunk],
            "event_ids_in_order": [item.id for item in chunk],
            "source_ids_in_order": [item.id for item in chunk],
        }
        frozen = frozen_by_index.get(index)
        if frozen is not None:
            if frozen["event_ids"] != [item.id for item in chunk]:
                raise RuntimeError(f"frozen chunk IDs differ at index {index}")
            if digest(payload) != frozen["request_payload_hash"]:
                raise RuntimeError(f"frozen request payload differs at index {index}")
        lookup[index] = {
            "chunk_index": index,
            "event_ids": [item.id for item in chunk],
            "payload": payload,
            "estimated_source_tokens": sum(
                estimate_tokens(json.dumps(source_item_payload(item), ensure_ascii=False)) for item in chunk
            ),
        }
    if len(lookup) != 148:
        raise RuntimeError(f"expected 148 frozen chunks, got {len(lookup)}")
    return profile, lookup, sample


def plan_without_hash() -> dict[str, Any]:
    profile, _, _ = load_inputs()
    return {
        "schema_version": "panoref-selector-language-plan-v1",
        "created_at": utc_now(),
        "source_commit": git_commit(),
        "profile_config": str(PROFILE_CONFIG_PATH.resolve()),
        "profile_config_sha256": file_sha256(PROFILE_CONFIG_PATH),
        "profile_sample": str(PROFILE_SAMPLE_PATH.resolve()),
        "profile_sample_sha256": file_sha256(PROFILE_SAMPLE_PATH),
        "request_encoding_plan": str(REQUEST_ENCODING_PLAN_PATH.resolve()),
        "request_encoding_plan_sha256": file_sha256(REQUEST_ENCODING_PLAN_PATH),
        "replay": profile["replay"],
        "replay_sha256": profile["replay_sha256"],
        "endpoint": profile["endpoint"],
        "model": MODEL,
        "chunk_target_tokens": CHUNK_TARGET,
        "chunk_count": 148,
        "gate_chunk_indices": GATE_INDICES,
        "translator_model": MODEL,
        "translator_generation": {"temperature": 0, "top_p": 1, "reasoning_effort": "none", "stream": False},
        "translation_prompt_sha256": digest({"faithful": TRANSLATOR_SYSTEM, "dense": DENSIFIER_SYSTEM}),
        "eligible_event_types": sorted(ELIGIBLE_EVENT_TYPES),
        "opaque_id_policy": "canonical opaque IDs are copied from frozen payloads and never translated or reordered",
        "technical_preservation_policy": "tool calls, tool outputs, diagnostics, code, paths, URLs, hashes, numbers, protocol keys, and technical identifiers are protected or unchanged",
        "arm_definitions": {
            "A_CANONICAL_ENGLISH": {"system_prompt": ENGLISH_SYSTEM_PROMPT, "prefix": ENGLISH_PREFIX, "suffix": ENGLISH_SUFFIX},
            "B_FAITHFUL_CHINESE": {"system_prompt": FAITHFUL_SYSTEM_PROMPT, "prefix": FAITHFUL_PREFIX, "suffix": FAITHFUL_SUFFIX},
            "C_DENSE_CHINESE": {"system_prompt": DENSE_SYSTEM_PROMPT, "prefix": DENSE_PREFIX, "suffix": DENSE_SUFFIX},
        },
        "generation_rules": {
            "same_model_runtime_settings": True,
            "serial": True,
            "same_chunk_order": True,
            "same_response_schema": True,
            "same_opaque_ids": True,
            "no_retries": True,
            "no_cache_or_concurrency_changes": True,
            "outcome_blind_translation": True,
        },
    }


def freeze_plan() -> None:
    if PLAN_PATH.exists():
        raise RuntimeError(f"refusing to overwrite frozen plan: {PLAN_PATH}")
    raw = plan_without_hash()
    write_json(PLAN_PATH, {**raw, "plan_sha256": digest(raw)})
    print(f"frozen plan: {PLAN_PATH}")
    print(f"plan hash: {digest(raw)}")


def load_plan() -> dict[str, Any]:
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    expected = dict(plan)
    actual = expected.pop("plan_sha256", None)
    if actual != digest(expected):
        raise RuntimeError("language plan hash mismatch")
    return plan


PRESERVE_PATTERNS = [
    re.compile(r"```[\s\S]*?```"),
    re.compile(r"`[^`\n]+`"),
    re.compile(r"https?://[^\s\"']+"),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"']+"),
    re.compile(r"(?<![A-Za-z0-9])[A-Fa-f0-9]{32,}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])(?:panoref_evt|msg|ctc|call|amsg|evt)_[A-Za-z0-9_-]+"),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_./\\:-]*[_./\\:][A-Za-z0-9_./\\:-]+"),
    re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?%?(?![A-Za-z0-9])"),
    re.compile(r'"(?:[^"\\]|\\.)*"(?=\s*:)'),
    re.compile(r'"[A-Za-z0-9_=-]{40,}"'),
]


def protect_text(text: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}
    counter = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal counter
        key = f"ORCHIDKEEP{counter:05d}X"
        counter += 1
        protected[key] = match.group(0)
        return key

    result = text
    for pattern in PRESERVE_PATTERNS:
        result = pattern.sub(replace, result)

    def replace_newline(match: re.Match[str]) -> str:
        nonlocal counter
        key = f"ORCHIDNL{counter:05d}X"
        counter += 1
        protected[key] = match.group(0)
        return key

    result = re.sub(r"\\r\\n|\\r|\\n|\r\n|\n", replace_newline, result)
    return result, protected


def restore_text(text: str, protected: dict[str, str]) -> str:
    missing = [key for key in protected if text.count(key) != 1]
    if missing:
        raise ValueError(f"translator changed protected tokens: {missing[:8]}")
    for key, value in protected.items():
        text = text.replace(key, value)
    return text


def translation_schema(field: str) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "selector_language_translation_v1",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {field: {"type": "string"}},
                "required": [field],
                "additionalProperties": False,
            },
        },
    }


def translate_one(text: str, *, dense: bool, client: httpx.Client) -> tuple[str, dict[str, Any]]:
    protected_text, protected = protect_text(text)
    field = "dense_text" if dense else "translated_text"
    system = DENSIFIER_SYSTEM if dense else TRANSLATOR_SYSTEM
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": protected_text},
        ],
        "temperature": 0,
        "top_p": 1,
        "reasoning_effort": "none",
        "stream": False,
        "response_format": translation_schema(field),
    }
    started = time.perf_counter()
    response = client.post("http://127.0.0.1:1234/v1/chat/completions", json=body)
    wall_ms = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    payload = response.json()
    content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
    if not isinstance(content, str):
        raise ValueError("translation response had no message content")
    value = json.loads(content).get(field)
    if not isinstance(value, str):
        raise ValueError(f"translation response missing {field}")
    return restore_text(value, protected), {
        "status": "SUCCEEDED",
        "wall_ms": wall_ms,
        "usage": payload.get("usage"),
        "input_chars": len(text),
        "protected_token_count": len(protected),
        "output_chars": len(value),
    }


def translate_text_value(text: str, *, dense: bool, client: httpx.Client) -> tuple[str, list[dict[str, Any]]]:
    """Translate paragraph-sized prose units to keep preservation prompts bounded."""

    parts = re.split(r"(\n\s*\n)", text)
    translated_parts: list[str] = []
    metrics: list[dict[str, Any]] = []
    for part in parts:
        if not part or part.isspace():
            translated_parts.append(part)
            continue
        translated, call_metrics = translate_one(part, dense=dense, client=client)
        translated_parts.append(translated)
        metrics.append(call_metrics)
    return "".join(translated_parts), metrics


def transform_item(item: dict[str, Any], *, client: httpx.Client) -> tuple[dict[str, Any], dict[str, Any]]:
    original = item["content"]
    if item["event_type"] not in ELIGIBLE_EVENT_TYPES:
        return item, {"item_id": item["id"], "event_type": item["event_type"], "status": "UNCHANGED"}

    def translate_text_nodes(value: Any, *, dense: bool) -> tuple[Any, list[dict[str, Any]]]:
        metrics: list[dict[str, Any]] = []
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if key == "text" and isinstance(child, str):
                    translated, call_metrics = translate_text_value(child, dense=dense, client=client)
                    result[key] = translated
                    metrics.extend(call_metrics)
                else:
                    result[key], child_metrics = translate_text_nodes(child, dense=dense)
                    metrics.extend(child_metrics)
            return result, metrics
        if isinstance(value, list):
            result_list = []
            for child in value:
                translated_child, child_metrics = translate_text_nodes(child, dense=dense)
                result_list.append(translated_child)
                metrics.extend(child_metrics)
            return result_list, metrics
        return value, metrics

    translation_mode = "raw_span"
    try:
        parsed = json.loads(original)
        if "source_kind" not in item:
            translation_mode = "parsed_text_nodes"
            faithful_obj, faithful_metrics = translate_text_nodes(parsed, dense=False)
            dense_obj, dense_metrics = translate_text_nodes(faithful_obj, dense=True)
            faithful = json.dumps(faithful_obj, ensure_ascii=False)
            dense = json.dumps(dense_obj, ensure_ascii=False)
        else:
            faithful, faithful_call_metrics = translate_one(original, dense=False, client=client)
            dense, dense_call_metrics = translate_one(faithful, dense=True, client=client)
            faithful_metrics = [faithful_call_metrics]
            dense_metrics = [dense_call_metrics]
    except json.JSONDecodeError:
        faithful, faithful_call_metrics = translate_one(original, dense=False, client=client)
        dense, dense_call_metrics = translate_one(faithful, dense=True, client=client)
        faithful_metrics = [faithful_call_metrics]
        dense_metrics = [dense_call_metrics]
    return {
        **item,
        "content": faithful,
    }, {
        "item_id": item["id"],
        "event_type": item["event_type"],
        "status": "TRANSLATED",
        "translation_mode": translation_mode,
        "original_content_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
        "faithful_content_sha256": hashlib.sha256(faithful.encode("utf-8")).hexdigest(),
        "dense_content_sha256": hashlib.sha256(dense.encode("utf-8")).hexdigest(),
        "faithful_metrics": faithful_metrics,
        "dense_metrics": dense_metrics,
        "faithful_content": faithful,
        "dense_content": dense,
    }


def generate(scope: str) -> None:
    plan = load_plan()
    _, lookup, _ = load_inputs()
    target_indices = GATE_INDICES if scope == "gate" else list(range(148))
    output_path = GATE_VARIANTS_PATH if scope == "gate" else FULL_VARIANTS_PATH
    if output_path.exists():
        raise RuntimeError(f"refusing to overwrite frozen variants: {output_path}")
    target_ids = [item_id for index in target_indices for item_id in lookup[index]["event_ids"]]
    unique_items: dict[str, dict[str, Any]] = {}
    for index in target_indices:
        for item in lookup[index]["payload"]["events"]:
            unique_items.setdefault(item["id"], item)
    calls: list[dict[str, Any]] = []
    transformed: dict[str, dict[str, Any]] = {}
    resume_cache: dict[str, dict[str, Any]] = {}
    for failure_path in sorted(OUT_ROOT.glob(f"TRANSLATION_FAILURE_{scope}_attempt_*.json")):
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        for completed in failure.get("completed_calls", []):
            if completed.get("status") == "TRANSLATED" and completed.get("faithful_content") and completed.get("dense_content"):
                resume_cache[completed["item_id"]] = {**completed, "resumed_from": failure_path.name}
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        for position, item in enumerate(unique_items.values(), start=1):
            print(f"translate {position}/{len(unique_items)} {item['id']}", flush=True)
            resumed = resume_cache.get(item["id"])
            if resumed is not None:
                calls.append({"item_id": item["id"], "event_type": item["event_type"], "status": "RESUMED_FROM_FAILED_ATTEMPT", "resumed_from": resumed["resumed_from"], "faithful_content_sha256": resumed["faithful_content_sha256"], "dense_content_sha256": resumed["dense_content_sha256"], "faithful_content": resumed["faithful_content"], "dense_content": resumed["dense_content"], "faithful_metrics": resumed.get("faithful_metrics", []), "dense_metrics": resumed.get("dense_metrics", [])})
                transformed[item["id"]] = calls[-1]
                continue
            try:
                _, audit = transform_item(item, client=client)
                calls.append(audit)
                if audit["status"] == "TRANSLATED":
                    transformed[item["id"]] = audit
            except Exception as error:
                failure = {"item_id": item["id"], "event_type": item["event_type"], "status": "FAILED", "error": f"{type(error).__name__}: {error}"}
                calls.append(failure)
                write_json(OUT_ROOT / f"TRANSLATION_FAILURE_{scope}.json", {"scope": scope, "failure": failure, "completed_calls": calls})
                raise
    variant_chunks = []
    for index in target_indices:
        base = lookup[index]
        faithful_payload = copy.deepcopy(base["payload"])
        dense_payload = copy.deepcopy(base["payload"])
        for event in faithful_payload["events"]:
            audit = transformed.get(event["id"])
            if audit:
                event["content"] = audit["faithful_content"]
        for event in dense_payload["events"]:
            audit = transformed.get(event["id"])
            if audit:
                event["content"] = audit["dense_content"]
        variant_chunks.append({
            "chunk_index": index,
            "event_ids": base["event_ids"],
            "estimated_source_tokens": base["estimated_source_tokens"],
            "canonical_payload_hash": digest(base["payload"]),
            "faithful_payload_hash": digest(faithful_payload),
            "dense_payload_hash": digest(dense_payload),
            "canonical_payload": base["payload"],
            "faithful_payload": faithful_payload,
            "dense_payload": dense_payload,
        })
    artifact = {
        "schema_version": "panoref-selector-language-variants-v1",
        "created_at": utc_now(),
        "scope": scope,
        "plan_sha256": plan["plan_sha256"],
        "source_commit": plan["source_commit"],
        "chunk_indices": target_indices,
        "translation_calls": calls,
        "translated_item_count": len(transformed),
        "variant_chunks": variant_chunks,
    }
    write_json(output_path, artifact)
    print(f"variants: {output_path}")
    print(f"variant hash: {file_sha256(output_path)}")


def load_variants(scope: str) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    plan = load_plan()
    path = GATE_VARIANTS_PATH if scope == "gate" else FULL_VARIANTS_PATH
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact["plan_sha256"] != plan["plan_sha256"]:
        raise RuntimeError("variant plan hash mismatch")
    return plan, {int(row["chunk_index"]): row for row in artifact["variant_chunks"]}


def build_request(plan: dict[str, Any], chunk: dict[str, Any], arm: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if arm == "A_CANONICAL_ENGLISH":
        system, prefix, suffix, payload = ENGLISH_SYSTEM_PROMPT, ENGLISH_PREFIX, ENGLISH_SUFFIX, chunk["canonical_payload"]
    elif arm == "B_FAITHFUL_CHINESE":
        system, prefix, suffix, payload = FAITHFUL_SYSTEM_PROMPT, FAITHFUL_PREFIX, FAITHFUL_SUFFIX, chunk["faithful_payload"]
    elif arm == "C_DENSE_CHINESE":
        system, prefix, suffix, payload = DENSE_SYSTEM_PROMPT, DENSE_PREFIX, DENSE_SUFFIX, chunk["dense_payload"]
    else:
        raise ValueError(arm)
    schema = selector_response_format_for_chunk(chunk["event_ids"])
    body = {
        "model": plan["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prefix + json.dumps(payload, ensure_ascii=False, sort_keys=True) + suffix},
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0,
        "top_p": 1,
        "reasoning_effort": "none",
        "response_format": schema,
    }
    identity = {
        "arm": arm,
        "chunk_index": chunk["chunk_index"],
        "request_hash": digest(body),
        "evidence_hash": digest(payload["events"]),
        "response_schema_hash": digest(schema),
        "system_prompt_hash": digest(system),
        "payload_json_chars": len(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        "estimated_prompt_tokens": estimate_tokens(body["messages"][0]["content"] + body["messages"][1]["content"]),
        "estimated_components": {
            "system": estimate_tokens(system),
            "wrapper": estimate_tokens(prefix + suffix),
            "evidence": estimate_tokens(json.dumps(payload["events"], ensure_ascii=False, sort_keys=True)),
            "schema": estimate_tokens(json.dumps(schema, ensure_ascii=False, sort_keys=True)),
        },
    }
    return body, identity


def find_named_fields(value: Any, path: str = "") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if any(term in key.lower().replace("-", "_") for term in ("cache", "prefix", "kv_")):
                if isinstance(child, (str, int, float, bool)) or child is None:
                    result.append({"path": child_path, "value": child})
            result.extend(find_named_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(find_named_fields(child, f"{path}[{index}]"))
    return result


def parse_response(content: str, allowed_ids: list[str]) -> tuple[list[str] | None, str | None]:
    try:
        response = json.loads(content)
    except json.JSONDecodeError as error:
        return None, f"assistant content was not JSON: {error}"
    if not isinstance(response, dict) or set(response) != {"selected_event_ids"}:
        return None, "selector response did not have exactly selected_event_ids"
    selected = response.get("selected_event_ids")
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        return None, "selected_event_ids was not a string array"
    if not set(selected).issubset(set(allowed_ids)):
        return None, "selector returned an ID outside the current chunk"
    return selected, None


async def call_selector(client: httpx.AsyncClient, plan: dict[str, Any], chunk: dict[str, Any], arm: str, global_index: int) -> dict[str, Any]:
    body, identity = build_request(plan, chunk, arm)
    started = time.perf_counter()
    first_content_at: float | None = None
    content = ""
    usage: dict[str, Any] | None = None
    response_headers: dict[str, str] = {}
    response_status: int | None = None
    response_payload_keys: set[str] = set()
    explicit_cache_fields: list[dict[str, Any]] = []
    backend_prompt_eval_ms = None
    backend_decode_ms = None
    backend_queue_wait_ms = None
    error = None
    error_category = None
    try:
        async with client.stream("POST", endpoint_url(plan["endpoint"]), json=body, headers={"content-type": "application/json"}) as response:
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
                    response_payload_keys.update(payload.keys())
                    explicit_cache_fields.extend(find_named_fields(payload))
                    usage = payload.get("usage") or usage
                    backend_prompt_eval_ms = backend_prompt_eval_ms or extract_metric(payload, "prompt_eval_ms") or extract_metric(payload, "prompt_eval_time_ms") or extract_metric(payload, "prompt_eval_duration_ms") or extract_metric(payload, "prefill_ms")
                    backend_decode_ms = backend_decode_ms or extract_metric(payload, "decode_ms") or extract_metric(payload, "decode_time_ms") or extract_metric(payload, "eval_duration") or extract_metric(payload, "generation_time_ms")
                    backend_queue_wait_ms = backend_queue_wait_ms or extract_metric(payload, "queue_wait_ms") or extract_metric(payload, "queue_time_ms") or extract_metric(payload, "queue_wait")
                    choice = (payload.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    token = delta.get("content") or choice.get("text") or ""
                    if token and first_content_at is None:
                        first_content_at = time.perf_counter()
                    content += token
                backend_prompt_eval_ms = backend_prompt_eval_ms or header_metric(response.headers, {"x_prompt_eval_ms", "x_prompt_eval_duration_ms", "x_prefill_ms"})
                backend_decode_ms = backend_decode_ms or header_metric(response.headers, {"x_decode_ms", "x_eval_duration_ms"})
                backend_queue_wait_ms = backend_queue_wait_ms or header_metric(response.headers, {"x_queue_wait_ms"})
    except httpx.TimeoutException as exc:
        error_category, error = "timeout", str(exc) or "request timed out"
    except Exception as exc:
        error_category, error = "protocol_or_transport", f"{type(exc).__name__}: {exc}"
    ended = time.perf_counter()
    selected, validation_error = (None, None) if error else parse_response(content, chunk["event_ids"])
    if validation_error:
        error_category, error = "selector_protocol", validation_error
    total_ms = (ended - started) * 1000
    ttft_ms = (first_content_at - started) * 1000 if first_content_at else None
    decode_ms = (ended - first_content_at) * 1000 if first_content_at else None
    input_tokens = (usage or {}).get("prompt_tokens")
    output_tokens = (usage or {}).get("completion_tokens")
    return {
        "timestamp": utc_now(),
        "global_call_index": global_index,
        **identity,
        "status": "SUCCEEDED" if error is None else "FAILED",
        "error": error,
        "error_category": error_category,
        "http_status": response_status,
        "response_payload_keys": sorted(response_payload_keys),
        "response_headers": {key: value for key, value in response_headers.items() if key.lower().startswith("x-") or "time" in key.lower() or "queue" in key.lower() or "cache" in key.lower()},
        "explicit_cache_fields": explicit_cache_fields,
        "selected_event_ids": selected,
        "decision_hash": digest(selected) if selected is not None else None,
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
            "input_tok_s_ttft_proxy": float(input_tokens) / (ttft_ms / 1000) if input_tokens and ttft_ms and ttft_ms > 0 else None,
            "output_tok_s_client_observed": float(output_tokens) / (decode_ms / 1000) if output_tokens and decode_ms and decode_ms > 0 else None,
        },
    }


async def run_async(scope: str, run_id: str) -> None:
    plan, chunks = load_variants(scope)
    if set(chunks) != set(GATE_INDICES if scope == "gate" else range(148)):
        raise RuntimeError("variant chunk set is incomplete")
    run_dir = OUT_ROOT / "runs" / run_id
    if run_dir.exists():
        raise RuntimeError(f"refusing to overwrite run directory: {run_dir}")
    run_dir.mkdir(parents=True)
    write_json(run_dir / "RUN_CONFIG.json", {"schema_version": "panoref-selector-language-run-v1", "run_id": run_id, "scope": scope, "plan_sha256": plan["plan_sha256"], "variant_sha256": file_sha256(GATE_VARIANTS_PATH if scope == "gate" else FULL_VARIANTS_PATH), "source_commit": plan["source_commit"], "arm_order": ["A_CANONICAL_ENGLISH", "B_FAITHFUL_CHINESE", "C_DENSE_CHINESE"], "serial": True, "no_retries": True})
    rows_path = run_dir / "CALLS.jsonl"
    global_index = 0
    timeout = httpx.Timeout(TIMEOUT_SECONDS, connect=10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        with rows_path.open("w", encoding="utf-8") as output:
            for chunk_index in sorted(chunks):
                for arm in ("A_CANONICAL_ENGLISH", "B_FAITHFUL_CHINESE", "C_DENSE_CHINESE"):
                    global_index += 1
                    print(f"call {global_index}/{len(chunks) * 3} {arm} chunk={chunk_index}", flush=True)
                    row = await call_selector(client, plan, chunks[chunk_index], arm, global_index)
                    output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    output.flush()
    print(f"run: {run_dir}")


def rows_for_run(run_id: str) -> list[dict[str, Any]]:
    path = OUT_ROOT / "runs" / run_id / "CALLS.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def values(rows: list[dict[str, Any]], metric: str) -> list[float]:
    return [float(row["metrics"][metric]) for row in rows if row["metrics"].get(metric) is not None]


def stats(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    data = values(rows, metric)
    return {"count": len(data), "p50": percentile(data, 50) if data else None, "p95": percentile(data, 95) if data else None, "mean": mean(data) if data else None, "total": sum(data) if data else None}


def report(run_id: str) -> None:
    plan = load_plan()
    rows = rows_for_run(run_id)
    run_dir = OUT_ROOT / "runs" / run_id
    by_arm = {arm: sorted([row for row in rows if row["arm"] == arm], key=lambda row: row["chunk_index"]) for arm in ("A_CANONICAL_ENGLISH", "B_FAITHFUL_CHINESE", "C_DENSE_CHINESE")}
    by_key = {(row["arm"], row["chunk_index"]): row for row in rows}
    paired: dict[str, Any] = {}
    baseline = by_arm["A_CANONICAL_ENGLISH"]
    for arm in ("B_FAITHFUL_CHINESE", "C_DENSE_CHINESE"):
        comparisons = []
        for base in baseline:
            other = by_key.get((arm, base["chunk_index"]))
            if not other or base["status"] != "SUCCEEDED" or other["status"] != "SUCCEEDED":
                continue
            bt, ot = base["metrics"].get("input_tokens"), other["metrics"].get("input_tokens")
            bw, ow = base["metrics"].get("ttft_ms"), other["metrics"].get("ttft_ms")
            comparisons.append({
                "chunk_index": base["chunk_index"],
                "decision_match": base.get("selected_event_ids") == other.get("selected_event_ids"),
                "baseline_input_tokens": bt,
                "translated_input_tokens": ot,
                "token_delta": ot - bt if bt is not None and ot is not None else None,
                "token_reduction_pct": 1 - ot / bt if bt and ot is not None else None,
                "baseline_ttft_ms": bw,
                "translated_ttft_ms": ow,
                "ttft_delta_ms": ow - bw if bw is not None and ow is not None else None,
                "ttft_reduction_pct": 1 - ow / bw if bw and ow is not None else None,
            })
        valid_token = [row for row in comparisons if row["token_reduction_pct"] is not None and row["ttft_reduction_pct"] is not None]
        correlation = None
        if len(valid_token) >= 3:
            xs = [row["token_reduction_pct"] for row in valid_token]
            ys = [row["ttft_reduction_pct"] for row in valid_token]
            mx, my = mean(xs), mean(ys)
            denom = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
            correlation = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom if denom else None
        paired[arm] = {"comparisons": comparisons, "decision_matches": sum(1 for row in comparisons if row["decision_match"]), "decision_compared": len(comparisons), "correlation_token_reduction_vs_ttft_reduction": correlation}
    summary = {
        "schema_version": "panoref-selector-language-summary-v1",
        "run_id": run_id,
        "plan_sha256": plan["plan_sha256"],
        "call_count": len(rows),
        "arms": {
            arm: {
                "calls": len(arm_rows),
                "succeeded": sum(1 for row in arm_rows if row["status"] == "SUCCEEDED"),
                "failed": sum(1 for row in arm_rows if row["status"] != "SUCCEEDED"),
                "input_tokens": stats(arm_rows, "input_tokens"),
                "ttft_ms": stats(arm_rows, "ttft_ms"),
                "prompt_eval_ms_backend": stats(arm_rows, "prompt_eval_ms_backend"),
                "decode_ms_client_observed": stats(arm_rows, "decode_ms_client_observed"),
                "total_wall_ms": stats(arm_rows, "total_wall_ms"),
                "output_tokens": stats(arm_rows, "output_tokens"),
                "errors": [{"chunk_index": row["chunk_index"], "error_category": row["error_category"], "error": row["error"]} for row in arm_rows if row["status"] != "SUCCEEDED"],
            } for arm, arm_rows in by_arm.items()
        },
        "paired": paired,
    }
    write_json(run_dir / "SUMMARY.json", summary)
    write_json(run_dir / "DECISION_AUDIT.json", paired)
    lines = [
        f"# PanoRef selector language representation benchmark: `{run_id}`",
        "",
        f"Plan hash: `{plan['plan_sha256']}`; source commit: `{plan['source_commit']}`.",
        "The three arms were run serially in the same chunk order with the same Qwen model, schema, opaque IDs, and generation settings. Tool/code/diagnostic source items were unchanged.",
        "",
        "| Arm | Calls | Success | Input p50 / mean / total | Token reduction vs A | TTFT p50 / p95 / mean | Prompt eval | Decode p50 | Wall p50 | Output tokens |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    base_input = summary["arms"]["A_CANONICAL_ENGLISH"]["input_tokens"]["p50"]
    base_total = summary["arms"]["A_CANONICAL_ENGLISH"]["input_tokens"]["total"]
    for arm, data in summary["arms"].items():
        inp, ttft, dec, wall, out = data["input_tokens"], data["ttft_ms"], data["decode_ms_client_observed"], data["total_wall_ms"], data["output_tokens"]
        red = None
        if arm != "A_CANONICAL_ENGLISH" and data["input_tokens"]["total"] is not None and base_total:
            red = 1 - data["input_tokens"]["total"] / base_total
        lines.append(f"| {arm} | {data['calls']} | {data['succeeded']} | {inp['p50']} / {inp['mean']} / {inp['total']} | {red} | {ttft['p50']} / {ttft['p95']} / {ttft['mean']} | {data['prompt_eval_ms_backend']['p50']} | {dec['p50']} | {wall['p50']} | {out['total']} |")
    lines.extend(["", "## Paired semantic and latency audit", ""])
    for arm, data in paired.items():
        lines.append(f"- {arm}: decision agreement `{data['decision_matches']}/{data['decision_compared']}`; token-reduction/TTFT-reduction correlation `{data['correlation_token_reduction_vs_ttft_reduction']}`.")
    lines.extend(["", "Prompt-eval telemetry is reported only when the backend exposed it; client-observed TTFT is measured from request start to the first streamed content token and is not inferred from total wall time.", ""])
    (run_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"summary: {run_dir / 'SUMMARY.json'}")
    print(f"report: {run_dir / 'REPORT.md'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("freeze-plan")
    generate_parser = sub.add_parser("generate")
    generate_parser.add_argument("--scope", choices=("gate", "full"), required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--scope", choices=("gate", "full"), required=True)
    run_parser.add_argument("--run-id", required=True)
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.command == "freeze-plan":
        freeze_plan()
    elif args.command == "generate":
        generate(args.scope)
    elif args.command == "run":
        asyncio.run(run_async(args.scope, args.run_id))
    elif args.command == "report":
        report(args.run_id)


if __name__ == "__main__":
    main()
