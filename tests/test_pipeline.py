from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from memory_gateway.compaction import (
    CompactionWorker,
    Event,
    SourceSpan,
    compute_input_hash,
    expand_source_items,
    queue_snapshot_job,
    reconstruct_source_content,
)
from memory_gateway.context import estimate_tokens
from memory_gateway.db import SQLiteStore
from memory_gateway.openai_adapter import ModelProtocolError
from memory_gateway.pipeline import (
    CanonicalizerBatchError,
    CanonicalizationResult,
    ConsolidationResult,
    LosslessCompactionEngine,
    SelectionResult,
    build_lossless_packet,
)
from memory_gateway.pipeline_adapters import (
    CANONICALIZER_RESPONSE_FORMAT,
    CONSOLIDATOR_RESPONSE_FORMAT,
    OpenAICompatCanonicalizerEngine,
    OpenAICompatConsolidatorEngine,
    OpenAICompatSelectorEngine,
    SELECTOR_RESPONSE_FORMAT,
    _chunk_events,
    _event_payload,
)


def event(event_id: str, sequence: int) -> Event:
    content = f"content-{event_id}"
    return Event(
        id=event_id,
        sequence=sequence,
        content=content,
        content_hash=sha256(content.encode()).hexdigest(),
        event_type="user_message",
        role="user",
    )


def sized_event(event_id: str, sequence: int, content_length: int = 300) -> Event:
    content = "x" * content_length
    return Event(
        id=event_id,
        sequence=sequence,
        content=content,
        content_hash=sha256(content.encode()).hexdigest(),
        event_type="user_message",
        role="user",
    )


class Selector:
    async def select(self, *, events):
        return SelectionResult(
            selected_event_ids=(events[1].id,),
            input_hash="selector-input",
            output_hash="selector-output",
            model_identity="fake-selector",
            prompt_version="selector-v1",
            generation_settings={},
        )


class Canonicalizer:
    async def canonicalize(self, *, events):
        return CanonicalizationResult(
            canonical_text="canonical event-2",
            source_event_ids=tuple(item.id for item in events),
            input_hash="canonical-input",
            output_hash="canonical-output",
            model_identity="fake-canonicalizer",
            prompt_version="canonicalizer-v1",
            generation_settings={},
        )


class Consolidator:
    async def consolidate(self, *, base_capsule, events, snapshot_end_event_id, packet):
        assert packet.canonical_text == "canonical event-2"
        assert packet.selected_event_ids == ("event-2",)
        assert packet.authoritative_events[0]["content"] == "content-event-2"
        content = "rendered capsule"
        return ConsolidationResult(
            content=content,
            evidence_event_ids=tuple(item.id for item in events),
            model_identity="fake-consolidator",
            prompt_version="consolidator-v1",
            generation_settings={},
        )


class FailingSelector:
    async def select(self, *, events):
        raise RuntimeError("qwen selector unavailable")


class FailingCanonicalizer:
    async def canonicalize(self, *, events):
        raise RuntimeError("qwen canonicalizer unavailable")


class FailingConsolidator:
    async def consolidate(self, *, base_capsule, events, snapshot_end_event_id, packet):
        raise RuntimeError("gemini consolidator unavailable")


class StructuredClient:
    def __init__(self, response):
        self.response = response
        self.model = "fake-stage-model"
        self.prompt_version = "fake-stage-v1"
        self.generation_settings = {"temperature": 0}
        self.last_telemetry = {"status": "SUCCEEDED"}

    async def complete_json(self, input_payload):
        self.input_payload = input_payload
        return self.response


class BatchCanonicalizerClient:
    model = "fake-batch-canonicalizer"
    prompt_version = "canonicalizer-batch-v1"
    generation_settings = {"temperature": 0}

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []
        self.last_telemetry = None

    async def complete_json(self, input_payload):
        self.calls.append(input_payload)
        call_index = len(self.calls) - 1
        self.last_telemetry = {
            "status": "SUCCEEDED",
            "input_tokens": 100 + call_index,
            "output_tokens": 10,
            "reasoning_tokens": 0,
            "wall_ms": 2.5,
            "raw_response_hash": f"response-{call_index}",
        }
        if self.responses:
            return self.responses[call_index]
        return {"canonical_text": f"canonical-batch-{call_index}"}


