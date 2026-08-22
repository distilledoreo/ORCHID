from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Protocol

from .context import estimate_tokens
from .db import SQLiteStore, canonical_json, content_hash


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Event:
    id: str
    sequence: int
    content: str
    content_hash: str
    event_type: str
    role: str | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Event":
        return cls(
            id=row["id"],
            sequence=int(row["sequence"]),
            content=row["content"],
            content_hash=row["content_hash"],
            event_type=row["event_type"],
            role=row["role"],
        )


@dataclass(frozen=True)
class SourceSpan:
    """A deterministic, verbatim slice of an oversized source event."""

    id: str
    parent_event_id: str
    sequence: int
    event_type: str
    role: str | None
    content: str
    content_hash: str
    parent_content_hash: str
    span_index: int
    source_start: int
    source_end: int


SourceItem = Event | SourceSpan


def source_item_payload(item: SourceItem) -> dict[str, Any]:
    """Serialize a whole event or span without changing its source content."""

    if isinstance(item, Event):
        return {
            "id": item.id,
            "sequence": item.sequence,
            "event_type": item.event_type,
            "role": item.role,
            "content": item.content,
            "content_hash": item.content_hash,
        }
    return {
        "id": item.id,
        "sequence": item.sequence,
        "event_type": item.event_type,
        "role": item.role,
        "content": item.content,
        "content_hash": item.content_hash,
        "source_kind": "span",
        "parent_event_id": item.parent_event_id,
        "parent_content_hash": item.parent_content_hash,
        "parent_sequence": item.sequence,
        "parent_event_type": item.event_type,
        "parent_role": item.role,
        "span_index": item.span_index,
        "source_start": item.source_start,
        "source_end": item.source_end,
    }


def source_item_parent_event_id(item: SourceItem) -> str:
    return item.id if isinstance(item, Event) else item.parent_event_id


def split_event_into_spans(
    event: Event,
    *,
    selector_budget_tokens: int = 1_200,
    safety_margin: float = 0.8,
) -> tuple[SourceSpan, ...]:
    """Split an event into deterministic, budgeted, character-range spans."""

    if selector_budget_tokens <= 0:
        raise ValueError("selector_budget_tokens must be positive")
    if not 0 < safety_margin <= 1:
        raise ValueError("safety_margin must be between 0 and 1")
    span_budget_tokens = max(1, int(selector_budget_tokens * safety_margin))
    if not event.content:
        return (
            SourceSpan(
                id=f"{event.id}::span::000000",
                parent_event_id=event.id,
                sequence=event.sequence,
                event_type=event.event_type,
                role=event.role,
                content="",
                content_hash=content_hash(""),
                parent_content_hash=event.content_hash,
                span_index=0,
                source_start=0,
                source_end=0,
            ),
        )

    spans: list[SourceSpan] = []
    start = 0
    span_index = 0
    content_length = len(event.content)
    while start < content_length:
        def candidate(end: int) -> SourceSpan:
            content = event.content[start:end]
            return SourceSpan(
                id=f"{event.id}::span::{span_index:06d}",
                parent_event_id=event.id,
                sequence=event.sequence,
                event_type=event.event_type,
                role=event.role,
                content=content,
                content_hash=content_hash(content),
                parent_content_hash=event.content_hash,
                span_index=span_index,
                source_start=start,
                source_end=end,
            )

        first = candidate(start + 1)
        if estimate_tokens(
            json.dumps(source_item_payload(first), ensure_ascii=False)
        ) > span_budget_tokens:
            raise ValueError("selector budget is too small for span provenance")

        low = start + 1
        high = content_length
        best = start + 1
        while low <= high:
            middle = (low + high) // 2
            if estimate_tokens(
                json.dumps(source_item_payload(candidate(middle)), ensure_ascii=False)
            ) <= span_budget_tokens:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        spans.append(candidate(best))
        start = best
        span_index += 1
    return tuple(spans)


def expand_source_items(
    events: Iterable[Event],
    *,
    selector_budget_tokens: int = 1_200,
    safety_margin: float = 0.8,
) -> tuple[SourceItem, ...]:
    """Keep normal events whole and expand only oversized events into spans."""

    items: list[SourceItem] = []
    for event in events:
        if estimate_tokens(
            json.dumps(source_item_payload(event), ensure_ascii=False)
        ) <= selector_budget_tokens:
            items.append(event)
        else:
            items.extend(
                split_event_into_spans(
                    event,
                    selector_budget_tokens=selector_budget_tokens,
                    safety_margin=safety_margin,
                )
            )
    return tuple(items)


