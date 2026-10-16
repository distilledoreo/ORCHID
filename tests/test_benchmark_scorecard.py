from __future__ import annotations

import json
from pathlib import Path

from memory_gateway.benchmark_scorecard import (
    compare_to_published,
    compute_economics,
    generate_scorecard,
    summarize_direct_foreground_latency,
    summarize_paired_vram,
    summarize_foreground_substitution,
    summarize_rpc_telemetry,
    validate_run,
)

ARTIFACT = (
    Path(__file__).resolve().parents[1]
    / "artifacts/agent_benchmarks/freetoshop_pi_vs_orchid_corrected_all_solar"
)


def test_validate_run_marks_missing_hidden_eval_as_invalid(tmp_path: Path) -> None:
    (tmp_path / "BENCHMARK_MANIFEST.json").write_text(
        '{"run_policy":{"manual_rescue":"forbidden"}}',
        encoding="utf-8",
    )
    (tmp_path / "driver_summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pi_native").mkdir()
    (tmp_path / "orchid").mkdir()
    (tmp_path / "pi_native/telemetry.jsonl").write_text(
        '{"type":"message_end","message":{"role":"assistant","usage":{"input":1,"totalTokens":2}}}\n',
        encoding="utf-8",
    )
    (tmp_path / "orchid/telemetry.jsonl").write_text(
        '{"type":"message_end","message":{"role":"assistant","usage":{"input":1,"totalTokens":2}}}\n',
        encoding="utf-8",
    )
    validity = validate_run(tmp_path, require_hidden_eval=True)
    assert not validity.valid
    assert "missing quality/hidden_eval.json" in validity.reasons


SUBSTITUTION_PROBE = (
    Path(__file__).resolve().parents[1]
    / "artifacts/agent_benchmarks/freetoshop_orchid_only_substitution_probe_20260828T125015Z"
)


def test_scorecard_replays_corrected_all_solar_economics() -> None:
    assert ARTIFACT.exists(), "reference artifact is required for replay validation"
    scorecard = generate_scorecard(ARTIFACT)
    replay = compare_to_published(
        scorecard,
        published_economics_path=ARTIFACT / "economics/SUMMARY.json",
    )
    assert replay["all_ok"], replay["checks"]
    assert scorecard["economics"]["pi_foreground"]["input_tokens"] == 1_811_513
    assert scorecard["economics"]["orchid_total_provider_equivalent_cost_usd"] == 0.26689164


def test_reference_scorecard_lacks_live_foreground_substitution_telemetry() -> None:
    scorecard = generate_scorecard(ARTIFACT, require_hidden_eval=False)
    assert scorecard["foreground_substitution"]["present"] is False


def test_substitution_probe_scorecard_records_provider_prompt_tokens() -> None:
    assert SUBSTITUTION_PROBE.exists()
    scorecard = generate_scorecard(SUBSTITUTION_PROBE, require_hidden_eval=False)
    substitution = scorecard["foreground_substitution"]
    assert substitution["present"] is True
    assert substitution["sum_provider_input_tokens"] > 0
    assert substitution["sum_provider_output_tokens"] > 0
    assert substitution["sum_provider_prompt_tokens"] > 0
    assert substitution["pricing_known"] is False
    assert substitution["calculated_foreground_cost_usd"] is None


def test_pareto_uses_token_class_dominance_when_prices_are_unknown() -> None:
    from memory_gateway.pareto_release import evaluate_single_run

    scorecard = generate_scorecard(SUBSTITUTION_PROBE, require_hidden_eval=False)
    result = evaluate_single_run(scorecard, artifact=SUBSTITUTION_PROBE)
    metrics = result["metrics"]
    assert metrics["cost_basis"] == "token_class_dominance"
    assert metrics["pi_total_cost_usd"] is None
    assert metrics["orchid_total_cost_usd"] is None
    assert metrics["cost_savings_fraction"] == (
        metrics["api_token_class_comparison"]["guaranteed_savings_fraction"]
    )