class ChunkSelectorClient:
    model = "fake-selector"
    prompt_version = "selector-v1"
    generation_settings = {"temperature": 0}

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.last_telemetry = None

    async def complete_json(self, input_payload):
        self.calls.append(input_payload)
        response = self.responses[len(self.calls) - 1]
        self.last_telemetry = {"status": "SUCCEEDED", "request_number": len(self.calls)}
        return response


class SpanSelectingClient:
    model = "fake-span-selector"
    prompt_version = "selector-span-v1"
    generation_settings = {"temperature": 0}

    def __init__(self):
        self.calls = []
        self.last_telemetry = None

    async def complete_json(self, input_payload):
        self.calls.append(input_payload)
        self.last_telemetry = {"status": "SUCCEEDED", "request_number": len(self.calls)}
        return {
            "selected_event_ids": [
                item["id"] for item in input_payload["events"]
            ]
        }


def store_with_old_capsule(tmp_path: Path) -> tuple[SQLiteStore, str, str]:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    old_content = "old active capsule"
    old_hash = sha256(old_content.encode()).hexdigest()
    old_id = store.create_capsule(
        thread_id="thread",
        base_capsule_id=None,
        content=old_content,
        source_event_hash="source",
        input_hash="input",
        output_hash=old_hash,
        capsule_hash=old_hash,
        snapshot_start_event_id=None,
        snapshot_end_event_id=None,
        covered_start_event_id=None,
        covered_end_event_id=None,
    )
    assert store.mark_capsule_ready(old_id)
    assert store.promote_capsule_cas("thread", old_id)
    for index in range(2):
        store.append_event(
            project_id="project",
            thread_id="thread",
            event_type="user_message",
            role="user",
            content=f"event-{index}",
        )
    return store, old_id, queue_snapshot_job(store, "thread")


def assert_failed_stage_preserves_old_capsule(
    store: SQLiteStore,
    old_id: str,
    job_id: str,
    engine: LosslessCompactionEngine,
    prefix: str,
) -> None:
    assert asyncio.run(CompactionWorker(store, engine, "failure-test-worker").run_once()) == "FAILED"
    job = store.get_job(job_id)
    assert job["error"].startswith(prefix)
    active = store.get_active_capsule("thread")
    assert active["id"] == old_id
    assert active["content"] == "old active capsule"
    assert active["state"] == "ACTIVE"


def test_selector_stage_failure_is_tagged_and_old_capsule_untouched(tmp_path: Path) -> None:
    store, old_id, job_id = store_with_old_capsule(tmp_path)
    engine = LosslessCompactionEngine(FailingSelector(), Canonicalizer(), Consolidator())
    assert_failed_stage_preserves_old_capsule(
        store, old_id, job_id, engine, "selector stage failed:"
    )


def test_canonicalizer_stage_failure_is_tagged_and_old_capsule_untouched(tmp_path: Path) -> None:
    store, old_id, job_id = store_with_old_capsule(tmp_path)
    engine = LosslessCompactionEngine(Selector(), FailingCanonicalizer(), Consolidator())
    assert_failed_stage_preserves_old_capsule(
        store, old_id, job_id, engine, "canonicalizer stage failed:"
    )


def test_consolidator_stage_failure_is_tagged_and_old_capsule_untouched(tmp_path: Path) -> None:
    store, old_id, job_id = store_with_old_capsule(tmp_path)
    engine = LosslessCompactionEngine(Selector(), Canonicalizer(), FailingConsolidator())
    assert_failed_stage_preserves_old_capsule(
        store, old_id, job_id, engine, "consolidator stage failed:"
    )


