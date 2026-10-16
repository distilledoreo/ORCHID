from __future__ import annotations

import pytest

from memory_gateway.capsule_envelope import (
    CAPSULE_SCHEMA_V2,
    CapsuleEnvelopeV2,
    MemoryEvidence,
    MemoryItemV2,
    assign_item_ids,
    envelope_to_dict,
    memory_item_id,
    parse_envelope,
    render_envelope,
    validate_envelope,
)


def _item(
    *,
    item_id: str = "mem_a",
    kind: str = "state",
    status: str = "active",
    text: str = "Lease Duration: 900 seconds.",
    source_id: str = "evt_1",
    excerpt: str = "lease_seconds is 900",
) -> MemoryItemV2:
    return MemoryItemV2(
        id=item_id,
        kind=kind,
        status=status,
        text=text,
        evidence=(MemoryEvidence(source_id=source_id, excerpt=excerpt),),
    )


def test_render_envelope_omits_superseded_items() -> None:
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=2,
        items=(
            _item(item_id="mem_old", status="superseded", text="Lease Duration: 30 seconds."),
            _item(text="Lease Duration: 900 seconds."),
        ),
    )
    rendered = render_envelope(envelope)
    assert "900 seconds" in rendered
    assert "30 seconds" not in rendered


def test_validate_envelope_rejects_abbreviated_ellipsis_excerpts() -> None:
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(
            _item(
                excerpt="if (debug) { ... }",
            ),
        ),
    )
    result = validate_envelope(
        envelope,
        sources={"evt_1": "if (debug) { return true; }"},
        allowed_source_ids=frozenset({"evt_1"}),
    )
    assert not result.ok
    assert any("excerpt not found" in violation for violation in result.violations)


def test_validate_envelope_accepts_literal_spread_operator_excerpts() -> None:
    source = "const merged = { ...defaults, enabled: true };"
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(
            _item(
                text="Spread defaults into merged object.",
                excerpt="...defaults",
            ),
        ),
    )
    result = validate_envelope(
        envelope,
        sources={"evt_1": source},
        allowed_source_ids=frozenset({"evt_1"}),
    )
    assert result.ok


def test_validate_envelope_rejects_ungrounded_excerpt() -> None:
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(_item(excerpt="database is Postgres"),),
    )
    result = validate_envelope(
        envelope,
        sources={"evt_1": "database is SQLite"},
        allowed_source_ids=frozenset({"evt_1"}),
    )
    assert not result.ok
    assert any("excerpt not found" in violation for violation in result.violations)


def test_validate_envelope_accepts_literal_escape_excerpts() -> None:
    source = "// line one\nif (debug) {"
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(
            MemoryItemV2(
                id="mem_a",
                kind="state",
                status="active",
                text="debug branch",
                evidence=(MemoryEvidence(source_id="evt_1", excerpt="// line one\\nif (debug) {"),),
            ),
        ),
    )
    result = validate_envelope(
        envelope,
        sources={"evt_1": source},
        allowed_source_ids=frozenset({"evt_1"}),
    )
    assert result.ok


def test_validate_envelope_rejects_unknown_source_id() -> None:
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(_item(source_id="evt_missing"),),
    )
    result = validate_envelope(
        envelope,
        sources={"evt_1": "lease_seconds is 900"},
        allowed_source_ids=frozenset({"evt_1"}),
    )
    assert not result.ok
    assert any("unknown source id" in violation for violation in result.violations)


def test_validate_envelope_accepts_bare_event_id_with_span_alias() -> None:
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(_item(source_id="panoref_evt_000936", excerpt="lease_seconds is 900"),),
    )
    result = validate_envelope(
        envelope,
        sources={
            "panoref_evt_000936": "lease_seconds is 900",
            "panoref_evt_000936::span::000000": "lease_seconds is 900",
        },
        allowed_source_ids=frozenset({"panoref_evt_000936::span::000000"}),
    )
    assert result.ok


def test_validate_envelope_accepts_truncated_word_boundary_excerpt() -> None:
    source = "Every triangle is expanded in memory for fast ray tests."
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(
            _item(
                text="Every triangle is expanded in memory.",
                excerpt="Every triangle is expanded in",
            ),
        ),
    )
    result = validate_envelope(
        envelope,
        sources={"evt_1": source},
        allowed_source_ids=frozenset({"evt_1"}),
    )
    assert result.ok


def test_validate_envelope_rejects_excerpt_attributed_to_sibling_source() -> None:
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(
            _item(
                text="Automated gate green with 371 unit/integration tests.",
                source_id="panoref_evt_000936",
                excerpt="The complete automated gate is green: 371 unit/integration tests",
            ),
        ),
    )
    result = validate_envelope(
        envelope,
        sources={
            "panoref_evt_000936::span::000000": "LocalAddress 127.0.0.1 4173",
            "panoref_evt_000937::span::000000": (
                "The complete automated gate is green: 371 unit/integration tests all pass."
            ),
        },
        allowed_source_ids=frozenset(
            {
                "panoref_evt_000936::span::000000",
                "panoref_evt_000937::span::000000",
            }
        ),
    )
    assert not result.ok
    assert any("excerpt not found" in violation for violation in result.violations)


