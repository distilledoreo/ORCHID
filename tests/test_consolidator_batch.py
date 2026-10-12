from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from memory_gateway.admission.consolidator_batch import (
    _consolidator_subprocess_env,
    generation_from_compaction,
    load_offline_document,
    run_consolidator_document_gate,
    run_live_consolidator_on_chunks,
)
from memory_gateway.compaction import Event
from memory_gateway.config import RuntimeConfig
from memory_gateway.db import content_hash

_FIXTURE = Path(__file__).parent / "fixtures" / "v2_offline_generations.json"


def _event(event_id: str, content: str) -> Event:
    return Event(
        id=event_id,
        sequence=1,
        content=content,
        content_hash=content_hash(content),
        event_type="user_message",
        role="user",
    )


def test_offline_fixture_passes_require_items_gate() -> None:
    document = load_offline_document(_FIXTURE)
    document["require_items"] = True
    report = asyncio.run(run_consolidator_document_gate(document))
    assert report["rejected"] == 1
    assert report["accepted"] == 1


def test_generation_from_compaction_preserves_items() -> None:
    events = [_event("evt_1", "Owner: Disti.")]
    items = (
        {
            "kind": "state",
            "status": "active",
            "text": "Owner: Disti.",
            "evidence": [{"source_id": "evt_1", "excerpt": "Owner: Disti."}],
        },
    )
    generation = generation_from_compaction(
        generation=1,
        events=events,
        content="Owner: Disti.",
        memory_items=items,
    )
    assert generation["items"] == list(items)
    assert generation["events"][0]["id"] == "evt_1"


def test_offline_document_rejects_ungrounded_items() -> None:
    document = load_offline_document(_FIXTURE)
    document["require_items"] = True
    report = asyncio.run(run_consolidator_document_gate(document))
    failures = [row for row in report["reports"] if not row["ok"]]
    assert failures
    assert failures[0]["generation"] == 2


def test_assemble_offline_document_from_checkpoints(tmp_path: Path) -> None:
    from memory_gateway.admission.consolidator_batch import (
        assemble_offline_document_from_checkpoints,
    )

    chunks = [
        {"chunk_index": 0, "events": [{"id": "evt_1", "sequence": 1, "content": "a"}]},
        {"chunk_index": 1, "events": [{"id": "evt_2", "sequence": 2, "content": "b"}]},
    ]
    for chunk in chunks:
        generation = {
            "generation": int(chunk["chunk_index"]) + 1,
            "content": chunk["events"][0]["content"],
            "events": chunk["events"],
            "items": [],
            "evidence_event_ids": [chunk["events"][0]["id"]],
        }
        path = tmp_path / f"chunk_{int(chunk['chunk_index']):04d}.generation.json"
        path.write_text(__import__("json").dumps(generation), encoding="utf-8")
    document = assemble_offline_document_from_checkpoints(
        tmp_path,
        chunks=chunks,
        require_all=True,
    )
    assert document["chunk_count"] == 2
    assert len(document["generations"]) == 2


def test_consolidator_subprocess_env_falls_back_to_benchmark_provider() -> None:
    env = _consolidator_subprocess_env(
        {
            "BENCHMARK_PROVIDER_BASE_URL": "https://openrouter.ai/api/v1",
            "BENCHMARK_PROVIDER_API_KEY": "bench-key",
            "BENCHMARK_MODEL_ID": "upstage/solar-pro4",
        }
    )
    assert env["ORCHID_CONSOLIDATOR_URL"] == "https://openrouter.ai/api/v1"
    assert env["ORCHID_CONSOLIDATOR_API_KEY"] == "bench-key"
    assert env["ORCHID_CONSOLIDATOR_MODEL"] == "upstage/solar-pro4"
    assert (Path(env["PYTHONPATH"]) / "memory_gateway").is_dir()


def test_run_live_consolidator_builds_fresh_engine_per_chunk(monkeypatch) -> None:
    monkeypatch.setenv("ORCHID_CONSOLIDATOR_ISOLATE_CHUNKS", "true")
    monkeypatch.setenv("ORCHID_CONSOLIDATOR_SUBPROCESS_CHUNKS", "false")
    monkeypatch.setenv("ORCHID_CONSOLIDATOR_INTER_CHUNK_DELAY_SECONDS", "0")
    engines: list[MagicMock] = []

    def _build_engine(_config: RuntimeConfig) -> MagicMock:
        engine = MagicMock()
        engine.compact = AsyncMock(
            return_value=MagicMock(content="ok", memory_items=(), evidence_event_ids=())
        )
        engines.append(engine)
        return engine

    config = RuntimeConfig(
        db_path=":memory:",
        backend_url=None,
        backend_api_key=None,
        backend_model=None,
        selector_url="http://127.0.0.1:1234/v1",
        selector_model="selector",
        selector_api_key=None,
        canonicalizer_url=None,
        canonicalizer_model=None,
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
    chunks = [
        {"chunk_index": 0, "events": [{"id": "evt_1", "sequence": 1, "content": "a"}]},
        {"chunk_index": 1, "events": [{"id": "evt_2", "sequence": 2, "content": "b"}]},
    ]
    with patch(
        "memory_gateway.admission.consolidator_batch.build_lossless_engine",
        side_effect=_build_engine,
    ):
        generations = asyncio.run(run_live_consolidator_on_chunks(chunks, config=config))
    assert len(generations) == 2
    assert len(engines) == 2
    assert engines[0] is not engines[1]