def test_selector_chunks_only_between_events() -> None:
    events = [sized_event(f"event-{index}", index) for index in range(3)]
    event_tokens = estimate_tokens(
        json.dumps(_event_payload(events[0]), ensure_ascii=False)
    )
    chunks = _chunk_events(events, target_tokens=event_tokens * 2)
    assert [[event.id for event in chunk] for chunk in chunks] == [
        ["event-0", "event-1"],
        ["event-2"],
    ]


def test_selector_puts_oversized_event_in_own_chunk() -> None:
    oversized = sized_event("oversized", 1, content_length=6_000)
    following = sized_event("following", 2)
    oversized_tokens = estimate_tokens(
        json.dumps(_event_payload(oversized), ensure_ascii=False)
    )
    chunks = _chunk_events([oversized, following], target_tokens=oversized_tokens - 1)
    assert [[event.id for event in chunk] for chunk in chunks] == [
        ["oversized"],
        ["following"],
    ]


def test_normal_events_remain_whole_and_span_payloads_are_deterministic() -> None:
    normal = sized_event("normal", 1, content_length=100)
    oversized = sized_event("oversized", 2, content_length=6_000)

    assert expand_source_items([normal]) == (normal,)
    first = expand_source_items(
        [oversized],
        selector_budget_tokens=200,
        safety_margin=0.8,
    )
    second = expand_source_items(
        [oversized],
        selector_budget_tokens=200,
        safety_margin=0.8,
    )

    assert first == second
    assert all(isinstance(item, SourceSpan) for item in first)
    spans = list(first)
    assert [span.span_index for span in spans] == list(range(len(spans)))
    assert spans[0].source_start == 0
    assert spans[-1].source_end == len(oversized.content)
    assert all(
        spans[index].source_end == spans[index + 1].source_start
        for index in range(len(spans) - 1)
    )
    assert all(
        estimate_tokens(json.dumps(_event_payload(span), ensure_ascii=False)) <= 160
        for span in spans
    )
    assert reconstruct_source_content(spans) == oversized.content


def test_source_span_reconstruction_rejects_tampered_content() -> None:
    oversized = sized_event("oversized", 1, content_length=6_000)
    spans = list(
        expand_source_items(
            [oversized],
            selector_budget_tokens=200,
            safety_margin=0.8,
        )
    )
    tampered = replace(spans[0], content="tampered")
    with pytest.raises(ValueError, match="source span range|content hash"):
        reconstruct_source_content([tampered, *spans[1:]])


def test_selector_exposes_oversized_event_as_ordered_source_spans() -> None:
    oversized = sized_event("oversized", 1, content_length=6_000)
    following = sized_event("following", 2, content_length=100)
    client = SpanSelectingClient()
    engine = OpenAICompatSelectorEngine(
        client,
        chunk_target_tokens=200,
        selector_context_tokens=32_768,
    )

    result = asyncio.run(engine.select(events=[oversized, following]))
    expected_items = expand_source_items(
        [oversized, following],
        selector_budget_tokens=200,
        safety_margin=0.8,
    )

    assert result.selected_event_ids == tuple(item.id for item in expected_items)
    assert len(client.calls) == len(engine.chunk_telemetry)
    assert all(
        record["chunk_estimated_tokens"] <= 200
        for record in engine.chunk_telemetry
    )
    assert all(
        item["id"] in {
            source["id"]
            for call in client.calls
            for source in call["events"]
        }
        for item in client.calls[0]["events"]
    )


def test_selector_rejects_unknown_source_span_id() -> None:
    oversized = sized_event("oversized", 1, content_length=6_000)
    client = ChunkSelectorClient(
        [{"selected_event_ids": ["oversized::span::999999"]}]
    )
    engine = OpenAICompatSelectorEngine(client, chunk_target_tokens=200)

    with pytest.raises(ModelProtocolError, match="outside current chunk"):
        asyncio.run(engine.select(events=[oversized]))
    assert engine.chunk_telemetry[0]["chunk_event_ids"] == (
        "oversized::span::000000",
    )


