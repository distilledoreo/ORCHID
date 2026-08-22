"""Staged live endurance runner: selector, capsule, and awake-recall layers.

Default CI does not call providers. The live path is opt-in via
ORCHID_LIVE_ENDURANCE=1 plus configured selector/canonicalizer/consolidator
and backend (Solar) endpoints. Temperature is fixed. Semantic misses are never
retried. Provider transport errors (429/500/503/504) may be retried up to three
times with exponential backoff.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import httpx

from endurance_harness import (
    CHECKPOINT_GENERATIONS,
    FactKind,
    PROJECT_ID,
    THREAD_ID,
    WorldState,
    _append,
    _lineage_depth,
    _pad_tail,
    parse_capsule_state,
    parse_facts,
    replay_history,
    script_for_generation,
)
from memory_gateway.compaction import CompactionWorker, Event, queue_snapshot_job
from memory_gateway.config import RuntimeConfig
from memory_gateway.context import ContextAssembler, estimate_tokens
from memory_gateway.db import SQLiteStore
from memory_gateway.openai_adapter import ModelProtocolError, ModelTransportError
from memory_gateway.pipeline import LosslessCompactionEngine, SelectionResult
from memory_gateway.pipeline_adapters import build_lossless_engine

STAGES = (10, 25, 50)
RETRYABLE_TRANSPORT_STATUSES = {429, 500, 503, 504}
TRANSPORT_RETRY_MAX_ATTEMPTS = 3
TRANSPORT_RETRY_BACKOFF_SECONDS = (30.0, 60.0, 120.0)
NEGATION = re.compile(
    r"\b(reject(?:ed)?|superseded|replaced|former|previous|old|not chosen|"
    r"declined|instead of|no longer|was\s+\d+|obsolete|incorrect)\b",
    re.I,
)
UNCONDITIONAL = re.compile(
    r"\b(always|unconditionally|regardless|must restart)\b",
    re.I,
)
# Windows matching these patterns are historical log residue, not current-state assertions.
HISTORICAL_LOG_CONTEXT = re.compile(
    r"protected-tail padding|filler chatter|additional log|"
    r"pads \d+ through \d+|recorded;|notes \d+ through \d+",
    re.I,
)
FACT_ASSERTION_ANCHORS: dict[str, re.Pattern[str]] = {
    "lease_seconds": re.compile(
        r"lease(?:[_\s-]*seconds?|[_\s-]*duration)|\[FACT id=lease_seconds",
        re.I,
    ),
    "compaction_generation": re.compile(
        r"compaction[_\s-]*generation\s*:|\[FACT id=compaction_generation",
        re.I,
    ),
    "database": re.compile(
        r"database(?:\s+status)?|\[FACT id=database|"
        r"\b(?:using|selected|chosen|is)\b.*(?:sqlite|postgres)",
        re.I,
    ),
    "lab_mascot": re.compile(r"lab[_\s-]*mascot|\[FACT id=lab_mascot", re.I),
    "coffee_order": re.compile(r"coffee(?:\s+order)?|\[FACT id=coffee_order", re.I),
    "project_name": re.compile(r"project(?:\s*name)?|\[FACT id=project_name", re.I),
    "owner": re.compile(r"\bowner\b|\[FACT id=owner", re.I),
    "timezone": re.compile(r"\btimezone\b|\[FACT id=timezone", re.I),
    "favorite_color": re.compile(
        r"favorite[_\s-]*color|\[FACT id=favorite_color",
        re.I,
    ),
    "recover_expired_jobs": re.compile(
        r"recover[_\s-]*expired[_\s-]*jobs|\[FACT id=recover_expired_jobs",
        re.I,
    ),
    "promotion": re.compile(r"promotion(?:\s+policy)?|\[FACT id=promotion", re.I),
    "event_model": re.compile(r"event[_\s-]*model|\[FACT id=event_model", re.I),
}


def http_status_from_error(error: BaseException | str) -> int | None:
    match = re.search(r"HTTP (\d{3})", str(error))
    if match:
        return int(match.group(1))
    return None


def is_retryable_transport_error(error: BaseException | str) -> bool:
    return http_status_from_error(error) in RETRYABLE_TRANSPORT_STATUSES


class TransportRetryingStructuredClient:
    """Retry only provider transport failures; never retry a valid semantic response."""

    def __init__(
        self,
        inner: Any,
        *,
        max_attempts: int = TRANSPORT_RETRY_MAX_ATTEMPTS,
        backoff_seconds: tuple[float, ...] = TRANSPORT_RETRY_BACKOFF_SECONDS,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @property
    def response_format(self) -> dict[str, Any] | None:
        return self._inner.response_format

    @response_format.setter
    def response_format(self, value: dict[str, Any] | None) -> None:
        self._inner.response_format = value

    async def complete_json(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._inner._transport_attempt = attempt
            try:
                return await self._inner.complete_json(input_payload)
            except ModelProtocolError:
                raise
            except ModelTransportError as error:
                last_error = error
                if not is_retryable_transport_error(error) or attempt >= self.max_attempts:
                    raise
                delay = self.backoff_seconds[min(attempt - 1, len(self.backoff_seconds) - 1)]
                await self._sleep(delay)
        assert last_error is not None
        raise last_error


def _optional_nonempty_string() -> dict[str, Any]:
    """String answers must be non-empty; null remains allowed for absence."""

    return {"type": ["string", "null"], "minLength": 1}


RECALL_PROMPT_VERSION = "awake-recall-instruction-v4-qid"
RECALL_SCHEMA_VERSION = "orchid_endurance_recall_v4_qid"
RECALL_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("q1", "What is the current lease duration?"),
    ("q2", "Under what condition may expired RUNNING jobs restart?"),
    ("q3", "What is the current database decision?"),
    ("q4", "Was a database alternative rejected? If so, which one?"),
    ("q5", "What is the project name?"),
    ("q6", "Who is the owner?"),
    ("q7", "What timezone is remembered?"),
    ("q8", "What decision was made about how events and corrections are stored?"),
    ("q9", "What decision was made about how a new memory capsule is promoted?"),
    ("q10", "What lab mascot is currently remembered?"),
    ("q11", "What coffee order is remembered?"),
    ("q12", "What favorite color is remembered?"),
)
# Frozen: q10 must not ask about the running backend/chat model. Gen-5 showed
# Solar filling that slot with its own identity while other absent facts stayed null.
RECALL_INSTRUCTION_CORE = (
    "Answer from the supplied memory only. "
    "Do not invent missing values. "
    "Preserve qualifiers, conditions, exceptions, negation, rejection, and uncertainty. "
    "Do not collapse a conditional policy into a bare boolean. "
    "When a requested value is a quantity, preserve the value and the unit or meaning "
    "represented by memory. "
    "Do not copy unrelated nearby text into another field. "
    "If a field is unknown, use null. Do not use empty strings. "
    "Return JSON only, with keys q1 through q12."
)


def recall_user_prompt(
    questions: tuple[tuple[str, str], ...] = RECALL_QUESTIONS,
) -> str:
    numbered = "\n".join(f"{qid}: {question}" for qid, question in questions)
    json_keys = ", ".join(qid for qid, _question in questions)
    core = RECALL_INSTRUCTION_CORE
    if questions is not RECALL_QUESTIONS:
        core = re.sub(
            r"Return JSON only, with keys q1 through q12\.",
            f"Return JSON only, with keys {json_keys}.",
            core,
        )
    return f"{core}\n\n{numbered}"


def recall_response_format(
    *,
    schema_name: str,
    questions: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    qid: _optional_nonempty_string() for qid, _question in questions
                },
                "required": [qid for qid, _question in questions],
                "additionalProperties": False,
            },
        },
    }


RECALL_INSTRUCTION = recall_user_prompt()
RECALL_RESPONSE_FORMAT = recall_response_format(
    schema_name=RECALL_SCHEMA_VERSION,
    questions=RECALL_QUESTIONS,
)

ABSENT_IDENTITY_PROBE_VERSION = "absent-identity-probe-v1"
ABSENT_IDENTITY_PROBE_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("backend_chat_model", "What backend chat model is currently remembered?"),
    ("favorite_animal", "What favorite animal is currently remembered?"),
    ("deployment_region", "What deployment region is currently remembered?"),
    ("cache_provider", "What cache provider is currently remembered?"),
    ("preferred_editor", "What preferred editor is currently remembered?"),
)
ABSENT_IDENTITY_INSTRUCTION = recall_user_prompt(ABSENT_IDENTITY_PROBE_QUESTIONS)
ABSENT_IDENTITY_RESPONSE_FORMAT = recall_response_format(
    schema_name=ABSENT_IDENTITY_PROBE_VERSION,
    questions=ABSENT_IDENTITY_PROBE_QUESTIONS,
)


RETRYABLE_PROTOCOL_MARKERS = (
    "outside current chunk",
    "unknown cited source",
    "unknown event id",
    "duplicate cited source",
    "duplicate event ids",
    "selected_event_ids as a string array",
    "selector response must",
    "canonicalizer response must",
    "must remain in supplied source order",
)
PROTOCOL_RETRY_MAX_ATTEMPTS = 3


def is_retryable_protocol_error(error: BaseException | str) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in RETRYABLE_PROTOCOL_MARKERS)


class ProtocolRetryingSelector:
    """Retry only source-reference protocol violations; never retry a valid subset."""

    def __init__(
        self,
        inner: Any,
        *,
        max_attempts: int = PROTOCOL_RETRY_MAX_ATTEMPTS,
        log_path: Path | None = None,
        stage: str = "selector",
        sleep: Any = asyncio.sleep,
        backoff_seconds: float = 2.0,
    ) -> None:
        self._inner = inner
        self.max_attempts = max_attempts
        self.log_path = log_path
        self.stage = stage
        self._sleep = sleep
        self.backoff_seconds = backoff_seconds
        self.protocol_events: list[dict[str, Any]] = []
        for name in (
            "chunk_target_tokens",
            "selector_context_tokens",
            "span_safety_margin",
            "client",
            "batch_target_tokens",
        ):
            if hasattr(inner, name):
                setattr(self, name, getattr(inner, name))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def select(self, *, events: list[Event]) -> SelectionResult:
        return await self._run(lambda: self._inner.select(events=events))

    async def canonicalize(self, *, events: list[Any]) -> Any:
        return await self._run(lambda: self._inner.canonicalize(events=events))

    async def _run(self, operation: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = await operation()
                if attempt > 1:
                    self._record(
                        {
                            "stage": self.stage,
                            "protocol_attempt": attempt,
                            "recovered": True,
                            "error": None,
                        }
                    )
                return result
            except Exception as error:
                last_error = error
                retryable = is_retryable_protocol_error(error)
                self._record(
                    {
                        "stage": self.stage,
                        "protocol_attempt": attempt,
                        "recovered": False,
                        "retryable": retryable,
                        "error": str(error),
                    }
                )
                if not retryable or attempt >= self.max_attempts:
                    raise
                await self._sleep(self.backoff_seconds)
        assert last_error is not None
        raise last_error

    def _record(self, payload: dict[str, Any]) -> None:
        self.protocol_events.append(payload)
        print(
            f"protocol {payload['stage']} attempt={payload['protocol_attempt']} "
            f"retryable={payload.get('retryable', False)} recovered={payload['recovered']}",
            flush=True,
        )
        if self.log_path is not None:
            append_jsonl(self.log_path, payload)


class LiveEnduranceStop(RuntimeError):
    def __init__(self, generation: int, layer: str, reason: str) -> None:
        self.generation = generation
        self.layer = layer
        self.reason = reason
        super().__init__(f"stop at generation {generation} [{layer}]: {reason}")


@dataclass
class LayerProbe:
    layer: str
    name: str
    passed: bool
    fatal: bool
    detail: str


@dataclass
class SelectionRecord:
    generation: int
    snapshot_event_ids: tuple[str, ...]
    selected_event_ids: tuple[str, ...]
    event_facts: dict[str, list[tuple[str, str, str]]]


@dataclass
class LiveCheckpoint:
    generation: int
    capsule_id: str
    capsule_chars: int
    capsule_tokens: int
    compaction_ms: float
    lineage_depth: int
    residue_hits: list[str]
    residue_chars: int
    probes: list[LayerProbe]
    selector_misses: list[str]
    capsule_losses: list[str]
    recall_losses: list[str]
    recall_answers: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return all(probe.passed for probe in self.probes)

    @property
    def fatal_failures(self) -> list[LayerProbe]:
        return [probe for probe in self.probes if probe.fatal and not probe.passed]


@dataclass
class LiveReport:
    stages_completed: list[int]
    stopped: LiveEnduranceStop | None
    checkpoints: list[LiveCheckpoint]
    latencies_ms: list[float]
    capsule_growth: list[tuple[int, int]]
    residue_by_generation: list[tuple[int, int]]
    generations_run: int

    @property
    def passed(self) -> bool:
        return self.stopped is None and all(item.passed for item in self.checkpoints)


class RecordingSelector:
    """Wraps a selector and records snapshot membership vs selected IDs."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.records: list[SelectionRecord] = []
        self._pending_generation = 0
        for name in (
            "chunk_target_tokens",
            "selector_context_tokens",
            "span_safety_margin",
            "client",
        ):
            if hasattr(inner, name):
                setattr(self, name, getattr(inner, name))

    def set_generation(self, generation: int) -> None:
        self._pending_generation = generation

    async def select(self, *, events: list[Event]) -> SelectionResult:
        result = await self.inner.select(events=events)
        self.records.append(
            SelectionRecord(
                generation=self._pending_generation,
                snapshot_event_ids=tuple(event.id for event in events),
                selected_event_ids=tuple(result.selected_event_ids),
                event_facts={
                    event.id: parse_facts(event.content) for event in events
                },
            )
        )
        return result


