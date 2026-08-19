from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Protocol

from .compaction import (
    Capsule,
    CompactionResult,
    Event,
    SourceItem,
    _hash,
    compute_input_hash,
    compute_source_event_hash,
    expand_source_items,
    source_item_parent_event_id,
    source_item_payload,
)
from .openai_adapter import ModelProtocolError


@dataclass(frozen=True)
class SelectionResult:
    selected_event_ids: tuple[str, ...]
    input_hash: str
    output_hash: str
    model_identity: str
    prompt_version: str
    generation_settings: dict[str, Any]


@dataclass(frozen=True)
class CanonicalizationBatchResult:
    batch_index: int
    covered_source_refs: tuple[str, ...]
    canonical_text: str
    input_hash: str
    output_hash: str
    cited_source_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalizationResult:
    canonical_text: str
    source_event_ids: tuple[str, ...]
    input_hash: str
    output_hash: str
    model_identity: str
    prompt_version: str
    generation_settings: dict[str, Any]
    batches: tuple[CanonicalizationBatchResult, ...] = ()
    cited_source_refs: tuple[str, ...] = ()

    @property
    def covered_source_refs(self) -> tuple[str, ...]:
        """Software-owned exhaustive coverage, retained under the legacy field."""

        return self.source_event_ids


@dataclass(frozen=True)
class ConsolidationResult:
    content: str
    evidence_event_ids: tuple[str, ...]
    model_identity: str
    prompt_version: str
    generation_settings: dict[str, Any]


class CanonicalizerBatchError(ModelProtocolError):
    def __init__(self, batch_index: int, error: Exception):
        self.batch_index = batch_index
        self.error = error
        super().__init__(f"batch {batch_index}: {error}")


@dataclass(frozen=True)
class LosslessPacket:
    canonical_text: str
    selected_event_ids: tuple[str, ...]
    authoritative_events: tuple[dict[str, Any], ...]
    input_hash: str
    packet_hash: str


class SelectorEngine(Protocol):
    async def select(self, *, events: list[Event]) -> SelectionResult:
        ...


class CanonicalizerEngine(Protocol):
    async def canonicalize(self, *, events: list[SourceItem]) -> CanonicalizationResult:
        ...