def test_lossless_pipeline_maps_span_evidence_to_parent_event() -> None:
    oversized = sized_event("oversized", 1, content_length=6_000)

    class SpanSelector:
        chunk_target_tokens = 200
        selector_context_tokens = 32_768
        span_safety_margin = 0.8

        async def select(self, *, events):
            items = expand_source_items(
                events,
                selector_budget_tokens=self.chunk_target_tokens,
                safety_margin=self.span_safety_margin,
            )
            return SelectionResult(
                selected_event_ids=(items[0].id,),
                input_hash="selector-input",
                output_hash="selector-output",
                model_identity="span-selector",
                prompt_version="selector-v1",
                generation_settings={},
            )

    class SpanCanonicalizer:
        async def canonicalize(self, *, events):
            assert isinstance(events[0], SourceSpan)
            return CanonicalizationResult(
                canonical_text="span canonical",
                source_event_ids=tuple(item.id for item in events),
                input_hash="canonical-input",
                output_hash="canonical-output",
                model_identity="span-canonicalizer",
                prompt_version="canonicalizer-v1",
                generation_settings={},
            )

    class SpanConsolidator:
        async def consolidate(self, *, base_capsule, events, snapshot_end_event_id, packet):
            assert packet.authoritative_events[0]["parent_event_id"] == "oversized"
            return ConsolidationResult(
                content="span capsule",
                evidence_event_ids=(events[0].id,),
                model_identity="span-consolidator",
                prompt_version="consolidator-v1",
                generation_settings={},
            )

    result = asyncio.run(
        LosslessCompactionEngine(
            SpanSelector(),
            SpanCanonicalizer(),
            SpanConsolidator(),
        ).compact(
            base_capsule=None,
            events=[oversized],
            snapshot_end_event_id=oversized.id,
        )
    )

    assert result.covered_event_ids == ("oversized",)
    assert result.evidence_event_ids == ("oversized",)
    assert result.evidence_source_ids == ("oversized::span::000000",)


def test_selector_unions_duplicate_chunk_selections_and_restores_order() -> None:
    events = [sized_event(f"event-{index}", index) for index in range(4)]
    event_tokens = estimate_tokens(
        json.dumps(_event_payload(events[0]), ensure_ascii=False)
    )
    client = ChunkSelectorClient(
        [
            {"selected_event_ids": ["event-1", "event-0", "event-1"]},
            {"selected_event_ids": ["event-3", "event-2"]},
        ]
    )
    engine = OpenAICompatSelectorEngine(client, chunk_target_tokens=event_tokens * 2)
    result = asyncio.run(engine.select(events=events))
    assert result.selected_event_ids == ("event-0", "event-1", "event-2", "event-3")
    assert len(client.calls) == 2
    assert len(engine.chunk_telemetry) == 2
    assert [record["chunk_index"] for record in engine.chunk_telemetry] == [0, 1]
    assert [record["chunk_event_ids"] for record in engine.chunk_telemetry] == [
        ("event-0", "event-1"),
        ("event-2", "event-3"),
    ]


def test_selector_rejects_id_outside_current_chunk() -> None:
    events = [sized_event(f"event-{index}", index) for index in range(2)]
    client = ChunkSelectorClient([{"selected_event_ids": ["not-in-chunk"]}])
    engine = OpenAICompatSelectorEngine(client, chunk_target_tokens=1)
    with pytest.raises(ModelProtocolError, match="outside current chunk"):
        asyncio.run(engine.select(events=events))
    assert len(engine.chunk_telemetry) == 1
    assert engine.chunk_telemetry[0]["status"] == "SUCCEEDED"


def test_lossless_pipeline_moves_authoritative_data_in_software() -> None:
    engine = LosslessCompactionEngine(Selector(), Canonicalizer(), Consolidator())
    result = asyncio.run(
        engine.compact(
            base_capsule=None,
            events=[event("event-1", 1), event("event-2", 2)],
            snapshot_end_event_id="event-2",
        )
    )
    assert result.content == "rendered capsule"
    assert result.covered_event_ids == ("event-1", "event-2")
    assert result.evidence_event_ids == ("event-2",)
    assert result.input_hash == compute_input_hash(
        None,
        [event("event-1", 1), event("event-2", 2)],
        "event-2",
    )
    assert result.output_hash == sha256(b"rendered capsule").hexdigest()


