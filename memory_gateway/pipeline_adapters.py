from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .compaction import (
    Capsule,
    Event,
    RetireMemory,
    SourceItem,
    _hash,
    compute_source_event_hash,
    expand_source_items,
    source_item_payload,
)
from .config import RuntimeConfig
from .context import estimate_tokens
from .openai_adapter import ModelProtocolError, ModelTransportError
from .pipeline import (
    CanonicalizationBatchResult,
    CanonicalizationResult,
    CanonicalizerBatchError,
    ConsolidationResult,
    LosslessCompactionEngine,
    LosslessPacket,
    SelectionResult,
    build_canonicalization_result,
)
from .source_projection import authoritative_source_text
from .structured_client import OpenAICompatStructuredClient

PATCH_CONTEXT_MAX_RECORDS = 16
PATCH_CONTEXT_TEXT_LIMIT = 320
_RECORD_TOKEN = re.compile(r"[A-Za-z0-9_./:-]{3,}")
_PINNED_RECORD_KINDS = frozenset(
    {"constraint", "decision", "blocker", "next_action"}
)

SELECTOR_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "selector_response_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "selected_event_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["selected_event_ids"],
            "additionalProperties": False,
        },
    },
}


def selector_response_format_for_chunk(allowed_ids: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """Build a per-chunk selector schema that only permits exact in-chunk source IDs."""

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "selector_response_v1",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "selected_event_ids": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": list(allowed_ids),
                        },
                    },
                },
                "required": ["selected_event_ids"],
                "additionalProperties": False,
            },
        },
    }

CANONICALIZER_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "canonicalizer_response_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "canonical_text": {"type": "string"},
                "cited_source_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["canonical_text"],
            "additionalProperties": False,
        },
    },
}


def canonicalizer_response_format_for_batch(
    allowed_ids: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """Build a canonicalizer schema that permits only exact supplied source IDs."""

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "canonicalizer_response_v1",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "canonical_text": {"type": "string"},
                    "cited_source_refs": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(allowed_ids)},
                    },
                },
                "required": ["canonical_text"],
                "additionalProperties": False,
            },
        },
    }

CONSOLIDATOR_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "consolidator_response_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "evidence_event_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "retire": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "memory_type": {"type": "string"},
                            "importance": {"type": "number", "minimum": 0, "maximum": 1},
                            "evidence_event_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": [
                            "content",
                            "memory_type",
                            "importance",
                            "evidence_event_ids",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["content", "evidence_event_ids"],
            "additionalProperties": False,
        },
    },
}


def consolidator_response_format_for_sources(
    allowed_ids: tuple[str, ...] | list[str],
    *,
    require_memory_items: bool = False,
) -> dict[str, Any]:
    """Build a consolidator schema that permits only exact supplied source IDs."""

    citation_items: dict[str, Any] = {"type": "string"}
    citation_array: dict[str, Any] = {
        "type": "array",
        "items": citation_items,
    }
    if allowed_ids:
        citation_items["enum"] = list(allowed_ids)
    else:
        citation_array["maxItems"] = 0
    evidence_entry = {
        "type": "object",
        "properties": {
            "source_id": citation_items,
            "excerpt": {"type": "string", "minLength": 1, "maxLength": 512},
        },
        "required": ["source_id", "excerpt"],
        "additionalProperties": False,
    }
    memory_item = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": sorted(
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
                ),
            },
            "status": {
                "type": "string",
                "enum": ["active", "resolved", "superseded"],
            },
            "text": {"type": "string"},
            "evidence": {
                "type": "array",
                "items": evidence_entry,
                "minItems": 1,
            },
            "supersedes_item_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["kind", "status", "text", "evidence"],
        "additionalProperties": False,
    }
    items_schema: dict[str, Any] = {
        "type": "array",
        "items": memory_item,
    }
    if require_memory_items:
        items_schema["minItems"] = 1
    required_fields = ["content", "evidence_event_ids"]
    if require_memory_items:
        required_fields.append("items")
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "consolidator_response_v1",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "evidence_event_ids": citation_array,
                    "items": items_schema,
                    "retire": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "memory_type": {"type": "string"},
                                "importance": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 1,
                                },
                                "evidence_event_ids": citation_array,
                            },
                            "required": [
                                "content",
                                "memory_type",
                                "importance",
                                "evidence_event_ids",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": required_fields,
                "additionalProperties": False,
            },
        },
    }