def durable_targets(expected: WorldState) -> list[tuple[str, str, str]]:
    """Still-valid conversational facts, excluding the synthetic generation counter."""

    targets: list[tuple[str, str, str]] = []
    for fact_id, value in expected.current.items():
        if fact_id == "compaction_generation":
            continue
        targets.append((fact_id, FactKind.CURRENT, value))
    for fact_id, value in expected.decisions.items():
        targets.append((fact_id, FactKind.DECISION, value))
    for fact_id, value in expected.conditionals.items():
        targets.append((fact_id, FactKind.CONDITIONAL, value))
    for fact_id, value in expected.low.items():
        targets.append((fact_id, FactKind.LOW, value))
    return targets


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _contains(haystack: str, needle: str) -> bool:
    return _normalize(needle).lower() in _normalize(haystack).lower()


def _value_occurrences(text: str, value: str) -> list[tuple[int, int]]:
    """Return (start, end) spans for value matches in text."""

    if value.isdigit():
        pattern = re.compile(rf"(?<!\d){re.escape(value)}(?!\d)")
        return [(match.start(), match.end()) for match in pattern.finditer(text)]
    lowered = text.lower()
    target = value.lower()
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        index = lowered.find(target, start)
        if index < 0:
            return spans
        spans.append((index, index + len(value)))
        start = index + len(value)
    return spans


def _windows(text: str, needle: str, radius: int = 90) -> list[str]:
    windows: list[str] = []
    for index, end in _value_occurrences(text, needle):
        left = max(0, index - radius)
        right = min(len(text), end + radius)
        windows.append(text[left:right])
    return windows


def _fact_assertion_anchor(fact_id: str) -> re.Pattern[str]:
    pattern = FACT_ASSERTION_ANCHORS.get(fact_id)
    if pattern is not None:
        return pattern
    escaped = re.escape(fact_id).replace(r"\ ", r"[_\s-]*")
    return re.compile(rf"\b{escaped}\b|\[FACT id={re.escape(fact_id)}", re.I)


def _windows_naive(text: str, needle: str, radius: int = 90) -> list[str]:
    """Legacy value-only windows without numeric word boundaries."""

    lowered = text.lower()
    target = needle.lower()
    windows: list[str] = []
    start = 0
    while True:
        index = lowered.find(target, start)
        if index < 0:
            return windows
        left = max(0, index - radius)
        right = min(len(text), index + len(needle) + radius)
        windows.append(text[left:right])
        start = index + len(needle)
    return windows


def _claimed_as_current_naive(text: str, value: str) -> bool:
    """Legacy value-only check; retained for offline rescoring comparisons."""

    windows = _windows_naive(text, value)
    if not windows:
        return False
    return any(not NEGATION.search(window) for window in windows)


def _claimed_as_current_for_fact(text: str, fact_id: str, value: str) -> bool:
    """True when an obsolete value is asserted as current for a specific fact key."""

    anchor = _fact_assertion_anchor(fact_id)
    windows = _windows(text, value)
    if not windows:
        return False
    for window in windows:
        if NEGATION.search(window):
            continue
        if HISTORICAL_LOG_CONTEXT.search(window):
            continue
        if not anchor.search(window):
            continue
        return True
    return False


def _claimed_as_current(text: str, value: str) -> bool:
    windows = _windows(text, value)
    if not windows:
        return False
    return any(not NEGATION.search(window) for window in windows)


