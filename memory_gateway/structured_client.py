from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from .openai_adapter import (
    ModelProtocolError,
    ModelTransportError,
    _endpoint_url,
    _now,
    _output_hash,
    _usage_metrics,
)
from .telemetry import (
    ModelCallContext,
    ModelRunRecorder,
    TelemetryPersistenceError,
    bounded_diagnostic_excerpt,
    deterministic_input_hash,
    endpoint_identity,
)


@dataclass
class OpenAICompatStructuredClient:
    """Small JSON-only client shared by the selector, canonicalizer, and consolidator."""

    endpoint: str
    model: str
    prompt_version: str
    system_prompt: str
    response_format: dict[str, Any] | None = None
    generation_settings: dict[str, Any] = field(default_factory=dict)
    timeout: float = 120.0
    api_key: str | None = None
    last_telemetry: dict[str, Any] | None = field(default=None, init=False)
    telemetry_recorder: ModelRunRecorder | None = None
    _job_context: ModelCallContext = field(default_factory=ModelCallContext, init=False)
    _call_context: ModelCallContext = field(default_factory=ModelCallContext, init=False)
    _ownership_checker: Callable[[], None] | None = field(default=None, init=False)

    def set_job_context(
        self,
        *,
        job_id: str | None,
        thread_id: str | None,
        generation: int | None,
    ) -> None:
        self._job_context = ModelCallContext(
            job_id=job_id,
            thread_id=thread_id,
            generation=generation,
        )
        self._call_context = self._job_context

    def set_call_context(
        self,
        *,
        stage: str,
        source_refs: tuple[str, ...] = (),
        selector_chunk_index: int | None = None,
        canonicalizer_batch_index: int | None = None,
    ) -> None:
        self._call_context = ModelCallContext(
            job_id=self._job_context.job_id,
            thread_id=self._job_context.thread_id,
            generation=self._job_context.generation,
            stage=stage,
            selector_chunk_index=selector_chunk_index,
            canonicalizer_batch_index=canonicalizer_batch_index,
            source_refs=tuple(source_refs),
        )

    def mark_call_failed(self, error: Any) -> None:
        telemetry = self.last_telemetry
        if telemetry is None:
            return
        message = str(error)
        excerpt = bounded_diagnostic_excerpt(message)
        telemetry.update(
            {
                "status": "FAILED",
                "error": message,
                "diagnostic_excerpt": excerpt,
            }
        )
        run_id = telemetry.get("run_id")
        if self.telemetry_recorder is None:
            return
        try:
            if run_id:
                self.telemetry_recorder.update_model_run(
                    run_id,
                    status="FAILED",
                    error=message,
                    diagnostic_excerpt=excerpt,
                )
            else:
                self._persist_telemetry(telemetry)
        except Exception as persistence_error:
            raise TelemetryPersistenceError(
                f"failed to update model-run telemetry: {persistence_error}"
            ) from persistence_error

    def set_ownership_checker(
        self,
        checker: Callable[[], None] | None,
    ) -> None:
        self._ownership_checker = checker

    def check_ownership(self) -> None:
        if self._ownership_checker is not None:
            self._ownership_checker()

    async def complete_json(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        self.check_ownership()
        input_hash = deterministic_input_hash(input_payload)
        settings = dict(self.generation_settings)
        stream = bool(settings.get("stream", False))
        settings.setdefault("temperature", 0)
        settings.setdefault("top_p", 1)
        settings["stream"] = stream
        if self.response_format is not None:
            settings.setdefault("response_format", self.response_format)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": (
                        "INPUT_JSON\n"
                        "--- BEGIN INPUT ---\n"
                        f"{json.dumps(input_payload, ensure_ascii=False, sort_keys=True)}\n"
                        "--- END INPUT ---"
                    ),
                },
            ],
            **settings,
        }
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"

        started = time.perf_counter()
        start_timestamp = _now()
        raw_content = ""
        usage: dict[str, Any] | None = None
        finish_reason: str | None = None
        first_token_at: float | None = None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if stream:
                    raw_content, usage, finish_reason, first_token_at = await self._stream(
                        client, body, headers
                    )
                else:
                    response = await client.post(_endpoint_url(self.endpoint), json=body, headers=headers)
                    if response.status_code >= 400:
                        raise ModelTransportError(f"HTTP {response.status_code}: {response.text[:500]}")
                    payload = response.json()
                    choice = self._first_choice(payload)
                    raw_content = self._message_content(choice)
                    usage = payload.get("usage")
                    finish_reason = choice.get("finish_reason")
                    first_token_at = time.perf_counter()
            self.check_ownership()
        except ModelTransportError as error:
            self._record_error(
                started,
                start_timestamp,
                settings,
                input_hash,
                error,
                raw_content,
                usage,
                finish_reason,
            )
            raise
        except ModelProtocolError as error:
            self._record_error(
                started,
                start_timestamp,
                settings,
                input_hash,
                error,
                raw_content,
                usage,
                finish_reason,
            )
            raise
        except asyncio.CancelledError:
            self._record_error(
                started,
                start_timestamp,
                settings,
                input_hash,
                "cancelled",
                raw_content,
                usage,
                finish_reason,
            )
            raise
        except httpx.TimeoutException as error:
            self._record_error(
                started,
                start_timestamp,
                settings,
                input_hash,
                error,
                raw_content,
                usage,
                finish_reason,
            )
            raise ModelTransportError(f"model request timed out after {self.timeout}s") from error
        except httpx.HTTPError as error:
            self._record_error(
                started,
                start_timestamp,
                settings,
                input_hash,
                error,
                raw_content,
                usage,
                finish_reason,
            )
            raise ModelTransportError(str(error)) from error
        except json.JSONDecodeError as error:
            self._record_error(
                started,
                start_timestamp,
                settings,
                input_hash,
                error,
                raw_content,
                usage,
                finish_reason,
            )
            raise ModelProtocolError("model response was not JSON") from error

        if not raw_content:
            self._record_error(
                started,
                start_timestamp,
                settings,
                input_hash,
                "empty assistant content",
                usage=usage,
                finish_reason=finish_reason,
            )
            raise ModelProtocolError("model returned no assistant content")
        try:
            result = json.loads(raw_content)
        except json.JSONDecodeError as error:
            self._record_error(
                started,
                start_timestamp,
                settings,
                input_hash,
                error,
                raw_content,
                usage,
                finish_reason,
            )
            raise ModelProtocolError("assistant content was not valid JSON") from error
        if not isinstance(result, dict):
            self._record_error(
                started,
                start_timestamp,
                settings,
                input_hash,
                "assistant JSON was not an object",
                raw_content,
                usage,
                finish_reason,
            )
            raise ModelProtocolError("assistant JSON must be an object")

        metrics = _usage_metrics(usage)
        ended = time.perf_counter()
        self.last_telemetry = {
            "model_identity": self.model,
            "endpoint": endpoint_identity(self.endpoint),
            "prompt_version": self.prompt_version,
            "generation_settings": settings,
            "input_hash": input_hash,
            "start_timestamp": start_timestamp,
            "end_timestamp": _now(),
            "input_tokens": metrics["input_tokens"],
            "output_tokens": metrics["output_tokens"],
            "reasoning_tokens": metrics["reasoning_tokens"],
            "ttft_ms": (
                (first_token_at - started) * 1000
                if first_token_at is not None
                else None
            ),
            "wall_ms": (ended - started) * 1000,
            "finish_reason": finish_reason,
            "raw_response_hash": _output_hash(raw_content),
            "diagnostic_excerpt": None,
            "stream": stream,
            "status": "SUCCEEDED",
        }
        self._persist_telemetry(self.last_telemetry)
        return result

    async def _stream(
        self,
        client: httpx.AsyncClient,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[str, dict[str, Any] | None, str | None, float | None]:
        content = ""
        usage = None
        finish_reason = None
        first_token_at = None
        async with client.stream("POST", _endpoint_url(self.endpoint), json=body, headers=headers) as response:
            if response.status_code >= 400:
                body_text = await response.aread()
                raise ModelTransportError(f"HTTP {response.status_code}: {body_text[:500]!r}")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload_text = line[5:].strip()
                if payload_text == "[DONE]":
                    continue
                try:
                    payload = json.loads(payload_text)
                except json.JSONDecodeError as error:
                    raise ModelProtocolError("stream contained a non-JSON data frame") from error
                usage = payload.get("usage") or usage
                choice = (payload.get("choices") or [{}])[0]
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta") or {}
                token = delta.get("content") or choice.get("text") or ""
                if token and first_token_at is None:
                    first_token_at = time.perf_counter()
                content += token
        return content, usage, finish_reason, first_token_at

    @staticmethod
    def _first_choice(payload: dict[str, Any]) -> dict[str, Any]:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelProtocolError("model response did not contain choices")
        return choices[0]

    @staticmethod
    def _message_content(choice: dict[str, Any]) -> str:
        message = choice.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise ModelProtocolError("model response message content was not a string")
        return content

    def _record_error(
        self,
        started: float,
        start_timestamp: str,
        settings: dict[str, Any],
        input_hash: str,
        error: Any,
        raw_content: str | None = None,
        usage: dict[str, Any] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        metrics = _usage_metrics(usage)
        self.last_telemetry = {
            "model_identity": self.model,
            "endpoint": endpoint_identity(self.endpoint),
            "prompt_version": self.prompt_version,
            "generation_settings": settings,
            "input_hash": input_hash,
            "start_timestamp": start_timestamp,
            "end_timestamp": _now(),
            "input_tokens": metrics["input_tokens"],
            "output_tokens": metrics["output_tokens"],
            "reasoning_tokens": metrics["reasoning_tokens"],
            "ttft_ms": None,
            "wall_ms": (time.perf_counter() - started) * 1000,
            "finish_reason": finish_reason,
            "raw_response_hash": _output_hash(raw_content) if raw_content else None,
            "diagnostic_excerpt": bounded_diagnostic_excerpt(
                raw_content or error
            ),
            "stream": bool(settings.get("stream", False)),
            "status": "FAILED",
            "error": str(error),
        }
        self._persist_telemetry(self.last_telemetry)

    def _persist_telemetry(self, telemetry: dict[str, Any] | None) -> None:
        if self.telemetry_recorder is None or telemetry is None:
            return
        context = self._call_context
        record = dict(telemetry)
        record.update(
            {
                "job_id": context.job_id,
                "thread_id": context.thread_id,
                "generation": context.generation,
                "model": self.model,
                "stage": context.stage or "unknown",
                "selector_chunk_index": context.selector_chunk_index,
                "canonicalizer_batch_index": context.canonicalizer_batch_index,
                "source_refs": context.source_refs,
                "metadata": {
                    "start_timestamp": telemetry.get("start_timestamp"),
                    "end_timestamp": telemetry.get("end_timestamp"),
                    "ttft_ms": telemetry.get("ttft_ms"),
                    "stream": telemetry.get("stream"),
                },
            }
        )
        try:
            run_id = self.telemetry_recorder.record_model_run(record)
        except Exception as error:
            raise TelemetryPersistenceError(
                f"failed to persist model-run telemetry: {error}"
            ) from error
        telemetry["run_id"] = run_id
