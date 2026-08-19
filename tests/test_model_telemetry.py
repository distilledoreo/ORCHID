from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

from memory_gateway.compaction import CompactionWorker, Event
from memory_gateway.config import RuntimeConfig
from memory_gateway.db import SQLiteStore
from memory_gateway.openai_adapter import ModelProtocolError
from memory_gateway.pipeline import build_lossless_packet
from memory_gateway.pipeline_adapters import (
    OpenAICompatCanonicalizerEngine,
    OpenAICompatConsolidatorEngine,
    OpenAICompatSelectorEngine,
    build_lossless_engine,
)
from memory_gateway.structured_client import OpenAICompatStructuredClient
from memory_gateway.telemetry import TelemetryPersistenceError


class StageHandler(BaseHTTPRequestHandler):
    mode = "valid"
    requests: list[dict] = []

    def log_message(self, *_args) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length))
        type(self).requests.append(body)
        schema_name = (
            body.get("response_format", {})
            .get("json_schema", {})
            .get("name")
        )
        user_content = body["messages"][1]["content"]
        input_json = user_content.split(
            "--- BEGIN INPUT ---\n", 1
        )[1].split("\n--- END INPUT ---", 1)[0]
        input_payload = json.loads(input_json)
        first_id = (
            input_payload.get("events", [{}])[0].get("id")
            or input_payload.get("selected_events", [{}])[0].get("id")
        )

        if type(self).mode == "canonicalizer-malformed" and schema_name == "canonicalizer_response_v1":
            content = "not-json"
        elif type(self).mode == "consolidator-malformed" and schema_name == "consolidator_response_v1":
            content = "not-json"
        elif type(self).mode == "selector-invalid":
            content = json.dumps({"selected_event_ids": ["missing"]})
        elif schema_name == "selector_response_v1":
            content = json.dumps({"selected_event_ids": [first_id]})
        elif schema_name == "canonicalizer_response_v1":
            content = json.dumps({"canonical_text": "canonical"})
        elif schema_name == "consolidator_response_v1":
            content = json.dumps(
                {"content": "rendered", "evidence_event_ids": [first_id]}
            )
        else:
            content = json.dumps({})

        response = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 41,
                "completion_tokens": 9,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@contextmanager
def stage_server(mode: str = "valid") -> Iterator[str]:
    StageHandler.mode = mode
    StageHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), StageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def fixture_events() -> list[Event]:
    return [
        Event(
            id=f"event-{index}",
            sequence=index,
            content=f"event content {index}",
            content_hash=sha256(f"event content {index}".encode()).hexdigest(),
            event_type="user_message",
            role="user",
        )
        for index in range(2)
    ]


def make_store(tmp_path: Path) -> tuple[SQLiteStore, str]:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    for event in fixture_events():
        store.append_event(
            project_id="project",
            thread_id="thread",
            event_type=event.event_type,
            role=event.role,
            content=event.content,
            event_id=event.id,
        )
    return store, store.create_compaction_job(
        thread_id="thread",
        base_capsule_id=None,
        snapshot_start_event_id="event-0",
        snapshot_end_event_id="event-1",
    )


def stage_client(
    endpoint: str,
    store: SQLiteStore,
    model: str,
    job_id: str,
) -> OpenAICompatStructuredClient:
    client = OpenAICompatStructuredClient(
        endpoint=endpoint,
        model=model,
        prompt_version=f"{model}-prompt-v1",
        system_prompt="Return the requested JSON.",
        telemetry_recorder=store,
    )
    client.set_job_context(job_id=job_id, thread_id="thread", generation=1)
    return client