def residue_hits(capsule: str, expected: WorldState) -> list[str]:
    hits: list[str] = []
    for fact_id, values in expected.superseded.items():
        for value in values:
            if expected.current.get(fact_id) == value:
                continue
            if _contains(capsule, value):
                hits.append(f"superseded:{fact_id}={value}")
    for fact_id, value in expected.rejected.items():
        if _contains(capsule, value):
            hits.append(f"rejected:{fact_id}={value}")
    return hits


def resurrection_hits(
    capsule: str,
    expected: WorldState,
    *,
    fact_key_scoped: bool = True,
) -> list[str]:
    """Return semantic resurrection hits, optionally using the legacy value-only scorer."""

    claimed = _claimed_as_current_for_fact if fact_key_scoped else (
        lambda text, fact_id, value: _claimed_as_current_naive(text, value)
    )
    hits: list[str] = []
    for fact_id, values in expected.superseded.items():
        for value in values:
            if expected.current.get(fact_id) == value:
                continue
            if claimed(capsule, fact_id, value):
                hits.append(f"superseded current {fact_id}={value}")
    for fact_id, value in expected.rejected.items():
        if claimed(capsule, fact_id, value) and not _contains(
            capsule, expected.current.get(fact_id, "")
        ):
            hits.append(f"rejected current {fact_id}={value}")
        elif claimed(capsule, fact_id, value) and fact_id == "database" and value == "Postgres":
            if not re.search(r"reject|not (using|chosen)|instead", capsule, re.I):
                hits.append(f"rejected current {fact_id}={value}")
    return hits


def score_selector_layer(
    expected: WorldState,
    records: list[SelectionRecord],
    events: list[dict[str, Any]],
) -> list[LayerProbe]:
    event_by_id = {event["id"]: event for event in events}
    misses: list[str] = []
    for fact_id, kind, value in durable_targets(expected):
        matching_ids = [
            event["id"]
            for event in events
            if (fact_id, kind, value) in parse_facts(event["content"])
        ]
        if not matching_ids:
            misses.append(f"{fact_id}: introducing event missing from history")
            continue
        latest_id = matching_ids[-1]
        host = next(
            (
                record
                for record in records
                if latest_id in record.snapshot_event_ids
            ),
            None,
        )
        if host is None:
            misses.append(f"{fact_id}: introducing event never entered a snapshot")
            continue
        selected = set(host.selected_event_ids)
        parent_ok = latest_id in selected or any(
            source_id.startswith(f"{latest_id}::span::") for source_id in selected
        )
        if not parent_ok:
            misses.append(
                f"{fact_id}={value} gen {host.generation} event {latest_id} not selected"
            )
        _ = event_by_id
    return [
        LayerProbe(
            layer="selector",
            name="selector_retention",
            passed=not misses,
            fatal=True,
            detail="; ".join(misses) or "all still-valid introducing events selected",
        )
    ]


def score_capsule_layer(expected: WorldState, capsule: str) -> list[LayerProbe]:
    probes: list[LayerProbe] = []
    losses: list[str] = []
    for fact_id, _kind, value in durable_targets(expected):
        if not _contains(capsule, value):
            losses.append(f"{fact_id}={value}")
    probes.append(
        LayerProbe(
            layer="capsule",
            name="current_facts_present",
            passed=not losses,
            fatal=True,
            detail="; ".join(losses) or "all still-valid values present in capsule",
        )
    )

    resurrection: list[str] = []
    resurrection = resurrection_hits(capsule, expected, fact_key_scoped=True)
    probes.append(
        LayerProbe(
            layer="capsule",
            name="no_resurrection",
            passed=not resurrection,
            fatal=True,
            detail="; ".join(resurrection) or "obsolete values not claimed as current",
        )
    )

    invented: list[str] = []
    if re.search(r"\bredis\b", capsule, re.I) and re.search(
        r"\b(chose|chosen|using|selected|decided on)\s+redis\b", capsule, re.I
    ):
        invented.append("Redis adopted as a decision")
    extra_claims = re.findall(
        r"\b(PostgreSQL cluster|MySQL|MongoDB)\b",
        capsule,
        re.I,
    )
    invented.extend(extra_claims)
    probes.append(
        LayerProbe(
            layer="capsule",
            name="no_invented_state",
            passed=not invented,
            fatal=True,
            detail="; ".join(invented) or "no invented store/cache decision",
        )
    )

    conditional_ok = True
    detail = "no conditionals yet"
    if expected.conditionals:
        text = expected.conditionals.get("recover_expired_jobs", "")
        present = _contains(capsule, "recover_expired_jobs") or _contains(capsule, text)
        unconditional = bool(UNCONDITIONAL.search(capsule)) and present
        conditional_ok = present and not unconditional
        detail = (
            "conditional preserved"
            if conditional_ok
            else "conditional missing or stated as unconditional"
        )
    probes.append(
        LayerProbe(
            layer="capsule",
            name="conditionals_remain_conditional",
            passed=conditional_ok,
            fatal=True,
            detail=detail,
        )
    )

    hits = residue_hits(capsule, expected)
    probes.append(
        LayerProbe(
            layer="capsule",
            name="superseded_residue",
            passed=True,
            fatal=False,
            detail=f"{len(hits)} obsolete mentions: " + (", ".join(hits) or "none"),
        )
    )
    return probes


def expected_recall(expected: WorldState) -> dict[str, str | None]:
    """Needles that must appear in q1..q12 answers. None means unavailable."""

    return {
        "q1": expected.current.get("lease_seconds"),
        "q2": "recover_expired_jobs" if expected.conditionals.get("recover_expired_jobs") else None,
        "q3": expected.current.get("database"),
        "q4": expected.rejected.get("database"),
        "q5": expected.current.get("project_name"),
        "q6": expected.current.get("owner"),
        "q7": expected.current.get("timezone"),
        "q8": "append-only" if expected.decisions.get("event_model") else None,
        "q9": "compare-and-swap" if expected.decisions.get("promotion") else None,
        "q10": expected.current.get("lab_mascot"),
        "q11": expected.low.get("coffee_order"),
        "q12": expected.low.get("favorite_color"),
    }


def _unavailable_answer(observed: Any) -> bool:
    if observed is None:
        return True
    if not isinstance(observed, str):
        return False
    lowered = observed.strip().lower()
    return lowered in {
        "null",
        "unknown",
        "none",
        "n/a",
        "not specified",
        "not remembered",
        "no",
        "false",
    } or lowered.startswith("i do not know") or "does not mention" in lowered or "not mentioned" in lowered


def classify_absent_identity_probe(answers: dict[str, Any]) -> dict[str, Any]:
    """Classify a probe of facts that should all be unavailable at gen 5."""

    invented = {
        key: value
        for key, value in answers.items()
        if not _unavailable_answer(value)
    }
    backend = invented.get("backend_chat_model")
    others = {
        key: value for key, value in invented.items() if key != "backend_chat_model"
    }
    backend_is_identity = isinstance(backend, str) and "solar" in backend.lower()
    if backend_is_identity and not others:
        verdict = "identity_leakage"
    elif invented:
        verdict = "general_hallucination"
    else:
        verdict = "all_null"
    return {
        "verdict": verdict,
        "invented": invented,
        "backend_is_identity": backend_is_identity,
    }


def score_recall_layer(expected: WorldState, answers: dict[str, Any]) -> list[LayerProbe]:
    want = expected_recall(expected)
    losses: list[str] = []
    invented: list[str] = []
    for key, value in want.items():
        observed = answers.get(key)
        if value is None:
            if not _unavailable_answer(observed):
                invented.append(f"{key}={observed}")
            continue
        if not isinstance(observed, str) or not _contains(observed, value):
            losses.append(f"{key} expected {value!r} got {observed!r}")
            continue
        if key == "q2":
            collapsed = observed.strip().lower() in {"true", "false", "yes", "no"}
            if "if" not in observed.lower() or collapsed:
                losses.append(f"{key} condition collapsed or missing: {observed!r}")
    return [
        LayerProbe(
            layer="recall",
            name="awake_recall",
            passed=not losses,
            fatal=True,
            detail="; ".join(losses) or "Solar matched still-valid state",
        ),
        LayerProbe(
            layer="recall",
            name="recall_no_invented_state",
            passed=not invented,
            fatal=True,
            detail="; ".join(invented) or "no extra recall claims",
        ),
    ]


class RecallClient:
    async def complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        raise NotImplementedError


