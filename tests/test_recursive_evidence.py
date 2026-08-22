from __future__ import annotations

import json

from tools.run_recursive_evidence import (
    aggregate_misses,
    final_semantic_counts,
    numeric_summary,
    semantic_counts,
)


def test_semantic_counts_and_miss_aggregation(tmp_path):
    output = tmp_path / "run"
    output.mkdir()
    rows = [
        {
            "generation": 0,
            "checkpoint": "intent-start",
            "check_id": "spot-002",
            "category": "CURRENT_INTENT_PRESERVATION",
            "status": "PASS",
        },
        {
            "generation": 1,
            "checkpoint": "architecture-baseline",
            "check_id": "spot-006",
            "category": "CURRENT_FACT_PRESERVATION",
            "status": "FAIL",
            "missing_all": [],
            "missing_any": ["bounded", "region-local"],
        },
    ]
    (output / "semantic_eval.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    assert semantic_counts(output) == {"evaluated": 2, "pass": 1, "fail": 1}
    assert final_semantic_counts(output) == {"generation": 1, "evaluated": 1, "pass": 0, "fail": 1}
    assert aggregate_misses([{"output": str(output)}]) == {
        "by_category": {"CURRENT_FACT_PRESERVATION": 1},
        "by_checkpoint": {"architecture-baseline": 1},
        "by_check": {
            "spot-006": {
                "count": 1,
                "category": "CURRENT_FACT_PRESERVATION",
                "checkpoint": "architecture-baseline",
                "missing_terms": {"bounded": 1, "region-local": 1},
            }
        },
        "missing_terms": {"bounded": 1, "region-local": 1},
    }


def test_numeric_summary_ignores_missing_values():
    assert numeric_summary([{"value": 3}, {"value": None}, {"value": 5}], "value") == {
        "median": 4.0,
        "min": 3.0,
        "max": 5.0,
    }
