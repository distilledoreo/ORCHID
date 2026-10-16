from __future__ import annotations

from pathlib import Path

from memory_gateway.benchmark_scorecard import generate_scorecard
from memory_gateway.pareto_release import (
    evaluate_paired_release,
    evaluate_single_run,
    extract_run_metrics,
    quality_noninferior,
)

_PI_QUALITY = {
    "arm": "PI_NATIVE",
    "assistant_messages": 1,
    "files_changed": 1,
    "hidden_exit": 1,
    "typecheck_exit": 0,
    "build_exit": 0,
    "tool_failures": 0,
}
_ORCHID_QUALITY = {
    "arm": "ORCHID",
    "assistant_messages": 2,
    "files_changed": 2,
    "hidden_exit": 0,
    "typecheck_exit": 0,
    "build_exit": 0,
    "tool_failures": 0,
}


REFERENCE = (
    Path(__file__).resolve().parents[1]
    / "artifacts/agent_benchmarks/freetoshop_pi_vs_orchid_corrected_all_solar"
)


def _synthetic_scorecard(
    *,
    pi_cost: float,
    orchid_cost: float,
    pi_prompt: float,
    orchid_prompt: float,
    pi_peak: float,
    orchid_peak: float,
    pi_latency_median: float,
    orchid_latency_median: float,
    pi_latency_p95: float,
    orchid_latency_p95: float,
    pi_quality: dict,
    orchid_quality: dict,
) -> dict:
    return {
        "validity": {"valid": True, "reasons": []},
        "run_id": "synthetic",
        "artifact": "/tmp/synthetic",
        "economics": {
            "pi_foreground": {
                "calculated_cost_usd": pi_cost,
                "input_tokens": pi_prompt,
                "largest_context_tokens": pi_peak,
                "foreground_latency_ms": {
                    "median": pi_latency_median,
                    "p95": pi_latency_p95,
                },
            },
            "orchid_foreground": {
                "input_tokens": orchid_prompt,
                "largest_context_tokens": orchid_peak,
                "foreground_latency_ms": {
                    "median": orchid_latency_median,
                    "p95": orchid_latency_p95,
                },
            },
            "orchid_total_provider_equivalent_cost_usd": orchid_cost,
        },
        "quality": {
            "milestones": [pi_quality, orchid_quality],
            "hidden_eval": {
                "evaluator": "sealed-hidden-v1",
                "oracle_commit": "abc123",
                "source_sha256": "def456",
                "arms": {
                    "PI_NATIVE": {
                        "exit_code": pi_quality.get("hidden_exit", 1),
                        "timed_out": False,
                    },
                    "ORCHID": {
                        "exit_code": orchid_quality.get("hidden_exit", 1),
                        "timed_out": False,
                    },
                },
            },
        },
        "foreground_substitution": {"present": True},
        "direct_foreground_latency": {
            "pi_native": {
                "present": True,
                "provider_wall_ms": {
                    "median": pi_latency_median,
                    "p95": pi_latency_p95,
                    "samples": 1,
                },
            },
            "orchid": {
                "present": True,
                "provider_wall_ms": {
                    "median": orchid_latency_median,
                    "p95": orchid_latency_p95,
                    "samples": 1,
                },
            },
        },
        "config_orchid": {"memory_format": "v2"},
        "selector_gpu": {"peak_vram_fraction": 0.5},
        "paired_vram": {
            "present": True,
            "complete": True,
            "arm_isolated": True,
            "pi_native": {
                "total": {"peak_bytes": 1_000, "byte_seconds": 10_000}
            },
            "orchid": {
                "selector_peak_fraction": 0.5,
                "total": {"peak_bytes": 800, "byte_seconds": 8_000},
            },
        },
    }


def test_quality_noninferior_passes_when_orchid_matches_or_beats_pi() -> None:
    metrics = extract_run_metrics(
        _synthetic_scorecard(
            pi_cost=1.0,
            orchid_cost=0.5,
            pi_prompt=1000,
            orchid_prompt=700,
            pi_peak=10000,
            orchid_peak=8000,
            pi_latency_median=1000,
            orchid_latency_median=900,
            pi_latency_p95=2000,
            orchid_latency_p95=2100,
            pi_quality={
                "arm": "PI_NATIVE",
                "assistant_messages": 40,
                "files_changed": 4,
                "hidden_exit": 0,
                "typecheck_exit": 0,
                "build_exit": 0,
                "tool_failures": 3,
            },
            orchid_quality={
                "arm": "ORCHID",
                "assistant_messages": 45,
                "files_changed": 5,
                "hidden_exit": 0,
                "typecheck_exit": 0,
                "build_exit": 0,
                "tool_failures": 2,
            },
        )
    )
    ok, reasons = quality_noninferior(metrics)
    assert ok, reasons


