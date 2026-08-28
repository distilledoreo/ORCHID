"""CapsuleEnvelopeV2: evidence-backed hybrid capsule schema and validation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..context import estimate_tokens
from ..db import canonical_json
from ..source_projection import (
    normalize_citation_match_text,
    normalize_citation_text,
    resolve_citation_source_id,
)

CAPSULE_SCHEMA_V2 = "orchid.capsule.v2"

ITEM_KINDS = frozenset(
    {
        "constraint",
        "decision",
        "state",
        "artifact",
        "completed",
        "failure",
        "blocker",
        "next_action",
    }
)
ITEM_STATUSES = frozenset({"active", "resolved", "superseded"})
RENDER_KIND_ORDER = (
    "constraint",
    "decision",
    "state",
    "blocker",
    "next_action",
    "artifact",
    "completed",
    "failure",
)
PROTECTED_ACTIVE_KINDS = frozenset(
    {"constraint", "decision", "state", "blocker", "next_action"}
)
_VALUE_NUMBERS = re.compile(r"\d+")
_LABEL_VALUE = re.compile(r"([^:\n]{1,40}):\s*([^.\n]+)")


def _grounding_tokens(text: str) -> list[str]:
    # Terminal styling is transport noise, not a factual numeric claim. Apply
    # the same normalization used for evidence before comparing value tokens.
    return list(_VALUE_NUMBERS.findall(normalize_citation_match_text(text)))


def _label_values(text: str) -> dict[str, str]:
    return {
        normalize_citation_match_text(match.group(1)).lower(): normalize_citation_match_text(
            match.group(2)
        ).lower()
        for match in _LABEL_VALUE.finditer(text)
    }


def _label_values_conflict(item_text: str, evidence: tuple[MemoryEvidence, ...]) -> bool:
    item_labels = _label_values(item_text)
    if not item_labels:
        return False
    excerpt_labels: dict[str, str] = {}
    for entry in evidence:
        excerpt_labels.update(_label_values(entry.excerpt))
    for key, value in item_labels.items():
        if key in excerpt_labels and excerpt_labels[key] != value:
            return True
    return False


def _excerpt_in_source(excerpt: str, source_text: str) -> bool:
    if excerpt in source_text:
        return True
    normalized = normalize_citation_text(excerpt)
    if normalized in source_text:
        return True
    match_excerpt = normalize_citation_match_text(excerpt)
    match_source = normalize_citation_match_text(source_text)
    if match_excerpt in match_source:
        return True
    if "..." in excerpt or "…" in excerpt:
        return False
    min_prefix = 24
    candidate = match_excerpt
    for _ in range(3):
        if len(candidate) < min_prefix:
            break
        parts = candidate.rsplit(" ", 1)
        if len(parts) < 2:
            break
        candidate = parts[0]
        if candidate in match_source:
            return True
    return False


def _excerpt_in_allowed_sources(
    excerpt: str,
    sources: dict[str, str],
    *,
    preferred_source_ids: tuple[str, ...] = (),
    allowed_source_ids: frozenset[str] | None = None,
) -> bool:
    ordered_ids: list[str] = []
    for source_id in preferred_source_ids:
        if source_id in sources and source_id not in ordered_ids:
            ordered_ids.append(source_id)
    if allowed_source_ids is not None:
        for source_id in sorted(allowed_source_ids):
            if source_id in sources and source_id not in ordered_ids:
                ordered_ids.append(source_id)
    else:
        for source_id in sources:
            if source_id not in ordered_ids:
                ordered_ids.append(source_id)
    return any(_excerpt_in_source(excerpt, sources[source_id]) for source_id in ordered_ids)


def _text_grounded_in_evidence(
    text: str,
    evidence: tuple[MemoryEvidence, ...],
    sources: dict[str, str],
) -> bool:
    combined = "\n".join(
        [
            *(normalize_citation_match_text(entry.excerpt) for entry in evidence),
            *(normalize_citation_match_text(sources.get(entry.source_id, "")) for entry in evidence),
        ]
    )
    for token in _grounding_tokens(text):
        if token not in combined:
            return False
    return True


@dataclass(frozen=True)
class MemoryEvidence:
    source_id: str
    excerpt: str


@dataclass(frozen=True)
class MemoryItemV2:
    id: str
    kind: str
    status: str
    text: str
    evidence: tuple[MemoryEvidence, ...]
    supersedes_item_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapsuleEnvelopeV2:
    schema_version: str
    generation: int
    items: tuple[MemoryItemV2, ...]


@dataclass(frozen=True)
class EnvelopeValidationResult:
    ok: bool
    violations: tuple[str, ...]
    rendered_tokens: int = 0


def memory_item_id(
    *,
    kind: str,
    text: str,
    evidence: Iterable[MemoryEvidence],
    supersedes_item_ids: Iterable[str] = (),
) -> str:
    payload = {
        "kind": kind,
        "text": text,
        "evidence": [
            {"source_id": item.source_id, "excerpt": item.excerpt}
            for item in evidence
        ],
        "supersedes_item_ids": sorted(supersedes_item_ids),
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"mem_{digest[:24]}"


def _parse_evidence(raw: Any) -> MemoryEvidence:
    if not isinstance(raw, dict):
        raise ValueError("evidence entry must be an object")
    source_id = raw.get("source_id")
    excerpt = raw.get("excerpt")
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("evidence.source_id must be a non-empty string")
    if not isinstance(excerpt, str) or not excerpt:
        raise ValueError("evidence.excerpt must be a non-empty string")
    return MemoryEvidence(source_id=source_id, excerpt=excerpt)


def _parse_item(raw: Any) -> MemoryItemV2:
    if not isinstance(raw, dict):
        raise ValueError("memory item must be an object")
    item_id = raw.get("id")
    kind = raw.get("kind")
    status = raw.get("status")
    text = raw.get("text")
    if not isinstance(item_id, str) or not item_id:
        raise ValueError("item.id must be a non-empty string")
    if not isinstance(kind, str) or kind not in ITEM_KINDS:
        raise ValueError(f"item.kind must be one of {sorted(ITEM_KINDS)}")
    if not isinstance(status, str) or status not in ITEM_STATUSES:
        raise ValueError(f"item.status must be one of {sorted(ITEM_STATUSES)}")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("item.text must be a non-empty string")
    evidence_raw = raw.get("evidence") or []
    if not isinstance(evidence_raw, list) or not evidence_raw:
        raise ValueError("item.evidence must be a non-empty array")
    evidence = tuple(_parse_evidence(entry) for entry in evidence_raw)
    supersede_raw = raw.get("supersedes_item_ids") or []
    if not isinstance(supersede_raw, list):
        raise ValueError("item.supersedes_item_ids must be an array")
    supersedes = tuple(
        item_id
        for item_id in supersede_raw
        if isinstance(item_id, str) and item_id
    )
    return MemoryItemV2(
        id=item_id,
        kind=kind,
        status=status,
        text=text.strip(),
        evidence=evidence,
        supersedes_item_ids=supersedes,
    )


def parse_envelope(raw: Any) -> CapsuleEnvelopeV2:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError("capsule envelope must be an object")
    schema_version = raw.get("schema_version")
    generation = raw.get("generation")
    items_raw = raw.get("items")
    if schema_version != CAPSULE_SCHEMA_V2:
        raise ValueError(f"schema_version must be {CAPSULE_SCHEMA_V2!r}")
    if not isinstance(generation, int) or generation < 1:
        raise ValueError("generation must be a positive integer")
    if not isinstance(items_raw, list):
        raise ValueError("items must be an array")
    items = tuple(_parse_item(item) for item in items_raw)
    return CapsuleEnvelopeV2(
        schema_version=CAPSULE_SCHEMA_V2,
        generation=generation,
        items=items,
    )


def envelope_to_dict(envelope: CapsuleEnvelopeV2) -> dict[str, Any]:
    return {
        "schema_version": envelope.schema_version,
        "generation": envelope.generation,
        "items": [
            {
                "id": item.id,
                "kind": item.kind,
                "status": item.status,
                "text": item.text,
                "evidence": [
                    {"source_id": evidence.source_id, "excerpt": evidence.excerpt}
                    for evidence in item.evidence
                ],
                "supersedes_item_ids": list(item.supersedes_item_ids),
            }
            for item in envelope.items
        ],
    }


def render_envelope(envelope: CapsuleEnvelopeV2) -> str:
    """Render only non-superseded items in a fixed section order."""

    active_items = [item for item in envelope.items if item.status != "superseded"]
    sections: list[str] = []
    for kind in RENDER_KIND_ORDER:
        kind_items = [item for item in active_items if item.kind == kind]
        if not kind_items:
            continue
        lines = [f"## {kind.replace('_', ' ').title()}"]
        for item in kind_items:
            lines.append(f"- [{item.id} status={item.status}] {item.text}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def validate_envelope(
    envelope: CapsuleEnvelopeV2,
    *,
    sources: dict[str, str],
    base_items: tuple[MemoryItemV2, ...] = (),
    allowed_source_ids: frozenset[str] | None = None,
    max_tokens: int = 2_048,
) -> EnvelopeValidationResult:
    violations: list[str] = []
    if envelope.schema_version != CAPSULE_SCHEMA_V2:
        violations.append("invalid schema_version")
    item_ids = [item.id for item in envelope.items]
    if len(set(item_ids)) != len(item_ids):
        violations.append("duplicate item ids")

    base_by_id = {item.id: item for item in base_items}
    new_by_id = {item.id: item for item in envelope.items}

    for item in envelope.items:
        previous = base_by_id.get(item.id)
        if previous is not None and previous == item:
            continue
        for evidence in item.evidence:
            resolved_source_id = evidence.source_id
            if allowed_source_ids is not None:
                resolved_source_id = (
                    resolve_citation_source_id(evidence.source_id, allowed_source_ids)
                    or evidence.source_id
                )
                if resolved_source_id not in allowed_source_ids:
                    violations.append(
                        f"unknown source id {evidence.source_id!r} for item {item.id}"
                    )
                    continue
            source_text = sources.get(resolved_source_id) or sources.get(
                evidence.source_id
            )
            if source_text is None:
                violations.append(
                    f"missing authoritative source for {evidence.source_id!r}"
                )
                continue
            if not _excerpt_in_allowed_sources(
                evidence.excerpt,
                sources,
                preferred_source_ids=(resolved_source_id,),
                allowed_source_ids=allowed_source_ids,
            ):
                violations.append(
                    f"excerpt not found in source {evidence.source_id!r} for item {item.id}"
                )
        if _label_values_conflict(item.text, item.evidence):
            violations.append(f"item {item.id} text is not grounded in cited evidence")
        elif not _text_grounded_in_evidence(item.text, item.evidence, sources):
            violations.append(f"item {item.id} text is not grounded in cited evidence")
        for superseded_id in item.supersedes_item_ids:
            if superseded_id not in base_by_id and superseded_id not in new_by_id:
                violations.append(
                    f"item {item.id} supersedes unknown id {superseded_id!r}"
                )

    for base_item in base_items:
        if base_item.kind not in PROTECTED_ACTIVE_KINDS:
            continue
        if base_item.status != "active":
            continue
        replacement = next(
            (
                item
                for item in envelope.items
                if base_item.id in item.supersedes_item_ids
            ),
            None,
        )
        carried = new_by_id.get(base_item.id)
        if replacement is None and carried is None:
            violations.append(
                f"protected active item {base_item.id} disappeared without supersession"
            )
        elif carried is not None and carried.text != base_item.text:
            violations.append(
                f"protected active item {base_item.id} changed without supersession"
            )

    rendered = render_envelope(envelope)
    rendered_tokens = estimate_tokens(rendered)
    if rendered_tokens > max_tokens:
        violations.append(
            f"rendered capsule exceeds token budget ({rendered_tokens}>{max_tokens})"
        )

    return EnvelopeValidationResult(
        ok=not violations,
        violations=tuple(violations),
        rendered_tokens=rendered_tokens,
    )


def assign_item_ids(
    candidates: Iterable[dict[str, Any]],
) -> tuple[MemoryItemV2, ...]:
    """Assign content-addressed IDs to consolidator candidate items."""

    items: list[MemoryItemV2] = []
    for raw in candidates:
        evidence = tuple(
            MemoryEvidence(
                source_id=str(entry["source_id"]),
                excerpt=str(entry["excerpt"]),
            )
            for entry in raw.get("evidence") or ()
            if isinstance(entry, dict)
        )
        supersedes = tuple(
            str(item_id)
            for item_id in raw.get("supersedes_item_ids") or ()
            if isinstance(item_id, str) and item_id
        )
        kind = str(raw.get("kind") or "")
        text = str(raw.get("text") or "").strip()
        status = str(raw.get("status") or "")
        item_id = memory_item_id(
            kind=kind,
            text=text,
            evidence=evidence,
            supersedes_item_ids=supersedes,
        )
        items.append(
            MemoryItemV2(
                id=item_id,
                kind=kind,
                status=status,
                text=text,
                evidence=evidence,
                supersedes_item_ids=supersedes,
            )
        )
    return tuple(items)
