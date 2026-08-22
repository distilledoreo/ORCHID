"""Freeze a bounded, independent PanoRef coding-trajectory slice.

The complete PanoRef session is too large for the modest repeat experiment.
This fixture preserves the authoritative review, final request, and the real
corrective/audit/validation tail as one immutable source condition.  It does
not inspect any new candidate capsule when creating the oracle.
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

import freeze_coding_trace as full_trace
from memory_gateway.compaction import expand_source_items, source_item_parent_event_id, source_item_payload
from memory_gateway.context import estimate_tokens
from memory_gateway.pipeline_adapters import _chunk_events


OUT = ROOT / "artifacts/agent_benchmarks/panoref_direct_generalization_bounded_v2"
REPLAY = OUT / "frozen_panoref_replay/events.jsonl"
ORACLE = OUT / "semantic_oracle/deterministic_checks.jsonl"
MANIFEST = OUT / "semantic_oracle/manifest.json"
RAW_PLAN = OUT / "arm_direct_raw_solar/batch_plan.json"
DIRECT_TARGET = 12_000
SELECTOR_TARGET = 1_200
REVIEW_SEQUENCE = 582
FINAL_REQUEST_SEQUENCE = 762
TAIL_START_SEQUENCE = 922


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


def selected_events() -> tuple[list[Any], list[dict[str, Any]], str]:
    events, _ = full_trace.convert_session()
    selected = [
        event
        for event in events
        if event.sequence in {REVIEW_SEQUENCE, FINAL_REQUEST_SEQUENCE}
        or event.sequence >= TAIL_START_SEQUENCE
    ]
    if not selected:
        raise RuntimeError("bounded PanoRef selection is empty")
    replay_rows = [
        {
            "event_id": event.id,
            "sequence": event.sequence,
            "event_type": event.event_type,
            "role": event.role,
            "content": event.content,
            "content_hash": event.content_hash,
        }
        for event in selected
    ]
    users = [
        {
            "sequence": event.sequence,
            "event_id": event.id,
            "content": event.content,
        }
        for event in selected
        if event.event_type == "message" and event.role == "user"
    ]
    session_sha256 = hashlib.sha256(full_trace.SESSION.read_bytes()).hexdigest()
    return selected, users, session_sha256


def plan_rows(chunks: list[list[Any]]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_index": index,
            "source_refs": [item.id for item in chunk],
            "parent_event_refs": [source_item_parent_event_id(item) for item in chunk],
            "source_tokens": sum(estimate_tokens(json.dumps(source_item_payload(item), ensure_ascii=False)) for item in chunk),
            "source_chars": sum(len(item.content) for item in chunk),
            "first_sequence": min((item.sequence for item in chunk), default=None),
            "last_sequence": max((item.sequence for item in chunk), default=None),
        }
        for index, chunk in enumerate(chunks)
    ]


def oracle_checks() -> list[dict[str, Any]]:
    specs = [
        ("review", REVIEW_SEQUENCE, "CURRENT_FACT_PRESERVATION", "allowed-floor", [], ["allowed-floor", "allowed floor", "floor region"]),
        ("review", REVIEW_SEQUENCE, "CURRENT_FACT_PRESERVATION", "connected-components", [], ["connected-component", "connected component"]),
        ("review", REVIEW_SEQUENCE, "BLOCKER_PRESERVATION", "large-import-risk", [], ["100K", "100,000", "large imported"]),
        ("final-request", FINAL_REQUEST_SEQUENCE, "CURRENT_INTENT_PRESERVATION", "rebase", ["rebase"], []),
        ("final-request", FINAL_REQUEST_SEQUENCE, "CONTINUATION_SUFFICIENCY", "adversarial-audit", [], ["subagent", "adversarial audit", "disprove"]),
        ("final-validation", 1195, "CURRENT_FACT_PRESERVATION", "stale-analysis", [], ["stale-analysis", "stale analysis"]),
        ("final-validation", 1195, "CONTINUATION_SUFFICIENCY", "test-evidence", [], ["373/373", "373 tests", "tests passed"]),
        ("final-validation", 1195, "CONTINUATION_SUFFICIENCY", "large-optimizer", [], ["101,250", "101250"]),
        ("final-validation", 1195, "CONTINUATION_SUFFICIENCY", "runtime-evidence", [], ["36.9 seconds", "36.9s", "36,788 triangles"]),
        ("final-validation", 1195, "CONTINUATION_SUFFICIENCY", "export-evidence", [], ["33,120,308", "valid 33,120,308-byte", "valid"]),
        ("final-validation", 1195, "CONTINUATION_SUFFICIENCY", "audit-completion", ["rounds five and six"], []),
    ]
    return [
        {
            "check_id": f"spot-{index:03d}",
            "checkpoint": checkpoint,
            "source_end_sequence": source_end,
            "source_refs": [f"panoref_evt_{source_end:06d}"],
            "category": category,
            "expectation_id": expectation_id,
            "required_all": required_all,
            "required_any": required_any,
            "expected_current_fact": required_all + required_any,
        }
        for index, (checkpoint, source_end, category, expectation_id, required_all, required_any)
        in enumerate(specs, start=1)
    ]


def main() -> None:
    if OUT.exists():
        raise SystemExit(f"refusing to overwrite frozen fixture: {OUT}")
    if not full_trace.SESSION.exists():
        raise SystemExit(f"missing session: {full_trace.SESSION}")

    events, user_events, session_sha256 = selected_events()
    items = list(expand_source_items(events, selector_budget_tokens=SELECTOR_TARGET, safety_margin=0.8))
    chunks = _chunk_events(items, target_tokens=DIRECT_TARGET)
    checks = oracle_checks()
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
    plan = plan_rows(chunks)
    manifest = {
        "oracle_version": "panoref-direct-generalization-bounded-v1",
        "replay_sha256": hashlib.sha256(REPLAY.read_bytes()).hexdigest(),
        "session_path": str(full_trace.SESSION),
        "session_sha256": session_sha256,
        "event_count": len(events),
        "source_item_count": len(items),
        "planned_source_tokens": sum(estimate_tokens(json.dumps(source_item_payload(item), ensure_ascii=False)) for item in items),
        "user_checkpoints": user_events,
        "oracle_sha256": digest(checks),
        "oracle_basis": "authoritative PanoRef review, final request, and final validation evidence from the frozen source session; no new candidate output was inspected",
        "direct_target_tokens": DIRECT_TARGET,
        "selector_target_tokens": SELECTOR_TARGET,
        "raw_plan_hash": digest(plan),
        "spot_check_count": len(checks),
        "selection_policy": {
            "preserved_sequences": [REVIEW_SEQUENCE, FINAL_REQUEST_SEQUENCE],
            "preserved_tail_start_sequence": TAIL_START_SEQUENCE,
            "excluded_scope": "earlier PanoRef implementation work before the late correction/validation tail; this is a bounded terminal trajectory slice, not the full session",
        },
    }
    write_json(MANIFEST, manifest)
    write_json(OUT / "trace_index.json", {"session": str(full_trace.SESSION), "events": replay_rows, "user_events": user_events})
    write_json(RAW_PLAN, {
        "policy": "bounded PanoRef terminal trajectory, immutable event/span boundaries, target 12000 estimated payload tokens",
        "plan_hash": digest(plan),
        "chunks": plan,
        "source_items": [source_item_payload(item) for item in items],
    })
    write_json(OUT / "FREEZE_METADATA.json", manifest)
    (OUT / "semantic_oracle/JUDGE_RUBRIC.md").write_text(
        "# Bounded PanoRef semantic rubric\n\n"
        "This deterministic oracle was created before any new direct runs. It checks durable review constraints, the final requested work, and the final validation evidence in the authoritative source slice. A missing term is a conservative coverage miss, not a complete human semantic judgment.\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "FROZEN", **{key: manifest[key] for key in ("event_count", "source_item_count", "planned_source_tokens", "spot_check_count", "raw_plan_hash")}}, indent=2))


if __name__ == "__main__":
    main()
