from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

from memory_gateway.cold_memory import (
    CALIBRATED_RANKING_POLICY,
    FTS5ColdMemoryRetriever,
    build_retrieval_query,
)
from memory_gateway.compaction import (
    CompactionResult,
    CompactionWorker,
    RetireMemory,
    compute_input_hash,
    queue_snapshot_job,
)
from memory_gateway.context import ContextAssembler
from memory_gateway.db import SQLiteStore
from memory_gateway.cold_telemetry import BufferedColdMemoryTelemetry


def make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    return store


def test_retire_storage_and_fts_retrieval_are_sidecar_only(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="tool_result",
        role="tool",
        content="memory_gateway/db.py renew_lease validates lease_until ownership",
    )
    before_events = store.list_events("thread")
    ids = store.persist_long_term_memories(
        thread_id="thread",
        memories=[
            {
                "content": "renew_lease must validate lease_until ownership",
                "memory_type": "decision",
                "importance": 0.9,
                "evidence_event_ids": [event["id"]],
            }
        ],
    )

    result = FTS5ColdMemoryRetriever(store).retrieve(
        project_id="project",
        thread_id="thread",
        query="renew_lease lease_until",
        token_budget=512,
        max_injected=3,
        mode="shadow",
    )

    assert ids and result.status == "ok"
    assert result.candidates[0].memory_id == ids[0]
    assert result.would_inject[0].memory_id == ids[0]
    assert store.list_events("thread") == before_events
    assert store.list_memory_evidence(ids[0])[0]["id"] == event["id"]
    with store.connect() as connection:
        row = connection.execute(
            "SELECT retrieval_count, injection_count FROM long_term_memories WHERE id = ?",
            (ids[0],),
        ).fetchone()
        run = connection.execute(
            "SELECT mode, status, would_inject_ids_json FROM cold_retrieval_runs"
        ).fetchone()
    assert (row["retrieval_count"], row["injection_count"]) == (1, 0)
    assert run["mode"] == "shadow"
    assert run["status"] == "ok"
    assert ids[0] in run["would_inject_ids_json"]
    with store.connect() as connection:
        telemetry = connection.execute(
            """
            SELECT attempted, timed_out, fail_open, query_hash, query_preview,
                   exact_candidate_count, lexical_candidate_count,
                   unique_candidate_count, threshold_candidate_count,
                   would_inject_count, injected_count,
                   retrieved_token_estimate, injected_token_estimate,
                   error_category, cold_retrieval_ms,
                   constructed_query, input_terms_json, identifier_terms_json,
                   fts_query, raw_fts_candidates_json, ranking_details_json,
                   query_construction_ms, db_checkout_ms, fts_ms, ranking_ms,
                   token_budget_ms, telemetry_ms, total_ms
            FROM cold_retrieval_runs
            """
        ).fetchone()
    assert telemetry["attempted"] == 1
    assert telemetry["timed_out"] == 0
    assert telemetry["fail_open"] == 0
    assert len(telemetry["query_hash"]) == 64
    assert telemetry["query_preview"] == "renew_lease lease_until"
    assert telemetry["exact_candidate_count"] == 1
    assert telemetry["lexical_candidate_count"] == 1
    assert telemetry["unique_candidate_count"] == 1
    assert telemetry["threshold_candidate_count"] == 1
    assert telemetry["would_inject_count"] == 1
    assert telemetry["injected_count"] == 0
    assert telemetry["retrieved_token_estimate"] > 0
    assert telemetry["injected_token_estimate"] == 0
    assert telemetry["error_category"] is None
    assert telemetry["cold_retrieval_ms"] >= 0
    assert telemetry["constructed_query"] == "renew_lease lease_until"
    assert "renew_lease" in json.loads(telemetry["input_terms_json"])
    assert "renew_lease" in json.loads(telemetry["identifier_terms_json"])
    assert telemetry["fts_query"]
    assert json.loads(telemetry["raw_fts_candidates_json"])[0]["memory_id"] == ids[0]
    assert json.loads(telemetry["ranking_details_json"])[0]["decision"] == "would_inject"
    assert telemetry["query_construction_ms"] >= 0
    assert telemetry["db_checkout_ms"] >= 0
    assert telemetry["fts_ms"] >= 0
    assert telemetry["ranking_ms"] >= 0
    assert telemetry["token_budget_ms"] >= 0
    assert telemetry["telemetry_ms"] >= 0
    assert telemetry["total_ms"] >= telemetry["cold_retrieval_ms"]


