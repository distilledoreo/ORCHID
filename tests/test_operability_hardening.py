from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import httpx

from memory_gateway.compaction import CompactionResult, CompactionWorker
from memory_gateway.config import RuntimeConfig
from memory_gateway.db import SQLiteStore
from memory_gateway.fake_engines import PerfectCompactionEngine
from memory_gateway.gateway import _stream_response, create_app
from memory_gateway.ingestion import append_novel_messages, parse_stream_message
from memory_gateway.scheduler import CompactionScheduler, ThresholdPolicy


def _config(tmp_path: Path, backend_url: str) -> RuntimeConfig:
    return RuntimeConfig(
        db_path=str(tmp_path / "memory.db"),
        backend_url=backend_url,
        backend_api_key=None,
        backend_model="upstage/solar-pro4",
        selector_url="http://127.0.0.1:1234/v1",
        selector_model="fake-selector",
        selector_api_key=None,
        canonicalizer_url="http://127.0.0.1:1234/v1",
        canonicalizer_model="fake-canonicalizer",
        canonicalizer_api_key=None,
        consolidator_url=None,
        consolidator_model=None,
        consolidator_api_key=None,
        context_tokens=100,
        background_fraction=0.25,
        urgent_fraction=0.75,
        worker_poll_seconds=0.01,
        model_timeout_seconds=1,
        cold_memory_mode="off",
    )


class FaultBackendHandler(BaseHTTPRequestHandler):
    scenarios: list[str] = []
    request_count = 0
    lock = threading.Lock()

    def log_message(self, *_args) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        self.rfile.read(length)
        with type(self).lock:
            type(self).request_count += 1
            scenario = type(self).scenarios.pop(0) if type(self).scenarios else "valid"
        if scenario == "http_error":
            body = b'{"error":{"message":"upstream unavailable"}}'
            self.send_response(503)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.flush()
        if scenario == "disconnect_before":
            return
        if scenario == "partial_text":
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"part"}}]}\n\n')
            self.wfile.flush()
            return
        if scenario == "partial_tool":
            self.wfile.write(
                b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"build","arguments":"{\\"x\\":"}}]}}]}\n\n'
            )
            self.wfile.flush()
            return
        if scenario == "missing_finish":
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"no finish"}}]}\n\n')
            self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        if scenario == "slow_valid":
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"slow"}}]}\n\n')
            self.wfile.flush()
            time.sleep(0.1)
        if scenario == "error_frame":
            self.wfile.write(
                b'data: {"error":{"type":"upstream","message":"forced provider error"}}\n\n'
            )
            self.wfile.flush()
            return
        frames = [
            b'data: {"choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        for frame in frames:
            self.wfile.write(frame)
            self.wfile.flush()


@contextmanager
def fault_backend(scenarios: list[str]) -> Iterator[str]:
    FaultBackendHandler.scenarios = list(scenarios)
    FaultBackendHandler.request_count = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), FaultBackendHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_ordered_ingestion_reuses_only_resent_history_prefix(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    first = append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
            {"role": "tool", "tool_call_id": "c", "content": "C"},
        ],
        request_id="request-1",
        source="request",
    )
    assert len(first.appended_events) == 3
    second = append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
            {"role": "tool", "tool_call_id": "c", "content": "C"},
            {"role": "assistant", "content": "D"},
            {"role": "tool", "tool_call_id": "e", "content": "E"},
        ],
        request_id="request-2",
        source="request",
    )
    assert second.reused_prefix_count == 3
    assert len(second.appended_events) == 2
    assert len(store.list_events("thread")) == 5

    repeated = append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[{"role": "user", "content": "A-new-position"}],
        request_id="request-3",
        source="request",
    )
    assert len(repeated.appended_events) == 1
    assert len(store.list_events("thread")) == 6


