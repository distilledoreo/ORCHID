from __future__ import annotations

import asyncio
import hashlib
import threading
from pathlib import Path

import pytest

from memory_gateway.config import RuntimeConfig
from memory_gateway.compaction import CompactionWorker, queue_snapshot_job
from memory_gateway.context import ContextAssembler
from memory_gateway.db import SQLiteStore
from memory_gateway.fake_engines import BrokenCompactionEngine, PerfectCompactionEngine, SlowCompactionEngine


def make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    return store


def ready_capsule(store: SQLiteStore, base: str | None, content: str) -> str:
    digest = hashlib.sha256(content.encode()).hexdigest()
    capsule_id = store.create_capsule(
        thread_id="thread",
        base_capsule_id=base,
        content=content,
        source_event_hash="source",
        input_hash="input",
        output_hash=digest,
        capsule_hash=digest,
        snapshot_start_event_id=None,
        snapshot_end_event_id=None,
        covered_start_event_id=None,
        covered_end_event_id=None,
    )
    assert store.mark_capsule_ready(capsule_id)
    return capsule_id


def test_events_are_append_only_and_sequenced(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="first",
    )
    second = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="assistant_message",
        role="assistant",
        content="second",
        parent_event_id=first["id"],
    )

    events = store.list_events("thread")
    assert [event["sequence"] for event in events] == [1, 2]
    assert second["parent_event_id"] == first["id"]
    assert first["content_hash"] == hashlib.sha256(b"first").hexdigest()