def test_query_builder_promotes_identifiers_from_hot_signals() -> None:
    query = build_retrieval_query(
        [{"role": "user", "content": "Yep, fix it."}],
        active_content="working on lease ownership logic",
        latest_tool_result="memory_gateway/db.py renew_lease() lease_until",
    )

    assert "renew_lease" in query
    assert "lease_until" in query
    assert "memory_gateway/db.py" in query
    assert "fix" not in query


def test_calibrated_policy_requires_identifier_evidence_and_filters_collision(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="fixture_evidence",
        role="tool",
        content="selector protocol evidence",
    )
    target_id, collision_id = store.persist_long_term_memories(
        thread_id="thread",
        memories=[
            {
                "content": (
                    "The selector response uses selector_chunk_index and "
                    "tests/test_openai_adapter.py per-chunk enum."
                ),
                "memory_type": "protocol",
                "importance": 0.86,
                "evidence_event_ids": [event["id"]],
            },
            {
                "content": (
                    "The dynamic per-chunk enum replaced the old selector "
                    "schema and is a related protocol note."
                ),
                "memory_type": "test",
                "importance": 0.7,
                "evidence_event_ids": [event["id"]],
            },
        ],
    )

    result = FTS5ColdMemoryRetriever(
        store,
        ranking_policy=CALIBRATED_RANKING_POLICY,
    ).retrieve(
        project_id="project",
        thread_id="thread",
        query="selector_chunk_index tests/test_openai_adapter.py per-chunk enum",
        token_budget=512,
        max_injected=3,
        mode="shadow",
    )

    assert [hit.memory_id for hit in result.would_inject] == [target_id]
    assert collision_id in [hit.memory_id for hit in result.candidates]
    collision_detail = next(
        detail
        for detail in result.ranking_details
        if detail["memory_id"] == collision_id
    )
    assert collision_detail["evidence_gate"] is False
    assert collision_detail["decision"] != "would_inject"


def test_buffered_telemetry_preserves_retrieval_decisions_and_flushes_in_batch(
    tmp_path: Path,
) -> None:
    def seeded(path: Path) -> SQLiteStore:
        store = make_store(path)
        event = store.append_event(
            project_id="project",
            thread_id="thread",
            event_type="fixture_evidence",
            role="tool",
            content="renew_lease lease_until ownership",
        )
        store.persist_long_term_memories(
            thread_id="thread",
            memories=[
                {
                    "id": "mem_buffered",
                    "content": "renew_lease validates lease_until ownership",
                    "memory_type": "decision",
                    "importance": 0.9,
                    "evidence_event_ids": [event["id"]],
                }
            ],
        )
        return store

    synchronous_store = seeded(tmp_path / "sync")
    synchronous = FTS5ColdMemoryRetriever(synchronous_store).retrieve(
        project_id="project",
        thread_id="thread",
        query="renew_lease lease_until",
        token_budget=512,
        max_injected=3,
        mode="shadow",
    )

    buffered_store = seeded(tmp_path / "buffered")
    telemetry = BufferedColdMemoryTelemetry(
        buffered_store,
        max_queue_size=16,
        batch_size=8,
        flush_interval_ms=1,
    )
    buffered = FTS5ColdMemoryRetriever(
        buffered_store,
        telemetry_sink=telemetry,
    ).retrieve(
        project_id="project",
        thread_id="thread",
        query="renew_lease lease_until",
        token_budget=512,
        max_injected=3,
        mode="shadow",
    )
    assert [hit.memory_id for hit in buffered.candidates] == [
        hit.memory_id for hit in synchronous.candidates
    ]
    assert [hit.memory_id for hit in buffered.would_inject] == [
        hit.memory_id for hit in synchronous.would_inject
    ]
    assert [detail["decision"] for detail in buffered.ranking_details] == [
        detail["decision"] for detail in synchronous.ranking_details
    ]
    telemetry.close()
    metrics = telemetry.metrics()
    assert metrics["dropped_count"] == 0
    assert metrics["flush_error_count"] == 0
    assert metrics["flushed_operation_count"] == 2
    with buffered_store.connect() as connection:
        memory = connection.execute(
            "SELECT retrieval_count FROM long_term_memories WHERE id = 'mem_buffered'"
        ).fetchone()
        run = connection.execute(
            "SELECT candidate_ids_json, would_inject_ids_json FROM cold_retrieval_runs"
        ).fetchone()
    assert memory["retrieval_count"] == 1
    assert "mem_buffered" in run["candidate_ids_json"]
    assert "mem_buffered" in run["would_inject_ids_json"]


