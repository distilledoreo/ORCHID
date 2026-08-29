from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from memory_gateway.capsule_admission import (
    CapsuleAdmissionResult,
    DeterministicSemanticVerifier,
    RejectingSemanticVerifier,
    admit_capsule_v2,
    build_verification_payload,
    synthesize_candidate_items,
)
from memory_gateway.capsule_envelope import (
    CAPSULE_SCHEMA_V2,
    CapsuleEnvelopeV2,
    MemoryEvidence,
    MemoryItemV2,
    envelope_to_dict,
)
from memory_gateway.compaction import (
    CompactionResult,
    CompactionWorker,
    Event,
    compute_input_hash,
)
from memory_gateway.db import SQLiteStore
from memory_gateway.ingestion import append_novel_messages
from memory_gateway.source_projection import authoritative_source_text


def _event(event_id: str, content: str, sequence: int = 1) -> Event:
    return Event(
        id=event_id,
        sequence=sequence,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        event_type="user_message",
        role="user",
    )


def _result(
    *,
    content: str,
    events: list[Event],
    memory_items: tuple[dict, ...] = (),
) -> CompactionResult:
    covered = tuple(event.id for event in events)
    return CompactionResult(
        content=content,
        covered_event_ids=covered,
        evidence_event_ids=covered,
        input_hash=compute_input_hash(None, events, events[-1].id if events else None),
        output_hash=hashlib.sha256(content.encode()).hexdigest(),
        model_identity="test",
        prompt_version="v1",
        generation_settings={},
        evidence_source_ids=covered,
        memory_items=memory_items,
    )


def test_admit_v2_accepts_grounded_items() -> None:
    events = [_event("evt_1", "Owner: Disti.")]
    items = (
        {
            "kind": "state",
            "status": "active",
            "text": "Owner: Disti.",
            "evidence": [{"source_id": "evt_1", "excerpt": "Owner: Disti."}],
        },
    )
    admission = asyncio.run(
        admit_capsule_v2(
            result=_result(content="Owner: Disti.", events=events, memory_items=items),
            generation=1,
            events=events,
            base_metadata=None,
            allowed_source_ids=frozenset({"evt_1"}),
            max_tokens=2048,
            verifier=DeterministicSemanticVerifier(),
        )
    )
    assert isinstance(admission, CapsuleAdmissionResult)
    assert admission.ok
    assert admission.envelope is not None
    assert "Disti" in admission.rendered_content


def test_admit_v2_canonicalizes_evidence_to_exact_unique_source_slice() -> None:
    events = [
        _event("evt_1::span::000000", "Unrelated source."),
        _event(
            "evt_2::span::000000",
            "The Latest Head adds 17 files.\n\nAll checks pass.",
            sequence=2,
        ),
    ]
    items = (
        {
            "kind": "state",
            "status": "active",
            "text": "The latest head adds 17 files.",
            "evidence": [
                {
                    "source_id": "evt_1::span::000000",
                    "excerpt": "the latest head adds 17 files.",
                }
            ],
        },
    )
    admission = asyncio.run(
        admit_capsule_v2(
            result=_result(content="latest state", events=events, memory_items=items),
            generation=1,
            events=events,
            base_metadata=None,
            allowed_source_ids=frozenset(event.id for event in events),
            max_tokens=2048,
            verifier=DeterministicSemanticVerifier(),
        )
    )

    assert admission.ok
    assert admission.deterministic_evidence_repairs == 1
    assert admission.metadata["deterministic_evidence_repairs"] == 1
    assert admission.envelope is not None
    evidence = admission.envelope.items[0].evidence[0]
    assert evidence.source_id == "evt_2::span::000000"
    assert evidence.excerpt == "The Latest Head adds 17 files."


def test_admit_v2_does_not_repair_paraphrased_evidence() -> None:
    events = [_event("evt_1", "If debug is enabled, return true.")]
    items = (
        {
            "kind": "state",
            "status": "active",
            "text": "Debug mode returns true.",
            "evidence": [
                {"source_id": "evt_1", "excerpt": "Debug mode returns the value."}
            ],
        },
    )
    admission = asyncio.run(
        admit_capsule_v2(
            result=_result(content="debug state", events=events, memory_items=items),
            generation=1,
            events=events,
            base_metadata=None,
            allowed_source_ids=frozenset({"evt_1"}),
            max_tokens=2048,
            verifier=DeterministicSemanticVerifier(),
        )
    )

    assert not admission.ok
    assert admission.deterministic_evidence_repairs == 0
    assert any(
        "excerpt not found" in violation
        for violation in admission.deterministic_violations
    )


