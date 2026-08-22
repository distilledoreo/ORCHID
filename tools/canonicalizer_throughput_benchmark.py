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

from memory_gateway.compaction import Event, SourceItem, expand_source_items
from memory_gateway.context import estimate_tokens
from memory_gateway.pipeline_adapters import (
    OpenAICompatCanonicalizerEngine,
    canonicalizer_response_format_for_batch,
    _chunk_events,
    _event_payload,
)
from memory_gateway.pipeline import CanonicalizerBatchError
from memory_gateway.structured_client import OpenAICompatStructuredClient


DEFAULT_REPLAY = Path(
    "artifacts/agent_benchmarks/freetoshop_operability_hardening/"
    "frozen_freetoshop_replay/events.jsonl"
)
DEFAULT_OUTPUT = Path(
    "artifacts/agent_benchmarks/freetoshop_canonicalizer_throughput"
)
DEFAULT_TARGETS = (2_000, 4_000, 8_000, 12_000, 16_000)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_events(path: Path) -> list[Event]:
    events: list[Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        events.append(
            Event(
                id=row["event_id"],
                sequence=int(row["sequence"]),
                content=row["content"],
                content_hash=row["content_hash"],
                event_type=row["event_type"],
                role=row.get("role"),
            )
        )
    return events


def _estimated_source_tokens(items: list[SourceItem]) -> int:
    return sum(
        estimate_tokens(json.dumps(_event_payload(item), ensure_ascii=False))
        for item in items
    )


def _prompt_breakdown(
    *,
    client: OpenAICompatStructuredClient,
    batch: list[SourceItem],
) -> dict[str, int | str]:
    source_json = json.dumps(
        {"events": [_event_payload(event) for event in batch]},
        ensure_ascii=False,
        sort_keys=True,
    )
    wrapper = "INPUT_JSON\n--- BEGIN INPUT ---\n\n--- END INPUT ---"
    response_format = canonicalizer_response_format_for_batch(
        [event.id for event in batch]
    )
    serialized_schema = json.dumps(response_format, ensure_ascii=False, sort_keys=True)
    return {
        "source_payload_estimated_tokens": estimate_tokens(source_json),
        "system_prompt_estimated_tokens": estimate_tokens(client.system_prompt),
        "response_schema_estimated_tokens": estimate_tokens(
            serialized_schema
        ),
        "wrapper_estimated_tokens": estimate_tokens(wrapper),
        "source_payload_chars": len(source_json),
        "prompt_hash": _sha256(
            client.system_prompt + "\n" + source_json + "\n" + json.dumps(
                response_format, ensure_ascii=False, sort_keys=True
            )
        ),
    }


def _make_engine(
    *,
    endpoint: str,
    model: str,
    timeout: float,
    max_output_tokens: int | None,
    batch_target_tokens: int,
) -> OpenAICompatCanonicalizerEngine:
    settings: dict[str, Any] = {
        "temperature": 0,
        "top_p": 1,
        "stream": False,
        "reasoning_effort": "none",
    }
    if max_output_tokens is not None:
        settings["max_tokens"] = max_output_tokens
    client = OpenAICompatStructuredClient(
        endpoint=endpoint,
        model=model,
        prompt_version="canonicalizer-v1",
        system_prompt=(
            "You canonicalize authoritative events for durable memory. Return JSON only with "
            '{"canonical_text":"...","cited_source_refs":["source-item-id"]}. '
            "cited_source_refs is optional and may cite a subset of supplied source items, "
            "but cited IDs must be the exact supplied item IDs in source order. For a source "
            "span, cite its complete ID including the ::span:: suffix; never cite its "
            "parent_event_id. Do not invent facts or IDs."
        ),
        generation_settings=settings,
        timeout=timeout,
    )
    return OpenAICompatCanonicalizerEngine(
        client,
        batch_target_tokens=batch_target_tokens,
    )


async def _run_one(
    *,
    events: list[Event],
    target: int,
    endpoint: str,
    model: str,
    timeout: float,
    max_output_tokens: int | None,
    full_window: bool,
) -> dict[str, Any]:
    source_items = list(
        expand_source_items(
            events,
            selector_budget_tokens=1_200,
            safety_margin=0.8,
        )
    )
    batches = _chunk_events(source_items, target_tokens=target)
    if not full_window:
        batches = batches[:1]
    selected = [item for batch in batches for item in batch]
    engine = _make_engine(
        endpoint=endpoint,
        model=model,
        timeout=timeout,
        max_output_tokens=max_output_tokens,
        batch_target_tokens=target,
    )
    started = time.perf_counter()
    status = "SUCCEEDED"
    error: str | None = None
    try:
        await engine.canonicalize(events=selected)
    except CanonicalizerBatchError as exc:
        status = "FAILED"
        error = str(exc.error)
    except Exception as exc:  # pragma: no cover - provider-specific boundary
        status = "FAILED"
        error = str(exc)
    wall_ms = (time.perf_counter() - started) * 1000

    batch_records: list[dict[str, Any]] = []
    for index, batch in enumerate(batches):
        telemetry = (
            dict(engine.batch_telemetry[index])
            if index < len(engine.batch_telemetry)
            else {}
        )
        batch_wall_ms = telemetry.get("batch_wall_ms") or telemetry.get("wall_ms")
        prompt = _prompt_breakdown(client=engine.client, batch=batch)
        record: dict[str, Any] = {
            "batch_index": index,
            "target_tokens": target,
            "source_event_count": len(batch),
            "source_refs": [item.id for item in batch],
            "estimated_input_tokens": _estimated_source_tokens(batch),
            "wall_ms": batch_wall_ms,
            "input_tokens": telemetry.get("input_tokens"),
            "output_tokens": telemetry.get("output_tokens"),
            "ttft_ms": telemetry.get("ttft_ms"),
            "finish_reason": telemetry.get("finish_reason"),
            "status": telemetry.get("status"),
            "error": telemetry.get("error") or telemetry.get("failure_reason"),
            "timeout": "timed out" in str(
                telemetry.get("error") or telemetry.get("failure_reason") or ""
            ).lower(),
            "structured_output_valid": telemetry.get("status") == "SUCCEEDED",
            "request_profile": telemetry.get("request_profile") or {},
            **prompt,
        }
        if batch_wall_ms:
            record["source_tokens_per_second"] = (
                record["estimated_input_tokens"] / (float(batch_wall_ms) / 1000)
            )
        batch_records.append(record)

    return {
        "target_tokens": target,
        "model": model,
        "endpoint": endpoint,
        "timeout_seconds": timeout,
        "max_output_tokens": max_output_tokens,
        "full_window": full_window,
        "source_event_count": len(selected),
        "source_tokens": _estimated_source_tokens(selected),
        "batch_count": len(batches),
        "wall_ms": wall_ms,
        "source_tokens_per_second": (
            _estimated_source_tokens(selected) / max(wall_ms / 1000, 0.000001)
        ),
        "status": status,
        "error": error,
        "batches": batch_records,
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    events = _load_events(Path(args.replay))
    if args.event_limit:
        events = events[: args.event_limit]
    targets = tuple(args.targets or DEFAULT_TARGETS)
    results: list[dict[str, Any]] = []
    for target in targets:
        result = await _run_one(
            events=events,
            target=target,
            endpoint=args.endpoint,
            model=args.model,
            timeout=args.timeout,
            max_output_tokens=args.max_output_tokens,
            full_window=args.full_window,
        )
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return {
        "replay": str(Path(args.replay).resolve()),
        "replay_sha256": _sha256(Path(args.replay).read_text(encoding="utf-8")),
        "targets": list(targets),
        "events_used": len(events),
        "endpoint": args.endpoint,
        "model": args.model,
        "timeout_seconds": args.timeout,
        "max_output_tokens": args.max_output_tokens,
        "full_window": args.full_window,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", default=str(DEFAULT_REPLAY))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT / "batch_sweep" / "results.json"))
    parser.add_argument("--endpoint", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--model", default="qwen3.5-4b@q6_k")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--max-output-tokens", type=int, default=None)
    parser.add_argument(
        "--event-limit",
        type=int,
        default=0,
        help="Limit replay events; zero uses the complete frozen trace.",
    )
    parser.add_argument("--targets", type=int, nargs="*", default=None)
    parser.add_argument("--full-window", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