def test_only_one_ready_descendant_wins_cas(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    base = ready_capsule(store, None, "capsule one")
    assert store.promote_capsule_cas("thread", base)
    left = ready_capsule(store, base, "left")
    right = ready_capsule(store, base, "right")

    barrier = threading.Barrier(2)
    results: list[bool] = []

    def promote(candidate: str) -> None:
        barrier.wait()
        results.append(store.promote_capsule_cas("thread", candidate))

    threads = [
        threading.Thread(target=promote, args=(left,)),
        threading.Thread(target=promote, args=(right,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]
    active = store.get_active_capsule("thread")
    assert active is not None
    assert active["id"] in {left, right}
    stale_id = right if active["id"] == left else left
    with store.connect() as connection:
        stale = connection.execute("SELECT state FROM capsules WHERE id = ?", (stale_id,)).fetchone()
    assert stale["state"] == "STALE"


def test_failed_candidate_cannot_replace_active(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    base = ready_capsule(store, None, "active")
    assert store.promote_capsule_cas("thread", base)
    candidate = store.create_capsule(
        thread_id="thread",
        base_capsule_id=base,
        content="bad",
        source_event_hash="source",
        input_hash="input",
        output_hash="output",
        capsule_hash="candidate",
        snapshot_start_event_id=None,
        snapshot_end_event_id=None,
        covered_start_event_id=None,
        covered_end_event_id=None,
    )
    assert store.mark_capsule_failed(candidate, "invalid coverage")
    assert store.get_active_capsule("thread")["id"] == base


def test_worker_uses_frozen_watermark_and_promotes_valid_candidate(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="before snapshot",
    )
    second = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="tool_result",
        role="tool",
        content="included in snapshot",
    )
    job_id = queue_snapshot_job(store, "thread")
    store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="after snapshot",
    )
    observed: list[tuple[str, ...]] = []

    class FakeEngine(PerfectCompactionEngine):
        async def compact(self, *, base_capsule, events, snapshot_end_event_id):
            observed.append(tuple(event.id for event in events))
            assert snapshot_end_event_id == second["id"]
            return await super().compact(
                base_capsule=base_capsule,
                events=events,
                snapshot_end_event_id=snapshot_end_event_id,
            )

    assert asyncio.run(CompactionWorker(store, FakeEngine(), "worker-a").run_once()) == "PROMOTED"
    assert observed == [(first["id"], second["id"])]
    assert store.get_job(job_id)["status"] == "PROMOTED"
    active = store.get_active_capsule("thread")
    assert active["covered_end_event_id"] == second["id"]
    assert store.latest_event("thread")["content"] == "after snapshot"


def test_expired_lease_can_be_reclaimed(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    job_id = queue_snapshot_job(store, "thread")
    first = store.claim_next_job("dead-worker", lease_seconds=-1)
    second = store.claim_next_job("replacement-worker", lease_seconds=60)
    assert first["id"] == job_id
    assert second["id"] == job_id
    assert second["worker_id"] == "replacement-worker"
    assert second["attempts"] == 2
    assert first["lease_token"] != second["lease_token"]
    assert not store.renew_job_lease(
        job_id,
        "dead-worker",
        first["lease_token"],
        lease_seconds=60,
    )
    assert not store.finish_job(
        job_id,
        "FAILED",
        "old owner must not finish",
        worker_id="dead-worker",
        lease_token=first["lease_token"],
    )
    assert store.owns_job(
        job_id,
        "replacement-worker",
        second["lease_token"],
    )


def test_lease_renewal_is_conditional_on_owner_and_token(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    job_id = queue_snapshot_job(store, "thread")
    claimed = store.claim_next_job("worker-a", lease_seconds=60)

    assert claimed is not None
    assert store.renew_job_lease(
        job_id,
        "worker-a",
        claimed["lease_token"],
        lease_seconds=60,
    )
    assert not store.renew_job_lease(
        job_id,
        "worker-b",
        claimed["lease_token"],
        lease_seconds=60,
    )
    assert not store.renew_job_lease(
        job_id,
        "worker-a",
        "wrong-token",
        lease_seconds=60,
    )


def test_expired_recovery_can_be_disabled_without_mutating_job(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    job_id = queue_snapshot_job(store, "thread")
    claimed = store.claim_next_job("previous-worker", lease_seconds=-1)

    assert claimed is not None
    assert (
        store.claim_next_job(
            "startup-worker",
            lease_seconds=60,
            recover_expired=False,
        )
        is None
    )
    job = store.get_job(job_id)
    assert job["status"] == "RUNNING"
    assert job["attempts"] == 1
    assert job["lease_token"] == claimed["lease_token"]
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


def test_reclaimed_worker_is_cancelled_before_promotion(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="long compaction",
    )
    job_id = queue_snapshot_job(store, "thread")
    started = asyncio.Event()
    blocked = asyncio.Event()

    class BlockingEngine:
        async def compact(self, *, base_capsule, events, snapshot_end_event_id):
            started.set()
            await blocked.wait()
            raise AssertionError("blocking owner should be cancelled")

    async def run_race() -> tuple[str, str]:
        old_worker = CompactionWorker(
            store,
            BlockingEngine(),
            "worker-a",
            lease_seconds=60,
            renewal_interval_seconds=0.01,
        )
        old_task = asyncio.create_task(old_worker.run_once())
        await asyncio.wait_for(started.wait(), timeout=1)
        with store.connect() as connection:
            connection.execute(
                "UPDATE compaction_jobs SET lease_until = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", job_id),
            )
        replacement_task = asyncio.create_task(
            CompactionWorker(
                store,
                PerfectCompactionEngine(),
                "worker-b",
                lease_seconds=60,
                renewal_interval_seconds=0.01,
                recover_expired_jobs=True,
            ).run_once()
        )
        replacement_result = await asyncio.wait_for(replacement_task, timeout=2)
        old_result = await asyncio.wait_for(old_task, timeout=2)
        return old_result, replacement_result

    old_result, replacement_result = asyncio.run(run_race())
    assert (old_result, replacement_result) == ("LOST", "PROMOTED")
    assert store.get_job(job_id)["status"] == "PROMOTED"
    assert store.get_active_capsule("thread")["covered_end_event_id"] == event["id"]


def test_lost_owner_cannot_promote_ready_candidate(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    base = ready_capsule(store, None, "base")
    assert store.promote_capsule_cas("thread", base)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="candidate event",
    )
    job_id = queue_snapshot_job(store, "thread")
    claimed = store.claim_next_job("worker-a", lease_seconds=60)
    candidate = store.create_capsule(
        thread_id="thread",
        base_capsule_id=base,
        content="candidate",
        source_event_hash="source",
        input_hash="input",
        output_hash="output",
        capsule_hash="candidate-hash",
        snapshot_start_event_id=event["id"],
        snapshot_end_event_id=event["id"],
        covered_start_event_id=event["id"],
        covered_end_event_id=event["id"],
    )
    assert store.mark_capsule_ready(candidate)
    assert not store.promote_capsule_cas(
        "thread",
        candidate,
        job_id=job_id,
        worker_id="worker-b",
        lease_token=claimed["lease_token"],
    )
    assert store.get_active_capsule("thread")["id"] == base
    with store.connect() as connection:
        assert connection.execute(
            "SELECT state FROM capsules WHERE id = ?", (candidate,)
        ).fetchone()["state"] == "STALE"


def test_runtime_lease_defaults_and_recovery_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ORCHID_LEASE_SECONDS",
        "ORCHID_LEASE_RENEWAL_SECONDS",
        "ORCHID_RECOVER_EXPIRED_JOBS",
    ):
        monkeypatch.delenv(name, raising=False)
    config = RuntimeConfig.from_env()
    assert config.lease_seconds == 900
    assert config.lease_renewal_seconds == 30
    assert config.recover_expired_jobs is False


def test_runtime_rejects_renewal_cadence_longer_than_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHID_LEASE_SECONDS", "10")
    monkeypatch.setenv("ORCHID_LEASE_RENEWAL_SECONDS", "10")
    with pytest.raises(ValueError, match="RENEWAL_SECONDS"):
        RuntimeConfig.from_env()


def test_broken_engine_fails_before_candidate_creation(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="must remain in raw history",
    )
    job_id = queue_snapshot_job(store, "thread")
    result = asyncio.run(CompactionWorker(store, BrokenCompactionEngine(), "worker-b").run_once())
    assert result == "FAILED"
    assert store.get_job(job_id)["status"] == "FAILED"
    assert store.get_active_capsule("thread") is None
    assert store.get_event(event["id"])["content"] == "must remain in raw history"


def test_delayed_jobs_preserve_lineage_and_context_continuity(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    v1 = ready_capsule(store, None, "v1")
    assert store.promote_capsule_cas("thread", v1)

    def append(content: str) -> dict:
        return store.append_event(
            project_id="project",
            thread_id="thread",
            event_type="user_message",
            role="user",
            content=content,
        )

    append("turn for v2")
    v2_job = queue_snapshot_job(store, "thread")
    assert asyncio.run(CompactionWorker(store, PerfectCompactionEngine(), "v2-worker").run_once()) == "PROMOTED"
    v2 = store.get_active_capsule("thread")
    assert store.get_job(v2_job)["status"] == "PROMOTED"

    append("turn captured by slow v3")
    v3_job = queue_snapshot_job(store, "thread")

    async def race_slow_v3() -> tuple[str, str]:
        v3_task = asyncio.create_task(CompactionWorker(store, SlowCompactionEngine(0.05), "v3-worker").run_once())
        await asyncio.sleep(0.01)
        append("turn captured by v4")
        v4_job = queue_snapshot_job(store, "thread")
        v4_result = await CompactionWorker(store, PerfectCompactionEngine(), "v4-worker").run_once()
        v3_result = await v3_task
        return v3_result, v4_result

    v3_result, v4_result = asyncio.run(race_slow_v3())
    assert (v3_result, v4_result) == ("STALE", "PROMOTED")
    assert store.get_job(v3_job)["status"] == "STALE"
    v4 = store.get_active_capsule("thread")
    assert v4 is not None
    assert v4["base_capsule_id"] == v2["id"]

    append("turn for failed v5")
    v5_job = queue_snapshot_job(store, "thread")
    assert asyncio.run(CompactionWorker(store, BrokenCompactionEngine(), "v5-worker").run_once()) == "FAILED"
    assert store.get_job(v5_job)["status"] == "FAILED"
    assert store.get_active_capsule("thread")["id"] == v4["id"]

    append("turn for v6")
    v6_job = queue_snapshot_job(store, "thread")
    assert asyncio.run(CompactionWorker(store, PerfectCompactionEngine(), "v6-worker").run_once()) == "PROMOTED"
    assert store.get_job(v6_job)["status"] == "PROMOTED"
    v6 = store.get_active_capsule("thread")
    assert v6["base_capsule_id"] == v4["id"]

    append("post-v6 raw tail")
    context = ContextAssembler(store, raw_tail_target_tokens=10_000, minimum_raw_tail_tokens=1).assemble(
        "thread",
        [{"role": "user", "content": "current request"}],
    )
    latest = store.latest_event("thread")
    assert latest["id"] in context.raw_tail_event_ids
    covered_end = store.get_event(v6["covered_end_event_id"])
    assert covered_end["sequence"] == latest["sequence"] - 1
