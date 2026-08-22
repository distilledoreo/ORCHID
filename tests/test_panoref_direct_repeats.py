from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from run_panoref_direct_repeats import budget_instruction, miss_summary  # noqa: E402


def test_budget_instruction_keeps_direct_experiment_policy() -> None:
    prompt = budget_instruction(1000)
    assert "approximately 1000 estimated tokens" in prompt
    assert "compress aggressively" in prompt
    assert "unresolved blockers" in prompt
    assert "Return only the replacement capsule" in prompt


def test_miss_summary_aggregates_semantic_failures_by_check(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    rows = [
        {
            "check_id": "spot-001",
            "category": "CURRENT_FACT_PRESERVATION",
            "status": "FAIL",
            "missing_any": ["allowed floor"],
        },
        {
            "check_id": "spot-002",
            "category": "BLOCKER_PRESERVATION",
            "status": "PASS",
            "missing_any": [],
        },
    ]
    (first / "semantic_eval.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (second / "semantic_eval.jsonl").write_text(
        json.dumps({
            "check_id": "spot-001",
            "category": "CURRENT_FACT_PRESERVATION",
            "status": "FAIL",
            "missing_any": ["allowed floor"],
        }) + "\n",
        encoding="utf-8",
    )

    summary = miss_summary([{"output": str(first)}, {"output": str(second)}])

    assert summary["by_check"] == {"spot-001": 2}
    assert summary["by_category"] == {"CURRENT_FACT_PRESERVATION": 2}
    assert summary["missing_terms"] == {"allowed floor": 2}


def test_miss_summary_can_exclude_partial_prefix_runs(tmp_path: Path) -> None:
    complete = tmp_path / "complete"
    partial = tmp_path / "partial"
    complete.mkdir()
    partial.mkdir()
    row = {
        "generation": 4,
        "check_id": "spot-001",
        "category": "CURRENT_FACT_PRESERVATION",
        "status": "FAIL",
        "missing_any": ["allowed floor"],
    }
    for path in (complete, partial):
        (path / "semantic_eval.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    summary = miss_summary(
        [
            {"output": str(complete), "full_trace_completed": True, "final_generation": 4},
            {"output": str(partial), "full_trace_completed": False},
        ],
        full_only=True,
    )

    assert summary["by_check"] == {"spot-001": 1}
