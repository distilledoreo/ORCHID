from __future__ import annotations

from memory_gateway.dense_reranker import (
    RerankerPolicy,
    apply_reranker_policy,
    calibrate_reranker_policy,
)


def _row(query_id: str, expected_ids: list[str], score: float, margin: float) -> dict:
    return {
        "query_id": query_id,
        "expected_ids": expected_ids,
        "reranker_evaluated": True,
        "reranker_candidate_count": 5,
        "reranker_top1_id": expected_ids[0] if expected_ids else "wrong",
        "reranker_top1_score": score,
        "reranker_margin": margin,
    }


def test_reranker_policy_has_accept_ambiguous_abstain_states():
    policy = RerankerPolicy(
        accept_min_score=0.8,
        accept_min_margin=0.1,
        ambiguous_min_score=0.4,
    )

    assert policy.decide(top1_score=0.9, margin=0.2, candidate_count=5) == "ACCEPT"
    assert policy.decide(top1_score=0.7, margin=0.2, candidate_count=5) == "AMBIGUOUS"
    assert policy.decide(top1_score=0.2, margin=0.2, candidate_count=5) == "ABSTAIN"
    assert policy.decide(top1_score=0.9, margin=0.2, candidate_count=0) == "ABSTAIN"


def test_reranker_calibration_chooses_a_precision_eligible_policy():
    rows = [
        _row("positive", ["mem_good"], 0.9, 0.2),
        _row("negative", [], 0.3, 0.1),
    ]

    result = calibrate_reranker_policy(rows, target_precision=0.99)

    assert result["target_met_on_calibration"] is True
    assert result["accept_precision"] == 1.0
    assert result["true_accept_count"] == 1


def test_lexical_bypass_is_not_counted_as_a_reranker_accept():
    rows = [
        {
            **_row("lexical", ["mem_good"], 0.9, 0.2),
            "reranker_evaluated": False,
            "fts_would_inject_ids": ["mem_good"],
        }
    ]

    apply_reranker_policy(
        rows,
        RerankerPolicy(accept_min_score=0.0, accept_min_margin=0.0, ambiguous_min_score=0.0),
    )

    assert rows[0]["decision"] == "LEXICAL_BYPASS"
    assert rows[0]["accepted_expected"] is False


def test_accepting_the_wrong_top1_is_a_false_accept():
    rows = [{
        **_row("wrong-top1", ["mem_expected"], 0.9, 0.2),
        "reranker_top1_id": "mem_wrong",
    }]

    apply_reranker_policy(
        rows,
        RerankerPolicy(accept_min_score=0.0, accept_min_margin=0.0, ambiguous_min_score=0.0),
    )

    assert rows[0]["decision"] == "ACCEPT"
    assert rows[0]["accepted_expected"] is False
    assert rows[0]["false_accept"] is True
