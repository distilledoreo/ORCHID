from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit


DIAGNOSTIC_EXCERPT_LIMIT = 2_000


class ModelRunRecorder(Protocol):
    def record_model_run(self, record: dict[str, Any]) -> str:
        ...

    def update_model_run(self, run_id: str, **updates: Any) -> None:
        ...


class TelemetryPersistenceError(RuntimeError):
    """A model call cannot be considered successful without its run record."""


@dataclass(frozen=True)
class ModelCallContext:
    job_id: str | None = None
    thread_id: str | None = None
    generation: int | None = None
    stage: str | None = None
    selector_chunk_index: int | None = None
    canonicalizer_batch_index: int | None = None
    source_refs: tuple[str, ...] = field(default_factory=tuple)


def deterministic_input_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def endpoint_identity(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if not parsed.scheme or not parsed.netloc:
        return endpoint.rstrip("/")
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def bounded_diagnostic_excerpt(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+|bearer\s+)[^\s,}\"']+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(
        r"(?i)((?:api[_-]?key|token|secret)\s*[:=]\s*)[^\s,}\"']+",
        r"\1<redacted>",
        text,
    )
    return text[:DIAGNOSTIC_EXCERPT_LIMIT]