def test_concrete_stage_adapters_keep_distinct_output_contracts() -> None:
    events = [event("event-1", 1), event("event-2", 2)]
    selector_client = StructuredClient({"selected_event_ids": ["event-2"]})
    canonicalizer_client = StructuredClient(
        {"canonical_text": "canonical event-2"}
    )
    consolidator_client = StructuredClient(
        {"content": "rendered capsule", "evidence_event_ids": ["event-2"]}
    )

    selection = asyncio.run(
        OpenAICompatSelectorEngine(selector_client).select(events=events)
    )
    canonical = asyncio.run(
        OpenAICompatCanonicalizerEngine(canonicalizer_client).canonicalize(
            events=[events[1]]
        )
    )
    consolidation = asyncio.run(
        OpenAICompatConsolidatorEngine(consolidator_client).consolidate(
            base_capsule=None,
            events=[events[1]],
            snapshot_end_event_id="event-2",
            packet=build_lossless_packet(canonical, [events[1]]),
        )
    )
    assert selection.selected_event_ids == ("event-2",)
    assert canonical.covered_source_refs == ("event-2",)
    assert canonical.cited_source_refs == ()
    assert consolidation.evidence_event_ids == ("event-2",)
    assert selector_client.input_payload["event_ids_in_order"] == ["event-1", "event-2"]
    assert selector_client.response_format == SELECTOR_RESPONSE_FORMAT
    assert canonicalizer_client.response_format == CANONICALIZER_RESPONSE_FORMAT
    assert consolidator_client.response_format == CONSOLIDATOR_RESPONSE_FORMAT


def test_consolidator_schema_is_strict_and_requires_both_fields() -> None:
    schema = CONSOLIDATOR_RESPONSE_FORMAT["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"] == {
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
    }


def test_consolidator_accepts_semantic_citation_subset() -> None:
    events = [event("event-1", 1), event("event-2", 2)]
    canonical = Canonicalizer()
    canonical_result = asyncio.run(canonical.canonicalize(events=events))
    client = StructuredClient(
        {"content": "rendered", "evidence_event_ids": ["event-2"]}
    )

    result = asyncio.run(
        OpenAICompatConsolidatorEngine(client).consolidate(
            base_capsule=None,
            events=events,
            snapshot_end_event_id="event-2",
            packet=build_lossless_packet(canonical_result, events),
        )
    )

    assert result.evidence_event_ids == ("event-2",)


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"content": 42, "evidence_event_ids": []},
        {"content": "rendered", "evidence_event_ids": "event-1"},
        {"content": "rendered", "evidence_event_ids": ["missing"]},
        {"content": "rendered", "evidence_event_ids": ["event-1", "event-1"]},
        {
            "content": "rendered",
            "evidence_event_ids": [],
            "extra": "rejected",
        },
    ],
)
def test_consolidator_rejects_invalid_schema_or_citations(response: dict) -> None:
    events = [event("event-1", 1)]
    canonical_result = asyncio.run(Canonicalizer().canonicalize(events=events))
    client = StructuredClient(response)

    with pytest.raises(ModelProtocolError):
        asyncio.run(
            OpenAICompatConsolidatorEngine(client).consolidate(
                base_capsule=None,
                events=events,
                snapshot_end_event_id="event-1",
                packet=build_lossless_packet(canonical_result, events),
            )
        )


