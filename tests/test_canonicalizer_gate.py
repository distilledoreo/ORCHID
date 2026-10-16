from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from memory_gateway.canonicalizer_gate import (
    INSUFFICIENT_EVIDENCE,
    KEEP,
    REMOVE,
    evaluate_canonicalizer_gate,
    gate_from_semantic_eval,
)
from memory_gateway.config import RuntimeConfig

FROZEN_EVAL = (
    Path(__file__).resolve().parents[1]
    / "artifacts/agent_benchmarks/freetoshop_direct_consolidation/semantic_eval/SUMMARY.json"
)


def test_frozen_bypass_full_trace_does_not_authorize_removal() -> None:
    gate = gate_from_semantic_eval(FROZEN_EVAL)
    assert gate.verdict == KEEP
    assert gate.may_disable_default is False
    assert any("no canonicalizer-on baseline" in reason for reason in gate.reasons)
    canonicalizer_field = next(
        field for field in fields(RuntimeConfig) if field.name == "canonicalizer_enabled"
    )
    assert canonicalizer_field.default is True


def test_remove_requires_non_inferior_paired_baseline() -> None:
    bypass = {
        "arm": "ARM_B_NO_CANONICALIZER",
        "semantic_checks": 100,
        "semantic_pass": 100,
        "semantic_fail": 0,
        "current_fact_losses": 0,
        "resurrection_failures": 0,
        "invention_failures": 0,
        "intent_or_blocker_losses": 1,
    }
    baseline = {
        "arm": "ARM_A_WITH_CANONICALIZER",
        "semantic_checks": 100,
        "semantic_pass": 98,
        "semantic_fail": 2,
        "current_fact_losses": 1,
        "resurrection_failures": 0,
        "invention_failures": 0,
        "intent_or_blocker_losses": 2,
    }
    gate = evaluate_canonicalizer_gate(
        bypass_eval=bypass,
        canonicalizer_on_eval=baseline,
    )
    assert gate.verdict == REMOVE
    assert gate.may_disable_default is True


def test_worse_bypass_keeps_canonicalizer() -> None:
    bypass = {
        "arm": "BYPASS",
        "semantic_checks": 10,
        "current_fact_losses": 3,
        "resurrection_failures": 0,
        "invention_failures": 0,
        "intent_or_blocker_losses": 0,
    }
    baseline = {
        "arm": "WITH_CANONICALIZER",
        "semantic_checks": 10,
        "current_fact_losses": 0,
        "resurrection_failures": 0,
        "invention_failures": 0,
        "intent_or_blocker_losses": 0,
    }
    gate = evaluate_canonicalizer_gate(
        bypass_eval=bypass,
        canonicalizer_on_eval=baseline,
    )
    assert gate.verdict == KEEP


def test_incomplete_bypass_is_insufficient() -> None:
    gate = evaluate_canonicalizer_gate(
        bypass_eval={"arm": "ARM_C_RAW_SOLAR", "status": "NOT_EVALUATED", "semantic_checks": 0}
    )
    assert gate.verdict == INSUFFICIENT_EVIDENCE
    assert json.dumps(gate.reasons)