def test_admit_v2_does_not_guess_between_ambiguous_source_matches() -> None:
    events = [
        _event("evt_1", "Unrelated source."),
        _event("evt_2", "Shared exact quote.", sequence=2),
        _event("evt_3", "Shared exact quote.", sequence=3),
    ]
    items = (
        {
            "kind": "state",
            "status": "active",
            "text": "Shared exact quote.",
            "evidence": [
                {"source_id": "evt_1", "excerpt": "Shared exact quote."}
            ],
        },
    )
    admission = asyncio.run(
        admit_capsule_v2(
            result=_result(content="shared quote", events=events, memory_items=items),
            generation=1,
            events=events,
            base_metadata=None,
            allowed_source_ids=frozenset(event.id for event in events),
            max_tokens=2048,
            verifier=DeterministicSemanticVerifier(),
        )
    )

    assert not admission.ok
    assert admission.deterministic_evidence_repairs == 0


def test_admit_v2_deterministically_carries_base_records_from_patch_only_result() -> None:
    base_item = MemoryItemV2(
        id="mem_owner",
        kind="state",
        status="active",
        text="Owner: Disti.",
        evidence=(MemoryEvidence(source_id="evt_old", excerpt="Owner: Disti."),),
    )
    base_envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(base_item,),
    )
    events = [_event("evt_new", "Lease Duration: 900 seconds.")]
    patch_items = (
        {
            "kind": "state",
            "status": "active",
            "text": "Lease Duration: 900 seconds.",
            "evidence": [
                {
                    "source_id": "evt_new",
                    "excerpt": "Lease Duration: 900 seconds.",
                }
            ],
        },
    )
    admission = asyncio.run(
        admit_capsule_v2(
            result=_result(
                content="Lease Duration: 900 seconds.",
                events=events,
                memory_items=patch_items,
            ),
            generation=2,
            events=events,
            base_metadata={
                "semantic_admission_ok": True,
                "envelope_json": __import__("json").dumps(
                    envelope_to_dict(base_envelope)
                ),
            },
            allowed_source_ids=frozenset({"evt_new"}),
            max_tokens=2048,
            verifier=DeterministicSemanticVerifier(),
        )
    )
    assert admission.ok
    assert admission.envelope is not None
    assert len(admission.envelope.items) == 2
    assert admission.envelope.items[0] == base_item
    assert admission.envelope.items[1].id != base_item.id
    assert "Owner: Disti." in admission.rendered_content
    assert "Lease Duration: 900 seconds." in admission.rendered_content


def test_admit_v2_rejects_false_identity() -> None:
    events = [_event("evt_1", "Owner: Disti.")]
    items = (
        {
            "kind": "state",
            "status": "active",
            "text": "Owner: Alice.",
            "evidence": [{"source_id": "evt_1", "excerpt": "Owner: Disti."}],
        },
    )
    admission = asyncio.run(
        admit_capsule_v2(
            result=_result(content="Owner: Alice.", events=events, memory_items=items),
            generation=1,
            events=events,
            base_metadata=None,
            allowed_source_ids=frozenset({"evt_1"}),
            max_tokens=2048,
        )
    )
    assert not admission.ok
    assert admission.error == "FAILED: semantic_rejection"


def test_admit_v2_rejects_unauthorized_chinese_script() -> None:
    events = [_event("evt_1", "Project: Orchid Memory Gateway.")]
    items = (
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
    )
    admission = asyncio.run(
        admit_capsule_v2(
            result=_result(
                content="项目: Orchid Memory Gateway.",
                events=events,
                memory_items=items,
            ),
            generation=1,
            events=events,
            base_metadata=None,
            allowed_source_ids=frozenset({"evt_1"}),
            max_tokens=2048,
        )
    )
    assert not admission.ok
    assert any(
        "unauthorized script" in violation
        for violation in admission.deterministic_violations
    )


def test_admit_v2_shadow_keeps_legacy_content() -> None:
    events = [_event("evt_1", "Owner: Disti.")]
    items = (
        {
            "kind": "state",
            "status": "active",
            "text": "Owner: Disti.",
            "evidence": [{"source_id": "evt_1", "excerpt": "Owner: Disti."}],
        },
    )
    admission = asyncio.run(
        admit_capsule_v2(
            result=_result(content="legacy prose body", events=events, memory_items=items),
            generation=1,
            events=events,
            base_metadata=None,
            allowed_source_ids=frozenset({"evt_1"}),
            max_tokens=2048,
            shadow_only=True,
        )
    )
    assert admission.ok
    assert admission.rendered_content == "legacy prose body"
    assert admission.envelope_json is not None


