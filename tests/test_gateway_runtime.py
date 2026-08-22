from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import httpx

from memory_gateway.config import RuntimeConfig
from memory_gateway.fake_engines import PerfectCompactionEngine
from memory_gateway.gateway import create_app


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

        asyncio.run(run())
        assert BackendHandler.last_model == "upstage/solar-pro4"
        assert BackendHandler.last_authorization == "Bearer backend-test-key"
        assert app.state.cold_memory_telemetry is not None
        assert app.state.cold_memory_telemetry.metrics()["flushed_operation_count"] >= 1
