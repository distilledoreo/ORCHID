from __future__ import annotations

from memory_gateway.dense_abstention import (
    DenseAbstentionFeatures,
    DenseAbstentionPolicy,
    calibrate_policy,
)


def test_abstention_policy_has_three_explicit_states():
    policy = DenseAbstentionPolicy(
        accept_min_score=0.70,
        accept_min_margin=0.10,
        accept_min_percentile=0.90,
    )

    assert policy.decide(
        DenseAbstentionFeatures(
            top1_score=0.80,
            margin=0.20,
            corpus_percentile=0.99,
            scope_agreement=1.0,
            candidate_count=5,
        )
    ) == "ACCEPT"
    assert policy.decide(
        DenseAbstentionFeatures(
            top1_score=0.75,
            margin=0.02,
            corpus_percentile=0.99,
            scope_agreement=1.0,
            candidate_count=5,
        )
    ) == "AMBIGUOUS"
    assert policy.decide(
        DenseAbstentionFeatures(
            top1_score=0.10,
            margin=0.01,
            corpus_percentile=0.20,
            scope_agreement=1.0,
            candidate_count=5,
        )
    ) == "ABSTAIN"


def test_calibration_uses_only_rows_provided_and_reports_precision_target():
    rows = [
        {
            "expected_ids": ["mem_good"],
            "features": {
                "top1_score": 0.90,
                "top2_score": 0.20,
                "margin": 0.70,
                "corpus_percentile": 1.0,
                "corpus_zscore": 5.0,
                "lexical_overlap": 0,
                "lexical_overlap_ratio": 0.0,
                "identifier_agreement": 0.0,
                "scope_agreement": 1.0,
                "activation_prior": 0.8,
                "candidate_count": 5,
            },
        },
        {
            "expected_ids": [],
            "features": {
                "top1_score": 0.25,
                "top2_score": 0.24,
                "margin": 0.01,
                "corpus_percentile": 0.70,
                "corpus_zscore": 1.0,
                "lexical_overlap": 0,
                "lexical_overlap_ratio": 0.0,
                "identifier_agreement": 0.0,
                "scope_agreement": 1.0,
                "activation_prior": 0.2,
                "candidate_count": 5,
            },
        },
    ]

    result = calibrate_policy(rows, target_precision=1.0)

    assert result["target_met_on_calibration"] is True
    assert result["accept_precision"] == 1.0
    assert result["true_accept_count"] == 1
