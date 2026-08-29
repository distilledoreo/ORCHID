"""Offline CapsuleEnvelopeV2 admission helpers for batch semantic gates."""

from __future__ import annotations

from typing import Any, Protocol

from ..compaction import CompactionResult, Event, compute_input_hash
from ..db import content_hash
from ..selector_contract import load_golden_request_hashes
from ..source_projection import authoritative_source_text, tool_inner_text
from .admit import (
    DeterministicSemanticVerifier,
    SemanticVerifier,
    admit_capsule_v2,
    synthesize_candidate_items,
)


class AdmissionReport(Protocol):
    generation: int
    ok: bool
    error: str | None


def event_from_selector_payload(raw: dict[str, Any]) -> Event:
    text = str(raw.get("content") or "")
    event_id = str(raw["id"])
    return Event(
        id=event_id,
        sequence=int(raw.get("sequence") or 1),
        content=text,
        content_hash=str(raw.get("content_hash") or content_hash(text)),
        event_type=str(raw.get("event_type") or "conversation_message"),
        role=raw.get("role"),
    )


def readable_event_text(content: str, *, max_chars: int = 2_000) -> str:
    text = tool_inner_text(content).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def synthesize_per_event_items(
    events: list[Event],
    *,
    max_chars: int = 1_500,
) -> tuple[dict[str, Any], ...]:
    """One grounded memory item per authoritative selector event."""

    per_event_chars = max(240, max_chars // max(len(events), 1))
    items: list[dict[str, Any]] = []
    for event in events:
        source = authoritative_source_text(event.content)
        if not source.strip():
            continue
        excerpt = source if len(source) <= per_event_chars else source[:per_event_chars].rsplit(" ", 1)[0] or source[:per_event_chars]
        items.append(
            {
                "kind": "state",
                "status": "active",
                "text": excerpt,
                "evidence": [{"source_id": event.id, "excerpt": excerpt}],
                "supersedes_item_ids": [],
            }
        )
    return tuple(items)


def build_generation_document(
    *,
    generation: int,
    events: list[Event],
    content: str | None = None,
    items: tuple[dict[str, Any], ...] | None = None,
    synthesis: str = "sentences",
) -> dict[str, Any]:
    sources = {event.id: authoritative_source_text(event.content) for event in events}
    prose = content
    memory_items = items
    if prose is None:
        prose = " ".join(
            part
            for part in (readable_event_text(event.content) for event in events)
            if part
        )
    if memory_items is None:
        if synthesis == "per_event":
            memory_items = synthesize_per_event_items(events)
        else:
            memory_items = synthesize_candidate_items(
                content=prose,
                evidence_event_ids=tuple(event.id for event in events),
                sources=sources,
            )
    return {
        "generation": generation,
        "content": prose,
        "events": [
            {
                "id": event.id,
                "sequence": event.sequence,
                "content": event.content,
                "content_hash": event.content_hash,
                "event_type": event.event_type,
                "role": event.role,
            }
            for event in events
        ],
        "items": [dict(item) for item in memory_items],
    }


def compaction_result_from_generation(raw: dict[str, Any], events: list[Event]) -> CompactionResult:
    content = str(raw.get("content") or "")
    covered = tuple(event.id for event in events)
    evidence = tuple(str(item) for item in raw.get("evidence_event_ids") or covered)
    memory_items = tuple(dict(item) for item in raw.get("items") or ())
    return CompactionResult(
        content=content,
        covered_event_ids=covered,
        evidence_event_ids=evidence,
        input_hash=str(
            raw.get("input_hash")
            or compute_input_hash(None, events, events[-1].id if events else None)
        ),
        output_hash=str(raw.get("output_hash") or content_hash(content)),
        model_identity=str(raw.get("model_identity") or "offline"),
        prompt_version=str(raw.get("prompt_version") or "offline"),
        generation_settings=dict(raw.get("generation_settings") or {}),
        evidence_source_ids=tuple(
            str(item) for item in raw.get("evidence_source_ids") or evidence
        ),
        memory_items=memory_items,
    )


async def process_v2_document(
    document: dict[str, Any],
    *,
    verifier: SemanticVerifier | None = None,
) -> list[dict[str, Any]]:
    generations = document.get("generations")
    if not isinstance(generations, list):
        raise ValueError("document must contain a generations array")
    verifier = verifier or DeterministicSemanticVerifier()
    reports: list[dict[str, Any]] = []
    base_metadata = document.get("base_metadata")
    for index, generation in enumerate(generations, start=1):
        if not isinstance(generation, dict):
            continue
        events = [
            event_from_selector_payload(item)
            for item in generation.get("events") or ()
            if isinstance(item, dict)
        ]
        result = compaction_result_from_generation(generation, events)
        admission = await admit_capsule_v2(
            result=result,
            generation=int(generation.get("generation") or index),
            events=events,
            base_metadata=base_metadata if isinstance(base_metadata, dict) else None,
            allowed_source_ids=frozenset(event.id for event in events),
            max_tokens=int(document.get("capsule_max_tokens") or 2048),
            verifier=verifier,
            shadow_only=bool(document.get("shadow_only", True)),
            require_items=bool(document.get("require_items", False)),
        )
        if admission.ok and admission.envelope_json:
            base_metadata = {
                "envelope_json": admission.envelope_json,
                "semantic_admission_ok": True,
            }
        reports.append(
            {
                "generation": int(generation.get("generation") or index),
                "ok": admission.ok,
                "error": admission.error,
                "deterministic_violations": list(admission.deterministic_violations),
                "deterministic_evidence_repairs": (
                    admission.deterministic_evidence_repairs
                ),
                "semantic_violations": list(admission.semantic_violations),
                "rendered_content": admission.rendered_content,
                "envelope_json": admission.envelope_json,
            }
        )
    return reports


def build_document_from_selector_chunks(
    chunks: list[dict[str, Any]],
    *,
    capsule_max_tokens: int = 2_048,
    shadow_only: bool = True,
    require_items: bool = False,
    synthesis: str = "per_event",
) -> dict[str, Any]:
    generations: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        events = [
            event_from_selector_payload(item)
            for item in chunk.get("events") or ()
            if isinstance(item, dict)
        ]
        if not events:
            continue
        generations.append(
            build_generation_document(
                generation=int(chunk.get("chunk_index", index - 1)) + 1,
                events=events,
                synthesis=synthesis,
            )
        )
    return {
        "schema_version": "orchid.v2_offline_gate.selector_chunks.v1",
        "capsule_max_tokens": capsule_max_tokens,
        "shadow_only": shadow_only,
        "require_items": require_items,
        "generations": generations,
    }


async def run_selector_chunk_gate(
    *,
    verifier: SemanticVerifier | None = None,
    chunks: list[dict[str, Any]] | None = None,
    shadow_only: bool = True,
    require_items: bool = False,
) -> dict[str, Any]:
    chunk_rows = chunks if chunks is not None else load_golden_request_hashes()["chunks"]
    reports: list[dict[str, Any]] = []
    for chunk in chunk_rows:
        document = build_document_from_selector_chunks(
            [chunk],
            shadow_only=shadow_only,
            require_items=require_items,
        )
        chunk_reports = await process_v2_document(document, verifier=verifier)
        for report in chunk_reports:
            report = dict(report)
            report["chunk_index"] = int(chunk.get("chunk_index", len(reports)))
            reports.append(report)
    rejected = [report for report in reports if not report["ok"]]
    return {
        "schema_version": "orchid.v2_offline_gate.report.v1",
        "chunk_count": len(chunk_rows),
        "generation_count": len(reports),
        "accepted": sum(1 for report in reports if report["ok"]),
        "rejected": len(rejected),
        "shadow_only": shadow_only,
        "require_items": require_items,
        "reports": reports,
        "rejections": [
            {
                "chunk_index": report.get("chunk_index"),
                "generation": report["generation"],
                "error": report["error"],
                "deterministic_violations": report["deterministic_violations"],
                "semantic_violations": report["semantic_violations"],
            }
            for report in rejected
        ],
    }


def summarize_reports(reports: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "generation_count": len(reports),
        "accepted": sum(1 for report in reports if report["ok"]),
        "rejected": sum(1 for report in reports if not report["ok"]),
    }