def test_synthesize_candidate_items_from_legacy_content() -> None:
    items = synthesize_candidate_items(
        content="Owner: Disti. Timezone: America/New_York.",
        evidence_event_ids=("evt_1",),
        sources={
            "evt_1": "Owner: Disti. Timezone: America/New_York.",
        },
    )
    assert len(items) == 2
    assert items[0]["text"].startswith("Owner: Disti.")


class _V2ShadowEngine:
    async def compact(
        self,
        *,
        base_capsule,
        events: list[Event],
        snapshot_end_event_id: str | None,
    ) -> CompactionResult:
        event = events[0]
        citation_text = authoritative_source_text(event.content)
        return _result(
            content="legacy promoted prose",
            events=events,
            memory_items=(
                {
                    "kind": "state",
                    "status": "active",
                    "text": citation_text,
                    "evidence": [{"source_id": event.id, "excerpt": citation_text}],
                },
            ),
        )


def test_worker_v2_shadow_stores_envelope_but_promotes_legacy(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project", memory_format="v2_shadow")
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[{"role": "user", "content": "Owner: Disti."}],
        request_id="r1",
        source="request",
    )
    job_id = store.request_coalesced_compaction_job(
        thread_id="thread",
        base_capsule_id=None,
        snapshot_start_event_id=store.list_events("thread")[0]["id"],
        snapshot_end_event_id=store.list_events("thread")[0]["id"],
        priority=1,
    )
    assert job_id is not None
    result = asyncio.run(
        CompactionWorker(
            store,
            _V2ShadowEngine(),
            "shadow-worker",
            memory_format="v2_shadow",
        ).run_once()
    )
    assert result == "PROMOTED"
    active = store.get_active_capsule("thread")
    assert active is not None
    assert active["content"] == "legacy promoted prose"
    metadata = __import__("json").loads(active["model_metadata_json"])
    assert metadata.get("semantic_admission_ok") is True
    assert metadata.get("shadow_only") is True
    assert metadata.get("envelope_json")


def test_build_verification_payload_includes_changed_items_and_excerpts() -> None:
    item = MemoryItemV2(
        id="mem_a",
        kind="state",
        status="active",
        text="Owner: Disti.",
        evidence=(MemoryEvidence(source_id="evt_1", excerpt="Owner: Disti."),),
    )
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=2,
        items=(item,),
    )
    payload = build_verification_payload(
        envelope=envelope,
        sources={"evt_1": "Owner: Disti."},
        base_items=(),
        changed_item_ids=("mem_a",),
        max_input_tokens=4096,
    )
    assert payload["changed_items"][0]["text"] == "Owner: Disti."
    assert payload["source_excerpts"]["evt_1"] == "Owner: Disti."


def test_build_verification_payload_never_silently_drops_changed_items() -> None:
    item = MemoryItemV2(
        id="mem_a",
        kind="state",
        status="active",
        text="Owner: Disti.",
        evidence=(MemoryEvidence(source_id="evt_1", excerpt="Owner: Disti."),),
    )
    envelope = CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=1,
        items=(item,),
    )

    with __import__("pytest").raises(ValueError, match="without safe truncation"):
        build_verification_payload(
            envelope=envelope,
            sources={"evt_1": "Owner: Disti."},
            base_items=(),
            changed_item_ids=("mem_a",),
            max_input_tokens=1,
        )


def test_admit_v2_rejects_when_semantic_verifier_rejects() -> None:
    events = [_event("evt_1", "Owner: Disti.")]
    items = (
        {
            "kind": "state",
            "status": "active",
            "text": "Owner: Disti.",
            "evidence": [{"source_id": "evt_1", "excerpt": "Owner: Disti."}],
        },
    )
    admission = asyncio.run(
        admit_capsule_v2(
            result=_result(content="Owner: Disti.", events=events, memory_items=items),
            generation=1,
            events=events,
            base_metadata=None,
            allowed_source_ids=frozenset({"evt_1"}),
            max_tokens=2048,
            verifier=RejectingSemanticVerifier(detail="unsupported identity"),
        )
    )
    assert not admission.ok
    assert admission.semantic_violations
    assert admission.error == "FAILED: semantic_rejection"