def test_buffered_telemetry_updates_actual_injection_after_queued_run(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="tool_result",
        role="tool",
        content="renew_lease ownership decision",
    )
    memory_id = store.persist_long_term_memories(
        thread_id="thread",
        memories=[
            {
                "id": "mem_injected_buffered",
                "content": "renew_lease requires ownership validation",
                "memory_type": "decision",
                "importance": 0.8,
                "evidence_event_ids": [event["id"]],
            }
        ],
    )[0]
    telemetry = BufferedColdMemoryTelemetry(
        store,
        max_queue_size=16,
        batch_size=16,
        flush_interval_ms=1,
    )
    snapshot = ContextAssembler(
        store,
        raw_tail_target_tokens=1_000,
        minimum_raw_tail_tokens=1,
        context_budget_tokens=2_000,
        cold_memory_provider=FTS5ColdMemoryRetriever(
            store,
            telemetry_sink=telemetry,
        ),
        cold_memory_mode="inject",
        cold_memory_telemetry=telemetry,
    ).assemble(
        "thread",
        [{"role": "user", "content": "Why did we choose renew_lease?"}],
        project_id="project",
    )
    assert snapshot.cold_memory_tokens > 0
    telemetry.close()
    with store.connect() as connection:
        memory = connection.execute(
            "SELECT retrieval_count, injection_count FROM long_term_memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        run = connection.execute(
            "SELECT injected_count, injected_ids_json FROM cold_retrieval_runs"
        ).fetchone()
    assert (memory["retrieval_count"], memory["injection_count"]) == (1, 1)
    assert run["injected_count"] == 1
    assert memory_id in run["injected_ids_json"]


def test_buffered_telemetry_drops_on_full_queue_without_blocking(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = make_store(tmp_path)
    telemetry = BufferedColdMemoryTelemetry(store, max_queue_size=1)

    def full(_operation) -> None:
        from queue import Full

        raise Full

    monkeypatch.setattr(telemetry._queue, "put_nowait", full)
    started = time.perf_counter()
    accepted = telemetry.record_memory_retrieval(memory_ids=("mem_missing",))
    elapsed_ms = (time.perf_counter() - started) * 1000
    telemetry.close()

    assert accepted is False
    assert telemetry.metrics()["dropped_count"] == 1
    assert elapsed_ms < 100


def test_buffered_flush_failure_does_not_change_retrieval_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="fixture_evidence",
        role="tool",
        content="renew_lease lease_until ownership",
    )
    store.persist_long_term_memories(
        thread_id="thread",
        memories=[
            {
                "id": "mem_flush_failure",
                "content": "renew_lease validates lease_until ownership",
                "memory_type": "decision",
                "importance": 0.9,
                "evidence_event_ids": [event["id"]],
            }
        ],
    )
    telemetry = BufferedColdMemoryTelemetry(store, flush_interval_ms=1)

    def fail(_operations) -> None:
        raise RuntimeError("telemetry database unavailable")

    monkeypatch.setattr(store, "record_cold_telemetry_batch", fail)
    result = FTS5ColdMemoryRetriever(
        store,
        telemetry_sink=telemetry,
    ).retrieve(
        project_id="project",
        thread_id="thread",
        query="renew_lease lease_until",
        token_budget=512,
        max_injected=3,
        mode="shadow",
    )
    telemetry.close()

    assert result.fail_open is False
    assert [hit.memory_id for hit in result.would_inject] == ["mem_flush_failure"]
    assert telemetry.metrics()["flush_error_count"] >= 1