def reconstruct_source_content(spans: Iterable[SourceSpan]) -> str:
    """Rebuild and verify the exact parent content from its spans."""

    ordered = sorted(spans, key=lambda span: span.span_index)
    if not ordered:
        raise ValueError("at least one source span is required")
    parent_id = ordered[0].parent_event_id
    parent_hash = ordered[0].parent_content_hash
    expected_start = 0
    content_parts: list[str] = []
    seen_indices: set[int] = set()
    for span in ordered:
        if span.parent_event_id != parent_id or span.parent_content_hash != parent_hash:
            raise ValueError("source spans do not share one parent event")
        if span.span_index in seen_indices:
            raise ValueError("source spans contain duplicate indices")
        seen_indices.add(span.span_index)
        if span.source_start != expected_start:
            raise ValueError("source spans have a gap or overlap")
        if span.source_end != span.source_start + len(span.content):
            raise ValueError("source span range does not match content length")
        if content_hash(span.content) != span.content_hash:
            raise ValueError("source span content hash does not match content")
        content_parts.append(span.content)
        expected_start = span.source_end
    content = "".join(content_parts)
    if content_hash(content) != parent_hash:
        raise ValueError("reconstructed content does not match parent content hash")
    return content


@dataclass(frozen=True)
class Capsule:
    id: str
    thread_id: str
    content: str
    capsule_hash: str
    covered_end_event_id: str | None

    @classmethod
    def from_row(cls, row: dict[str, Any] | None) -> "Capsule | None":
        if row is None:
            return None
        return cls(
            id=row["id"],
            thread_id=row["thread_id"],
            content=row["content"],
            capsule_hash=row["capsule_hash"],
            covered_end_event_id=row["covered_end_event_id"],
        )


@dataclass(frozen=True)
class CompactionSnapshot:
    thread_id: str
    base_capsule_id: str | None
    snapshot_start_event_id: str | None
    snapshot_end_event_id: str | None
    events: tuple[dict[str, Any], ...]
    base_capsule_content: str | None
    source_event_hash: str
    input_hash: str


@dataclass(frozen=True)
class CompactionResult:
    """Immutable model output. It has no database capabilities."""

    content: str
    covered_event_ids: tuple[str, ...]
    evidence_event_ids: tuple[str, ...]
    input_hash: str
    output_hash: str
    model_identity: str
    prompt_version: str
    generation_settings: dict[str, Any]
    evidence_source_ids: tuple[str, ...] = ()
    retire_memories: tuple["RetireMemory", ...] = ()


@dataclass(frozen=True)
class RetireMemory:
    """A semantic cold-memory object with event provenance."""

    content: str
    memory_type: str
    importance: float
    evidence_event_ids: tuple[str, ...]


class CompactionEngine(Protocol):
    async def compact(
        self,
        *,
        base_capsule: Capsule | None,
        events: list[Event],
        snapshot_end_event_id: str | None,
    ) -> CompactionResult:
        """Return candidate material without mutating storage."""


def compute_source_event_hash(
    base_capsule: Capsule | None,
    events: list[Event] | tuple[Event, ...],
) -> str:
    return _hash(
        {
            "base_capsule_hash": base_capsule.capsule_hash if base_capsule else None,
            "events": [
                {
                    "id": event.id,
                    "sequence": event.sequence,
                    "content_hash": event.content_hash,
                }
                for event in events
            ],
        }
    )


def compute_input_hash(
    base_capsule: Capsule | None,
    events: list[Event] | tuple[Event, ...],
    snapshot_end_event_id: str | None,
) -> str:
    source_hash = compute_source_event_hash(base_capsule, events)
    return _hash(
        {
            "base_capsule_id": base_capsule.id if base_capsule else None,
            "base_capsule_hash": base_capsule.capsule_hash if base_capsule else None,
            "snapshot_end_event_id": snapshot_end_event_id,
            "source_event_hash": source_hash,
        }
    )


