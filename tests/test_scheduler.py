from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from memory_gateway.compaction import CompactionWorker
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