def test_shadow_context_does_not_inject_or_change_hot_context(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="tool_result",
        role="tool",
        content="renew_lease ownership decision",
    )
    store.persist_long_term_memories(
        thread_id="thread",
        memories=[
            {
                "content": "renew_lease requires ownership validation",
                "memory_type": "decision",
                "importance": 0.8,
                "evidence_event_ids": [event["id"]],
            }
        ],
    )
    request = [{"role": "user", "content": "Why did we choose renew_lease?"}]
    baseline = ContextAssembler(
        store,
        raw_tail_target_tokens=1_000,
        minimum_raw_tail_tokens=1,
    ).assemble("thread", request)
    shadow = ContextAssembler(
        store,
        raw_tail_target_tokens=1_000,
        minimum_raw_tail_tokens=1,
        context_budget_tokens=2_000,
        cold_memory_provider=FTS5ColdMemoryRetriever(store),
        cold_memory_mode="shadow",
    ).assemble("thread", request, project_id="project")

    assert shadow.messages == baseline.messages
    assert shadow.cold_retrieval is not None
    assert shadow.cold_retrieval.would_inject
    assert shadow.cold_retrieval.constructed_query
    assert shadow.cold_retrieval.raw_fts_candidates
    assert shadow.cold_retrieval.ranking_details[0]["decision"] == "would_inject"
    assert shadow.cold_retrieval.total_ms >= shadow.cold_retrieval.latency_ms
    assert all("COLD RETRIEVED MEMORY" not in message["content"] for message in shadow.messages)
    assert store.list_events("thread")[-1]["id"] == event["id"]


def test_injection_respects_remaining_context_budget(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="tool_result",
        role="tool",
        content="renew_lease ownership decision",
    )
    store.persist_long_term_memories(
        thread_id="thread",
        memories=[
            {
                "content": "renew_lease requires ownership validation",
                "memory_type": "decision",
                "importance": 0.8,
                "evidence_event_ids": [event["id"]],
            }
        ],
    )
    snapshot = ContextAssembler(
        store,
        raw_tail_target_tokens=1,
        minimum_raw_tail_tokens=0,
        context_budget_tokens=1,
        cold_memory_provider=FTS5ColdMemoryRetriever(store),
        cold_memory_mode="inject",
    ).assemble(
        "thread",
        [{"role": "user", "content": "renew_lease"}],
        project_id="project",
    )

    assert snapshot.cold_retrieval is not None
    assert snapshot.cold_retrieval.candidates
    assert not snapshot.cold_retrieval.would_inject
    assert snapshot.cold_memory_tokens == 0
    assert all("COLD RETRIEVED MEMORY" not in message["content"] for message in snapshot.messages)


def test_injection_adds_only_bounded_non_authoritative_context(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="tool_result",
        role="tool",
        content="renew_lease ownership decision",
    )
    memory_id = store.persist_long_term_memories(
        thread_id="thread",
        memories=[
            {
                "content": "renew_lease requires ownership validation",
                "memory_type": "decision",
                "importance": 0.8,
                "evidence_event_ids": [event["id"]],
            }
        ],
    )[0]

    snapshot = ContextAssembler(
        store,
        raw_tail_target_tokens=1_000,
        minimum_raw_tail_tokens=1,
        context_budget_tokens=2_000,
        cold_memory_provider=FTS5ColdMemoryRetriever(store),
        cold_memory_mode="inject",
        cold_memory_token_budget=512,
        cold_memory_max_injected=3,
    ).assemble(
        "thread",
        [{"role": "user", "content": "Why did we choose renew_lease?"}],
        project_id="project",
    )

    cold_messages = [
        message
        for message in snapshot.messages
        if "COLD RETRIEVED MEMORY" in message["content"]
    ]
    assert len(cold_messages) == 1
    assert memory_id in cold_messages[0]["content"]
    with store.connect() as connection:
        row = connection.execute(
            "SELECT injection_count FROM long_term_memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        telemetry = connection.execute(
            """
            SELECT injected_count, injected_token_estimate, injected_ids_json
            FROM cold_retrieval_runs
            """
        ).fetchone()
    assert row["injection_count"] == 1
    assert telemetry["injected_count"] == 1
    assert telemetry["injected_token_estimate"] == snapshot.cold_memory_tokens
    assert memory_id in telemetry["injected_ids_json"]


