from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from memory_gateway.compaction import CompactionWorker, freeze_snapshot
from memory_gateway.context import estimate_tokens
from memory_gateway.db import SQLiteStore
from memory_gateway.fake_engines import PerfectCompactionEngine
from memory_gateway.scheduler import CompactionScheduler, ThresholdPolicy


def make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    return store


def active_capsule_through(store: SQLiteStore, event_id: str) -> str:
    content = "active capsule"
    digest = hashlib.sha256(content.encode()).hexdigest()
    capsule_id = store.create_capsule(
        thread_id="thread",
        base_capsule_id=None,
        content=content,
        source_event_hash="source",
        input_hash="input",
        output_hash=digest,
        capsule_hash=digest,
        snapshot_start_event_id=None,
        snapshot_end_event_id=event_id,
        covered_start_event_id=event_id,
        covered_end_event_id=event_id,
    )
    assert store.mark_capsule_ready(capsule_id)
    assert store.promote_capsule_cas("thread", capsule_id)
    return capsule_id


def test_uncompacted_tokens_include_all_events_without_capsule(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="first",
        token_count=3,
    )
    second_content = "second event without a stored token count"
    store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="assistant_message",
        role="assistant",
        content=second_content,
    )
    scheduler = CompactionScheduler(
        store,
        ThresholdPolicy(usable_context_tokens=10, background_fraction=0.5),
    )
    assert scheduler.uncompacted_tokens("thread") == 3 + estimate_tokens(second_content)


def test_bootstrap_threshold_starts_first_capsule_early_then_restores_steady_state(
    tmp_path: Path,
) -> None:
    _ = tmp_path
    policy = ThresholdPolicy(
        usable_context_tokens=100,
        background_threshold_tokens=22,
        bootstrap_threshold_tokens=8,
    )
    assert policy.priority_for(8, bootstrap=True) == 50
    assert policy.priority_for(8, bootstrap=False) is None
    assert policy.priority_for(22, bootstrap=False) == 50


def test_projected_tokens_can_raise_priority_before_current_threshold() -> None:
    policy = ThresholdPolicy(
        usable_context_tokens=64_000,
        background_threshold_tokens=22_000,
        urgent_threshold_tokens=64_000,
    )
    assert policy.priority_for(10_000, projected_tokens=22_000) == 50
    assert policy.priority_for(10_000, projected_tokens=52_000) == 100
    assert policy.priority_for(10_000, projected_tokens=10_000) is None


def test_freeze_snapshot_honors_source_window_without_emptying_backlog(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    first = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="a" * 3_000,
    )
    second = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="assistant_message",
        role="assistant",
        content="b" * 3_000,
    )

    snapshot = freeze_snapshot(store, "thread", source_window_tokens=1_000)

    assert snapshot.snapshot_start_event_id == first["id"]
    assert snapshot.snapshot_end_event_id == first["id"]
    assert [event["id"] for event in snapshot.events] == [first["id"]]
    assert second["id"] not in [event["id"] for event in snapshot.events]


def test_freeze_snapshot_caps_selector_chunks_so_progress_is_incremental(
    tmp_path: Path,
) -> None:
    from memory_gateway.compaction import (
        MAX_SELECTOR_CHUNKS_PER_GENERATION,
        selector_chunk_count_for_events,
    )

    store = make_store(tmp_path)
    rows = []
    for index in range(16):
        rows.append(
            store.append_event(
                project_id="project",
                thread_id="thread",
                event_type="assistant_message",
                role="assistant",
                content=("durable freetoshop milestone " * 80) + str(index),
            )
        )

    unbounded_chunks = selector_chunk_count_for_events(rows)
    assert unbounded_chunks > MAX_SELECTOR_CHUNKS_PER_GENERATION

    snapshot = freeze_snapshot(store, "thread", source_window_tokens=26_999)
    capped = selector_chunk_count_for_events(list(snapshot.events))
    assert 1 <= capped <= MAX_SELECTOR_CHUNKS_PER_GENERATION
    assert snapshot.snapshot_end_event_id != rows[-1]["id"]