DEFAULT_CANONICALIZER_INPUT_TOKENS = 8_192


def _event_payload(event: SourceItem) -> dict[str, Any]:
    return source_item_payload(event)


def _consolidator_source_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Expose only the citation surface used later by admission.

    Selector inputs remain byte-for-byte unchanged. Span envelope fields and
    raw content are omitted so the consolidator does not re-pay parent hashes,
    offsets, or nested protocol dumps.
    """

    raw_content = event.get("content")
    if not isinstance(raw_content, str) or not raw_content:
        raw_content = str(event.get("citation_text") or "")
    payload: dict[str, Any] = {
        "id": event.get("id"),
        "citation_text": authoritative_source_text(raw_content),
    }
    event_type = event.get("event_type")
    role = event.get("role")
    if isinstance(event_type, str) and event_type:
        payload["event_type"] = event_type
    if isinstance(role, str) and role:
        payload["role"] = role
    return payload


def _capsule_payload(
    capsule: Capsule | None,
    *,
    include_content: bool = True,
) -> dict[str, Any] | None:
    if capsule is None:
        return None
    payload = {
        "id": capsule.id,
        "thread_id": capsule.thread_id,
        "capsule_hash": capsule.capsule_hash,
        "covered_end_event_id": capsule.covered_end_event_id,
    }
    if include_content:
        payload["content"] = capsule.content
    return payload


def compose_incremental_content(
    *,
    model_content: str,
    settled_items: list[dict[str, Any]],
    patch_items: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> str:
    """Keep already-accepted item text without asking the model to rewrite it."""

    superseded_ids = {
        item_id
        for item in patch_items
        for item_id in item.get("supersedes_item_ids") or ()
        if isinstance(item_id, str)
    }
    carried: list[str] = []
    seen: set[str] = set()
    for item in settled_items:
        if item.get("id") in superseded_ids:
            continue
        text = str(item.get("text") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        carried.append(text)
    body = model_content.strip()
    missing = [text for text in carried if text not in body]
    if not missing:
        return model_content
    if not body:
        return "\n\n".join(missing)
    return "\n\n".join([*missing, body])


def settled_items_for_new_sources(
    items: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    new_source_ids: tuple[str, ...] | list[str],
) -> list[dict[str, Any]]:
    """Carry evidence-backed items whose sources are outside the new window."""

    new_ids = set(new_source_ids)
    settled: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence") or []
        if not isinstance(evidence, list):
            continue
        source_ids = {
            entry.get("source_id")
            for entry in evidence
            if isinstance(entry, dict) and isinstance(entry.get("source_id"), str)
        }
        if source_ids and source_ids.isdisjoint(new_ids):
            settled.append(dict(item))
    return settled


def _record_tokens(text: str) -> frozenset[str]:
    return frozenset(token.lower() for token in _RECORD_TOKEN.findall(text))


def base_record_context_for_packet(
    items: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    packet: LosslessPacket,
    *,
    max_records: int = PATCH_CONTEXT_MAX_RECORDS,
    text_limit: int = PATCH_CONTEXT_TEXT_LIMIT,
) -> list[dict[str, Any]]:
    """Return bounded prior-record context for a delta-only consolidation call.

    Full prior evidence and capsule prose stay out of the request. Potentially
    affected records expose only stable identity and compact semantic state so
    the model can emit a superseding patch when new evidence changes them.
    """

    if max_records <= 0:
        return []
    source_text = "\n".join(
        [
            packet.canonical_text,
            *(
                str(event.get("content") or event.get("citation_text") or "")
                for event in packet.authoritative_events
            ),
        ]
    )
    query_tokens = _record_tokens(source_text)
    ranked: list[tuple[int, int, int, dict[str, Any]]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get("status") == "superseded":
            continue
        item_id = item.get("id")
        text = item.get("text")
        kind = item.get("kind")
        status = item.get("status")
        if not all(isinstance(value, str) and value for value in (item_id, text, kind)):
            continue
        if not isinstance(status, str) or not status:
            continue
        overlap = len(_record_tokens(text) & query_tokens)
        pinned = int(kind in _PINNED_RECORD_KINDS and status == "active")
        if overlap == 0 and not pinned:
            continue
        context = {
            "id": item_id,
            "kind": kind,
            "status": status,
            "text": text[:text_limit],
        }
        ranked.append((overlap, pinned, index, context))
    selected = sorted(
        ranked,
        key=lambda row: (-row[0], -row[1], -row[2]),
    )[:max_records]
    return [row[3] for row in selected]


def _lossless_packet_payload(packet: LosslessPacket) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "selected_event_ids": list(packet.selected_event_ids),
        "authoritative_events": [
            _consolidator_source_payload(event) for event in packet.authoritative_events
        ],
        "packet_hash": packet.packet_hash,
    }
    if packet.canonical_text:
        payload["canonical_text"] = packet.canonical_text
    return payload


def _generation_settings(client: OpenAICompatStructuredClient) -> dict[str, Any]:
    return dict(client.generation_settings)


def _set_call_context(
    client: Any,
    *,
    stage: str,
    source_refs: tuple[str, ...],
    selector_chunk_index: int | None = None,
    canonicalizer_batch_index: int | None = None,
) -> None:
    setter = getattr(client, "set_call_context", None)
    if setter is not None:
        setter(
            stage=stage,
            source_refs=source_refs,
            selector_chunk_index=selector_chunk_index,
            canonicalizer_batch_index=canonicalizer_batch_index,
        )


def _mark_call_failed(client: Any, error: Exception) -> None:
    marker = getattr(client, "mark_call_failed", None)
    if marker is not None:
        marker(error)


def _check_call_ownership(client: Any) -> None:
    checker = getattr(client, "check_ownership", None)
    if checker is not None:
        checker()


def _require_exact_response_keys(
    response: Any,
    expected_keys: set[str],
    stage: str,
    *,
    optional_keys: set[str] | None = None,
) -> None:
    optional_keys = optional_keys or set()
    if (
        not isinstance(response, dict)
        or not expected_keys.issubset(response)
        or bool(set(response) - expected_keys - optional_keys)
    ):
        expected = ", ".join(sorted(expected_keys))
        raise ModelProtocolError(
            f"{stage} response must contain exactly: {expected}"
        )


def _chunk_events(
    events: list[SourceItem],
    target_tokens: int = 1_200,
) -> list[list[SourceItem]]:
    if target_tokens <= 0:
        raise ValueError("target_tokens must be positive")
    chunks: list[list[SourceItem]] = []
    current: list[SourceItem] = []
    current_tokens = 0
    for event in events:
        event_tokens = estimate_tokens(
            json.dumps(_event_payload(event), ensure_ascii=False)
        )
        if current and current_tokens + event_tokens > target_tokens:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(event)
        current_tokens += event_tokens
    if current:
        chunks.append(current)
    return chunks


@dataclass
class OpenAICompatSelectorEngine:
    client: OpenAICompatStructuredClient
    chunk_target_tokens: int = 1_200
    selector_context_tokens: int = 32_768
    span_safety_margin: float = 0.8
    chunk_telemetry: tuple[dict[str, Any], ...] = field(default_factory=tuple, init=False)

    def __post_init__(self) -> None:
        self.client.response_format = SELECTOR_RESPONSE_FORMAT

    async def select(self, *, events: list[Event]) -> SelectionResult:
        selected_ids: set[str] = set()
        telemetry: list[dict[str, Any]] = []
        self.chunk_telemetry = ()
        span_budget_tokens = min(
            max(1, int(self.chunk_target_tokens * self.span_safety_margin)),
            max(1, int(self.selector_context_tokens * self.span_safety_margin)),
        )
        span_safety_margin = span_budget_tokens / self.chunk_target_tokens
        try:
            source_items = expand_source_items(
                events,
                selector_budget_tokens=self.chunk_target_tokens,
                safety_margin=span_safety_margin,
            )
        except ValueError:
            # A deliberately tiny test budget can be smaller than the fixed
            # provenance envelope. Real configured budgets use the span path.
            source_items = tuple(events)
        chunks = _chunk_events(list(source_items), target_tokens=self.chunk_target_tokens)
        for chunk_index, chunk in enumerate(chunks):
            chunk_ids = [event.id for event in chunk]
            chunk_payload = {
                "events": [_event_payload(event) for event in chunk],
                "event_ids_in_order": chunk_ids,
                "source_ids_in_order": chunk_ids,
            }
            try:
                if hasattr(self.client, "last_telemetry"):
                    self.client.last_telemetry = None
                _set_call_context(
                    self.client,
                    stage="selector",
                    source_refs=tuple(chunk_ids),
                    selector_chunk_index=chunk_index,
                )
                _check_call_ownership(self.client)
                self.client.response_format = selector_response_format_for_chunk(chunk_ids)
                response = await self.client.complete_json(chunk_payload)
                _check_call_ownership(self.client)
                _require_exact_response_keys(
                    response,
                    {"selected_event_ids"},
                    "selector",
                )
                selected = response.get("selected_event_ids")
                if not isinstance(selected, list) or not all(
                    isinstance(item, str) for item in selected
                ):
                    raise ModelProtocolError(
                        "selector response must contain selected_event_ids as a string array"
                    )
                allowed = {event.id for event in chunk}
                if not set(selected).issubset(allowed):
                    raise ModelProtocolError(
                        "selector returned ID outside current chunk"
                    )
                selected_ids.update(selected)
                remember = getattr(self.client, "remember_validated_result", None)
                if remember is not None and not (
                    self.client.last_telemetry or {}
                ).get("cache_hit"):
                    remember(response)
            except Exception as error:
                _mark_call_failed(self.client, error)
                raise
            finally:
                chunk_record = dict(self.client.last_telemetry or {})
                chunk_record.update(
                    {
                        "chunk_index": chunk_index,
                        "chunk_event_ids": tuple(event.id for event in chunk),
                        "chunk_parent_event_ids": tuple(
                            event.id
                            if isinstance(event, Event)
                            else event.parent_event_id
                            for event in chunk
                        ),
                        "chunk_event_count": len(chunk),
                        "chunk_estimated_tokens": sum(
                            estimate_tokens(
                                json.dumps(_event_payload(event), ensure_ascii=False)
                            )
                            for event in chunk
                        ),
                    }
                )
                telemetry.append(chunk_record)
                self.chunk_telemetry = tuple(telemetry)
        ordered_selected = tuple(
            item.id for item in source_items if item.id in selected_ids
        )
        return SelectionResult(
            selected_event_ids=ordered_selected,
            input_hash=compute_source_event_hash(None, events),
            output_hash=_hash({"selected_event_ids": ordered_selected}),
            model_identity=self.client.model,
            prompt_version=self.client.prompt_version,
            generation_settings=_generation_settings(self.client),
        )

    @property
    def last_telemetry(self) -> dict[str, Any] | None:
        return self.client.last_telemetry


@dataclass
class OpenAICompatCanonicalizerEngine:
    client: OpenAICompatStructuredClient
    batch_target_tokens: int = DEFAULT_CANONICALIZER_INPUT_TOKENS
    batch_telemetry: tuple[dict[str, Any], ...] = field(default_factory=tuple, init=False)

    def __post_init__(self) -> None:
        self.client.response_format = CANONICALIZER_RESPONSE_FORMAT

    async def canonicalize(self, *, events: list[SourceItem]) -> CanonicalizationResult:
        self.batch_telemetry = ()
        batches = _chunk_events(list(events), target_tokens=self.batch_target_tokens)
        if not batches:
            batches = [[]]
        batch_results: list[CanonicalizationBatchResult] = []
        telemetry: list[dict[str, Any]] = []
        for batch_index, batch in enumerate(batches):
            batch_started = time.perf_counter()
            expected_ids = tuple(event.id for event in batch)
            estimated_tokens = sum(
                estimate_tokens(json.dumps(_event_payload(event), ensure_ascii=False))
                for event in batch
            )
            batch_result: CanonicalizationBatchResult | None = None
            failure: Exception | None = None
            try:
                if hasattr(self.client, "last_telemetry"):
                    self.client.last_telemetry = None
                _set_call_context(
                    self.client,
                    stage="canonicalizer",
                    source_refs=expected_ids,
                    canonicalizer_batch_index=batch_index,
                )
                self.client.response_format = canonicalizer_response_format_for_batch(
                    expected_ids
                )
                if estimated_tokens > self.batch_target_tokens:
                    raise ValueError(
                        "source item exceeds canonicalizer input budget "
                        f"({estimated_tokens} > {self.batch_target_tokens} estimated tokens)"
                    )
                _check_call_ownership(self.client)
                response = await self.client.complete_json(
                    {
                        "events": [_event_payload(event) for event in batch],
                    }
                )
                _check_call_ownership(self.client)
                _require_exact_response_keys(
                    response,
                    {"canonical_text"}
                    if "cited_source_refs" not in response
                    else {"canonical_text", "cited_source_refs"},
                    "canonicalizer",
                )
                canonical_text = response.get("canonical_text")
                if not isinstance(canonical_text, str):
                    raise ModelProtocolError(
                        "canonicalizer response must contain canonical_text as a string"
                    )
                cited_ids = response.get("cited_source_refs", [])
                if not isinstance(cited_ids, list) or not all(
                    isinstance(item, str) for item in cited_ids
                ):
                    raise ModelProtocolError(
                        "canonicalizer response must contain cited_source_refs "
                        "as a string array when present"
                    )
                unknown_ids = set(cited_ids) - set(expected_ids)
                if unknown_ids:
                    raise ModelProtocolError(
                        "canonicalizer returned unknown cited source reference(s): "
                        + ", ".join(sorted(unknown_ids))
                    )
                if len(set(cited_ids)) != len(cited_ids):
                    raise ModelProtocolError(
                        "canonicalizer returned duplicate cited source references"
                    )
                expected_positions = {
                    source_id: position
                    for position, source_id in enumerate(expected_ids)
                }
                cited_positions = [
                    expected_positions[source_id] for source_id in cited_ids
                ]
                if cited_positions != sorted(cited_positions):
                    raise ModelProtocolError(
                        "canonicalizer cited source references must remain "
                        "in supplied source order"
                    )
                batch_result = CanonicalizationBatchResult(
                    batch_index=batch_index,
                    covered_source_refs=expected_ids,
                    canonical_text=canonical_text,
                    input_hash=compute_source_event_hash(None, batch),
                    output_hash=_hash(
                        {
                            "canonical_text": canonical_text,
                            "covered_source_refs": list(expected_ids),
                            "cited_source_refs": list(cited_ids),
                        }
                    ),
                    cited_source_refs=tuple(cited_ids),
                )
                batch_results.append(batch_result)
                remember = getattr(self.client, "remember_validated_result", None)
                if remember is not None and not (
                    self.client.last_telemetry or {}
                ).get("cache_hit"):
                    remember(response)
            except Exception as error:
                failure = error
                _mark_call_failed(self.client, error)
                raise CanonicalizerBatchError(batch_index, error) from error
            finally:
                batch_record = dict(self.client.last_telemetry or {})
                request_profile = batch_record.get("request_profile") or {}
                metadata = batch_record.get("metadata") or {}
                transport_attempt = metadata.get("transport_attempt", 1)
                batch_record.update(
                    {
                        "batch_index": batch_index,
                        "batch_covered_source_refs": expected_ids,
                        "batch_source_event_ids": expected_ids,
                        "batch_event_count": len(batch),
                        "batch_estimated_tokens": estimated_tokens,
                        "actual_prompt_tokens": batch_record.get("input_tokens"),
                        "response_hash": batch_record.get("raw_response_hash"),
                        "batch_wall_ms": (time.perf_counter() - batch_started) * 1000,
                        "inference_wall_ms": batch_record.get("wall_ms"),
                        "queue_wait_ms": None,
                        "retry_count": max(0, int(transport_attempt) - 1),
                        "structured_output_valid": batch_result is not None,
                        "timeout": isinstance(failure, ModelTransportError)
                        and "timed out" in str(failure).lower(),
                        "request_profile": request_profile,
                    }
                )
                if batch_result is not None:
                    batch_record["batch_output_hash"] = batch_result.output_hash
                if failure is not None:
                    batch_record["status"] = "FAILED"
                    batch_record["failure_reason"] = str(failure)
                telemetry.append(batch_record)
                self.batch_telemetry = tuple(telemetry)

        return build_canonicalization_result(
            batches=tuple(batch_results),
            selected_events=list(events),
            model_identity=self.client.model,
            prompt_version=self.client.prompt_version,
            generation_settings=_generation_settings(self.client),
        )

    @property
    def last_telemetry(self) -> dict[str, Any] | None:
        return self.client.last_telemetry


@dataclass
class OpenAICompatConsolidatorEngine:
    client: OpenAICompatStructuredClient
    require_memory_items: bool = False
    patch_telemetry: dict[str, Any] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.client.response_format = CONSOLIDATOR_RESPONSE_FORMAT

    async def consolidate(
        self,
        *,
        base_capsule: Capsule | None,
        events: list[SourceItem],
        snapshot_end_event_id: str | None,
        packet: LosslessPacket,
    ) -> ConsolidationResult:
        allowed_ids = tuple(item.id for item in events)
        _set_call_context(
            self.client,
            stage="consolidator",
            source_refs=allowed_ids,
        )
        try:
            _check_call_ownership(self.client)
            self.client.response_format = consolidator_response_format_for_sources(
                allowed_ids,
                require_memory_items=self.require_memory_items,
            )
            base_items = (
                list(base_capsule.memory_items)
                if base_capsule is not None
                else []
            )
            patch_mode = bool(base_items)
            base_record_context = base_record_context_for_packet(base_items, packet)
            payload: dict[str, Any] = {
                "base_capsule": _capsule_payload(
                    base_capsule,
                    include_content=not patch_mode,
                ),
                "snapshot_end_event_id": snapshot_end_event_id,
                "lossless_packet": _lossless_packet_payload(packet),
            }
            if patch_mode:
                payload["base_record_context"] = base_record_context
                payload["base_record_context_truncated"] = (
                    len(base_record_context) < len(base_items)
                )
            self.patch_telemetry = {
                "patch_mode": patch_mode,
                "base_records_total": len(base_items),
                "base_records_sent": len(base_record_context),
                "base_capsule_content_sent": not patch_mode,
            }
            response = await self.client.complete_json(payload)
            _check_call_ownership(self.client)
            _require_exact_response_keys(
                response,
                {"content", "evidence_event_ids"},
                "consolidator",
                optional_keys={"retire", "items"},
            )
            content = response.get("content")
            evidence_ids = response.get("evidence_event_ids")
            if not isinstance(content, str):
                raise ModelProtocolError(
                    "consolidator response must contain content as a string"
                )
            if not isinstance(evidence_ids, list) or not all(
                isinstance(item, str) for item in evidence_ids
            ):
                raise ModelProtocolError(
                    "consolidator response must contain evidence_event_ids "
                    "as a string array"
                )
            if len(set(evidence_ids)) != len(evidence_ids):
                raise ModelProtocolError(
                    "consolidator returned duplicate evidence event IDs"
                )
            unknown_ids = set(evidence_ids) - set(allowed_ids)
            if unknown_ids:
                raise ModelProtocolError(
                    "consolidator returned unknown evidence event ID(s): "
                    + ", ".join(sorted(unknown_ids))
                )
            retire_memories: list[RetireMemory] = []
            raw_retire = response.get("retire", [])
            if isinstance(raw_retire, list):
                for item in raw_retire:
                    if not isinstance(item, dict):
                        continue
                    retire_content = item.get("content")
                    memory_type = item.get("memory_type")
                    importance = item.get("importance")
                    retire_evidence = item.get("evidence_event_ids")
                    if (
                        not isinstance(retire_content, str)
                        or not isinstance(memory_type, str)
                        or not isinstance(importance, (int, float))
                        or isinstance(importance, bool)
                        or not 0 <= importance <= 1
                        or not isinstance(retire_evidence, list)
                        or not retire_evidence
                        or not all(isinstance(ref, str) for ref in retire_evidence)
                    ):
                        continue
                    retire_memories.append(
                        RetireMemory(
                            content=retire_content,
                            memory_type=memory_type,
                            importance=float(importance),
                            evidence_event_ids=tuple(retire_evidence),
                        )
                    )
            memory_items: list[dict[str, Any]] = []
            raw_items = response.get("items", [])
            if isinstance(raw_items, list):
                for item in raw_items:
                    if isinstance(item, dict):
                        memory_items.append(dict(item))
            if patch_mode:
                content = compose_incremental_content(
                    model_content=content,
                    settled_items=base_items,
                    patch_items=memory_items,
                )
            self.patch_telemetry["patch_records_returned"] = len(memory_items)
            remember = getattr(self.client, "remember_validated_result", None)
            if remember is not None and not (
                self.client.last_telemetry or {}
            ).get("cache_hit"):
                remember(response)
            return ConsolidationResult(
                content=content,
                evidence_event_ids=tuple(evidence_ids),
                model_identity=self.client.model,
                prompt_version=self.client.prompt_version,
                generation_settings=_generation_settings(self.client),
                retire_memories=tuple(retire_memories),
                memory_items=tuple(memory_items),
            )
        except Exception as error:
            _mark_call_failed(self.client, error)
            raise

    @property
    def last_telemetry(self) -> dict[str, Any] | None:
        if self.client.last_telemetry is None and not self.patch_telemetry:
            return None
        return {**dict(self.client.last_telemetry or {}), **self.patch_telemetry}


def build_lossless_engine(
    config: RuntimeConfig,
    *,
    telemetry_recorder: Any | None = None,
) -> LosslessCompactionEngine | None:
    if not config.compaction_configured:
        return None
    generation_settings = {
        "temperature": 0,
        "top_p": 1,
        "stream": False,
        "reasoning_effort": "none",
    }
    selector = OpenAICompatSelectorEngine(
        OpenAICompatStructuredClient(
            endpoint=config.selector_url,
            model=config.selector_model,
            prompt_version="selector-v1",
            system_prompt=(
                "You select durable-memory source items. Return JSON only with exactly "
                '{"selected_event_ids":["source-item-id"]}. Select only whole-event or '
                "source-span IDs present in the input; do not summarize or rewrite items."
            ),
            generation_settings=generation_settings,
            timeout=config.model_timeout_seconds,
            api_key=config.selector_api_key,
            telemetry_recorder=telemetry_recorder,
        ),
        selector_context_tokens=config.selector_context_tokens,
    )
    canonicalizer = None
    if config.canonicalizer_enabled:
        canonicalizer = OpenAICompatCanonicalizerEngine(
            OpenAICompatStructuredClient(
                endpoint=config.canonicalizer_url,
                model=config.canonicalizer_model,
                prompt_version="canonicalizer-v1",
                system_prompt=(
                    "You canonicalize authoritative events for durable memory. Return JSON only with "
                    '{"canonical_text":"...","cited_source_refs":["source-item-id"]}. '
                    "cited_source_refs is optional and may cite a subset of supplied source items, "
                    "but cited IDs must be the exact supplied item IDs in source order. For a source "
                    "span, cite its complete ID including the ::span:: suffix; never cite its "
                    "parent_event_id. Do not invent facts or IDs."
                ),
                generation_settings=generation_settings,
                timeout=config.model_timeout_seconds,
                api_key=config.canonicalizer_api_key,
                telemetry_recorder=telemetry_recorder,
            ),
            batch_target_tokens=config.canonicalizer_input_tokens,
        )
    consolidator_prompt = (
        "You render a durable-memory capsule from an already-approved lossless packet. "
        "Return JSON only with {'content':'...','evidence_event_ids':['source-item-id'], "
        "'retire':[{'content':'...','memory_type':'finding','importance':0.0, "
        "'evidence_event_ids':['source-item-id']}]}. The optional retire array "
        "must be [] when there is no durable cold memory to store. "
        "Preserve conditions, causal relationships, current state, negative knowledge, "
        "and uncertainty. Cite only authoritative source-item IDs from the input. "
        "Retire only durable semantic memories, not raw excerpts. "
        "If base_record_context is present, it is a bounded index of prior records that "
        "may be affected by the new evidence. Return only new or changed record patches; "
        "do not copy unchanged records. To replace prior state, cite its id in "
        "supersedes_item_ids. Software carries every omitted prior record forward. Return "
        "content covering only new or changed state from the lossless packet."
    )
    if config.memory_format in {"v2", "v2_shadow"}:
        consolidator_prompt += (
            " Also return 'items':[{'kind':'state|decision|constraint|blocker|next_action|"
            "artifact|completed|failure','status':'active|resolved|superseded','text':'...',"
            "'evidence':[{'source_id':'source-item-id','excerpt':'exact substring'}],"
            "'supersedes_item_ids':['mem_id']}] where every excerpt must be copied "
            "verbatim from lossless_packet.authoritative_events[].citation_text for the cited "
            "source_id without paraphrasing, summarizing, reformatting, normalizing "
            "whitespace, or using ellipsis (...). Prefer one complete line or the shortest "
            "contiguous substring that proves the claim; excerpts cannot exceed 512 characters. "
            "Use multiple shorter evidence entries "
            "instead of one truncated excerpt. Every active fact must be evidence-backed. "
            "Every evidence.source_id must exactly match an id in "
            "lossless_packet.authoritative_events for this request only; never cite "
            "parent_event_id or IDs from other packets."
        )
    consolidator = OpenAICompatConsolidatorEngine(
        OpenAICompatStructuredClient(
            endpoint=config.consolidator_url,
            model=config.consolidator_model or "",
            prompt_version=(
                "consolidator-v3-citation-view"
                if config.memory_format in {"v2", "v2_shadow"}
                else "consolidator-v1"
            ),
            system_prompt=consolidator_prompt,
            generation_settings=generation_settings,
            timeout=config.model_timeout_seconds,
            api_key=config.consolidator_api_key,
            telemetry_recorder=telemetry_recorder,
        ),
        require_memory_items=config.memory_format in {"v2", "v2_shadow"},
    )
    return LosslessCompactionEngine(selector, canonicalizer, consolidator)