def test_evaluate_single_run_flags_cost_and_prompt_savings() -> None:
    scorecard = _synthetic_scorecard(
        pi_cost=1.0,
        orchid_cost=0.91,
        pi_prompt=1000,
        orchid_prompt=900,
        pi_peak=10000,
        orchid_peak=8000,
        pi_latency_median=1000,
        orchid_latency_median=900,
        pi_latency_p95=2000,
        orchid_latency_p95=2100,
        pi_quality=_PI_QUALITY,
        orchid_quality=_ORCHID_QUALITY,
    )
    result = evaluate_single_run(scorecard)
    assert result["checks"]["api_cost_savings"] is False
    assert result["checks"]["prompt_kv_savings"] is False
    assert result["checks"]["peak_context_savings"] is True


def test_quality_gate_rejects_equal_hidden_failures_and_activity_proxies() -> None:
    scorecard = _synthetic_scorecard(
        pi_cost=1.0,
        orchid_cost=0.5,
        pi_prompt=1_000,
        orchid_prompt=700,
        pi_peak=10_000,
        orchid_peak=8_000,
        pi_latency_median=1_000,
        orchid_latency_median=900,
        pi_latency_p95=2_000,
        orchid_latency_p95=1_900,
        pi_quality={**_PI_QUALITY, "hidden_exit": 1, "assistant_messages": 1},
        orchid_quality={
            **_ORCHID_QUALITY,
            "hidden_exit": 1,
            "assistant_messages": 999,
            "files_changed": 999,
        },
    )
    metrics = extract_run_metrics(scorecard)
    ok, reasons = quality_noninferior(metrics)
    assert ok is False
    assert any("orchid_hidden_functional_pass" in reason for reason in reasons)


def test_evaluate_single_run_requires_substitution_and_v2_memory_format() -> None:
    scorecard = _synthetic_scorecard(
        pi_cost=1.0,
        orchid_cost=0.5,
        pi_prompt=1000,
        orchid_prompt=700,
        pi_peak=10000,
        orchid_peak=8000,
        pi_latency_median=1000,
        orchid_latency_median=900,
        pi_latency_p95=2000,
        orchid_latency_p95=2100,
        pi_quality=_PI_QUALITY,
        orchid_quality=_ORCHID_QUALITY,
    )
    scorecard["foreground_substitution"] = {"present": False}
    scorecard["config_orchid"] = {"memory_format": "v1"}
    result = evaluate_single_run(scorecard)
    assert result["checks"]["foreground_substitution_present"] is False
    assert result["checks"]["memory_format_v2"] is False


def test_evaluate_paired_release_requires_three_runs() -> None:
    report = evaluate_paired_release([REFERENCE])
    assert report["release_pass"] is False
    assert "need 3 paired runs" in report["reasons"][0]


def test_reference_artifact_scorecard_metrics_are_extractable() -> None:
    assert REFERENCE.exists()
    scorecard = generate_scorecard(REFERENCE, require_hidden_eval=False)
    metrics = extract_run_metrics(scorecard, artifact=REFERENCE)
    assert metrics["pi_prompt_tokens"] > 0
    assert metrics["promotions"] >= 3
    assert metrics["post_final_promotion_turns"] >= 0


def test_benchmark_memory_format_defaults_to_v1_until_gate_passes(tmp_path: Path) -> None:
    from memory_gateway.pareto_release import benchmark_memory_format, consolidator_live_gate_status

    report = tmp_path / "gate.json"
    assert benchmark_memory_format(report_path=report) == "v1"
    assert consolidator_live_gate_status(report).get("ready") is False

    report.write_text(
        '{"generation_count":32,"accepted":32,"rejected":0}',
        encoding="utf-8",
    )
    assert consolidator_live_gate_status(report).get("ready") is True
    assert benchmark_memory_format(report_path=report) == "v2"
    assert benchmark_memory_format(explicit="v2_shadow", report_path=report) == "v2_shadow"


