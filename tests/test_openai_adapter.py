from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

from memory_gateway.compaction import Event, compute_input_hash, validate_compaction_result
from memory_gateway.compaction import CompactionWorker, queue_snapshot_job
from memory_gateway.db import SQLiteStore
from memory_gateway.openai_adapter import (
    ModelProtocolError,
    ModelTransportError,
    OpenAICompatCompactionEngine,
)
from memory_gateway.structured_client import OpenAICompatStructuredClient
from memory_gateway.pipeline_adapters import (
    CANONICALIZER_RESPONSE_FORMAT,
    OpenAICompatCanonicalizerEngine,
    OpenAICompatSelectorEngine,
    SELECTOR_RESPONSE_FORMAT,
)


class ScenarioHandler(BaseHTTPRequestHandler):
    scenario = "valid"
    delay = 0.0
    last_request_body = None

    def log_message(self, *_args) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        request_body = json.loads(self.rfile.read(length))
        type(self).last_request_body = request_body
        if self.scenario == "timeout":
            time.sleep(self.delay)
        if self.scenario == "http-500":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"backend failed")
            return
        response_object = {
            "content": "durable capsule",
            "covered_event_ids": ["event-1"],
            "evidence_event_ids": ["event-1"],
        }
        if self.scenario == "wrong-evidence":
            response_object["evidence_event_ids"] = ["missing-event"]
        if self.scenario == "malformed":
            response_content = '{"content":'
        elif self.scenario == "truncated":
            response_content = '{"content":"truncated"'
        else:
            response_content = json.dumps(response_object)
        if self.scenario == "stream":
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            chunks = [response_content[: len(response_content) // 2], response_content[len(response_content) // 2 :]]
            for chunk in chunks:
                frame = json.dumps({"choices": [{"delta": {"content": chunk}}]})
                self.wfile.write(f"data: {frame}\n\n".encode())
            usage = json.dumps(
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 42,
                        "completion_tokens": 8,
                        "completion_tokens_details": {"reasoning_tokens": 0},
                    },
                }
            )
            self.wfile.write(f"data: {usage}\n\ndata: [DONE]\n\n".encode())
            return
        payload = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": response_content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 42,
                "completion_tokens": 8,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@contextmanager
def fake_server(scenario: str, delay: float = 0.0) -> Iterator[str]:
    ScenarioHandler.scenario = scenario
    ScenarioHandler.delay = delay
    ScenarioHandler.last_request_body = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), ScenarioHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def fixture_event() -> Event:
    content = "event content"
    return Event(
        id="event-1",
        sequence=1,
        content=content,
        content_hash=sha256(content.encode()).hexdigest(),
        event_type="user_message",
        role="user",
    )


def adapter(endpoint: str, **kwargs) -> OpenAICompatCompactionEngine:
    return OpenAICompatCompactionEngine(
        endpoint=endpoint,
        model="fake-model",
        prompt_version="test-prompt-v1",
        system_prompt="Return the compaction result JSON.",
        timeout=kwargs.pop("timeout", 1.0),
        generation_settings=kwargs,
    )


def test_non_streaming_adapter_returns_immutable_result_and_telemetry() -> None:
    event = fixture_event()
    with fake_server("valid") as endpoint:
        engine = adapter(endpoint)
        result = asyncio.run(
            engine.compact(base_capsule=None, events=[event], snapshot_end_event_id=event.id)
        )
    assert result.content == "durable capsule"
    assert result.covered_event_ids == (event.id,)
    assert result.input_hash == compute_input_hash(None, [event], event.id)
    assert engine.last_telemetry["reasoning_tokens"] == 0
    assert engine.last_telemetry["input_tokens"] == 42
    assert engine.last_telemetry["finish_reason"] == "stop"


def test_streaming_and_slow_valid_responses_use_same_adapter() -> None:
    event = fixture_event()
    with fake_server("stream", delay=0.02) as endpoint:
        engine = adapter(endpoint, stream=True, timeout=1.0)
        result = asyncio.run(
            engine.compact(base_capsule=None, events=[event], snapshot_end_event_id=event.id)
        )
    assert result.content == "durable capsule"
    assert engine.last_telemetry["stream"] is True
    assert engine.last_telemetry["wall_ms"] > 0