def test_adapter_consolidator_failure_leaves_active_capsule_untouched(
    tmp_path: Path,
) -> None:
    store, old_id, job_id = store_with_old_capsule(tmp_path)
    events = [event("event-1", 1), event("event-2", 2)]
    client = StructuredClient(
        {"content": "rendered", "evidence_event_ids": ["missing"]}
    )

    assert_failed_stage_preserves_old_capsule(
        store,
        old_id,
        job_id,
        LosslessCompactionEngine(
            Selector(),
            Canonicalizer(),
            OpenAICompatConsolidatorEngine(client),
        ),
        "consolidator stage failed:",
    )


def test_canonicalizer_small_input_uses_one_batch() -> None:
    events = [event("event-0", 0), event("event-1", 1)]
    client = BatchCanonicalizerClient()
    engine = OpenAICompatCanonicalizerEngine(client, batch_target_tokens=100)

    result = asyncio.run(engine.canonicalize(events=events))

    assert len(client.calls) == 1
    assert result.source_event_ids == ("event-0", "event-1")
    assert len(result.batches) == 1
    aggregate = json.loads(result.canonical_text)
    assert aggregate["format"] == "orchid_canonicalization_aggregate_v1"
    assert aggregate["covered_source_refs"] == ["event-0", "event-1"]
    assert aggregate["cited_source_refs"] == []
    assert aggregate["batches"][0]["batch_index"] == 0
    assert aggregate["batches"][0]["covered_source_refs"] == [
        "event-0",
        "event-1",
    ]
    assert aggregate["batches"][0]["cited_source_refs"] == []
    assert engine.batch_telemetry[0]["batch_index"] == 0
    assert engine.batch_telemetry[0]["batch_covered_source_refs"] == (
        "event-0",
        "event-1",
    )
    assert engine.batch_telemetry[0]["actual_prompt_tokens"] == 100
    assert engine.batch_telemetry[0]["response_hash"] == "response-0"


def test_canonicalizer_batches_are_deterministic_and_source_ordered() -> None:
    events = [sized_event(f"event-{index}", index, content_length=600) for index in range(4)]
    item_tokens = estimate_tokens(
        json.dumps(_event_payload(events[0]), ensure_ascii=False)
    )
    target = item_tokens * 2

    first = OpenAICompatCanonicalizerEngine(
        BatchCanonicalizerClient(),
        batch_target_tokens=target,
    )
    second = OpenAICompatCanonicalizerEngine(
        BatchCanonicalizerClient(),
        batch_target_tokens=target,
    )
    first_result = asyncio.run(first.canonicalize(events=events))
    second_result = asyncio.run(second.canonicalize(events=events))

    assert [
        [event["id"] for event in call["events"]]
        for call in first.client.calls
    ] == [
        ["event-0", "event-1"],
        ["event-2", "event-3"],
    ]
    assert first_result.canonical_text == second_result.canonical_text
    assert first_result.output_hash == second_result.output_hash
    assert first_result.source_event_ids == tuple(event.id for event in events)
    assert tuple(
        source_id
        for batch in first_result.batches
        for source_id in batch.covered_source_refs
    ) == tuple(event.id for event in events)
    assert all(
        record["batch_estimated_tokens"] <= target
        for record in first.batch_telemetry
    )


def test_canonicalizer_preserves_source_span_boundaries_and_provenance() -> None:
    oversized = sized_event("oversized", 1, content_length=6_000)
    source_items = list(
        expand_source_items(
            [oversized],
            selector_budget_tokens=200,
            safety_margin=0.8,
        )
    )
    client = BatchCanonicalizerClient()
    engine = OpenAICompatCanonicalizerEngine(client, batch_target_tokens=400)
    result = asyncio.run(engine.canonicalize(events=source_items))

    supplied_ids = [
        item["id"]
        for call in client.calls
        for item in call["events"]
    ]
    assert supplied_ids == [item.id for item in source_items]
    assert all("source_kind" in item for call in client.calls for item in call["events"])
    assert result.source_event_ids == tuple(item.id for item in source_items)
    assert all(
        record["batch_estimated_tokens"] <= 400
        for record in engine.batch_telemetry
    )


