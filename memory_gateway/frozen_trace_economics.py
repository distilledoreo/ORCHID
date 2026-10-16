"""Deterministic token accounting for frozen-trace compaction optimizations.

Selector request shape stays frozen. This module only measures payload size
of already-landed cuts: source projection before the selector, and the
consolidator citation view after selection.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .compaction import (
    Event,
    expand_source_items,
    source_item_parent_event_id,
    source_item_payload,
)
from .context import estimate_tokens
from .pipeline_adapters import _chunk_events, _consolidator_source_payload
from .selector_contract import (
    CHUNK_TARGET_TOKENS,
    SERIAL_EXECUTION,
    SPAN_SAFETY_MARGIN,
)
from .source_projection import project_compaction_events

DEFAULT_FREETOSHOP_REPLAY = (
    Path(__file__).resolve().parents[1]
    / "artifacts/agent_benchmarks/freetoshop_operability_hardening"
    / "frozen_freetoshop_replay/events.jsonl"
)
SELECTOR_CHUNK_TARGET_TOKENS = CHUNK_TARGET_TOKENS


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_tokens(payload: Any) -> int:
    return estimate_tokens(_dumps(payload))


def load_frozen_events(path: Path) -> tuple[Event, ...]:
    events: list[Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        event_id = row.get("event_id") or row.get("id")
        if not isinstance(event_id, str):
            continue
        events.append(
            Event(
                id=event_id,
                sequence=int(row["sequence"]),
                content=str(row["content"]),
                content_hash=str(row["content_hash"]),
                event_type=str(row["event_type"]),
                role=row.get("role"),
            )
        )
    return tuple(events)


def _item_metrics(events: tuple[Event, ...] | list[Event]) -> dict[str, int]:
    items = expand_source_items(
        list(events),
        selector_budget_tokens=SELECTOR_CHUNK_TARGET_TOKENS,
        safety_margin=SPAN_SAFETY_MARGIN,
    )
    full_tokens = 0
    slim_tokens = 0
    for item in items:
        full = source_item_payload(item)
        full_tokens += payload_tokens(full)
        slim_tokens += payload_tokens(_consolidator_source_payload(full))
    chunks = _chunk_events(list(items), SELECTOR_CHUNK_TARGET_TOKENS)
    return {
        "source_items": len(items),
        "selector_chunks": len(chunks),
        "selector_event_payload_tokens": full_tokens,
        "consolidator_full_event_tokens": full_tokens,
        "consolidator_slim_event_tokens": slim_tokens,
    }


def organizer_exact_once_report(
    events: tuple[Event, ...] | list[Event],
    *,
    project_sources: bool = True,
) -> dict[str, Any]:
    """Account for every organizer unit without making model calls.

    One large raw event may expand into multiple deterministic source spans; that
    is part of the frozen selector contract. Exact-once therefore means each raw
    event enters the organizer plan once and every resulting whole-event/span ID
    is delivered to exactly one selector chunk, in order.
    """

    admitted_events = tuple(events)
    admitted_ids = [event.id for event in admitted_events]
    admission_counts = Counter(admitted_ids)
    organizer_events = (
        tuple(project_compaction_events(list(admitted_events)))
        if project_sources
        else admitted_events
    )
    source_items = expand_source_items(
        organizer_events,
        selector_budget_tokens=SELECTOR_CHUNK_TARGET_TOKENS,
        safety_margin=SPAN_SAFETY_MARGIN,
    )
    chunks = _chunk_events(list(source_items), SELECTOR_CHUNK_TARGET_TOKENS)
    planned_item_ids = [item.id for item in source_items]
    delivered_items = [item for chunk in chunks for item in chunk]
    delivered_item_ids = [item.id for item in delivered_items]
    delivery_counts = Counter(delivered_item_ids)
    parent_counts = Counter(
        source_item_parent_event_id(item) for item in source_items
    )
    admitted_id_set = set(admitted_ids)
    planned_id_set = set(planned_item_ids)
    delivered_id_set = set(delivered_item_ids)

    duplicate_event_admissions = {
        event_id: count
        for event_id, count in sorted(admission_counts.items())
        if count != 1
    }
    duplicate_source_item_plans = {
        item_id: count
        for item_id, count in sorted(Counter(planned_item_ids).items())
        if count != 1
    }
    duplicate_source_item_deliveries = {
        item_id: count
        for item_id, count in sorted(delivery_counts.items())
        if count != 1
    }
    missing_parent_events = sorted(admitted_id_set - set(parent_counts))
    unknown_parent_events = sorted(set(parent_counts) - admitted_id_set)
    missing_source_items = sorted(planned_id_set - delivered_id_set)
    unexpected_source_items = sorted(delivered_id_set - planned_id_set)
    ordered_sequences = all(
        previous.sequence < current.sequence
        for previous, current in zip(admitted_events, admitted_events[1:])
    )
    exact_once = not any(
        (
            duplicate_event_admissions,
            duplicate_source_item_plans,
            duplicate_source_item_deliveries,
            missing_parent_events,
            unknown_parent_events,
            missing_source_items,
            unexpected_source_items,
        )
    ) and delivered_item_ids == planned_item_ids and ordered_sequences

    return {
        "schema_version": "orchid.organizer_exact_once.v1",
        "project_sources": project_sources,
        "serial_execution": SERIAL_EXECUTION,
        "raw_events": len(admitted_events),
        "unique_raw_event_ids": len(admitted_id_set),
        "source_items": len(source_items),
        "selector_chunks": len(chunks),
        "raw_event_admission_counts": dict(sorted(admission_counts.items())),
        "raw_event_source_item_counts": dict(sorted(parent_counts.items())),
        "source_item_delivery_counts": dict(sorted(delivery_counts.items())),
        "chunk_source_item_counts": [len(chunk) for chunk in chunks],
        "duplicate_event_admissions": duplicate_event_admissions,
        "duplicate_source_item_plans": duplicate_source_item_plans,
        "duplicate_source_item_deliveries": duplicate_source_item_deliveries,
        "missing_parent_events": missing_parent_events,
        "unknown_parent_events": unknown_parent_events,
        "missing_source_items": missing_source_items,
        "unexpected_source_items": unexpected_source_items,
        "delivery_order_preserved": delivered_item_ids == planned_item_ids,
        "event_sequence_ordered": ordered_sequences,
        "exact_once": exact_once,
    }


def measure_compaction_payloads(events: tuple[Event, ...] | list[Event]) -> dict[str, Any]:
    """Compare raw vs projected selector/consolidator payloads without model calls."""

    raw = _item_metrics(events)
    projected_events = project_compaction_events(list(events))
    projected = _item_metrics(projected_events)
    slim_saved = raw["consolidator_full_event_tokens"] - raw["consolidator_slim_event_tokens"]
    projected_selector_saved = (
        raw["selector_event_payload_tokens"] - projected["selector_event_payload_tokens"]
    )
    return {
        "events": len(events),
        "selector_contract": {
            "chunk_target_tokens": SELECTOR_CHUNK_TARGET_TOKENS,
            "serial": SERIAL_EXECUTION,
            "request_shape_unchanged": True,
        },
        "raw": raw,
        "projected": projected,
        "savings": {
            "consolidator_slim_vs_full_tokens": slim_saved,
            "selector_projected_vs_raw_tokens": projected_selector_saved,
            "selector_chunks_raw": raw["selector_chunks"],
            "selector_chunks_projected": projected["selector_chunks"],
        },
    }


def replay_freetoshop_frozen_trace(
    path: Path = DEFAULT_FREETOSHOP_REPLAY,
) -> dict[str, Any]:
    events = load_frozen_events(path)
    report = measure_compaction_payloads(events)
    report["organizer_exact_once"] = organizer_exact_once_report(events)
    report["replay_path"] = str(path)
    report["replay_present"] = path.exists()
    return report
