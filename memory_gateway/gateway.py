from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .compaction import CompactionEngine, CompactionWorker
from .config import RuntimeConfig
from .context import ContextAssembler
from .db import SQLiteStore, new_id
from .pipeline_adapters import build_lossless_engine
from .scheduler import CompactionScheduler, ThresholdPolicy


def create_app(
    *,
    db_path: str | None = None,
    backend_url: str | None = None,
    config: RuntimeConfig | None = None,
    compaction_engine: CompactionEngine | None = None,
) -> FastAPI:
    runtime = config or RuntimeConfig.from_env(db_path=db_path, backend_url=backend_url)
    store = SQLiteStore(runtime.db_path)
    assembler = ContextAssembler(
        store,
        raw_tail_target_tokens=runtime.raw_tail_target_tokens,
        minimum_raw_tail_tokens=runtime.minimum_raw_tail_tokens,
    )
    scheduler = CompactionScheduler(
        store,
        ThresholdPolicy(
            usable_context_tokens=runtime.context_tokens,
            background_fraction=runtime.background_fraction,
            urgent_fraction=runtime.urgent_fraction,
        ),
    )
    engine = (
        compaction_engine
        if compaction_engine is not None
        else build_lossless_engine(runtime, telemetry_recorder=store)
    )
    worker = (
        CompactionWorker(
            store,
            engine,
            "gateway-compaction-worker",
            lease_seconds=runtime.lease_seconds,
            renewal_interval_seconds=runtime.lease_renewal_seconds,
            recover_expired_jobs=runtime.recover_expired_jobs,
        )
        if engine
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop_event = asyncio.Event()
        worker_task: asyncio.Task[None] | None = None
        if worker is not None:
            worker_task = asyncio.create_task(
                _worker_loop(worker, stop_event, runtime.worker_poll_seconds),
                name="orchid-compaction-worker",
            )
            app.state.worker_task = worker_task
        try:
            yield
        finally:
            stop_event.set()
            if worker_task is not None:
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass
            app.state.worker_task = None

    backend = runtime.backend_url
    app = FastAPI(title="Orchid Memory Gateway", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.runtime = runtime
    app.state.backend_url = backend
    app.state.assembler = assembler
    app.state.scheduler = scheduler
    app.state.compaction_engine = engine
    app.state.compaction_worker = worker
    app.state.worker_task = None

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {
            "status": "ok",
            "compaction": "configured" if worker is not None else "not_configured",
        }

    @app.get("/v1/models")
    async def models(request: Request) -> JSONResponse:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{backend}/v1/models",
                headers=_backend_headers(request, runtime.backend_api_key),
            )
        return JSONResponse(status_code=response.status_code, content=_json_or_text(response))

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> StreamingResponse | JSONResponse:
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "messages must be an array", "type": "invalid_request_error"}},
            )
        project_id = request.headers.get("x-memory-project", "default")
        thread_id = request.headers.get("x-memory-thread", "default")
        request_id = request.headers.get("x-request-id", new_id("req"))
        store.ensure_project_and_thread(project_id, thread_id)
        store.append_event(
            project_id=project_id,
            thread_id=thread_id,
            event_type="request",
            role="user",
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            metadata={"path": "/v1/chat/completions", "stream": bool(payload.get("stream"))},
            request_id=request_id,
        )
        context = assembler.assemble(thread_id, payload["messages"])
        outbound = dict(payload)
        outbound["messages"] = context.messages
        if runtime.backend_model:
            outbound["model"] = runtime.backend_model
        headers = _backend_headers(request, runtime.backend_api_key)
        headers["content-type"] = "application/json"

        if payload.get("stream", False):
            return StreamingResponse(
                _stream_response(
                    backend=backend,
                    outbound=outbound,
                    headers=headers,
                    store=store,
                    assembler=assembler,
                    scheduler=scheduler,
                    project_id=project_id,
                    thread_id=thread_id,
                    request_id=request_id,
                ),
                media_type="text/event-stream",
            )

        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                f"{backend}/v1/chat/completions",
                json=outbound,
                headers=headers,
            )
        response_text = response.text
        store.append_event(
            project_id=project_id,
            thread_id=thread_id,
            event_type="assistant_response" if response.is_success else "backend_error",
            role="assistant" if response.is_success else "system",
            content=response_text,
            metadata={"status_code": response.status_code, "stream": False},
            request_id=request_id,
        )
        _maybe_enqueue(thread_id, scheduler)
        return JSONResponse(status_code=response.status_code, content=_json_or_text(response))

    @app.get("/debug/thread/{thread_id}")
    async def debug_thread(thread_id: str) -> dict[str, Any]:
        context = assembler.assemble(thread_id, [])
        jobs = store.list_jobs(thread_id=thread_id)
        active = store.get_active_capsule(thread_id)
        promoted = next((job for job in jobs if job["status"] == "PROMOTED"), None)
        failed = next((job for job in jobs if job["status"] == "FAILED"), None)
        return {
            "thread_id": thread_id,
            "active_capsule": _capsule_debug(active),
            "raw_tail_event_ids": list(context.raw_tail_event_ids),
            "raw_tail_tokens": context.raw_tail_tokens,
            "capsule_tokens": context.capsule_tokens,
            "estimated_active_tokens": context.estimated_tokens,
            "last_snapshot_watermark": (
                active["covered_end_event_id"] if active else None
            ),
            "jobs": jobs,
            "last_promotion": promoted,
            "last_failure": failed,
        }

    @app.get("/debug/jobs")
    async def debug_jobs(limit: int = 100) -> dict[str, Any]:
        bounded_limit = max(1, min(limit, 500))
        jobs = store.list_jobs(limit=bounded_limit)
        return {
            "jobs": jobs,
            "queued_or_running": [
                job for job in jobs if job["status"] in {"QUEUED", "RUNNING"}
            ],
            "last_failure": next((job for job in jobs if job["status"] == "FAILED"), None),
        }

    return app


