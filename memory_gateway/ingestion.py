from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .context import estimate_tokens
from .db import SQLiteStore, canonical_json, content_hash


@dataclass(frozen=True)
class IngestionResult:
    appended_events: tuple[dict[str, Any], ...]
    reused_prefix_count: int
    known_message_count: int
    incoming_message_count: int
    duplicate_protection: str


def message_content(message: dict[str, Any]) -> str:
    """Return the stable event payload for one protocol message."""

    # Pi may attach provider-local reasoning/diagnostic fields when it resends
    # an assistant message. Those fields are not a new conversational event;
    # identity is based on the OpenAI message semantics that affect the next
    # request. Tool IDs, names, arguments, role, and content remain exact.
    transient = {
        "reasoning",
        "thinking",
        "redacted_thinking",
        "provider_metadata",
        "metadata",
    }
    identity = {key: value for key, value in message.items() if key not in transient}
    for field in ("tool_calls",):
        calls = identity.get(field)
        if isinstance(calls, list):
            normalized_calls = []
            for call in calls:
                if not isinstance(call, dict):
                    normalized_calls.append(call)
                    continue
                normalized = dict(call)
                function = normalized.get("function")
                if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                    normalized_function = dict(function)
                    normalized_function["arguments"] = _normalize_json_text(
                        normalized_function["arguments"]
                    )
                    normalized["function"] = normalized_function
                normalized_calls.append(normalized)
            identity[field] = normalized_calls
    if isinstance(identity.get("function_call"), dict):
        function_call = dict(identity["function_call"])
        if isinstance(function_call.get("arguments"), str):
            function_call["arguments"] = _normalize_json_text(function_call["arguments"])
        identity["function_call"] = function_call
    return canonical_json(identity)


def _normalize_json_text(value: str) -> str:
    try:
        return canonical_json(json.loads(value))
    except (TypeError, json.JSONDecodeError):
        return value


def message_hash(message: dict[str, Any]) -> str:
    return content_hash(message_content(message))


def message_event_type(role: str | None) -> str:
    return {
        "user": "user_message",
        "assistant": "assistant_message",
        "tool": "tool_result",
        "system": "system_message",
        "developer": "developer_message",
    }.get(role or "", "conversation_message")


def normalize_message(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    role = value.get("role")
    if not isinstance(role, str) or not role:
        return None
    # Preserve protocol fields, including tool call IDs and arguments. The
    # canonical JSON hash is intentionally exact: repeated text at a new
    # sequence remains a new event, while a resent history prefix is reused.
    return dict(value)


def append_novel_messages(
    store: SQLiteStore,
    *,
    project_id: str,
    thread_id: str,
    messages: list[Any],
    request_id: str | None,
    source: str,
) -> IngestionResult:
    normalized = tuple(
        message
        for raw in messages
        if (message := normalize_message(raw)) is not None
    )
    result = store.append_novel_chat_messages(
        project_id=project_id,
        thread_id=thread_id,
        messages=normalized,
        request_id=request_id,
        source=source,
    )
    return IngestionResult(**result)


def parse_assistant_message(response_payload: Any) -> dict[str, Any] | None:
    if not isinstance(response_payload, dict):
        return None
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    return normalize_message(message)


def parse_stream_message(chunks: list[bytes]) -> dict[str, Any] | None:
    """Reconstruct a completed OpenAI assistant message from SSE chunks."""

    role = "assistant"
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    function_call: dict[str, str] = {}
    # A network read is not an SSE frame boundary.  Joining first keeps a
    # frame split across two provider chunks parseable and prevents the
    # fallback raw-response event from reintroducing a duplicate.
    wire = b"".join(chunks)
    for line in wire.decode("utf-8", errors="replace").splitlines():
            if not line.startswith("data:"):
                continue
            payload_text = line[5:].strip()
            if payload_text == "[DONE]":
                continue
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                continue
            choices = payload.get("choices") or []
            if not choices or not isinstance(choices[0], dict):
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            if isinstance(delta.get("role"), str):
                role = delta["role"]
            token = delta.get("content") or choice.get("text")
            if isinstance(token, str):
                content_parts.append(token)
            for call in delta.get("tool_calls") or ():
                if not isinstance(call, dict):
                    continue
                index = call.get("index", 0)
                if not isinstance(index, int):
                    continue
                target = tool_calls.setdefault(
                    index,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                for key in ("id", "type"):
                    if isinstance(call.get(key), str):
                        target[key] = call[key]
                function = call.get("function") or {}
                if isinstance(function.get("name"), str):
                    target["function"]["name"] += function["name"]
                if isinstance(function.get("arguments"), str):
                    target["function"]["arguments"] += function["arguments"]
            delta_function = delta.get("function_call") or {}
            if isinstance(delta_function.get("name"), str):
                function_call["name"] = function_call.get("name", "") + delta_function["name"]
            if isinstance(delta_function.get("arguments"), str):
                function_call["arguments"] = function_call.get("arguments", "") + delta_function["arguments"]
    message: dict[str, Any] = {"role": role, "content": "".join(content_parts) or None}
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
    if function_call:
        message["function_call"] = function_call
    return normalize_message(message)