class SolarRecallClient:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        max_attempts: int = TRANSPORT_RETRY_MAX_ATTEMPTS,
        backoff_seconds: tuple[float, ...] = TRANSPORT_RETRY_BACKOFF_SECONDS,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self.config = config
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"content-type": "application/json"}
        if self.config.backend_api_key:
            headers["authorization"] = f"Bearer {self.config.backend_api_key}"
        body = {
            "model": self.config.backend_model or "solar-pro4",
            "temperature": 0,
            "top_p": 1,
            "stream": False,
            "messages": messages,
            "response_format": response_format or RECALL_RESPONSE_FORMAT,
        }
        last_status = None
        last_excerpt = ""
        for attempt in range(1, self.max_attempts + 1):
            async with httpx.AsyncClient(timeout=self.config.model_timeout_seconds) as client:
                response = await client.post(
                    _chat_url(self.config.backend_url),
                    json=body,
                    headers=headers,
                )
            last_status = response.status_code
            last_excerpt = response.text[:300]
            if response.status_code in RETRYABLE_TRANSPORT_STATUSES:
                if attempt >= self.max_attempts:
                    raise LiveEnduranceStop(
                        0,
                        "provider",
                        "PROVIDER ABORT after "
                        f"{self.max_attempts} transport attempts: "
                        f"Solar HTTP {response.status_code}: {last_excerpt}",
                    )
                delay = self.backoff_seconds[min(attempt - 1, len(self.backoff_seconds) - 1)]
                await self._sleep(delay)
                continue
            if response.status_code >= 400:
                raise LiveEnduranceStop(
                    0,
                    "recall",
                    f"Solar HTTP {response.status_code}: {last_excerpt}",
                )
            payload = response.json()
            content = (
                ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
            )
            if not isinstance(content, str):
                raise LiveEnduranceStop(0, "recall", "Solar returned no string content")
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as error:
                raise LiveEnduranceStop(0, "recall", "Solar content was not JSON") from error
            if not isinstance(parsed, dict):
                raise LiveEnduranceStop(0, "recall", "Solar JSON was not an object")
            return parsed
        raise LiveEnduranceStop(
            0,
            "provider",
            f"PROVIDER ABORT: Solar HTTP {last_status}: {last_excerpt}",
        )


class CapsuleParseRecallClient:
    """Deterministic recall used by the harness unit tests."""

    async def complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        capsule = ""
        for message in messages:
            text = str(message.get("content") or "")
            if text.startswith("PERSISTENT MEMORY CAPSULE"):
                capsule = text
                break
        body = capsule.split("\n\n", 1)[-1]
        state = parse_capsule_state(body)
        if state is not None:
            return {
                "q1": state.current.get("lease_seconds"),
                "q2": state.conditionals.get("recover_expired_jobs"),
                "q3": state.current.get("database"),
                "q4": state.rejected.get("database"),
                "q5": state.current.get("project_name"),
                "q6": state.current.get("owner"),
                "q7": state.current.get("timezone"),
                "q8": state.decisions.get("event_model"),
                "q9": state.decisions.get("promotion"),
                "q10": state.current.get("lab_mascot"),
                "q11": state.low.get("coffee_order"),
                "q12": state.low.get("favorite_color"),
            }
        answers: dict[str, Any] = {qid: None for qid, _question in RECALL_QUESTIONS}
        if _contains(capsule, "30") and not _contains(capsule, "900"):
            answers["q1"] = "30 seconds"
        if _contains(capsule, "900"):
            answers["q1"] = "900 seconds"
        if _contains(capsule, "recover_expired_jobs"):
            answers["q2"] = (
                "expired RUNNING jobs restart only if recover_expired_jobs is true"
            )
        if _contains(capsule, "SQLite"):
            answers["q3"] = "SQLite"
        elif _contains(capsule, "undecided"):
            answers["q3"] = "undecided"
        if _contains(capsule, "Postgres"):
            answers["q4"] = "Postgres"
        if _contains(capsule, "Orchid Memory Gateway"):
            answers["q5"] = "Orchid Memory Gateway"
        if _contains(capsule, "Disti"):
            answers["q6"] = "Disti"
        if _contains(capsule, "America/New_York"):
            answers["q7"] = "America/New_York"
        if _contains(capsule, "append-only"):
            answers["q8"] = "events are append-only; corrections add a new event"
        if _contains(capsule, "compare-and-swap"):
            answers["q9"] = (
                "promote READY descendants with compare-and-swap against the active capsule"
            )
        if _contains(capsule, "okapi"):
            answers["q10"] = "okapi"
        elif _contains(capsule, "red panda"):
            answers["q10"] = "red panda"
        if _contains(capsule, "black coffee"):
            answers["q11"] = "black coffee"
        elif _contains(capsule, "oat latte"):
            answers["q11"] = "oat latte"
        if _contains(capsule, "teal"):
            answers["q12"] = "teal"
        return answers