def test_validate_envelope_skips_citation_checks_for_unchanged_carried_items() -> None:
    carried = _item(
        item_id="mem_carried",
        source_id="panoref_evt_000923::span::000001",
        excerpt="continuous visibility scores",
        text="Dual-projector blending uses continuous visibility scores.",
    )
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=2,
        items=(
            carried,
            _item(
                item_id="mem_new",
                source_id="panoref_evt_000934::span::000000",
                excerpt="Exit code: 0",
                text="Tests passed with exit code 0.",
            ),
        ),
    )
    result = validate_envelope(
        envelope,
        sources={"panoref_evt_000934::span::000000": "Exit code: 0"},
        base_items=(carried,),
        allowed_source_ids=frozenset({"panoref_evt_000934::span::000000"}),
    )
    assert result.ok


def test_validate_envelope_rejects_disappearing_protected_base_item() -> None:
    base = _item(item_id="mem_base", kind="constraint", text="Owner: Disti.")
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=2,
        items=(),
    )
    result = validate_envelope(
        envelope,
        sources={"evt_1": "Owner: Disti."},
        base_items=(base,),
        allowed_source_ids=frozenset({"evt_1"}),
    )
    assert not result.ok
    assert any("disappeared without supersession" in violation for violation in result.violations)


def test_validate_envelope_accepts_explicit_supersession() -> None:
    base = _item(
        item_id="mem_base",
        kind="state",
        text="Lease Duration: 30 seconds.",
        excerpt="lease_seconds is 30",
    )
    replacement = MemoryItemV2(
        id="mem_new",
        kind="state",
        status="active",
        text="Lease Duration: 900 seconds.",
        evidence=(MemoryEvidence(source_id="evt_2", excerpt="lease_seconds is 900"),),
        supersedes_item_ids=("mem_base",),
    )
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=2,
        items=(replacement, MemoryItemV2(
            id="mem_base",
            kind="state",
            status="superseded",
            text="Lease Duration: 30 seconds.",
            evidence=base.evidence,
        )),
    )
    result = validate_envelope(
        envelope,
        sources={
            "evt_1": "lease_seconds is 30",
            "evt_2": "lease_seconds is 900",
        },
        base_items=(base,),
        allowed_source_ids=frozenset({"evt_1", "evt_2"}),
    )
    assert result.ok


def test_validate_envelope_rejects_false_numeric_claim_without_evidence() -> None:
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(
            _item(
                kind="state",
                text="Lease Duration: 9000 seconds.",
                excerpt="lease_seconds is 900",
            ),
        ),
    )
    result = validate_envelope(
        envelope,
        sources={"evt_1": "lease_seconds is 900"},
        allowed_source_ids=frozenset({"evt_1"}),
    )
    assert not result.ok
    assert any("not grounded in cited evidence" in violation for violation in result.violations)


def test_validate_envelope_ignores_ansi_style_numbers_in_grounding() -> None:
    source = "\x1b[32m✓\x1b[39m 51 tests passed in 44.71s"
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(
            _item(
                text=source,
                excerpt=source,
            ),
        ),
    )

    result = validate_envelope(
        envelope,
        sources={"evt_1": source},
        allowed_source_ids=frozenset({"evt_1"}),
    )

    assert result.ok


def test_validate_envelope_rejects_false_identity_without_evidence() -> None:
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(
            _item(
                kind="state",
                text="Owner: Alice.",
                excerpt="Owner: Disti.",
            ),
        ),
    )
    result = validate_envelope(
        envelope,
        sources={"evt_1": "Owner: Disti."},
        allowed_source_ids=frozenset({"evt_1"}),
    )
    assert not result.ok
    assert any("not grounded in cited evidence" in violation for violation in result.violations)


def test_assign_item_ids_is_content_addressed() -> None:
    items = assign_item_ids(
        [
            {
                "kind": "state",
                "status": "active",
                "text": "Timezone: America/New_York.",
                "evidence": [
                    {"source_id": "evt_1", "excerpt": "America/New_York"},
                ],
            }
        ]
    )
    assert len(items) == 1
    expected = memory_item_id(
        kind="state",
        text="Timezone: America/New_York.",
        evidence=(MemoryEvidence(source_id="evt_1", excerpt="America/New_York"),),
    )
    assert items[0].id == expected


def test_parse_and_round_trip_envelope_dict() -> None:
    payload = envelope_to_dict(
        CapsuleEnvelopeV2(
            schema_version=CAPSULE_SCHEMA_V2,
            generation=3,
            items=(_item(),),
        )
    )
    envelope = parse_envelope(payload)
    assert envelope.generation == 3
    assert envelope.items[0].text.startswith("Lease Duration")


def test_validate_envelope_rejects_oversized_rendering() -> None:
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(_item(text="x" * 12_000, excerpt="x" * 20),),
    )
    result = validate_envelope(
        envelope,
        sources={"evt_1": "x" * 20},
        allowed_source_ids=frozenset({"evt_1"}),
        max_tokens=64,
    )
    assert not result.ok
    assert any("exceeds token budget" in violation for violation in result.violations)


def test_parse_envelope_rejects_bad_schema() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        parse_envelope({"schema_version": "v1", "generation": 1, "items": []})
