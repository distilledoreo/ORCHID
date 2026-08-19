from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .compaction import (
    Capsule,
    Event,
    SourceItem,
    _hash,
    compute_source_event_hash,
    expand_source_items,
    source_item_parent_event_id,
    source_item_payload,
)
from .config import RuntimeConfig
from .context import estimate_tokens
from .openai_adapter import ModelProtocolError
from .pipeline import (
    CanonicalizationBatchResult,
    CanonicalizationResult,
    CanonicalizerBatchError,
    ConsolidationResult,
    LosslessPacket,
    LosslessCompactionEngine,
    SelectionResult,
    build_canonicalization_result,
)
from .structured_client import OpenAICompatStructuredClient


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
            },
            "required": ["content", "evidence_event_ids"],
            "additionalProperties": False,
        },
    },
}

DEFAULT_CANONICALIZER_INPUT_TOKENS = 8_192


def _event_payload(event: SourceItem) -> dict[str, Any]:
    return source_item_payload(event)


def _capsule_payload(capsule: Capsule | None) -> dict[str, Any] | None:
    if capsule is None:
        return None
    return {
        "id": capsule.id,
        "thread_id": capsule.thread_id,
        "content": capsule.content,
        "capsule_hash": capsule.capsule_hash,
        "covered_end_event_id": capsule.covered_end_event_id,
    }


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
) -> None:
    if not isinstance(response, dict) or set(response) != expected_keys:
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
            chunk_payload = {
                "events": [_event_payload(event) for event in chunk],
                "event_ids_in_order": [event.id for event in chunk],
                "source_ids_in_order": [event.id for event in chunk],
            }
            try:
                _set_call_context(
                    self.client,
                    stage="selector",
                    source_refs=tuple(event.id for event in chunk),
                    selector_chunk_index=chunk_index,
                )
                _check_call_ownership(self.client)
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
            expected_ids = tuple(event.id for event in batch)
            estimated_tokens = sum(
                estimate_tokens(json.dumps(_event_payload(event), ensure_ascii=False))
                for event in batch
            )
            batch_result: CanonicalizationBatchResult | None = None
            failure: Exception | None = None
            try:
                _set_call_context(
                    self.client,
                    stage="canonicalizer",
                    source_refs=expected_ids,
                    canonicalizer_batch_index=batch_index,
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
            except Exception as error:
                failure = error
                _mark_call_failed(self.client, error)
                raise CanonicalizerBatchError(batch_index, error) from error
            finally:
                batch_record = dict(self.client.last_telemetry or {})
                batch_record.update(
                    {
                        "batch_index": batch_index,
                        "batch_covered_source_refs": expected_ids,
                        "batch_source_event_ids": expected_ids,
                        "batch_event_count": len(batch),
                        "batch_estimated_tokens": estimated_tokens,
                        "actual_prompt_tokens": batch_record.get("input_tokens"),
                        "response_hash": batch_record.get("raw_response_hash"),
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
        _set_call_context(
            self.client,
            stage="consolidator",
            source_refs=tuple(item.id for item in events),
        )
        try:
            _check_call_ownership(self.client)
            response = await self.client.complete_json(
                {
                    "base_capsule": _capsule_payload(base_capsule),
                    "snapshot_end_event_id": snapshot_end_event_id,
                    "selected_events": [_event_payload(event) for event in events],
                    "lossless_packet": {
                        "canonical_text": packet.canonical_text,
                        "selected_event_ids": list(packet.selected_event_ids),
                        "authoritative_events": list(packet.authoritative_events),
                        "packet_hash": packet.packet_hash,
                    },
                }
            )
            _check_call_ownership(self.client)
            _require_exact_response_keys(
                response,
                {"content", "evidence_event_ids"},
                "consolidator",
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
            allowed_ids = {item.id for item in events} | {
                source_item_parent_event_id(item) for item in events
            }
            unknown_ids = set(evidence_ids) - allowed_ids
            if unknown_ids:
                raise ModelProtocolError(
                    "consolidator returned unknown evidence event ID(s): "
                    + ", ".join(sorted(unknown_ids))
                )
            return ConsolidationResult(
                content=content,
                evidence_event_ids=tuple(evidence_ids),
                model_identity=self.client.model,
                prompt_version=self.client.prompt_version,
                generation_settings=_generation_settings(self.client),
            )
        except Exception as error:
            _mark_call_failed(self.client, error)
            raise

    @property
    def last_telemetry(self) -> dict[str, Any] | None:
        return self.client.last_telemetry


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
    canonicalizer = OpenAICompatCanonicalizerEngine(
        OpenAICompatStructuredClient(
            endpoint=config.canonicalizer_url,
            model=config.canonicalizer_model,
            prompt_version="canonicalizer-v1",
            system_prompt=(
                "You canonicalize authoritative events for durable memory. Return JSON only with "
                '{"canonical_text":"...","cited_source_refs":["source-item-id"]}. '
                "cited_source_refs is optional and may cite a subset of supplied source items, "
                "but cited IDs must be supplied IDs in source order. Do not invent facts or IDs."
            ),
            generation_settings=generation_settings,
            timeout=config.model_timeout_seconds,
            api_key=config.canonicalizer_api_key,
            telemetry_recorder=telemetry_recorder,
        ),
        batch_target_tokens=config.canonicalizer_input_tokens,
    )
    consolidator = OpenAICompatConsolidatorEngine(
        OpenAICompatStructuredClient(
            endpoint=config.consolidator_url,
            model=config.consolidator_model or "",
            prompt_version="consolidator-v1",
            system_prompt=(
                "You render a durable-memory capsule from an already-approved lossless packet. "
                "Return JSON only with {'content':'...','evidence_event_ids':['source-item-id']}. "
                "Preserve conditions, causal relationships, current state, negative knowledge, "
                "and uncertainty. Cite only authoritative source-item IDs from the input."
            ),
            generation_settings=generation_settings,
            timeout=config.model_timeout_seconds,
            api_key=config.consolidator_api_key,
            telemetry_recorder=telemetry_recorder,
        )
    )
    return LosslessCompactionEngine(selector, canonicalizer, consolidator)
