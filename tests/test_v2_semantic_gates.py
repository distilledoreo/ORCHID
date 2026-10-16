"""Phase 2 semantic gate suite for CapsuleEnvelopeV2 admission."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from memory_gateway.capsule_admission import DeterministicSemanticVerifier, admit_capsule_v2
from memory_gateway.compaction import CompactionResult, Event, compute_input_hash

_FIXTURE = Path(__file__).parent / "fixtures" / "v2_offline_generations.json"


def _event(event_id: str, content: str, sequence: int = 1) -> Event:
    return Event(
        id=event_id,
        sequence=sequence,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        event_type="user_message",
        role="user",
    )


def _result(*, content: str, events: list[Event], memory_items: tuple[dict, ...]) -> CompactionResult:
    covered = tuple(event.id for event in events)
    return CompactionResult(
        content=content,
        covered_event_ids=covered,
        evidence_event_ids=covered,
        input_hash=compute_input_hash(None, events, events[-1].id if events else None),
        output_hash=hashlib.sha256(content.encode()).hexdigest(),
        model_identity="gate-suite",
        prompt_version="v1",
        generation_settings={},
        evidence_source_ids=covered,
        memory_items=memory_items,
    )


@pytest.mark.parametrize(
    ("source_text", "content", "items", "expect_ok"),
    [
        (
            "Owner: Disti.",
            "Owner: Disti.",
            (
                {
                    "kind": "state",
                    "status": "active",
                    "text": "Owner: Disti.",
                    "evidence": [{"source_id": "evt_1", "excerpt": "Owner: Disti."}],
                },
            ),
            True,
        ),
        (
            "Owner: Disti.",
            "Owner: Alice.",
            (
                {
                    "kind": "state",
                    "status": "active",
                    "text": "Owner: Alice.",
                    "evidence": [{"source_id": "evt_1", "excerpt": "Owner: Disti."}],
                },
            ),
            False,
        ),
        (
            "Project: Orchid Memory Gateway.",
            "项目: Orchid Memory Gateway.",
            (
                {
                    "kind": "state",
                    "status": "active",
                    "text": "项目: Orchid Memory Gateway.",
                    "evidence": [
                        {
                            "source_id": "evt_1",
                            "excerpt": "Project: Orchid Memory Gateway.",
                        }
                    ],
                },
            ),
            False,
        ),
    ],
    ids=["grounded_owner", "false_identity", "unauthorized_chinese"],
)
def test_known_bad_capsule_gates(
    source_text: str,
    content: str,
    items: tuple[dict, ...],
    expect_ok: bool,
) -> None:
    events = [_event("evt_1", source_text)]
    admission = asyncio.run(
        admit_capsule_v2(
            result=_result(content=content, events=events, memory_items=items),
            generation=1,
            events=events,
            base_metadata=None,
            allowed_source_ids=frozenset({"evt_1"}),
            max_tokens=2048,
            verifier=DeterministicSemanticVerifier(),
            require_items=True,
        )
    )
    assert admission.ok is expect_ok
    if not expect_ok:
        assert admission.error == "FAILED: semantic_rejection"


def test_offline_fixture_generation1_accepts_generation2_rejects() -> None:
    document = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    base_metadata = None
    for index, generation in enumerate(document["generations"], start=1):
        events = [
            _event(
                str(raw["id"]),
                str(raw["content"]),
                sequence=int(raw.get("sequence") or 1),
            )
            for raw in generation["events"]
        ]
        items = tuple(dict(item) for item in generation.get("items") or ())
        admission = asyncio.run(
            admit_capsule_v2(
                result=_result(
                    content=str(generation["content"]),
                    events=events,
                    memory_items=items,
                ),
                generation=int(generation.get("generation") or index),
                events=events,
                base_metadata=base_metadata,
                allowed_source_ids=frozenset(event.id for event in events),
                max_tokens=int(document.get("capsule_max_tokens") or 2048),
                verifier=DeterministicSemanticVerifier(),
                shadow_only=bool(document.get("shadow_only", True)),
                require_items=False,
            )
        )
        if index == 1:
            assert admission.ok
            base_metadata = {"envelope_json": admission.envelope_json}
        else:
            assert not admission.ok
            assert admission.error == "FAILED: semantic_rejection"
