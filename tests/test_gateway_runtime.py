from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from memory_gateway.config import RuntimeConfig
from memory_gateway.fake_engines import PerfectCompactionEngine
from memory_gateway.gateway import create_app


class BlockingCompactionEngine:
    """Hold background maintenance open while foreground requests continue."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.delegate = PerfectCompactionEngine()

    async def compact(self, **kwargs):
        self.started.set()
        await self.release.wait()
        return await self.delegate.compact(**kwargs)


class BackendHandler(BaseHTTPRequestHandler):
    last_model: str | None = None
    last_authorization: str | None = None

    def log_message(self, *_args) -> None:
        return

    def do_POST(self) -> None:
        body_length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(body_length))
        type(self).last_model = body.get("model")
        type(self).last_authorization = self.headers.get("authorization")
        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "solar response"},
                    "finish_reason": "stop",
                }
            ],
        }
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@contextmanager
def backend_server() -> Iterator[str]:
    BackendHandler.last_model = None
    BackendHandler.last_authorization = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), BackendHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_gateway_routes_solar_and_runs_background_compaction(tmp_path: Path) -> None:
    with backend_server() as backend_url:
        config = RuntimeConfig(
            db_path=str(tmp_path / "memory.db"),
            backend_url=backend_url,
            backend_api_key="backend-test-key",
            backend_model="upstage/solar-pro4",
            selector_url="http://127.0.0.1:1234/v1",
            selector_model="qwen3.5-4b@q6_k",
            selector_api_key=None,
            canonicalizer_url="http://127.0.0.1:1234/v1",
            canonicalizer_model="qwen3.5-4b@q6_k",
            canonicalizer_api_key=None,
            consolidator_url=None,
            consolidator_model=None,
            consolidator_api_key=None,
            context_tokens=10,
            foreground_target_tokens=1,
            foreground_hard_limit_tokens=64_000,
            background_fraction=0.5,
            urgent_fraction=0.8,
            worker_poll_seconds=0.01,
            model_timeout_seconds=1,
            cold_memory_mode="shadow",
        )
        app = create_app(config=config, compaction_engine=PerfectCompactionEngine())

        async def run() -> None:
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://orchid") as client:
                    response = await client.post(
                        "/v1/chat/completions",
                        headers={
                            "x-memory-project": "project",
                            "x-memory-thread": "thread",
                        },
                        json={
                            "model": "client-model-is-overridden",
                            "messages": [{"role": "user", "content": "hello"}],
                        },
                    )
                    assert response.status_code == 200
                    assert response.json()["choices"][0]["message"]["content"] == "solar response"

                    debug = None
                    for _ in range(100):
                        debug_response = await client.get("/debug/thread/thread")
                        debug = debug_response.json()
                        if debug["last_promotion"] is not None:
                            break
                        await asyncio.sleep(0.01)
                    assert debug is not None
                    assert debug["last_promotion"]["status"] == "PROMOTED"
                    assert debug["active_capsule"]["state"] == "ACTIVE"
                    assert debug["estimated_active_tokens"] > 0
                    assert debug["compaction_wait_ms"] == 0
                    assert debug["memory_maintenance_awaited"] is False
                    assert sum(debug["component_tokens"].values()) == debug[
                        "estimated_active_tokens"
                    ]

        asyncio.run(run())
        assert BackendHandler.last_model == "upstage/solar-pro4"
        assert BackendHandler.last_authorization == "Bearer backend-test-key"
        assert app.state.cold_memory_telemetry is not None
        assert app.state.cold_memory_telemetry.metrics()["flushed_operation_count"] >= 1


def test_gateway_returns_503_when_foreground_hard_limit_exceeded(
    tmp_path: Path, monkeypatch
) -> None:
    with backend_server() as backend_url:
        telemetry_path = tmp_path / "foreground.jsonl"
        monkeypatch.setenv("ORCHID_FOREGROUND_TELEMETRY_PATH", str(telemetry_path))
        config = RuntimeConfig(
            db_path=str(tmp_path / "memory.db"),
            backend_url=backend_url,
            backend_api_key=None,
            backend_model="upstage/solar-pro4",
            selector_url="http://127.0.0.1:1234/v1",
            selector_model="qwen3.5-4b@q6_k",
            selector_api_key=None,
            canonicalizer_url="http://127.0.0.1:1234/v1",
            canonicalizer_model="qwen3.5-4b@q6_k",
            canonicalizer_api_key=None,
            consolidator_url=None,
            consolidator_model=None,
            consolidator_api_key=None,
            context_tokens=128_000,
            background_fraction=0.5,
            urgent_fraction=0.8,
            worker_poll_seconds=0.01,
            model_timeout_seconds=1,
            foreground_hard_limit_tokens=16,
        )
        app = create_app(config=config, compaction_engine=PerfectCompactionEngine())

        async def run() -> None:
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://orchid") as client:
                    response = await client.post(
                        "/v1/chat/completions",
                        headers={
                            "x-memory-project": "project",
                            "x-memory-thread": "thread",
                        },
                        json={
                            "messages": [
                                {
                                    "role": "user",
                                    "content": "x" * 400,
                                }
                            ],
                        },
                    )
                    assert response.status_code == 503
                    assert response.headers.get("retry-after") == "60"
                    body = response.json()
                    assert body["error"]["type"] == "context_capacity_exceeded"
                    assert body["error"]["limit_tokens"] == 16
                    assert body["error"]["estimated_tokens"] > 16
                    assert body["error"]["job_id"]
                    rows = [
                        json.loads(line)
                        for line in telemetry_path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                    assert len(rows) == 1
                    assert rows[0]["gateway_outcome"] == "context_capacity_exceeded"
                    assert rows[0]["compaction_wait_ms"] == 0.0
                    assert rows[0]["memory_maintenance_awaited"] is False

        asyncio.run(run())


def test_foreground_request_does_not_await_running_compaction(tmp_path: Path) -> None:
    with backend_server() as backend_url:
        engine = BlockingCompactionEngine()
        config = RuntimeConfig(
            db_path=str(tmp_path / "memory.db"),
            backend_url=backend_url,
            backend_api_key=None,
            backend_model="upstage/solar-pro4",
            selector_url="http://127.0.0.1:1234/v1",
            selector_model="qwen3.5-4b@q6_k",
            selector_api_key=None,
            canonicalizer_url="http://127.0.0.1:1234/v1",
            canonicalizer_model="qwen3.5-4b@q6_k",
            canonicalizer_api_key=None,
            consolidator_url=None,
            consolidator_model=None,
            consolidator_api_key=None,
            context_tokens=128_000,
            foreground_target_tokens=1,
            foreground_hard_limit_tokens=48_000,
            background_fraction=0.5,
            urgent_fraction=0.8,
            worker_poll_seconds=0.001,
            model_timeout_seconds=1,
        )
        app = create_app(config=config, compaction_engine=engine)

        async def run() -> None:
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://orchid",
                ) as client:
                    first_message = {"role": "user", "content": "start background work"}
                    first = await client.post(
                        "/v1/chat/completions",
                        headers={
                            "x-memory-project": "project",
                            "x-memory-thread": "thread",
                        },
                        json={"messages": [first_message]},
                    )
                    assert first.status_code == 200
                    await asyncio.wait_for(engine.started.wait(), timeout=1)

                    second = await asyncio.wait_for(
                        client.post(
                            "/v1/chat/completions",
                            headers={
                                "x-memory-project": "project",
                                "x-memory-thread": "thread",
                            },
                            json={
                                "messages": [
                                    first_message,
                                    {"role": "user", "content": "continue immediately"},
                                ]
                            },
                        ),
                        timeout=1,
                    )
                    assert second.status_code == 200
                    running = (await client.get("/debug/jobs")).json()[
                        "queued_or_running"
                    ]
                    assert any(job["status"] == "RUNNING" for job in running)
                    engine.release.set()

        asyncio.run(run())


def _capacity_snapshot(estimated_tokens: int, *, hard_limit_exceeded: bool = False):
    from memory_gateway.context import ContextSnapshot

    return ContextSnapshot(
        active_capsule_id=None,
        raw_tail_event_ids=(),
        capsule_tokens=0,
        raw_tail_tokens=0,
        request_tokens=estimated_tokens,
        cold_memory_tokens=0,
        estimated_tokens=estimated_tokens,
        messages=[],
        hard_limit_exceeded=hard_limit_exceeded,
    )


def test_capacity_check_uses_the_declared_hard_ceiling() -> None:
    from types import SimpleNamespace

    from memory_gateway.gateway import foreground_needs_capacity_wait

    runtime = SimpleNamespace(
        foreground_hard_limit_tokens=48_000,
        compaction_reserved_output_tokens=8_192,
    )
    assert foreground_needs_capacity_wait(_capacity_snapshot(40_000), runtime) is False
    assert (
        foreground_needs_capacity_wait(
            _capacity_snapshot(40_000, hard_limit_exceeded=True), runtime
        )
        is True
    )


def test_capacity_check_does_not_apply_legacy_estimator_scaling() -> None:
    from types import SimpleNamespace

    from memory_gateway.gateway import foreground_needs_capacity_wait

    runtime = SimpleNamespace(
        foreground_hard_limit_tokens=128_000,
        compaction_reserved_output_tokens=8_192,
    )
    assert foreground_needs_capacity_wait(_capacity_snapshot(114_716), runtime) is False
    assert (
        foreground_needs_capacity_wait(
            _capacity_snapshot(114_716, hard_limit_exceeded=True), runtime
        )
        is True
    )


def test_foreground_capacity_wait_budget_is_zero() -> None:
    from types import SimpleNamespace

    from memory_gateway.frozen_pipeline import MAXIMUM_FOREGROUND_WAIT_MS
    from memory_gateway.gateway import foreground_capacity_wait_budget_seconds

    sealed = SimpleNamespace(model_timeout_seconds=180, foreground_hard_limit_tokens=128_000)
    keep = SimpleNamespace(model_timeout_seconds=180, foreground_hard_limit_tokens=48_000)
    assert MAXIMUM_FOREGROUND_WAIT_MS == 0
    assert foreground_capacity_wait_budget_seconds(sealed) == 0
    assert foreground_capacity_wait_budget_seconds(keep) == 0


def test_capacity_path_enqueues_without_waiting(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from memory_gateway.db import SQLiteStore
    from memory_gateway.gateway import _wait_out_hard_limit

    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    job_id = store.create_compaction_job(
        thread_id="thread",
        base_capsule_id=None,
        snapshot_start_event_id="evt_a",
        snapshot_end_event_id="evt_a",
    )

    class Scheduler:
        def enqueue_urgent(self, thread_id: str) -> str:
            assert thread_id == "thread"
            return job_id

    runtime = SimpleNamespace(
        model_timeout_seconds=180,
        foreground_hard_limit_tokens=48_000,
        worker_poll_seconds=0.05,
    )

    async def run() -> None:
        context, returned_job_id = await _wait_out_hard_limit(
            store=store,
            assembler=object(),
            scheduler=Scheduler(),
            runtime=runtime,
            thread_id="thread",
            inbound=[{"role": "user", "content": "x"}],
            project_id="project",
            context=_capacity_snapshot(70_030, hard_limit_exceeded=True),
        )
        assert returned_job_id == job_id
        assert context.compaction_wait_ms == 0.0
        assert context.memory_maintenance_awaited is False

    asyncio.run(run())