def _chat_url(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    if endpoint.endswith("/v1"):
        return f"{endpoint}/chat/completions"
    return f"{endpoint}/v1/chat/completions"


def _live_configured(config: RuntimeConfig) -> bool:
    return bool(
        config.compaction_configured
        and config.backend_url
        and config.backend_model
    )


HARD_STOP_LAYERS = {"provenance", "provider"}
DEGRADATION_CHECKPOINTS = (60, 75, 100, 125, 150, 175, 200)
FIRST_SEMANTIC_FAILURE = {
    "generation": 50,
    "verdict": "FAIL",
    "cause": {
        "selector": "PASS",
        "provenance_lineage": "PASS",
        "awake_recall": "PASS",
        "capsule_current_facts": "still present",
        "capsule_no_resurrection": "FAIL",
        "detail": (
            "obsolete values were being represented as current; "
            "superseded current compaction_generation=3,4,5,7,30,35,40; "
            "superseded current lease_seconds=30"
        ),
        "residue_count": 10,
        "capsule_chars": 770,
    },
    "immutable": True,
}
FIRST_SELECTOR_PROTOCOL_FAILURE = {
    "generation": 63,
    "verdict": "FAILED",
    "error": "selector returned ID outside current chunk",
    "immutable": True,
}


def _split_probe_items(detail: str, *, ok_prefixes: tuple[str, ...]) -> list[str]:
    lowered = detail.strip().lower()
    if any(lowered.startswith(prefix.lower()) for prefix in ok_prefixes):
        return []
    return [part.strip() for part in detail.split(";") if part.strip()]


def taxonomy_from_probes(
    selector_probes: list[LayerProbe],
    capsule_probes: list[LayerProbe],
    residue: list[str],
    recall_probes: list[LayerProbe] | None = None,
) -> dict[str, Any]:
    by_name = {probe.name: probe for probe in selector_probes + capsule_probes}
    resurrection = _split_probe_items(
        by_name["no_resurrection"].detail,
        ok_prefixes=("obsolete values not claimed",),
    )
    current_fact_loss = _split_probe_items(
        by_name["current_facts_present"].detail,
        ok_prefixes=("all still-valid values present",),
    )
    invented = _split_probe_items(
        by_name["no_invented_state"].detail,
        ok_prefixes=("no invented",),
    )
    conditional_items: list[str] = []
    if not by_name["conditionals_remain_conditional"].passed:
        conditional_items = [by_name["conditionals_remain_conditional"].detail]
    rejected = [item for item in resurrection if "rejected current" in item]
    superseded_resurrection = [
        item for item in resurrection if "rejected current" not in item
    ]
    selector_loss = _split_probe_items(
        by_name["selector_retention"].detail,
        ok_prefixes=("all still-valid introducing",),
    )
    recall_loss: list[str] = []
    recall_invented: list[str] = []
    if recall_probes:
        recall_by_name = {probe.name: probe for probe in recall_probes}
        if "awake_recall" in recall_by_name and not recall_by_name["awake_recall"].passed:
            recall_loss = _split_probe_items(
                recall_by_name["awake_recall"].detail,
                ok_prefixes=("solar matched",),
            )
        if (
            "recall_no_invented_state" in recall_by_name
            and not recall_by_name["recall_no_invented_state"].passed
        ):
            recall_invented = _split_probe_items(
                recall_by_name["recall_no_invented_state"].detail,
                ok_prefixes=("no extra recall",),
            )
    resurrected_values = {item.split("=", 1)[-1] for item in resurrection if "=" in item}
    residue_only = [
        hit
        for hit in residue
        if hit.split("=", 1)[-1] not in resurrected_values
    ]
    return {
        "residue": residue_only,
        "residue_count": len(residue_only),
        "residue_all": residue,
        "resurrection": superseded_resurrection,
        "resurrection_count": len(superseded_resurrection),
        "rejected_option_corruption": rejected,
        "rejected_option_corruption_count": len(rejected),
        "current_fact_loss": current_fact_loss,
        "current_fact_loss_count": len(current_fact_loss),
        "invented_state": invented,
        "invented_state_count": len(invented),
        "conditional_corruption": conditional_items,
        "conditional_corruption_count": len(conditional_items),
        "selector_loss": selector_loss,
        "selector_loss_count": len(selector_loss),
        "awake_recall_failure": recall_loss + recall_invented,
        "awake_recall_failure_count": len(recall_loss) + len(recall_invented),
        "semantic_error_count": (
            len(superseded_resurrection)
            + len(rejected)
            + len(current_fact_loss)
            + len(invented)
            + len(conditional_items)
            + len(selector_loss)
            + len(recall_loss)
            + len(recall_invented)
        ),
    }


def healed_items(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, list[str]]:
    if previous is None:
        return {}
    healed: dict[str, list[str]] = {}
    for key in (
        "residue",
        "resurrection",
        "rejected_option_corruption",
        "current_fact_loss",
        "invented_state",
        "conditional_corruption",
        "selector_loss",
        "awake_recall_failure",
    ):
        gone = sorted(set(previous.get(key) or ()) - set(current.get(key) or ()))
        if gone:
            healed[key] = gone
    return healed


def hard_stop_from_probes(
    generation: int,
    probes: list[LayerProbe],
    *,
    continue_on_semantic: bool,
) -> LiveEnduranceStop | None:
    for probe in probes:
        if probe.passed or not probe.fatal:
            continue
        if probe.layer in HARD_STOP_LAYERS or not continue_on_semantic:
            return LiveEnduranceStop(
                generation, probe.layer, f"{probe.name}: {probe.detail}"
            )
    return None


def checkpoint_sqlite(path: Path) -> None:
    import sqlite3

    connection = sqlite3.connect(str(path))
    try:
        connection.execute("PRAGMA wal_checkpoint(FULL)")
    finally:
        connection.close()


def copy_sqlite_db(source: Path, destination: Path) -> None:
    import shutil

    checkpoint_sqlite(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    shutil.copy2(source, destination)
    for suffix in ("-wal", "-shm"):
        extra = destination.with_name(destination.name + suffix)
        if extra.exists():
            extra.unlink()


def history_and_backlog_stats(store: SQLiteStore, active: dict[str, Any]) -> dict[str, Any]:
    events = store.list_events(THREAD_ID)
    history_chars = sum(len(event.get("content") or "") for event in events)
    history_tokens = sum(estimate_tokens(event.get("content") or "") for event in events)
    covered = (
        store.get_event(active["covered_end_event_id"])
        if active.get("covered_end_event_id")
        else None
    )
    covered_seq = int(covered["sequence"]) if covered else 0
    backlog = [
        event for event in events if int(event["sequence"]) > covered_seq
    ]
    backlog_chars = sum(len(event.get("content") or "") for event in backlog)
    capsule_chars = len(active["content"])
    capsule_tokens = estimate_tokens(active["content"])
    return {
        "authoritative_raw_event_count": len(events),
        "authoritative_raw_chars": history_chars,
        "authoritative_raw_tokens": history_tokens,
        "covered_watermark_event_id": active.get("covered_end_event_id"),
        "covered_watermark_sequence": covered_seq,
        "uncompacted_backlog_events": len(backlog),
        "uncompacted_backlog_chars": backlog_chars,
        "uncompacted_backlog_tokens": sum(
            estimate_tokens(event.get("content") or "") for event in backlog
        ),
        "capsule_chars": capsule_chars,
        "capsule_tokens": capsule_tokens,
        "compression_ratio_vs_history": (
            (history_chars / capsule_chars) if capsule_chars else None
        ),
    }


def job_and_model_stats(store: SQLiteStore, generation: int) -> dict[str, Any]:
    jobs = [
        job
        for job in store.list_jobs(thread_id=THREAD_ID, limit=500)
        if int(job.get("generation") or 0) == generation
    ]
    job = jobs[0] if jobs else {}
    runs = store.list_model_runs(job_id=job["id"], limit=200) if job.get("id") else []
    consolidator = [run for run in runs if run.get("stage") == "consolidator"]
    selector = [run for run in runs if run.get("stage") == "selector"]
    canonicalizer = [run for run in runs if run.get("stage") == "canonicalizer"]
    attempts = []
    for run in runs:
        metadata = run.get("metadata_json") or run.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        attempt = metadata.get("transport_attempt")
        if attempt is not None:
            attempts.append(int(attempt))
    return {
        "job_id": job.get("id"),
        "snapshot_start_event_id": job.get("snapshot_start_event_id"),
        "snapshot_end_event_id": job.get("snapshot_end_event_id"),
        "selector_chunks": len(selector),
        "canonicalizer_batches": len(canonicalizer),
        "selected_reference_count": None,
        "gemini_input_tokens": sum(int(run.get("input_tokens") or 0) for run in consolidator),
        "gemini_output_tokens": sum(int(run.get("output_tokens") or 0) for run in consolidator),
        "gemini_reasoning_tokens": sum(
            int(run.get("reasoning_tokens") or 0) for run in consolidator
        ),
        "gemini_latency_ms": sum(float(run.get("wall_ms") or 0) for run in consolidator),
        "provider_attempt_count": max(attempts) if attempts else len(consolidator) or 1,
        "model_run_statuses": [run.get("status") for run in runs],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def freeze_gen50_first_failure(*, source_db: Path, freeze_dir: Path) -> dict[str, Any]:
    freeze_dir.mkdir(parents=True, exist_ok=True)
    frozen_db = freeze_dir / "live_endurance_gen50_first_failure.db"
    copy_sqlite_db(source_db, frozen_db)
    store = SQLiteStore(str(frozen_db))
    active = store.get_active_capsule(THREAD_ID)
    if active is None:
        raise RuntimeError("freeze copy has no active capsule")
    events = store.list_events(THREAD_ID)
    expected = replay_history(event["content"] for event in events)
    capsule_probes = score_capsule_layer(expected, active["content"])
    residue = residue_hits(active["content"], expected)
    payload = {
        "study": "orchid-endurance-degradation-51-200",
        "first_semantic_failure": FIRST_SEMANTIC_FAILURE,
        "frozen_db": str(frozen_db),
        "capsule": {
            "id": active["id"],
            "base_capsule_id": active.get("base_capsule_id"),
            "content": active["content"],
            "chars": len(active["content"]),
            "source_event_hash": active.get("source_event_hash"),
            "input_hash": active.get("input_hash"),
            "output_hash": active.get("output_hash"),
            "capsule_hash": active.get("capsule_hash"),
            "covered_start_event_id": active.get("covered_start_event_id"),
            "covered_end_event_id": active.get("covered_end_event_id"),
            "snapshot_start_event_id": active.get("snapshot_start_event_id"),
            "snapshot_end_event_id": active.get("snapshot_end_event_id"),
        },
        "lineage_depth": _lineage_depth(store, active["id"]),
        "history": history_and_backlog_stats(store, active),
        "capsule_probes": [
            {"name": probe.name, "passed": probe.passed, "detail": probe.detail}
            for probe in capsule_probes
        ],
        "residue": residue,
        "note": (
            "Generation 50 is permanently recorded as the first semantic failure. "
            "This copy must not be mutated. Working copies must be separate files."
        ),
    }
    write_json(freeze_dir / "gen50_first_semantic_failure.json", payload)
    return payload


def freeze_gen63_protocol_failure(*, source_db: Path, freeze_dir: Path) -> dict[str, Any]:
    freeze_dir.mkdir(parents=True, exist_ok=True)
    frozen_db = freeze_dir / "live_endurance_gen63_protocol_failure.db"
    copy_sqlite_db(source_db, frozen_db)
    store = SQLiteStore(str(frozen_db))
    active = store.get_active_capsule(THREAD_ID)
    jobs = [
        job
        for job in store.list_jobs(thread_id=THREAD_ID, limit=200)
        if int(job.get("generation") or 0) == 63
    ]
    job = jobs[0] if jobs else {}
    runs = store.list_model_runs(job_id=job["id"], limit=200) if job.get("id") else []
    payload = {
        "study": "orchid-endurance-degradation-51-200",
        "first_semantic_failure": FIRST_SEMANTIC_FAILURE,
        "first_selector_protocol_failure": FIRST_SELECTOR_PROTOCOL_FAILURE,
        "frozen_db": str(frozen_db),
        "active_capsule": None
        if active is None
        else {
            "id": active["id"],
            "base_capsule_id": active.get("base_capsule_id"),
            "chars": len(active["content"]),
            "capsule_hash": active.get("capsule_hash"),
            "covered_end_event_id": active.get("covered_end_event_id"),
        },
        "failed_job": {
            "id": job.get("id"),
            "generation": job.get("generation"),
            "status": job.get("status"),
            "error": job.get("error"),
            "base_capsule_id": job.get("base_capsule_id"),
            "snapshot_start_event_id": job.get("snapshot_start_event_id"),
            "snapshot_end_event_id": job.get("snapshot_end_event_id"),
            "attempts": job.get("attempts"),
        },
        "model_runs": [
            {
                "id": run.get("id"),
                "stage": run.get("stage"),
                "status": run.get("status"),
                "selector_chunk_index": run.get("selector_chunk_index"),
                "input_hash": run.get("input_hash"),
                "output_hash": run.get("output_hash"),
                "raw_response_hash": run.get("raw_response_hash"),
                "diagnostic_excerpt": run.get("diagnostic_excerpt"),
            }
            for run in runs
        ],
        "note": (
            "Generation 63 is permanently recorded as the first selector protocol "
            "failure. Recovery may later promote this snapshot; this copy is not mutated."
        ),
    }
    write_json(freeze_dir / "gen63_first_selector_protocol_failure.json", payload)
    return payload


def write_milestone_artifact(
    *,
    path: Path,
    generation: int,
    store: SQLiteStore,
    active: dict[str, Any],
    taxonomy: dict[str, Any],
    metrics: dict[str, Any],
    checkpoint: LiveCheckpoint | None,
) -> None:
    payload = {
        "generation": generation,
        "first_semantic_failure": FIRST_SEMANTIC_FAILURE,
        "first_selector_protocol_failure": FIRST_SELECTOR_PROTOCOL_FAILURE,
        "capsule": {
            "id": active["id"],
            "base_capsule_id": active.get("base_capsule_id"),
            "content": active["content"],
            "chars": len(active["content"]),
            "source_event_hash": active.get("source_event_hash"),
            "input_hash": active.get("input_hash"),
            "output_hash": active.get("output_hash"),
            "capsule_hash": active.get("capsule_hash"),
            "covered_start_event_id": active.get("covered_start_event_id"),
            "covered_end_event_id": active.get("covered_end_event_id"),
        },
        "lineage_depth": _lineage_depth(store, active["id"]),
        "history": history_and_backlog_stats(store, active),
        "taxonomy": taxonomy,
        "metrics": metrics,
    }
    if checkpoint is not None:
        payload["checkpoint"] = {
            "passed": checkpoint.passed,
            "probes": [
                {
                    "layer": probe.layer,
                    "name": probe.name,
                    "passed": probe.passed,
                    "detail": probe.detail,
                }
                for probe in checkpoint.probes
            ],
            "recall_answers": checkpoint.recall_answers,
        }
    write_json(path, payload)


def _raise_if_fatal(
    generation: int,
    probes: list[LayerProbe],
    *,
    continue_on_semantic: bool = False,
) -> None:
    error = hard_stop_from_probes(
        generation, probes, continue_on_semantic=continue_on_semantic
    )
    if error is not None:
        raise error


def seed_historical_selector_records(
    store: SQLiteStore,
    recorder: RecordingSelector,
) -> None:
    """Restore prior snapshot/selection rows after a process restart.

    Selected IDs are reconstructed as FACT-bearing events in each promoted
    capsule's covered range. Live generations after start still record Qwen.
    """

    if recorder.records:
        return
    events = store.list_events(THREAD_ID)
    active = store.get_active_capsule(THREAD_ID)
    chain: list[dict[str, Any]] = []
    current = active
    seen: set[str] = set()
    while current and current["id"] not in seen:
        seen.add(current["id"])
        chain.append(current)
        base_id = current.get("base_capsule_id")
        if not base_id:
            break
        with store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM capsules WHERE id = ?",
                (base_id,),
            ).fetchone()
        current = dict(row) if row else None
    chain.reverse()
    for generation, capsule in enumerate(chain, start=1):
        start_event = (
            store.get_event(capsule["covered_start_event_id"])
            if capsule.get("covered_start_event_id")
            else None
        )
        end_event = (
            store.get_event(capsule["covered_end_event_id"])
            if capsule.get("covered_end_event_id")
            else None
        )
        if start_event is None or end_event is None:
            continue
        snapshot = [
            event
            for event in events
            if start_event["sequence"] <= event["sequence"] <= end_event["sequence"]
        ]
        selected = tuple(
            event["id"] for event in snapshot if parse_facts(event["content"])
        )
        recorder.records.append(
            SelectionRecord(
                generation=generation,
                snapshot_event_ids=tuple(event["id"] for event in snapshot),
                selected_event_ids=selected,
                event_facts={
                    event["id"]: parse_facts(event["content"]) for event in snapshot
                },
            )
        )


def requeue_latest_failed_transport_job(store: SQLiteStore) -> int:
    jobs = store.list_jobs(thread_id=THREAD_ID, limit=50)
    failed = next(
        (
            job
            for job in jobs
            if job["status"] == "FAILED"
            and is_retryable_transport_error(job.get("error") or "")
        ),
        None,
    )
    if failed is None:
        raise RuntimeError("no failed transport compaction job to requeue")
    with store.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE compaction_jobs
            SET status = 'QUEUED', error = NULL, finished_at = NULL,
                worker_id = NULL, lease_token = NULL, lease_until = NULL
            WHERE id = ? AND status = 'FAILED'
            """,
            (failed["id"],),
        )
    return int(failed["generation"])


def count_failed_selector_runs(store: SQLiteStore, generation: int) -> int:
    jobs = [
        job
        for job in store.list_jobs(thread_id=THREAD_ID, limit=200)
        if int(job.get("generation") or 0) == generation
    ]
    count = 0
    for job in jobs:
        if not job.get("id"):
            continue
        for run in store.list_model_runs(job_id=job["id"], limit=200):
            if run.get("stage") == "selector" and run.get("status") == "FAILED":
                count += 1
    return count


def requeue_latest_failed_protocol_job(store: SQLiteStore) -> int:
    jobs = store.list_jobs(thread_id=THREAD_ID, limit=50)
    failed = next(
        (
            job
            for job in jobs
            if job["status"] == "FAILED"
            and is_retryable_protocol_error(job.get("error") or "")
        ),
        None,
    )
    if failed is None:
        raise RuntimeError("no failed selector-protocol compaction job to requeue")
    with store.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE compaction_jobs
            SET status = 'QUEUED', error = NULL, finished_at = NULL,
                worker_id = NULL, lease_token = NULL, lease_until = NULL
            WHERE id = ? AND status = 'FAILED'
            """,
            (failed["id"],),
        )
    return int(failed["generation"])


async def run_live_endurance(
    store: SQLiteStore,
    engine: LosslessCompactionEngine,
    recall: RecallClient,
    *,
    stages: tuple[int, ...] = STAGES,
    checkpoints: tuple[int, ...] = CHECKPOINT_GENERATIONS,
    worker_id: str = "live-endurance-worker",
    start_generation: int = 1,
    resume_failed_transport: bool = False,
    resume_failed_protocol: bool = False,
    continue_on_semantic: bool = False,
    metrics_path: Path | None = None,
    artifact_dir: Path | None = None,
) -> LiveReport:
    requeued_generation: int | None = None
    if resume_failed_protocol:
        requeued_generation = requeue_latest_failed_protocol_job(store)
        start_generation = requeued_generation
    elif resume_failed_transport:
        requeued_generation = requeue_latest_failed_transport_job(store)
        start_generation = requeued_generation
    if start_generation <= 1:
        store.create_project(PROJECT_ID)
        store.create_thread(THREAD_ID, PROJECT_ID)
    recorder = engine.selector
    if not isinstance(recorder, RecordingSelector):
        recorder = RecordingSelector(engine.selector)
        engine.selector = recorder
    if start_generation > 1:
        seed_historical_selector_records(store, recorder)
    worker = CompactionWorker(store, engine, worker_id)
    assembler = ContextAssembler(
        store,
        raw_tail_target_tokens=80,
        minimum_raw_tail_tokens=1,
    )
    max_generation = max(stages)
    checkpoint_reports: list[LiveCheckpoint] = []
    latencies: list[float] = []
    growth: list[tuple[int, int]] = []
    residue_series: list[tuple[int, int]] = []
    active = store.get_active_capsule(THREAD_ID) if start_generation > 1 else None
    previous_active: str | None = active["id"] if active else None
    if active is not None:
        growth.append((start_generation - 1, len(active["content"])))
    stopped: LiveEnduranceStop | None = None
    completed_stages: list[int] = []
    generations_run = start_generation - 1
    previous_taxonomy: dict[str, Any] | None = None
    first_seen: dict[str, int] = {"resurrection": 50} if continue_on_semantic else {}

    try:
        for generation in range(start_generation, max_generation + 1):
            if requeued_generation != generation:
                for content in script_for_generation(generation):
                    _append(store, content)
                queue_snapshot_job(store, THREAD_ID)
            recorder.set_generation(generation)
            started = time.perf_counter()
            result = await worker.run_once()
            elapsed_ms = (time.perf_counter() - started) * 1000
            if result != "PROMOTED":
                job = store.list_jobs(thread_id=THREAD_ID, limit=1)[0]
                error = job.get("error") or ""
                if is_retryable_transport_error(error):
                    raise LiveEnduranceStop(
                        generation,
                        "provider",
                        "PROVIDER ABORT after "
                        f"{TRANSPORT_RETRY_MAX_ATTEMPTS} transport attempts: {error}",
                    )
                if is_retryable_protocol_error(error):
                    raise LiveEnduranceStop(
                        generation,
                        "protocol",
                        "PROTOCOL ABORT after "
                        f"{PROTOCOL_RETRY_MAX_ATTEMPTS} selector protocol attempts: {error}",
                    )
                raise LiveEnduranceStop(
                    generation,
                    "provenance",
                    f"compaction returned {result}: {error}",
                )
            latencies.append(elapsed_ms)
            active = store.get_active_capsule(THREAD_ID)
            if active is None:
                raise LiveEnduranceStop(generation, "provenance", "no active capsule")
            if previous_active is not None and active["base_capsule_id"] != previous_active:
                raise LiveEnduranceStop(
                    generation,
                    "provenance",
                    "descendant base_capsule_id mismatch",
                )
            previous_active = active["id"]
            growth.append((generation, len(active["content"])))
            events = store.list_events(THREAD_ID)
            expected = replay_history(event["content"] for event in events)
            residue = residue_hits(active["content"], expected)
            residue_series.append((generation, len(residue)))
            selector_probes = score_selector_layer(expected, recorder.records, events)
            capsule_probes = score_capsule_layer(expected, active["content"])
            lineage_ok = _lineage_depth(store, active["id"]) == generation
            provenance_probes = [
                LayerProbe(
                    layer="provenance",
                    name="lineage_depth",
                    passed=lineage_ok,
                    fatal=True,
                    detail=f"depth={_lineage_depth(store, active['id'])} expected={generation}",
                )
            ]
            checkpoint: LiveCheckpoint | None = None
            answers: dict[str, Any] | None = None
            recall_probes: list[LayerProbe] | None = None
            if generation in checkpoints:
                context = assembler.assemble(
                    THREAD_ID,
                    [{"role": "user", "content": "capsule-only recall probe"}],
                )
                tail_facts = sum(
                    1
                    for event in events
                    if event["id"] in context.raw_tail_event_ids
                    and parse_facts(event["content"])
                )
                provenance_probes.append(
                    LayerProbe(
                        layer="provenance",
                        name="source_facts_left_tail",
                        passed=tail_facts == 0,
                        fatal=True,
                        detail=f"fact_events_in_tail={tail_facts}",
                    )
                )
                recall_messages = [
                    message
                    for message in context.messages
                    if message.get("role") != "user"
                ] + [
                    {
                        "role": "user",
                        "content": RECALL_INSTRUCTION,
                    }
                ]
                try:
                    answers = await recall.complete(recall_messages)
                    recall_probes = score_recall_layer(expected, answers)
                except LiveEnduranceStop as error:
                    if error.layer == "provider":
                        raise
                    answers = None
                    recall_probes = [
                        LayerProbe(
                            layer="recall",
                            name="awake_recall",
                            passed=False,
                            fatal=True,
                            detail=str(error),
                        )
                    ]
                except Exception as error:
                    answers = None
                    recall_probes = [
                        LayerProbe(
                            layer="recall",
                            name="awake_recall",
                            passed=False,
                            fatal=True,
                            detail=f"provider/schema failure: {error}",
                        )
                    ]
                checkpoint = LiveCheckpoint(
                    generation=generation,
                    capsule_id=active["id"],
                    capsule_chars=len(active["content"]),
                    capsule_tokens=estimate_tokens(active["content"]),
                    compaction_ms=elapsed_ms,
                    lineage_depth=_lineage_depth(store, active["id"]),
                    residue_hits=residue,
                    residue_chars=len(active["content"]),
                    probes=provenance_probes + selector_probes + capsule_probes + (recall_probes or []),
                    selector_misses=[
                        probe.detail
                        for probe in selector_probes
                        if not probe.passed
                    ],
                    capsule_losses=[
                        probe.detail
                        for probe in capsule_probes
                        if probe.fatal and not probe.passed
                    ],
                    recall_losses=[
                        probe.detail
                        for probe in (recall_probes or [])
                        if not probe.passed
                    ],
                    recall_answers=answers,
                )
                checkpoint_reports.append(checkpoint)
                failed = [
                    f"{probe.layer}:{probe.name}"
                    for probe in checkpoint.probes
                    if not probe.passed
                ]
                print(
                    f"checkpoint gen {generation} "
                    f"{'PASS' if checkpoint.passed else 'FAIL'} "
                    f"chars={checkpoint.capsule_chars} "
                    f"fails={','.join(failed) or 'none'}",
                    flush=True,
                )
            taxonomy = taxonomy_from_probes(
                selector_probes,
                capsule_probes,
                residue,
                recall_probes,
            )
            healed = healed_items(previous_taxonomy, taxonomy)
            for class_name, items in taxonomy.items():
                if class_name.endswith("_count") or class_name == "residue_all":
                    continue
                if items and class_name not in first_seen:
                    first_seen[class_name] = generation
            selected = next(
                (
                    record.selected_event_ids
                    for record in reversed(recorder.records)
                    if record.generation == generation
                ),
                (),
            )
            stats = history_and_backlog_stats(store, active)
            model_stats = job_and_model_stats(store, generation)
            model_stats["selected_reference_count"] = len(selected)
            metrics = {
                "generation": generation,
                "active_capsule_id": active["id"],
                "base_capsule_id": active.get("base_capsule_id"),
                "compaction_latency_ms": elapsed_ms,
                "first_semantic_failure_generation": 50 if continue_on_semantic else None,
                **stats,
                **model_stats,
                **{
                    key: taxonomy[key]
                    for key in (
                        "residue_count",
                        "resurrection_count",
                        "current_fact_loss_count",
                        "invented_state_count",
                        "conditional_corruption_count",
                        "rejected_option_corruption_count",
                        "selector_loss_count",
                        "awake_recall_failure_count",
                        "semantic_error_count",
                    )
                },
                "residue": taxonomy["residue"],
                "resurrection": taxonomy["resurrection"],
                "current_fact_loss": taxonomy["current_fact_loss"],
                "healed": healed,
                "first_seen": dict(first_seen),
                "solar_checkpoint": None
                if checkpoint is None
                else {
                    "passed": checkpoint.passed,
                    "fails": [
                        f"{probe.layer}:{probe.name}"
                        for probe in checkpoint.probes
                        if not probe.passed
                    ],
                },
            }
            if metrics_path is not None:
                append_jsonl(metrics_path, metrics)
            if artifact_dir is not None and generation in (
                DEGRADATION_CHECKPOINTS if continue_on_semantic else checkpoints
            ):
                write_milestone_artifact(
                    path=artifact_dir / f"gen{generation}_milestone.json",
                    generation=generation,
                    store=store,
                    active=active,
                    taxonomy=taxonomy,
                    metrics=metrics,
                    checkpoint=checkpoint,
                )
            previous_taxonomy = taxonomy
            _pad_tail(store, generation)
            generations_run = generation
            print(
                f"generation {generation} PROMOTED "
                f"chars={len(active['content'])} residue={taxonomy['residue_count']} "
                f"resurrection={taxonomy['resurrection_count']} "
                f"lost={taxonomy['current_fact_loss_count']} "
                f"ms={elapsed_ms:.0f}",
                flush=True,
            )
            if continue_on_semantic:
                _raise_if_fatal(
                    generation,
                    provenance_probes
                    + selector_probes
                    + capsule_probes
                    + (recall_probes or []),
                    continue_on_semantic=True,
                )
            elif checkpoint is not None:
                _raise_if_fatal(generation, checkpoint.probes)

            if generation in stages:
                completed_stages.append(generation)
    except LiveEnduranceStop as error:
        stopped = error
        print(
            f"STOPPED gen {error.generation} [{error.layer}] {error.reason}",
            flush=True,
        )

    return LiveReport(
        stages_completed=completed_stages,
        stopped=stopped,
        checkpoints=checkpoint_reports,
        latencies_ms=latencies,
        capsule_growth=growth,
        residue_by_generation=residue_series,
        generations_run=generations_run,
    )


def format_live_report(report: LiveReport) -> str:
    lines = [
        f"ORCHID live endurance generations_run={report.generations_run} "
        f"stages={report.stages_completed} passed={report.passed}",
    ]
    if report.latencies_ms:
        ordered = sorted(report.latencies_ms)
        lines.append(
            "compaction latency ms: "
            f"min={min(ordered):.1f} median={ordered[len(ordered)//2]:.1f} "
            f"max={max(ordered):.1f}"
        )
    if report.capsule_growth:
        lines.append(
            "capsule growth: "
            + ", ".join(f"{generation}:{chars}" for generation, chars in report.capsule_growth)
        )
    if report.residue_by_generation:
        lines.append(
            "residue hits: "
            + ", ".join(
                f"{generation}:{count}" for generation, count in report.residue_by_generation
            )
        )
    for checkpoint in report.checkpoints:
        status = "PASS" if checkpoint.passed else "FAIL"
        lines.append(
            f"checkpoint gen {checkpoint.generation} {status} "
            f"chars={checkpoint.capsule_chars} residue={len(checkpoint.residue_hits)} "
            f"compact_ms={checkpoint.compaction_ms:.1f}"
        )
        for probe in checkpoint.probes:
            mark = "ok" if probe.passed else "FAIL"
            lines.append(f"  [{probe.layer}] {mark} {probe.name}: {probe.detail}")
    if report.stopped:
        lines.append(
            f"STOPPED gen {report.stopped.generation} [{report.stopped.layer}] "
            f"{report.stopped.reason}"
        )
    return "\n".join(lines)


def capsule_only_recall_messages(
    store: SQLiteStore,
    instruction: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    assembler = ContextAssembler(
        store,
        raw_tail_target_tokens=80,
        minimum_raw_tail_tokens=1,
    )
    active = store.get_active_capsule(THREAD_ID)
    if active is None:
        raise SystemExit("no active capsule to validate")
    context = assembler.assemble(
        THREAD_ID,
        [{"role": "user", "content": "capsule-only recall probe"}],
    )
    messages = [
        message
        for message in context.messages
        if message.get("role") != "user"
    ] + [{"role": "user", "content": instruction}]
    return active, messages


def live_engine(
    config: RuntimeConfig,
    store: SQLiteStore,
    *,
    protocol_log: Path | None = None,
    protocol_max_attempts: int = PROTOCOL_RETRY_MAX_ATTEMPTS,
) -> LosslessCompactionEngine:
    engine = build_lossless_engine(config, telemetry_recorder=store)
    if engine is None:
        raise SystemExit("compaction pipeline is not configured")
    for stage in (engine.selector, engine.canonicalizer, engine.consolidator):
        client = getattr(stage, "client", None)
        if client is not None:
            stage.client = TransportRetryingStructuredClient(client)
    engine.selector = ProtocolRetryingSelector(
        engine.selector,
        max_attempts=protocol_max_attempts,
        log_path=protocol_log,
        stage="selector",
    )
    engine.canonicalizer = ProtocolRetryingSelector(
        engine.canonicalizer,
        max_attempts=protocol_max_attempts,
        log_path=protocol_log,
        stage="canonicalizer",
    )
    engine.selector = RecordingSelector(engine.selector)
    return engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Staged live ORCHID endurance run")
    parser.add_argument("--db", default="./data/live_endurance.db")
    parser.add_argument("--stages", default="10,25,50")
    parser.add_argument("--start-generation", type=int, default=None)
    parser.add_argument(
        "--degradation",
        action="store_true",
        help="Continue past semantic failures through --stages as a post-failure study",
    )
    parser.add_argument(
        "--metrics",
        default="",
        help="JSONL path for per-generation degradation metrics",
    )
    parser.add_argument(
        "--artifact-dir",
        default="",
        help="Directory for milestone JSON artifacts",
    )
    parser.add_argument(
        "--freeze-gen50",
        default="",
        help="Copy the current DB into this directory as the immutable gen-50 failure freeze",
    )
    parser.add_argument(
        "--freeze-gen63",
        default="",
        help="Copy the current DB as the immutable gen-63 selector protocol failure freeze",
    )
    parser.add_argument(
        "--resume-failed-transport",
        action="store_true",
        help="Requeue the latest 429/500/503/504 FAILED job without appending new events",
    )
    parser.add_argument(
        "--resume-failed-protocol",
        action="store_true",
        help="Requeue the latest selector-protocol FAILED job without appending new events",
    )
    parser.add_argument(
        "--frozen-qid-validate",
        action="store_true",
        help="Score one q1-q12 Solar recall against the existing DB capsule; do not compact",
    )
    parser.add_argument(
        "--absent-identity-probe",
        action="store_true",
        help="Ask several currently absent facts against the existing capsule; do not compact",
    )
    parser.add_argument(
        "--artifact",
        default="",
        help="Optional JSON path for --frozen-qid-validate or --absent-identity-probe",
    )
    args = parser.parse_args(argv)
    if args.freeze_gen50:
        payload = freeze_gen50_first_failure(
            source_db=Path(args.db),
            freeze_dir=Path(args.freeze_gen50),
        )
        print(json.dumps({"frozen": payload["frozen_db"], "capsule_id": payload["capsule"]["id"]}, indent=2))
        if not args.degradation and not args.resume_failed_transport and not args.resume_failed_protocol:
            return 0
    if args.freeze_gen63:
        payload = freeze_gen63_protocol_failure(
            source_db=Path(args.db),
            freeze_dir=Path(args.freeze_gen63),
        )
        print(json.dumps({"frozen": payload["frozen_db"], "job": payload["failed_job"]["id"]}, indent=2))
        if not args.degradation and not args.resume_failed_protocol:
            return 0
    config = RuntimeConfig.from_env(db_path=args.db)
    if not _live_configured(config):
        print(
            "live endurance requires selector, canonicalizer, consolidator, "
            "and backend (Solar) configuration"
        )
        return 2
    store = SQLiteStore(config.db_path)
    if args.absent_identity_probe:
        recall = SolarRecallClient(config)
        active, messages = capsule_only_recall_messages(
            store, ABSENT_IDENTITY_INSTRUCTION
        )

        async def _probe() -> dict[str, Any]:
            return await recall.complete(
                messages,
                response_format=ABSENT_IDENTITY_RESPONSE_FORMAT,
            )

        answers = asyncio.run(_probe())
        classification = classify_absent_identity_probe(answers)
        payload = {
            "probe_version": ABSENT_IDENTITY_PROBE_VERSION,
            "capsule_id": active["id"],
            "capsule_chars": len(active["content"]),
            "questions": [
                {"id": qid, "question": question}
                for qid, question in ABSENT_IDENTITY_PROBE_QUESTIONS
            ],
            "answers": answers,
            **classification,
        }
        if args.artifact:
            Path(args.artifact).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if classification["verdict"] != "general_hallucination" else 1
    if args.frozen_qid_validate:
        recall = SolarRecallClient(config)
        active, messages = capsule_only_recall_messages(store, RECALL_INSTRUCTION)
        events = store.list_events(THREAD_ID)
        expected = replay_history(event["content"] for event in events)

        async def _once() -> dict[str, Any]:
            return await recall.complete(messages)

        answers = asyncio.run(_once())
        probes = score_recall_layer(expected, answers)
        payload = {
            "prompt_version": RECALL_PROMPT_VERSION,
            "schema_version": RECALL_SCHEMA_VERSION,
            "capsule_id": active["id"],
            "answers": answers,
            "passed": all(probe.passed for probe in probes),
            "probes": [
                {"name": probe.name, "passed": probe.passed, "detail": probe.detail}
                for probe in probes
            ],
        }
        if args.artifact:
            Path(args.artifact).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["passed"] else 1
    if args.degradation:
        stages = (
            tuple(int(part) for part in args.stages.split(",") if part)
            if args.stages != "10,25,50"
            else (200,)
        )
        start_generation = args.start_generation or 51
        checkpoints = DEGRADATION_CHECKPOINTS
        continue_on_semantic = True
    else:
        stages = tuple(int(part) for part in args.stages.split(",") if part)
        start_generation = args.start_generation or 1
        checkpoints = CHECKPOINT_GENERATIONS
        continue_on_semantic = False
    engine = live_engine(
        config,
        store,
        protocol_log=Path(args.artifact_dir) / "protocol.jsonl" if args.artifact_dir else None,
    )
    report = asyncio.run(
        run_live_endurance(
            store,
            engine,
            SolarRecallClient(config),
            stages=stages,
            checkpoints=checkpoints,
            start_generation=start_generation,
            resume_failed_transport=args.resume_failed_transport,
            resume_failed_protocol=args.resume_failed_protocol,
            continue_on_semantic=continue_on_semantic,
            metrics_path=Path(args.metrics) if args.metrics else None,
            artifact_dir=Path(args.artifact_dir) if args.artifact_dir else None,
        )
    )
    print(format_live_report(report))
    if args.degradation:
        return 0 if report.stopped is None else 1
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
