from __future__ import annotations

from pathlib import Path

from memory_gateway.db import SQLiteStore

from endurance_harness import (
    CHECKPOINT_GENERATIONS,
    DEFAULT_GENERATIONS,
    format_report,
    naive_engine,
    run_endurance,
    semantic_engine,
)


def test_semantic_pipeline_survives_fifty_compaction_generations(tmp_path: Path, capsys) -> None:
    store = SQLiteStore(tmp_path / "endurance.db")
    report = run_endurance(
        store,
        semantic_engine(include_low=True),
        generations=DEFAULT_GENERATIONS,
        checkpoints=CHECKPOINT_GENERATIONS,
        include_low=True,
        require_low=True,
        mode="semantic-lossless",
    )
    print(format_report(report))
    assert report.passed, format_report(report)

    first = report.checkpoints[0]
    last = report.checkpoints[-1]
    assert first.generation == 1
    assert last.generation == 50
    assert last.observed is not None
    assert last.observed.current["compaction_generation"] == "50"
    assert last.observed.current["lease_seconds"] == "900"
    assert last.observed.current["database"] == "SQLite"
    assert last.observed.rejected["database"] == "Postgres"
    assert "undecided" in last.observed.superseded["database"]
    assert "30" in last.observed.superseded["lease_seconds"]
    assert last.observed.low["coffee_order"] == "black coffee"
    assert last.stable_drift == 0.0
    assert last.capsule_chars <= first.capsule_chars * 4
    assert max(report.latencies_ms) < 5_000


def test_selector_dropping_low_salience_is_visible_at_generation_fifty(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "selector.db")
    report = run_endurance(
        store,
        semantic_engine(include_low=False),
        generations=DEFAULT_GENERATIONS,
        checkpoints=CHECKPOINT_GENERATIONS,
        include_low=False,
        require_low=False,
        mode="lossy-selector",
    )
    assert report.passed, format_report(report)
    last = report.checkpoints[-1]
    assert last.observed is not None
    assert last.observed.current["database"] == "SQLite"
    assert last.observed.low == {}


def test_naive_concatenation_resurrects_rejected_and_superseded_state(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "naive.db")
    report = run_endurance(
        store,
        naive_engine(),
        generations=10,
        checkpoints=(1, 5, 10),
        include_low=True,
        require_low=True,
        mode="naive-concat",
    )
    gen10 = report.checkpoints[-1]
    failed = {probe.name: probe for probe in gen10.probes if not probe.passed}
    assert "structured_capsule" in failed
    assert "no_resurrection" in failed
    assert any("Postgres" in item or "undecided" in item or "30" in item for item in gen10.resurrection)
