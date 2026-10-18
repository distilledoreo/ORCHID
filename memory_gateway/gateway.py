from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .cold_memory import CALIBRATED_RANKING_POLICY, FTS5ColdMemoryRetriever
from .cold_telemetry import BufferedColdMemoryTelemetry
from .compaction import CompactionEngine, CompactionWorker
from .config import RuntimeConfig
from .context import ContextAssembler, ContextSnapshot
from .core_goal import MAX_COMPACTION_WAIT_MS as MAXIMUM_FOREGROUND_WAIT_MS
from .db import SQLiteStore, new_id
from .foreground_telemetry import (
    append_record,
    finalize_record,
    record_provider_latency,
    substitution_record,
)
from .ingestion import append_novel_messages, parse_assistant_message, parse_stream_message
from .pipeline_adapters import build_lossless_engine
from .scheduler import CompactionScheduler, ThresholdPolicy


def foreground_needs_capacity_wait(
    context: ContextSnapshot, runtime: RuntimeConfig
) -> bool:
    _ = runtime
    return context.hard_limit_exceeded


def foreground_capacity_wait_budget_seconds(runtime: RuntimeConfig) -> float:
    _ = runtime
    return MAXIMUM_FOREGROUND_WAIT_MS / 1000.0


def in_flight_compaction_job_id(store: SQLiteStore, thread_id: str) -> str | None:
    jobs = store.list_jobs(thread_id=thread_id, statuses=("QUEUED", "RUNNING"), limit=8)
    running = next((job for job in jobs if job.get("status") == "RUNNING"), None)
    if running is not None:
        return str(running["id"])
    queued = next((job for job in jobs if job.get("status") == "QUEUED"), None)
    if queued is not None:
        return str(queued["id"])
    return None


async def _wait_out_hard_limit(
    *,
    store: SQLiteStore,
    assembler: ContextAssembler,
    scheduler: CompactionScheduler,
    runtime: RuntimeConfig,
    thread_id: str,
    inbound: list[dict[str, Any]],
    project_id: str,
    context: ContextSnapshot,
) -> tuple[ContextSnapshot, str | None]:
    """Enqueue background maintenance without blocking the foreground path."""

    _ = (store, assembler, runtime, inbound, project_id)
    job_id = scheduler.enqueue_urgent(thread_id)
    return replace(
        context,
        compaction_wait_ms=0.0,
        memory_maintenance_awaited=False,
    ), in_flight_compaction_job_id(store, thread_id) or job_id


def estimate_request_overhead(payload: dict) -> int:
    from .context import estimate_tokens
    from .db import canonical_json
    return sum(estimate_tokens(canonical_json(payload[key])) for key in
               ("tools", "functions", "response_format", "tool_choice") if key in payload)


def completion_reservation(payload: dict, runtime: RuntimeConfig) -> int:
    value = payload.get("max_completion_tokens", payload.get("max_tokens", runtime.foreground_reserved_output_tokens))
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("completion token limit must be positive")
    return value


