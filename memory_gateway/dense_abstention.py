"""Offline deterministic dense ACCEPT/AMBIGUOUS/ABSTAIN calibration tools.

This module is intentionally not imported by the gateway.  It contains only
feature extraction inputs and threshold calibration; it does not perform
embedding, fusion, reranking, context assembly, or injection.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Literal


DecisionState = Literal["ACCEPT", "AMBIGUOUS", "ABSTAIN"]


@dataclass(frozen=True)
class DenseAbstentionFeatures:
    top1_score: float = 0.0
    top2_score: float = 0.0
    margin: float = 0.0
    corpus_percentile: float = 0.0
    corpus_zscore: float = 0.0
    lexical_overlap: int = 0
    lexical_overlap_ratio: float = 0.0
    identifier_agreement: float = 0.0
    scope_agreement: float = 0.0
    activation_prior: float = 0.0
    candidate_count: int = 0


@dataclass(frozen=True)
class DenseAbstentionPolicy:
    """A deliberately small threshold policy for offline calibration."""

    name: str = "uncalibrated"
    accept_min_score: float = 0.50
    accept_min_margin: float = 0.05
    accept_min_percentile: float = 0.90
    accept_min_lexical_overlap: int = 0
    accept_min_identifier_agreement: float = 0.0
    accept_min_scope_agreement: float = 1.0
    ambiguous_min_score: float = 0.20
    ambiguous_min_percentile: float = 0.50

    def decide(self, features: DenseAbstentionFeatures) -> DecisionState:
        if features.candidate_count <= 0:
            return "ABSTAIN"
        if (
            features.top1_score >= self.accept_min_score
            and features.margin >= self.accept_min_margin
            and features.corpus_percentile >= self.accept_min_percentile
            and features.lexical_overlap >= self.accept_min_lexical_overlap
            and features.identifier_agreement >= self.accept_min_identifier_agreement
            and features.scope_agreement >= self.accept_min_scope_agreement
        ):
            return "ACCEPT"
        if (
            features.top1_score >= self.ambiguous_min_score
            and features.corpus_percentile >= self.ambiguous_min_percentile
        ):
            return "AMBIGUOUS"
        return "ABSTAIN"


def feature_dict(features: DenseAbstentionFeatures) -> dict[str, Any]:
    return {
        "top1_score": round(features.top1_score, 8),
        "top2_score": round(features.top2_score, 8),
        "margin": round(features.margin, 8),
        "corpus_percentile": round(features.corpus_percentile, 8),
        "corpus_zscore": round(features.corpus_zscore, 8),
        "lexical_overlap": features.lexical_overlap,
        "lexical_overlap_ratio": round(features.lexical_overlap_ratio, 8),
        "identifier_agreement": round(features.identifier_agreement, 8),
        "scope_agreement": round(features.scope_agreement, 8),
        "activation_prior": round(features.activation_prior, 8),
        "candidate_count": features.candidate_count,
    }


def _evaluate_policy(
    rows: Iterable[dict[str, Any]],
    policy: DenseAbstentionPolicy,
) -> dict[str, Any]:
    materialized = list(rows)
    decisions = [
        policy.decide(DenseAbstentionFeatures(**row["features"]))
        for row in materialized
    ]
    accepted = [
        row for row, decision in zip(materialized, decisions) if decision == "ACCEPT"
    ]
    positives = [row for row in materialized if row["expected_ids"]]
    true_accepts = sum(
        bool(row["expected_ids"]) for row in accepted
    )
    false_accepts = len(accepted) - true_accepts
    ambiguous = sum(decision == "AMBIGUOUS" for decision in decisions)
    abstained = sum(decision == "ABSTAIN" for decision in decisions)
    return {
        "policy": policy,
        "decisions": decisions,
        "query_count": len(materialized),
        "positive_count": len(positives),
        "accepted_count": len(accepted),
        "true_accept_count": true_accepts,
        "false_accept_count": false_accepts,
        "accept_precision": true_accepts / len(accepted) if accepted else 0.0,
        "accept_recall": true_accepts / len(positives) if positives else 0.0,
        "ambiguous_count": ambiguous,
        "abstain_count": abstained,
        "accept_rate": len(accepted) / len(materialized) if materialized else 0.0,
        "abstention_precision": (
            sum(not row["expected_ids"] for row, decision in zip(materialized, decisions) if decision != "ACCEPT")
            / sum(decision != "ACCEPT" for decision in decisions)
            if any(decision != "ACCEPT" for decision in decisions)
            else 0.0
        ),
    }


def evaluate_policy(
    rows: Iterable[dict[str, Any]],
    policy: DenseAbstentionPolicy,
) -> dict[str, Any]:
    """Evaluate one policy and return JSON-safe metrics."""

    result = _evaluate_policy(rows, policy)
    result["policy"] = {
        key: value
        for key, value in vars(result["policy"]).items()
    }
    return result


def calibrate_policy(
    rows: Iterable[dict[str, Any]],
    *,
    target_precision: float = 0.99,
) -> dict[str, Any]:
    """Select conservative thresholds using calibration rows only.

    The search is a finite, transparent grid.  It maximizes accepted true
    positives subject to the target precision, then prefers more accepted
    queries and less strict thresholds.  If the target is impossible, the
    returned policy maximizes precision first and records that the target was
    not met.
    """

    materialized = list(rows)
    score_grid = tuple(round(0.20 + index * 0.02, 2) for index in range(21))
    margin_grid = tuple(round(index * 0.02, 2) for index in range(11))
    percentile_grid = (0.0, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.98, 1.0)
    lexical_grid = (0, 1)
    identifier_grid = (0.0, 0.5)
    candidates: list[dict[str, Any]] = []
    for score in score_grid:
        for margin in margin_grid:
            for percentile in percentile_grid:
                for lexical_overlap in lexical_grid:
                    for identifier_agreement in identifier_grid:
                        policy = DenseAbstentionPolicy(
                            name="phase2_1_calibrated",
                            accept_min_score=score,
                            accept_min_margin=margin,
                            accept_min_percentile=percentile,
                            accept_min_lexical_overlap=lexical_overlap,
                            accept_min_identifier_agreement=identifier_agreement,
                        )
                        metrics = _evaluate_policy(materialized, policy)
                        candidates.append(metrics)

    eligible = [
        metrics
        for metrics in candidates
        if metrics["accept_precision"] >= target_precision
    ]
    if eligible:
        selected = max(
            eligible,
            key=lambda metrics: (
                metrics["true_accept_count"],
                metrics["accept_precision"],
                -metrics["policy"].accept_min_score,
                -metrics["policy"].accept_min_margin,
                -metrics["policy"].accept_min_percentile,
            ),
        )
        target_met = True
    else:
        selected = max(
            candidates,
            key=lambda metrics: (
                metrics["accept_precision"],
                metrics["true_accept_count"],
                -metrics["policy"].accept_min_score,
            ),
        )
        target_met = False
    result = evaluate_policy(materialized, selected["policy"])
    result["target_precision"] = target_precision
    result["target_met_on_calibration"] = target_met
    result["grid_size"] = len(candidates)
    result["eligible_policy_count"] = len(eligible)
    return result
