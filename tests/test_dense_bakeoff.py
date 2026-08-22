from __future__ import annotations

import pytest

for _dependency in ("numpy", "onnxruntime", "tokenizers", "huggingface_hub"):
    pytest.importorskip(_dependency)

from tests.cold_memory_phase2_2_representation_bakeoff import _primary_metric


def test_bakeoff_gate_excludes_precision_below_target():
    result = _primary_metric(
        {
            "accept_count": 7,
            "accept_precision": 0.985,
            "accept_recall": 0.40,
        },
        target_precision=0.99,
    )

    assert result["eligible"] is False
    assert result["holdout_accept_recall"] is None


def test_bakeoff_gate_reports_recall_for_precision_eligible_model():
    result = _primary_metric(
        {
            "accept_count": 10,
            "accept_precision": 0.99,
            "accept_recall": 0.35,
        },
        target_precision=0.99,
    )

    assert result["eligible"] is True
    assert result["holdout_accept_recall"] == 0.35