def test_successful_selector_canonicalizer_and_consolidator_runs_persist(
    tmp_path: Path,
) -> None:
    store, job_id = make_store(tmp_path)
    events = fixture_events()
    with stage_server() as endpoint:
        selector_client = stage_client(endpoint, store, "selector-model", job_id)
        selector = OpenAICompatSelectorEngine(selector_client)
        selection = asyncio.run(selector.select(events=events))

        canonicalizer_client = stage_client(
            endpoint, store, "canonicalizer-model", job_id
        )
        canonicalizer = OpenAICompatCanonicalizerEngine(canonicalizer_client)
        canonical = asyncio.run(
            canonicalizer.canonicalize(
                events=[events[0]],
            )
        )

        consolidator_client = stage_client(
            endpoint, store, "consolidator-model", job_id
        )
        consolidator = OpenAICompatConsolidatorEngine(consolidator_client)
        asyncio.run(
            consolidator.consolidate(
                base_capsule=None,
                events=[events[0]],
                snapshot_end_event_id=events[1].id,
                packet=build_lossless_packet(canonical, [events[0]]),
            )
        )

    assert selection.selected_event_ids == ("event-0",)
    rows = store.list_model_runs()
    assert [row["stage"] for row in rows] == [
        "selector",
        "canonicalizer",
        "consolidator",
    ]
    assert all(row["status"] == "SUCCEEDED" for row in rows)
    assert all(row["job_id"] == job_id for row in rows)
    assert all(row["thread_id"] == "thread" for row in rows)
    assert all(row["generation"] == 1 for row in rows)
    assert all(row["input_tokens"] == 41 for row in rows)
    assert all(row["reasoning_tokens"] == 0 for row in rows)
    assert all(row["raw_response_hash"] for row in rows)
    assert all("Authorization" not in row["metadata_json"] for row in rows)
    assert json.loads(rows[0]["source_refs_json"]) == ["event-0", "event-1"]
    assert json.loads(rows[1]["source_refs_json"]) == ["event-0"]
    assert json.loads(rows[2]["source_refs_json"]) == ["event-0"]
    assert StageHandler.requests[-1]["response_format"]["json_schema"]["strict"] is True
    assert (
        StageHandler.requests[-1]["response_format"]["json_schema"]["name"]
        == "consolidator_response_v1"
    )
    assert job_id


def test_worker_attaches_job_context_to_all_persistent_stage_runs(
    tmp_path: Path,
) -> None:
    store, job_id = make_store(tmp_path)
    with stage_server() as endpoint:
        config = RuntimeConfig(
            db_path=str(tmp_path / "memory.db"),
            backend_url=endpoint,
            backend_api_key=None,
            backend_model="backend-model",
            selector_url=endpoint,
            selector_model="selector-model",
            selector_api_key=None,
            canonicalizer_url=endpoint,
            canonicalizer_model="canonicalizer-model",
            canonicalizer_api_key=None,
            consolidator_url=endpoint,
            consolidator_model="consolidator-model",
            consolidator_api_key=None,
            context_tokens=32_768,
            background_fraction=0.65,
            urgent_fraction=0.85,
            worker_poll_seconds=0.1,
            model_timeout_seconds=5,
        )
        engine = build_lossless_engine(config, telemetry_recorder=store)
        assert engine is not None
        assert (
            asyncio.run(
                CompactionWorker(store, engine, "telemetry-worker").run_once()
            )
            == "PROMOTED"
        )

    rows = store.list_model_runs(job_id=job_id)
    assert [row["stage"] for row in rows] == [
        "selector",
        "canonicalizer",
        "consolidator",
    ]
    assert all(row["job_id"] == job_id for row in rows)
    assert all(row["thread_id"] == "thread" for row in rows)
    assert all(row["generation"] == 1 for row in rows)
    assert all(row["status"] == "SUCCEEDED" for row in rows)


