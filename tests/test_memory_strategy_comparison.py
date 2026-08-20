"""Offline tests for memory strategy comparison helpers."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_ROOT = _TESTS.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from endurance_harness import THREAD_ID, expected_state_after  # noqa: E402
from memory_strategy_comparison import (  # noqa: E402
    CHECKPOINT_GENERATIONS,
    RAW_TAIL_MINIMUM_TOKENS,
    RAW_TAIL_TARGET_TOKENS,
    build_traditional_user_prompt,
    classify_recall_answers,
    compute_full_raw_metrics,
    generate_raw_only_audit,
    render_events,
    score_summary_semantics,
    select_tail,
    token_count_events,
    traditional_prompt_bundle_hash,
)
from residency_shadow_chained import open_connections  # noqa: E402


def test_traditional_prompt_bundle_is_stable() -> None:
    first = traditional_prompt_bundle_hash()
    second = traditional_prompt_bundle_hash()
    assert first == second
    assert len(first) == 64


def test_raw_only_audit_finds_only_churn() -> None:
    audit = generate_raw_only_audit()
    assert audit["raw_only_group_count"] == 595
    assert audit["retire_entry_count"] == 3
    assert audit["arguable_durable_but_cold_groups"] == 0
    assert audit["obvious_durable_lesson_groups"] == 0
    assert audit["bucket_counts"]["padding"] == 199
    assert audit["bucket_counts"]["filler_chatter"] == 196
    assert sum(audit["bucket_counts"].values()) == 595


def test_full_raw_metrics_monotonic_through_200() -> None:
    conns = open_connections()
    rows = compute_full_raw_metrics(conns)
    assert len(rows) == 200
    tokens = [row["raw_history_tokens"] for row in rows]
    assert tokens == sorted(tokens)
    assert tokens[-1] > 50_000


def test_tail_policy_matches_endurance_harness_budget() -> None:
    assert RAW_TAIL_TARGET_TOKENS == 80
    assert RAW_TAIL_MINIMUM_TOKENS == 1
    conns = open_connections()
    rows = compute_full_raw_metrics(conns)
    gen50 = next(row for row in rows if row["generation"] == 50)
    conn = sqlite3.connect(_ROOT / "data" / "live_endurance_degradation.db")
    conn.row_factory = sqlite3.Row
    end = conn.execute(
        "SELECT snapshot_end_event_id FROM compaction_jobs WHERE generation = 50 AND status = 'PROMOTED'"
    ).fetchone()
    end_seq = conn.execute(
        "SELECT sequence FROM events WHERE id = ?", (end[0],)
    ).fetchone()[0]
    events = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM events WHERE thread_id = ? AND sequence <= ? ORDER BY sequence",
            (THREAD_ID, end_seq),
        )
    ]
    tail = select_tail(events)
    tail_tokens = token_count_events(tail)
    assert 1 <= tail_tokens <= RAW_TAIL_TARGET_TOKENS + 40


def test_score_summary_semantics_detects_missing_current_fact() -> None:
    summary = "Project exists."
    result = score_summary_semantics(summary, generation=20)
    assert result["current_fact_loss"] is True


def test_classify_recall_answers_handles_null_expected() -> None:
    expected = expected_state_after(5)
    answers = {f"q{i}": None for i in range(1, 13)}
    answers["q1"] = "900 seconds"
    result = classify_recall_answers(expected, answers)
    assert result["correct_count"] >= 1


def test_checkpoint_schedule_matches_shadow_run() -> None:
    assert CHECKPOINT_GENERATIONS == (1, 10, 25, 50, 75, 100, 125, 150, 175, 200)


def test_traditional_prompt_builder_includes_previous_and_new_events() -> None:
    prompt = build_traditional_user_prompt(
        "old summary",
        [{"id": "evt_x", "sequence": 1, "role": "user", "content": "hello"}],
    )
    assert "PREVIOUS SUMMARY" in prompt
    assert "NEW EVENTS" in prompt
    assert "old summary" in prompt
    assert "evt_x" in prompt