def test_identity_leak_capsule_fails_validation() -> None:
    from memory_gateway.compaction import (
        CompactionResult,
        CompactionSnapshot,
        validate_compaction_result,
    )

    content = "Agnes, a language model developed by Sapiens AI."
    snapshot = CompactionSnapshot(
        thread_id="thread",
        base_capsule_id=None,
        snapshot_start_event_id="evt_1",
        snapshot_end_event_id="evt_11",
        events=tuple({"id": f"evt_{i}"} for i in range(1, 12)),
        base_capsule_content=None,
        source_event_hash="s",
        input_hash="i",
    )
    result = CompactionResult(
        content=content,
        covered_event_ids=tuple(event["id"] for event in snapshot.events),
        evidence_event_ids=("evt_1",),
        input_hash="i",
        output_hash=hashlib.sha256(content.encode()).hexdigest(),
        model_identity="consolidator",
        prompt_version="v1",
        generation_settings={},
    )
    with pytest.raises(ValueError, match="model-identity leak"):
        validate_compaction_result(snapshot, result)


def test_uncompacted_tokens_start_after_active_capsule_coverage(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    covered = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="already covered",
        token_count=100,
    )
    active_capsule_through(store, covered["id"])
    after_first = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="after capsule",
        token_count=4,
    )
    after_second_content = "another uncompacted event"
    store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="assistant_message",
        role="assistant",
        content=after_second_content,
    )
    scheduler = CompactionScheduler(
        store,
        ThresholdPolicy(usable_context_tokens=10, background_fraction=0.5),
    )
    assert scheduler.uncompacted_tokens("thread") == 4 + estimate_tokens(after_second_content)
    job_id = scheduler.maybe_enqueue("thread")
    assert job_id is not None
    job = store.get_job(job_id)
    assert job["snapshot_start_event_id"] == after_first["id"]