def test_coalescing_bounds_pressure_signals_while_job_runs(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    for index in range(4):
        store.append_event(
            project_id="project",
            thread_id="thread",
            event_type="user_message",
            role="user",
            content=f"initial-{index}",
            token_count=20,
        )
    scheduler = CompactionScheduler(
        store,
        ThresholdPolicy(usable_context_tokens=10, background_fraction=0.5),
    )
    first_job = scheduler.maybe_enqueue("thread")
    assert first_job is not None
    claim = store.claim_next_job("worker", lease_seconds=60)
    assert claim is not None
    for index in range(50):
        store.append_event(
            project_id="project",
            thread_id="thread",
            event_type="tool_result",
            role="tool",
            content=f"new-{index}",
            token_count=20,
        )
        assert scheduler.maybe_enqueue("thread") == first_job
    active = store.list_jobs(thread_id="thread", statuses=("QUEUED", "RUNNING"))
    assert [job["id"] for job in active] == [first_job]
    assert store.get_compaction_state("thread")["compaction_dirty"] == 1


def test_provider_stream_failures_emit_terminal_frame_and_next_request_progresses(
    tmp_path: Path,
) -> None:
    scenarios = [
        "disconnect_before",
        "valid",
        "partial_text",
        "valid",
        "partial_tool",
        "valid",
        "error_frame",
        "valid",
        "missing_finish",
        "valid",
        "http_error",
        "valid",
    ]
    with fault_backend(scenarios) as backend_url:
        app = create_app(
            config=_config(tmp_path, backend_url),
            compaction_engine=PerfectCompactionEngine(),
        )

        async def run() -> None:
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://orchid") as client:
                    for index in range(len(scenarios)):
                        response = await client.post(
                            "/v1/chat/completions",
                            headers={"x-memory-project": "project", "x-memory-thread": "thread"},
                            json={
                                "stream": True,
                                "messages": [{"role": "user", "content": f"turn-{index}"}],
                            },
                        )
                        assert response.status_code == 200
                        assert b"finish_reason" in response.content
                        assert b"[DONE]" in response.content
                    streams = app.state.store.list_gateway_stream_runs(thread_id="thread")
                    assert len(streams) == len(scenarios)
                    assert all(row["cleanup_completed_at"] for row in streams)
                    assert any(row["status"] == "FAILED" for row in streams)
                    assert any(row["status"] == "SUCCEEDED" for row in streams)

        asyncio.run(run())
    assert FaultBackendHandler.request_count == len(scenarios)


def test_provider_stream_fault_injection_120_cycle_soak(tmp_path: Path) -> None:
    scenarios = [
        "partial_text" if index % 13 == 0 else
        "error_frame" if index % 29 == 0 else
        "disconnect_before" if index % 41 == 0 else
        "valid"
        for index in range(120)
    ]
    with fault_backend(scenarios) as backend_url:
        app = create_app(
            config=_config(tmp_path, backend_url),
            compaction_engine=PerfectCompactionEngine(),
        )

        async def run() -> None:
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://orchid") as client:
                    for index in range(120):
                        response = await client.post(
                            "/v1/chat/completions",
                            headers={"x-memory-project": "project", "x-memory-thread": "soak"},
                            json={
                                "stream": True,
                                "messages": [{"role": "user", "content": f"soak-{index}"}],
                            },
                        )
                        assert response.status_code == 200
                        assert b"[DONE]" in response.content
                    streams = app.state.store.list_gateway_stream_runs(thread_id="soak")
                    assert len(streams) == 120
                    assert all(row["cleanup_completed_at"] for row in streams)
                    assert not any(row["status"] == "ACCEPTED" for row in streams)

        asyncio.run(run())
    assert FaultBackendHandler.request_count == 120


def test_client_cancellation_is_distinct_and_next_request_progresses(
    tmp_path: Path,
) -> None:
    with fault_backend(["slow_valid", "valid"]) as backend_url:
        app = create_app(
            config=_config(tmp_path, backend_url),
            compaction_engine=PerfectCompactionEngine(),
        )

        async def run() -> None:
            async with app.router.lifespan_context(app):
                store = app.state.store
                store.ensure_project_and_thread("project", "cancel-thread")
                run_id = store.create_gateway_stream_run(
                    project_id="project",
                    thread_id="cancel-thread",
                    request_id="cancel-1",
                )
                stream = _stream_response(
                    backend=backend_url,
                    outbound={
                        "stream": True,
                        "messages": [{"role": "user", "content": "cancel me"}],
                    },
                    headers={"content-type": "application/json"},
                    store=store,
                    assembler=app.state.assembler,
                    scheduler=app.state.scheduler,
                    project_id="project",
                    thread_id="cancel-thread",
                    request_id="cancel-1",
                    stream_run_id=run_id,
                )
                first_chunk = await anext(stream)
                assert b"slow" in first_chunk
                await stream.aclose()
                cancelled = store.list_gateway_stream_runs(thread_id="cancel-thread")
                assert cancelled[0]["status"] == "CANCELLED"
                assert cancelled[0]["failure_category"] == "client_cancelled"
                assert cancelled[0]["cleanup_completed_at"]

                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://orchid"
                ) as client:
                    response = await client.post(
                        "/v1/chat/completions",
                        headers={
                            "x-memory-project": "project",
                            "x-memory-thread": "cancel-thread",
                        },
                        json={
                            "stream": True,
                            "messages": [{"role": "user", "content": "after cancel"}],
                        },
                    )
                    assert response.status_code == 200
                    assert b"[DONE]" in response.content

        asyncio.run(run())
    assert FaultBackendHandler.request_count == 2


def test_stream_message_parser_handles_frames_split_across_reads() -> None:
    chunks = [
        b'data: {"choices":[{"delta":{"content":"hel',
        b'lo"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b"data: [DONE]\n\n",
    ]
    message = parse_stream_message(chunks)
    assert message == {"role": "assistant", "content": "hello"}


def test_coalesced_worker_requeues_one_fresh_snapshot_after_dirty_history(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    for index in range(4):
        store.append_event(
            project_id="project",
            thread_id="thread",
            event_type="user_message",
            role="user",
            content=f"initial-{index}",
            token_count=20,
        )
    scheduler = CompactionScheduler(
        store,
        ThresholdPolicy(usable_context_tokens=10, background_fraction=0.5),
    )
    first_job = scheduler.maybe_enqueue("thread")
    assert first_job is not None
    engine = BlockingPerfectEngine()
    worker = CompactionWorker(
        store,
        engine,
        "worker",
        on_job_finished=scheduler.reconcile_after_job,
    )

    async def run() -> None:
        task = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(engine.started.wait(), timeout=2)
        for index in range(4):
            store.append_event(
                project_id="project",
                thread_id="thread",
                event_type="tool_result",
                role="tool",
                content=f"during-{index}",
                token_count=20,
            )
            assert scheduler.maybe_enqueue("thread") == first_job
        engine.release.set()
        assert await asyncio.wait_for(task, timeout=2) == "PROMOTED"

    asyncio.run(run())
    active = store.list_jobs(thread_id="thread", statuses=("QUEUED", "RUNNING"))
    assert len(active) == 1
    assert active[0]["id"] != first_job


class BlockingPerfectEngine(PerfectCompactionEngine):
    started: asyncio.Event
    release: asyncio.Event

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def compact(self, **kwargs) -> CompactionResult:
        self.started.set()
        await self.release.wait()
        return await super().compact(**kwargs)
