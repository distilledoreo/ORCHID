"""Disposable recursive raw-to-Solar memory benchmark.

This is intentionally outside ORCHID's production pipeline. It replays the
frozen FreetoShop source trace, sends bounded raw windows plus the previous
plain-text capsule to Solar, and records enough evidence to compare the
result with the frozen Phase 3.3 Selector-to-Solar run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.compaction import (
    Event,
    SourceItem,
    expand_source_items,
    source_item_parent_event_id,
    source_item_payload,
)
from memory_gateway.context import estimate_tokens
from memory_gateway.openai_adapter import _endpoint_url
from memory_gateway.pipeline_adapters import _chunk_events, _event_payload


REPLAY = ROOT / (
    "artifacts/agent_benchmarks/freetoshop_operability_hardening/"
    "frozen_freetoshop_replay/events.jsonl"
)
PHASE33 = ROOT / "artifacts/agent_benchmarks/freetoshop_direct_consolidation"
OUT = ROOT / "artifacts/agent_benchmarks/freetoshop_recursive_raw_solar"
RAW_PLAN = PHASE33 / "arm_c_raw_solar" / "batch_plan.json"
ORACLE = PHASE33 / "semantic_oracle" / "deterministic_checks.jsonl"
SOLAR_ENDPOINT = "https://openrouter.ai/api"
SOLAR_MODEL = "upstage/solar-pro4"
SOLAR_TIMEOUT = 180.0
ARRIVAL_RATE = 59.148
INPUT_USD_PER_M = 0.03
OUTPUT_USD_PER_M = 0.12
GENERATION = {
    "temperature": 0,
    "top_p": 1,
    "stream": False,
    "reasoning_effort": "none",
}
SYSTEM_PROMPT = (
    "You are a recursive durable-memory compactor. Read the previous plain-text "
    "capsule and the next bounded window of authoritative raw coding-agent history. "
    "Return only the replacement durable-memory capsule as plain text. Do not return "
    "JSON, markdown fences, commentary, or a preamble. Preserve current task state, "
    "current facts, implementation decisions, rejected alternatives, unresolved "
    "blockers, supersession, user intent, and continuation-critical constraints. "
    "Discard transient shell output, repetitive logs, and incidental chatter. Do not "
    "invent facts. The raw source items are authoritative, but source IDs do not need "
    "to appear in the plain-text capsule."
)


def digest(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    else:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_events() -> list[Event]:
    events: list[Event] = []
    for line in REPLAY.read_text(encoding="utf-8").splitlines():
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


def source_items(events: list[Event]) -> list[SourceItem]:
    return list(expand_source_items(events, selector_budget_tokens=1_200, safety_margin=0.8))


def source_tokens(items: list[SourceItem]) -> int:
    return sum(estimate_tokens(json.dumps(source_item_payload(item), ensure_ascii=False)) for item in items)


def source_chars(items: list[SourceItem]) -> int:
    return sum(len(item.content) for item in items)


def plan_rows(chunks: list[list[SourceItem]]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_index": index,
            "source_refs": [item.id for item in chunk],
            "parent_event_refs": [source_item_parent_event_id(item) for item in chunk],
            "source_tokens": source_tokens(chunk),
            "source_chars": source_chars(chunk),
            "first_sequence": min((item.sequence for item in chunk), default=None),
            "last_sequence": max((item.sequence for item in chunk), default=None),
        }
        for index, chunk in enumerate(chunks)
    ]


def capsule_text_from_choice(choice: dict[str, Any]) -> str:
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts).strip()
    raise ValueError("Solar response message content was not plain text")


def usage_metrics(usage: Any) -> dict[str, Any]:
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("completion_tokens_details") or {}
    return {
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens", usage.get("reasoning_tokens", 0)),
    }


def prompt_for(previous: str | None, chunk: list[SourceItem]) -> str:
    raw = [_event_payload(item) for item in chunk]
    return (
        "PREVIOUS_PLAIN_TEXT_CAPSULE\n"
        "--- BEGIN PREVIOUS CAPSULE ---\n"
        f"{previous or '(none; this is the first generation)'}\n"
        "--- END PREVIOUS CAPSULE ---\n\n"
        "NEXT_AUTHORITATIVE_RAW_SOURCE_ITEMS_JSON\n"
        "--- BEGIN RAW SOURCE ITEMS ---\n"
        f"{json.dumps(raw, ensure_ascii=False, sort_keys=True)}\n"
        "--- END RAW SOURCE ITEMS ---\n"
    )


async def call_solar(
    client: httpx.AsyncClient,
    prompt: str,
    *,
    api_key: str,
) -> tuple[str, dict[str, Any]]:
    body = {
        "model": SOLAR_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        **GENERATION,
    }
    request_hash = digest(body)
    started = time.perf_counter()
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        async with asyncio.timeout(SOLAR_TIMEOUT):
            response = await client.post(
                _endpoint_url(SOLAR_ENDPOINT),
                json=body,
                headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
            )
        elapsed_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
        payload = response.json()
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise RuntimeError("Solar response did not contain choices")
        content = capsule_text_from_choice(choices[0])
        if not content:
            raise RuntimeError("Solar returned an empty plain-text capsule")
        usage = usage_metrics(payload.get("usage"))
        telemetry = {
            "status": "SUCCEEDED",
            "request_hash": request_hash,
            "start_timestamp": timestamp,
            "wall_ms": elapsed_ms,
            "finish_reason": choices[0].get("finish_reason"),
            "response_id": payload.get("id"),
            **usage,
        }
        return content, telemetry
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        telemetry = {
            "status": "FAILED",
            "request_hash": request_hash,
            "start_timestamp": timestamp,
            "wall_ms": elapsed_ms,
            "error": str(exc)[:1000],
            "error_type": type(exc).__name__,
        }
        raise RuntimeError((str(exc) or type(exc).__name__)[:1000]) from exc


def frozen_chunks(items: list[SourceItem]) -> list[list[SourceItem]]:
    plan = load_json(RAW_PLAN)
    chunks = []
    lookup = {item.id: item for item in items}
    for row in plan["chunks"]:
        chunk = [lookup[item_id] for item_id in row["source_refs"]]
        chunks.append(chunk)
    if digest(plan_rows(chunks)) != plan["plan_hash"]:
        raise RuntimeError("frozen raw batch plan hash mismatch")
    return chunks


async def run() -> None:
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise RuntimeError("OPENROUTER_API_KEY is unavailable")
    events = load_events()
    items = source_items(events)
    chunks = frozen_chunks(items)
    OUT.mkdir(parents=True, exist_ok=True)
    telemetry_path = OUT / "telemetry.jsonl"
    capsules_path = OUT / "capsules.jsonl"
    telemetry_path.unlink(missing_ok=True)
    capsules_path.unlink(missing_ok=True)
    started = time.perf_counter()
    previous: str | None = None
    completed = 0
    failure: str | None = None
    timeout_count = 0
    input_total = 0
    output_total = 0
    cost_total = 0.0
    limits = httpx.Timeout(SOLAR_TIMEOUT, connect=30.0)
    async with httpx.AsyncClient(timeout=limits) as client:
        for generation, chunk in enumerate(chunks):
            prompt = prompt_for(previous, chunk)
            base_capsule_hash = digest(previous) if previous is not None else None
            row: dict[str, Any] = {
                "arm": "RECURSIVE_RAW_SOLAR_PLAIN_TEXT",
                "generation": generation,
                "source_refs": [item.id for item in chunk],
                "source_tokens": source_tokens(chunk),
                "source_chars": source_chars(chunk),
                "source_start_sequence": min((item.sequence for item in chunk), default=None),
                "source_end_sequence": max((item.sequence for item in chunk), default=None),
                "prompt_chars": len(prompt),
                "prompt_estimated_tokens": estimate_tokens(prompt),
                "base_capsule_chars": len(previous or ""),
                "base_capsule_estimated_tokens": estimate_tokens(previous or ""),
                "prompt_hash": digest(prompt),
            }
            call_started = time.perf_counter()
            try:
                content, provider = await call_solar(client, prompt, api_key=os.environ["OPENROUTER_API_KEY"])
                row.update(
                    {
                        "status": "SUCCEEDED",
                        "response": provider,
                        "wall_ms": (time.perf_counter() - call_started) * 1000,
                        "capsule_chars": len(content),
                        "capsule_estimated_tokens": estimate_tokens(content),
                        "capsule_hash": digest(content),
                    }
                )
                previous = content
                completed += 1
                input_total += int(provider.get("input_tokens") or 0)
                output_total += int(provider.get("output_tokens") or 0)
                cost_total += (float(provider.get("input_tokens") or 0) / 1_000_000 * INPUT_USD_PER_M)
                cost_total += (float(provider.get("output_tokens") or 0) / 1_000_000 * OUTPUT_USD_PER_M)
                append_jsonl(capsules_path, {
                    "generation": generation,
                    "content": content,
                    "content_hash": digest(content),
                    "base_capsule_hash": base_capsule_hash,
                    "source_refs": [item.id for item in chunk],
                    "covered_end_sequence": row["source_end_sequence"],
                })
            except Exception as exc:
                message = (str(exc) or type(exc).__name__)[:1000]
                timeout_count += int("timed out" in message.lower() or "timeout" in message.lower())
                row.update({
                    "status": "FAILED",
                    "error": message,
                    "wall_ms": (time.perf_counter() - call_started) * 1000,
                })
                failure = message
            append_jsonl(telemetry_path, row)
            if failure is not None:
                break
            previous_before = previous
    wall_ms = (time.perf_counter() - started) * 1000
    source_retired = sum(row["source_tokens"] for row in load_jsonl(telemetry_path) if row.get("status") == "SUCCEEDED")
    summary = {
        "arm": "RECURSIVE_RAW_SOLAR_PLAIN_TEXT",
        "status": "SUCCEEDED" if failure is None and completed == len(chunks) else "FAILED",
        "error": failure,
        "event_count": len(events),
        "source_item_count": len(items),
        "planned_source_tokens": source_tokens(items),
        "source_tokens_retired": source_retired,
        "planned_generations": len(chunks),
        "completed_generations": completed,
        "wall_ms": wall_ms,
        "source_tokens_per_second": source_retired / max(wall_ms / 1000, 0.001),
        "margin_vs_arrival": source_retired / max(wall_ms / 1000, 0.001) - ARRIVAL_RATE,
        "solar_calls": completed + int(failure is not None),
        "solar_input_tokens": input_total,
        "solar_output_tokens": output_total,
        "solar_estimated_cost_usd": cost_total,
        "timeout_count": timeout_count,
        "batch_plan_hash": load_json(RAW_PLAN)["plan_hash"],
        "replay_sha256": hashlib.sha256(REPLAY.read_bytes()).hexdigest(),
        "system_prompt_hash": digest(SYSTEM_PROMPT),
        "generation_hash": digest(GENERATION),
        "response_format": None,
    }
    write_json(OUT / "CONFIG.json", {
        "arm": summary["arm"],
        "replay_sha256": summary["replay_sha256"],
        "semantic_oracle_manifest_sha256": hashlib.sha256((PHASE33 / "semantic_oracle" / "manifest.json").read_bytes()).hexdigest(),
        "batch_plan_hash": summary["batch_plan_hash"],
        "solar_endpoint": SOLAR_ENDPOINT,
        "solar_model": SOLAR_MODEL,
        "solar_timeout_seconds": SOLAR_TIMEOUT,
        "generation": GENERATION,
        "response_format": None,
        "plain_text_capsule": True,
        "recursive_base_capsule": True,
    })
    write_json(OUT / "batch_plan.json", {
        "policy": "frozen Phase 3.3 bounded raw source windows, recursive plain-text capsule, target 12000 estimated payload tokens",
        "plan_hash": summary["batch_plan_hash"],
        "chunks": plan_rows(chunks),
    })
    write_json(OUT / "SUMMARY.json", summary)


def evaluate_text(text: str, end_sequence: int, oracle: list[dict[str, Any]], generation: int) -> list[dict[str, Any]]:
    lowered = text.lower()
    rows = []
    for check in oracle:
        if int(check["source_end_sequence"]) > end_sequence:
            continue
        all_terms = [term.lower() for term in check.get("required_all", [])]
        any_terms = [term.lower() for term in check.get("required_any", [])]
        all_ok = all(term in lowered for term in all_terms)
        any_ok = not any_terms or any(term in lowered for term in any_terms)
        rows.append({
            "generation": generation,
            "checkpoint": check["checkpoint"],
            "check_id": check["check_id"],
            "category": check["category"],
            "status": "PASS" if all_ok and any_ok else "FAIL",
            "missing_all": [term for term in all_terms if term not in lowered],
            "missing_any": any_terms if any_terms and not any_ok else [],
        })
    return rows


def primary_capture() -> list[dict[str, Any]]:
    """Return only the valid prefix through the first failed generation."""

    rows: list[dict[str, Any]] = []
    for row in load_jsonl(OUT / "telemetry.jsonl"):
        rows.append(row)
        if row.get("status") != "SUCCEEDED":
            break
    return rows


def finalize_capture() -> None:
    """Finalize an interrupted capture without making another provider call."""

    events = load_events()
    items = source_items(events)
    chunks = frozen_chunks(items)
    rows = primary_capture()
    completed = sum(row.get("status") == "SUCCEEDED" for row in rows)
    failed = next((row for row in rows if row.get("status") != "SUCCEEDED"), None)
    source_retired = sum(int(row.get("source_tokens") or 0) for row in rows if row.get("status") == "SUCCEEDED")
    input_total = sum(int((row.get("response") or {}).get("input_tokens") or 0) for row in rows)
    output_total = sum(int((row.get("response") or {}).get("output_tokens") or 0) for row in rows)
    cost_total = input_total / 1_000_000 * INPUT_USD_PER_M + output_total / 1_000_000 * OUTPUT_USD_PER_M
    wall_ms = sum(float(row.get("wall_ms") or 0) for row in rows)
    failed_wall_ms = float((failed or {}).get("wall_ms") or 0)
    error = (failed or {}).get("error") or (
        f"TimeoutError after {SOLAR_TIMEOUT:.0f}s"
        if failed_wall_ms >= SOLAR_TIMEOUT * 1000 * 0.95
        else "capture ended before a provider response was finalized"
    )
    summary = {
        "arm": "RECURSIVE_RAW_SOLAR_PLAIN_TEXT",
        "status": "SUCCEEDED" if failed is None and completed == len(chunks) else "FAILED",
        "error": error,
        "event_count": len(events),
        "source_item_count": len(items),
        "planned_source_tokens": source_tokens(items),
        "source_tokens_retired": source_retired,
        "planned_generations": len(chunks),
        "completed_generations": completed,
        "wall_ms": wall_ms,
        "source_tokens_per_second": source_retired / max(wall_ms / 1000, 0.001),
        "margin_vs_arrival": source_retired / max(wall_ms / 1000, 0.001) - ARRIVAL_RATE,
        "solar_calls": len(rows),
        "solar_input_tokens": input_total,
        "solar_output_tokens": output_total,
        "solar_estimated_cost_usd": cost_total,
        "timeout_count": sum(
            "timeout" in str(row.get("error", "")).lower()
            or float(row.get("wall_ms") or 0) >= SOLAR_TIMEOUT * 1000 * 0.95
            for row in rows
        ),
        "batch_plan_hash": load_json(RAW_PLAN)["plan_hash"],
        "replay_sha256": hashlib.sha256(REPLAY.read_bytes()).hexdigest(),
        "system_prompt_hash": digest(SYSTEM_PROMPT),
        "generation_hash": digest(GENERATION),
        "response_format": None,
        "primary_capture_stops_at_first_failure": True,
        "raw_records_after_first_failure_ignored": max(0, len(load_jsonl(OUT / "telemetry.jsonl")) - len(rows)),
    }
    write_json(OUT / "SUMMARY.json", summary)
    write_json(OUT / "CONFIG.json", {
        "arm": summary["arm"],
        "replay_sha256": summary["replay_sha256"],
        "semantic_oracle_manifest_sha256": hashlib.sha256((PHASE33 / "semantic_oracle" / "manifest.json").read_bytes()).hexdigest(),
        "batch_plan_hash": summary["batch_plan_hash"],
        "solar_endpoint": SOLAR_ENDPOINT,
        "solar_model": SOLAR_MODEL,
        "solar_timeout_seconds": SOLAR_TIMEOUT,
        "generation": GENERATION,
        "response_format": None,
        "plain_text_capsule": True,
        "recursive_base_capsule": True,
        "capture_note": "A post-timeout harness bug issued one additional raw record; it is retained in raw telemetry but excluded from the primary prefix.",
    })
    write_json(OUT / "batch_plan.json", {
        "policy": "frozen Phase 3.3 bounded raw source windows, recursive plain-text capsule, target 12000 estimated payload tokens",
        "plan_hash": summary["batch_plan_hash"],
        "chunks": plan_rows(chunks),
    })


def evaluate_and_report() -> None:
    summary = load_json(OUT / "SUMMARY.json")
    telemetry = primary_capture()
    valid_generations = {row["generation"] for row in telemetry if row.get("status") == "SUCCEEDED"}
    capsules = {row["generation"]: row for row in load_jsonl(OUT / "capsules.jsonl") if row.get("generation") in valid_generations}
    oracle = load_jsonl(ORACLE)
    eval_rows: list[dict[str, Any]] = []
    for row in telemetry:
        capsule = capsules.get(row["generation"])
        if row.get("status") == "SUCCEEDED" and capsule:
            eval_rows.extend(evaluate_text(capsule["content"], row["source_end_sequence"], oracle, row["generation"]))
    OUT.joinpath("semantic_eval.jsonl").unlink(missing_ok=True)
    for row in eval_rows:
        append_jsonl(OUT / "semantic_eval.jsonl", row)
    by_category: dict[str, dict[str, int]] = {}
    for row in eval_rows:
        category = by_category.setdefault(row["category"], {"pass": 0, "fail": 0})
        category["pass" if row["status"] == "PASS" else "fail"] += 1
    final_generation = max(capsules) if capsules else None
    final_rows = [row for row in eval_rows if row["generation"] == final_generation]
    growth = [
        {
            "generation": row["generation"],
            "capsule_chars": row["capsule_chars"],
            "capsule_estimated_tokens": row["capsule_estimated_tokens"],
            "base_capsule_chars": row["base_capsule_chars"],
            "source_end_sequence": row["source_end_sequence"],
        }
        for row in telemetry
        if row.get("status") == "SUCCEEDED"
    ]
    b_summary = load_json(PHASE33 / "arm_b_selector_solar" / "SUMMARY.json")
    b_capsules = load_jsonl(PHASE33 / "arm_b_selector_solar" / "capsules.jsonl")
    b_eval = [row for row in load_jsonl(PHASE33 / "semantic_eval" / "arm_b.jsonl") if row.get("kind") != "STRUCTURAL"]
    b_final_generation = max((row.get("generation", -1) for row in b_capsules), default=None)
    b_final = [row for row in b_eval if row.get("generation") == b_final_generation]
    b_growth = [{
        "generation": row.get("generation"),
        "capsule_chars": len(str(row.get("content") or "")),
        "capsule_estimated_tokens": estimate_tokens(str(row.get("content") or "")),
    } for row in b_capsules]
    raw_growth_ratio = (
        growth[-1]["capsule_estimated_tokens"] / max(growth[0]["capsule_estimated_tokens"], 1)
        if growth else None
    )
    b_growth_ratio = (
        b_growth[-1]["capsule_estimated_tokens"] / max(b_growth[0]["capsule_estimated_tokens"], 1)
        if b_growth else None
    )
    comparison = {
        "recursive_raw_solar": {
            "status": summary["status"],
            "source_tokens_retired": summary["source_tokens_retired"],
            "wall_seconds": summary["wall_ms"] / 1000,
            "source_tokens_per_second": summary["source_tokens_per_second"],
            "solar_input_tokens": summary["solar_input_tokens"],
            "solar_output_tokens": summary["solar_output_tokens"],
            "solar_estimated_cost_usd": summary["solar_estimated_cost_usd"],
            "semantic_checks": len(eval_rows),
            "semantic_pass": sum(row["status"] == "PASS" for row in eval_rows),
            "semantic_fail": sum(row["status"] == "FAIL" for row in eval_rows),
            "final_checkpoint_checks": len(final_rows),
            "final_checkpoint_pass": sum(row["status"] == "PASS" for row in final_rows),
            "final_checkpoint_fail": sum(row["status"] == "FAIL" for row in final_rows),
            "capsule_growth": growth,
            "final_capsule_estimated_tokens": growth[-1]["capsule_estimated_tokens"] if growth else None,
            "max_capsule_estimated_tokens": max((row["capsule_estimated_tokens"] for row in growth), default=None),
            "first_to_final_capsule_token_ratio": raw_growth_ratio,
            "semantic_by_category": by_category,
        },
        "existing_selector_solar": {
            "status": b_summary.get("status"),
            "source_tokens_retired": b_summary.get("source_tokens_retired"),
            "wall_seconds": b_summary.get("wall_ms", 0) / 1000,
            "source_tokens_per_second": b_summary.get("source_tokens_per_second"),
            "solar_input_tokens": b_summary.get("solar_input_tokens"),
            "solar_output_tokens": b_summary.get("solar_output_tokens"),
            "solar_estimated_cost_usd": b_summary.get("solar_estimated_cost_usd"),
            "semantic_checks": len(b_eval),
            "semantic_pass": sum(row.get("status") == "PASS" for row in b_eval),
            "semantic_fail": sum(row.get("status") == "FAIL" for row in b_eval),
            "final_checkpoint_checks": len(b_final),
            "final_checkpoint_pass": sum(row.get("status") == "PASS" for row in b_final),
            "final_checkpoint_fail": sum(row.get("status") == "FAIL" for row in b_final),
            "capsule_growth": b_growth,
            "final_capsule_estimated_tokens": b_growth[-1]["capsule_estimated_tokens"] if b_growth else None,
            "max_capsule_estimated_tokens": max((row["capsule_estimated_tokens"] for row in b_growth), default=None),
            "first_to_final_capsule_token_ratio": b_growth_ratio,
        },
    }
    write_json(OUT / "COMPARISON.json", comparison)
    report = [
        "# Disposable recursive raw → Solar benchmark",
        "",
        "This is an isolated benchmark harness. It made no ORCHID production changes and used the frozen Phase 3.3 replay, raw batch plan, and semantic oracle.",
        "",
        "## Measured facts",
        "",
        f"- Replay: {summary['source_item_count']} source items, {summary['planned_source_tokens']:,} planned source tokens; replay SHA `{summary['replay_sha256']}`.",
        f"- Recursive raw→Solar status: **{summary['status']}**; {summary['completed_generations']}/{summary['planned_generations']} generations completed.",
        f"- Retirement: {summary['source_tokens_retired']:,} tokens in {summary['wall_ms'] / 1000:.2f}s = **{summary['source_tokens_per_second']:.3f} tok/s**; margin versus arrival {summary['margin_vs_arrival']:.3f} tok/s.",
        f"- Solar: {summary['solar_calls']} calls, {summary['solar_input_tokens']:,} input tokens, {summary['solar_output_tokens']:,} output tokens, estimated cost ${summary['solar_estimated_cost_usd']:.6f}.",
        f"- Failures/timeouts: {summary['error'] or 'none'} / {summary['timeout_count']}.",
        f"- Semantic retention: {comparison['recursive_raw_solar']['semantic_pass']} PASS / {comparison['recursive_raw_solar']['semantic_fail']} FAIL across {comparison['recursive_raw_solar']['semantic_checks']} applicable frozen checks.",
        f"- Final checkpoint retention: {comparison['recursive_raw_solar']['final_checkpoint_pass']} PASS / {comparison['recursive_raw_solar']['final_checkpoint_fail']} FAIL.",
        f"- Capsule size: final {comparison['recursive_raw_solar']['final_capsule_estimated_tokens']} estimated tokens; maximum {comparison['recursive_raw_solar']['max_capsule_estimated_tokens']}.",
        f"- Capsule growth: {comparison['recursive_raw_solar']['first_to_final_capsule_token_ratio']:.2f}x from first to final valid generation.",
        "",
        "## Comparison with existing Selector → Solar",
        "",
        f"- Selector→Solar: {b_summary.get('source_tokens_retired', 0):,} selected source tokens in {b_summary.get('wall_ms', 0) / 1000:.2f}s = **{b_summary.get('source_tokens_per_second', 0):.3f} tok/s**.",
        f"- Selector→Solar provider use: {b_summary.get('solar_input_tokens', 0):,.0f} input / {b_summary.get('solar_output_tokens', 0):,.0f} output tokens; estimated cost ${b_summary.get('solar_estimated_cost_usd', 0):.6f}.",
        f"- Selector→Solar semantic retention under the same frozen rubric: {comparison['existing_selector_solar']['semantic_pass']} PASS / {comparison['existing_selector_solar']['semantic_fail']} FAIL across {comparison['existing_selector_solar']['semantic_checks']} checks.",
        f"- Selector→Solar final capsule: {comparison['existing_selector_solar']['final_capsule_estimated_tokens']} estimated tokens; maximum {comparison['existing_selector_solar']['max_capsule_estimated_tokens']}.",
        f"- Selector→Solar first-to-final capsule ratio: {comparison['existing_selector_solar']['first_to_final_capsule_token_ratio']:.2f}x.",
        "",
        "## Interpretation",
        "",
        "Plain-text output removes the structured-output failure mode and makes the recursive memory mechanism directly testable, but it also removes software-enforced source provenance. Semantic checks are the frozen deterministic coverage rubric, not a complete human semantic judge. The Selector→Solar numbers are historical Phase 3.3 results and were not rerun or tuned for this disposable experiment.",
        "",
        "## Decision",
        "",
        "This benchmark does not change ORCHID and does not authorize a production architecture. Direct recursive raw→Solar is useful only if it completes the trace with adequate semantic retention and acceptable capsule growth; the recorded comparison is the evidence for that decision.",
    ]
    (OUT / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "finalize", "evaluate"))
    args = parser.parse_args()
    if args.command == "run":
        asyncio.run(run())
    elif args.command == "finalize":
        finalize_capture()
    else:
        evaluate_and_report()


if __name__ == "__main__":
    main()