def test_worker_v2_shadow_promotes_legacy_when_semantic_verifier_rejects(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project", memory_format="v2_shadow")
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[{"role": "user", "content": "Owner: Disti."}],
        request_id="r1",
        source="request",
    )
    job_id = store.request_coalesced_compaction_job(
        thread_id="thread",
        base_capsule_id=None,
        snapshot_start_event_id=store.list_events("thread")[0]["id"],
        snapshot_end_event_id=store.list_events("thread")[0]["id"],
        priority=1,
    )
    assert job_id is not None
    result = asyncio.run(
        CompactionWorker(
            store,
            _V2ShadowEngine(),
            "reject-worker",
            memory_format="v2_shadow",
            capsule_verifier=RejectingSemanticVerifier(),
        ).run_once()
    )
    assert result == "PROMOTED"
    active = store.get_active_capsule("thread")
    assert active is not None
    assert active["content"] == "legacy promoted prose"
    metadata = __import__("json").loads(active["model_metadata_json"])
    assert metadata.get("semantic_admission_ok") is False
    assert metadata.get("shadow_only") is True
    assert metadata.get("semantic_violations")


def test_worker_strict_v2_fails_when_semantic_verifier_rejects(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project", memory_format="v2")
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[{"role": "user", "content": "Owner: Disti."}],
        request_id="r1",
        source="request",
    )
    event_id = store.list_events("thread")[0]["id"]
    job_id = store.request_coalesced_compaction_job(
        thread_id="thread",
        base_capsule_id=None,
        snapshot_start_event_id=event_id,
        snapshot_end_event_id=event_id,
        priority=1,
    )
    assert job_id is not None

    result = asyncio.run(
        CompactionWorker(
            store,
            _V2ShadowEngine(),
            "reject-worker",
            capsule_verifier=RejectingSemanticVerifier(),
        ).run_once()
    )

    assert result == "FAILED"
    assert store.get_active_capsule("thread") is None


def test_rejected_shadow_envelope_is_not_a_recursive_base() -> None:
    from memory_gateway.capsule_admission import base_items_from_capsule_metadata

    rejected = {
        "semantic_admission_ok": False,
        "envelope_json": __import__("json").dumps(
            {
                "schema_version": CAPSULE_SCHEMA_V2,
                "generation": 1,
                "items": [
                    {
                        "id": "mem_rejected",
                        "kind": "state",
                        "status": "active",
                        "text": "Owner: Alice.",
                        "evidence": [
                            {"source_id": "evt_1", "excerpt": "Owner: Disti."}
                        ],
                        "supersedes_item_ids": [],
                    }
                ],
            }
        ),
    }

    assert base_items_from_capsule_metadata(rejected) == ()


def test_build_capsule_verifier_rejects_explicitly_without_backend() -> None:
    from memory_gateway.capsule_admission import build_capsule_verifier
    from memory_gateway.config import RuntimeConfig

    verifier = build_capsule_verifier(
        RuntimeConfig(
            db_path=":memory:",
            backend_url="http://127.0.0.1:1",
            backend_api_key=None,
            backend_model=None,
            selector_url="http://127.0.0.1:1/v1",
            selector_model="selector",
            selector_api_key=None,
            canonicalizer_url="http://127.0.0.1:1/v1",
            canonicalizer_model="canonicalizer",
            canonicalizer_api_key=None,
            consolidator_url="http://127.0.0.1:1/v1",
            consolidator_model="consolidator",
            consolidator_api_key=None,
            context_tokens=128_000,
            background_fraction=0.5,
            urgent_fraction=0.8,
            worker_poll_seconds=0.1,
            model_timeout_seconds=1,
            memory_format="v2_shadow",
        )
    )
    assert isinstance(verifier, RejectingSemanticVerifier)


def test_worker_v2_promotes_rendered_envelope(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project", memory_format="v2")
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[{"role": "user", "content": "Owner: Disti."}],
        request_id="r1",
        source="request",
    )
    job_id = store.request_coalesced_compaction_job(
        thread_id="thread",
        base_capsule_id=None,
        snapshot_start_event_id=store.list_events("thread")[0]["id"],
        snapshot_end_event_id=store.list_events("thread")[0]["id"],
        priority=1,
    )
    assert job_id is not None
    result = asyncio.run(
        CompactionWorker(
            store,
            _V2ShadowEngine(),
            "v2-worker",
        ).run_once()
    )
    assert result == "PROMOTED"
    active = store.get_active_capsule("thread")
    assert active is not None
    assert active["content"] != "legacy promoted prose"
    assert "DERIVED FACTUAL MEMORY" in active["content"] or "State" in active["content"]
    assert "Disti" in active["content"]
    metadata = __import__("json").loads(active["model_metadata_json"])
    assert metadata.get("semantic_admission_ok") is True
    assert metadata.get("shadow_only") is False
    assert metadata.get("envelope_json")


def test_thread_memory_format_is_immutable_on_ensure(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.ensure_project_and_thread("project", "thread", memory_format="v1")
    store.ensure_project_and_thread("project", "thread", memory_format="v2_shadow")
    assert store.get_thread_memory_format("thread") == "v1"
