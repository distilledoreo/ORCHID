"""Measure shared-client role switching versus persistent role-affine clients."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.compaction import Event, expand_source_items
from memory_gateway.context import estimate_tokens
from memory_gateway.pipeline_adapters import (
    _chunk_events,
    _event_payload,
    canonicalizer_response_format_for_batch,
    selector_response_format_for_chunk,
)
from memory_gateway.structured_client import OpenAICompatStructuredClient

from phase3_2_stall_analysis import (
    CANONICALIZER_SYSTEM,
    ENDPOINT,
    GENERATION_SETTINGS,
    MODEL,
    SELECTOR_SYSTEM,
    load_events,
)


def make_client(system_prompt: str, prompt_version: str) -> OpenAICompatStructuredClient:
    return OpenAICompatStructuredClient(
        endpoint=ENDPOINT,
        model=MODEL,
        prompt_version=prompt_version,
        system_prompt=system_prompt,
        generation_settings=dict(GENERATION_SETTINGS),
        timeout=180.0,
    )


def metrics(client: OpenAICompatStructuredClient, *, started: float, role: str) -> dict[str, Any]:
    telemetry = dict(client.last_telemetry or {})
    profile = telemetry.get("request_profile") or {}
    return {
        "role": role,
        "status": telemetry.get("status"),
        "wall_ms": (time.perf_counter() - started) * 1000,
        "provider_wall_ms": telemetry.get("wall_ms"),
        "ttft_ms": telemetry.get("ttft_ms"),
        "input_tokens": telemetry.get("input_tokens"),
        "output_tokens": telemetry.get("output_tokens"),
        "finish_reason": telemetry.get("finish_reason"),
        "error": telemetry.get("error"),
        "error_category": telemetry.get("error_category"),
        "input_payload_chars": profile.get("input_payload_chars"),
        "response_schema_chars": profile.get("response_schema_chars"),
        "input_hash": profile.get("input_payload_hash"),
        "response_schema_hash": profile.get("response_schema_hash"),
    }


async def call_role(
    client: OpenAICompatStructuredClient,
    *,
    role: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    system_prompt: str,
    prompt_version: str,
) -> dict[str, Any]:
    client.system_prompt = system_prompt
    client.prompt_version = prompt_version
    client.response_format = schema
    started = time.perf_counter()
    try:
        response = await client.complete_json(payload)
        status = "SUCCEEDED"
        error = None
        if role == "selector" and not isinstance(response.get("selected_event_ids"), list):
            status = "FAILED"
            error = "selector response missing selected_event_ids"
        if role == "canonicalizer" and not isinstance(response.get("canonical_text"), str):
            status = "FAILED"
            error = "canonicalizer response missing canonical_text"
    except Exception as exc:
        response = None
        status = "FAILED"
        error = str(exc)[:1000]
    row = metrics(client, started=started, role=role)
    row.update({"status": status, "error": error or row.get("error"), "response_hash": hashlib.sha256(json.dumps(response or {}, sort_keys=True).encode()).hexdigest()})
    return row


async def run_config(
    *,
    name: str,
    selector_payload: dict[str, Any],
    selector_schema: dict[str, Any],
    canonicalizer_payload: dict[str, Any],
    canonicalizer_schema: dict[str, Any],
    iterations: int,
    shared: bool,
) -> dict[str, Any]:
    shared_client = make_client("", "role-switch-v1") if shared else None
    selector_client = shared_client or make_client(SELECTOR_SYSTEM, "selector-v1")
    canonicalizer_client = shared_client or make_client(CANONICALIZER_SYSTEM, "canonicalizer-v1")
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index in range(iterations):
        rows.append({"iteration": index, **(await call_role(selector_client, role="selector", payload=selector_payload, schema=selector_schema, system_prompt=SELECTOR_SYSTEM, prompt_version="selector-v1"))})
        rows.append({"iteration": index, **(await call_role(canonicalizer_client, role="canonicalizer", payload=canonicalizer_payload, schema=canonicalizer_schema, system_prompt=CANONICALIZER_SYSTEM, prompt_version="canonicalizer-v1"))})
    return {
        "config": name,
        "shared_client": shared,
        "iterations": iterations,
        "wall_ms": (time.perf_counter() - started) * 1000,
        "rows": rows,
        "failures": sum(row.get("status") != "SUCCEEDED" for row in rows),
        "timeouts": sum(row.get("error_category") == "timeout" for row in rows),
        "ttft_observed_count": sum(row.get("ttft_ms") is not None for row in rows),
        "mean_request_ms": sum(row.get("wall_ms") or 0 for row in rows) / max(len(rows), 1),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    events = load_events(Path(args.replay))
    items = list(expand_source_items(events, selector_budget_tokens=1_200, safety_margin=0.8))
    chunks = _chunk_events(items, target_tokens=4_000)
    chunk = chunks[0]
    ids = [item.id for item in chunk]
    selector_payload = {
        "events": [_event_payload(item) for item in chunk],
        "event_ids_in_order": ids,
        "source_ids_in_order": ids,
    }
    canonicalizer_payload = {"events": [_event_payload(item) for item in chunk]}
    result = {
        "replay": str(Path(args.replay).resolve()),
        "replay_sha256": hashlib.sha256(Path(args.replay).read_bytes()).hexdigest(),
        "model": MODEL,
        "endpoint": ENDPOINT,
        "source_refs": ids,
        "source_tokens": sum(
            estimate_tokens(json.dumps(_event_payload(item), ensure_ascii=False))
            for item in chunk
        ),
        "configurations": [
            await run_config(
                name="CONFIG_1_SHARED_CLIENT_ROLE_SWITCH",
                selector_payload=selector_payload,
                selector_schema=selector_response_format_for_chunk(ids),
                canonicalizer_payload=canonicalizer_payload,
                canonicalizer_schema=canonicalizer_response_format_for_batch(ids),
                iterations=args.iterations,
                shared=True,
            ),
            await run_config(
                name="CONFIG_2_TWO_PERSISTENT_ROLE_CLIENTS",
                selector_payload=selector_payload,
                selector_schema=selector_response_format_for_chunk(ids),
                canonicalizer_payload=canonicalizer_payload,
                canonicalizer_schema=canonicalizer_response_format_for_batch(ids),
                iterations=args.iterations,
                shared=False,
            ),
        ],
        "ttft_note": "Non-streaming requests were used, so the client has no first-token signal; ttft comparisons are unavailable.",
        "prefix_cache_note": "The OpenAI-compatible API and LM Studio CLI exposed no KV/prefix-cache hit metric in this run.",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rows_path = output.with_name("results.jsonl")
    with rows_path.open("w", encoding="utf-8") as handle:
        for config in result["configurations"]:
            for row in config["rows"]:
                handle.write(json.dumps({"config": config["config"], **row}, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", default=str(Path(__file__).resolve().parents[1] / "artifacts/agent_benchmarks/freetoshop_operability_hardening/frozen_freetoshop_replay/events.jsonl"))
    parser.add_argument("--output", default=str(Path(__file__).resolve().parents[1] / "artifacts/agent_benchmarks/freetoshop_pipeline_ablation/role_switch/results.json"))
    parser.add_argument("--iterations", type=int, default=2)
    args = parser.parse_args()
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