def test_canonicalizer_citation_subset_preserves_mechanical_coverage() -> None:
    events = [event("event-0", 0), event("event-1", 1)]
    engine = OpenAICompatCanonicalizerEngine(
        BatchCanonicalizerClient(
            [{"canonical_text": "text", "cited_source_refs": ["event-1"]}]
        ),
        batch_target_tokens=100,
    )

    result = asyncio.run(engine.canonicalize(events=events))

    assert result.covered_source_refs == ("event-0", "event-1")
    assert result.cited_source_refs == ("event-1",)
    assert result.batches[0].covered_source_refs == ("event-0", "event-1")
    assert result.batches[0].cited_source_refs == ("event-1",)
    packet = build_lossless_packet(result, events)
    assert tuple(item["id"] for item in packet.authoritative_events) == (
        "event-0",
        "event-1",
    )


def test_canonicalizer_zero_citations_preserves_mechanical_coverage() -> None:
    events = [event("event-0", 0), event("event-1", 1)]
    result = asyncio.run(
        OpenAICompatCanonicalizerEngine(
            BatchCanonicalizerClient([{"canonical_text": "text"}]),
            batch_target_tokens=100,
        ).canonicalize(events=events)
    )

    assert result.covered_source_refs == ("event-0", "event-1")
    assert result.cited_source_refs == ()


@pytest.mark.parametrize(
    ("cited_refs", "message"),
    [
        (["missing"], "unknown cited source reference"),
        (["event-0", "event-0"], "duplicate cited source references"),
        (["event-1", "event-0"], "remain in supplied source order"),
    ],
)
def test_canonicalizer_rejects_invalid_citations(
    cited_refs: list[str],
    message: str,
) -> None:
    events = [event("event-0", 0), event("event-1", 1)]
    engine = OpenAICompatCanonicalizerEngine(
        BatchCanonicalizerClient(
            [{"canonical_text": "text", "cited_source_refs": cited_refs}]
        ),
        batch_target_tokens=100,
    )

    with pytest.raises(CanonicalizerBatchError, match=message):
        asyncio.run(engine.canonicalize(events=events))
    assert engine.batch_telemetry[0]["status"] == "FAILED"
    assert engine.batch_telemetry[0]["batch_index"] == 0
    assert message in engine.batch_telemetry[0]["failure_reason"]


def test_malformed_canonicalizer_batch_preserves_active_capsule(
    tmp_path: Path,
) -> None:
    store, old_id, job_id = store_with_old_capsule(tmp_path)
    canonicalizer = OpenAICompatCanonicalizerEngine(
        BatchCanonicalizerClient([{}]),
        batch_target_tokens=100,
    )

    assert_failed_stage_preserves_old_capsule(
        store,
        old_id,
        job_id,
        LosslessCompactionEngine(Selector(), canonicalizer, Consolidator()),
        "canonicalizer stage failed at batch 0:",
    )


def test_frozen_generation_six_selected_evidence_fits_canonicalizer_batches() -> None:
    database = Path(__file__).parents[1] / "data" / "live_test.db"
    if not database.exists():
        pytest.skip("frozen live_test.db is not present")

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT * FROM events "
        "WHERE thread_id = 'thread-1' AND sequence BETWEEN 15 AND 28 "
        "ORDER BY sequence"
    ).fetchall()
    connection.close()
    events = [Event.from_row(dict(row)) for row in rows]
    assert len(events) == 14

    source_items = list(
        expand_source_items(
            events,
            selector_budget_tokens=1_200,
            safety_margin=0.8,
        )
    )
    selected_ids = [
        *[
            f"{events[0].id}::span::{index:06d}"
            for index in range(3)
        ],
        events[1].id,
        f"{events[2].id}::span::000000",
        events[3].id,
        events[5].id,
        events[6].id,
        events[7].id,
        *[
            f"{events[8].id}::span::{index:06d}"
            for index in range(2)
        ],
        events[9].id,
        *[
            f"{events[10].id}::span::{index:06d}"
            for index in range(44)
        ],
        events[11].id,
        events[13].id,
    ]
    selected = [item for item in source_items if item.id in set(selected_ids)]
    assert [item.id for item in selected] == selected_ids

    batches = _chunk_events(selected, target_tokens=8_192)

    assert len(batches) > 1
    assert all(
        sum(
            estimate_tokens(json.dumps(_event_payload(item), ensure_ascii=False))
            for item in batch
        )
        <= 8_192
        for batch in batches
    )
    assert all(
        sum(
            estimate_tokens(json.dumps(_event_payload(item), ensure_ascii=False))
            for item in batch
        )
        < 32_768
        for batch in batches
    )
    assert [
        item.id
        for batch in batches
        for item in batch
    ] == selected_ids


