"""Deterministic views of immutable message rows, including legacy wire rows."""

from __future__ import annotations

import json
from dataclasses import replace

from .db import canonical_json


def event_messages(event: dict) -> list[dict]:
    from .ingestion import normalize_message, parse_assistant_message, parse_stream_message

    kind = event.get("event_type")
    text = event.get("content", "")
    if kind == "backend_error":
        return []
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        payload = None
    if kind == "request" and isinstance(payload, dict):
        return [message for raw in payload.get("messages", [])
                if (message := normalize_message(raw)) is not None]
    if kind == "assistant_response":
        if isinstance(payload, dict) and "choices" in payload:
            message = parse_assistant_message(payload)
            return [message] if message else []
        if text.startswith("data:"):
            message = parse_stream_message([text.encode()])
            return [message] if message else []
        # An unreadable legacy wire response must not be model-visible.
        if payload is not None:
            return []
    if isinstance(payload, dict) and payload.get("role"):
        message = normalize_message(payload)
        return [message] if message else []
    role = event.get("role")
    if role in {"user", "assistant", "tool", "system", "developer", "function"}:
        return [{"role": role, "content": text}]
    return []


def source_text(event: dict) -> str:
    """Character offsets refer to this versioned, immutable message-text view."""
    parts = []
    for message in event_messages(event):
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif content is not None:
            parts.append(canonical_json(content))
        for key in ("tool_calls", "function_call"):
            if message.get(key):
                parts.append(key + ": " + canonical_json(message[key]))
    return "\n".join(parts)


def model_events(events):
    """Legacy envelopes become messages; original IDs/rows are never changed."""
    from .db import content_hash
    from .ingestion import message_hash
    known: list[str] = []
    result = []
    for event in events:
        if event.event_type not in {"request", "assistant_response", "backend_error"}:
            result.append(event)
            continue
        messages = event_messages({"event_type": event.event_type, "role": event.role, "content": event.content})
        if event.event_type == "request":
            incoming = [message_hash(m) for m in messages]
            overlap = max((n for n in range(min(len(known), len(incoming)) + 1)
                           if known[-n:] == incoming[:n] or n == 0), default=0)
            messages = messages[overlap:]
        known.extend(message_hash(m) for m in messages)
        text = "\n".join(canonical_json(message) for message in messages)
        result.append(replace(event, content=text, content_hash=content_hash(text)))
    return result