def test_consolidator_live_gate_status_flags_stale_partial_probe(tmp_path: Path) -> None:
    from memory_gateway.pareto_release import consolidator_live_gate_status

    report = tmp_path / "gate.json"
    report.write_text(
        '{"generation_count":3,"accepted":0,"rejected":3}',
        encoding="utf-8",
    )
    status = consolidator_live_gate_status(report)
    assert status["ready"] is False
    assert status["reason"] == "stale_partial_probe"


def test_pareto_prerequisite_report_flags_invalid_key_and_incomplete_gate(
    tmp_path: Path,
) -> None:
    from memory_gateway.pareto_release import pareto_prerequisite_report

    (tmp_path / "artifacts/selector_contract").mkdir(parents=True)
    (tmp_path / "artifacts/selector_contract/V2_CONSOLIDATOR_GATE_REPORT.json").write_text(
        '{"generation_count":32,"accepted":10,"rejected":22}',
        encoding="utf-8",
    )
    report = pareto_prerequisite_report(tmp_path, api_key="sk-test-key")
    assert report["ready"] is False
    assert not report["checks"]["provider_profile"]
    assert not report["checks"]["consolidator_live_gate"]
    assert report["benchmark_memory_format"] == "v1"
    assert any("setup-agnes" in blocker for blocker in report["blockers"])
    assert any("consolidator live gate" in blocker for blocker in report["blockers"])


def test_reference_post_final_turns_use_started_at_fallback() -> None:
    from memory_gateway.pareto_release import count_post_final_promotion_turns

    assert count_post_final_promotion_turns(REFERENCE) == 1


def test_orchid_release_gate_complete_requires_post_final_turns() -> None:
    from memory_gateway.pareto_release import orchid_release_gate_complete

    assert orchid_release_gate_complete("PASSED", post_final_promotion_turns=5)
    assert not orchid_release_gate_complete("PASSED", post_final_promotion_turns=4)
    assert not orchid_release_gate_complete("PENDING", post_final_promotion_turns=5)


def test_validate_pareto_pair_artifact_flags_reference_baseline() -> None:
    from memory_gateway.pareto_release import validate_pareto_pair_artifact

    report = validate_pareto_pair_artifact(REFERENCE)
    assert report["ready"] is False
    assert "foreground_substitution_present" in report["failed_checks"]
    assert "memory_format_v2" in report["failed_checks"]


def test_summarize_worktree_dirty_lines_groups_prefixes() -> None:
    from memory_gateway.pareto_release import summarize_worktree_dirty_lines

    summary = summarize_worktree_dirty_lines(
        [
            " M memory_gateway/gateway.py",
            " M tests/test_pareto_release.py",
            " M tools/start_freetoshop_corrected_all_solar.sh",
        ]
    )
    assert summary["total"] == 3
    assert summary["by_prefix"] == {
        "memory_gateway": 1,
        "tests": 1,
        "tools": 1,
    }


def test_build_release_next_steps_orders_key_before_triplet() -> None:
    from memory_gateway.pareto_release import build_release_next_steps

    key_steps = build_release_next_steps(
        {"provider_profile": False, "consolidator_live_gate": False},
        gate={"reason": "stale_partial_probe"},
    )
    assert key_steps[0].endswith("setup-agnes")
    gate_steps = build_release_next_steps(
        {
            "provider_profile": True,
            "consolidator_live_gate": False,
            "selector_frozen_hashes": True,
            "worktree_clean": True,
        },
        gate={"reason": "stale_partial_probe"},
    )
    assert gate_steps[0].endswith("reset-consolidator-gate")
    triplet_steps = build_release_next_steps(
        {
            "provider_profile": True,
            "consolidator_live_gate": True,
            "selector_frozen_hashes": True,
            "worktree_clean": True,
        },
        gate={"reason": "passed"},
    )
    assert any("run-pareto-triplet" in step for step in triplet_steps)


def test_substitution_probe_status_reports_latest_probe(tmp_path: Path) -> None:
    from memory_gateway.pareto_release import substitution_probe_status

    probe = tmp_path / "artifacts/agent_benchmarks/freetoshop_orchid_only_substitution_probe_test"
    telemetry = probe / "orchid/foreground_substitution.jsonl"
    telemetry.parent.mkdir(parents=True)
    telemetry.write_text(
        '{"active": true, "suppressed_covered_tokens": 1200}\n'
        '{"active": false, "suppressed_covered_tokens": 0}\n',
        encoding="utf-8",
    )
    status = substitution_probe_status(tmp_path)
    assert status["present"] is True
    assert status["ready"] is True
    assert status["active_requests"] == 1
    assert status["peak_suppressed_covered_tokens"] == 1200