def test_fail_open_timeout_preserves_active_and_hot_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="tool_result",
        role="tool",
        content="hot tail remains authoritative",
    )
    digest = hashlib.sha256(b"active capsule").hexdigest()
    capsule_id = store.create_capsule(
        thread_id="thread",
        base_capsule_id=None,
        content="active capsule",
        source_event_hash="source",
        input_hash="input",
        output_hash=digest,
        capsule_hash=digest,
        snapshot_start_event_id=None,
        snapshot_end_event_id=None,
        covered_start_event_id=event["id"],
        covered_end_event_id=event["id"],
    )
    assert store.mark_capsule_ready(capsule_id)
    assert store.promote_capsule_cas("thread", capsule_id)
    before_events = store.list_events("thread")
    before_active = store.get_active_capsule("thread")
    request = [{"role": "user", "content": "renew_lease"}]
    baseline = ContextAssembler(
        store,
        raw_tail_target_tokens=1_000,
        minimum_raw_tail_tokens=1,
    ).assemble("thread", request)

    def timeout(**kwargs):
        raise TimeoutError("cold-memory deadline exceeded")

    monkeypatch.setattr(store, "search_long_term_memories", timeout)
    shadow = ContextAssembler(
        store,
        raw_tail_target_tokens=1_000,
        minimum_raw_tail_tokens=1,
        cold_memory_provider=FTS5ColdMemoryRetriever(store),
        cold_memory_mode="shadow",
    ).assemble("thread", request, project_id="project")

    assert shadow.messages == baseline.messages
    assert shadow.active_capsule_id == baseline.active_capsule_id == capsule_id
    assert shadow.raw_tail_event_ids == baseline.raw_tail_event_ids == (event["id"],)
    assert shadow.cold_retrieval is not None
    assert shadow.cold_retrieval.fail_open is True
    assert shadow.cold_retrieval.timed_out is True
    assert shadow.cold_retrieval.error_category == "timeout"
    assert store.list_events("thread") == before_events
    assert store.get_active_capsule("thread") == before_active
    with store.connect() as connection:
        telemetry = connection.execute(
            "SELECT attempted, timed_out, fail_open, error_category, candidate_ids_json FROM cold_retrieval_runs"
        ).fetchone()
    assert telemetry["attempted"] == 1
    assert telemetry["timed_out"] == 1
    assert telemetry["fail_open"] == 1
    assert telemetry["error_category"] == "timeout"
    assert telemetry["candidate_ids_json"] == "[]"


def test_fail_open_handles_fts_failure_and_malformed_row(tmp_path: Path, monkeypatch) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="tool_result",
        role="tool",
        content="hot context survives sidecar failure",
    )
    before_events = store.list_events("thread")
    baseline = ContextAssembler(
        store,
        raw_tail_target_tokens=1_000,
        minimum_raw_tail_tokens=1,
    ).assemble("thread", [{"role": "user", "content": "historic query"}])
    retriever = FTS5ColdMemoryRetriever(store)

    def fts_failure(**kwargs):
        raise RuntimeError("FTS5 unavailable")

    monkeypatch.setattr(store, "search_long_term_memories", fts_failure)
    failed = retriever.retrieve(
        project_id="project",
        thread_id="thread",
        query="historic query",
        token_budget=512,
        max_injected=3,
        mode="shadow",
    )
    assert failed.fail_open is True
    assert failed.candidates == ()
    assert failed.error_category == "fts_error"

    monkeypatch.setattr(store, "search_long_term_memories", lambda **kwargs: [{"id": "bad"}])
    malformed = retriever.retrieve(
        project_id="project",
        thread_id="thread",
        query="historic query",
        token_budget=512,
        max_injected=3,
        mode="shadow",
    )
    assert malformed.fail_open is True
    assert malformed.error_category == "malformed_row"
    assert malformed.candidates == ()
    assert store.list_events("thread") == before_events
    assert store.get_active_capsule("thread") is None
    with store.connect() as connection:
        categories = [
            row["error_category"]
            for row in connection.execute(
                "SELECT error_category FROM cold_retrieval_runs ORDER BY rowid"
            ).fetchall()
        ]
    assert categories == ["fts_error", "malformed_row"]
    assert baseline.cold_memory_tokens == 0


