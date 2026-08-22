"""Phase 3.2 exact canonicalizer-stall and role-switch experiments.

This tool is intentionally experiment-only. It does not alter ORCHID runtime
configuration or database state. Inputs are reconstructed from the frozen
FreetoShop replay and persisted as hashes plus a bounded exact request fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.compaction import Event, SourceItem, expand_source_items
from memory_gateway.pipeline_adapters import (
    _chunk_events,
    _event_payload,
    canonicalizer_response_format_for_batch,
    selector_response_format_for_chunk,
)
from memory_gateway.structured_client import OpenAICompatStructuredClient
from memory_gateway.context import estimate_tokens


DEFAULT_REPLAY = ROOT / (
    "artifacts/agent_benchmarks/freetoshop_operability_hardening/"
    "frozen_freetoshop_replay/events.jsonl"
)
DEFAULT_OUT = ROOT / "artifacts/agent_benchmarks/freetoshop_pipeline_ablation"
MODEL = "qwen3.5-4b@q6_k"
ENDPOINT = "http://127.0.0.1:1234/v1"
TARGET_TOKENS = 12_000
TIMEOUT_SECONDS = 180.0
CANONICALIZER_SYSTEM = (
    "You canonicalize authoritative events for durable memory. Return JSON only with "
    '{"canonical_text":"...","cited_source_refs":["source-item-id"]}. '
    "cited_source_refs is optional and may cite a subset of supplied source items, "
    "but cited IDs must be the exact supplied item IDs in source order. For a source "
    "span, cite its complete ID including the ::span:: suffix; never cite its "
    "parent_event_id. Do not invent facts or IDs."
)
SELECTOR_SYSTEM = (
    "You select durable-memory source items. Return JSON only with exactly "
    '{"selected_event_ids":["source-item-id"]}. Select only whole-event or '
    "source-span IDs present in the input; do not summarize or rewrite items."
)
GENERATION_SETTINGS = {
    "temperature": 0,
    "top_p": 1,
    "stream": False,
    "reasoning_effort": "none",
}


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_events(path: Path) -> list[Event]:
    result: list[Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        result.append(
            Event(
                id=row["event_id"],
                sequence=int(row["sequence"]),
                content=row["content"],
                content_hash=row["content_hash"],
                event_type=row["event_type"],
                role=row.get("role"),
            )
        )
    return result


def build_batches(events: list[Event], target_tokens: int) -> list[list[SourceItem]]:
    items = list(
        expand_source_items(
            events,
            selector_budget_tokens=1_200,
            safety_margin=0.8,
        )
    )
    return _chunk_events(items, target_tokens=target_tokens)


def client(*, system_prompt: str, prompt_version: str) -> OpenAICompatStructuredClient:
    return OpenAICompatStructuredClient(
        endpoint=ENDPOINT,
        model=MODEL,
        prompt_version=prompt_version,
        system_prompt=system_prompt,
        generation_settings=dict(GENERATION_SETTINGS),
        timeout=TIMEOUT_SECONDS,
    )


def _safe_error(error: Any) -> str | None:
    if error is None:
        return None
    return str(error)[:1000]


async def call_canonicalizer(
    items: list[SourceItem],
    *,
    label: str,
    prompt_version: str = "canonicalizer-v1",
) -> dict[str, Any]:
    target_client = client(system_prompt=CANONICALIZER_SYSTEM, prompt_version=prompt_version)
    ids = tuple(item.id for item in items)
    payload = {"events": [_event_payload(item) for item in items]}
    target_client.response_format = canonicalizer_response_format_for_batch(ids)
    started = time.perf_counter()
    status = "SUCCEEDED"
    error: str | None = None
    response: dict[str, Any] | None = None
    try:
        response = await target_client.complete_json(payload)
        if not isinstance(response.get("canonical_text"), str):
            raise ValueError("canonicalizer response missing canonical_text")
        cited = response.get("cited_source_refs", [])
        if not isinstance(cited, list) or not all(isinstance(item, str) for item in cited):
            raise ValueError("canonicalizer cited_source_refs is not a string array")
        if not set(cited).issubset(set(ids)):
            raise ValueError("canonicalizer returned an unknown source ID")
    except Exception as exc:  # provider-specific boundary
        status = "FAILED"
        error = _safe_error(exc)
    wall_ms = (time.perf_counter() - started) * 1000
    telemetry = dict(target_client.last_telemetry or {})
    return {
        "condition": label,
        "status": status,
        "error": error,
        "timeout": bool(telemetry.get("error_category") == "timeout")
        or "timed out" in (error or "").lower(),
        "wall_ms": wall_ms,
        "input_tokens": telemetry.get("input_tokens"),
        "output_tokens": telemetry.get("output_tokens"),
        "ttft_ms": telemetry.get("ttft_ms"),
        "finish_reason": telemetry.get("finish_reason"),
        "failure_phase": telemetry.get("failure_phase"),
        "request_profile": telemetry.get("request_profile"),
        "input_hash": sha256_json(payload),
        "source_ref_count": len(ids),
        "source_refs": list(ids),
        "source_tokens": sum(
            estimate_tokens(json.dumps(_event_payload(item), ensure_ascii=False)) for item in items
        ),
        "prompt_version": prompt_version,
        "response_hash": sha256_json(response) if response is not None else None,
    }


async def call_selector(items: list[SourceItem], *, label: str) -> dict[str, Any]:
    target_client = client(system_prompt=SELECTOR_SYSTEM, prompt_version="selector-v1")
    ids = tuple(item.id for item in items)
    payload = {
        "events": [_event_payload(item) for item in items],
        "event_ids_in_order": list(ids),
        "source_ids_in_order": list(ids),
    }
    target_client.response_format = selector_response_format_for_chunk(ids)
    started = time.perf_counter()
    status = "SUCCEEDED"
    error: str | None = None
    response: dict[str, Any] | None = None
    try:
        response = await target_client.complete_json(payload)
        selected = response.get("selected_event_ids")
        if not isinstance(selected, list) or not set(selected).issubset(set(ids)):
            raise ValueError("selector returned an invalid source ID")
    except Exception as exc:
        status = "FAILED"
        error = _safe_error(exc)
    return {
        "condition": label,
        "status": status,
        "error": error,
        "timeout": "timed out" in (error or "").lower(),
        "wall_ms": (time.perf_counter() - started) * 1000,
        "input_tokens": (target_client.last_telemetry or {}).get("input_tokens"),
        "output_tokens": (target_client.last_telemetry or {}).get("output_tokens"),
        "ttft_ms": (target_client.last_telemetry or {}).get("ttft_ms"),
        "finish_reason": (target_client.last_telemetry or {}).get("finish_reason"),
        "request_profile": (target_client.last_telemetry or {}).get("request_profile"),
        "input_hash": sha256_json(payload),
        "source_ref_count": len(ids),
        "source_refs": list(ids),
        "selected_ids": (response or {}).get("selected_event_ids") if response else None,
    }


def fixture_for(batch: list[SourceItem]) -> dict[str, Any]:
    ids = tuple(item.id for item in batch)
    payload = {"events": [_event_payload(item) for item in batch]}
    schema = canonicalizer_response_format_for_batch(ids)
    return {
        "model": MODEL,
        "endpoint": ENDPOINT,
        "timeout_seconds": TIMEOUT_SECONDS,
        "generation_settings": GENERATION_SETTINGS,
        "system_prompt": CANONICALIZER_SYSTEM,
        "input_payload": payload,
        "response_format": schema,
        "input_payload_sha256": sha256_json(payload),
        "response_format_sha256": sha256_json(schema),
        "source_refs": list(ids),
    }


async def run_stall(args: argparse.Namespace) -> dict[str, Any]:
    events = load_events(Path(args.replay))
    batches = build_batches(events, TARGET_TOKENS)
    if args.batch_index >= len(batches):
        raise SystemExit(f"batch index {args.batch_index} outside {len(batches)} batches")
    target_batch = batches[args.batch_index]
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "request_fixture.json").write_text(
        json.dumps(fixture_for(target_batch), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    conditions: list[dict[str, Any]] = []
    for trial in range(args.trials):
        conditions.append({"trial": trial, **(await call_canonicalizer(target_batch, label="A_idle"))})
        if args.include_context_conditions:
            preceding = batches[0]
            conditions.append({
                "trial": trial,
                "precondition": "selector_request",
                **(await call_selector(preceding, label="B_after_selector")),
                "follow_up": await call_canonicalizer(target_batch, label="B_after_selector_canonicalizer"),
            })
            conditions.append({
                "trial": trial,
                "precondition": "canonicalizer_request",
                "warmup": await call_canonicalizer(preceding, label="C_after_canonicalizer_warmup"),
                "follow_up": await call_canonicalizer(target_batch, label="C_after_canonicalizer"),
            })
            alternation: list[dict[str, Any]] = []
            for index in range(3):
                alternation.append(await call_selector(preceding, label=f"D_alternation_selector_{index}"))
                alternation.append(await call_canonicalizer(target_batch, label=f"D_alternation_canonicalizer_{index}"))
            conditions.append({"trial": trial, "precondition": "alternation", "requests": alternation})
    if args.concurrency:
        concurrent = await asyncio.gather(
            *(call_canonicalizer(target_batch, label=f"E_concurrent_{index}") for index in range(args.concurrency))
        )
        conditions.append({"trial": 0, "precondition": "concurrent_requests", "requests": concurrent})

    payload = {
        "replay": str(Path(args.replay).resolve()),
        "replay_sha256": hashlib.sha256(Path(args.replay).read_bytes()).hexdigest(),
        "events_used": len(events),
        "expanded_source_items": sum(len(batch) for batch in batches),
        "batch_index": args.batch_index,
        "batch_count": len(batches),
        "target_tokens": TARGET_TOKENS,
        "source_tokens": sum(
            estimate_tokens(json.dumps(_event_payload(item), ensure_ascii=False)) for item in target_batch
        ),
        "trial_count": args.trials,
        "conditions": conditions,
    }
    with (out / "trials.jsonl").open("w", encoding="utf-8") as handle:
        for row in conditions:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out / "SUMMARY.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def _run_sync(coro: Any) -> Any:
    return asyncio.run(coro)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", default=str(DEFAULT_REPLAY))
    parser.add_argument("--output", default=str(DEFAULT_OUT / "stall_analysis"))
    parser.add_argument("--batch-index", type=int, default=2)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--include-context-conditions", action="store_true")
    parser.add_argument("--concurrency", type=int, default=0)
    args = parser.parse_args()
    result = _run_sync(run_stall(args))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
