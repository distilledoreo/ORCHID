"""Run Phase 3.2 semantic-pipeline ablations on the frozen FreetoShop trace.

This is an experiment harness. It does not alter the production scheduler,
ACTIVE semantics, retrieval, or database. Each arm writes its own JSONL and
summary under the Phase 3.2 artifact directory.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.compaction import (
    Capsule,
    CompactionResult,
    Event,
    SourceItem,
    compute_input_hash,
    expand_source_items,
    source_item_parent_event_id,
    source_item_payload,
)
from memory_gateway.context import estimate_tokens
from memory_gateway.db import SQLiteStore
from memory_gateway.openai_adapter import ModelProtocolError, ModelTransportError
from memory_gateway.pipeline import (
    CanonicalizationBatchResult,
    CanonicalizationResult,
    LosslessCompactionEngine,
    LosslessPacket,
    SelectionResult,
    build_canonicalization_result,
    build_lossless_packet,
)
from memory_gateway.pipeline_adapters import (
    CONSOLIDATOR_RESPONSE_FORMAT,
    OpenAICompatCanonicalizerEngine,
    OpenAICompatConsolidatorEngine,
    OpenAICompatSelectorEngine,
    _chunk_events,
    _event_payload,
)
from memory_gateway.structured_client import OpenAICompatStructuredClient


DEFAULT_REPLAY = ROOT / (
    "artifacts/agent_benchmarks/freetoshop_operability_hardening/"
    "frozen_freetoshop_replay/events.jsonl"
)
DEFAULT_OUT = ROOT / "artifacts/agent_benchmarks/freetoshop_pipeline_ablation"
LOCAL_ENDPOINT = "http://127.0.0.1:1234/v1"
LOCAL_MODEL = "qwen3.5-4b@q6_k"
SOLAR_ENDPOINT = "https://openrouter.ai/api"
SOLAR_MODEL = "upstage/solar-pro4"
LOCAL_TIMEOUT = 180.0
SOLAR_TIMEOUT = 180.0
SELECTOR_TARGET = 1_200
CANONICALIZER_TARGET = 12_000
DIRECT_TARGET = 12_000
GENERATION = {
    "temperature": 0,
    "top_p": 1,
    "stream": False,
    "reasoning_effort": "none",
}
CANONICALIZER_SYSTEM = (
    "You canonicalize authoritative events for durable memory. Return JSON only with "
    '{"canonical_text":"...","cited_source_refs":["source-item-id"]}. '
    "cited_source_refs is optional and may cite a subset of supplied source items, "
    "but cited IDs must be the exact supplied item IDs in source order. For a source "
    "span, cite its complete ID including the ::span:: suffix; never cite its "
    "parent_event_id. Do not invent facts or IDs."
)
CONSOLIDATOR_SYSTEM = (
    "You render a durable-memory capsule from an already-approved lossless packet. "
    "Return JSON only with {'content':'...','evidence_event_ids':['source-item-id'], "
    "'retire':[{'content':'...','memory_type':'finding','importance':0.0, "
    "'evidence_event_ids':['source-item-id']}]}. The optional retire array "
    "must be [] when there is no durable cold memory to store. Preserve conditions, "
    "causal relationships, current state, negative knowledge, and uncertainty. "
    "Cite only authoritative source-item IDs from the input. Retire only durable "
    "semantic memories, not raw excerpts."
)
DIRECT_SELECTOR_CONSOLIDATOR_SYSTEM = (
    "You are an experimental semantic compactor. Normalize and consolidate the "
    "authoritative source events directly into one durable capsule without a separate "
    "canonicalizer. Preserve current state, conditions, causal relationships, negative "
    "knowledge, and uncertainty. Do not invent facts. Return JSON only with "
    "{'content':'...','evidence_event_ids':['source-item-id']}. Cite only exact IDs "
    "from the supplied source_ids_in_order."
)
DIRECT_RAW_CONSOLIDATOR_SYSTEM = (
    "You are an experimental bounded raw-history compactor. Read the supplied bounded "
    "authoritative events directly and produce a durable capsule. Filter disposable "
    "tool noise while preserving durable implementation state, current decisions, "
    "unresolved blockers, and historical qualifiers. Do not invent facts. Return JSON "
    "only with {'content':'...','evidence_event_ids':['source-item-id']}. Cite only "
    "exact IDs from source_ids_in_order."
)


def digest(value: Any) -> str:
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


def source_items(events: list[Event]) -> list[SourceItem]:
    return list(expand_source_items(events, selector_budget_tokens=SELECTOR_TARGET, safety_margin=0.8))


def make_client(
    *,
    endpoint: str,
    model: str,
    api_key: str | None,
    prompt_version: str,
    system_prompt: str,
    timeout: float,
) -> OpenAICompatStructuredClient:
    return OpenAICompatStructuredClient(
        endpoint=endpoint,
        model=model,
        prompt_version=prompt_version,
        system_prompt=system_prompt,
        generation_settings=dict(GENERATION),
        timeout=timeout,
        api_key=api_key,
    )


def client_metrics(client: Any) -> dict[str, Any]:
    telemetry = dict(getattr(client, "last_telemetry", None) or {})
    return {
        "status": telemetry.get("status"),
        "input_tokens": telemetry.get("input_tokens"),
        "output_tokens": telemetry.get("output_tokens"),
        "reasoning_tokens": telemetry.get("reasoning_tokens"),
        "wall_ms": telemetry.get("wall_ms"),
        "ttft_ms": telemetry.get("ttft_ms"),
        "finish_reason": telemetry.get("finish_reason"),
        "error": telemetry.get("error"),
        "error_category": telemetry.get("error_category"),
        "request_profile": telemetry.get("request_profile"),
    }


def aggregate_client_metrics(clients: Iterable[Any]) -> dict[str, Any]:
    rows = [client_metrics(client) for client in clients]
    numeric = ("input_tokens", "output_tokens", "reasoning_tokens", "wall_ms")
    return {
        "calls": len(rows),
        **{key: sum(float(row.get(key) or 0) for row in rows) for key in numeric},
        "timeouts": sum(row.get("error_category") == "timeout" for row in rows),
        "failures": sum(row.get("status") == "FAILED" for row in rows),
        "ttft_observed_count": sum(row.get("ttft_ms") is not None for row in rows),
    }


def selected_items_from_result(items: list[SourceItem], selection: SelectionResult) -> list[SourceItem]:
    selected = set(selection.selected_event_ids)
    return [item for item in items if item.id in selected]


def source_token_count(items: Iterable[SourceItem]) -> int:
    return sum(estimate_tokens(json.dumps(source_item_payload(item), ensure_ascii=False)) for item in items)


def source_event_hash(events: list[Event]) -> str:
    return digest([(event.id, event.content_hash) for event in events])


def make_capsule(content: str, *, thread_id: str, end_id: str | None, index: int) -> Capsule:
    capsule_hash = digest(content)
    return Capsule(
        id=f"ablation-cap-{index}",
        thread_id=thread_id,
        content=content,
        capsule_hash=capsule_hash,
        covered_end_event_id=end_id,
    )


def parse_direct_response(
    response: dict[str, Any],
    *,
    allowed_ids: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    if set(response) - {"content", "evidence_event_ids", "retire"}:
        raise ModelProtocolError("direct consolidator response contained unknown keys")
    content = response.get("content")
    evidence = response.get("evidence_event_ids")
    if not isinstance(content, str) or not content.strip():
        raise ModelProtocolError("direct consolidator content was empty")
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise ModelProtocolError("direct consolidator evidence_event_ids was invalid")
    if not set(evidence).issubset(set(allowed_ids)):
        raise ModelProtocolError("direct consolidator returned an unknown source ID")
    if len(set(evidence)) != len(evidence):
        raise ModelProtocolError("direct consolidator returned duplicate source IDs")
    return content, tuple(evidence)


@dataclass
class ArmResult:
    arm: str
    status: str
    error: str | None
    result: CompactionResult | None
    source_tokens: int
    local_clients: list[Any]
    solar_clients: list[Any]
    selected_items: list[SourceItem]
    source_items: list[SourceItem]
    stage: dict[str, Any]
    promotions: int
    promotion_detail: dict[str, Any]
    semantic_quality: dict[str, Any]


async def run_current_arm(events: list[Event], *, arm: str = "ARM_A_CURRENT") -> ArmResult:
    key = os.environ.get("OPENROUTER_API_KEY")
    selector_client = make_client(
        endpoint=LOCAL_ENDPOINT,
        model=LOCAL_MODEL,
        api_key=None,
        prompt_version="selector-v1",
        system_prompt=(
            "You select durable-memory source items. Return JSON only with exactly "
            '{"selected_event_ids":["source-item-id"]}. Select only whole-event or '
            "source-span IDs present in the input; do not summarize or rewrite items."
        ),
        timeout=LOCAL_TIMEOUT,
    )
    canonicalizer_client = make_client(
        endpoint=LOCAL_ENDPOINT,
        model=LOCAL_MODEL,
        api_key=None,
        prompt_version="canonicalizer-v1",
        system_prompt=CANONICALIZER_SYSTEM,
        timeout=LOCAL_TIMEOUT,
    )
    solar_client = make_client(
        endpoint=SOLAR_ENDPOINT,
        model=SOLAR_MODEL,
        api_key=key,
        prompt_version="consolidator-v1",
        system_prompt=CONSOLIDATOR_SYSTEM,
        timeout=SOLAR_TIMEOUT,
    )
    selector = OpenAICompatSelectorEngine(
        selector_client,
        chunk_target_tokens=SELECTOR_TARGET,
        selector_context_tokens=32_768,
    )
    canonicalizer = OpenAICompatCanonicalizerEngine(
        canonicalizer_client,
        batch_target_tokens=CANONICALIZER_TARGET,
    )
    consolidator = OpenAICompatConsolidatorEngine(solar_client)
    engine = LosslessCompactionEngine(selector, canonicalizer, consolidator)
    started = time.perf_counter()
    result: CompactionResult | None = None
    error: str | None = None
    try:
        result = await engine.compact(
            base_capsule=None,
            events=events,
            snapshot_end_event_id=events[-1].id if events else None,
        )
        status = "SUCCEEDED"
    except Exception as exc:
        status = "FAILED"
        error = str(exc)[:2000]
    all_items = source_items(events)
    selected = []
    if selector.chunk_telemetry:
        selected_ids = {
            selected_id
            for row in selector.chunk_telemetry
            for selected_id in row.get("selected_event_ids", ())
        }
        # The current adapter's chunk telemetry predates selected IDs; derive
        # from the result when available and otherwise leave this diagnostic.
        if result is not None:
            selected = all_items
    if result is not None:
        selected = [item for item in all_items if item.id in set(result.evidence_source_ids)]
    return ArmResult(
        arm=arm,
        status=status,
        error=error,
        result=result,
        source_tokens=sum(estimate_tokens(event.content) for event in events),
        local_clients=[selector_client, canonicalizer_client],
        solar_clients=[solar_client],
        selected_items=selected,
        source_items=all_items,
        stage={
            "wall_ms": (time.perf_counter() - started) * 1000,
            "engine_telemetry": engine.last_telemetry,
            "selector": {
                "calls": len(selector.chunk_telemetry),
                "wall_ms": sum(float(row.get("wall_ms") or 0) for row in selector.chunk_telemetry),
            },
            "canonicalizer": {
                "calls": len(canonicalizer.batch_telemetry),
                "wall_ms": sum(float(row.get("batch_wall_ms") or 0) for row in canonicalizer.batch_telemetry),
            },
        },
        promotions=0,
        promotion_detail={},
        semantic_quality={},
    )


async def run_selector(events: list[Event]) -> tuple[SelectionResult | None, list[SourceItem], list[Any], dict[str, Any], str | None]:
    selector_client = make_client(
        endpoint=LOCAL_ENDPOINT,
        model=LOCAL_MODEL,
        api_key=None,
        prompt_version="selector-v1",
        system_prompt=(
            "You select durable-memory source items. Return JSON only with exactly "
            '{"selected_event_ids":["source-item-id"]}. Select only whole-event or '
            "source-span IDs present in the input; do not summarize or rewrite items."
        ),
        timeout=LOCAL_TIMEOUT,
    )
    selector = OpenAICompatSelectorEngine(
        selector_client,
        chunk_target_tokens=SELECTOR_TARGET,
        selector_context_tokens=32_768,
    )
    try:
        selection = await selector.select(events=events)
        return selection, source_items(events), [selector_client], {
            "calls": len(selector.chunk_telemetry),
            "wall_ms": sum(float(row.get("wall_ms") or 0) for row in selector.chunk_telemetry),
            "telemetry": list(selector.chunk_telemetry),
        }, None
    except Exception as exc:
        return None, source_items(events), [selector_client], {
            "calls": len(selector.chunk_telemetry),
            "wall_ms": sum(float(row.get("wall_ms") or 0) for row in selector.chunk_telemetry),
            "telemetry": list(selector.chunk_telemetry),
        }, str(exc)[:2000]


async def run_direct_arm(
    events: list[Event],
    *,
    arm: str,
    selected: list[SourceItem],
    all_items: list[SourceItem],
    selector_clients: list[Any],
    selector_stage: dict[str, Any],
    raw_direct: bool,
) -> ArmResult:
    key = os.environ.get("OPENROUTER_API_KEY")
    solar_clients: list[Any] = []
    content: str | None = None
    previous: Capsule | None = None
    evidence_parent_ids: list[str] = []
    chunk_records: list[dict[str, Any]] = []
    started = time.perf_counter()
    failure: str | None = None
    chunks = _chunk_events(list(selected), target_tokens=DIRECT_TARGET)
    for index, chunk in enumerate(chunks):
        solar_client = make_client(
            endpoint=SOLAR_ENDPOINT,
            model=SOLAR_MODEL,
            api_key=key,
            prompt_version=f"{arm.lower()}-consolidator-v1",
            system_prompt=(DIRECT_RAW_CONSOLIDATOR_SYSTEM if raw_direct else DIRECT_SELECTOR_CONSOLIDATOR_SYSTEM),
            timeout=SOLAR_TIMEOUT,
        )
        solar_client.response_format = CONSOLIDATOR_RESPONSE_FORMAT
        solar_clients.append(solar_client)
        ids = tuple(item.id for item in chunk)
        payload = {
            "mode": "bounded_raw_direct" if raw_direct else "selector_direct",
            "base_capsule": (
                {
                    "id": previous.id,
                    "thread_id": previous.thread_id,
                    "content": previous.content,
                    "capsule_hash": previous.capsule_hash,
                    "covered_end_event_id": previous.covered_end_event_id,
                }
                if previous else None
            ),
            "snapshot_end_event_id": events[-1].id if events else None,
            "source_ids_in_order": list(ids),
            "source_events": [_event_payload(item) for item in chunk],
            "authoritative_source_universe": [item.id for item in selected],
        }
        call_started = time.perf_counter()
        try:
            response = await solar_client.complete_json(payload)
            chunk_content, evidence_ids = parse_direct_response(response, allowed_ids=ids)
            content = chunk_content
            for source_id in evidence_ids:
                item = next(item for item in chunk if item.id == source_id)
                parent_id = source_item_parent_event_id(item)
                if parent_id not in evidence_parent_ids:
                    evidence_parent_ids.append(parent_id)
            previous = make_capsule(
                content,
                thread_id="phase3-2-thread",
                end_id=source_item_parent_event_id(chunk[-1]) if chunk else None,
                index=index,
            )
            chunk_records.append({
                "chunk_index": index,
                "status": "SUCCEEDED",
                "source_refs": list(ids),
                "source_tokens": source_token_count(chunk),
                "wall_ms": (time.perf_counter() - call_started) * 1000,
                "response": client_metrics(solar_client),
                "evidence_ids": list(evidence_ids),
                "input_hash": digest(payload),
            })
        except Exception as exc:
            failure = str(exc)[:2000]
            chunk_records.append({
                "chunk_index": index,
                "status": "FAILED",
                "source_refs": list(ids),
                "source_tokens": source_token_count(chunk),
                "wall_ms": (time.perf_counter() - call_started) * 1000,
                "response": client_metrics(solar_client),
                "error": failure,
                "input_hash": digest(payload),
            })
            break
    status = "SUCCEEDED" if failure is None and bool(chunks) and content else "FAILED"
    result = None
    if status == "SUCCEEDED" and content is not None:
        result = CompactionResult(
            content=content,
            covered_event_ids=tuple(event.id for event in events),
            evidence_event_ids=tuple(evidence_parent_ids),
            input_hash=compute_input_hash(None, events, events[-1].id if events else None),
            output_hash=hashlib.sha256(content.encode()).hexdigest(),
            model_identity=SOLAR_MODEL,
            prompt_version=f"{arm.lower()}-consolidator-v1",
            generation_settings=dict(GENERATION),
            evidence_source_ids=tuple(
                item.id for item in selected if item.id in {
                    ref for record in chunk_records for ref in record.get("evidence_ids", [])
                }
            ),
        )
    direct_wall_ms = (time.perf_counter() - started) * 1000
    selector_wall_ms = float(selector_stage.get("wall_ms") or 0)
    return ArmResult(
        arm=arm,
        status=status,
        error=failure,
        result=result,
        source_tokens=sum(estimate_tokens(event.content) for event in events),
        local_clients=selector_clients,
        solar_clients=solar_clients,
        selected_items=selected,
        source_items=all_items,
        stage={
            # Selector work happens before this function is entered. Include
            # it in the arm's end-to-end wall clock so rates cannot silently
            # exclude the stage they are intended to compare.
            "wall_ms": selector_wall_ms + direct_wall_ms,
            "direct_wall_ms": direct_wall_ms,
            "selector": selector_stage,
            "direct_consolidator": {
                "calls": len(chunk_records),
                "completed_calls": sum(row["status"] == "SUCCEEDED" for row in chunk_records),
                "wall_ms": sum(float(row.get("wall_ms") or 0) for row in chunk_records),
                "chunks": chunk_records,
            },
        },
        promotions=0,
        promotion_detail={},
        semantic_quality={},
    )


def promote_for_check(events: list[Event], result: CompactionResult | None, arm: str) -> dict[str, Any]:
    if result is None:
        return {"status": "NOT_REACHED"}
    # Windows can retain SQLite WAL handles briefly after a context manager
    # exits. Keep these tiny evaluator DBs as forensic artifacts instead of
    # making promotion validation depend on best-effort directory deletion.
    temp = tempfile.mkdtemp(prefix="orchid_phase3_2_")
    database = Path(temp) / "memory.db"
    store = SQLiteStore(database)
    project_id = "phase3-2-project"
    thread_id = f"phase3-2-{arm.lower()}"
    store.create_project(project_id)
    store.create_thread(thread_id, project_id)
    parent = None
    for event in events:
        parent = store.append_event(
            project_id=project_id,
            thread_id=thread_id,
            event_type=event.event_type,
            role=event.role,
            content=event.content,
            event_id=event.id,
            parent_event_id=parent["id"] if parent else None,
        )
    capsule_id = store.create_capsule(
        thread_id=thread_id,
        base_capsule_id=None,
        content=result.content,
        source_event_hash=source_event_hash(events),
        input_hash=result.input_hash,
        output_hash=result.output_hash,
        capsule_hash=result.output_hash,
        snapshot_start_event_id=events[0].id if events else None,
        snapshot_end_event_id=events[-1].id if events else None,
        covered_start_event_id=events[0].id if events else None,
        covered_end_event_id=events[-1].id if events else None,
        model_metadata={"arm": arm, "phase": "3.2"},
    )
    ready = store.mark_capsule_ready(capsule_id)
    promoted = store.promote_capsule_cas(thread_id, capsule_id)
    active = store.get_active_capsule(thread_id)
    return {
        "status": "PROMOTED" if promoted else "FAILED",
        "capsule_id": capsule_id,
        "database": str(database),
        "ready": ready,
        "promoted": promoted,
        "active_tokens": estimate_tokens(active["content"]) if active else None,
        "active_content_hash": active.get("capsule_hash") if active else None,
    }


def quality_for(arm_result: ArmResult, promotion: dict[str, Any]) -> dict[str, Any]:
    result = arm_result.result
    known_refs = set(item.id for item in arm_result.selected_items)
    evidence_refs = set(result.evidence_source_ids if result else ())
    return {
        "software_promotion": promotion.get("status"),
        "nonempty_capsule": bool(result and result.content.strip()),
        "exact_source_ref_subset": evidence_refs.issubset(known_refs) if result else False,
        "unknown_source_ref_count": len(evidence_refs - known_refs) if result else None,
        "covered_event_count": len(result.covered_event_ids) if result else 0,
        "expected_event_count": len(arm_result.source_items),
        "evidence_parent_count": len(result.evidence_event_ids) if result else 0,
        "semantic_judge": "not_run; no deterministic frozen semantic judge exists for this trace",
        "current_state_correctness": "not independently scored",
        "supersession_correctness": "not independently scored",
        "invention_correctness": "not independently scored",
    }


def serialize_arm(result: ArmResult, promotion: dict[str, Any]) -> dict[str, Any]:
    result.promotion_detail = promotion
    result.promotions = int(promotion.get("promoted", False))
    result.semantic_quality = quality_for(result, promotion)
    local = aggregate_client_metrics(result.local_clients)
    solar = aggregate_client_metrics(result.solar_clients)
    return {
        "arm": result.arm,
        "status": result.status,
        "error": result.error,
        "source_tokens": result.source_tokens,
        "source_tokens_per_second": (
            result.source_tokens / max(float(result.stage.get("wall_ms") or 0) / 1000, 0.000001)
            if result.status == "SUCCEEDED" else None
        ),
        "local_model": local,
        "solar_model": solar,
        "stage": result.stage,
        "promotion": promotion,
        "quality": result.semantic_quality,
        "selected_source_tokens": source_token_count(result.selected_items),
        "selected_source_ref_count": len(result.selected_items),
        "source_item_count": len(result.source_items),
        "capsule_tokens": estimate_tokens(result.result.content) if result.result else None,
        "result": (
            {
                "content_hash": result.result.output_hash,
                "covered_event_ids": list(result.result.covered_event_ids),
                "evidence_event_ids": list(result.result.evidence_event_ids),
                "evidence_source_ids": list(result.result.evidence_source_ids),
            }
            if result.result else None
        ),
    }


async def run_arm(arm: str, events: list[Event]) -> dict[str, Any]:
    if arm == "ARM_A_CURRENT":
        result = await run_current_arm(events)
    elif arm == "ARM_C_DIRECT":
        items = source_items(events)
        result = await run_direct_arm(
            events,
            arm=arm,
            selected=items,
            all_items=items,
            selector_clients=[],
            selector_stage={"calls": 0, "wall_ms": 0, "status": "NOT_RUN"},
            raw_direct=True,
        )
    else:
        selection, items, selector_clients, selector_stage, selector_error = await run_selector(events)
        if selector_error:
            result = ArmResult(
                arm=arm,
                status="FAILED",
                error=selector_error,
                result=None,
                source_tokens=sum(estimate_tokens(event.content) for event in events),
                local_clients=selector_clients,
                solar_clients=[],
                selected_items=[],
                source_items=items,
                stage={"selector": selector_stage},
                promotions=0,
                promotion_detail={},
                semantic_quality={},
            )
        else:
            selected = selected_items_from_result(items, selection) if selection else []
            result = await run_direct_arm(
                events,
                arm=arm,
                selected=items if arm == "ARM_C_DIRECT" else selected,
                all_items=items,
                selector_clients=selector_clients,
                selector_stage=selector_stage,
                raw_direct=arm == "ARM_C_DIRECT",
            )
    promotion = promote_for_check(events, result.result, arm)
    return serialize_arm(result, promotion)


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    replay = Path(args.replay)
    events = load_events(replay)
    if args.event_limit:
        events = events[: args.event_limit]
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    arms = args.arms or ["ARM_A_CURRENT", "ARM_B_NO_CANONICALIZER", "ARM_C_DIRECT"]
    results: list[dict[str, Any]] = []
    for arm in arms:
        arm_dir = out / "ablation" / {
            "ARM_A_CURRENT": "arm_a_current",
            "ARM_B_NO_CANONICALIZER": "arm_b_no_canonicalizer",
            "ARM_C_DIRECT": "arm_c_direct",
            "ARM_D_ROLE_AFFINITY": "arm_d_role_affinity",
            "ARM_E_PIPELINED": "arm_e_pipelined",
        }.get(arm, arm.lower())
        arm_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "arm": arm,
            "replay": str(replay.resolve()),
            "replay_sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
            "events_used": len(events),
            "local_endpoint": LOCAL_ENDPOINT,
            "local_model": LOCAL_MODEL,
            "solar_endpoint": SOLAR_ENDPOINT,
            "solar_model": SOLAR_MODEL,
            "selector_target_tokens": SELECTOR_TARGET,
            "canonicalizer_target_tokens": CANONICALIZER_TARGET,
            "direct_target_tokens": DIRECT_TARGET,
            "local_timeout_seconds": LOCAL_TIMEOUT,
            "solar_timeout_seconds": SOLAR_TIMEOUT,
            "semantic_judge": "not configured",
        }
        (arm_dir / "CONFIG.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        try:
            result = await run_arm(arm, events)
        except Exception as exc:
            result = {
                "arm": arm,
                "status": "HARNESS_ERROR",
                "error": str(exc)[:2000],
            }
        results.append(result)
        (arm_dir / "SUMMARY.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        with (arm_dir / "telemetry.jsonl").open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        (arm_dir / "REPORT.md").write_text(
            f"# {arm}\n\n"
            f"Status: `{result.get('status')}`\n\n"
            f"Source tokens: `{result.get('source_tokens')}`\n\n"
            f"Full-arm throughput is reported only when status is `SUCCEEDED`; partial work is not treated as a completed-arm rate.\n",
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return {
        "phase": "3.2",
        "replay": str(replay.resolve()),
        "replay_sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
        "events_used": len(events),
        "arms": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", default=str(DEFAULT_REPLAY))
    parser.add_argument("--output", default=str(DEFAULT_OUT))
    parser.add_argument("--arms", nargs="*", default=None)
    parser.add_argument("--event-limit", type=int, default=0)
    args = parser.parse_args()
    result = asyncio.run(main_async(args))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "ablation" / "SUMMARY.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
