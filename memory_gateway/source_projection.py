"""Project bulky tool results into compact durable stubs for compaction.

The live tool loop still needs verbatim file dumps. Compaction does not: the
files remain on disk. This module keeps paths, error summaries, and a short
preview, and collapses duplicate tool payloads inside a snapshot.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .compaction import Event
from .db import content_hash

_PATH_LINE = re.compile(
    r"(^|[\s|])(/[^\s|:]+|[A-Za-z]:\\[^\s|:]+|[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+)"
)
_SOURCE_HINT = re.compile(
    r"^(import |export |from ['\"]|/\*|// |#include |package |using |func |class |const |let |var |type )",
    re.M,
)
_ERROR_LINE = re.compile(
    r"(error TS\d+|Error:|FAIL |Failed |✗|× |Traceback|TypeError|ReferenceError|EADDRINUSE)",
    re.I,
)


def tool_inner_text(content: str) -> str:
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return content
    if isinstance(payload, dict) and isinstance(payload.get("content"), str):
        return payload["content"]
    return content


def classify_tool_text(text: str) -> str:
    stripped = text.lstrip()
    if not stripped:
        return "empty"
    if _ERROR_LINE.search(stripped[:8000]) or "error TS" in stripped:
        return "diagnostics"
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) >= 8:
        pathish = sum(1 for line in lines[:40] if _PATH_LINE.search(line) and " " not in line[:3])
        if pathish / min(len(lines), 40) >= 0.6 and "import " not in stripped[:200]:
            return "path_list"
    if len(stripped) >= 800 and _SOURCE_HINT.search(stripped[:1500]):
        return "source_file"
    if len(stripped) >= 1200 and (
        stripped.count("{") + stripped.count(";") > 20 or "function " in stripped[:2000]
    ):
        return "source_file"
    if stripped.lstrip().startswith("{") and '"name"' in stripped[:120] and "version" in stripped[:400]:
        return "manifest"
    if stripped.startswith("# ") or stripped.startswith("## "):
        return "markdown"
    return "keep"


def _preview(text: str, limit: int = 240) -> str:
    compact = " ".join(text[: limit * 4].split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _diagnostic_summary(text: str, *, max_lines: int = 40) -> str:
    lines = text.splitlines()
    matched = [line for line in lines if _ERROR_LINE.search(line)]
    if not matched:
        matched = lines[:12] + (["…"] if len(lines) > 24 else []) + lines[-12:]
    if len(matched) > max_lines:
        matched = matched[: max_lines - 1] + ["…"]
    return "\n".join(matched)


def project_tool_content(content: str, *, content_hash_value: str) -> tuple[str, str]:
    """Return (kind, projected_content). Non-tool callers should not use this."""

    inner = tool_inner_text(content)
    kind = classify_tool_text(inner)
    if kind == "keep" or kind == "empty":
        return kind, content
    if kind == "diagnostics":
        stub = (
            f"TOOL_DIAGNOSTICS_STUB content_hash={content_hash_value}\n"
            f"{_diagnostic_summary(inner)}"
        )
        return kind, stub
    if kind == "path_list":
        lines = [line.strip() for line in inner.splitlines() if line.strip()]
        stub = (
            f"TOOL_PATH_LIST_STUB content_hash={content_hash_value} "
            f"entries={len(lines)} sample={_preview('; '.join(lines[:8]))}"
        )
        return kind, stub
    stub = (
        f"TOOL_READ_STUB kind={kind} content_hash={content_hash_value} "
        f"chars={len(inner)} preview={_preview(inner)}"
    )
    return kind, stub


def project_compaction_events(
    events: list[Event],
    *,
    seen: dict[str, int] | None = None,
) -> list[Event]:
    """Stub bulky/duplicate tool results; leave other roles unchanged."""

    hashes = seen if seen is not None else {}
    projected: list[Event] = []
    for event in events:
        if event.event_type != "tool_result" and event.role != "tool":
            projected.append(event)
            continue
        original_hash = event.content_hash or content_hash(event.content)
        first_sequence = hashes.get(original_hash)
        if first_sequence is not None:
            stub = (
                f"DUPLICATE_TOOL_RESULT content_hash={original_hash} "
                f"first_sequence={first_sequence}"
            )
            projected.append(
                Event(
                    id=event.id,
                    sequence=event.sequence,
                    content=stub,
                    content_hash=content_hash(stub),
                    event_type=event.event_type,
                    role=event.role,
                )
            )
            continue
        hashes[original_hash] = event.sequence
        _kind, new_content = project_tool_content(
            event.content, content_hash_value=original_hash
        )
        if new_content == event.content:
            projected.append(event)
            continue
        projected.append(
            Event(
                id=event.id,
                sequence=event.sequence,
                content=new_content,
                content_hash=content_hash(new_content),
                event_type=event.event_type,
                role=event.role,
            )
        )
    return projected


def projected_source_tokens(event_row: dict[str, Any], seen: dict[str, int]) -> int:
    """Token estimate for freeze-window accounting after projection."""

    from .compaction import source_item_payload
    from .context import estimate_tokens

    projected = project_compaction_events([Event.from_row(event_row)], seen=seen)[0]
    return estimate_tokens(
        json.dumps(source_item_payload(projected), ensure_ascii=False)
    )