async def _worker_loop(
    worker: CompactionWorker,
    stop_event: asyncio.Event,
    poll_seconds: float,
) -> None:
    while not stop_event.is_set():
        try:
            result = await worker.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            result = "FAILED"
        if result is None or result in {"FAILED", "LOST"}:
            await asyncio.sleep(poll_seconds)


def _maybe_enqueue(
    thread_id: str,
    scheduler: CompactionScheduler,
) -> str | None:
    return scheduler.maybe_enqueue(thread_id)


def _backend_headers(request: Request, configured_api_key: str | None) -> dict[str, str]:
    authorization = request.headers.get("authorization")
    if authorization is None and configured_api_key:
        authorization = f"Bearer {configured_api_key}"
    return {"authorization": authorization} if authorization else {}


def _capsule_debug(capsule: dict[str, Any] | None) -> dict[str, Any] | None:
    if capsule is None:
        return None
    return {
        "id": capsule["id"],
        "base_capsule_id": capsule["base_capsule_id"],
        "state": capsule["state"],
        "content": capsule["content"],
        "covered_start_event_id": capsule["covered_start_event_id"],
        "covered_end_event_id": capsule["covered_end_event_id"],
        "created_at": capsule["created_at"],
        "validated_at": capsule["validated_at"],
        "error": capsule["error"],
        "model_metadata_json": capsule["model_metadata_json"],
    }


def _json_or_text(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"data": response.text}


async def _stream_response(
    *,
    backend: str,
    outbound: dict[str, Any],
    headers: dict[str, str],
    store: SQLiteStore,
    assembler: ContextAssembler,
    scheduler: CompactionScheduler,
    project_id: str,
    thread_id: str,
    request_id: str,
):
    chunks: list[bytes] = []
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            f"{backend}/v1/chat/completions",
            json=outbound,
            headers=headers,
        ) as response:
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                yield chunk
            raw = b"".join(chunks).decode("utf-8", errors="replace")
            store.append_event(
                project_id=project_id,
                thread_id=thread_id,
                event_type="assistant_response" if response.is_success else "backend_error",
                role="assistant" if response.is_success else "system",
                content=raw,
                metadata={"status_code": response.status_code, "stream": True},
                request_id=request_id,
            )
            _maybe_enqueue(thread_id, scheduler)


app = create_app()