def build_canonicalization_result(
    *,
    batches: tuple[CanonicalizationBatchResult, ...],
    selected_events: list[SourceItem],
    model_identity: str,
    prompt_version: str,
    generation_settings: dict[str, Any],
) -> CanonicalizationResult:
    selected_ids = tuple(item.id for item in selected_events)
    flattened_ids = tuple(
        source_id
        for batch in batches
        for source_id in batch.covered_source_refs
    )
    if flattened_ids != selected_ids:
        raise ValueError(
            "canonicalizer batch references do not cover selected source items "
            "exactly once in source order"
        )
    if len(set(flattened_ids)) != len(flattened_ids):
        raise ValueError("canonicalizer batch references contain duplicates")
    cited_ids = tuple(
        source_id
        for batch in batches
        for source_id in batch.cited_source_refs
    )
    if len(set(cited_ids)) != len(cited_ids):
        raise ValueError("canonicalizer cited source references contain duplicates")

    aggregate_payload = {
        "format": "orchid_canonicalization_aggregate_v1",
        "covered_source_refs": list(selected_ids),
        "cited_source_refs": list(cited_ids),
        "selected_source_input_hash": compute_source_event_hash(None, selected_events),
        "batches": [
            {
                "batch_index": batch.batch_index,
                "covered_source_refs": list(batch.covered_source_refs),
                "cited_source_refs": list(batch.cited_source_refs),
                "input_hash": batch.input_hash,
                "output_hash": batch.output_hash,
                "canonical_text": batch.canonical_text,
            }
            for batch in batches
        ],
    }
    canonical_text = json.dumps(
        aggregate_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return CanonicalizationResult(
        canonical_text=canonical_text,
        source_event_ids=selected_ids,
        input_hash=aggregate_payload["selected_source_input_hash"],
        output_hash=_hash(aggregate_payload),
        model_identity=model_identity,
        prompt_version=prompt_version,
        generation_settings=dict(generation_settings),
        batches=batches,
        cited_source_refs=cited_ids,
    )


class ConsolidatorEngine(Protocol):
    async def consolidate(
        self,
        *,
        base_capsule: Capsule | None,
        events: list[SourceItem],
        snapshot_end_event_id: str | None,
        packet: LosslessPacket,
    ) -> ConsolidationResult:
        ...


def build_lossless_packet(
    canonical: CanonicalizationResult,
    selected_events: list[SourceItem],
) -> LosslessPacket:
    selected_ids = tuple(event.id for event in selected_events)
    if canonical.covered_source_refs != selected_ids:
        raise ValueError(
            "canonicalizer covered_source_refs do not match software-selected events"
        )
    authoritative_events = tuple(source_item_payload(item) for item in selected_events)
    packet_payload = {
        "canonical_text": canonical.canonical_text,
        "selected_event_ids": selected_ids,
        "authoritative_events": authoritative_events,
    }
    return LosslessPacket(
        canonical_text=canonical.canonical_text,
        selected_event_ids=selected_ids,
        authoritative_events=authoritative_events,
        input_hash=canonical.input_hash,
        packet_hash=_hash(packet_payload),
    )


class LosslessCompactionEngine:
    """Composes model stages while software owns event retrieval and packet assembly."""

    def __init__(
        self,
        selector: SelectorEngine,
        canonicalizer: CanonicalizerEngine,
        consolidator: ConsolidatorEngine,
    ):
        self.selector = selector
        self.canonicalizer = canonicalizer
        self.consolidator = consolidator
        self._ownership_checker: Callable[[], None] | None = None

    def set_job_context(
        self,
        *,
        job_id: str | None,
        thread_id: str | None,
        generation: int | None,
    ) -> None:
        for stage in (self.selector, self.canonicalizer, self.consolidator):
            client = getattr(stage, "client", None)
            setter = getattr(client, "set_job_context", None)
            if setter is not None:
                setter(
                    job_id=job_id,
                    thread_id=thread_id,
                    generation=generation,
                )

    def set_ownership_checker(
        self,
        checker: Callable[[], None] | None,
    ) -> None:
        self._ownership_checker = checker
        for stage in (self.selector, self.canonicalizer, self.consolidator):
            client = getattr(stage, "client", None)
            setter = getattr(client, "set_ownership_checker", None)
            if setter is not None:
                setter(checker)

    def _check_ownership(self) -> None:
        if self._ownership_checker is not None:
            self._ownership_checker()

    async def compact(
        self,
        *,
        base_capsule: Capsule | None,
        events: list[Event],
        snapshot_end_event_id: str | None,
    ) -> CompactionResult:
        self._check_ownership()
        try:
            selection = await self.selector.select(events=events)
        except Exception as error:
            raise RuntimeError(f"selector stage failed: {error}") from error
        selector_budget = getattr(self.selector, "chunk_target_tokens", 1_200)
        selector_context = getattr(self.selector, "selector_context_tokens", 32_768)
        span_safety_margin = getattr(self.selector, "span_safety_margin", 0.8)
        try:
            source_items = expand_source_items(
                events,
                selector_budget_tokens=selector_budget,
                safety_margin=min(
                    span_safety_margin,
                    max(1, int(selector_context * span_safety_margin))
                    / selector_budget,
                ),
            )
        except ValueError:
            source_items = tuple(events)
        source_item_by_id = {item.id: item for item in source_items}
        event_by_id = {event.id: event for event in events}
        if len(set(selection.selected_event_ids)) != len(selection.selected_event_ids):
            raise ValueError("selector returned duplicate event IDs")
        if not set(selection.selected_event_ids).issubset(source_item_by_id):
            raise ValueError("selector returned an unknown event ID")
        selected_ids = set(selection.selected_event_ids)
        selected_events = [item for item in source_items if item.id in selected_ids]
        self._check_ownership()
        try:
            canonical = await self.canonicalizer.canonicalize(events=selected_events)
        except CanonicalizerBatchError as error:
            raise RuntimeError(
                f"canonicalizer stage failed at batch {error.batch_index}: {error.error}"
            ) from error
        except Exception as error:
            raise RuntimeError(f"canonicalizer stage failed: {error}") from error
        self._check_ownership()
        packet = build_lossless_packet(canonical, selected_events)
        self._check_ownership()
        try:
            consolidation = await self.consolidator.consolidate(
                base_capsule=base_capsule,
                events=selected_events,
                snapshot_end_event_id=snapshot_end_event_id,
                packet=packet,
            )
        except Exception as error:
            raise RuntimeError(f"consolidator stage failed: {error}") from error
        self._check_ownership()
        snapshot_event_ids = tuple(event.id for event in events)
        if len(set(consolidation.evidence_event_ids)) != len(consolidation.evidence_event_ids):
            raise ValueError("consolidator returned duplicate evidence event IDs")
        reported_evidence_ids = set(consolidation.evidence_event_ids)
        evidence_source_ids = [
            item.id for item in source_items if item.id in reported_evidence_ids
        ]
        source_item_ids = set(source_item_by_id)
        evidence_source_ids.extend(
            event.id
            for event in events
            if event.id in reported_evidence_ids and event.id not in source_item_ids
        )
        for source_id in consolidation.evidence_event_ids:
            if source_id in source_item_by_id:
                item = source_item_by_id[source_id]
            elif source_id in event_by_id:
                item = event_by_id[source_id]
            else:
                raise ValueError("consolidator returned an unknown evidence event ID")
        reported_parent_ids = {
            source_item_parent_event_id(
                source_item_by_id[source_id]
                if source_id in source_item_by_id
                else event_by_id[source_id]
            )
            for source_id in consolidation.evidence_event_ids
        }
        evidence_event_ids = [
            event.id for event in events if event.id in reported_parent_ids
        ]
        return CompactionResult(
            content=consolidation.content,
            covered_event_ids=snapshot_event_ids,
            evidence_event_ids=tuple(evidence_event_ids),
            input_hash=compute_input_hash(base_capsule, events, snapshot_end_event_id),
            output_hash=sha256(consolidation.content.encode("utf-8")).hexdigest(),
            model_identity=consolidation.model_identity,
            prompt_version=consolidation.prompt_version,
            generation_settings=consolidation.generation_settings,
            evidence_source_ids=tuple(evidence_source_ids),
        )
