from __future__ import annotations

import pytest

from memory_gateway.dense_eligibility import (
    filter_dense_candidates,
    is_dense_memory_eligible,
)
from memory_gateway.dense_experiment import DenseMemoryCandidate


PROJECT_ID = "project"
THREAD_ID = "thread"


def _memory(memory_id: str, status: str, **scope: str) -> dict[str, str]:
    return {
        "id": memory_id,
        "status": status,
        "project_id": scope.get("project_id", PROJECT_ID),
        "thread_id": scope.get("thread_id", THREAD_ID),
    }


def test_implicit_dense_eligibility_excludes_superseded_and_wrong_scope():
    assert is_dense_memory_eligible(
        _memory("active", "ACTIVE"),
        project_id=PROJECT_ID,
        thread_id=THREAD_ID,
    )
    assert not is_dense_memory_eligible(
        _memory("old", "SUPERSEDED"),
        project_id=PROJECT_ID,
        thread_id=THREAD_ID,
    )
    assert not is_dense_memory_eligible(
        _memory("other", "ACTIVE", project_id="other"),
        project_id=PROJECT_ID,
        thread_id=THREAD_ID,
    )


def test_explicit_dense_search_keeps_superseded_memories_searchable():
    memories = {
        "active": _memory("active", "ACTIVE"),
        "old": _memory("old", "SUPERSEDED"),
        "disabled": _memory("disabled", "DISABLED"),
    }
    candidates = tuple(
        DenseMemoryCandidate(memory_id=memory_id, score=score)
        for memory_id, score in (("old", 0.9), ("active", 0.8), ("disabled", 0.7))
    )

    implicit = filter_dense_candidates(
        candidates,
        memories,
        project_id=PROJECT_ID,
        thread_id=THREAD_ID,
        mode="implicit",
    )
    explicit = filter_dense_candidates(
        candidates,
        memories,
        project_id=PROJECT_ID,
        thread_id=THREAD_ID,
        mode="explicit",
    )

    assert [candidate.memory_id for candidate in implicit] == ["active"]
    assert [candidate.memory_id for candidate in explicit] == ["old", "active"]


def test_phase23_expansion_keeps_base_calibration_and_adds_status_calibration():
    for dependency in ("numpy", "onnxruntime", "tokenizers", "huggingface_hub"):
        pytest.importorskip(dependency)
    from tests.cold_memory_phase2_3_status_eligibility import _expand_holdout

    base = [
        {
            "id": f"cal_{index}",
            "category": "base_calibration",
            "query": "calibration",
            "expected_ids": [],
            "split": "calibration",
        }
        for index in range(151)
    ]
    base.extend(
        {
            "id": f"positive_{index}",
            "category": "clear_semantic_positive",
            "query": f"positive {index}",
            "expected_ids": ["mem_lease_renewal"],
            "split": "holdout",
        }
        for index in range(50)
    )
    for category in ("near_miss_negative", "wrong_fact_negative", "no_cold_memory_negative"):
        base.extend(
            {
                "id": f"{category}_{index}",
                "category": category,
                "query": f"{category} {index}",
                "expected_ids": [],
                "split": "holdout",
            }
            for index in range(25)
        )
    base.extend(
        {
            "id": f"superseded_{index}",
            "category": "superseded_negative",
            "query": f"superseded {index}",
            "expected_ids": [],
            "split": "holdout",
        }
        for index in range(25)
    )
    base.extend(
        {
            "id": f"guardrail_{index}",
            "category": "lexical_guardrail",
            "query": f"guardrail {index}",
            "expected_ids": [],
            "split": "external_guardrail",
        }
        for index in range(18)
    )

    expanded = _expand_holdout(base)

    assert sum(row["split"] == "calibration" for row in expanded) == 176
    assert sum(row["split"] == "holdout" for row in expanded) == 300
    assert sum(
        row["category"] == "superseded_status_negative"
        and row["split"] == "calibration"
        for row in expanded
    ) == 25
