"""Frozen semantic gate for keeping, bypassing, or removing the canonicalizer.

Removal is allowed only when a full-trace bypass arm is non-inferior to a
canonicalizer-on baseline on current-fact loss, resurrection, invention, and
intent/blocker loss. Speed-only pilots are not sufficient.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KEEP = "KEEP"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
REMOVE = "REMOVE"

_LOSS_FIELDS = (
    "current_fact_losses",
    "resurrection_failures",
    "invention_failures",
    "intent_or_blocker_losses",
)


@dataclass(frozen=True)
class CanonicalizerGate:
    verdict: str
    reasons: tuple[str, ...]
    bypass_eval: dict[str, Any] | None
    canonicalizer_on_eval: dict[str, Any] | None

    @property
    def may_disable_default(self) -> bool:
        return self.verdict == REMOVE


def _completed(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("status") in {"NOT_EVALUATED", "FAILED"}:
        return False
    checks = row.get("semantic_checks")
    return isinstance(checks, int) and checks > 0


def _int_field(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    return int(value) if isinstance(value, (int, float)) else None


def evaluate_canonicalizer_gate(
    *,
    bypass_eval: dict[str, Any] | None,
    canonicalizer_on_eval: dict[str, Any] | None = None,
) -> CanonicalizerGate:
    """Decide whether the canonicalizer may be removed from the default path."""

    reasons: list[str] = []
    if not _completed(bypass_eval):
        reasons.append("bypass arm has no completed full-trace semantic evaluation")
        return CanonicalizerGate(
            INSUFFICIENT_EVIDENCE,
            tuple(reasons),
            bypass_eval,
            canonicalizer_on_eval,
        )
    assert bypass_eval is not None
    if not _completed(canonicalizer_on_eval):
        losses = [
            field
            for field in _LOSS_FIELDS
            if (_int_field(bypass_eval, field) or 0) > 0
        ]
        fails = _int_field(bypass_eval, "semantic_fail") or 0
        if losses or fails:
            reasons.append(
                "bypass completed with semantic losses but there is no "
                "canonicalizer-on baseline for non-inferiority"
            )
            reasons.extend(f"bypass {field}={bypass_eval.get(field)}" for field in losses)
            if fails:
                reasons.append(
                    f"bypass semantic_fail={fails} / semantic_pass="
                    f"{bypass_eval.get('semantic_pass')}"
                )
            return CanonicalizerGate(
                KEEP,
                tuple(reasons),
                bypass_eval,
                canonicalizer_on_eval,
            )
        reasons.append(
            "bypass completed without recorded losses, but removal still "
            "requires a paired canonicalizer-on baseline"
        )
        return CanonicalizerGate(
            INSUFFICIENT_EVIDENCE,
            tuple(reasons),
            bypass_eval,
            canonicalizer_on_eval,
        )
    assert canonicalizer_on_eval is not None
    worse: list[str] = []
    for field in _LOSS_FIELDS:
        bypass_value = _int_field(bypass_eval, field)
        baseline_value = _int_field(canonicalizer_on_eval, field)
        if bypass_value is None or baseline_value is None:
            reasons.append(f"missing {field} on one or both arms")
            return CanonicalizerGate(
                INSUFFICIENT_EVIDENCE,
                tuple(reasons),
                bypass_eval,
                canonicalizer_on_eval,
            )
        if bypass_value > baseline_value:
            worse.append(f"{field}: bypass {bypass_value} > baseline {baseline_value}")
    if worse:
        reasons.append("bypass is inferior to canonicalizer-on on semantic losses")
        reasons.extend(worse)
        return CanonicalizerGate(
            KEEP,
            tuple(reasons),
            bypass_eval,
            canonicalizer_on_eval,
        )
    reasons.append(
        "bypass is non-inferior to canonicalizer-on on current-fact, "
        "resurrection, invention, and intent/blocker losses"
    )
    return CanonicalizerGate(
        REMOVE,
        tuple(reasons),
        bypass_eval,
        canonicalizer_on_eval,
    )


def load_eval_rows(path: Path) -> tuple[dict[str, Any], ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return (payload,)
    if isinstance(payload, list):
        return tuple(row for row in payload if isinstance(row, dict))
    return ()


def gate_from_semantic_eval(path: Path) -> CanonicalizerGate:
    rows = load_eval_rows(path)
    bypass = next(
        (
            row
            for row in rows
            if "NO_CANONICALIZER" in str(row.get("arm") or "").upper()
            or "SELECTOR_SOLAR" in str(row.get("arm") or "").upper()
            or "BYPASS" in str(row.get("arm") or "").upper()
        ),
        None,
    )
    canonicalizer_on = next(
        (
            row
            for row in rows
            if "CURRENT" in str(row.get("arm") or "").upper()
            or "WITH_CANONICALIZER" in str(row.get("arm") or "").upper()
            or str(row.get("arm") or "").upper() == "ARM_A"
        ),
        None,
    )
    return evaluate_canonicalizer_gate(
        bypass_eval=bypass,
        canonicalizer_on_eval=canonicalizer_on,
    )
