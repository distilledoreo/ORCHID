"""Tests for offline V2 semantic gates and CloudCapsuleVerifier."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from memory_gateway.capsule_admission import (
    CloudCapsuleVerifier,
    build_verification_payload,
    parse_verification_response,
)
from memory_gateway.capsule_envelope import CapsuleEnvelopeV2, MemoryEvidence, MemoryItemV2
from memory_gateway.selector_contract import load_golden_request_hashes
from memory_gateway.source_projection import authoritative_source_text
from memory_gateway.v2_offline_gate import (
    build_generation_document,
    event_from_selector_payload,
    run_selector_chunk_gate,
    synthesize_per_event_items,
)


@dataclass
class _MockStructuredClient:
    response: dict[str, Any]

    async def complete_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.last_payload = payload
        return dict(self.response)


def test_parse_verification_response_accepts_and_rejects() -> None:
    accepted = parse_verification_response({"accept": True, "violations": []})
    assert accepted.accept
    rejected = parse_verification_response(
        {
            "accept": False,
            "violations": [
                {"type": "unsupported_identity", "item_id": "mem_a", "detail": "bad"},
            ],
        }
    )
    assert not rejected.accept
    assert rejected.violations[0]["type"] == "unsupported_identity"


def test_cloud_capsule_verifier_calls_bounded_payload() -> None:
    item = MemoryItemV2(
        id="mem_a",
        kind="state",
        status="active",
        text="Owner: Disti.",
        evidence=(MemoryEvidence(source_id="evt_1", excerpt="Owner: Disti."),),
    )
    envelope = CapsuleEnvelopeV2(
        schema_version="orchid.capsule.v2",
        generation=1,
        items=(item,),
    )
    client = _MockStructuredClient(response={"accept": True, "violations": []})
    verifier = CloudCapsuleVerifier(client, max_input_tokens=4096)
    result = asyncio.run(
        verifier.verify(
            envelope=envelope,
            rendered_content="Owner: Disti.",
            sources={"evt_1": "Owner: Disti."},
            base_items=(),
            changed_item_ids=("mem_a",),
        )
    )
    assert result.accept
    assert client.last_payload["changed_items"][0]["text"] == "Owner: Disti."
    payload = build_verification_payload(
        envelope=envelope,
        sources={"evt_1": "Owner: Disti."},
        base_items=(),
        changed_item_ids=("mem_a",),
        max_input_tokens=4096,
    )
    assert payload["task"] == "verify_capsule_envelope_v2"


def test_frozen_selector_chunks_pass_offline_v2_gate() -> None:
    golden = load_golden_request_hashes()
    report = asyncio.run(run_selector_chunk_gate())
    assert report["chunk_count"] == len(golden["chunks"])
    assert report["rejected"] == 0
    assert report["accepted"] == report["chunk_count"]


def test_synthesize_per_event_items_uses_raw_source_excerpt() -> None:
    event = event_from_selector_payload(
        {
            "id": "evt_1",
            "sequence": 1,
            "content": '{"content":"Owner: Disti."}',
            "content_hash": "abc",
            "event_type": "message",
            "role": "user",
        }
    )
    items = synthesize_per_event_items([event])
    assert len(items) == 1
    source_text = authoritative_source_text(event.content)
    assert items[0]["evidence"][0]["excerpt"] in source_text
    assert items[0]["text"] == items[0]["evidence"][0]["excerpt"]


def test_build_generation_document_per_event_synthesis() -> None:
    event = event_from_selector_payload(
        {
            "id": "evt_1",
            "sequence": 1,
            "content": "Owner: Disti.",
            "content_hash": "abc",
            "event_type": "message",
            "role": "user",
        }
    )
    document = build_generation_document(generation=1, events=[event], synthesis="per_event")
    assert document["items"]
    assert document["items"][0]["text"] == "Owner: Disti."