def freeze_snapshot(
    store: SQLiteStore,
    thread_id: str,
    *,
    expected_base_capsule_id: str | None = None,
    snapshot_end_event_id: str | None = None,
) -> CompactionSnapshot:
    active_row = store.get_active_capsule(thread_id)
    active = Capsule.from_row(active_row)
    active_id = active.id if active else None
    if expected_base_capsule_id is not None and active_id != expected_base_capsule_id:
        raise RuntimeError("active capsule changed before snapshot freeze")

    events = store.list_events(thread_id)
    base_end_sequence = 0
    if active and active.covered_end_event_id:
        covered_end = store.get_event(active.covered_end_event_id)
        if covered_end:
            base_end_sequence = int(covered_end["sequence"])

    if snapshot_end_event_id is not None:
        end_event = store.get_event(snapshot_end_event_id)
        if end_event is None or end_event["thread_id"] != thread_id:
            raise RuntimeError("snapshot end event does not belong to thread")
        snapshot_end_sequence = int(end_event["sequence"])
    else:
        snapshot_end_sequence = events[-1]["sequence"] if events else base_end_sequence

    snapshot_events = tuple(
        event
        for event in events
        if base_end_sequence < event["sequence"] <= snapshot_end_sequence
    )
    start_id = snapshot_events[0]["id"] if snapshot_events else None
    end_id = (
        snapshot_events[-1]["id"]
        if snapshot_events
        else active.covered_end_event_id
        if active
        else None
    )
    event_objects = tuple(Event.from_row(event) for event in snapshot_events)
    source_event_hash = compute_source_event_hash(active, event_objects)
    input_hash = compute_input_hash(active, event_objects, end_id)
    return CompactionSnapshot(
        thread_id=thread_id,
        base_capsule_id=active_id,
        snapshot_start_event_id=start_id,
        snapshot_end_event_id=end_id,
        events=snapshot_events,
        base_capsule_content=active.content if active else None,
        source_event_hash=source_event_hash,
        input_hash=input_hash,
    )


def queue_snapshot_job(store: SQLiteStore, thread_id: str, priority: int = 0) -> str | None:
    snapshot = freeze_snapshot(store, thread_id)
    # Keep the low-level test/maintenance helper compatible with empty
    # snapshots. Production pressure scheduling already avoids calling this
    # path when there is no novel history, while lease/recovery callers may
    # still need a durable empty job to exercise ownership semantics.
    if snapshot.snapshot_start_event_id is None or snapshot.snapshot_end_event_id is None:
        return store.create_compaction_job(
            thread_id=thread_id,
            base_capsule_id=snapshot.base_capsule_id,
            snapshot_start_event_id=None,
            snapshot_end_event_id=None,
            priority=priority,
        )
    return store.request_coalesced_compaction_job(
        thread_id=thread_id,
        base_capsule_id=snapshot.base_capsule_id,
        snapshot_start_event_id=snapshot.snapshot_start_event_id,
        snapshot_end_event_id=snapshot.snapshot_end_event_id,
        priority=priority,
    )


def validate_compaction_result(
    snapshot: CompactionSnapshot,
    result: CompactionResult,
) -> tuple[str, dict[str, Any]]:
    if not result.content.strip():
        raise ValueError("candidate content is empty")
    expected_events = tuple(event["id"] for event in snapshot.events)
    if result.covered_event_ids != expected_events:
        raise ValueError("covered_event_ids do not exactly match the frozen snapshot")
    if len(set(result.evidence_event_ids)) != len(result.evidence_event_ids):
        raise ValueError("evidence_event_ids contain duplicates")
    if not set(result.evidence_event_ids).issubset(set(expected_events)):
        raise ValueError("evidence_event_ids contain unknown or out-of-snapshot events")
    if len(set(result.evidence_source_ids)) != len(result.evidence_source_ids):
        raise ValueError("evidence_source_ids contain duplicates")
    expected_event_set = set(expected_events)
    for source_id in result.evidence_source_ids:
        parent_id = (
            source_id.split("::span::", 1)[0]
            if "::span::" in source_id
            else source_id
        )
        if parent_id not in expected_event_set:
            raise ValueError("evidence_source_ids contain unknown or out-of-snapshot sources")
    if result.input_hash != snapshot.input_hash:
        raise ValueError("result input_hash does not match frozen snapshot")
    expected_output_hash = hashlib.sha256(result.content.encode("utf-8")).hexdigest()
    if result.output_hash != expected_output_hash:
        raise ValueError("result output_hash does not match content")
    if not result.model_identity.strip() or not result.prompt_version.strip():
        raise ValueError("model_identity and prompt_version are required")
    capsule_hash = _hash(
        {
            "input_hash": result.input_hash,
            "output_hash": result.output_hash,
            "covered_event_ids": result.covered_event_ids,
            "evidence_event_ids": result.evidence_event_ids,
            "model_identity": result.model_identity,
            "prompt_version": result.prompt_version,
            "generation_settings": result.generation_settings,
        }
    )
    metadata = {
        "model_identity": result.model_identity,
        "prompt_version": result.prompt_version,
        "generation_settings": result.generation_settings,
        "covered_event_ids": result.covered_event_ids,
        "evidence_event_ids": result.evidence_event_ids,
        "evidence_source_ids": result.evidence_source_ids,
        "retire_memory_count": len(getattr(result, "retire_memories", ())),
    }
    return capsule_hash, metadata


