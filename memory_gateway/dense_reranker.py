"""Offline cross-encoder reranking primitives for Phase 2.4."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

@dataclass(frozen=True)
class RerankerCandidate:
    memory_id: str
    dense_score: float
    reranker_score: float


class OnnxCrossEncoderReranker:
    """Score query/memory pairs with a local ONNX cross-encoder."""

    def __init__(
        self,
        *,
        model_path: str | Path,
        tokenizer_path: str | Path,
        max_length: int = 256,
        providers: list[str] | None = None,
    ) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if max_length <= 0:
            raise ValueError("max_length must be positive")
        self.model_path = str(model_path)
        self.tokenizer_path = str(tokenizer_path)
        self.max_length = max_length
        self.tokenizer = Tokenizer.from_file(self.tokenizer_path)
        self.tokenizer.enable_truncation(max_length=max_length)
        self.tokenizer.enable_padding()
        self.session = ort.InferenceSession(
            self.model_path,
            providers=providers or ["CPUExecutionProvider"],
        )
        self.output_name = self.session.get_outputs()[0].name
        self.input_names = tuple(item.name for item in self.session.get_inputs())

    def score_pairs(self, pairs: Iterable[tuple[str, str]]) -> np.ndarray:
        import numpy as np

        values = [(str(query), str(memory)) for query, memory in pairs]
        if not values:
            return np.empty((0,), dtype=np.float32)
        encoded = self.tokenizer.encode_batch(values)
        fields = {
            "input_ids": np.asarray([item.ids for item in encoded], dtype=np.int64),
            "attention_mask": np.asarray(
                [item.attention_mask for item in encoded], dtype=np.int64
            ),
            "token_type_ids": np.asarray(
                [item.type_ids for item in encoded], dtype=np.int64
            ),
        }
        inputs = {
            name: fields[name]
            for name in self.input_names
            if name in fields
        }
        outputs = self.session.run([self.output_name], inputs)[0]
        return np.asarray(outputs, dtype=np.float32).reshape(-1)


@dataclass(frozen=True)
class RerankerPolicy:
    accept_min_score: float
    accept_min_margin: float
    ambiguous_min_score: float
    name: str = "phase2_4_calibrated"

    def decide(
        self,
        *,
        top1_score: float | None,
        margin: float,
        candidate_count: int,
    ) -> str:
        if candidate_count <= 0 or top1_score is None:
            return "ABSTAIN"
        if (
            top1_score >= self.accept_min_score
            and margin >= self.accept_min_margin
        ):
            return "ACCEPT"
        if top1_score >= self.ambiguous_min_score:
            return "AMBIGUOUS"
        return "ABSTAIN"


def _accepted(rows: list[dict], score: float, margin: float) -> list[dict]:
    return [
        row
        for row in rows
        if row.get("reranker_evaluated")
        and row.get("reranker_candidate_count", 0) > 0
        and float(row["reranker_top1_score"]) >= score
        and float(row["reranker_margin"]) >= margin
    ]


def calibrate_reranker_policy(
    rows: list[dict],
    *,
    target_precision: float = 0.99,
) -> dict:
    """Choose score/margin thresholds using calibration rows only."""

    usable = [
        row
        for row in rows
        if row.get("reranker_evaluated")
        and row.get("reranker_candidate_count", 0) > 0
    ]
    scores = sorted({round(float(row["reranker_top1_score"]), 8) for row in usable})
    margins = sorted({round(float(row["reranker_margin"]), 8) for row in usable})
    if not scores:
        policy = RerankerPolicy(1.0, 0.0, 1.0)
        return {
            "policy": policy.__dict__,
            "target_precision": target_precision,
            "target_met_on_calibration": False,
            "grid_size": 0,
            "accepted_count": 0,
            "true_accept_count": 0,
            "false_accept_count": 0,
            "accept_precision": 0.0,
            "accept_recall": 0.0,
            "positive_count": sum(bool(row["expected_ids"]) for row in rows),
        }
    score_thresholds = sorted(set(scores + [0.0]))
    margin_thresholds = sorted(set(margins + [0.0]))
    positive_count = sum(bool(row["expected_ids"]) for row in rows)
    target_candidates: list[tuple[float, float, int, int, float]] = []
    all_candidates: list[tuple[float, float, int, int, float]] = []
    for score, margin in itertools.product(score_thresholds, margin_thresholds):
        accepted = _accepted(rows, score, margin)
        true_accepts = sum(
            bool(set(row["expected_ids"]) & {row.get("reranker_top1_id")})
            for row in accepted
        )
        false_accepts = len(accepted) - true_accepts
        precision = true_accepts / len(accepted) if accepted else 0.0
        candidate = (score, margin, true_accepts, false_accepts, precision)
        if accepted:
            all_candidates.append(candidate)
            if precision >= target_precision:
                target_candidates.append(candidate)
    selected_from_target = bool(target_candidates)
    candidates = target_candidates or all_candidates
    if candidates:
        if selected_from_target:
            selected = max(candidates, key=lambda item: (item[2], item[4], item[0], item[1]))
        else:
            selected = max(candidates, key=lambda item: (item[4], item[2], item[0], item[1]))
        score, margin, true_accepts, false_accepts, precision = selected
    else:
        score, margin, true_accepts, false_accepts, precision = (max(scores), 0.0, 0, 0, 0.0)
    ambiguous_min_score = min(scores)
    accepted_count = true_accepts + false_accepts
    policy = RerankerPolicy(
        accept_min_score=score,
        accept_min_margin=margin,
        ambiguous_min_score=ambiguous_min_score,
    )
    return {
        "policy": policy.__dict__,
        "target_precision": target_precision,
        "target_met_on_calibration": selected_from_target,
        "grid_size": len(score_thresholds) * len(margin_thresholds),
        "accepted_count": accepted_count,
        "true_accept_count": true_accepts,
        "false_accept_count": false_accepts,
        "accept_precision": precision,
        "accept_recall": true_accepts / positive_count if positive_count else 0.0,
        "positive_count": positive_count,
    }


def apply_reranker_policy(rows: list[dict], policy: RerankerPolicy) -> None:
    for row in rows:
        if not row.get("reranker_evaluated"):
            row["decision"] = "LEXICAL_BYPASS"
            row["accepted_expected"] = False
            row["false_accept"] = False
            continue
        row["decision"] = policy.decide(
            top1_score=row.get("reranker_top1_score"),
            margin=float(row.get("reranker_margin", 0.0)),
            candidate_count=int(row.get("reranker_candidate_count", 0)),
        )
        row["accepted_expected"] = bool(
            row["decision"] == "ACCEPT"
            and set(row["expected_ids"]) & {row.get("reranker_top1_id")}
        )
        row["false_accept"] = bool(
            row["decision"] == "ACCEPT" and not row["accepted_expected"]
        )
