from __future__ import annotations

import sqlite3
from pathlib import Path

from memory_gateway.db import SQLiteStore
from tools import benchmark_freetoshop_corrected_all_solar as benchmark


def _model_run(store: SQLiteStore, job_id: str, generation: int, stage: str, event_id: str) -> None:
    store.record_model_run(
        {
            "job_id": job_id,
            "thread_id": benchmark.THREAD_ID,
            "generation": generation,
            "stage": stage,
            "model": "upstage/solar-pro4",
            "endpoint": "https://openrouter.ai/api",
            "prompt_version": f"{stage}-v1",
            "generation_settings": {"temperature": 0},
            "source_refs": [event_id],
            "input_hash": f"input-{stage}-{generation}",
            "output_hash": f"output-{stage}-{generation}",
            "input_tokens": 100,
            "output_tokens": 10,
            "wall_ms": 25,
            "status": "SUCCEEDED",
            "metadata": {
                "request_profile": {
                    "input_payload_chars": 200,
                    "system_prompt_chars": 100,
                    "response_schema_chars": 100,
                    "request_wrapper_chars": 40,
                }
            },
        }
    )


def _three_promotions(path: Path) -> tuple[SQLiteStore, list[str], list[str]]:
    store = SQLiteStore(path)
    store.initialize()
    store.create_project("project")
    store.create_thread(benchmark.THREAD_ID, "project")
    event_ids: list[str] = []
    job_ids: list[str] = []
    base_capsule_id = None
    for generation in range(1, 4):
        event_id = f"event-{generation}"
        store.append_event(
            project_id="project",
            thread_id=benchmark.THREAD_ID,
            event_type="user_message",
            role="user",
            content=f"event content {generation}",
            token_count=10,
            event_id=event_id,
        )
        job_id = store.create_compaction_job(
            thread_id=benchmark.THREAD_ID,
            base_capsule_id=base_capsule_id,
            snapshot_start_event_id=event_id,
            snapshot_end_event_id=event_id,
        )
        _model_run(store, job_id, generation, "selector", event_id)
        _model_run(store, job_id, generation, "consolidator", event_id)
        capsule_id = store.create_capsule(
            thread_id=benchmark.THREAD_ID,
            base_capsule_id=base_capsule_id,
            content=f"capsule {generation}",
            source_event_hash=f"source-{generation}",
            input_hash=f"input-{generation}",
            output_hash=f"output-{generation}",
            capsule_hash=f"capsule-hash-{generation}",
            snapshot_start_event_id=event_id,
            snapshot_end_event_id=event_id,
            covered_start_event_id=event_id,
            covered_end_event_id=event_id,
            model_metadata={
                "evidence_source_ids": [event_id],
                "evidence_event_ids": [event_id],
            },
        )
        assert store.mark_capsule_ready(capsule_id)
        assert store.promote_capsule_cas(benchmark.THREAD_ID, capsule_id)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE compaction_jobs SET status='PROMOTED', started_at=created_at, "
                "finished_at=created_at WHERE id=?",
                (job_id,),
            )
        event_ids.append(event_id)
        job_ids.append(job_id)
        base_capsule_id = capsule_id
    return store, event_ids, job_ids


def test_three_exact_promotions_pass_gate(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "memory.db"
    _three_promotions(database)
    monkeypatch.setattr(benchmark, "DB_PATH", database)

    state = benchmark.inspect_state()

    assert state["gate"] == {
        "status": "PASSED",
        "required": 3,
        "promotions": 3,
        "clean_promotions": 3,
        "failures": 0,
        "invariant_failures": 0,
    }
    assert state["canonicalizer_calls"] == 0
    assert all(item["clean_promotion"] for item in state["generations"])
    assert state["scheduler"]["bounded"] is True


def test_canonicalizer_call_fails_gate(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "memory.db"
    store, event_ids, job_ids = _three_promotions(database)
    _model_run(store, job_ids[-1], 3, "canonicalizer", event_ids[-1])
    monkeypatch.setattr(benchmark, "DB_PATH", database)

    state = benchmark.inspect_state()

    assert state["gate"]["status"] == "FAILED"
    assert state["canonicalizer_calls"] == 1
    assert state["generations"][-1]["clean_promotion"] is False
