"""CapsuleEnvelopeV2 admission: deterministic validation and semantic verification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from ..compaction import CompactionResult, Event
from ..context import estimate_tokens
from ..db import canonical_json
from ..source_projection import (
    augment_source_text_aliases,
    authoritative_source_text,
    exact_citation_excerpt,
    resolve_citation_source_id,
)
from .envelope import (
    CAPSULE_SCHEMA_V2,
    CapsuleEnvelopeV2,
    MemoryItemV2,
    assign_item_ids,
    envelope_to_dict,
    parse_envelope,
    render_envelope,
    validate_envelope,
)

_CJK = re.compile(r"[\u4e00-\u9fff]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

CAPSULE_VERIFIER_SYSTEM_PROMPT = (
    "You verify CapsuleEnvelopeV2 memory items for semantic admission. "
    "Return JSON only with "
    '{"accept":true,"violations":[]} or {"accept":false,"violations":[{"type":"...",'
    '"item_id":"...","detail":"..."}]}. '
    "Reject when any changed item is not fully supported by its exact excerpts, "
    "introduces unsupported identity or language, drops a protected active fact "
    "without explicit supersession, or adds ungrounded state. "
    "Do not repair or rewrite items."
)

VERIFICATION_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "capsule_verification_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "accept": {"type": "boolean"},
                "violations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": [
                                    "ungrounded_item",
                                    "unsupported_identity",
                                    "missing_protected_fact",
                                    "unauthorized_language",
                                    "other",
                                ],
                            },
                            "item_id": {"type": "string"},
                            "detail": {"type": "string"},
                        },
                        "required": ["type", "detail"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["accept", "violations"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class SemanticVerificationResult:
    accept: bool
    violations: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class CapsuleAdmissionResult:
    ok: bool
    rendered_content: str
    envelope: CapsuleEnvelopeV2 | None
    envelope_json: str | None
    deterministic_violations: tuple[str, ...] = ()
    semantic_violations: tuple[dict[str, Any], ...] = ()
    deterministic_evidence_repairs: int = 0
    shadow_only: bool = False
    error: str | None = None

    @property
    def metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "memory_format": (
                CAPSULE_SCHEMA_V2 if self.envelope is not None else "orchid.capsule.v1"
            ),
            "semantic_admission_ok": self.ok,
            "shadow_only": self.shadow_only,
        }
        if self.envelope is not None:
            payload["envelope_generation"] = self.envelope.generation
            payload["envelope_item_count"] = len(self.envelope.items)
            payload["envelope_rendered_tokens"] = estimate_tokens(self.rendered_content)
        if self.deterministic_violations:
            payload["deterministic_violations"] = list(self.deterministic_violations)
        if self.semantic_violations:
            payload["semantic_violations"] = list(self.semantic_violations)
        if self.deterministic_evidence_repairs:
            payload["deterministic_evidence_repairs"] = (
                self.deterministic_evidence_repairs
            )
        if self.error:
            payload["semantic_rejection"] = self.error
        return payload


class SemanticVerifier(Protocol):
    async def verify(
        self,
        *,
        envelope: CapsuleEnvelopeV2,
        rendered_content: str,
        sources: dict[str, str],
        base_items: tuple[MemoryItemV2, ...],
        changed_item_ids: tuple[str, ...],
    ) -> SemanticVerificationResult:
        ...


class DeterministicSemanticVerifier:
    """Accept when deterministic validation already passed."""

    async def verify(
        self,
        *,
        envelope: CapsuleEnvelopeV2,
        rendered_content: str,
        sources: dict[str, str],
        base_items: tuple[MemoryItemV2, ...],
        changed_item_ids: tuple[str, ...],
    ) -> SemanticVerificationResult:
        return SemanticVerificationResult(accept=True)


@dataclass
class CloudCapsuleVerifier:
    """Bounded cloud verification using the reference main model."""

    client: Any
    max_input_tokens: int = 4_096

    async def verify(
        self,
        *,
        envelope: CapsuleEnvelopeV2,
        rendered_content: str,
        sources: dict[str, str],
        base_items: tuple[MemoryItemV2, ...],
        changed_item_ids: tuple[str, ...],
    ) -> SemanticVerificationResult:
        if not changed_item_ids:
            return SemanticVerificationResult(accept=True)
        payload = build_verification_payload(
            envelope=envelope,
            sources=sources,
            base_items=base_items,
            changed_item_ids=changed_item_ids,
            max_input_tokens=self.max_input_tokens,
        )
        response = await self.client.complete_json(payload)
        return parse_verification_response(response)


@dataclass
class RejectingSemanticVerifier:
    """Test helper that always rejects with a typed violation."""

    detail: str = "semantic verifier rejected candidate"
    violation_type: str = "other"

    async def verify(
        self,
        *,
        envelope: CapsuleEnvelopeV2,
        rendered_content: str,
        sources: dict[str, str],
        base_items: tuple[MemoryItemV2, ...],
        changed_item_ids: tuple[str, ...],
    ) -> SemanticVerificationResult:
        item_id = changed_item_ids[0] if changed_item_ids else (
            envelope.items[0].id if envelope.items else ""
        )
        return SemanticVerificationResult(
            accept=False,
            violations=(
                {
                    "type": self.violation_type,
                    "item_id": item_id,
                    "detail": self.detail,
                },
            ),
        )


def _relevant_base_items(
    envelope: CapsuleEnvelopeV2,
    base_items: tuple[MemoryItemV2, ...],
) -> tuple[MemoryItemV2, ...]:
    superseded_ids = {
        superseded_id
        for item in envelope.items
        for superseded_id in item.supersedes_item_ids
    }
    return tuple(
        item
        for item in base_items
        if item.id in superseded_ids or item.status == "active"
    )


def _item_payload(item: MemoryItemV2, *, include_evidence: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": item.id,
        "kind": item.kind,
        "status": item.status,
        "text": item.text,
    }
    if include_evidence:
        payload["evidence"] = [
            {"source_id": evidence.source_id, "excerpt": evidence.excerpt}
            for evidence in item.evidence
        ]
        payload["supersedes_item_ids"] = list(item.supersedes_item_ids)
    return payload


def build_verification_payload(
    *,
    envelope: CapsuleEnvelopeV2,
    sources: dict[str, str],
    base_items: tuple[MemoryItemV2, ...],
    changed_item_ids: tuple[str, ...],
    max_input_tokens: int = 4_096,
) -> dict[str, Any]:
    if max_input_tokens <= 0:
        raise ValueError("max_input_tokens must be positive")
    changed_ids = set(changed_item_ids)
    changed_items = [
        item for item in envelope.items if item.id in changed_ids and item.status != "superseded"
    ]
    missing_changed_ids = changed_ids - {item.id for item in envelope.items}
    if missing_changed_ids:
        raise ValueError(
            "verification payload references missing changed item(s): "
            + ", ".join(sorted(missing_changed_ids))
        )
    relevant_base = _relevant_base_items(envelope, base_items)
    cited_excerpts: dict[str, list[str]] = {}
    for item in changed_items:
        for evidence in item.evidence:
            if evidence.source_id not in sources:
                continue
            excerpts = cited_excerpts.setdefault(evidence.source_id, [])
            if evidence.excerpt not in excerpts:
                excerpts.append(evidence.excerpt)
    payload: dict[str, Any] = {
        "task": "verify_capsule_envelope_v2",
        "generation": envelope.generation,
        "changed_items": [_item_payload(item, include_evidence=True) for item in changed_items],
        "base_items": [_item_payload(item, include_evidence=False) for item in relevant_base],
        # Deterministic admission has already proven these are exact source
        # substrings. Sending the witnesses, rather than entire raw sources,
        # keeps semantic verification bounded without deleting evidence.
        "source_excerpts": {
            source_id: "\n\n".join(cited_excerpts[source_id])
            for source_id in sorted(cited_excerpts)
        },
    }
    estimated_tokens = estimate_tokens(canonical_json(payload))
    if estimated_tokens > max_input_tokens:
        raise ValueError(
            "verification payload exceeds token budget without safe truncation "
            f"({estimated_tokens}>{max_input_tokens})"
        )
    return payload


def build_capsule_verifier(
    config: Any,
    *,
    telemetry_recorder: Any | None = None,
) -> SemanticVerifier | None:
    memory_format = getattr(config, "memory_format", "v1")
    if memory_format == "v1":
        return None
    if not getattr(config, "capsule_verification_enabled", True):
        return DeterministicSemanticVerifier()
    backend_url = getattr(config, "backend_url", None)
    backend_model = getattr(config, "backend_model", None)
    if not backend_url or not backend_model:
        return RejectingSemanticVerifier(
            detail="capsule verifier unavailable: backend URL and model are required",
        )
    from ..transport.structured import OpenAICompatStructuredClient

    return CloudCapsuleVerifier(
        OpenAICompatStructuredClient(
            endpoint=backend_url,
            model=backend_model,
            prompt_version="capsule-verifier-v1",
            system_prompt=CAPSULE_VERIFIER_SYSTEM_PROMPT,
            response_format=VERIFICATION_RESPONSE_FORMAT,
            generation_settings={
                "temperature": 0,
                "top_p": 1,
                "stream": False,
                "reasoning_effort": "none",
                "max_tokens": getattr(config, "capsule_verification_output_tokens", 512),
            },
            timeout=getattr(config, "model_timeout_seconds", 120.0),
            api_key=getattr(config, "backend_api_key", None),
            telemetry_recorder=telemetry_recorder,
        ),
        max_input_tokens=getattr(config, "capsule_verification_input_tokens", 4_096),
    )


def build_source_text_map(events: list[Event]) -> dict[str, str]:
    return augment_source_text_aliases(
        {event.id: authoritative_source_text(event.content) for event in events}
    )


def canonicalize_candidate_evidence(
    candidate_items: tuple[dict[str, Any], ...],
    *,
    sources: dict[str, str],
    allowed_source_ids: frozenset[str],
) -> tuple[tuple[dict[str, Any], ...], int]:
    """Replace formatting-normalized quotes with exact stored substrings.

    A quote may be relinked to another source in the same immutable delta only
    when that source is the unique full normalized match. No fuzzy or semantic
    repair is permitted, so unsupported evidence continues to fail admission.
    """

    repaired_items: list[dict[str, Any]] = []
    repair_count = 0
    for candidate in candidate_items:
        item = dict(candidate)
        raw_evidence = candidate.get("evidence")
        if not isinstance(raw_evidence, list):
            repaired_items.append(item)
            continue
        repaired_evidence: list[Any] = []
        for raw_entry in raw_evidence:
            if not isinstance(raw_entry, dict):
                repaired_evidence.append(raw_entry)
                continue
            entry = dict(raw_entry)
            source_id = entry.get("source_id")
            excerpt = entry.get("excerpt")
            if not isinstance(source_id, str) or not isinstance(excerpt, str):
                repaired_evidence.append(entry)
                continue
            resolved = resolve_citation_source_id(source_id, allowed_source_ids)
            preferred = resolved if resolved in allowed_source_ids else None
            selected: tuple[str, str] | None = None
            matches: list[tuple[str, str]] = []
            if preferred is not None:
                exact = exact_citation_excerpt(excerpt, sources.get(preferred, ""))
                if exact is not None:
                    selected = (preferred, exact)
            if selected is None:
                for candidate_source_id in sorted(allowed_source_ids):
                    if candidate_source_id == preferred:
                        continue
                    exact = exact_citation_excerpt(
                        excerpt,
                        sources.get(candidate_source_id, ""),
                    )
                    if exact is not None:
                        matches.append((candidate_source_id, exact))
            if selected is None and len(matches) == 1:
                selected = matches[0]
            if selected is not None:
                selected_source_id, exact = selected
                if selected_source_id != source_id or exact != excerpt:
                    repair_count += 1
                entry["source_id"] = selected_source_id
                entry["excerpt"] = exact
            repaired_evidence.append(entry)
        item["evidence"] = repaired_evidence
        repaired_items.append(item)
    return tuple(repaired_items), repair_count


def base_items_from_capsule_metadata(
    model_metadata: dict[str, Any] | None,
) -> tuple[MemoryItemV2, ...]:
    if not model_metadata:
        return ()
    if model_metadata.get("semantic_admission_ok") is not True:
        return ()
    raw = model_metadata.get("envelope_json")
    if not isinstance(raw, str) or not raw.strip():
        return ()
    try:
        envelope = parse_envelope(raw)
    except ValueError:
        return ()
    return tuple(item for item in envelope.items if item.status != "superseded")


def synthesize_candidate_items(
    *,
    content: str,
    evidence_event_ids: tuple[str, ...],
    sources: dict[str, str],
) -> tuple[dict[str, Any], ...]:
    """Best-effort bridge from legacy consolidator prose to V2 items."""

    if not content.strip():
        return ()
    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(content.strip()) if part.strip()]
    if not sentences:
        sentences = [content.strip()]
    candidates: list[dict[str, Any]] = []
    for sentence in sentences:
        evidence: list[dict[str, str]] = []
        for source_id in evidence_event_ids:
            source_text = sources.get(source_id, "")
            if not source_text:
                continue
            max_excerpt = min(240, len(source_text))
            truncated = source_text[:max_excerpt]
            fallback_excerpt = truncated.rsplit(" ", 1)[0] or truncated
            excerpt = sentence if sentence in source_text else fallback_excerpt
            if excerpt:
                evidence.append({"source_id": source_id, "excerpt": excerpt})
                break
        if not evidence and evidence_event_ids:
            source_id = evidence_event_ids[0]
            source_text = sources.get(source_id, "")
            if source_text:
                evidence.append(
                    {
                        "source_id": source_id,
                        "excerpt": (
                            source_text[: min(240, len(source_text))].rsplit(" ", 1)[0]
                            or source_text[: min(240, len(source_text))]
                        ),
                    }
                )
        if not evidence:
            continue
        candidates.append(
            {
                "kind": "state",
                "status": "active",
                "text": sentence,
                "evidence": evidence,
                "supersedes_item_ids": [],
            }
        )
    return tuple(candidates)


def _unauthorized_script_violations(
    envelope: CapsuleEnvelopeV2,
    sources: dict[str, str],
) -> tuple[str, ...]:
    rendered = render_envelope(envelope)
    cjk_chars = sorted(set(_CJK.findall(rendered)))
    if not cjk_chars:
        return ()
    source_blob = "\n".join(sources.values())
    violations: list[str] = []
    for char in cjk_chars:
        if char not in source_blob:
            violations.append(
                f"rendered capsule contains unauthorized script character {char!r}"
            )
    return tuple(violations)


def build_envelope(
    *,
    generation: int,
    candidate_items: tuple[dict[str, Any], ...],
    base_items: tuple[MemoryItemV2, ...],
) -> CapsuleEnvelopeV2:
    assigned = assign_item_ids(candidate_items)
    carried = [
        item
        for item in base_items
        if item.status == "active"
        and item.id not in {new.id for new in assigned}
        and not any(item.id in new.supersedes_item_ids for new in assigned)
    ]
    merged = tuple(carried) + assigned
    return CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=generation,
        items=merged,
    )


def _changed_item_ids(
    envelope: CapsuleEnvelopeV2,
    base_items: tuple[MemoryItemV2, ...],
) -> tuple[str, ...]:
    base_by_id = {item.id: item for item in base_items}
    changed: list[str] = []
    for item in envelope.items:
        previous = base_by_id.get(item.id)
        if previous is None or previous.text != item.text or previous.status != item.status:
            changed.append(item.id)
    return tuple(changed)


async def admit_capsule_v2(
    *,
    result: CompactionResult,
    generation: int,
    events: list[Event],
    base_metadata: dict[str, Any] | None,
    allowed_source_ids: frozenset[str],
    max_tokens: int,
    verifier: SemanticVerifier | None = None,
    shadow_only: bool = False,
    require_items: bool = False,
) -> CapsuleAdmissionResult:
    sources = build_source_text_map(events)
    base_items = base_items_from_capsule_metadata(base_metadata)
    candidate_items = tuple(result.memory_items)
    if not candidate_items:
        if require_items:
            return CapsuleAdmissionResult(
                ok=False,
                rendered_content=result.content,
                envelope=None,
                envelope_json=None,
                deterministic_violations=("consolidator returned no memory items",),
                shadow_only=shadow_only,
                error="FAILED: semantic_rejection",
            )
        candidate_items = synthesize_candidate_items(
            content=result.content,
            evidence_event_ids=result.evidence_source_ids or result.evidence_event_ids,
            sources=sources,
        )
    if not candidate_items:
        return CapsuleAdmissionResult(
            ok=False,
            rendered_content=result.content,
            envelope=None,
            envelope_json=None,
            deterministic_violations=("unable to derive memory items",),
            shadow_only=shadow_only,
            error="FAILED: semantic_rejection",
        )

    candidate_items, evidence_repairs = canonicalize_candidate_evidence(
        candidate_items,
        sources=sources,
        allowed_source_ids=allowed_source_ids,
    )
    envelope = build_envelope(
        generation=generation,
        candidate_items=candidate_items,
        base_items=base_items,
    )
    validation = validate_envelope(
        envelope,
        sources=sources,
        base_items=base_items,
        allowed_source_ids=allowed_source_ids,
        max_tokens=max_tokens,
    )
    script_violations = _unauthorized_script_violations(envelope, sources)
    deterministic_violations = validation.violations + script_violations
    if deterministic_violations:
        return CapsuleAdmissionResult(
            ok=False,
            rendered_content=result.content,
            envelope=envelope,
            envelope_json=canonical_json(envelope_to_dict(envelope)),
            deterministic_violations=deterministic_violations,
            deterministic_evidence_repairs=evidence_repairs,
            shadow_only=shadow_only,
            error="FAILED: semantic_rejection",
        )

    rendered = render_envelope(envelope)
    semantic = SemanticVerificationResult(accept=True)
    if verifier is not None:
        try:
            semantic = await verifier.verify(
                envelope=envelope,
                rendered_content=rendered,
                sources=sources,
                base_items=base_items,
                changed_item_ids=_changed_item_ids(envelope, base_items),
            )
        except Exception as error:
            semantic = SemanticVerificationResult(
                accept=False,
                violations=(
                    {
                        "type": "other",
                        "detail": f"capsule verifier error: {str(error)[:240]}",
                    },
                ),
            )
    if not semantic.accept:
        return CapsuleAdmissionResult(
            ok=False,
            rendered_content=result.content,
            envelope=envelope,
            envelope_json=canonical_json(envelope_to_dict(envelope)),
            semantic_violations=semantic.violations,
            deterministic_evidence_repairs=evidence_repairs,
            shadow_only=shadow_only,
            error="FAILED: semantic_rejection",
        )

    promotion_content = result.content if shadow_only else rendered
    return CapsuleAdmissionResult(
        ok=True,
        rendered_content=promotion_content,
        envelope=envelope,
        envelope_json=canonical_json(envelope_to_dict(envelope)),
        deterministic_evidence_repairs=evidence_repairs,
        shadow_only=shadow_only,
    )


def parse_verification_response(raw: Any) -> SemanticVerificationResult:
    if not isinstance(raw, dict):
        raise ValueError("verification response must be an object")
    accept = raw.get("accept")
    violations_raw = raw.get("violations")
    if not isinstance(accept, bool):
        raise ValueError("verification response must include boolean accept")
    if not isinstance(violations_raw, list):
        raise ValueError("verification response must include violations array")
    violations: list[dict[str, Any]] = []
    for entry in violations_raw:
        if isinstance(entry, dict):
            violations.append(dict(entry))
    return SemanticVerificationResult(accept=accept, violations=tuple(violations))
