"""Freeze one completed coding-agent transcript for direct-memory replay.

This is a disposable evidence fixture. It converts response items from a
completed Codex coding session into the same Event format used by the
FreetoShop direct raw-to-Solar harness, then writes a fresh raw batch plan and
an oracle derived from the source task and its durable acceptance constraints.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.compaction import Event, expand_source_items, source_item_parent_event_id, source_item_payload
from memory_gateway.context import estimate_tokens
from memory_gateway.pipeline_adapters import _chunk_events


SESSION = (
    Path(r"C:\Users\disti\.codex\sessions\2026\07\19")
    / "rollout-2026-07-19T00-04-33-019f788b-fdf8-7231-99d9-41a274b3aa04.jsonl"
)
OUT = ROOT / "artifacts/agent_benchmarks/panoref_direct_generalization"
REPLAY = OUT / "frozen_panoref_replay/events.jsonl"
ORACLE = OUT / "semantic_oracle/deterministic_checks.jsonl"
MANIFEST = OUT / "semantic_oracle/manifest.json"
RAW_PLAN = OUT / "arm_direct_raw_solar/batch_plan.json"
DIRECT_TARGET = 12_000
SELECTOR_TARGET = 1_200


def digest(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    else:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def response_content(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def convert_session() -> tuple[list[Event], list[dict[str, Any]]]:
    events: list[Event] = []
    user_events: list[dict[str, Any]] = []
    for line in SESSION.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("type") != "response_item":
            continue
        payload = record.get("payload") or {}
        response_type = payload.get("type")
        if response_type not in {
            "message",
            "function_call",
            "function_call_output",
            "custom_tool_call",
            "custom_tool_call_output",
            "agent_message",
        }:
            continue
        role = payload.get("role")
        if response_type in {"function_call", "custom_tool_call", "agent_message"}:
            role = role or "assistant"
        elif response_type in {"function_call_output", "custom_tool_call_output"}:
            role = role or "tool"
        content = response_content(payload)
        sequence = len(events) + 1
        event = Event(
            id=f"panoref_evt_{sequence:06d}",
            sequence=sequence,
            content=content,
            content_hash=digest(content),
            event_type=response_type,
            role=role,
        )
        events.append(event)
        if response_type == "message" and role == "user":
            user_events.append({"sequence": sequence, "event_id": event.id, "content": content})
    if not events or not user_events:
        raise RuntimeError("session did not contain convertible coding-agent response items")
    return events, user_events


def plan_rows(chunks: list[list[Any]]) -> list[dict[str, Any]]:
    rows = []
    for index, chunk in enumerate(chunks):
        rows.append(
            {
                "chunk_index": index,
                "source_refs": [item.id for item in chunk],
                "parent_event_refs": [source_item_parent_event_id(item) for item in chunk],
                "source_tokens": sum(estimate_tokens(json.dumps(source_item_payload(item), ensure_ascii=False)) for item in chunk),
                "source_chars": sum(len(item.content) for item in chunk),
                "first_sequence": min((item.sequence for item in chunk), default=None),
                "last_sequence": max((item.sequence for item in chunk), default=None),
            }
        )
    return rows


def checkpoint_checks(user_events: list[dict[str, Any]], final_sequence: int) -> list[dict[str, Any]]:
    if len(user_events) < 3:
        raise RuntimeError(f"expected at least three user checkpoints, found {len(user_events)}")
    initial = user_events[0]["sequence"]
    review = user_events[1]["sequence"]
    final_request = user_events[-1]["sequence"]

    specs = [
        ("intent-start", initial, "CURRENT_INTENT_PRESERVATION", "branch", ["t3code/projector-occlusion"], []),
        ("intent-start", initial, "CURRENT_INTENT_PRESERVATION", "crash-repair", [], ["page crashes", "crashes"]),
        ("intent-start", initial, "CURRENT_INTENT_PRESERVATION", "ui-overlap", [], ["UI elements overlapping", "overlap"]),
        ("intent-start", initial, "CURRENT_INTENT_PRESERVATION", "coverage-feature", [], ["coverage-analysis engine", "coverage analysis"]),
        ("intent-start", initial, "CONTINUATION_SUFFICIENCY", "final-qa", [], ["qa bug search", "QA bug search"]),
        ("review", review, "CURRENT_FACT_PRESERVATION", "allowed-floor", [], ["allowed-floor", "allowed floor", "floor region"]),
        ("review", review, "CURRENT_FACT_PRESERVATION", "connected-components", [], ["connected-component", "connected component"]),
        ("review", review, "CURRENT_FACT_PRESERVATION", "candidate-enforcement", [], ["candidate/refinement", "candidate and refinement", "exact candidate"]),
        ("review", review, "BLOCKER_PRESERVATION", "large-import-risk", [], ["100K", "100,000", "large imported"]),
        ("final-validation", final_sequence, "CURRENT_FACT_PRESERVATION", "stale-analysis", [], ["stale-analysis", "stale analysis"]),
        ("final-validation", final_sequence, "CONTINUATION_SUFFICIENCY", "test-evidence", [], ["373/373", "373 tests", "tests passed"]),
        ("final-validation", final_sequence, "CONTINUATION_SUFFICIENCY", "runtime-evidence", [], ["36.9 seconds", "36.9s", "36,788 triangles"]),
    ]
    checks = []
    for index, (checkpoint, source_end, category, expectation_id, required_all, required_any) in enumerate(specs, start=1):
        checks.append(
            {
                "check_id": f"spot-{index:03d}",
                "checkpoint": checkpoint,
                "source_end_sequence": source_end,
                "source_refs": [event["event_id"] for event in user_events if event["sequence"] <= source_end][-1:],
                "category": category,
                "expectation_id": expectation_id,
                "required_all": required_all,
                "required_any": required_any,
                "expected_current_fact": required_all + required_any,
            }
        )
    return checks


def main() -> None:
    if not SESSION.exists():
        raise SystemExit(f"missing session: {SESSION}")
    if OUT.exists():
        raise SystemExit(f"refusing to overwrite frozen fixture: {OUT}")

    events, user_events = convert_session()
    items = list(expand_source_items(events, selector_budget_tokens=SELECTOR_TARGET, safety_margin=0.8))
    direct_chunks = _chunk_events(items, target_tokens=DIRECT_TARGET)
    checks = checkpoint_checks(user_events, events[-1].sequence)
    replay_rows = [
        {
            "event_id": event.id,
            "sequence": event.sequence,
            "event_type": event.event_type,
            "role": event.role,
            "content": event.content,
            "content_hash": event.content_hash,
        }
        for event in events
    ]
    write_jsonl(REPLAY, replay_rows)
    write_jsonl(ORACLE, checks)
    manifest = {
        "oracle_version": "panoref-direct-generalization-v1",
        "replay_sha256": hashlib.sha256(REPLAY.read_bytes()).hexdigest(),
        "session_path": str(SESSION),
        "session_sha256": hashlib.sha256(SESSION.read_bytes()).hexdigest(),
        "event_count": len(events),
        "source_item_count": len(items),
        "planned_source_tokens": sum(estimate_tokens(json.dumps(source_item_payload(item), ensure_ascii=False)) for item in items),
        "user_checkpoints": user_events,
        "oracle_sha256": digest(checks),
        "oracle_basis": "authoritative source task, review constraints, and final validation requirements from the frozen transcript; no new candidate output was inspected",
        "direct_target_tokens": DIRECT_TARGET,
        "selector_target_tokens": SELECTOR_TARGET,
        "raw_plan_hash": digest(plan_rows(direct_chunks)),
        "spot_check_count": len(checks),
    }
    write_json(MANIFEST, manifest)
    write_json(OUT / "trace_index.json", {"session": str(SESSION), "events": replay_rows, "user_events": user_events})
    write_json(RAW_PLAN, {
        "policy": "bounded raw source items, immutable event/span boundaries, target 12000 estimated payload tokens",
        "plan_hash": digest(plan_rows(direct_chunks)),
        "chunks": plan_rows(direct_chunks),
        "source_items": [source_item_payload(item) for item in items],
    })
    write_json(OUT / "FREEZE_METADATA.json", manifest)
    (OUT / "semantic_oracle/JUDGE_RUBRIC.md").write_text(
        "# Frozen PanoRef semantic rubric\n\n"
        "This deterministic content-coverage oracle was created before any new direct runs. "
        "It checks durable intent, constraints, blockers, and validation evidence from the "
        "authoritative coding transcript. A missing term is a conservative coverage miss, "
        "not a complete human semantic judgment.\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "FROZEN", **{key: manifest[key] for key in ("event_count", "source_item_count", "planned_source_tokens", "spot_check_count", "raw_plan_hash")}}, indent=2))


if __name__ == "__main__":
    main()