class LeaseLostError(RuntimeError):
    """The worker no longer owns the leased compaction job."""


class CompactionWorker:
    def __init__(
        self,
        store: SQLiteStore,
        engine: CompactionEngine,
        worker_id: str,
        *,
        lease_seconds: int = 900,
        renewal_interval_seconds: float = 30.0,
        recover_expired_jobs: bool = False,
        on_job_finished: Callable[[str], None] | None = None,
    ):
        self.store = store
        self.engine = engine
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.renewal_interval_seconds = renewal_interval_seconds
        self.recover_expired_jobs = recover_expired_jobs
        self.on_job_finished = on_job_finished

    def _assert_ownership(self, job: dict[str, Any]) -> None:
        lease_token = job.get("lease_token")
        if not lease_token or not self.store.owns_job(
            job["id"],
            self.worker_id,
            lease_token,
        ):
            raise LeaseLostError(f"lease ownership lost for job {job['id']}")

    async def _renew_lease(
        self,
        job: dict[str, Any],
        lease_lost: asyncio.Event,
    ) -> None:
        while True:
            await asyncio.sleep(self.renewal_interval_seconds)
            try:
                renewed = self.store.renew_job_lease(
                    job["id"],
                    self.worker_id,
                    job["lease_token"],
                    self.lease_seconds,
                )
            except Exception:
                renewed = False
            if not renewed:
                lease_lost.set()
                return

    async def _run_owned_compaction(
        self,
        job: dict[str, Any],
        *,
        base_capsule: Capsule | None,
        events: list[Event],
        snapshot_end_event_id: str | None,
        lease_lost: asyncio.Event,
    ) -> CompactionResult:
        engine_task = asyncio.create_task(
            self.engine.compact(
                base_capsule=base_capsule,
                events=events,
                snapshot_end_event_id=snapshot_end_event_id,
            )
        )
        lost_task = asyncio.create_task(lease_lost.wait())
        try:
            done, _ = await asyncio.wait(
                {engine_task, lost_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if lost_task in done or lease_lost.is_set():
                engine_task.cancel()
                with suppress(asyncio.CancelledError):
                    await engine_task
                raise LeaseLostError(f"lease ownership lost for job {job['id']}")
            return engine_task.result()
        finally:
            lost_task.cancel()
            with suppress(asyncio.CancelledError):
                await lost_task

    async def run_once(self) -> str | None:
        job = self.store.claim_next_job(
            self.worker_id,
            self.lease_seconds,
            recover_expired=self.recover_expired_jobs,
        )
        if job is None:
            return None
        candidate_id: str | None = None
        lease_lost = asyncio.Event()
        renewal_task = asyncio.create_task(self._renew_lease(job, lease_lost))
        set_job_context = getattr(self.engine, "set_job_context", None)
        set_ownership_checker = getattr(self.engine, "set_ownership_checker", None)
        if set_job_context is not None:
            set_job_context(
                job_id=job["id"],
                thread_id=job["thread_id"],
                generation=job["generation"],
            )
        if set_ownership_checker is not None:
            set_ownership_checker(lambda: self._assert_ownership(job))
        try:
            self._assert_ownership(job)
            active = self.store.get_active_capsule(job["thread_id"])
            active_id = active["id"] if active else None
            if active_id != job["base_capsule_id"]:
                if not self.store.finish_job(
                    job["id"],
                    "STALE",
                    "active capsule changed before worker start",
                    worker_id=self.worker_id,
                    lease_token=job["lease_token"],
                ):
                    return "LOST"
                return "STALE"
            try:
                snapshot = freeze_snapshot(
                    self.store,
                    job["thread_id"],
                    expected_base_capsule_id=job["base_capsule_id"],
                    snapshot_end_event_id=job["snapshot_end_event_id"],
                )
            except RuntimeError as error:
                if not self.store.finish_job(
                    job["id"],
                    "STALE",
                    str(error),
                    worker_id=self.worker_id,
                    lease_token=job["lease_token"],
                ):
                    return "LOST"
                return "STALE"

            base_capsule = Capsule.from_row(active)
            events = [Event.from_row(event) for event in snapshot.events]
            self._assert_ownership(job)
            result = await self._run_owned_compaction(
                job,
                base_capsule=base_capsule,
                events=events,
                snapshot_end_event_id=snapshot.snapshot_end_event_id,
                lease_lost=lease_lost,
            )
            self._assert_ownership(job)
            capsule_hash, model_metadata = validate_compaction_result(snapshot, result)
            self._assert_ownership(job)
            candidate_id = self.store.create_capsule(
                thread_id=snapshot.thread_id,
                base_capsule_id=snapshot.base_capsule_id,
                content=result.content,
                source_event_hash=snapshot.source_event_hash,
                input_hash=result.input_hash,
                output_hash=result.output_hash,
                capsule_hash=capsule_hash,
                snapshot_start_event_id=snapshot.snapshot_start_event_id,
                snapshot_end_event_id=snapshot.snapshot_end_event_id,
                covered_start_event_id=snapshot.snapshot_start_event_id,
                covered_end_event_id=snapshot.snapshot_end_event_id,
                model_metadata=model_metadata,
            )
            self._assert_ownership(job)
            if not self.store.mark_capsule_ready(candidate_id):
                raise RuntimeError("candidate could not enter READY state")
            self._assert_ownership(job)
            if self.store.promote_capsule_cas(
                snapshot.thread_id,
                candidate_id,
                job_id=job["id"],
                worker_id=self.worker_id,
                lease_token=job["lease_token"],
            ):
                # Cold persistence is deliberately after the hot CAS and
                # fail-open: a sidecar/index problem must not turn a valid
                # ACTIVE promotion into a failed compaction.
                try:
                    self.store.persist_long_term_memories(
                        thread_id=snapshot.thread_id,
                        memories=[
                            asdict(memory)
                            for memory in getattr(result, "retire_memories", ())
                            if isinstance(memory, RetireMemory)
                        ],
                    )
                except Exception:
                    pass
                if not self.store.finish_job(
                    job["id"],
                    "PROMOTED",
                    worker_id=self.worker_id,
                    lease_token=job["lease_token"],
                ):
                    return "LOST"
                return "PROMOTED"
            if not self.store.finish_job(
                job["id"],
                "STALE",
                "compare-and-swap or lease ownership lost",
                worker_id=self.worker_id,
                lease_token=job["lease_token"],
            ):
                return "LOST"
            return "STALE"
        except LeaseLostError:
            if candidate_id:
                self.store.mark_capsule_failed(candidate_id, "lease ownership lost")
            return "LOST"
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if candidate_id:
                self.store.mark_capsule_failed(candidate_id, str(error))
            if not self.store.finish_job(
                job["id"],
                "FAILED",
                str(error),
                worker_id=self.worker_id,
                lease_token=job["lease_token"],
            ):
                return "LOST"
            return "FAILED"
        finally:
            renewal_task.cancel()
            with suppress(asyncio.CancelledError):
                await renewal_task
            if set_ownership_checker is not None:
                set_ownership_checker(None)
            if set_job_context is not None:
                set_job_context(
                    job_id=None,
                    thread_id=None,
                    generation=None,
                )
            if self.on_job_finished is not None:
                try:
                    self.on_job_finished(job["thread_id"])
                except Exception:
                    # The dirty state is durable; a callback failure must not
                    # turn a completed lease into a gateway failure.
                    pass