@pytest.mark.parametrize(
    ("stage", "mode"),
    [
        ("selector", "selector-invalid"),
        ("canonicalizer", "canonicalizer-malformed"),
        ("consolidator", "consolidator-malformed"),
    ],
)
def test_failed_stage_model_calls_persist_failure_telemetry(
    tmp_path: Path,
    stage: str,
    mode: str,
) -> None:
    store, job_id = make_store(tmp_path)
    events = fixture_events()

    with stage_server(mode) as endpoint:
        if stage == "selector":
            client = stage_client(endpoint, store, "selector-model", job_id)
            with pytest.raises(ModelProtocolError):
                asyncio.run(
                    OpenAICompatSelectorEngine(client).select(events=events)
                )
        elif stage == "canonicalizer":
            client = stage_client(
                endpoint, store, "canonicalizer-model", job_id
            )
            with pytest.raises(ModelProtocolError):
                asyncio.run(
                    OpenAICompatCanonicalizerEngine(client).canonicalize(
                        events=[events[0]]
                    )
                )
        else:
            canonicalizer_client = stage_client(
                endpoint, store, "canonicalizer-model", job_id
            )
            canonical = asyncio.run(
                OpenAICompatCanonicalizerEngine(
                    canonicalizer_client
                ).canonicalize(events=[events[0]])
            )
            client = stage_client(
                endpoint, store, "consolidator-model", job_id
            )
            with pytest.raises(ModelProtocolError):
                asyncio.run(
                    OpenAICompatConsolidatorEngine(client).consolidate(
                        base_capsule=None,
                        events=[events[0]],
                        snapshot_end_event_id=events[0].id,
                        packet=build_lossless_packet(canonical, [events[0]]),
                    )
                )

    row = store.list_model_runs()[-1]
    assert row["stage"] == stage
    assert row["status"] == "FAILED"
    assert row["raw_response_hash"]
    assert row["diagnostic_excerpt"]
    assert row["error"]


def test_telemetry_persistence_failure_is_a_model_failure() -> None:
    class FailingRecorder:
        def record_model_run(self, record: dict) -> str:
            raise RuntimeError("telemetry database unavailable")

        def update_model_run(self, run_id: str, **updates: object) -> None:
            raise AssertionError("no run should have been created")

    client = OpenAICompatStructuredClient(
        endpoint="http://127.0.0.1:9/v1",
        model="model",
        prompt_version="prompt-v1",
        system_prompt="Return JSON.",
        telemetry_recorder=FailingRecorder(),
    )
    client.set_call_context(stage="consolidator", source_refs=("event-0",))

    async def invoke() -> None:
        await client.complete_json({"events": [{"id": "event-0"}]})

    with pytest.raises(Exception) as error:
        asyncio.run(invoke())
    assert isinstance(
        error.value,
        (TelemetryPersistenceError, OSError, RuntimeError),
    )


def test_legacy_model_runs_table_migrates_without_losing_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.db"
    store = SQLiteStore(database)
    store.create_project("project")
    store.create_thread("thread", "project")
    job_id = store.create_compaction_job(
        thread_id="thread",
        base_capsule_id=None,
        snapshot_start_event_id=None,
        snapshot_end_event_id=None,
    )
    with store.connect() as connection:
        connection.execute("DROP TABLE model_runs")
        connection.execute(
            """
            CREATE TABLE model_runs(
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES compaction_jobs(id),
                pass_name TEXT NOT NULL,
                model TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                output_hash TEXT,
                reasoning_tokens INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER,
                wall_ms REAL,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO model_runs(
                id, job_id, pass_name, model, input_hash, output_hash,
                reasoning_tokens, input_tokens, output_tokens, wall_ms,
                status, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-run",
                job_id,
                "selector",
                "legacy-model",
                "input",
                "output",
                0,
                10,
                2,
                3.0,
                "SUCCEEDED",
                "{}",
                "2026-01-01T00:00:00+00:00",
            ),
        )

    migrated = SQLiteStore(database)
    rows = migrated.list_model_runs(job_id=job_id)
    assert len(rows) == 1
    assert rows[0]["id"] == "legacy-run"
    assert rows[0]["stage"] == "selector"
    assert rows[0]["thread_id"] == "thread"
    assert rows[0]["generation"] == 1
