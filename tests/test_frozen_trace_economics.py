from __future__ import annotations

import json

from memory_gateway.compaction import Event
from memory_gateway.db import content_hash
from memory_gateway.frozen_trace_economics import (
    DEFAULT_FREETOSHOP_REPLAY,
    measure_compaction_payloads,
    organizer_exact_once_report,
    replay_freetoshop_frozen_trace,
)


def _event(content: str, *, sequence: int = 1, event_type: str = "message") -> Event:
    return Event(
        id=f"evt_{sequence}",
        sequence=sequence,
        content=content,
        content_hash=content_hash(content),
        event_type=event_type,
        role="tool" if event_type == "tool_result" else "user",
    )


def test_slim_citation_view_drops_envelope_fields() -> None:
    envelope = json.dumps(
        {
            "output": [
                {"type": "text", "text": "Clone Stamp is the current tool"},
                {"type": "text", "text": "hash-only protocol dump " + ("ABCD" * 80)},
            ]
        }
    )
    report = measure_compaction_payloads([_event(envelope)])
    assert report["selector_contract"]["request_shape_unchanged"] is True
    assert report["raw"]["consolidator_slim_event_tokens"] < report["raw"][
        "consolidator_full_event_tokens"
    ]


def test_source_projection_shrinks_selector_payload_without_changing_chunk_contract() -> None:
    source = "import { Layer } from './types.js';\n" + ("export const x = 1;\n" * 80)
    tool = json.dumps({"content": source, "role": "tool", "tool_call_id": "call_1"})
    report = measure_compaction_payloads(
        [_event(tool, event_type="tool_result")]
    )
    assert report["projected"]["selector_event_payload_tokens"] < report["raw"][
        "selector_event_payload_tokens"
    ]
    assert report["selector_contract"]["chunk_target_tokens"] == 1_200
    assert report["selector_contract"]["serial"] is True


def test_frozen_freetoshop_replay_slim_and_projection_cut_tokens() -> None:
    assert DEFAULT_FREETOSHOP_REPLAY.exists()
    report = replay_freetoshop_frozen_trace(DEFAULT_FREETOSHOP_REPLAY)
    assert report["events"] == 199
    assert report["savings"]["consolidator_slim_vs_full_tokens"] > 0
    assert report["savings"]["selector_projected_vs_raw_tokens"] > 0
    assert report["projected"]["selector_chunks"] <= report["raw"]["selector_chunks"]
    assert report["projected"]["consolidator_slim_event_tokens"] < report["raw"][
        "consolidator_full_event_tokens"
    ]
    exact_once = report["organizer_exact_once"]
    assert exact_once["exact_once"] is True
    assert exact_once["raw_events"] == 199
    assert exact_once["unique_raw_event_ids"] == 199
    assert exact_once["source_items"] == 216
    assert exact_once["selector_chunks"] == 63
    assert set(exact_once["raw_event_source_item_counts"]) == set(
        exact_once["raw_event_admission_counts"]
    )
    assert set(exact_once["source_item_delivery_counts"].values()) == {1}


def test_exact_once_accounts_for_deterministic_spans_without_calling_them_duplicates() -> None:
    large = _event("export const value = 1;\n" * 1_000)
    report = organizer_exact_once_report([large])
    assert report["exact_once"] is True
    assert report["raw_events"] == 1
    assert report["source_items"] > 1
    assert report["raw_event_source_item_counts"] == {
        large.id: report["source_items"]
    }
    assert set(report["source_item_delivery_counts"].values()) == {1}


def test_exact_once_fails_closed_on_duplicate_raw_event_identity() -> None:
    first = _event("first", sequence=1)
    duplicate = Event(
        id=first.id,
        sequence=2,
        content="second",
        content_hash=content_hash("second"),
        event_type="message",
        role="user",
    )
    report = organizer_exact_once_report([first, duplicate], project_sources=False)
    assert report["exact_once"] is False
    assert report["duplicate_event_admissions"] == {first.id: 2}