def test_empty_index_and_provider_exception_are_fail_open_without_hot_change(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    baseline = ContextAssembler(
        store,
        raw_tail_target_tokens=1_000,
        minimum_raw_tail_tokens=0,
    ).assemble("thread", [{"role": "user", "content": "nothing historic"}])
    empty = FTS5ColdMemoryRetriever(store).retrieve(
        project_id="project",
        thread_id="thread",
        query="known_but_missing_memory",
        token_budget=512,
        max_injected=3,
        mode="shadow",
    )
    assert empty.status == "no_match"
    assert empty.candidates == ()
    assert empty.fail_open is False

    class ExplodingProvider:
        def retrieve(self, **kwargs):
            raise RuntimeError("retriever crashed")

    snapshot = ContextAssembler(
        store,
        raw_tail_target_tokens=1_000,
        minimum_raw_tail_tokens=0,
        cold_memory_provider=ExplodingProvider(),
        cold_memory_mode="shadow",
    ).assemble(
        "thread",
        [{"role": "user", "content": "nothing historic"}],
        project_id="project",
    )
    assert snapshot.messages == baseline.messages
    assert snapshot.cold_retrieval is not None
    assert snapshot.cold_retrieval.fail_open is True
    assert snapshot.cold_retrieval.error_category == "provider_exception"
    assert store.list_events("thread") == []
    assert store.get_active_capsule("thread") is None
    with store.connect() as connection:
        telemetry = connection.execute(
            """
            SELECT fail_open, error_category, candidate_ids_json
            FROM cold_retrieval_runs
            ORDER BY rowid DESC
            LIMIT 1
            """
        ).fetchone()
    assert telemetry["fail_open"] == 1
    assert telemetry["error_category"] == "provider_exception"
    assert telemetry["candidate_ids_json"] == "[]"


class RetireEngine:
    async def compact(self, *, base_capsule, events, snapshot_end_event_id):
        content = "hot capsule"
        return CompactionResult(
            content=content,
            covered_event_ids=tuple(event.id for event in events),
            evidence_event_ids=tuple(event.id for event in events),
            input_hash=compute_input_hash(base_capsule, events, snapshot_end_event_id),
            output_hash=hashlib.sha256(content.encode()).hexdigest(),
            model_identity="retire-test",
            prompt_version="retire-test-v1",
            generation_settings={},
            retire_memories=(
                RetireMemory(
                    content="lease ownership must be checked",
                    memory_type="finding",
                    importance=0.9,
                    evidence_event_ids=(events[0].id,),
                ),
            ),
        )


def test_worker_persists_retire_after_hot_promotion(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    event = store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="lease ownership must be checked",
    )
    job_id = queue_snapshot_job(store, "thread")

    assert asyncio.run(CompactionWorker(store, RetireEngine(), "worker").run_once()) == "PROMOTED"
    assert store.get_job(job_id)["status"] == "PROMOTED"
    active = store.get_active_capsule("thread")
    assert active is not None
    with store.connect() as connection:
        memory = connection.execute(
            "SELECT * FROM long_term_memories"
        ).fetchone()
    assert memory["status"] == "ACTIVE"
    assert store.list_memory_evidence(memory["id"])[0]["id"] == event["id"]


def test_cold_persistence_failure_does_not_fail_hot_promotion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = make_store(tmp_path)
    store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="user_message",
        role="user",
        content="hot path remains valid",
    )
    queue_snapshot_job(store, "thread")

    def fail(*args, **kwargs):
        raise RuntimeError("cold sidecar unavailable")

    monkeypatch.setattr(store, "persist_long_term_memories", fail)
    assert asyncio.run(CompactionWorker(store, RetireEngine(), "worker").run_once()) == "PROMOTED"
    assert store.get_active_capsule("thread") is not None
