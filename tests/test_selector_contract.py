from __future__ import annotations

from memory_gateway.selector_contract import (
    CHUNK_TARGET_TOKENS,
    SELECTOR_MODEL,
    SELECTOR_PROMPT_VERSION,
    SELECTOR_SYSTEM_PROMPT,
    build_chunk_payload,
    request_payload_hash,
    response_schema_hash,
    verify_golden_hashes,
)


def test_selector_contract_constants_are_frozen() -> None:
    assert SELECTOR_MODEL == "qwen3.5-4b@q6_k"
    assert SELECTOR_PROMPT_VERSION == "selector-v1"
    assert CHUNK_TARGET_TOKENS == 1_200
    assert "selected_event_ids" in SELECTOR_SYSTEM_PROMPT


def test_selector_request_hashes_are_deterministic() -> None:
    payload = build_chunk_payload(
        events=[{"id": "evt_a", "content": "alpha"}],
        event_ids=["evt_a"],
    )
    first = request_payload_hash(payload)
    second = request_payload_hash(payload)
    assert first == second
    assert response_schema_hash(["evt_a"]) == response_schema_hash(["evt_a"])


def test_frozen_selector_golden_hashes_match_production_contract() -> None:
    result = verify_golden_hashes()
    assert result["ok"], result["mismatches"]
