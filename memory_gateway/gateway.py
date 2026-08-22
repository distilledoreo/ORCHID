from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .compaction import CompactionEngine, CompactionWorker
from .config import RuntimeConfig
from .context import ContextAssembler
from .cold_memory import CALIBRATED_RANKING_POLICY, FTS5ColdMemoryRetriever
from .db import SQLiteStore, new_id
from .pipeline_adapters import build_lossless_engine
from .scheduler import CompactionScheduler, ThresholdPolicy
from .cold_telemetry import BufferedColdMemoryTelemetry
from .ingestion import append_novel_messages, parse_assistant_message, parse_stream_message


def create_app(
    *,
    db_path: str | None = None,
    backend_url: str | None = None,
    config: RuntimeConfig | None = None,
    compaction_engine: CompactionEngine | None = None,
) -> FastAPI:
    runtime = config or RuntimeConfig.from_env(db_path=db_path, backend_url=backend_url)
    store = SQLiteStore(runtime.db_path)
    cold_memory_telemetry = (
        BufferedColdMemoryTelemetry(
            store,
            max_queue_size=runtime.cold_memory_telemetry_queue_size,
            batch_size=runtime.cold_memory_telemetry_batch_size,
            flush_interval_ms=runtime.cold_memory_telemetry_flush_ms,
        )
        if runtime.cold_memory_mode != "off"
        else None
    )
    cold_memory_provider = (
        FTS5ColdMemoryRetriever(
            store,
            candidate_limit=runtime.cold_memory_candidate_limit,
            timeout_ms=runtime.cold_memory_timeout_ms,
            ranking_policy=CALIBRATED_RANKING_POLICY,
            telemetry_sink=cold_memory_telemetry,
        )
        if runtime.cold_memory_mode != "off"
        else None
    )
    assembler = ContextAssembler(
        store,
        raw_tail_target_tokens=runtime.raw_tail_target_tokens,
        minimum_raw_tail_tokens=runtime.minimum_raw_tail_tokens,
        context_budget_tokens=runtime.context_tokens,
        cold_memory_provider=cold_memory_provider,
        cold_memory_mode=runtime.cold_memory_mode,
        cold_memory_token_budget=runtime.cold_memory_token_budget,
        cold_memory_max_injected=runtime.cold_memory_max_injected,
        cold_memory_telemetry=cold_memory_telemetry,
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
            on_job_finished=scheduler.reconcile_after_job,
        )
        if engine
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop_event = asyncio.Event()
        worker_task: asyncio.Task[None] | None = None
        if cold_memory_telemetry is not None:
            cold_memory_telemetry.start()
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
            if cold_memory_telemetry is not None:
                cold_memory_telemetry.close()

    backend = runtime.backend_url
    app = FastAPI(title="Orchid Memory Gateway", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.runtime = runtime
    app.state.backend_url = backend
    app.state.assembler = assembler
    app.state.scheduler = scheduler
    app.state.compaction_engine = engine
    app.state.compaction_worker = worker
    app.state.cold_memory_provider = cold_memory_provider
    app.state.cold_memory_telemetry = cold_memory_telemetry
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
        append_novel_messages(
            store,
            project_id=project_id,
            thread_id=thread_id,
            messages=payload["messages"],
            request_id=request_id,
            source="request",
        )
        context = assembler.assemble(
            thread_id,
            payload["messages"],
            project_id=project_id,
        )
        outbound = dict(payload)
        outbound["messages"] = context.messages
        if runtime.backend_model:
            outbound["model"] = runtime.backend_model
        headers = _backend_headers(request, runtime.backend_api_key)
        headers["content-type"] = "application/json"

        if payload.get("stream", False):
            stream_run_id = store.create_gateway_stream_run(
                project_id=project_id,
                thread_id=thread_id,
                request_id=request_id,
            )
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
                    stream_run_id=stream_run_id,
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
        if response.is_success:
            response_message = parse_assistant_message(_json_or_text(response))
            if response_message is not None:
                append_novel_messages(
                    store,
                    project_id=project_id,
                    thread_id=thread_id,
                    messages=[response_message],
                    request_id=request_id,
                    source="response",
                )
            else:
                store.append_event(
                    project_id=project_id,
                    thread_id=thread_id,
                    event_type="assistant_response",
                    role="assistant",
                    content=response_text,
                    metadata={"status_code": response.status_code, "stream": False},
                    request_id=request_id,
                )
        else:
            store.append_event(
                project_id=project_id,
                thread_id=thread_id,
                event_type="backend_error",
                role="system",
                content=response_text[:2000],
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
    stream_run_id: str,
):
    chunks: list[bytes] = []
    pending = b""
    saw_done = False
    saw_finish_reason = False
    first_token_at: str | None = None
    provider_started_at = _stream_now()
    stream_end_at: str | None = None
    status = "ACCEPTED"
    failure_category: str | None = None
    error_excerpt: str | None = None

    def inspect_line(line: bytes) -> None:
        nonlocal saw_done, saw_finish_reason, first_token_at
        text = line.decode("utf-8", errors="replace").strip()
        if not text or not text.startswith("data:"):
            return
        payload_text = text[5:].strip()
        if payload_text == "[DONE]":
            saw_done = True
            return
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as error:
            raise ProviderStreamError("malformed_sse_frame", str(error)) from error
        if isinstance(payload.get("error"), dict):
            message = payload["error"].get("message") or "provider error frame"
            raise ProviderStreamError("provider_error_frame", str(message))
        choices = payload.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return
        choice = choices[0]
        if choice.get("finish_reason") is not None:
            saw_finish_reason = True
        delta = choice.get("delta") or {}
        has_token = bool(
            delta.get("content")
            or delta.get("tool_calls")
            or delta.get("function_call")
            or choice.get("text")
        )
        if has_token and first_token_at is None:
            first_token_at = _stream_now()

    try:
        store.update_gateway_stream_run(
            stream_run_id,
            provider_started_at=provider_started_at,
            status="PROVIDER_STARTED",
        )
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{backend}/v1/chat/completions",
                json=outbound,
                headers=headers,
            ) as response:
                if response.status_code >= 400:
                    body_text = (await response.aread())[:500].decode(
                        "utf-8", errors="replace"
                    )
                    raise ProviderStreamError(
                        "provider_http_error",
                        f"HTTP {response.status_code}: {body_text}",
                    )
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk)
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        inspect_line(line)
                    yield chunk
                if pending.strip():
                    inspect_line(pending)
                if not saw_done or not saw_finish_reason:
                    raise ProviderStreamError(
                        "eof_without_normal_terminator",
                        "provider stream ended without finish_reason and [DONE]",
                    )
                stream_end_at = _stream_now()
        response_message = parse_stream_message(chunks)
        if response_message is not None:
            append_novel_messages(
                store,
                project_id=project_id,
                thread_id=thread_id,
                messages=[response_message],
                request_id=request_id,
                source="response",
            )
        else:
            store.append_event(
                project_id=project_id,
                thread_id=thread_id,
                event_type="assistant_response",
                role="assistant",
                content=b"".join(chunks).decode("utf-8", errors="replace"),
                metadata={"stream": True},
                request_id=request_id,
            )
        _maybe_enqueue(thread_id, scheduler)
        status = "SUCCEEDED"
        store.update_gateway_stream_run(
            stream_run_id,
            first_token_at=first_token_at,
            stream_end_at=stream_end_at or _stream_now(),
            status=status,
            bytes_forwarded=sum(len(chunk) for chunk in chunks),
            partial_output=int(bool(chunks)),
        )
    except asyncio.CancelledError:
        status = "CANCELLED"
        failure_category = "client_cancelled"
        error_excerpt = "client cancelled stream"
        stream_end_at = _stream_now()
        store.update_gateway_stream_run(
            stream_run_id,
            first_token_at=first_token_at,
            stream_end_at=stream_end_at,
            status="CANCELLED",
            failure_category=failure_category,
            bytes_forwarded=sum(len(chunk) for chunk in chunks),
            partial_output=int(bool(chunks)),
            error_excerpt=error_excerpt,
        )
        raise
    except ProviderStreamError as error:
        status = "FAILED"
        failure_category = error.category
        error_excerpt = str(error)[:500]
        stream_end_at = _stream_now()
        # A provider failure must be fail-open.  Recording diagnostics is
        # useful, but a database/queue problem while recording them must not
        # prevent the synthetic terminal frame from reaching the client.
        try:
            store.append_event(
                project_id=project_id,
                thread_id=thread_id,
                event_type="backend_error",
                role="system",
                content=json.dumps(
                    {"error": error_excerpt, "failure_category": failure_category},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                metadata={"stream": True, "provider_failure": True},
                request_id=request_id,
            )
        except Exception:
            pass
        try:
            _maybe_enqueue(thread_id, scheduler)
        except Exception:
            pass
        try:
            store.update_gateway_stream_run(
                stream_run_id,
                first_token_at=first_token_at,
                stream_end_at=stream_end_at,
                status="FAILED",
                failure_category=failure_category,
                bytes_forwarded=sum(len(chunk) for chunk in chunks),
                partial_output=int(bool(chunks)),
                error_excerpt=error_excerpt,
            )
        except Exception:
            pass
        yield _synthetic_stream_error(request_id, failure_category, error_excerpt)
        yield b"data: [DONE]\n\n"
    except Exception as error:
        status = "FAILED"
        failure_category = "proxy_internal_error"
        error_excerpt = str(error)[:500]
        stream_end_at = _stream_now()
        store.update_gateway_stream_run(
            stream_run_id,
            first_token_at=first_token_at,
            stream_end_at=stream_end_at,
            status="FAILED",
            failure_category=failure_category,
            bytes_forwarded=sum(len(chunk) for chunk in chunks),
            partial_output=int(bool(chunks)),
            error_excerpt=error_excerpt,
        )
        yield _synthetic_stream_error(request_id, failure_category, error_excerpt)
        yield b"data: [DONE]\n\n"
    finally:
        try:
            final_status = status
            final_failure = failure_category
            if final_status != "SUCCEEDED" and final_failure is None:
                final_status = "CANCELLED"
                final_failure = "client_cancelled"
            store.update_gateway_stream_run(
                stream_run_id,
                cleanup_completed_at=_stream_now(),
                status=final_status,
                failure_category=final_failure,
            )
        except Exception:
            pass


class ProviderStreamError(RuntimeError):
    def __init__(self, category: str, message: str):
        self.category = category
        super().__init__(message)


def _stream_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _synthetic_stream_error(request_id: str, category: str, message: str) -> bytes:
    payload = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}],
        "error": {"type": category, "message": message[:500]},
    }
    return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


app = create_app()
