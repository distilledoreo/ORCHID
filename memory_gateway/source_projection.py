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
_TRUNCATED_JSON_TEXT_FIELD = re.compile(r'"text"\s*:\s*"((?:\\.|[^"\\])*)', re.DOTALL)
_SOURCE_HINT = re.compile(
    r"^(import |export |from ['\"]|/\*|// |#include |package |using |func |class |const |let |var |type )",
    re.M,
)
_ERROR_LINE = re.compile(
    r"(error TS\d+|Error:|FAIL |Failed |✗|× |Traceback|TypeError|ReferenceError|EADDRINUSE)",
    re.I,
)
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _decode_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return (
            value.replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )


def _embedded_text_fields(content: str) -> str | None:
    """Recover inner text from truncated Pi tool envelopes when JSON parsing fails."""

    # The permissive expression returns complete fields normally and also the
    # trailing partial field when a source span cuts through a large text
    # block. Looking for complete fields first loses that final (usually most
    # informative) block whenever the envelope starts with a short header.
    matches = _TRUNCATED_JSON_TEXT_FIELD.findall(content)
    if not matches:
        return None
    return "\n".join(_decode_json_string(match) for match in matches)


def tool_inner_text(content: str) -> str:
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        embedded = _embedded_text_fields(content)
        return embedded if embedded is not None else content
    if isinstance(payload, dict):
        inner = payload.get("content")
        if inner is None:
            inner = payload.get("output")
        if isinstance(inner, str):
            return inner
        if isinstance(inner, list):
            parts: list[str] = []
            for block in inner:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block, str):
                    parts.append(block)
            if parts:
                return "\n".join(parts)
    return content


def normalize_citation_text(text: str) -> str:
    """Decode literal JSON escape sequences embedded in span slices."""

    normalized = (
        text.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
    )
    while True:
        decoded = _UNICODE_ESCAPE.sub(
            lambda match: chr(int(match.group(1), 16)),
            normalized,
        )
        if decoded == normalized:
            break
        normalized = decoded
    return normalized


def normalize_citation_match_text(text: str) -> str:
    """Normalize source and excerpt text for deterministic substring checks."""

    normalized = normalize_citation_text(text)
    normalized = _ANSI_ESCAPE.sub("", normalized)
    normalized = (
        normalized.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u00a0", " ")
    )
    return re.sub(r"\s+", " ", normalized).strip()


def _citation_match_text_with_offsets(
    source_text: str,
) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    """Normalize citation text while retaining offsets into the exact source."""

    characters: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    index = 0
    quote_map = {
        "\u2019": "'",
        "\u2018": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00a0": " ",
    }
    while index < len(source_text):
        ansi = _ANSI_ESCAPE.match(source_text, index)
        if ansi is not None:
            index = ansi.end()
            continue
        source_start = index
        source_end = index + 1
        character = quote_map.get(source_text[index], source_text[index])
        index = source_end
        if character.isspace():
            if not characters:
                continue
            if characters[-1] == " ":
                ends[-1] = source_end
                continue
            characters.append(" ")
            starts.append(source_start)
            ends.append(source_end)
            continue
        for normalized_character in character.casefold():
            characters.append(normalized_character)
            starts.append(source_start)
            ends.append(source_end)
    while characters and characters[-1] == " ":
        characters.pop()
        starts.pop()
        ends.pop()
    return "".join(characters), tuple(starts), tuple(ends)


def exact_citation_excerpt(
    excerpt: str,
    source_text: str,
    *,
    max_chars: int = 512,
) -> str | None:
    """Map a formatting-normalized quote back to an exact source substring.

    This repairs transport-only differences such as JSON escapes, ANSI style
    sequences, whitespace, smart quotes, and case. It deliberately does not
    use fuzzy or semantic matching: a paraphrase remains a failed citation.
    """

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    needle = normalize_citation_match_text(excerpt).casefold()
    if not needle:
        return None
    haystack, starts, ends = _citation_match_text_with_offsets(source_text)
    match_start = haystack.find(needle)
    if match_start < 0:
        return None
    match_end = match_start + len(needle)
    exact = source_text[starts[match_start] : ends[match_end - 1]]
    if len(exact) > max_chars:
        return None
    return exact


def authoritative_source_text(content: str) -> str:
    """Return citation-ready source text for CapsuleEnvelopeV2 excerpt checks."""

    return normalize_citation_text(tool_inner_text(content))


def resolve_citation_source_id(
    source_id: str,
    allowed_source_ids: frozenset[str],
) -> str | None:
    """Map bare event ids to authoritative ``::span::`` ids when unambiguous."""

    if source_id in allowed_source_ids:
        return source_id
    if "::span::" in source_id:
        return None
    matches = sorted(
        sid for sid in allowed_source_ids if sid.split("::span::", 1)[0] == source_id
    )
    if not matches:
        return None
    preferred = f"{source_id}::span::000000"
    if preferred in allowed_source_ids:
        return preferred
    if len(matches) == 1:
        return matches[0]
    return None


def augment_source_text_aliases(sources: dict[str, str]) -> dict[str, str]:
    """Add bare event-id aliases for authoritative span-backed source text."""

    augmented = dict(sources)
    by_base: dict[str, list[str]] = {}
    for sid in sources:
        base, _, span = sid.partition("::span::")
        if not span:
            continue
        by_base.setdefault(base, []).append(sid)
    for base, spans in by_base.items():
        if base in augmented:
            continue
        preferred = f"{base}::span::000000"
        if preferred in augmented:
            augmented[base] = augmented[preferred]
        elif len(spans) == 1:
            augmented[base] = augmented[spans[0]]
    return augmented


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

    from .message_views import model_events

    events = model_events(events)
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
