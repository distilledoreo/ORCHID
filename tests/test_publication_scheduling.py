from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from memory_gateway.compaction import CompactionResult, CompactionWorker, Event, compute_input_hash
from memory_gateway.db import SQLiteStore
from memory_gateway.ingestion import append_novel_messages
from memory_gateway.scheduler import CompactionScheduler, ThresholdPolicy


def _event(event_id: str, content: str, sequence: int = 1) -> Event:
    return Event(
        id=event_id,
        sequence=sequence,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        event_type="user_message",
        role="user",
    )


class _LowValueEngine:
    async def compact(self, *, base_capsule, events: list[Event], snapshot_end_event_id):
        content = "x" * 7_600
        covered = tuple(event.id for event in events)
        return CompactionResult(
            content=content,
            covered_event_ids=covered,
            evidence_event_ids=covered,
            input_hash=compute_input_hash(base_capsule, events, snapshot_end_event_id),
            output_hash=hashlib.sha256(content.encode()).hexdigest(),
            model_identity="test",
            prompt_version="v1",
            generation_settings={},
            evidence_source_ids=covered,
        )


class _HighValueEngine:
    async def compact(self, *, base_capsule, events: list[Event], snapshot_end_event_id):
        content = "summary"
        covered = tuple(event.id for event in events)
        return CompactionResult(
            content=content,
            covered_event_ids=covered,
            evidence_event_ids=covered,
            input_hash=compute_input_hash(base_capsule, events, snapshot_end_event_id),
            output_hash=hashlib.sha256(content.encode()).hexdigest(),
            model_identity="test",
            prompt_version="v1",
            generation_settings={},
            evidence_source_ids=covered,
        )


def test_absolute_threshold_policy_uses_foreground_limits() -> None:
    policy = ThresholdPolicy(
        usable_context_tokens=64_000,
        background_threshold_tokens=22_000,
        urgent_threshold_tokens=64_000,
    )
    assert policy.priority_for(21_999) is None
    assert policy.priority_for(22_000) == 50
    assert policy.priority_for(64_000) == 100


def test_scheduler_defers_low_value_publication(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[{"role": "user", "content": "a" * 8_000}],
        request_id="r1",
        source="request",
    )
    job_id = store.request_coalesced_compaction_job(
        thread_id="thread",
        base_capsule_id=None,
        snapshot_start_event_id=store.list_events("thread")[0]["id"],
        snapshot_end_event_id=store.list_events("thread")[0]["id"],
        priority=50,
    )
    assert job_id is not None
    result = asyncio.run(
        CompactionWorker(
            store,
            _LowValueEngine(),
            "defer-worker",
            publication_min_savings_ratio=1.25,
            publication_economics_enabled=True,
        ).run_once()
    )
    assert result == "DEFERRED"
    assert store.get_active_capsule("thread") is None
    ready = store.get_latest_ready_capsule("thread")
    assert ready is not None


def test_scheduler_promotes_high_value_candidate(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[{"role": "user", "content": "a" * 8_000}],
        request_id="r1",
        source="request",
    )
    job_id = store.request_coalesced_compaction_job(
        thread_id="thread",
        base_capsule_id=None,
        snapshot_start_event_id=store.list_events("thread")[0]["id"],
        snapshot_end_event_id=store.list_events("thread")[0]["id"],
        priority=50,
    )
    assert job_id is not None
    result = asyncio.run(
        CompactionWorker(
            store,
            _HighValueEngine(),
            "promote-worker",
            publication_min_savings_ratio=1.25,
            publication_economics_enabled=True,
        ).run_once()
    )
    assert result == "PROMOTED"
    assert store.get_active_capsule("thread") is not None


def test_try_promote_ready_candidate_promotes_when_metadata_passes(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="hello",
    )
    capsule_id = store.create_capsule(
        thread_id="thread",
        base_capsule_id=None,
        content="summary",
        source_event_hash="source",
        input_hash="input",
        output_hash="output",
        capsule_hash="capsule",
        snapshot_start_event_id=event["id"],
        snapshot_end_event_id=event["id"],
        covered_start_event_id=event["id"],
        covered_end_event_id=event["id"],
        model_metadata={
            "publication_economics": {
                "future_savings_tokens": 10_000,
                "reset_cost_tokens": 100,
                "min_savings_ratio": 1.25,
            }
        },
        source_event_ids=(event["id"],),
    )
    assert store.mark_capsule_ready(capsule_id)
    scheduler = CompactionScheduler(
        store,
        ThresholdPolicy(usable_context_tokens=64_000),
        publication_economics_enabled=True,
    )
    assert scheduler.try_promote_ready_candidate("thread")
    active = store.get_active_capsule("thread")
    assert active is not None
    assert active["id"] == capsule_id