def create_app(
    *,
    db_path: str | None = None,
    backend_url: str | None = None,
    config: RuntimeConfig | None = None,
    compaction_engine: CompactionEngine | None = None,
    specprefill_service=None,
) -> FastAPI:
    runtime = config or RuntimeConfig.from_env(db_path=db_path, backend_url=backend_url)
    from .specprefill.service import GatewayOptions, SpecPrefillService
    native_options = GatewayOptions.from_env() if specprefill_service is None else None
    native_service = specprefill_service or (SpecPrefillService(native_options) if native_options else None)
    from .activity import ActivityGate, ForegroundActivityMiddleware
    from .observer import build_observer_engine
    activity_gate = ActivityGate()
    store = SQLiteStore(runtime.db_path)
    if runtime.coding_agent_mode:
        store.coding_policy = {"keep_turns": runtime.coding_keep_turns, "offload_tokens": runtime.coding_offload_tokens}
    from .raw_recall import RawRecall
    raw_recall = RawRecall(store, embeddings_url=runtime.embeddings_url, embeddings_model=runtime.embeddings_model,
                           embeddings_api_key=runtime.embeddings_api_key, timeout=runtime.embeddings_timeout_seconds)
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
        layout=runtime.prompt_layout,
        raw_tail_target_tokens=runtime.raw_tail_target_tokens,
        minimum_raw_tail_tokens=runtime.minimum_raw_tail_tokens,
        context_budget_tokens=runtime.foreground_target_tokens,
        foreground_hard_limit_tokens=runtime.foreground_hard_limit_tokens,
        cold_memory_provider=cold_memory_provider,
        cold_memory_mode=runtime.cold_memory_mode,
        cold_memory_token_budget=runtime.cold_memory_token_budget,
        cold_memory_max_injected=runtime.cold_memory_max_injected,
        cold_memory_telemetry=cold_memory_telemetry,
    )
    scheduler = CompactionScheduler(
        store,
        ThresholdPolicy(
            usable_context_tokens=runtime.foreground_hard_limit_tokens,
            background_threshold_tokens=runtime.foreground_target_tokens,
            bootstrap_threshold_tokens=min(
                runtime.initial_compaction_threshold_tokens,
                runtime.foreground_target_tokens,
            ),
            urgent_threshold_tokens=runtime.foreground_hard_limit_tokens,
            predictive_lookahead_turns=runtime.compaction_forecast_lookahead_turns,
        ),
        source_window_tokens=runtime.compaction_source_window_tokens,
        forecast_alpha=runtime.compaction_forecast_alpha,
        publication_min_savings_ratio=runtime.publication_min_savings_ratio,
        publication_economics_enabled=runtime.publication_economics_enabled,
        publication_horizon_turns=runtime.publication_horizon_turns,
    )
    configured_engine = (build_observer_engine(runtime, store, activity_gate)
                         if runtime.memory_engine == "observer" or
                         (runtime.memory_engine == "auto" and runtime.observer_url and runtime.observer_model)
                         else build_lossless_engine(runtime, telemetry_recorder=store))
    engine = (
        compaction_engine
        if compaction_engine is not None
        else configured_engine
    )
    from .capsule_admission import build_capsule_verifier

    capsule_verifier = build_capsule_verifier(runtime, telemetry_recorder=store)
    worker = (
        CompactionWorker(
            store,
            engine,
            "gateway-compaction-worker",
            lease_seconds=runtime.lease_seconds,
            renewal_interval_seconds=runtime.lease_renewal_seconds,
            recover_expired_jobs=runtime.recover_expired_jobs,
            on_job_finished=scheduler.reconcile_after_job,
            memory_format=runtime.memory_format,
            capsule_max_tokens=runtime.capsule_max_tokens,
            capsule_verifier=capsule_verifier,
            publication_min_savings_ratio=runtime.publication_min_savings_ratio,
            publication_economics_enabled=runtime.publication_economics_enabled,
            publication_horizon_turns=runtime.publication_horizon_turns,
        )
        if engine and native_service is None
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
                _worker_loop(worker, stop_event, runtime.worker_poll_seconds,
                             activity_gate if runtime.background_idle_only else None),
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
            if app.state.specprefill_tasks:
                await asyncio.gather(*app.state.specprefill_tasks, return_exceptions=True)
            if native_service is not None:
                await asyncio.to_thread(native_service.close)
            if cold_memory_telemetry is not None:
                cold_memory_telemetry.close()

    backend = runtime.backend_url
    app = FastAPI(title="Orchid Memory Gateway", version="0.1.0", lifespan=lifespan)
    app.add_middleware(ForegroundActivityMiddleware, gate=activity_gate)
    app.state.activity_gate = activity_gate
    app.state.specprefill_service = native_service
    app.state.specprefill_tasks = set()
    app.state.raw_recall = raw_recall
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
            "compaction": "specprefill" if native_service is not None else ("configured" if worker is not None else "not_configured"),
        }

    @app.get("/v1/models")
    async def models(request: Request) -> JSONResponse:
        if native_service is not None:
            return JSONResponse({"object": "list", "data": [{"id": native_service.options.model, "object": "model", "owned_by": "local"}]})
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
        if native_service is not None:
            from .specprefill.http import handle
            return await handle(request, payload, native_service, store)
        try:
            completion_reservation(payload, runtime)
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": {"type": "invalid_request_error", "message": str(error)}})
        from .ingestion import normalize_message
        if any(normalize_message(message) is None for message in payload["messages"]):
            return JSONResponse(status_code=400, content={"error": {"type": "invalid_request_error",
                                "message": "each message must have a supported role"}})
        project_id = request.headers.get("x-memory-project", "default")
        thread_id = request.headers.get("x-memory-thread", "default")
        request_id = request.headers.get("x-request-id", new_id("req"))
        store.ensure_project_and_thread(
            project_id,
            thread_id,
            memory_format=runtime.memory_format,
        )
        ingestion = append_novel_messages(
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
            request_event_ids=ingestion.message_event_ids,
            overhead_tokens=estimate_request_overhead(payload),
            reserved_output_tokens=completion_reservation(payload, runtime),
        )
        foreground_record = substitution_record(
            request_id=request_id,
            thread_id=thread_id,
            inbound=payload["messages"],
            snapshot=context,
            stream=bool(payload.get("stream", False)),
        )
        job_id = None
        if foreground_needs_capacity_wait(context, runtime):
            context, job_id = await _wait_out_hard_limit(
                store=store,
                assembler=assembler,
                scheduler=scheduler,
                runtime=runtime,
                thread_id=thread_id,
                inbound=payload["messages"],
                project_id=project_id,
                context=context,
            )
        if foreground_needs_capacity_wait(context, runtime):
            job_id = job_id or scheduler.enqueue_urgent(thread_id)
            foreground_record["gateway_outcome"] = "context_capacity_exceeded"
            append_record(finalize_record(foreground_record, {}))
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "60"},
                content={
                    "error": {
                        "type": "context_capacity_exceeded",
                        "message": "foreground context exceeds hard limit",
                        "estimated_tokens": context.estimated_tokens,
                        "limit_tokens": runtime.foreground_hard_limit_tokens,
                        "job_id": job_id,
                    }
                },
            )
        outbound = dict(payload)
        outbound["messages"] = context.messages
        if runtime.backend_model:
            outbound["model"] = runtime.backend_model
        from memory_gateway.openrouter_routing import apply_openrouter_provider_routing

        outbound = apply_openrouter_provider_routing(outbound)
        headers = _backend_headers(request, runtime.backend_api_key)
        headers["content-type"] = "application/json"
        foreground_record["gateway_outcome"] = "forwarded"

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
                    foreground_record=foreground_record,
                ),
                media_type="text/event-stream",
            )

        provider_started_perf = time.perf_counter()
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                f"{backend}/v1/chat/completions",
                json=outbound,
                headers=headers,
            )
        parsed = _json_or_text(response)
        record_provider_latency(
            foreground_record,
            provider_wall_ms=(time.perf_counter() - provider_started_perf) * 1_000,
            success=response.is_success,
        )
        append_record(finalize_record(foreground_record, parsed))
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
                    usage=parsed.get("usage") if isinstance(parsed, dict) else None,
                )
            else:
                return JSONResponse(status_code=502, content={"error": {
                    "type": "provider_protocol_error", "message": "provider response has no valid assistant message"}})
            if isinstance(parsed.get("usage"), dict):
                store.record_request_usage(thread_id, request_id, parsed["usage"])
        _maybe_enqueue(thread_id, scheduler)
        return JSONResponse(status_code=response.status_code, content=_json_or_text(response))

    def recall_scope(request):
        project = request.headers.get("x-memory-project")
        thread = request.headers.get("x-memory-thread")
        if not project or not thread:
            raise ValueError("recall requires explicit project and thread headers")
        if native_service is not None:
            from .specprefill.service import digest
            thread = "specprefill-" + digest([project, thread])
        return project, thread

    @app.post("/v1/memory/recall")
    async def recall_events(request: Request):
        try:
            project, thread = recall_scope(request)
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("recall body must be an object")
            return await raw_recall.recall(project, thread, body.get("query"), body.get("k", 5))
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": {"message": str(error)}})
        except Exception:
            return JSONResponse(status_code=503, content={"error": {"type": "recall_unavailable"}}, headers={"Retry-After": "1"})

    @app.get("/v1/memory/events/{event_id}")
    async def read_event(event_id: str, request: Request, start: int = 0, end: int | None = None):
        try:
            project, thread = recall_scope(request)
            result = raw_recall.read(project, thread, event_id, start, end)
            if result is None:
                return JSONResponse(status_code=404, content={"error": {"type": "event_not_found"}})
            return result
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": {"message": str(error)}})
        except Exception:
            return JSONResponse(status_code=503, content={"error": {"type": "recall_unavailable"}})

    @app.post("/v1/memory/index-embeddings")
    async def index_embeddings(request: Request):
        try:
            project, thread = recall_scope(request)
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("index body must be an object")
            return await raw_recall.index_embeddings(project, thread, body.get("batch_size", 32))
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": {"message": str(error)}})
        except Exception:
            return JSONResponse(status_code=503, content={"error": {"type": "index_unavailable"}})

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
            "foreground_target_tokens": context.foreground_target_tokens,
            "foreground_hard_limit_tokens": context.foreground_hard_limit_tokens,
            "foreground_target_exceeded": context.target_exceeded,
            "foreground_hard_limit_exceeded": context.hard_limit_exceeded,
            "emergency_spill_tokens": context.emergency_spill_tokens,
            "organizer_lag_events": context.organizer_lag_events,
            "organizer_lag_tokens": context.organizer_lag_tokens,
            "compaction_wait_ms": context.compaction_wait_ms,
            "memory_maintenance_awaited": context.memory_maintenance_awaited,
            "component_tokens": context.component_tokens,
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
    activity_gate=None,
) -> None:
    while not stop_event.is_set():
        if activity_gate is not None:
            await activity_gate.idle.wait()
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
    foreground_record: dict[str, Any] | None = None,
):
    chunks: list[bytes] = []
    pending = b""
    saw_done = False
    saw_finish_reason = False
    first_token_at: str | None = None
    first_token_perf: float | None = None
    last_usage_payload: dict[str, Any] | None = None
    provider_started_at = _stream_now()
    provider_started_perf = time.perf_counter()
    stream_end_at: str | None = None
    status = "ACCEPTED"
    failure_category: str | None = None
    error_excerpt: str | None = None

    def inspect_line(line: bytes) -> None:
        nonlocal saw_done, saw_finish_reason, first_token_at, first_token_perf, last_usage_payload
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
        if not isinstance(payload, dict):
            return
        if isinstance(payload.get("usage"), dict):
            last_usage_payload = payload
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
            first_token_perf = time.perf_counter()

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
        if response_message is None:
            raise ProviderStreamError("invalid_assistant_message", "provider stream has no valid assistant message")
        append_novel_messages(
            store, project_id=project_id, thread_id=thread_id,
            messages=[response_message], request_id=request_id, source="response",
            usage=(last_usage_payload or {}).get("usage"),
        )
        if isinstance((last_usage_payload or {}).get("usage"), dict):
            store.record_request_usage(thread_id, request_id, last_usage_payload["usage"])
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
        if foreground_record is not None:
            provider_ended_perf = time.perf_counter()
            record_provider_latency(
                foreground_record,
                provider_wall_ms=(provider_ended_perf - provider_started_perf) * 1_000,
                time_to_first_event_ms=(
                    (first_token_perf - provider_started_perf) * 1_000
                    if first_token_perf is not None
                    else None
                ),
                success=True,
            )
            append_record(finalize_record(foreground_record, last_usage_payload or {}))
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
        if foreground_record is not None:
            foreground_record["gateway_outcome"] = "client_cancelled"
            record_provider_latency(
                foreground_record,
                provider_wall_ms=(time.perf_counter() - provider_started_perf) * 1_000,
                time_to_first_event_ms=(
                    (first_token_perf - provider_started_perf) * 1_000
                    if first_token_perf is not None
                    else None
                ),
                success=False,
            )
            append_record(finalize_record(foreground_record, {}))
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
        if foreground_record is not None:
            foreground_record["gateway_outcome"] = "provider_stream_error"
            record_provider_latency(
                foreground_record,
                provider_wall_ms=(time.perf_counter() - provider_started_perf) * 1_000,
                time_to_first_event_ms=(
                    (first_token_perf - provider_started_perf) * 1_000
                    if first_token_perf is not None
                    else None
                ),
                success=False,
            )
            append_record(finalize_record(foreground_record, {}))
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
        if foreground_record is not None:
            foreground_record["gateway_outcome"] = "proxy_internal_error"
            record_provider_latency(
                foreground_record,
                provider_wall_ms=(time.perf_counter() - provider_started_perf) * 1_000,
                time_to_first_event_ms=(
                    (first_token_perf - provider_started_perf) * 1_000
                    if first_token_perf is not None
                    else None
                ),
                success=False,
            )
            append_record(finalize_record(foreground_record, {}))
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
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _synthetic_stream_error(request_id: str, category: str, message: str) -> bytes:
    payload = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}],
        "error": {"type": category, "message": message[:500]},
    }
    return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


app = create_app()