def test_parked_expired_job_does_not_block_fresh_snapshot_or_recovery_gate(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    covered = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="covered",
        token_count=1,
    )
    active_capsule_through(store, covered["id"])
    old_event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="generation seven evidence",
        token_count=5,
    )
    old_job_id = CompactionScheduler(
        store,
        ThresholdPolicy(usable_context_tokens=1, background_fraction=0.5),
    ).maybe_enqueue("thread")
    assert old_job_id is not None
    old_claim = store.claim_next_job("old-worker", lease_seconds=-1)
    assert old_claim is not None
    with store.connect() as connection:
        connection.execute(
            """
            UPDATE compaction_jobs
            SET generation = 7, attempts = 2,
                lease_until = '2000-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (old_job_id,),
        )
    fresh_event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="newer evidence",
        token_count=5,
    )
    scheduler = CompactionScheduler(
        store,
        ThresholdPolicy(usable_context_tokens=1, background_fraction=0.5),
    )

    fresh_job_id = scheduler.maybe_enqueue("thread")
    assert fresh_job_id is not None
    assert fresh_job_id != old_job_id
    fresh_job = store.get_job(fresh_job_id)
    assert fresh_job["generation"] == 8
    assert fresh_job["snapshot_start_event_id"] == old_event["id"]
    assert fresh_job["snapshot_end_event_id"] == fresh_event["id"]
    assert fresh_job["status"] == "QUEUED"

    claimed_fresh = store.claim_next_job(
        "fresh-worker",
        lease_seconds=60,
        recover_expired=False,
    )
    assert claimed_fresh is not None
    assert claimed_fresh["id"] == fresh_job_id
    assert claimed_fresh["lease_token"] != old_claim["lease_token"]
    assert store.get_job(old_job_id)["attempts"] == 2
    assert (
        asyncio.run(
            CompactionWorker(
                store,
                PerfectCompactionEngine(),
                "startup-worker",
            ).run_once()
        )
        is None
    )
    assert not store.renew_job_lease(
        old_job_id,
        "old-worker",
        old_claim["lease_token"],
        lease_seconds=60,
    )
    reclaimed = store.claim_next_job(
        "recovery-worker",
        lease_seconds=60,
        recover_expired=True,
    )
    assert reclaimed is not None
    assert reclaimed["id"] == old_job_id
    assert reclaimed["lease_token"] != old_claim["lease_token"]
    assert not store.finish_job(
        old_job_id,
        "FAILED",
        "parked owner",
        worker_id="old-worker",
        lease_token=old_claim["lease_token"],
    )

    candidate = store.create_capsule(
        thread_id="thread",
        base_capsule_id=store.get_active_capsule("thread")["id"],
        content="candidate",
        source_event_hash="source",
        input_hash="input",
        output_hash="output",
        capsule_hash="candidate",
        snapshot_start_event_id=old_event["id"],
        snapshot_end_event_id=fresh_event["id"],
        covered_start_event_id=old_event["id"],
        covered_end_event_id=fresh_event["id"],
    )
    assert store.mark_capsule_ready(candidate)
    assert not store.promote_capsule_cas(
        "thread",
        candidate,
        job_id=old_job_id,
        worker_id="old-worker",
        lease_token=old_claim["lease_token"],
    )
    assert store.get_active_capsule("thread")["id"] != candidate


def test_failed_snapshot_can_be_retried_with_new_generation(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="keep compacting this window",
        token_count=40,
    )
    event_id = event["id"]
    first = store.request_coalesced_compaction_job(
        thread_id="thread",
        base_capsule_id=None,
        snapshot_start_event_id=event_id,
        snapshot_end_event_id=event_id,
        priority=50,
    )
    assert first is not None
    claimed = store.claim_next_job("worker", lease_seconds=60)
    assert claimed is not None
    assert store.finish_job(
        first,
        "FAILED",
        "consolidator stage failed: model request timed out after 180.0s",
        worker_id="worker",
        lease_token=claimed["lease_token"],
    )
    retry = store.request_coalesced_compaction_job(
        thread_id="thread",
        base_capsule_id=None,
        snapshot_start_event_id=event_id,
        snapshot_end_event_id=event_id,
        priority=100,
    )
    assert retry is not None
    assert retry != first
    jobs = {job["id"]: job for job in store.list_jobs(thread_id="thread")}
    assert jobs[first]["status"] == "FAILED"
    assert jobs[retry]["status"] == "QUEUED"
    assert jobs[retry]["generation"] == jobs[first]["generation"] + 1
    assert jobs[retry]["snapshot_end_event_id"] == event_id


def test_capsule_promotion_and_job_completion_commit_together(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="atomic promotion evidence",
        token_count=4,
    )
    job_id = store.create_compaction_job(
        thread_id="thread",
        base_capsule_id=None,
        snapshot_start_event_id=event["id"],
        snapshot_end_event_id=event["id"],
    )
    claimed = store.claim_next_job("atomic-worker")
    assert claimed is not None
    content = "atomic capsule"
    candidate_id = store.create_capsule(
        thread_id="thread",
        base_capsule_id=None,
        content=content,
        source_event_hash="source",
        input_hash="input",
        output_hash=hashlib.sha256(content.encode()).hexdigest(),
        capsule_hash="capsule",
        snapshot_start_event_id=event["id"],
        snapshot_end_event_id=event["id"],
        covered_start_event_id=event["id"],
        covered_end_event_id=event["id"],
        source_event_ids=(event["id"],),
    )
    assert store.mark_capsule_ready(candidate_id)

    assert store.promote_capsule_cas_and_finish_job(
        "thread",
        candidate_id,
        job_id=job_id,
        worker_id="atomic-worker",
        lease_token=claimed["lease_token"],
    )

    assert store.get_job(job_id)["status"] == "PROMOTED"
    assert store.get_job(job_id)["lease_until"] is None
    assert store.get_active_capsule("thread")["id"] == candidate_id
    with store.connect() as connection:
        active_count = connection.execute(
            "SELECT COUNT(*) FROM capsules WHERE thread_id = ? AND state = 'ACTIVE'",
            ("thread",),
        ).fetchone()[0]
    assert active_count == 1