def test_shared_structured_client_parses_stage_json() -> None:
    with fake_server("valid") as endpoint:
        client = OpenAICompatStructuredClient(
            endpoint=endpoint,
            model="fake-stage-model",
            prompt_version="stage-v1",
            system_prompt="Return JSON.",
        )
        result = asyncio.run(client.complete_json({"events": ["event-1"]}))
    assert result["covered_event_ids"] == ["event-1"]
    assert client.last_telemetry["status"] == "SUCCEEDED"


def test_selector_and_canonicalizer_send_json_schema_response_formats() -> None:
    event = fixture_event()
    with fake_server("valid") as endpoint:
        selector_client = OpenAICompatStructuredClient(
            endpoint=endpoint,
            model="fake-selector",
            prompt_version="selector-v1",
            system_prompt="Return selector JSON.",
        )
        selector = OpenAICompatSelectorEngine(selector_client)
        with pytest.raises(ModelProtocolError, match="selected_event_ids"):
            asyncio.run(selector.select(events=[event]))
        assert ScenarioHandler.last_request_body["response_format"] == SELECTOR_RESPONSE_FORMAT

    with fake_server("valid") as endpoint:
        canonicalizer_client = OpenAICompatStructuredClient(
            endpoint=endpoint,
            model="fake-canonicalizer",
            prompt_version="canonicalizer-v1",
            system_prompt="Return canonicalizer JSON.",
        )
        canonicalizer = OpenAICompatCanonicalizerEngine(canonicalizer_client)
        with pytest.raises(ModelProtocolError, match="canonicalizer response"):
            asyncio.run(canonicalizer.canonicalize(events=[event]))
        assert (
            ScenarioHandler.last_request_body["response_format"]
            == CANONICALIZER_RESPONSE_FORMAT
        )


@pytest.mark.parametrize("scenario", ["malformed", "truncated"])
def test_malformed_or_truncated_model_output_is_rejected(scenario: str) -> None:
    event = fixture_event()
    with fake_server(scenario) as endpoint:
        engine = adapter(endpoint)
        with pytest.raises(ModelProtocolError):
            asyncio.run(engine.compact(base_capsule=None, events=[event], snapshot_end_event_id=event.id))


def test_http_failure_and_timeout_fail_as_transport_errors() -> None:
    event = fixture_event()
    with fake_server("http-500") as endpoint:
        engine = adapter(endpoint)
        with pytest.raises(ModelTransportError):
            asyncio.run(engine.compact(base_capsule=None, events=[event], snapshot_end_event_id=event.id))
        assert engine.last_telemetry["status"] == "FAILED"
    with fake_server("timeout", delay=0.1) as endpoint:
        engine = adapter(endpoint, timeout=0.01)
        with pytest.raises(ModelTransportError):
            asyncio.run(engine.compact(base_capsule=None, events=[event], snapshot_end_event_id=event.id))
        assert engine.last_telemetry["status"] == "FAILED"


def test_wrong_evidence_reaches_deterministic_worker_validation() -> None:
    event = fixture_event()
    with fake_server("wrong-evidence") as endpoint:
        engine = adapter(endpoint)
        result = asyncio.run(
            engine.compact(base_capsule=None, events=[event], snapshot_end_event_id=event.id)
        )
    class Snapshot:
        input_hash = result.input_hash
        events = ({"id": event.id},)
    with pytest.raises(ValueError, match="evidence_event_ids"):
        validate_compaction_result(Snapshot(), result)


def test_cancellation_does_not_return_partial_model_output() -> None:
    event = fixture_event()

    async def run_cancelled() -> OpenAICompatCompactionEngine:
        with fake_server("timeout", delay=0.2) as endpoint:
            engine = adapter(endpoint, timeout=1.0)
            task = asyncio.create_task(
                engine.compact(base_capsule=None, events=[event], snapshot_end_event_id=event.id)
            )
            await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert engine.last_telemetry["status"] == "FAILED"
            return engine

    asyncio.run(run_cancelled())


def test_openai_adapter_can_drive_worker_without_owning_promotion(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="event content",
        event_id="event-1",
    )
    job_id = queue_snapshot_job(store, "thread")
    with fake_server("valid") as endpoint:
        engine = adapter(endpoint)
        result = asyncio.run(CompactionWorker(store, engine, "adapter-worker").run_once())
    assert result == "PROMOTED"
    assert store.get_job(job_id)["status"] == "PROMOTED"
    assert store.get_active_capsule("thread")["content"] == "durable capsule"
    assert engine.last_telemetry["raw_response_hash"]