def test_selector_and_canonicalizer_reject_missing_or_wrong_schema_fields() -> None:
    events = [event("event-1", 1)]
    selector_responses = [
        {},
        {"selected_event_ids": "event-1"},
        {"selected_event_ids": [], "extra": "rejected"},
    ]
    for response in selector_responses:
        with pytest.raises(ModelProtocolError, match="selected_event_ids"):
            asyncio.run(
                OpenAICompatSelectorEngine(StructuredClient(response)).select(
                    events=events
                )
            )

    canonicalizer_responses = [
        {},
        {"canonical_text": "text", "cited_source_refs": "event-1"},
        {"canonical_text": "text", "extra": "rejected"},
    ]
    for response in canonicalizer_responses:
        with pytest.raises(ModelProtocolError, match="canonicalizer response"):
            asyncio.run(
                OpenAICompatCanonicalizerEngine(
                    StructuredClient(response)
                ).canonicalize(events=events)
            )


def test_schema_mode_does_not_weaken_canonicalizer_provenance_validation() -> None:
    class BadCanonicalizer(Canonicalizer):
        async def canonicalize(self, *, events):
            return CanonicalizationResult(
                canonical_text="canonical",
                source_event_ids=("missing",),
                input_hash="canonical-input",
                output_hash="canonical-output",
                model_identity="fake-canonicalizer",
                prompt_version="canonicalizer-v1",
                generation_settings={},
            )

    with pytest.raises(ValueError, match="canonicalizer covered_source_refs"):
        asyncio.run(
            LosslessCompactionEngine(
                Selector(),
                BadCanonicalizer(),
                Consolidator(),
            ).compact(
                base_capsule=None,
                events=[event("event-1", 1), event("event-2", 2)],
                snapshot_end_event_id="event-2",
            )
        )


def test_lossless_pipeline_rejects_unknown_consolidator_evidence() -> None:
    class BadConsolidator(Consolidator):
        async def consolidate(self, *, base_capsule, events, snapshot_end_event_id, packet):
            return ConsolidationResult(
                content="rendered capsule",
                evidence_event_ids=("missing",),
                model_identity="fake-consolidator",
                prompt_version="consolidator-v1",
                generation_settings={},
            )

    engine = LosslessCompactionEngine(Selector(), Canonicalizer(), BadConsolidator())
    with pytest.raises(ValueError, match="unknown evidence event ID"):
        asyncio.run(
            engine.compact(
                base_capsule=None,
                events=[event("event-1", 1), event("event-2", 2)],
                snapshot_end_event_id="event-2",
            )
        )


def test_lossless_pipeline_rejects_unknown_selector_ids() -> None:
    class BadSelector(Selector):
        async def select(self, *, events):
            result = await super().select(events=events)
            return SelectionResult(
                selected_event_ids=("missing",),
                input_hash=result.input_hash,
                output_hash=result.output_hash,
                model_identity=result.model_identity,
                prompt_version=result.prompt_version,
                generation_settings=result.generation_settings,
            )

    engine = LosslessCompactionEngine(BadSelector(), Canonicalizer(), Consolidator())
    with pytest.raises(ValueError, match="unknown event ID"):
        asyncio.run(
            engine.compact(
                base_capsule=None,
                events=[event("event-1", 1), event("event-2", 2)],
                snapshot_end_event_id="event-2",
            )
        )