def _write_rpc_usage(path: Path, *, input_tokens: int, cache_read: int, output: int) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "timestamp": 1,
                    "usage": {
                        "input": input_tokens,
                        "cacheRead": cache_read,
                        "output": output,
                        "totalTokens": input_tokens + cache_read + output,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_rpc_summary_counts_cache_in_prompt_and_requires_cache_price(tmp_path: Path) -> None:
    telemetry = tmp_path / "telemetry.jsonl"
    _write_rpc_usage(telemetry, input_tokens=100, cache_read=60, output=10)
    partial = summarize_rpc_telemetry(
        telemetry,
        pricing={"input": 1.0, "output": 2.0},
    )
    assert partial["prompt_tokens"] == 160
    assert partial["largest_prompt_tokens"] == 160
    assert partial["api_token_classes"] == {
        "uncached_input_tokens": 100,
        "cache_read_tokens": 60,
        "cache_write_tokens": 0,
        "output_tokens": 10,
    }
    assert partial["calculated_cost_usd"] == 0.00012
    assert partial["fully_priced_cost_usd"] is None

    complete = summarize_rpc_telemetry(
        telemetry,
        pricing={"input": 1.0, "cache_read": 0.25, "cache_write": 0.0, "output": 2.0},
    )
    assert complete["fully_priced_cost_usd"] == 0.000135


def test_economics_excludes_local_selector_from_api_cost(tmp_path: Path) -> None:
    pi_path = tmp_path / "pi.jsonl"
    orchid_path = tmp_path / "orchid.jsonl"
    _write_rpc_usage(pi_path, input_tokens=1_000, cache_read=0, output=100)
    _write_rpc_usage(orchid_path, input_tokens=600, cache_read=0, output=80)
    pricing = {"input": 1.0, "cache_read": 0.0, "cache_write": 0.0, "output": 2.0}
    economics = compute_economics(
        pi_rpc=summarize_rpc_telemetry(pi_path, pricing=pricing),
        orchid_rpc=summarize_rpc_telemetry(orchid_path, pricing=pricing),
        memory_transitions={
            "model_runs_by_stage": [
                {"stage": "selector", "input_tokens": 500, "output_tokens": 50},
                {"stage": "consolidator", "input_tokens": 100, "output_tokens": 10},
            ]
        },
        pricing=pricing,
    )
    assert [row["stage"] for row in economics["orchid_local_memory_stages"]] == [
        "selector"
    ]
    assert [row["stage"] for row in economics["orchid_cloud_memory_stages"]] == [
        "consolidator"
    ]
    assert economics["orchid_cloud_memory_token_classes"]["uncached_input_tokens"] == 100
    assert economics["orchid_total_fully_priced_cost_usd"] == 0.00088


def test_direct_foreground_latency_excludes_failed_requests(tmp_path: Path) -> None:
    path = tmp_path / "foreground_requests.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "provider_wall_ms": 100,
                    "time_to_first_event_ms": 20,
                    "success": True,
                },
                {
                    "provider_wall_ms": 9_999,
                    "time_to_first_event_ms": 9_999,
                    "success": False,
                },
                {
                    "provider_wall_ms": 200,
                    "time_to_first_event_ms": 40,
                    "success": True,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    summary = summarize_direct_foreground_latency(path)
    assert summary["present"] is True
    assert summary["provider_wall_ms"] == {"median": 150.0, "p95": 195.0, "samples": 2}
    assert summary["time_to_first_event_ms"] == {
        "median": 30.0,
        "p95": 39.0,
        "samples": 2,
    }


def test_paired_vram_requires_isolated_main_and_selector_measurements(
    tmp_path: Path,
) -> None:
    hardware = tmp_path / "hardware"
    hardware.mkdir()
    device = {
        "baseline_bytes": 100,
        "peak_bytes": 200,
        "byte_seconds": 1_000,
        "capacity_bytes": 1_000,
        "samples": 10,
    }
    (hardware / "paired_vram_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "orchid.paired_vram.v1",
                "measurement_method": "hardware_samples",
                "arm_isolated": True,
                "arms": {
                    "PI_NATIVE": {"devices": {"main_gpu": device}},
                    "ORCHID": {
                        "devices": {
                            "main_gpu": device,
                            "selector_gpu": {**device, "peak_bytes": 300},
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    summary = summarize_paired_vram(tmp_path)
    assert summary["complete"] is True
    assert summary["pi_native"]["total"]["peak_bytes"] == 200
    assert summary["orchid"]["total"]["peak_bytes"] == 500
    assert summary["orchid"]["selector_peak_fraction"] == 0.3


def test_substitution_summary_attributes_cache_breaks(tmp_path: Path) -> None:
    path = tmp_path / "foreground_substitution.jsonl"
    path.write_text(
        json.dumps(
            {
                "provider_input_tokens": 100,
                "provider_output_tokens": 10,
                "provider_cache_read_tokens": 80,
                "provider_input_includes_cache": True,
                "prompt_cache": {
                    "reusable_prefix_tokens": 75,
                    "estimated_cache_write_tokens": 25,
                    "cache_break_block": "conversation",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    summary = summarize_foreground_substitution(path)
    assert summary["sum_provider_uncached_input_tokens"] == 20
    assert summary["sum_provider_prompt_tokens"] == 100
    assert summary["prompt_cache"]["provider_cache_hit_rate"] == 1
    assert summary["prompt_cache"]["cache_break_counts"] == {"conversation": 1}
